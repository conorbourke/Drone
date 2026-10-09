"""Claude image readings: read a project's reference images and propose parameters.

A reading at effort "high" with four images can take a minute, longer than a proxy may keep a
request open. ``POST`` therefore stores the reading with ``status: "running"`` and returns 202
at once; the Claude call runs on the app's single-worker reading executor (created in the
lifespan, see ``app.main``) with its own database session, and the browser polls
``GET /api/image-readings/{id}`` until the status is ``ok``, ``refused`` or ``error``. A missing
API key is still answered synchronously with a plain 503.
"""

from __future__ import annotations

import logging
from concurrent.futures import Executor
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from app.assistant import vision
from app.assistant.proposal import build_proposal
from app.config import Settings
from app.deps import AppSettings, CurrentUser, DbSession, current_user
from app.imaging import stored_path
from app.models import Image, ImageReading
from app.routers.common import current_parameters, owned_project
from app.schemas.images import ReadingCreate, ReadingOut

log = logging.getLogger("app.readings")

router = APIRouter(prefix="/api", tags=["image-readings"], dependencies=[Depends(current_user)])

MAX_READING_IMAGES = 4

REFUSAL_MESSAGE = (
    "Claude declined to read these images. Nothing was changed. Try different pictures of the "
    "aircraft, or enter the dimensions by hand."
)
INTERRUPTED_MESSAGE = (
    "The reading was interrupted because the server restarted. Nothing was changed. Run it again."
)
UNEXPECTED_MESSAGE = (
    "Something went wrong while reading the images. Nothing was changed. Try again."
)


def reading_out(reading: ImageReading) -> ReadingOut:
    return ReadingOut(
        id=reading.id,
        project_id=reading.project_id,
        model=reading.model,
        reference=reading.reference,
        image_ids=list(reading.image_ids),
        status=reading.status,  # type: ignore[arg-type]
        proposal=reading.proposal,
        error=reading.error,
        usage=reading.usage,
        created_at=reading.created_at,
    )


def mark_interrupted_readings(session_factory: sessionmaker[Session]) -> int:
    """At startup: readings left "running" by a previous process can never finish."""
    with session_factory() as db:
        result = db.execute(
            update(ImageReading)
            .where(ImageReading.status == "running")
            .values(status="error", error=INTERRUPTED_MESSAGE)
        )
        db.commit()
        return int(getattr(result, "rowcount", 0) or 0)


def run_reading(
    session_factory: sessionmaker[Session],
    settings: Settings,
    reading_id: int,
    images: list[vision.VisionImage],
    reference_parameter: str,
    reference_value_mm: float,
    draft_parameters: dict[str, Any],
) -> None:
    """Background job: call Claude and store the outcome on the reading row.

    Runs on the reading executor's thread with its own session. Never raises: every outcome,
    including an unexpected bug, ends with the row out of ``running``.
    """
    outcome: dict[str, Any]
    try:
        result = vision.read_images(settings, images, reference_parameter)
    except vision.VisionError as exc:  # includes VisionNotConfigured
        outcome = {"status": "error", "error": str(exc)}
    except OSError:
        log.exception("Could not prepare images for Claude")
        outcome = {
            "status": "error",
            "error": "The stored images could not be read. Upload them again.",
        }
    except Exception:
        log.exception("Image reading failed unexpectedly")
        outcome = {"status": "error", "error": UNEXPECTED_MESSAGE}
    else:
        outcome = {"model": result.model, "usage": result.usage}
        if result.status == "refused":
            category = result.refusal_category
            outcome["status"] = "refused"
            outcome["error"] = REFUSAL_MESSAGE + (f" (category: {category})" if category else "")
        else:
            assert result.answer is not None
            try:
                outcome["proposal"] = build_proposal(
                    result.answer, reference_parameter, reference_value_mm, draft_parameters
                )
                outcome["status"] = "ok"
            except Exception:
                log.exception("Could not build the proposal from Claude's answer")
                outcome["status"] = "error"
                outcome["error"] = UNEXPECTED_MESSAGE

    try:
        with session_factory() as db:
            reading = db.get(ImageReading, reading_id)
            if reading is None:  # the project was deleted while Claude was reading
                return
            for key, value in outcome.items():
                setattr(reading, key, value)
            db.commit()
    except Exception:
        log.exception("Could not store the result of image reading %s", reading_id)


@router.get("/image-readings/status")
def reading_status(settings: AppSettings) -> dict[str, Any]:
    """Whether image reading is available, so the UI can show the plain message up front."""
    available = vision.is_available(settings)
    return {
        "available": available,
        "model": settings.claude_model,
        "message": None if available else vision.MISSING_KEY_MESSAGE,
    }


@router.get("/projects/{project_id}/image-readings", response_model=list[ReadingOut])
def list_readings(project_id: int, db: DbSession, user: CurrentUser) -> list[ReadingOut]:
    project = owned_project(db, user, project_id)
    rows = db.scalars(
        select(ImageReading)
        .where(ImageReading.project_id == project.id)
        .order_by(ImageReading.created_at.desc(), ImageReading.id.desc())
    ).all()
    return [reading_out(r) for r in rows]


@router.get("/image-readings/{reading_id}", response_model=ReadingOut)
def get_reading(reading_id: int, db: DbSession, user: CurrentUser) -> ReadingOut:
    """One reading; the browser polls this while the status is ``running``."""
    reading = db.get(ImageReading, reading_id)
    if reading is None or reading.owner_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Reading not found.")
    return reading_out(reading)


@router.post(
    "/projects/{project_id}/image-readings",
    response_model=ReadingOut,
    status_code=status.HTTP_202_ACCEPTED,
    responses={503: {"description": "No Claude API key configured"}},
)
def create_reading(
    project_id: int,
    body: ReadingCreate,
    request: Request,
    db: DbSession,
    user: CurrentUser,
    settings: AppSettings,
) -> Any:
    project = owned_project(db, user, project_id)
    all_images = db.scalars(
        select(Image).where(Image.project_id == project.id).order_by(Image.id)
    ).all()
    if body.image_ids is None:
        chosen = list(all_images)
    else:
        by_id = {image.id: image for image in all_images}
        ids = list(dict.fromkeys(body.image_ids))
        missing = [i for i in ids if i not in by_id]
        if missing:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Some of the chosen images do not belong to this project.",
            )
        chosen = [by_id[i] for i in ids]
    if not chosen:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Add at least one reference image before asking Claude to read them.",
        )
    if len(chosen) > MAX_READING_IMAGES:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Choose at most {MAX_READING_IMAGES} images for one reading.",
        )
    if not vision.is_available(settings):
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"detail": vision.MISSING_KEY_MESSAGE},
        )
    executor: Executor | None = getattr(request.app.state, "reading_executor", None)
    if executor is None:  # only outside the lifespan (never in a running server)
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"detail": "The server is starting up. Try again in a moment."},
        )

    images = [
        vision.VisionImage(
            view=i.view, path=stored_path(settings.images_dir, i.project_id, i.storage_name)
        )
        for i in chosen
    ]
    reading = ImageReading(
        owner_id=user.id,
        project_id=project.id,
        model=settings.claude_model,
        reference=body.reference.model_dump(),
        image_ids=[i.id for i in chosen],
        status="running",
    )
    db.add(reading)
    db.commit()
    db.refresh(reading)
    try:
        executor.submit(
            run_reading,
            request.app.state.session_factory,
            settings,
            reading.id,
            images,
            body.reference.parameter,
            body.reference.value_mm,
            current_parameters(project.draft_parameters),
        )
    except RuntimeError:  # executor already shut down: the server is stopping
        reading.status = "error"
        reading.error = INTERRUPTED_MESSAGE
        db.commit()
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"detail": "The server is restarting. Try again in a moment."},
        )
    return JSONResponse(
        status_code=status.HTTP_202_ACCEPTED,
        content=reading_out(reading).model_dump(mode="json"),
    )

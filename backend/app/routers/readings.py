"""Claude image readings: read a project's reference images and propose parameters."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import JSONResponse
from sqlalchemy import select

from app.assistant import vision
from app.assistant.proposal import build_proposal
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


@router.post(
    "/projects/{project_id}/image-readings",
    response_model=ReadingOut,
    status_code=status.HTTP_201_CREATED,
    responses={200: {"description": "Claude declined (status refused)"}},
)
def create_reading(
    project_id: int,
    body: ReadingCreate,
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

    reference = body.reference.model_dump()
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
        reference=reference,
        image_ids=[i.id for i in chosen],
        status="error",
    )
    try:
        result = vision.read_images(settings, images, body.reference.parameter)
    except vision.VisionNotConfigured:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"detail": vision.MISSING_KEY_MESSAGE},
        )
    except vision.VisionError as exc:
        reading.error = str(exc)
        db.add(reading)
        db.commit()
        return JSONResponse(status_code=status.HTTP_502_BAD_GATEWAY, content={"detail": str(exc)})
    except OSError:
        log.exception("Could not prepare images for Claude")
        reading.error = "The stored images could not be read. Upload them again."
        db.add(reading)
        db.commit()
        return JSONResponse(
            status_code=status.HTTP_502_BAD_GATEWAY, content={"detail": reading.error}
        )

    reading.model = result.model
    reading.usage = result.usage
    if result.status == "refused":
        reading.status = "refused"
        category = result.refusal_category
        reading.error = REFUSAL_MESSAGE + (f" (category: {category})" if category else "")
        db.add(reading)
        db.commit()
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content=reading_out(reading).model_dump(mode="json"),
        )

    assert result.answer is not None
    reading.status = "ok"
    reading.proposal = build_proposal(
        result.answer,
        body.reference.parameter,
        body.reference.value_mm,
        current_parameters(project.draft_parameters),
    )
    db.add(reading)
    db.commit()
    return reading_out(reading)

"""Reference images: upload, list, change view, delete, and serve the file."""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.deps import AppSettings, CurrentUser, DbSession, current_user
from app.imaging import (
    MAX_IMAGE_BYTES,
    MAX_IMAGES_PER_PROJECT,
    ImageRejected,
    new_storage_name,
    process_upload,
    sanitise_filename,
    stored_path,
    write_atomically,
)
from app.models import Image, User
from app.routers.common import not_found, owned_project
from app.schemas.images import ImageOut, ImageUpdate, ImageView

log = logging.getLogger("app.images")

router = APIRouter(prefix="/api", tags=["images"], dependencies=[Depends(current_user)])

FILE_CACHE_CONTROL = "private, max-age=3600"


def image_out(image: Image) -> ImageOut:
    return ImageOut(
        id=image.id,
        filename=image.filename,
        view=image.view,  # type: ignore[arg-type]
        width_px=image.width_px,
        height_px=image.height_px,
        size_bytes=image.size_bytes,
        url=f"/api/images/{image.id}/file",
        created_at=image.created_at,
    )


def owned_image(db: Session, user: User, image_id: int) -> Image:
    image = db.get(Image, image_id)
    if image is None or image.owner_id != user.id:
        raise not_found("Image")
    return image


@router.post(
    "/projects/{project_id}/images",
    response_model=ImageOut,
    status_code=status.HTTP_201_CREATED,
)
def upload_image(
    project_id: int,
    db: DbSession,
    user: CurrentUser,
    settings: AppSettings,
    file: Annotated[UploadFile, File(description="JPEG, PNG or WebP image, at most 15 MB.")],
    view: Annotated[ImageView, Form(description="Which view of the aircraft it shows.")] = "other",
) -> ImageOut:
    project = owned_project(db, user, project_id)
    count = db.scalar(select(func.count(Image.id)).where(Image.project_id == project.id)) or 0
    if count >= MAX_IMAGES_PER_PROJECT:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=f"A project can hold at most {MAX_IMAGES_PER_PROJECT} images. "
            "Delete one before adding another.",
        )
    data = file.file.read(MAX_IMAGE_BYTES + 1)
    if len(data) > MAX_IMAGE_BYTES:
        raise HTTPException(
            status.HTTP_413_CONTENT_TOO_LARGE,
            detail="The image is larger than 15 MB. Export a smaller copy and try again.",
        )
    try:
        processed = process_upload(data)
    except ImageRejected as exc:
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail=str(exc)) from None

    storage_name = new_storage_name(processed.extension)
    path = stored_path(settings.images_dir, project.id, storage_name)
    write_atomically(path, processed.data)
    image = Image(
        owner_id=user.id,
        project_id=project.id,
        filename=sanitise_filename(file.filename),
        content_type=processed.content_type,
        size_bytes=len(processed.data),
        width_px=processed.width_px,
        height_px=processed.height_px,
        view=view,
        storage_name=storage_name,
    )
    db.add(image)
    try:
        db.commit()
    except Exception:
        db.rollback()
        path.unlink(missing_ok=True)
        raise
    return image_out(image)


@router.get("/projects/{project_id}/images", response_model=list[ImageOut])
def list_images(project_id: int, db: DbSession, user: CurrentUser) -> list[ImageOut]:
    project = owned_project(db, user, project_id)
    rows = db.scalars(select(Image).where(Image.project_id == project.id).order_by(Image.id)).all()
    return [image_out(i) for i in rows]


@router.patch("/images/{image_id}", response_model=ImageOut)
def update_image(image_id: int, body: ImageUpdate, db: DbSession, user: CurrentUser) -> ImageOut:
    image = owned_image(db, user, image_id)
    image.view = body.view
    db.commit()
    return image_out(image)


@router.delete("/images/{image_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_image(image_id: int, db: DbSession, user: CurrentUser, settings: AppSettings) -> None:
    image = owned_image(db, user, image_id)
    path = stored_path(settings.images_dir, image.project_id, image.storage_name)
    db.delete(image)
    db.commit()
    path.unlink(missing_ok=True)


@router.get("/images/{image_id}/file")
def image_file(
    image_id: int, db: DbSession, user: CurrentUser, settings: AppSettings
) -> FileResponse:
    image = owned_image(db, user, image_id)
    path = stored_path(settings.images_dir, image.project_id, image.storage_name)
    if not path.is_file():
        log.warning("Image %s has no file on disk", image.id)
        raise not_found("Image file")
    return FileResponse(
        path,
        media_type=image.content_type,
        headers={
            "Cache-Control": FILE_CACHE_CONTROL,
            "X-Content-Type-Options": "nosniff",
            "Content-Disposition": "inline",
        },
    )

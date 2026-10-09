"""Image handling with Pillow: upload checks, orientation, storage paths, and the copy sent
to Claude. The file type is always decided from the bytes, never from the name or the
browser's declared content type."""

from __future__ import annotations

import io
import re
import secrets
import shutil
import warnings
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError

MAX_IMAGE_BYTES = 15 * 1024 * 1024
MAX_IMAGES_PER_PROJECT = 6
MAX_IMAGE_PIXELS = 60_000_000  # far above any camera; refuses decompression bombs early
CLAUDE_MAX_EDGE_PX = 1568
CLAUDE_JPEG_QUALITY = 88

# Pillow format name -> (stored content type, file extension)
ALLOWED_FORMATS: dict[str, tuple[str, str]] = {
    "JPEG": ("image/jpeg", ".jpg"),
    "PNG": ("image/png", ".png"),
    "WEBP": ("image/webp", ".webp"),
}
EXIF_ORIENTATION = 0x0112
STORAGE_NAME_RE = re.compile(r"^[0-9a-f]{32}\.(jpg|png|webp)$")


class ImageRejected(ValueError):
    """The upload is not an image we accept. The message is plain and safe to show."""


@dataclass(frozen=True)
class ProcessedImage:
    data: bytes
    content_type: str
    extension: str
    width_px: int
    height_px: int


def sanitise_filename(name: str | None) -> str:
    """Keep only a plain base name: letters, digits, dot, dash, underscore and spaces."""
    base = (name or "").replace("\\", "/").rsplit("/", 1)[-1]
    cleaned = re.sub(r"[^A-Za-z0-9._ -]+", "_", base).strip(" ._")
    return cleaned[:120] or "image"


def process_upload(data: bytes) -> ProcessedImage:
    """Check the bytes are a JPEG, PNG or WebP image, apply the EXIF orientation and return
    what to store. A file without a rotation tag is stored byte for byte."""
    if not data:
        raise ImageRejected("The file is empty.")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            image = Image.open(io.BytesIO(data))
            fmt = image.format or ""
            if fmt not in ALLOWED_FORMATS:
                raise ImageRejected("Only JPEG, PNG and WebP images are accepted.")
            width, height = image.size
            if width * height > MAX_IMAGE_PIXELS:
                raise ImageRejected("The image has too many pixels (limit 60 megapixels).")
            image.load()
    except ImageRejected:
        raise
    except (UnidentifiedImageError, Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise ImageRejected("Only JPEG, PNG and WebP images are accepted.") from None
    except (OSError, ValueError, SyntaxError):
        raise ImageRejected("The image file is damaged or incomplete.") from None

    content_type, extension = ALLOWED_FORMATS[fmt]
    orientation = image.getexif().get(EXIF_ORIENTATION, 1)
    if orientation in (None, 1):
        return ProcessedImage(data, content_type, extension, width, height)

    upright = ImageOps.exif_transpose(image)
    out = io.BytesIO()
    if fmt == "JPEG":
        upright.save(out, format="JPEG", quality=92, optimize=True)
    elif fmt == "PNG":
        upright.save(out, format="PNG", optimize=True)
    else:
        upright.save(out, format="WEBP", quality=92)
    return ProcessedImage(out.getvalue(), content_type, extension, upright.size[0], upright.size[1])


def new_storage_name(extension: str) -> str:
    return secrets.token_hex(16) + extension


def project_dir(images_root: Path, project_id: int) -> Path:
    return images_root / str(int(project_id))


def stored_path(images_root: Path, project_id: int, storage_name: str) -> Path:
    """Path of a stored file. The name comes from the database, and is still checked."""
    if not STORAGE_NAME_RE.match(storage_name):
        raise ValueError("Unexpected storage name.")
    root = images_root.resolve()
    path = (project_dir(images_root, project_id) / storage_name).resolve()
    if root not in path.parents:
        raise ValueError("Path outside the images directory.")
    return path


def write_atomically(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".part")
    tmp.write_bytes(data)
    tmp.replace(path)


def remove_project_files(images_root: Path, project_id: int) -> None:
    shutil.rmtree(project_dir(images_root, project_id), ignore_errors=True)


def jpeg_for_claude(path: Path) -> bytes:
    """At most 1568 px on the long side, re-encoded as JPEG quality 88."""
    with Image.open(path) as image:
        image = ImageOps.exif_transpose(image)
        if image.mode not in ("RGB", "L"):
            rgba = image.convert("RGBA")
            background = Image.new("RGB", rgba.size, (255, 255, 255))
            background.paste(rgba, mask=rgba.getchannel("A"))
            image = background
        elif image.mode == "L":
            image = image.convert("RGB")
        image.thumbnail((CLAUDE_MAX_EDGE_PX, CLAUDE_MAX_EDGE_PX), Image.Resampling.LANCZOS)
        out = io.BytesIO()
        image.save(out, format="JPEG", quality=CLAUDE_JPEG_QUALITY)
        return out.getvalue()

"""Image handling with Pillow: upload checks, orientation, storage paths, and the copy sent
to Claude. The file type is always decided from the bytes, never from the name or the
browser's declared content type.

Memory matters here: the server has 1-2 GB, and a fully decoded 40 MP image is 160 MB
(Pillow keeps RGB as four bytes per pixel). So uploads are checked without a full decode
where the format allows it, the original bytes are stored as they are (browsers honour the
EXIF orientation when they draw the image), and the copy for Claude is decoded at reduced
size: JPEG through the decoder's DCT scaling (``draft``), PNG one band of rows at a time.
"""

from __future__ import annotations

import io
import re
import secrets
import shutil
import struct
import warnings
import zlib
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from PIL import Image, UnidentifiedImageError

MAX_IMAGE_BYTES = 15 * 1024 * 1024
MAX_IMAGES_PER_PROJECT = 6
MAX_IMAGE_PIXELS = 40_000_000  # above any phone camera; refuses decompression bombs early
# Pillow can only decode WebP whole, through a path that needs about 14 bytes per pixel, and
# PNG files it cannot read band by band (16-bit, interlaced, under 8-bit) are decoded whole
# too. These get a lower limit so the copy for Claude stays within ~150 MB.
MAX_WEBP_PIXELS = 8_000_000
MAX_PNG_WHOLE_DECODE_PIXELS = 24_000_000
CLAUDE_MAX_EDGE_PX = 1568
CLAUDE_JPEG_QUALITY = 88
PNG_BAND_BYTES = 4 * 1024 * 1024  # raw rows decoded at once when reducing a PNG

# Pillow format name -> (stored content type, file extension)
ALLOWED_FORMATS: dict[str, tuple[str, str]] = {
    "JPEG": ("image/jpeg", ".jpg"),
    "PNG": ("image/png", ".png"),
    "WEBP": ("image/webp", ".webp"),
}
EXIF_ORIENTATION = 0x0112
STORAGE_NAME_RE = re.compile(r"^[0-9a-f]{32}\.(jpg|png|webp)$")
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
# PNG colour type -> (channels, Pillow raw mode) for 8-bit images.
PNG_COLOUR_TYPES: dict[int, tuple[int, str]] = {
    0: (1, "L"),
    2: (3, "RGB"),
    3: (1, "P"),
    4: (2, "LA"),
    6: (4, "RGBA"),
}
# EXIF orientation -> the transpose that makes the image upright (as ImageOps.exif_transpose).
ORIENTATION_TRANSPOSE: dict[int, Image.Transpose] = {
    2: Image.Transpose.FLIP_LEFT_RIGHT,
    3: Image.Transpose.ROTATE_180,
    4: Image.Transpose.FLIP_TOP_BOTTOM,
    5: Image.Transpose.TRANSPOSE,
    6: Image.Transpose.ROTATE_270,
    7: Image.Transpose.TRANSVERSE,
    8: Image.Transpose.ROTATE_90,
}


class ImageRejected(ValueError):
    """The upload is not an image we accept. The message is plain and safe to show."""


@dataclass(frozen=True)
class ProcessedImage:
    """What to store. ``data`` is always the uploaded bytes; ``width_px`` and ``height_px``
    are the upright size, after the EXIF ``orientation`` (1 = none) is applied."""

    data: bytes
    content_type: str
    extension: str
    width_px: int
    height_px: int
    orientation: int = 1


def sanitise_filename(name: str | None) -> str:
    """Keep only a plain base name: letters, digits, dot, dash, underscore and spaces."""
    base = (name or "").replace("\\", "/").rsplit("/", 1)[-1]
    cleaned = re.sub(r"[^A-Za-z0-9._ -]+", "_", base).strip(" ._")
    return cleaned[:120] or "image"


# --- PNG chunk reading ------------------------------------------------------------------


def _png_chunks(fp: BinaryIO) -> Iterator[tuple[bytes, bytes]]:
    """Yield ``(type, data)`` for each chunk, checking CRCs. Stops after IEND."""
    fp.seek(0)
    if fp.read(8) != PNG_SIGNATURE:
        raise SyntaxError("not a PNG file")
    while True:
        header = fp.read(8)
        if len(header) < 8:
            raise OSError("truncated PNG")
        length, kind = struct.unpack(">I4s", header)
        data = fp.read(length)
        crc = fp.read(4)
        if len(data) < length or len(crc) < 4:
            raise OSError("truncated PNG")
        if zlib.crc32(kind + data) != struct.unpack(">I", crc)[0]:
            raise SyntaxError("broken PNG chunk")
        yield kind, data
        if kind == b"IEND":
            return


def _png_chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))


def _exif_orientation(exif_bytes: bytes | None) -> int:
    if not exif_bytes:
        return 1
    exif = Image.Exif()
    try:
        exif.load(exif_bytes)
    except Exception:  # a damaged EXIF block is ignored, as browsers do
        return 1
    return _valid_orientation(exif.get(EXIF_ORIENTATION, 1))


def _valid_orientation(value: object) -> int:
    return value if isinstance(value, int) and value in range(1, 9) else 1


def _png_orientation(fp: BinaryIO) -> int:
    """The eXIf orientation, read from the chunks without decoding the pixels. (Pillow's own
    ``getexif`` on a PNG decodes the whole image first.)"""
    for kind, data in _png_chunks(fp):
        if kind == b"eXIf":
            return _exif_orientation(data)
    return 1


def _orientation(image: Image.Image, fp: BinaryIO) -> int:
    if image.format == "PNG":
        return _png_orientation(fp)
    return _valid_orientation(image.getexif().get(EXIF_ORIENTATION, 1))


def _png_supports_bands(ihdr: bytes) -> bool:
    _w, _h, depth, colour, _c, _f, interlace = struct.unpack(">IIBBBBB", ihdr)
    return depth == 8 and interlace == 0 and colour in PNG_COLOUR_TYPES


def _png_reduced(fp: BinaryIO, factor: int) -> Image.Image | None:
    """Decode a PNG one band of rows at a time, box-reducing each band by ``factor``.

    Each band is wrapped in a small PNG of its own (the previous band's last row, unfiltered,
    first) and decoded by Pillow, so the filters are undone in C and only one band is ever in
    memory at full size. Returns None for PNGs this cannot handle (16-bit, under 8-bit,
    interlaced); those are decoded whole. The result is RGB, or RGBA when the PNG has alpha.
    """
    chunks = _png_chunks(fp)
    kind, ihdr = next(chunks)
    if kind != b"IHDR" or len(ihdr) != 13:
        raise SyntaxError("broken PNG header")
    if not _png_supports_bands(ihdr):
        return None
    width, height, _d, colour, _c, _f, _i = struct.unpack(">IIBBBBB", ihdr)
    channels, rawmode = PNG_COLOUR_TYPES[colour]
    stride = width * channels
    row_bytes = stride + 1
    band_rows = max(factor, (PNG_BAND_BYTES // row_bytes) // factor * factor)

    extra: list[bytes] = []  # PLTE and tRNS, copied into each band's PNG
    has_alpha = colour in (4, 6)
    out = Image.new("RGBA" if has_alpha else "RGB", (-(-width // factor), -(-height // factor)))
    work_mode = out.mode
    previous = bytes(stride)  # PNG filters treat the row above the first as zeros
    pending = bytearray()
    inflater = zlib.decompressobj()
    y = 0

    def emit(rows: int) -> None:
        nonlocal previous, y, out, work_mode
        raw = bytes(pending[: rows * row_bytes])
        del pending[: rows * row_bytes]
        header = struct.pack(">IIBBBBB", width, rows + 1, 8, colour, 0, 0, 0)
        mini = (
            PNG_SIGNATURE
            + _png_chunk(b"IHDR", header)
            + b"".join(extra)
            + _png_chunk(b"IDAT", zlib.compress(b"\0" + previous + raw, 0))
            + _png_chunk(b"IEND", b"")
        )
        del raw
        with Image.open(io.BytesIO(mini)) as band:
            band.load()
            previous = band.crop((0, rows, width, rows + 1)).tobytes("raw", rawmode)
            if out.mode == "RGB" and band.mode in ("P", "L", "RGB") and band.has_transparency_data:
                out = out.convert("RGBA")
                work_mode = "RGBA"
            piece = band.crop((0, 1, width, rows + 1)).convert(work_mode)
        if factor > 1:
            piece = piece.reduce(factor)
        out.paste(piece, (0, y // factor))
        y += rows

    def drain() -> None:
        while y < height:
            rows = min(band_rows, height - y)
            if len(pending) < rows * row_bytes:
                return
            emit(rows)

    for kind, data in chunks:
        if kind in (b"PLTE", b"tRNS"):
            extra.append(_png_chunk(kind, data))
        elif kind == b"IDAT":
            data_left = data
            while data_left and y < height:
                wanted = max(1, band_rows * row_bytes - len(pending))
                pending += inflater.decompress(data_left, wanted)
                data_left = inflater.unconsumed_tail
                drain()
        elif kind == b"IEND":
            break
    if y < height:
        pending += inflater.flush()
        drain()
    if y < height:
        raise OSError("The PNG image data is incomplete.")
    return out


# --- upload -----------------------------------------------------------------------------


def process_upload(data: bytes) -> ProcessedImage:
    """Check the bytes are a JPEG, PNG or WebP image and return what to store.

    The pixels are not decoded in full: PNG integrity is the chunk CRCs, JPEG is decoded at
    1/8 scale, and WebP (which Pillow cannot scale while decoding) has a lower pixel limit.
    The bytes are stored as uploaded; the EXIF orientation is recorded, and the reported size
    is the upright one. Browsers apply the orientation when they show the image
    (``image-orientation: from-image``) and ``jpeg_for_claude`` applies it to Claude's copy.
    """
    if not data:
        raise ImageRejected("The file is empty.")
    stream = io.BytesIO(data)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            image = Image.open(stream)
            fmt = image.format or ""
            if fmt not in ALLOWED_FORMATS:
                raise ImageRejected("Only JPEG, PNG and WebP images are accepted.")
            width, height = image.size
            if width * height > MAX_IMAGE_PIXELS:
                raise ImageRejected("The image has too many pixels (limit 40 megapixels).")
            orientation = _orientation(image, stream)
            if fmt == "PNG":
                stream.seek(0)
                ihdr = next(_png_chunks(stream))[1]
                if not _png_supports_bands(ihdr) and width * height > MAX_PNG_WHOLE_DECODE_PIXELS:
                    raise ImageRejected(
                        "This kind of PNG (16-bit or interlaced) is limited to 24 megapixels. "
                        "Save it as an 8-bit PNG or a JPEG."
                    )
                # Every chunk's CRC, without inflating the pixel data.
                image = Image.open(io.BytesIO(data))
                image.verify()
            elif fmt == "JPEG":
                # Decode at the smallest DCT scale: catches truncated or corrupt data while
                # holding about 1/64 of the pixels.
                image.draft(image.mode, (max(1, width // 8), max(1, height // 8)))
                image.load()
            else:
                if width * height > MAX_WEBP_PIXELS:
                    raise ImageRejected(
                        "WebP images are limited to 8 megapixels. Save it as a JPEG instead."
                    )
                image.load()
    except ImageRejected:
        raise
    except (UnidentifiedImageError, Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise ImageRejected("Only JPEG, PNG and WebP images are accepted.") from None
    except (OSError, ValueError, SyntaxError, struct.error, StopIteration):
        raise ImageRejected("The image file is damaged or incomplete.") from None

    content_type, extension = ALLOWED_FORMATS[fmt]
    if orientation in (5, 6, 7, 8):  # quarter turns swap the sides
        width, height = height, width
    return ProcessedImage(data, content_type, extension, width, height, orientation)


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


# --- the copy sent to Claude ------------------------------------------------------------


def _has_alpha(image: Image.Image) -> bool:
    return image.mode in ("RGBA", "LA", "PA", "RGBa", "La") or (
        image.mode in ("P", "L", "RGB") and "transparency" in image.info
    )


def jpeg_for_claude(path: Path) -> bytes:
    """At most 1568 px on the long side, upright, alpha on white, JPEG quality 88.

    The order keeps the peak small: reduce first (JPEG decodes at a DCT scale, PNG band by
    band), then turn the small image upright, then convert the mode and flatten the alpha.
    """
    box = (CLAUDE_MAX_EDGE_PX, CLAUDE_MAX_EDGE_PX)
    with path.open("rb") as fp, Image.open(fp) as source:
        orientation = _orientation(source, fp)
        small: Image.Image | None = None
        if source.format == "PNG":
            factor = max(1, max(source.size) // CLAUDE_MAX_EDGE_PX)
            small = _png_reduced(fp, factor)
        if small is None:
            if source.format == "JPEG":
                source.draft("RGB", box)
            source.thumbnail(box, Image.Resampling.LANCZOS)
            small = source
        else:
            small.thumbnail(box, Image.Resampling.LANCZOS)
        transpose = ORIENTATION_TRANSPOSE.get(orientation)
        if transpose is not None:
            small = small.transpose(transpose)
        if _has_alpha(small):
            rgba = small.convert("RGBA")
            image = Image.new("RGB", rgba.size, (255, 255, 255))
            image.paste(rgba, mask=rgba.getchannel("A"))
        elif small.mode != "RGB":
            image = small.convert("RGB")
        else:
            image = small
        out = io.BytesIO()
        image.save(out, format="JPEG", quality=CLAUDE_JPEG_QUALITY)
        return out.getvalue()

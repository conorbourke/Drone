"""Reference images: type sniffing, limits, view updates, serving and file cleanup."""

from __future__ import annotations

import io
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image, ImageChops

from app.config import Settings
from app.imaging import (
    MAX_IMAGE_BYTES,
    ImageRejected,
    _png_reduced,
    jpeg_for_claude,
    process_upload,
    sanitise_filename,
)
from app.models import Image as ImageRow


def make_image(fmt: str = "PNG", size: tuple[int, int] = (64, 32), **save: object) -> bytes:
    image = Image.new("RGB", size, (30, 120, 200))
    out = io.BytesIO()
    image.save(out, format=fmt, **save)
    return out.getvalue()


def upload(
    client: TestClient,
    project_id: int,
    data: bytes,
    name: str = "plane.png",
    content_type: str = "image/png",
    view: str = "top",
):
    return client.post(
        f"/api/projects/{project_id}/images",
        files={"file": (name, data, content_type)},
        data={"view": view},
    )


def test_upload_png_list_and_serve(auth_client: TestClient, project: dict) -> None:
    pid = project["id"]
    data = make_image("PNG", (64, 32))
    response = upload(auth_client, pid, data, name="../../my plane?.png")
    assert response.status_code == 201, response.text
    body = response.json()
    assert set(body) == {
        "id",
        "filename",
        "view",
        "width_px",
        "height_px",
        "size_bytes",
        "url",
        "created_at",
    }
    assert body["filename"] == "my plane_.png"
    assert (body["width_px"], body["height_px"]) == (64, 32)
    assert body["view"] == "top" and body["size_bytes"] == len(data)
    assert body["url"] == f"/api/images/{body['id']}/file"
    assert body["created_at"].endswith("Z")

    listed = auth_client.get(f"/api/projects/{pid}/images").json()
    assert [i["id"] for i in listed] == [body["id"]]

    served = auth_client.get(body["url"])
    assert served.status_code == 200
    assert served.content == data
    assert served.headers["content-type"] == "image/png"
    assert served.headers["cache-control"] == "private, max-age=3600"
    assert served.headers["x-content-type-options"] == "nosniff"


def test_type_is_sniffed_from_bytes_not_name(auth_client: TestClient, project: dict) -> None:
    pid = project["id"]
    # A real WebP called .txt with a text content type is accepted as WebP.
    ok = upload(auth_client, pid, make_image("WEBP"), name="notes.txt", content_type="text/plain")
    assert ok.status_code == 201, ok.text
    served = auth_client.get(ok.json()["url"])
    assert served.headers["content-type"] == "image/webp"
    # A JPEG is a JPEG.
    jpeg = upload(auth_client, pid, make_image("JPEG"), name="a.jpg", content_type="image/jpeg")
    assert jpeg.status_code == 201
    assert auth_client.get(jpeg.json()["url"]).headers["content-type"] == "image/jpeg"
    # A GIF named .png and text named .jpg are refused with a plain message.
    gif = upload(auth_client, pid, make_image("GIF"), name="sneaky.png")
    assert gif.status_code == 415
    assert gif.json()["detail"] == "Only JPEG, PNG and WebP images are accepted."
    text = upload(auth_client, pid, b"<html>not an image</html>", name="x.jpg")
    assert text.status_code == 415
    empty = upload(auth_client, pid, b"", name="x.png")
    assert empty.status_code == 415
    truncated = upload(auth_client, pid, make_image("PNG", (300, 300))[:200], name="cut.png")
    assert truncated.status_code == 415
    assert len(auth_client.get(f"/api/projects/{pid}/images").json()) == 2


def test_exif_orientation_is_recorded_and_bytes_kept(
    auth_client: TestClient, project: dict, tmp_path: Path
) -> None:
    exif = Image.Exif()
    exif[0x0112] = 6  # rotate 90 degrees clockwise to display
    data = make_image("JPEG", (80, 40), exif=exif.tobytes())
    response = upload(auth_client, project["id"], data, name="phone.jpg", content_type="image/jpeg")
    assert response.status_code == 201, response.text
    body = response.json()
    # The reported size is the upright one; the stored file is the upload, byte for byte
    # (browsers apply the orientation when they draw it).
    assert (body["width_px"], body["height_px"]) == (40, 80)
    assert auth_client.get(body["url"]).content == data
    assert process_upload(data).orientation == 6
    # Claude's copy is turned upright.
    path = tmp_path / "phone.jpg"
    path.write_bytes(data)
    assert Image.open(io.BytesIO(jpeg_for_claude(path))).size == (40, 80)


def test_png_exif_orientation_and_alpha_for_claude(tmp_path: Path) -> None:
    image = Image.new("RGBA", (90, 30), (255, 0, 0, 0))  # fully transparent red
    exif = Image.Exif()
    exif[0x0112] = 8
    out = io.BytesIO()
    image.save(out, format="PNG", exif=exif.tobytes())
    processed = process_upload(out.getvalue())
    assert (processed.width_px, processed.height_px, processed.orientation) == (30, 90, 8)
    path = tmp_path / "a.png"
    path.write_bytes(out.getvalue())
    claude = Image.open(io.BytesIO(jpeg_for_claude(path)))
    assert claude.size == (30, 90)
    r, g, b = claude.convert("RGB").getpixel((15, 45))  # type: ignore[misc]
    assert min(r, g, b) > 245  # transparent pixels become white, not red or black


def test_png_band_decoding_matches_full_decode() -> None:
    w, h = 1001, 703
    noise = Image.effect_noise((w, h), 60)
    gradient = Image.linear_gradient("L").resize((w, h))
    rgb = Image.merge("RGB", (noise, gradient, noise.transpose(Image.Transpose.FLIP_LEFT_RIGHT)))
    rgba = rgb.copy()
    rgba.putalpha(gradient)
    for source in (rgb, rgba, noise, Image.merge("LA", (noise, gradient)), rgb.quantize(64)):
        out = io.BytesIO()
        source.save(out, format="PNG")
        for factor in (1, 3):
            reduced = _png_reduced(io.BytesIO(out.getvalue()), factor)
            assert reduced is not None
            full = Image.open(io.BytesIO(out.getvalue())).convert(reduced.mode).reduce(factor)
            assert ImageChops.difference(reduced, full).getbbox() is None, (source.mode, factor)


def test_pixel_limits() -> None:
    # 7680 x 7680 is 59 MP: refused from the header, before any decoding.
    out = io.BytesIO()
    Image.new("L", (7680, 7680)).save(out, format="PNG", compress_level=1)
    with pytest.raises(ImageRejected, match="40 megapixels"):
        process_upload(out.getvalue())
    out = io.BytesIO()
    Image.new("RGB", (3000, 3000)).save(out, format="WEBP")
    with pytest.raises(ImageRejected, match="8 megapixels"):
        process_upload(out.getvalue())


PEAK_MEMORY_SCRIPT = """
import sys
from pathlib import Path
from app.imaging import jpeg_for_claude, process_upload
path = Path(sys.argv[1])
processed = process_upload(path.read_bytes())
del processed
jpeg_for_claude(path)
status = Path("/proc/self/status").read_text()
print(next(line.split()[1] for line in status.splitlines() if line.startswith("VmHWM:")))
"""


@pytest.mark.parametrize(
    ("fmt", "size"),
    [("PNG", (6300, 6300)), ("JPEG", (7300, 5470))],
    ids=["png-rgba-40mp", "jpeg-40mp"],
)
@pytest.mark.skipif(not Path("/proc/self/status").exists(), reason="needs Linux /proc")
def test_peak_memory_for_large_images(fmt: str, size: tuple[int, int], tmp_path: Path) -> None:
    """The whole upload check plus Claude's copy, in a fresh interpreter. Pillow allocates
    pixels outside Python's allocator, so tracemalloc would not see them; the process's
    resident high-water mark (VmHWM) does. (ru_maxrss would carry over this test process's
    own peak through fork and exec.) Fully decoded, the PNG alone would be 159 MB."""
    w, h = size
    coarse = Image.effect_noise((w // 16, h // 16), 80).resize((w, h))
    image = Image.merge("RGB", (coarse, Image.linear_gradient("L").resize((w, h)), coarse))
    path = tmp_path / f"big.{fmt.lower()}"
    if fmt == "PNG":
        image.putalpha(coarse)
        image.save(path, format="PNG", compress_level=1)
    else:
        image.save(path, format="JPEG", quality=90)
    del image, coarse
    result = subprocess.run(
        [sys.executable, "-c", PEAK_MEMORY_SCRIPT, str(path)],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        check=True,
        timeout=120,
    )
    peak_mb = int(result.stdout.strip()) / 1024  # VmHWM is in kB
    assert peak_mb < 150, f"peak {peak_mb:.0f} MB"


def test_size_limits(auth_client: TestClient, project: dict) -> None:
    pid = project["id"]
    # Just over 15 MB of file: refused by the route with a plain message.
    big = b"\x89PNG\r\n\x1a\n" + b"\0" * MAX_IMAGE_BYTES
    response = upload(auth_client, pid, big)
    assert response.status_code == 413
    assert "15 MB" in response.json()["detail"]
    # Over 16 MB of body: refused by the middleware before the body is read.
    huge = upload(auth_client, pid, b"\0" * (16 * 1024 * 1024 + 10))
    assert huge.status_code == 413
    assert "16 MB" in huge.json()["detail"]
    # Every other route keeps the 1 MB limit, including other POSTs under the same project.
    other = auth_client.post(
        f"/api/projects/{pid}/versions",
        content=b"{" + b" " * 1_100_000 + b"}",
        headers={"Content-Type": "application/json"},
    )
    assert other.status_code == 413
    assert "1000 kB" in other.json()["detail"]


def test_count_limit(auth_client: TestClient, project: dict) -> None:
    pid = project["id"]
    for _ in range(6):
        assert upload(auth_client, pid, make_image()).status_code == 201
    seventh = upload(auth_client, pid, make_image())
    assert seventh.status_code == 409
    assert "at most 6" in seventh.json()["detail"]


def test_count_limit_holds_under_parallel_uploads(auth_client: TestClient, project: dict) -> None:
    pid = project["id"]
    data = make_image("PNG", (400, 300))
    with ThreadPoolExecutor(max_workers=8) as pool:
        codes = sorted(pool.map(lambda _: upload(auth_client, pid, data).status_code, range(10)))
    assert codes == [201] * 6 + [409] * 4
    assert len(auth_client.get(f"/api/projects/{pid}/images").json()) == 6


def test_upload_allowance_needs_a_valid_session(
    client: TestClient, auth_client: TestClient, project: dict
) -> None:
    body = b"\0" * (2 * 1024 * 1024)  # over 1 MB, under 16 MB
    url = f"/api/projects/{project['id']}/images"
    # Signed in: the larger allowance applies, so the route itself answers (not an image).
    signed_in = upload(auth_client, project["id"], body)
    assert signed_in.status_code == 415
    # No cookie, or a forged one: the normal 1 MB limit, before anything is read.
    auth_client.cookies.clear()
    anonymous = upload(auth_client, project["id"], body)
    assert anonymous.status_code == 413
    assert "1000 kB" in anonymous.json()["detail"]
    forged = auth_client.post(
        url,
        files={"file": ("a.png", body, "image/png")},
        headers={"Cookie": "vtol_session=eyJ1aWQiOjF9.forged.signature"},
    )
    assert forged.status_code == 413


def test_view_update_and_validation(auth_client: TestClient, project: dict) -> None:
    image = upload(auth_client, project["id"], make_image(), view="other").json()
    patched = auth_client.patch(f"/api/images/{image['id']}", json={"view": "side"})
    assert patched.status_code == 200
    assert patched.json()["view"] == "side"
    assert auth_client.patch(f"/api/images/{image['id']}", json={"view": "back"}).status_code == 422
    bad = upload(auth_client, project["id"], make_image(), view="underneath")
    assert bad.status_code == 422
    assert auth_client.patch("/api/images/999999", json={"view": "top"}).status_code == 404
    assert auth_client.get("/api/images/999999/file").status_code == 404


def test_delete_image_removes_file(
    app: FastAPI, auth_client: TestClient, project: dict, settings: Settings
) -> None:
    image = upload(auth_client, project["id"], make_image()).json()
    with app.state.session_factory() as db:
        row = db.get(ImageRow, image["id"])
        assert row is not None
        path = settings.images_dir / str(project["id"]) / row.storage_name
    assert path.is_file()
    assert auth_client.delete(f"/api/images/{image['id']}").status_code == 204
    assert not path.exists()
    assert auth_client.get(image["url"]).status_code == 404
    assert auth_client.delete(f"/api/images/{image['id']}").status_code == 404


def test_deleting_project_deletes_images_and_directory(
    app: FastAPI, auth_client: TestClient, project: dict, settings: Settings
) -> None:
    pid = project["id"]
    ids = [upload(auth_client, pid, make_image()).json()["id"] for _ in range(2)]
    directory: Path = settings.images_dir / str(pid)
    assert len(list(directory.iterdir())) == 2
    assert auth_client.delete(f"/api/projects/{pid}").status_code == 204
    assert not directory.exists()
    with app.state.session_factory() as db:
        assert all(db.get(ImageRow, i) is None for i in ids)


def test_unknown_project(auth_client: TestClient) -> None:
    assert upload(auth_client, 999999, make_image()).status_code == 404
    assert auth_client.get("/api/projects/999999/images").status_code == 404


def test_upload_requires_csrf_header(app: FastAPI, auth_client: TestClient, project: dict) -> None:
    response = auth_client.post(
        f"/api/projects/{project['id']}/images",
        files={"file": ("a.png", make_image(), "image/png")},
        headers={"X-Requested-With": ""},
    )
    assert response.status_code == 403


def test_sanitise_filename() -> None:
    assert sanitise_filename("C:\\Users\\me\\IMG 001.JPG") == "IMG 001.JPG"
    assert sanitise_filename("../../etc/passwd") == "passwd"
    assert sanitise_filename("") == "image"
    assert sanitise_filename("....") == "image"
    assert len(sanitise_filename("a" * 500 + ".png")) == 120

"""Phase 5 file exports: the ``exports`` table, the ``export`` worker job (CAD in a child
process), downloads, the ZIP, piece previews and cascade deletes.

Most tests use the fast fake generator (``tests/export_fakes.py`` through the
``EXPORT_FAKE_GENERATOR`` seam); ``test_real_export_of_a_small_design`` runs the real CAD
kernel once on a deliberately small design.
"""

from __future__ import annotations

import base64
import copy
import csv
import io
import json
import os
import time
import zipfile
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import inspect, select

from app.cad import model as cad_model
from app.cad.bom import PHASE4, build_bom
from app.config import Settings
from app.defaults import DEFAULT_DESIGN_PARAMETERS, DEFAULT_MISSION, DEFAULT_SETTINGS
from app.exports import UNEXPECTED_MESSAGE, recover_exports
from app.models import Analysis, Export, Project
from app.parts_catalog.load import load_file
from tests.conftest import BACKEND_DIR

FAKE = "tests.export_fakes:generate_files"
SEED = BACKEND_DIR / "seed" / "parts.json"


@pytest.fixture
def fake(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> Settings:
    monkeypatch.setattr(settings, "export_fake_generator", FAKE)
    return settings


def _project(client: TestClient, name: str = "Exports") -> dict[str, Any]:
    response = client.post("/api/projects", json={"name": name})
    assert response.status_code == 201, response.text
    return response.json()


def _start(client: TestClient, project_id: int, source: Any = "draft") -> dict[str, Any]:
    response = client.post(f"/api/projects/{project_id}/exports", json={"source": source})
    assert response.status_code == 202, response.text
    return response.json()


def _wait(client: TestClient, export_id: int, timeout: float = 60.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while True:
        body = client.get(f"/api/exports/{export_id}").json()
        if body["status"] in ("done", "error"):
            return body
        assert time.monotonic() < deadline, body
        time.sleep(0.05)


def _wait_status(client: TestClient, export_id: int, status: str, timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    while client.get(f"/api/exports/{export_id}").json()["status"] != status:
        assert time.monotonic() < deadline
        time.sleep(0.05)


def _export_dir(settings: Settings, export_id: int) -> Path:
    return settings.files_dir / "exports" / str(export_id)


# ---------------------------------------------------------------------------
# Table
# ---------------------------------------------------------------------------


def test_migration_0005_exports_table(app: FastAPI) -> None:
    inspector = inspect(app.state.engine)
    assert "exports" in inspector.get_table_names()
    fks = {
        fk["constrained_columns"][0]: fk["options"].get("ondelete")
        for fk in inspector.get_foreign_keys("exports")
    }
    assert fks == {
        "owner_id": "CASCADE",
        "project_id": "CASCADE",
        "version_id": "CASCADE",
        "reused_from_id": "SET NULL",
    }
    columns = {c["name"] for c in inspector.get_columns("exports")}
    assert {
        "source",
        "inputs",
        "inputs_hash",
        "status",
        "progress",
        "stage",
        "error",
        "manifest",
        "files_dir",
        "total_size_bytes",
        "duration_s",
        "peak_rss_mb",
        "started_at",
        "finished_at",
        "created_at",
        "updated_at",
    } <= columns


# ---------------------------------------------------------------------------
# Lifecycle with the fake generator
# ---------------------------------------------------------------------------


def test_fake_export_lifecycle_downloads_and_zip(auth_client: TestClient, fake: Settings) -> None:
    project = _project(auth_client)
    item = _start(auth_client, project["id"])
    assert item["status"] in ("queued", "running")
    assert item["source"] == "draft" and item["version_id"] is None
    assert item["parts"] == "generic" and item["analysis_id"] is None
    assert item["zip_url"] is None and item["summary"] is None
    body = _wait(auth_client, item["id"])
    assert body["status"] == "done", body
    assert body["progress"] == 1.0 and body["stage"] == "Done" and body["error"] is None
    assert body["duration_s"] > 0 and body["peak_rss_mb"] > 0
    assert body["finished_at"].endswith("Z")
    manifest = body["manifest"]
    # The generator ran in a child process, with the snapshotted inputs.
    assert manifest["fake"]["pid"] != os.getpid()
    assert manifest["fake"]["project"]["project"] == "Exports"
    assert manifest["fake"]["project"]["version"] == "Draft"
    assert manifest["fake"]["mesh_tolerance_mm"] == 0.05
    assert manifest["fake"]["settings_schema"] == DEFAULT_SETTINGS["schema_version"]
    paths = [f["path"] for f in manifest["files"]]
    assert body["file_count"] == len(paths) == 11
    assert body["summary"]["pieces"] == 2 and body["summary"]["all_pieces_fit"] is True
    assert body["summary"]["groups"]["print"] == 4
    assert body["summary"]["bed_mm"] == [256.0, 256.0]
    assert body["zip_url"] == f"/api/exports/{item['id']}/zip"
    folder = _export_dir(fake, item["id"])
    on_disk = sum(p.stat().st_size for p in folder.rglob("*") if p.is_file())
    assert body["total_size_bytes"] == on_disk

    listed = auth_client.get(f"/api/projects/{project['id']}/exports").json()
    assert [r["id"] for r in listed] == [item["id"]]
    assert "manifest" not in listed[0]

    expected_types = {
        "stl": "model/stl",
        "3mf": "model/3mf",
        "step": "model/step",
        "pdf": "application/pdf",
        "dxf": "image/vnd.dxf",
        "csv": "text/csv; charset=utf-8",
        "md": "text/markdown; charset=utf-8",
        "json": "application/json",
    }
    for rel in [*paths, "manifest.json"]:
        r = auth_client.get(f"/api/exports/{item['id']}/files/{rel}")
        assert r.status_code == 200, rel
        assert r.content == (folder / rel).read_bytes()
        assert r.headers["content-type"] == expected_types[rel.rsplit(".", 1)[1]]
        assert r.headers["content-disposition"].startswith("attachment;")
        assert Path(rel).name in r.headers["content-disposition"]
        assert r.headers["cache-control"] == "private, max-age=3600"

    z = auth_client.get(f"/api/exports/{item['id']}/zip")
    assert z.status_code == 200
    assert z.headers["content-type"] == "application/zip"
    assert z.headers["content-disposition"] == (
        f'attachment; filename="exports-draft-files-{item["id"]}.zip"'
    )
    with zipfile.ZipFile(io.BytesIO(z.content)) as zf:
        assert zf.testzip() is None
        root = f"exports-draft-files-{item['id']}/"
        names = zf.namelist()
        assert sorted(names) == sorted(root + p for p in [*paths, "manifest.json"])
        for rel in paths:
            assert zf.read(root + rel) == (folder / rel).read_bytes()
        assert zf.getinfo(root + "print/all_pieces.3mf").compress_type == zipfile.ZIP_STORED
        assert zf.getinfo(root + "cad/assembly.step").compress_type == zipfile.ZIP_DEFLATED

    mesh = auth_client.get(f"/api/exports/{item['id']}/pieces/wing_right_02of02/mesh")
    assert mesh.status_code == 200
    m = mesh.json()
    assert m["label"] == "Wing R 2/2" and m["part_key"] == "wing_right"
    assert m["bed_mm"] == [256.0, 256.0] and m["envelope_mm"] == [240.0, 240.0, 240.0]
    assert m["triangles"] == 12 and m["vertices"] == 8 and m["decimated"] is False
    pos = np.frombuffer(base64.b64decode(m["positions"]), dtype="<f4").reshape(-1, 3)
    idx = np.frombuffer(base64.b64decode(m["indices"]), dtype="<u4").reshape(-1, 3)
    assert pos.shape == (8, 3) and idx.shape == (12, 3) and idx.max() == 7
    assert m["bounds_mm"] == {"min": [0.0, 0.0, 0.0], "max": [100.0, 100.0, 100.0]}

    assert auth_client.delete(f"/api/exports/{item['id']}").status_code == 204
    assert auth_client.get(f"/api/exports/{item['id']}").status_code == 404
    assert not folder.exists()


def test_reuse_by_inputs_hash(app: FastAPI, auth_client: TestClient, fake: Settings) -> None:
    project = _project(auth_client)
    first = _wait(auth_client, _start(auth_client, project["id"])["id"])
    again = _start(auth_client, project["id"])
    assert again["id"] == first["id"] and again["status"] == "done"
    # A version with the same design but its own title block: a new export.
    version = auth_client.post(
        f"/api/projects/{project['id']}/versions", json={"name": "V1"}
    ).json()
    v_item = _start(auth_client, project["id"], {"version_id": version["id"]})
    assert v_item["id"] != first["id"]
    assert v_item["source"] == "version" and v_item["version_number"] == version["number"]
    _wait(auth_client, v_item["id"])
    # Same inputs in another project of the same name pattern is not reachable (names are
    # unique), so check the hard-link path with a manually copied row: same hash, other
    # project -> files linked into a new export, reused_from_id set.
    other = _project(auth_client, "Other")
    with app.state.session_factory() as db:
        row = db.get(Export, first["id"])
        assert row is not None
        clone_inputs = copy.deepcopy(row.inputs)
        digest = row.inputs_hash
    from app.routers import exports as router_module

    real_hash = router_module.ex.inputs_hash
    try:
        router_module.ex.inputs_hash = lambda _inputs: digest  # type: ignore[assignment]
        reused = _start(auth_client, other["id"])
    finally:
        router_module.ex.inputs_hash = real_hash  # type: ignore[assignment]
    assert reused["status"] == "done" and reused["reused_from_id"] == first["id"]
    assert reused["stage"] == "Reused identical earlier files"
    src, dst = _export_dir(fake, first["id"]), _export_dir(fake, reused["id"])
    assert (dst / "bom.csv").read_bytes() == (src / "bom.csv").read_bytes()
    assert (dst / "bom.csv").stat().st_ino == (src / "bom.csv").stat().st_ino  # hard link
    # Deleting the original keeps the reused export's files.
    assert auth_client.delete(f"/api/exports/{first['id']}").status_code == 204
    assert auth_client.get(f"/api/exports/{reused['id']}/files/bom.csv").status_code == 200
    assert auth_client.get(f"/api/exports/{reused['id']}/zip").status_code == 200
    assert clone_inputs["project"]["project"] == "Exports"
    # Missing files are never reused.
    (dst / "bom.csv").unlink()
    assert auth_client.get(f"/api/exports/{reused['id']}/zip").status_code == 410


def test_pending_export_is_returned_and_delete_cancels_it(
    app: FastAPI, auth_client: TestClient, fake: Settings
) -> None:
    project = _project(auth_client, "slow")
    first = _start(auth_client, project["id"])
    assert _start(auth_client, project["id"])["id"] == first["id"]
    _wait_status(auth_client, first["id"], "running")
    deadline = time.monotonic() + 10
    while auth_client.get(f"/api/exports/{first['id']}").json()["progress"] <= 0.02:
        assert time.monotonic() < deadline  # progress relayed from the child
        time.sleep(0.05)
    running = auth_client.get(f"/api/exports/{first['id']}").json()
    assert running["stage"].startswith("Slow step")
    assert auth_client.delete(f"/api/exports/{first['id']}").status_code == 204
    assert not _export_dir(fake, first["id"]).exists()
    # The worker is free again at once (the child was killed).
    other = _project(auth_client, "after")
    done = _wait(auth_client, _start(auth_client, other["id"])["id"], timeout=20)
    assert done["status"] == "done", done
    assert not _export_dir(fake, first["id"]).exists()


def test_queue_position_while_waiting(auth_client: TestClient, fake: Settings) -> None:
    slow = _project(auth_client, "slow")
    first = _start(auth_client, slow["id"])
    _wait_status(auth_client, first["id"], "running")
    second = _start(auth_client, _project(auth_client, "next")["id"])
    assert second["status"] == "queued" and second["queue_position"] == 1
    auth_client.delete(f"/api/exports/{first['id']}")
    assert _wait(auth_client, second["id"])["status"] == "done"


@pytest.mark.parametrize(
    ("mode", "message"),
    [
        ("fail-cad", "The wing is too thin at the tip for the 10 mm spar tube."),
        ("fail-envelope", "Piece Wing R 1/2 (250 x 120 x 30 mm) does not fit the printer."),
        ("crash", UNEXPECTED_MESSAGE),
        ("die", "The CAD process stopped unexpectedly (exit code 3)."),
    ],
)
def test_export_errors_are_plain_messages(
    auth_client: TestClient, fake: Settings, mode: str, message: str
) -> None:
    project = _project(auth_client, mode)
    body = _wait(auth_client, _start(auth_client, project["id"])["id"])
    assert body["status"] == "error"
    assert body["error"].startswith(message)
    assert body["manifest"] is None and body["zip_url"] is None
    assert "Traceback" not in body["error"] and "RuntimeError" not in body["error"]
    assert not _export_dir(fake, body["id"]).exists()
    assert auth_client.get(f"/api/exports/{body['id']}/zip").status_code == 404
    assert auth_client.get(f"/api/exports/{body['id']}/files/bom.csv").status_code == 404


def test_timeout_kills_the_child(
    auth_client: TestClient, fake: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(fake, "export_timeout_s", 1.5)
    project = _project(auth_client, "slow")
    t0 = time.monotonic()
    body = _wait(auth_client, _start(auth_client, project["id"])["id"], timeout=20)
    assert time.monotonic() - t0 < 10
    assert body["status"] == "error"
    assert body["error"].startswith("Making the files took longer than 1.5 seconds")


def test_memory_guard_kills_the_child(
    auth_client: TestClient, fake: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(fake, "export_memory_limit_mb", 250.0)
    project = _project(auth_client, "hog")
    body = _wait(auth_client, _start(auth_client, project["id"])["id"], timeout=30)
    assert body["status"] == "error"
    assert body["error"].startswith("Making the files needed more than 250 MB of memory")
    assert body["peak_rss_mb"] > 250


def test_worker_not_running_answers_503(app: FastAPI, auth_client: TestClient) -> None:
    project = _project(auth_client)
    worker = app.state.analysis_worker
    app.state.analysis_worker = None
    try:
        r = auth_client.post(f"/api/projects/{project['id']}/exports", json={})
    finally:
        app.state.analysis_worker = worker
    assert r.status_code == 503 and "starting up" in r.json()["detail"]


def test_version_of_another_project_is_422(auth_client: TestClient, fake: Settings) -> None:
    a, b = _project(auth_client, "A"), _project(auth_client, "B")
    v = auth_client.post(f"/api/projects/{b['id']}/versions", json={"name": "V"}).json()
    r = auth_client.post(
        f"/api/projects/{a['id']}/exports", json={"source": {"version_id": v["id"]}}
    )
    assert r.status_code == 422
    assert auth_client.post(f"/api/projects/{a['id']}/exports", json={"source": "x"}).status_code
    assert auth_client.post("/api/projects/999999/exports", json={}).status_code == 404


# ---------------------------------------------------------------------------
# Download path safety
# ---------------------------------------------------------------------------


def test_downloads_only_serve_manifest_files(auth_client: TestClient, fake: Settings) -> None:
    project = _project(auth_client)
    body = _wait(auth_client, _start(auth_client, project["id"])["id"])
    eid = body["id"]
    folder = _export_dir(fake, eid)
    (folder / "secret.txt").write_text("not listed")
    (fake.files_dir / "outside.txt").write_text("outside")
    for bad in (
        "secret.txt",
        "print",
        "print/stl",
        "print/stl/",
        # Dot segments percent-encoded: the HTTP client would otherwise resolve them itself.
        "print/%2e%2e/bom.csv",
        "%2e/bom.csv",
        "%2e%2e/outside.txt",
        "%2e%2e/%2e%2e/%2e%2e/app.db",
        "..%2F..%2Fapp.db",
        "%2e%2e/%2e%2e/app.db",
        "/etc/passwd",
        "BOM.CSV",
        "bom.csv/",
        "bom.csv%00",
        "",
    ):
        r = auth_client.get(f"/api/exports/{eid}/files/{bad}")
        assert r.status_code == 404, (bad, r.status_code)
        assert r.content != b"not listed" and b"outside" not in r.content
    assert auth_client.get(f"/api/exports/{eid}/files/bom.csv").status_code == 200
    for bad_piece in ("nope", "..", "wing_right_01of02.stl", "bom"):
        assert auth_client.get(f"/api/exports/{eid}/pieces/{bad_piece}/mesh").status_code == 404
    assert auth_client.get(f"/api/exports/{eid + 999}/files/bom.csv").status_code == 404
    # A listed file that was swapped for a symlink out of the folder is refused.
    target = folder / "notes" / "wing_right.md"
    target.unlink()
    target.symlink_to(fake.files_dir / "outside.txt")
    assert auth_client.get(f"/api/exports/{eid}/files/notes/wing_right.md").status_code == 404
    # Not finished yet: nothing to download.
    slow = _start(auth_client, _project(auth_client, "slow")["id"])
    assert auth_client.get(f"/api/exports/{slow['id']}/files/bom.csv").status_code == 404
    assert auth_client.get(f"/api/exports/{slow['id']}/zip").status_code == 404
    assert auth_client.delete(f"/api/exports/{slow['id']}").status_code == 204


# ---------------------------------------------------------------------------
# Cascades
# ---------------------------------------------------------------------------


def test_deleting_a_version_or_project_removes_exports_and_files(
    app: FastAPI, auth_client: TestClient, fake: Settings
) -> None:
    project = _project(auth_client)
    version = auth_client.post(
        f"/api/projects/{project['id']}/versions", json={"name": "V1"}
    ).json()
    v_export = _wait(
        auth_client, _start(auth_client, project["id"], {"version_id": version["id"]})["id"]
    )
    d_export = _wait(auth_client, _start(auth_client, project["id"])["id"])
    assert _export_dir(fake, v_export["id"]).is_dir()
    # Exports never block deleting a version (no 409, unlike analyses).
    assert auth_client.delete(f"/api/versions/{version['id']}").status_code == 204
    assert auth_client.get(f"/api/exports/{v_export['id']}").status_code == 404
    assert not _export_dir(fake, v_export["id"]).exists()
    assert _export_dir(fake, d_export["id"]).is_dir()
    # A running export is stopped when its project goes.
    slow = _project(auth_client, "slow")
    running = _start(auth_client, slow["id"])
    _wait_status(auth_client, running["id"], "running")
    assert auth_client.delete(f"/api/projects/{slow['id']}").status_code == 204
    assert auth_client.delete(f"/api/projects/{project['id']}").status_code == 204
    with app.state.session_factory() as db:
        assert db.scalars(select(Export)).all() == []
    assert not _export_dir(fake, d_export["id"]).exists()
    time.sleep(0.6)  # the cancelled job's own clean-up
    assert not _export_dir(fake, running["id"]).exists()
    done = _wait(auth_client, _start(auth_client, _project(auth_client, "after")["id"])["id"])
    assert done["status"] == "done"


def test_restart_recovery(app: FastAPI, auth_client: TestClient, fake: Settings) -> None:
    project = _project(auth_client)
    with app.state.session_factory() as db:
        owner_id = db.get(Project, project["id"]).owner_id  # type: ignore[union-attr]
        rows = []
        for status in ("running", "queued"):
            row = Export(
                owner_id=owner_id,
                project_id=project["id"],
                source="draft",
                inputs={
                    "parameters": DEFAULT_DESIGN_PARAMETERS,
                    "mission": DEFAULT_MISSION,
                    "settings": DEFAULT_SETTINGS,
                    "project": {"project": "Exports", "version": "Draft", "date": "2026-10-09"},
                },
                inputs_hash=status * 8,
                status=status,
                progress=0.3,
                stage="x",
            )
            db.add(row)
            rows.append(row)
        db.commit()
        running_id, queued_id = rows[0].id, rows[1].id
    _export_dir(fake, running_id).mkdir(parents=True)
    (_export_dir(fake, running_id) / "partial.stl").write_bytes(b"x")
    orphan = fake.exports_dir / "424242"
    orphan.mkdir(parents=True)
    failed, queued = recover_exports(app.state.session_factory, fake)
    assert failed == 1 and queued == [queued_id]
    assert not _export_dir(fake, running_id).exists() and not orphan.exists()
    body = auth_client.get(f"/api/exports/{running_id}").json()
    assert body["status"] == "error" and "interrupted" in body["error"]
    app.state.analysis_worker.submit_export(queued_id)
    assert _wait(auth_client, queued_id)["status"] == "done"


# ---------------------------------------------------------------------------
# Inputs: selected parts and the latest analysis
# ---------------------------------------------------------------------------


def test_inputs_use_selected_parts_and_matching_analysis(
    app: FastAPI, auth_client: TestClient, fake: Settings
) -> None:
    with app.state.session_factory() as db:
        load_file(db, SEED)
    project = _project(auth_client)
    draft = auth_client.get(f"/api/projects/{project['id']}/draft").json()
    with app.state.session_factory() as db:
        owner_id = db.get(Project, project["id"]).owner_id  # type: ignore[union-attr]
        for params, cg in ((draft["parameters"], 321.0), ({"stale": True}, 999.0)):
            db.add(
                Analysis(
                    owner_id=owner_id,
                    project_id=project["id"],
                    kind="full",
                    inputs={"parameters": params, "mission": draft["mission"]},
                    inputs_hash="h" * 64,
                    status="done",
                    progress=1.0,
                    stage="Done",
                    result={
                        "valid": True,
                        "balance": {"cg_max_payload_x": {"value": cg, "unit": "mm"}},
                        "structure": {"spar_sizing": {"outer_mm": 12.0}},
                        "summary": {},
                    },
                )
            )
            db.commit()
        matching_id = db.scalar(
            select(Analysis.id).where(Analysis.project_id == project["id"]).order_by(Analysis.id)
        )
    item = _start(auth_client, project["id"])
    assert item["parts"] == "selected"
    assert item["analysis_id"] == matching_id  # the newer row is for other parameters
    body = _wait(auth_client, item["id"])
    fake_info = body["manifest"]["fake"]
    assert fake_info["analysis"] == {
        "balance": {"cg_max_payload_x": {"value": 321.0, "unit": "mm"}},
        "structure": {"spar_sizing": {"outer_mm": 12.0}},
    }
    roles = set(fake_info["parts_roles"])
    assert {"lift_motor", "lift_prop", "esc", "battery", "spar_tube"} <= roles
    with app.state.session_factory() as db:
        row = db.get(Export, item["id"])
        assert row is not None
        sel = {i["role"]: i for i in row.inputs["parts_selection"]}
    parts_list = auth_client.get(f"/api/projects/{project['id']}/parts-list").json()
    for r in parts_list["roles"]:
        if not r["filled"]:
            continue
        s = sel[r["role"]]
        assert s["part_id"] == r["part"]["id"] and s["quantity"] == r["quantity"]
        assert s["mass_g"] * s["quantity"] == pytest.approx(r["line_mass_g"], abs=0.01)
        assert s["price_eur"] == r["unit_price_eur"]
    bom = (_export_dir(fake, item["id"]) / "bom.csv").read_text()
    assert parts_list["roles"][0]["part"]["model"] in bom


def test_bom_maps_phase4_roles_without_double_counting() -> None:
    model = cad_model.build_model(DEFAULT_DESIGN_PARAMETERS, DEFAULT_MISSION, DEFAULT_SETTINGS)
    tubes = [
        {"key": "spar_right", "label": "Wing spar tube (right)", "od_mm": 12.0, "wall_mm": 1.0,
         "length_mm": 800.0},
        {"key": "boom_right", "label": "Boom (right)", "od_mm": 20.0, "wall_mm": 1.0,
         "length_mm": 700.0},
        {"key": "spar_tail", "label": "Spar tube (Tail)", "od_mm": 6.0, "wall_mm": 1.0,
         "length_mm": 300.0},
    ]  # fmt: skip
    selection = [
        {"role": r, "category": c, "manufacturer": "M", "model": r, "quantity": q,
         "mass_g": 10.0, "price_eur": 5.0}
        for r, c, q in (
            ("lift_prop", "propeller", 4),
            ("esc", "esc", 4),
            ("autopilot", "autopilot", 1),
            ("spar_tube", "carbon_tube", 2),
        )
    ]  # fmt: skip
    rows, totals = build_bom(model, [], tubes, [], [], selection)
    roles = [r["role"] for r in rows]
    assert "lift_propeller" not in roles and "lift_esc" not in roles and "avionics" not in roles
    assert "lift_motor" in roles  # not selected: the generic line stays
    spar = next(r for r in rows if str(r["item"]).startswith("Wing spar tube"))
    assert spar["line mass g"] == "" and spar["line price €"] == ""
    assert "cut from the selected tube" in spar["notes"]
    boom = next(r for r in rows if str(r["item"]).startswith("Boom"))
    assert boom["line mass g"] != ""  # boom tube not selected
    assert totals["selection_mass_g"] == pytest.approx(10.0 * (4 + 4 + 1 + 2))
    assert not any(r["model"] == PHASE4 and r["role"] == "lift_propeller" for r in rows)


def test_cad_model_reads_phase4_role_names() -> None:
    selection = [
        {
            "role": "spar_tube",
            "category": "carbon_tube",
            "spec": {"outer_diameter_mm": 10.0, "inner_diameter_mm": 8.0},
            "mass_g": 40.0,
            "quantity": 1,
        },
        {
            "role": "battery",
            "category": "cell",
            "spec": {"diameter_mm": 21.0, "length_mm": 70.0},
            "mass_g": 70.0,
            "quantity": 12,
        },
    ]
    model = cad_model.build_model(
        DEFAULT_DESIGN_PARAMETERS, DEFAULT_MISSION, DEFAULT_SETTINGS, parts_selection=selection
    )
    assert model.spar["outer_mm"] == 10.0 and model.spar["wall_mm"] == 1.0
    assert model.spar["source"] == "Phase 4 selection"


# ---------------------------------------------------------------------------
# The real CAD kernel, once (about 40 s)
# ---------------------------------------------------------------------------


def _small_design(parameters: dict[str, Any]) -> dict[str, Any]:
    p = copy.deepcopy(parameters)
    p["wing"].update(span_mm=1000.0, root_chord_mm=200.0, tip_chord_mm=160.0)
    p["fuselage"].update(length_mm=700.0)
    p["booms"].update(lateral_offset_mm=250.0, length_mm=560.0, x_offset_mm=-200.0)
    p["motors"].update(rear_x_mm=520.0)
    p["pusher"].update(x_mm=680.0)
    p["tail"].update(span_mm=360.0, chord_mm=110.0, arm_mm=420.0)
    p["propulsion"].update(prop_diameter_mm=250.0)
    return p


def test_real_export_of_a_small_design(app: FastAPI, auth_client: TestClient) -> None:
    with app.state.session_factory() as db:
        load_file(db, SEED)
    project = _project(auth_client, "Small")
    draft = auth_client.get(f"/api/projects/{project['id']}/draft").json()
    r = auth_client.put(
        f"/api/projects/{project['id']}/draft",
        json={"parameters": _small_design(draft["parameters"]), "mission": draft["mission"]},
    )
    assert r.status_code == 200, r.text
    item = _start(auth_client, project["id"])
    assert item["parts"] == "selected"
    body = _wait(auth_client, item["id"], timeout=240)
    assert body["status"] == "done", body["error"]
    manifest = body["manifest"]
    assert manifest["schema"] == "vtol-files/1"
    assert manifest["checks"]["all_pieces_fit"] is True
    assert manifest["checks"]["watertight_all"] is True
    assert manifest["construction"]["parts_selection"] == "Phase 4 selection"
    # The child process's peak (OpenCascade loaded there, not in the API process).
    assert 300 < body["peak_rss_mb"] < 1500
    # BOM: the selected parts, not the generic Phase 4 placeholders for those roles.
    bom = auth_client.get(f"/api/exports/{item['id']}/files/bom.csv")
    assert bom.status_code == 200 and bom.headers["content-type"] == "text/csv; charset=utf-8"
    rows = list(csv.DictReader(io.StringIO(bom.text)))
    parts_list = auth_client.get(f"/api/projects/{project['id']}/parts-list").json()
    filled = [x for x in parts_list["roles"] if x["filled"]]
    assert filled
    for role in filled:
        line = next(x for x in rows if x["role"] == role["role"])
        assert line["model"] == role["part"]["model"]
        assert float(line["line mass g"]) == pytest.approx(role["line_mass_g"], abs=0.05)
    assert not any(x["model"] == PHASE4 for x in rows if x["role"] == "lift_motor")
    # ZIP: every listed file plus the manifest, intact.
    z = auth_client.get(f"/api/exports/{item['id']}/zip")
    with zipfile.ZipFile(io.BytesIO(z.content)) as zf:
        assert zf.testzip() is None
        names = {n.split("/", 1)[1] for n in zf.namelist()}
        assert names == {f["path"] for f in manifest["files"]} | {"manifest.json"}
        assert (
            json.loads(zf.read(f"small-draft-files-{item['id']}/manifest.json"))["files"]
            == manifest["files"]
        )
    # Piece preview in print orientation.
    piece = manifest["parts"][0]["pieces"][0]
    pid = Path(piece["stl"]).stem
    mesh = auth_client.get(f"/api/exports/{item['id']}/pieces/{pid}/mesh").json()
    assert mesh["label"] == piece["label"] and mesh["fits"] is True
    assert mesh["triangles"] == piece["triangles"] and not mesh["decimated"]
    size = np.array(mesh["bounds_mm"]["max"]) - np.array(mesh["bounds_mm"]["min"])
    assert np.allclose(size, piece["size_mm"], atol=0.05)
    assert mesh["bed_mm"] == manifest["printer"]["bed_mm"]

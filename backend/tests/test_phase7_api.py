"""Phase 7 API: mould sets (rows of ``exports`` with ``kind = "moulds"``, migration 0007) run in
the export child process, with downloads, the ZIP, tile previews, reuse, errors and cascade
deletes; and the synchronous full-scale checks of the draft or a version.

Most mould tests use the fast fake generator (``tests/export_fakes.py:generate_moulds`` through
the ``MOULD_FAKE_GENERATOR`` seam); ``test_real_nose_mould_through_the_api`` runs the real mould
library once for the nose bay only.
"""

from __future__ import annotations

import base64
import copy
import io
import os
import time
import zipfile
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, text

from app.config import Settings
from app.defaults import DEFAULT_DESIGN_PARAMETERS, DEFAULT_MISSION, DEFAULT_SETTINGS
from app.models import Analysis, Export
from app.parts_catalog.load import load_file
from tests.conftest import BACKEND_DIR

FAKE = "tests.export_fakes:generate_moulds"
FAKE_FILES = "tests.export_fakes:generate_files"
SEED = BACKEND_DIR / "seed" / "parts.json"
FULLSCALE_KEYS = {
    "fullscale.mtow",
    "fullscale.mtow_layup",
    "fullscale.motor_out",
    "fullscale.spar_tube",
    "fullscale.boom_tube",
    "fullscale.landing_gear",
    "fullscale.battery_current",
}


@pytest.fixture
def fake(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> Settings:
    monkeypatch.setattr(settings, "mould_fake_generator", FAKE)
    monkeypatch.setattr(settings, "export_fake_generator", FAKE_FILES)
    return settings


def _project(client: TestClient, name: str = "Moulds") -> dict[str, Any]:
    response = client.post("/api/projects", json={"name": name})
    assert response.status_code == 201, response.text
    return response.json()


def _start(client: TestClient, project_id: int, **body: Any) -> dict[str, Any]:
    response = client.post(f"/api/projects/{project_id}/moulds", json=body)
    assert response.status_code == 202, response.text
    return response.json()


def _wait(client: TestClient, mould_id: int, timeout: float = 60.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while True:
        body = client.get(f"/api/moulds/{mould_id}").json()
        if body["status"] in ("done", "error"):
            return body
        assert time.monotonic() < deadline, body
        time.sleep(0.05)


def _dir(settings: Settings, mould_id: int) -> Path:
    return settings.files_dir / "exports" / str(mould_id)


# ---------------------------------------------------------------------------
# Migration 0007
# ---------------------------------------------------------------------------


def test_migration_0007_adds_kind_and_keeps_autoincrement(app: FastAPI) -> None:
    inspector = inspect(app.state.engine)
    columns = {c["name"]: c for c in inspector.get_columns("exports")}
    assert "kind" in columns and columns["kind"]["nullable"] is False
    assert "ix_exports_kind" in {i["name"] for i in inspector.get_indexes("exports")}
    with app.state.engine.connect() as conn:
        sql = conn.execute(
            text("SELECT sql FROM sqlite_master WHERE type='table' AND name='exports'")
        ).scalar()
    assert "AUTOINCREMENT" in sql


def test_migration_0007_downgrade_and_upgrade(tmp_path: Path) -> None:
    url = f"sqlite:///{tmp_path / 'm.db'}"
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "head")
    engine = create_engine(url)
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO users (id, email, display_name, is_owner, created_at, "
                "updated_at) VALUES (1, 'o@example.com', 'O', 1, '2026-01-01', "
                "'2026-01-01')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO projects (id, owner_id, name, description, "
                "draft_parameters, draft_mission, draft_updated_at, next_version_number, "
                "created_at, updated_at) VALUES (1, 1, 'P', '', '{}', '{}', '2026-01-01', 1, "
                "'2026-01-01', '2026-01-01')"
            )
        )
        for kind in ("files", "moulds"):
            conn.execute(
                text(
                    "INSERT INTO exports (owner_id, project_id, kind, source, inputs, "
                    "inputs_hash, status, progress, stage, created_at, updated_at) VALUES "
                    "(1, 1, :kind, 'draft', '{}', 'h', 'done', 1.0, 'Done', '2026-01-01', "
                    "'2026-01-01')"
                ),
                {"kind": kind},
            )
    command.downgrade(cfg, "0006")
    with engine.connect() as conn:
        cols = {c["name"] for c in inspect(conn).get_columns("exports")}
        assert "kind" not in cols
        assert conn.execute(text("SELECT count(*) FROM exports")).scalar() == 1  # files kept
        sql = conn.execute(
            text("SELECT sql FROM sqlite_master WHERE type='table' AND name='exports'")
        ).scalar()
        assert "AUTOINCREMENT" in sql
        fks = {fk["constrained_columns"][0] for fk in inspect(conn).get_foreign_keys("exports")}
        assert fks == {"owner_id", "project_id", "version_id", "reused_from_id"}
    command.upgrade(cfg, "head")
    with engine.connect() as conn:
        assert conn.execute(text("SELECT kind FROM exports")).scalars().all() == ["files"]
    engine.dispose()


# ---------------------------------------------------------------------------
# Mould sets with the fake generator
# ---------------------------------------------------------------------------


def test_fake_mould_lifecycle_downloads_zip_preview_delete(
    auth_client: TestClient, fake: Settings
) -> None:
    project = _project(auth_client)
    item = _start(auth_client, project["id"])
    assert item["status"] in ("queued", "running")
    assert item["mould_parts"] == ["nose", "fuselage", "wing_root_fairing"]
    assert item["options"] == {"min_draft_deg": 2.0, "vent_channels": False}
    assert item["zip_url"] is None and item["summary"] is None
    body = _wait(auth_client, item["id"])
    assert body["status"] == "done", body
    assert body["progress"] == 1.0 and body["error"] is None
    manifest = body["manifest"]
    assert manifest["schema"] == "vtol-moulds/1"
    assert manifest["fake"]["pid"] != os.getpid()  # ran in the child process
    assert manifest["fake"]["parts"] == ["nose", "fuselage", "wing_root_fairing"]
    assert manifest["fake"]["options"] == {"min_draft_deg": 2.0, "vent_channels": False}
    assert manifest["fake"]["project"]["project"] == "Moulds"
    summary = body["summary"]
    assert [p["part"] for p in summary["parts"]] == ["nose", "fuselage", "wing_root_fairing"]
    assert summary["tiles"] == 8 and summary["all_tiles_fit"] is True
    assert summary["demouldable"] is True and summary["flagged_faces"] == 2
    assert summary["kinds"] == {"stl": 8, "3mf": 8, "step": 6, "pdf": 3}
    assert summary["estimated_mass_g"] > 0
    assert body["zip_url"] == f"/api/moulds/{item['id']}/zip"
    paths = [f["path"] for f in manifest["files"]]
    assert body["file_count"] == len(paths) == 25
    folder = _dir(fake, item["id"])
    on_disk = sum(p.stat().st_size for p in folder.rglob("*") if p.is_file())
    assert body["total_size_bytes"] == on_disk

    listed = auth_client.get(f"/api/projects/{project['id']}/moulds").json()
    assert [r["id"] for r in listed] == [item["id"]] and "manifest" not in listed[0]
    # Mould sets are not file exports, and the other way round.
    assert auth_client.get(f"/api/projects/{project['id']}/exports").json() == []
    assert auth_client.get(f"/api/exports/{item['id']}").status_code == 404
    assert auth_client.get(f"/api/exports/{item['id']}/zip").status_code == 404

    for rel in [*paths, "moulds_manifest.json"]:
        r = auth_client.get(f"/api/moulds/{item['id']}/files/{rel}")
        assert r.status_code == 200, rel
        assert r.content == (folder / rel).read_bytes()
        assert r.headers["content-disposition"].startswith("attachment;")
    assert auth_client.get(f"/api/moulds/{item['id']}/files/manifest.json").status_code == 404
    assert auth_client.get(f"/api/moulds/{item['id']}/files/%2E%2E/app.db").status_code == 404

    z = auth_client.get(f"/api/moulds/{item['id']}/zip")
    assert z.status_code == 200 and z.headers["content-type"] == "application/zip"
    assert z.content[:4] == b"PK\x03\x04"
    root = f"moulds-draft-moulds-{item['id']}"
    assert z.headers["content-disposition"] == f'attachment; filename="{root}.zip"'
    with zipfile.ZipFile(io.BytesIO(z.content)) as zf:
        assert zf.testzip() is None
        assert sorted(zf.namelist()) == sorted(
            f"{root}/{p}" for p in [*paths, "moulds_manifest.json"]
        )

    mesh = auth_client.get(f"/api/moulds/{item['id']}/tiles/FUS-L-02of02/mesh")
    assert mesh.status_code == 200, mesh.text
    m = mesh.json()
    assert m["label"] == "FUS-L 2/2" and m["part_key"] == "fuselage" and m["half"] == "left"
    assert m["fits"] is True and m["filament"] == "PETG"
    assert m["bed_mm"] == [256, 256] and m["envelope_mm"] == [240.0, 240.0, 240.0]
    assert m["orientation"]["description"] == "back down"
    pos = np.frombuffer(base64.b64decode(m["positions"]), dtype="<f4").reshape(-1, 3)
    assert pos.shape == (8, 3) and m["triangles"] == 12
    assert m["bounds_mm"]["max"] == [80.0, 80.0, 80.0]
    assert auth_client.get(f"/api/moulds/{item['id']}/tiles/NOPE/mesh").status_code == 404

    assert auth_client.delete(f"/api/moulds/{item['id']}").status_code == 204
    assert auth_client.get(f"/api/moulds/{item['id']}").status_code == 404
    assert not folder.exists()


def test_part_selection_options_and_validation(auth_client: TestClient, fake: Settings) -> None:
    project = _project(auth_client)
    pid = project["id"]
    nose = _wait(auth_client, _start(auth_client, pid, parts=["nose"])["id"])
    assert [p["key"] for p in nose["manifest"]["parts"]] == ["nose"]
    # Order and duplicates do not matter: the same set reuses the finished one.
    again = _start(auth_client, pid, parts=["nose", "nose"])
    assert again["id"] == nose["id"]
    two = _start(auth_client, pid, parts=["wing_root_fairing", "nose"], min_draft_deg=3)
    assert two["id"] != nose["id"]
    assert two["mould_parts"] == ["nose", "wing_root_fairing"]
    done = _wait(auth_client, two["id"])
    assert done["manifest"]["fake"]["options"] == {"min_draft_deg": 3.0, "vent_channels": False}
    for bad in (
        {"parts": []},
        {"parts": ["wing"]},
        {"min_draft_deg": 0},
        {"min_draft_deg": 45},
        {"source": {"version_id": 999999}},
        {"extra": 1},
    ):
        r = auth_client.post(f"/api/projects/{pid}/moulds", json=bad)
        assert r.status_code == 422, (bad, r.text)


def test_version_source_and_cascade_deletes(
    app: FastAPI, auth_client: TestClient, fake: Settings
) -> None:
    project = _project(auth_client)
    version = auth_client.post(
        f"/api/projects/{project['id']}/versions", json={"name": "V1"}
    ).json()
    item = _start(auth_client, project["id"], source={"version_id": version["id"]}, parts=["nose"])
    assert item["source"] == "version" and item["version_number"] == version["number"]
    body = _wait(auth_client, item["id"])
    assert body["manifest"]["fake"]["project"]["version"] == f"Version {version['number']}: V1"
    folder = _dir(fake, item["id"])
    assert folder.is_dir()
    assert auth_client.delete(f"/api/versions/{version['id']}").status_code == 204
    assert auth_client.get(f"/api/moulds/{item['id']}").status_code == 404
    assert not folder.exists()
    draft_item = _wait(auth_client, _start(auth_client, project["id"], parts=["nose"])["id"])
    folder = _dir(fake, draft_item["id"])
    assert auth_client.delete(f"/api/projects/{project['id']}").status_code == 204
    assert not folder.exists()
    with app.state.session_factory() as db:
        assert db.get(Export, draft_item["id"]) is None


@pytest.mark.parametrize(
    ("mode", "message"),
    [
        ("fail-cad", "The fuselage is too short for a nose bay mould."),
        ("crash", "Something went wrong while making the files."),
        ("die", "The CAD process stopped unexpectedly"),
    ],
)
def test_mould_errors_are_plain_messages(
    auth_client: TestClient, fake: Settings, mode: str, message: str
) -> None:
    project = _project(auth_client, mode)
    body = _wait(auth_client, _start(auth_client, project["id"])["id"])
    assert body["status"] == "error"
    assert body["error"].startswith(message)
    assert body["manifest"] is None and body["zip_url"] is None
    assert "Traceback" not in body["error"]
    assert not _dir(fake, body["id"]).exists()
    assert auth_client.get(f"/api/moulds/{body['id']}/zip").status_code == 404


def test_mould_timeout_uses_its_own_limit(
    auth_client: TestClient, fake: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(fake, "mould_timeout_s", 1.5)
    project = _project(auth_client, "slow")
    body = _wait(auth_client, _start(auth_client, project["id"])["id"], timeout=20)
    assert body["status"] == "error"
    assert body["error"].startswith("Making the moulds took longer than 1.5 seconds")


def test_file_exports_still_work_beside_moulds(auth_client: TestClient, fake: Settings) -> None:
    project = _project(auth_client)
    mould = _wait(auth_client, _start(auth_client, project["id"], parts=["nose"])["id"])
    r = auth_client.post(f"/api/projects/{project['id']}/exports", json={"source": "draft"})
    assert r.status_code == 202
    export_id = r.json()["id"]
    assert export_id != mould["id"]
    deadline = time.monotonic() + 30
    while auth_client.get(f"/api/exports/{export_id}").json()["status"] not in ("done", "error"):
        assert time.monotonic() < deadline
        time.sleep(0.05)
    assert auth_client.get(f"/api/exports/{export_id}").json()["status"] == "done"
    assert [e["id"] for e in auth_client.get(f"/api/projects/{project['id']}/exports").json()] == [
        export_id
    ]
    assert [m["id"] for m in auth_client.get(f"/api/projects/{project['id']}/moulds").json()] == [
        mould["id"]
    ]
    assert auth_client.get(f"/api/moulds/{export_id}").status_code == 404


def test_fake_generator_is_ignored_in_production(settings: Settings) -> None:
    prod = settings.model_copy(update={"app_env": "production", "mould_fake_generator": FAKE})
    assert prod.fake_mould_generator is None
    assert settings.model_copy(update={"mould_fake_generator": FAKE}).fake_mould_generator == FAKE


# ---------------------------------------------------------------------------
# Real mould library through the API (nose only)
# ---------------------------------------------------------------------------


def test_real_nose_mould_through_the_api(auth_client: TestClient) -> None:
    project = _project(auth_client, "Real moulds")
    item = _start(auth_client, project["id"], parts=["nose"])
    body = _wait(auth_client, item["id"], timeout=400)
    assert body["status"] == "done", body["error"]
    manifest = body["manifest"]
    assert manifest["schema"] == "vtol-moulds/1"
    assert [p["key"] for p in manifest["parts"]] == ["nose"]
    assert body["summary"]["all_tiles_fit"] is True and body["summary"]["demouldable"] is True
    assert 200 < body["peak_rss_mb"] < 1500
    tile = manifest["parts"][0]["halves"][0]["tiles"][0]
    stem = Path(tile["files"]["stl"]).stem
    mesh = auth_client.get(f"/api/moulds/{item['id']}/tiles/{stem}/mesh").json()
    assert mesh["fits"] is True and mesh["triangles"] > 100
    size = np.array(mesh["bounds_mm"]["max"]) - np.array(mesh["bounds_mm"]["min"])
    assert np.allclose(sorted(size), sorted(tile["size_mm"]), atol=0.5)
    z = auth_client.get(f"/api/moulds/{item['id']}/zip")
    assert z.content[:4] == b"PK\x03\x04"


# ---------------------------------------------------------------------------
# Full-scale checks
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def scaled_24kg(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    from app.engine.scale import run_scale

    out = run_scale(
        copy.deepcopy(DEFAULT_DESIGN_PARAMETERS),
        copy.deepcopy(DEFAULT_MISSION),
        copy.deepcopy(DEFAULT_SETTINGS),
        24.0,
        mode="fast",
        cache_dir=str(tmp_path_factory.mktemp("polars-phase7")),
    )
    assert out["valid"]
    return {"parameters": out["parameters"], "mission": out["mission"]}


def _put_draft(client: TestClient, project_id: int, design: dict[str, Any]) -> None:
    r = client.put(
        f"/api/projects/{project_id}/draft",
        json={"parameters": design["parameters"], "mission": design["mission"]},
    )
    assert r.status_code == 200, r.text


def test_fullscale_checks_of_a_24kg_draft(
    app: FastAPI, auth_client: TestClient, scaled_24kg: dict[str, Any]
) -> None:
    with app.state.session_factory() as db:
        load_file(db, SEED)
    project = _project(auth_client, "Full scale")
    _put_draft(auth_client, project["id"], scaled_24kg)
    r = auth_client.post(f"/api/projects/{project['id']}/fullscale", json={"source": "draft"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["schema"] == "fullscale-checks/1" and body["valid"] is True
    assert body["source"] == "draft" and body["version_id"] is None
    assert body["analysis_source"] == "quick" and body["analysis_id"] is None
    assert body["parts"] == "generic"
    assert body["catalogue_size"] > 0
    assert "analysis" not in body  # the big analysis stays on the server
    keys = {c["key"] for c in body["checks"]}
    assert keys >= FULLSCALE_KEYS
    assert {"hover_thrust_to_weight", "battery_current"} <= keys
    assert body["counts"]["fail"] == sum(1 for c in body["checks"] if c["level"] == "fail")
    mo = body["motor_out"]
    assert len(mo["cases"]) == 8 and mo["level"] in ("ok", "warn", "fail")
    assert mo["worst_case"]["failed_motor"] and mo["message"]
    assert "recommendation" in mo
    # The tube checks used the database catalogue (the seed's tubes, loaded above).
    assert body["structure"]["spar"]["catalogue"]
    assert body["structure"]["booms"]["catalogue"]
    assert body["landing_gear"]["load_factor"] > 1
    assert body["battery"]["hover_current_a"] > 0
    assert isinstance(body["battery"]["li_ion_alternatives"], list)
    assert body["layup"]["structural_mass"]["layup_total_g"] > 0
    assert "IAA" in body["range_endurance"]["a3_note"]
    assert [t["kg"] for t in body["mtow"]["thresholds"]] == [23.0, 24.0, 25.0]
    # Same request again: the cached answer.
    again = auth_client.post(f"/api/projects/{project['id']}/fullscale", json={})
    assert again.status_code == 200
    assert again.json()["motor_out"] == mo


def test_fullscale_reuses_a_matching_analysis_and_reads_versions(
    app: FastAPI, auth_client: TestClient, scaled_24kg: dict[str, Any], settings: Settings
) -> None:
    from app.engine.analysis import run_analysis
    from app.jobs import polar_cache_dir
    from app.routers.settings import effective_settings

    project = _project(auth_client, "Reuse")
    _put_draft(auth_client, project["id"], scaled_24kg)
    version = auth_client.post(
        f"/api/projects/{project['id']}/versions", json={"name": "Final"}
    ).json()
    draft = auth_client.get(f"/api/projects/{project['id']}/draft").json()
    with app.state.session_factory() as db:
        owner_id = db.execute(text("SELECT id FROM users LIMIT 1")).scalar_one()
        settings_doc, _ = effective_settings(db, owner_id)
        result = run_analysis(
            draft["parameters"],
            draft["mission"],
            settings_doc,
            mode="fast",
            cache_dir=str(polar_cache_dir(settings)),
        )
        row = Analysis(
            owner_id=owner_id,
            project_id=project["id"],
            version_id=version["id"],
            kind="full",
            inputs={
                "parameters": draft["parameters"],
                "mission": draft["mission"],
                "settings": settings_doc,
            },
            inputs_hash="x" * 64,
            status="done",
            progress=1.0,
            stage="Done",
            result=result,
        )
        db.add(row)
        db.commit()
        analysis_id = row.id
    r = auth_client.post(
        f"/api/projects/{project['id']}/fullscale", json={"source": {"version_id": version["id"]}}
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["source"] == "version" and body["version_number"] == version["number"]
    assert body["analysis_source"] == "reused" and body["analysis_id"] == analysis_id
    # The draft has no matching analysis of its own (analyses belong to their source).
    d = auth_client.post(f"/api/projects/{project['id']}/fullscale", json={"source": "draft"})
    assert d.json()["analysis_source"] == "quick"
    # A version of another project is refused.
    other = _project(auth_client, "Other")
    bad = auth_client.post(
        f"/api/projects/{other['id']}/fullscale", json={"source": {"version_id": version["id"]}}
    )
    assert bad.status_code == 422


def test_fullscale_of_the_default_prototype_runs(auth_client: TestClient) -> None:
    project = _project(auth_client, "Prototype")
    body = auth_client.post(f"/api/projects/{project['id']}/fullscale", json={}).json()
    assert body["valid"] is True
    assert body["catalogue_size"] == 0  # tests start with an empty catalogue
    assert any("final" in n for n in body["notes"])  # prototype scale is pointed out
    assert {c["key"] for c in body["checks"]} >= FULLSCALE_KEYS

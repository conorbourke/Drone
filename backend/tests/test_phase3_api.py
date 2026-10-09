"""Phase 3: settings schema 2, "Try as new version" (from-patch), the validation endpoints."""

from __future__ import annotations

import copy
import json
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.defaults import DEFAULT_SETTINGS
from app.jobs import validation_report_path
from app.main import create_app
from app.models import AppSettings, User
from app.routers import validation as validation_router
from app.schemas.migrate import upgrade_settings
from tests.conftest import PASSWORD, make_settings, run_migrations

# ---------------------------------------------------------------------------
# Settings schema 2
# ---------------------------------------------------------------------------

PHASE3_PATHS = {
    "checks.manoeuvre_load_factor": 3.0,
    "checks.structural_safety_factor": 1.5,
    "checks.transition_thrust_margin_min": 1.3,
    "analysis.ncrit": 9.0,
}


def test_settings_upgrade_1_to_2() -> None:
    upgraded = upgrade_settings({"schema_version": 1, "checks": {"static_margin_min": 0.06}})
    assert upgraded["schema_version"] == 3
    assert upgraded["checks"]["static_margin_min"] == 0.06
    for path, value in PHASE3_PATHS.items():
        block, key = path.split(".")
        assert upgraded[block][key] == value
    # A full v1 document keeps every value it had.
    v1 = copy.deepcopy(DEFAULT_SETTINGS)
    v1["schema_version"] = 1
    del v1["analysis"]
    del v1["budget"]
    for key in (
        "manoeuvre_load_factor",
        "structural_safety_factor",
        "transition_thrust_margin_min",
    ):
        del v1["checks"][key]
    assert upgrade_settings(v1) == DEFAULT_SETTINGS


def test_stored_v1_settings_read_back_as_v2(app: FastAPI, auth_client: TestClient) -> None:
    with app.state.session_factory() as db:
        owner = db.scalar(select(User).where(User.is_owner.is_(True)))
        assert owner is not None
        db.add(
            AppSettings(
                owner_id=owner.id,
                data={"schema_version": 1, "checks": {"static_margin_min": 0.06}},
            )
        )
        db.commit()
    body = auth_client.get("/api/settings").json()
    assert body["warnings"] == []
    assert body["settings"]["schema_version"] == 3
    assert body["settings"]["checks"]["static_margin_min"] == 0.06
    assert body["meta"]["checks.static_margin_min"]["is_default"] is False
    for path, value in PHASE3_PATHS.items():
        block, key = path.split(".")
        assert body["settings"][block][key] == value
        meta = body["meta"][path]
        assert meta["is_default"] is True
        assert meta["label"] and meta["description"] and meta["source"]


def test_put_settings_v2_invariants_and_v1_documents(app: FastAPI, auth_client: TestClient) -> None:
    def put(mutate: Callable[[dict], None]) -> Any:
        doc = copy.deepcopy(DEFAULT_SETTINGS)
        mutate(doc)
        return auth_client.put("/api/settings", json=doc)

    for path, bad, phrase in (
        ("checks.manoeuvre_load_factor", 0.5, "manoeuvre load factor"),
        ("checks.structural_safety_factor", 0.9, "safety factor"),
        ("checks.transition_thrust_margin_min", 0.8, "transition thrust margin"),
        ("analysis.ncrit", 20, "Ncrit"),
    ):
        block, key = path.split(".")
        response = put(lambda d, b=block, k=key, v=bad: d[b].__setitem__(k, v))
        assert response.status_code == 422, path
        assert phrase in response.text, (path, response.text)

    response = put(lambda d: d["checks"].__setitem__("manoeuvre_load_factor", 3.8))
    assert response.status_code == 200
    assert response.json()["meta"]["checks.manoeuvre_load_factor"]["is_default"] is False
    with app.state.session_factory() as db:
        row = db.scalar(select(AppSettings))
        assert row is not None
        assert row.data == {"schema_version": 3, "checks": {"manoeuvre_load_factor": 3.8}}

    # A browser tab opened before the update sends a v1 document: upgraded, not refused.
    v1 = copy.deepcopy(DEFAULT_SETTINGS)
    v1["schema_version"] = 1
    del v1["analysis"]
    response = auth_client.put("/api/settings", json=v1)
    assert response.status_code == 200, response.text
    assert response.json()["settings"]["analysis"]["ncrit"] == 9.0


# ---------------------------------------------------------------------------
# Try as new version
# ---------------------------------------------------------------------------


def test_from_patch_on_the_draft(auth_client: TestClient, project: dict) -> None:
    pid = project["id"]
    url = f"/api/projects/{pid}/versions/from-patch"
    response = auth_client.post(
        url,
        json={
            "name": "Longer wing",
            "notes": "From a recommendation",
            "patch": {"wing.span_mm": 1900, "mission.cruise_speed_mps": 17},
        },
    )
    assert response.status_code == 201, response.text
    version = response.json()
    assert version["parameters"]["wing"]["span_mm"] == 1900
    assert version["mission"]["cruise_speed_mps"] == 17
    assert version["notes"] == "From a recommendation"
    assert version["parent_version_id"] is None  # the draft was not based on a version
    draft = auth_client.get(f"/api/projects/{pid}/draft").json()
    assert draft["parameters"]["wing"]["span_mm"] == 1800  # the draft is untouched
    assert draft["based_on_version_id"] is None

    # Once the draft is based on a version, that version is the parent.
    v1 = auth_client.post(f"/api/projects/{pid}/versions", json={"name": "v1"}).json()
    response = auth_client.post(url, json={"name": "Wider", "patch": {"fuselage.width_mm": 120}})
    assert response.status_code == 201
    assert response.json()["parent_version_id"] == v1["id"]


def test_from_patch_on_a_version(auth_client: TestClient, project: dict) -> None:
    pid = project["id"]
    v1 = auth_client.post(f"/api/projects/{pid}/versions", json={"name": "v1"}).json()
    response = auth_client.post(
        f"/api/projects/{pid}/versions/from-patch",
        json={
            "name": "v1 bigger battery",
            "base": {"version_id": v1["id"]},
            "patch": {"battery.capacity_mah": 6000, "wing.airfoil": "e387"},
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["parent_version_id"] == v1["id"]
    assert body["number"] == 2
    assert body["parameters"]["battery"]["capacity_mah"] == 6000
    assert body["parameters"]["wing"]["airfoil"] == "e387"


@pytest.mark.parametrize(
    ("patch", "status", "phrase"),
    [
        ({"wing.nope_mm": 1}, 422, "Unknown parameter 'wing.nope_mm'"),
        ({"wing": 1}, 422, "Unknown parameter 'wing'"),
        ({"schema_version": 1}, 422, "Unknown parameter"),
        ({"wing.tip_chord_mm": 400}, 422, "not valid"),
        ({"layout": "helicopter"}, 422, "not valid"),
        ({}, 422, "empty"),
        ({"mission.payload_max_g": 10}, 422, "mission is not valid"),
    ],
)
def test_from_patch_rejects_bad_patches(
    auth_client: TestClient, project: dict, patch: dict, status: int, phrase: str
) -> None:
    response = auth_client.post(
        f"/api/projects/{project['id']}/versions/from-patch", json={"name": "x", "patch": patch}
    )
    assert response.status_code == status, response.text
    assert phrase in json.dumps(response.json()), response.text


def test_from_patch_name_conflict_and_foreign_base(auth_client: TestClient, project: dict) -> None:
    pid = project["id"]
    auth_client.post(f"/api/projects/{pid}/versions", json={"name": "v1"})
    response = auth_client.post(
        f"/api/projects/{pid}/versions/from-patch",
        json={"name": "v1", "patch": {"wing.span_mm": 1900}},
    )
    assert response.status_code == 409
    other = auth_client.post("/api/projects", json={"name": "Other"}).json()
    ov = auth_client.post(f"/api/projects/{other['id']}/versions", json={"name": "o1"}).json()
    response = auth_client.post(
        f"/api/projects/{pid}/versions/from-patch",
        json={"name": "x", "base": {"version_id": ov["id"]}, "patch": {"wing.span_mm": 1900}},
    )
    assert response.status_code == 422
    assert "does not belong" in response.json()["detail"]


# ---------------------------------------------------------------------------
# System info and validation
# ---------------------------------------------------------------------------


def test_system_info_current_phase(auth_client: TestClient) -> None:
    body = auth_client.get("/api/system/info").json()
    assert body["phase"] == 5
    assert body["version"] == "0.5.0"


def _fake_report(passed: int = 3) -> dict[str, Any]:
    return {
        "schema": "validation-report/1",
        "summary": {"pass": passed, "fail": 0, "skipped": 0, "info": 0, "cases": passed},
        "cases": [],
    }


def test_validation_endpoint_and_run(
    auth_client: TestClient, settings: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(validation_router, "SNAPSHOT_PATH", tmp_path / "missing.json")
    validation_report_path(settings).unlink(missing_ok=True)
    body = auth_client.get("/api/validation").json()
    assert body == {"report": None, "source": None, "job": body["job"]}
    assert body["job"]["status"] == "idle"

    snapshot = tmp_path / "snapshot.json"
    snapshot.write_text(json.dumps(_fake_report(1)))
    monkeypatch.setattr(validation_router, "SNAPSHOT_PATH", snapshot)
    body = auth_client.get("/api/validation").json()
    assert body["source"] == "snapshot" and body["report"]["summary"]["pass"] == 1

    calls: list[dict[str, Any]] = []

    def run_validation(path: str, *, cache_dir: str, progress: Any) -> dict[str, Any]:
        calls.append({"path": path, "cache_dir": cache_dir})
        progress(0.5, "XFOIL against wind-tunnel data")
        report = _fake_report(5)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(json.dumps(report))
        return report

    monkeypatch.setattr("app.validation.run_validation", run_validation)
    response = auth_client.post("/api/validation/run")
    assert response.status_code == 202, response.text
    assert response.json()["job"]["status"] in ("queued", "running", "done")
    deadline = time.monotonic() + 30
    while True:
        body = auth_client.get("/api/validation").json()
        if body["job"]["status"] in ("done", "error"):
            break
        assert time.monotonic() < deadline
        time.sleep(0.05)
    assert body["job"]["status"] == "done"
    assert body["job"]["trigger"] == "owner"
    assert body["job"]["finished_at"].endswith("Z")
    assert body["source"] == "app" and body["report"]["summary"]["pass"] == 5
    assert calls[0]["path"] == str(validation_report_path(settings))
    assert calls[0]["cache_dir"].endswith(str(Path("cache") / "polars"))
    validation_report_path(settings).unlink()


def test_validation_runs_once_at_first_startup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []

    def run_validation(path: str, *, cache_dir: str, progress: Any) -> dict[str, Any]:
        calls.append(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(json.dumps(_fake_report()))
        return _fake_report()

    monkeypatch.setattr("app.validation.run_validation", run_validation)
    settings = make_settings(tmp_path, validation_on_startup=True)
    run_migrations(settings.resolved_database_url)
    app = create_app(settings)
    for _start in range(2):
        with TestClient(app, headers={"X-Requested-With": "fetch"}) as client:
            assert client.post("/api/auth/login", json={"password": PASSWORD}).status_code == 200
            deadline = time.monotonic() + 30
            while True:
                job = client.get("/api/validation").json()["job"]
                if _start == 1 or job["status"] == "done":
                    break
                assert time.monotonic() < deadline, job
                time.sleep(0.05)
            if _start == 0:
                assert job["trigger"] == "startup"
    assert calls == [str(validation_report_path(settings))]  # not again: the report exists


def test_real_validation_report_snapshot_is_committed() -> None:
    """docs/validation/latest.json is the committed snapshot written by a full run."""
    path = validation_router.SNAPSHOT_PATH
    assert path.is_file(), path
    report = json.loads(path.read_text())
    assert report["schema"] == "validation-report/1"
    assert report["summary"]["cases"] == len(report["cases"]) > 10
    groups = {c["group"] for c in report["cases"]}
    assert groups == {"textbook", "avl_reference", "xfoil_reference", "published_design"}

"""Phase 3 analysis API: lifecycle, worker behaviour, reuse, RESTRICT delete, scale, recovery."""

from __future__ import annotations

import copy
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import jobs
from app.defaults import DEFAULT_SETTINGS
from app.main import create_app
from app.models import Analysis, Project
from tests.conftest import PASSWORD, make_settings, run_migrations


def wait_for(
    client: TestClient, analysis_id: int, statuses: tuple[str, ...] = ("done", "error"), timeout=180
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while True:
        body = client.get(f"/api/analyses/{analysis_id}").json()
        if body["status"] in statuses:
            return body
        assert time.monotonic() < deadline, body
        time.sleep(0.1)


def quantity(value: float, unit: str = "min", label: str = "x") -> dict[str, Any]:
    return {
        "value": value,
        "low": value * 0.8,
        "high": value * 1.2,
        "unit": unit,
        "label": label,
        "explain": "",
        "source": "",
    }


def fake_result(endurance: float = 20.0, **extra: Any) -> dict[str, Any]:
    return {
        "valid": True,
        "mode": "full",
        "summary": {
            "endurance_cruise": quantity(endurance),
            "takeoff_mass": quantity(2.5, "kg"),
        },
        "checks": [{"key": "endurance", "label": "Endurance", "level": "warn", "message": "m"}],
        **extra,
    }


@pytest.fixture
def stub_engine(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Replace the slow engine calls with instant stubs; records the calls."""
    calls: dict[str, Any] = {"analysis": [], "recommend": [], "scale": []}

    def run_analysis(p, m, s, **kw):  # type: ignore[no-untyped-def]
        calls["analysis"].append({"parameters": p, "settings": s, **kw})
        kw["progress"](0.5, "Running AVL")
        return fake_result()

    def run_recommendations(p, m, s, **kw):  # type: ignore[no-untyped-def]
        calls["recommend"].append(kw)
        kw["progress"](0.5, "Trying: Increase the wingspan")
        return {"valid": True, "recommendations": [{"rank": 1, "patch": {"wing.span_mm": 1890}}]}

    def run_scale(p, m, s, target, **kw):  # type: ignore[no-untyped-def]
        calls["scale"].append({"target": target, **kw})
        kw["progress"](0.5, "Sizing the wing")
        return {"valid": True, "target_takeoff_mass_kg": target, "analysis": fake_result()}

    monkeypatch.setattr("app.engine.analysis.run_analysis", run_analysis)
    monkeypatch.setattr("app.engine.recommend.run_recommendations", run_recommendations)
    monkeypatch.setattr("app.engine.scale.run_scale", run_scale)
    return calls


def post_analysis(client: TestClient, project_id: int, source: Any = "draft") -> dict[str, Any]:
    response = client.post(f"/api/projects/{project_id}/analyses", json={"source": source})
    assert response.status_code == 202, response.text
    return response.json()


# ---------------------------------------------------------------------------
# The real engine, once
# ---------------------------------------------------------------------------


def test_full_analysis_lifecycle_with_real_engine(auth_client: TestClient, project: dict) -> None:
    """Analyse returns results, checks and ranked recommendations (acceptance criterion)."""
    created = post_analysis(auth_client, project["id"])
    assert created["status"] in ("queued", "running")
    assert created["kind"] == "full" and created["source"] == "draft"
    assert set(created) >= {"id", "status", "progress", "stage", "inputs_hash"}

    seen_stages: set[str] = set()
    deadline = time.monotonic() + 240
    while True:
        body = auth_client.get(f"/api/analyses/{created['id']}").json()
        seen_stages.add(body["stage"])
        if body["status"] in ("done", "error"):
            break
        assert time.monotonic() < deadline, body
        time.sleep(0.25)
    assert body["status"] == "done", body["error"]
    assert body["progress"] == 1.0 and body["duration_s"] > 0
    result = body["result"]
    assert result["valid"] is True
    assert result["summary"]["endurance_cruise"]["unit"] == "min"
    assert {c["level"] for c in result["checks"]} <= {"ok", "warn", "fail", "info"}
    recs = result["recommendations"]
    assert recs["valid"] is True
    ranks = [r["rank"] for r in recs["recommendations"]]
    assert ranks == sorted(ranks)
    gains = [r["endurance_gain_min"] for r in recs["recommendations"]]
    assert gains == sorted(gains, reverse=True)
    for r in recs["recommendations"]:
        assert r["patch"] and r["sentence"]
    assert len(seen_stages) >= 2  # progress was written while it ran
    assert body["headline"]["endurance_cruise"]["unit"] == "min"

    # Same inputs again: reused at once, not re-run.
    again = post_analysis(auth_client, project["id"])
    assert again["status"] == "done"
    assert again["reused_from_id"] == created["id"]
    reused = auth_client.get(f"/api/analyses/{again['id']}").json()
    assert reused["result"]["summary"] == result["summary"]

    # Try the top recommendation as a new version.
    if recs["recommendations"]:
        top = recs["recommendations"][0]
        response = auth_client.post(
            f"/api/projects/{project['id']}/versions/from-patch",
            json={"name": "Top recommendation", "base": "draft", "patch": top["patch"]},
        )
        assert response.status_code == 201, response.text
        for path, value in top["patch"].items():
            node = response.json()["parameters"]
            for key in path.split("."):
                node = node[key]
            assert node == value

    listed = auth_client.get(f"/api/projects/{project['id']}/analyses").json()
    assert [a["id"] for a in listed] == [again["id"], created["id"]]
    assert "result" not in listed[0]


# ---------------------------------------------------------------------------
# Worker behaviour with stubbed engines
# ---------------------------------------------------------------------------


def test_results_arrive_before_recommendations(
    auth_client: TestClient, project: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    started, release = threading.Event(), threading.Event()

    def run_analysis(p, m, s, **kw):  # type: ignore[no-untyped-def]
        return fake_result()

    def run_recommendations(p, m, s, **kw):  # type: ignore[no-untyped-def]
        started.set()
        while not release.wait(0.05):
            kw["progress"](0.1, "Trying: Increase the wingspan")
        return {"valid": True, "recommendations": []}

    monkeypatch.setattr("app.engine.analysis.run_analysis", run_analysis)
    monkeypatch.setattr("app.engine.recommend.run_recommendations", run_recommendations)
    created = post_analysis(auth_client, project["id"])
    assert started.wait(30)
    body = auth_client.get(f"/api/analyses/{created['id']}").json()
    assert body["status"] == "running"
    assert body["result"]["summary"]["endurance_cruise"]["value"] == 20.0
    assert body["result"]["recommendations"] is None
    assert 0.55 <= body["progress"] < 1.0
    assert body["stage"] in ("Finding improvements", "Trying: Increase the wingspan")
    release.set()
    done = wait_for(auth_client, created["id"])
    assert done["status"] == "done"
    assert done["result"]["recommendations"] == {"valid": True, "recommendations": []}


def test_invalid_design_skips_recommendations(
    auth_client: TestClient, project: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "app.engine.analysis.run_analysis",
        lambda p, m, s, **kw: {"valid": False, "checks": [{"level": "fail"}], "summary": {}},
    )
    created = post_analysis(auth_client, project["id"])
    done = wait_for(auth_client, created["id"])
    assert done["status"] == "done"
    assert done["result"]["recommendations"]["recommendations"] == []
    assert "could not be analysed" in done["result"]["recommendations"]["message"]


def test_engine_crash_marks_error(
    auth_client: TestClient, project: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*_a: Any, **_k: Any) -> None:
        raise RuntimeError("bug")

    monkeypatch.setattr("app.engine.analysis.run_analysis", boom)
    created = post_analysis(auth_client, project["id"])
    done = wait_for(auth_client, created["id"])
    assert done["status"] == "error"
    assert "Something went wrong" in done["error"] and "bug" not in done["error"]


def test_analyses_run_strictly_one_at_a_time(
    auth_client: TestClient, project: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    lock = threading.Lock()
    state = {"active": 0, "max": 0}

    def run_analysis(p, m, s, **kw):  # type: ignore[no-untyped-def]
        with lock:
            state["active"] += 1
            state["max"] = max(state["max"], state["active"])
        time.sleep(0.15)
        with lock:
            state["active"] -= 1
        return {"valid": False, "checks": [], "summary": {}}

    monkeypatch.setattr("app.engine.analysis.run_analysis", run_analysis)
    ids = []
    for span in (1700, 1750, 1800):
        draft = auth_client.get(f"/api/projects/{project['id']}/draft").json()
        draft["parameters"]["wing"]["span_mm"] = span
        auth_client.put(f"/api/projects/{project['id']}/draft", json=draft)
        ids.append(post_analysis(auth_client, project["id"])["id"])
    queued = auth_client.get(f"/api/analyses/{ids[-1]}").json()
    if queued["status"] == "queued":
        assert queued["queue_position"] is not None
    for aid in ids:
        assert wait_for(auth_client, aid)["status"] == "done"
    assert state["max"] == 1


def test_pending_duplicate_is_returned_and_settings_change_the_hash(
    auth_client: TestClient, project: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    release = threading.Event()

    def run_analysis(p, m, s, **kw):  # type: ignore[no-untyped-def]
        release.wait(30)
        return {"valid": False, "checks": [], "summary": {}, "s": s}

    monkeypatch.setattr("app.engine.analysis.run_analysis", run_analysis)
    first = post_analysis(auth_client, project["id"])
    second = post_analysis(auth_client, project["id"])
    assert second["id"] == first["id"]  # still pending: the same job
    release.set()
    done = wait_for(auth_client, first["id"])
    assert done["result"]["s"]["checks"]["manoeuvre_load_factor"] == 3.0

    doc = copy.deepcopy(DEFAULT_SETTINGS)
    doc["checks"]["manoeuvre_load_factor"] = 4.0
    assert auth_client.put("/api/settings", json=doc).status_code == 200
    third = post_analysis(auth_client, project["id"])
    assert third["id"] != first["id"] and third["reused_from_id"] is None
    assert third["inputs_hash"] != first["inputs_hash"]
    done = wait_for(auth_client, third["id"])
    # The engine gets the owner's settings and their labels and sources.
    assert done["result"]["s"]["checks"]["manoeuvre_load_factor"] == 4.0


def test_engine_receives_settings_meta(
    auth_client: TestClient, project: dict, stub_engine: dict[str, Any]
) -> None:
    created = post_analysis(auth_client, project["id"])
    assert wait_for(auth_client, created["id"])["status"] == "done"
    kw = stub_engine["analysis"][0]
    assert kw["mode"] == "full"
    assert kw["cache_dir"].endswith(str(Path("cache") / "polars"))
    meta = kw["settings_meta"]
    assert meta["checks.manoeuvre_load_factor"]["label"] == "Manoeuvre load factor"
    assert "CS-23" in meta["checks.structural_safety_factor"]["source"]
    assert stub_engine["recommend"][0]["settings_meta"] == meta


def test_version_source_and_validation(
    auth_client: TestClient, project: dict, stub_engine: dict[str, Any]
) -> None:
    version = auth_client.post(
        f"/api/projects/{project['id']}/versions", json={"name": "v1"}
    ).json()
    created = post_analysis(auth_client, project["id"], {"version_id": version["id"]})
    assert created["source"] == "version"
    assert created["version_id"] == version["id"] and created["version_number"] == 1
    assert wait_for(auth_client, created["id"])["status"] == "done"

    other = auth_client.post("/api/projects", json={"name": "Other"}).json()
    response = auth_client.post(
        f"/api/projects/{other['id']}/analyses", json={"source": {"version_id": version["id"]}}
    )
    assert response.status_code == 422
    assert "does not belong" in response.json()["detail"]
    response = auth_client.post(f"/api/projects/{project['id']}/analyses", json={"kind": "x"})
    assert response.status_code == 422
    assert auth_client.post("/api/projects/999999/analyses", json={}).status_code == 404
    assert auth_client.get("/api/analyses/999999").status_code == 404


def test_version_with_analyses_restrict_delete(
    app: FastAPI, auth_client: TestClient, project: dict, stub_engine: dict[str, Any]
) -> None:
    version = auth_client.post(
        f"/api/projects/{project['id']}/versions", json={"name": "v1"}
    ).json()
    created = post_analysis(auth_client, project["id"], {"version_id": version["id"]})
    wait_for(auth_client, created["id"])

    response = auth_client.delete(f"/api/versions/{version['id']}")
    assert response.status_code == 409
    body = response.json()
    assert body["analyses"] == 1
    assert body["detail"] == (
        "Version 1 has 1 saved analysis. Delete it together with the version, or keep the version."
    )
    assert auth_client.get(f"/api/versions/{version['id']}").status_code == 200

    response = auth_client.delete(f"/api/versions/{version['id']}?with_analyses=true")
    assert response.status_code == 204
    assert auth_client.get(f"/api/versions/{version['id']}").status_code == 404
    assert auth_client.get(f"/api/analyses/{created['id']}").status_code == 404
    with app.state.session_factory() as db:
        assert db.scalars(select(Analysis)).all() == []


def test_ddl_restrict_without_the_api_check(
    app: FastAPI, auth_client: TestClient, project: dict
) -> None:
    """The database itself refuses (ON DELETE RESTRICT), not only the endpoint's count."""
    from sqlalchemy import text
    from sqlalchemy.exc import IntegrityError

    version = auth_client.post(
        f"/api/projects/{project['id']}/versions", json={"name": "v1"}
    ).json()
    with app.state.session_factory() as db:
        owner_id = db.get(Project, project["id"]).owner_id  # type: ignore[union-attr]
        db.add(
            Analysis(
                owner_id=owner_id,
                project_id=project["id"],
                version_id=version["id"],
                kind="full",
                inputs={},
                inputs_hash="x",
                status="done",
            )
        )
        db.commit()
        with pytest.raises(IntegrityError):
            db.execute(text("DELETE FROM design_versions WHERE id = :id"), {"id": version["id"]})
            db.commit()
        db.rollback()


def test_project_delete_removes_analyses_of_its_versions(
    app: FastAPI, auth_client: TestClient, project: dict, stub_engine: dict[str, Any]
) -> None:
    version = auth_client.post(
        f"/api/projects/{project['id']}/versions", json={"name": "v1"}
    ).json()
    created = post_analysis(auth_client, project["id"], {"version_id": version["id"]})
    wait_for(auth_client, created["id"])
    post_analysis(auth_client, project["id"])
    assert auth_client.delete(f"/api/projects/{project['id']}").status_code == 204
    with app.state.session_factory() as db:
        assert db.scalars(select(Analysis)).all() == []


def test_scale_job(auth_client: TestClient, project: dict, stub_engine: dict[str, Any]) -> None:
    response = auth_client.post(
        f"/api/projects/{project['id']}/scale", json={"target_takeoff_mass_kg": 24}
    )
    assert response.status_code == 202, response.text
    created = response.json()
    assert created["kind"] == "scale"
    done = wait_for(auth_client, created["id"])
    assert done["status"] == "done"
    assert done["target_takeoff_mass_kg"] == 24.0
    assert done["result"]["target_takeoff_mass_kg"] == 24.0
    assert done["headline"]["endurance_cruise"]["value"] == 20.0
    assert stub_engine["scale"][0]["target"] == 24.0
    assert stub_engine["scale"][0]["mode"] == "full"
    for bad in (0, -1, 26, "x"):
        response = auth_client.post(
            f"/api/projects/{project['id']}/scale", json={"target_takeoff_mass_kg": bad}
        )
        assert response.status_code == 422, bad
    # A different target is a different job; the same target is reused.
    again = auth_client.post(
        f"/api/projects/{project['id']}/scale", json={"target_takeoff_mass_kg": 24}
    ).json()
    assert again["reused_from_id"] == created["id"]


# ---------------------------------------------------------------------------
# Startup recovery and shutdown
# ---------------------------------------------------------------------------


@pytest.fixture
def fresh_app(tmp_path: Path) -> Iterator[Callable[..., FastAPI]]:
    def make(**overrides: Any) -> FastAPI:
        settings = make_settings(tmp_path, **overrides)
        run_migrations(settings.resolved_database_url)
        return create_app(settings)

    yield make


def _login(client: TestClient) -> None:
    assert client.post("/api/auth/login", json={"password": PASSWORD}).status_code == 200


def test_startup_recovers_running_and_requeues_queued(
    fresh_app: Callable[..., FastAPI], stub_engine: dict[str, Any]
) -> None:
    app = fresh_app()
    with TestClient(app, headers={"X-Requested-With": "fetch"}) as client:
        _login(client)
        project = client.post("/api/projects", json={"name": "P"}).json()
        a = post_analysis(client, project["id"])
        wait_for(client, a["id"])
    with app.state.session_factory() as db:
        template = db.get(Analysis, a["id"])
        assert template is not None
        rows = []
        for status in ("running", "queued"):
            row = Analysis(
                owner_id=template.owner_id,
                project_id=template.project_id,
                kind="full",
                inputs=template.inputs,
                inputs_hash=f"h-{status}",
                status=status,
                stage="x",
            )
            db.add(row)
            rows.append(row)
        db.commit()
        running_id, queued_id = rows[0].id, rows[1].id

    with TestClient(app, headers={"X-Requested-With": "fetch"}) as client:
        _login(client)
        crashed = client.get(f"/api/analyses/{running_id}").json()
        assert crashed["status"] == "error"
        assert "interrupted" in crashed["error"]
        assert wait_for(client, queued_id)["status"] == "done"


def test_shutdown_requeues_the_running_job_and_stops_the_solver(
    fresh_app: Callable[..., FastAPI], monkeypatch: pytest.MonkeyPatch
) -> None:
    started = threading.Event()
    solver_stops: list[bool] = []

    def run_analysis(p, m, s, **kw):  # type: ignore[no-untyped-def]
        started.set()
        while True:  # a long analysis that reports progress
            kw["progress"](0.3, "Running XFOIL")
            time.sleep(0.02)

    monkeypatch.setattr("app.engine.analysis.run_analysis", run_analysis)
    monkeypatch.setattr(jobs, "shutdown_solver_worker", lambda: solver_stops.append(True))
    app = fresh_app()
    with TestClient(app, headers={"X-Requested-With": "fetch"}) as client:
        _login(client)
        project = client.post("/api/projects", json={"name": "P"}).json()
        created = post_analysis(client, project["id"])
        assert started.wait(30)
    assert solver_stops == [True]
    with app.state.session_factory() as db:
        row = db.get(Analysis, created["id"])
        assert row is not None
        assert row.status == "queued"
        assert row.stage == jobs.REQUEUED_STAGE
        assert row.result is None


def test_progress_writes_are_throttled(monkeypatch: pytest.MonkeyPatch) -> None:
    writes: list[dict[str, Any]] = []
    prog = jobs._Progress(None, 1, threading.Event())  # type: ignore[arg-type]
    monkeypatch.setattr(prog, "write", lambda **v: writes.append(v))
    report = prog.window(0.0, 0.5)
    for i in range(200):
        report(i / 200, "Same stage")
    assert 1 <= len(writes) <= 3
    assert writes[0]["progress"] == 0.0
    stop = threading.Event()
    prog2 = jobs._Progress(None, 1, stop)  # type: ignore[arg-type]
    monkeypatch.setattr(prog2, "write", lambda **v: writes.append(v))
    stop.set()
    with pytest.raises(jobs.JobCancelled):
        prog2.update(0.1, "x")

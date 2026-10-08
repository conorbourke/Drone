from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import create_engine

from app import PHASE, __version__
from app.backup import BackupManager
from app.config import Settings
from app.db import make_session_factory
from app.main import create_app, resolve_static_dir
from tests.conftest import FETCH_HEADERS, PASSWORD, make_settings


def test_health_is_public(client: TestClient) -> None:
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": __version__, "db": "ok"}


def test_health_503_when_db_unreachable(app: FastAPI, client: TestClient) -> None:
    original = app.state.session_factory
    app.state.session_factory = make_session_factory(
        create_engine("sqlite:////nonexistent-dir-for-test/app.db")
    )
    try:
        response = client.get("/api/health")
    finally:
        app.state.session_factory = original
    assert response.status_code == 503
    assert response.json()["db"] == "unreachable"


def test_system_info(auth_client: TestClient, settings: Settings) -> None:
    response = auth_client.get("/api/system/info")
    assert response.status_code == 200
    body = response.json()
    assert body["version"] == __version__
    assert body["phase"] == PHASE == 1
    assert body["environment"] == "test"
    assert body["data_dir"] == str(settings.app_data_dir)
    assert set(body["backup"]) == {"enabled", "last_run_at", "next_run_at", "count"}
    assert body["backup"]["enabled"] is False
    text = response.text
    assert PASSWORD not in text and "secret" not in text.lower()


def test_security_headers_present(client: TestClient) -> None:
    api = client.get("/api/health")
    assert api.headers["x-content-type-options"] == "nosniff"
    assert api.headers["referrer-policy"] == "same-origin"
    assert api.headers["x-frame-options"] == "DENY"
    assert api.headers["cache-control"] == "no-store"
    assert "strict-transport-security" not in api.headers
    assert "access-control-allow-origin" not in api.headers
    root = client.get("/")
    assert root.headers["x-frame-options"] == "DENY"
    assert "cache-control" not in root.headers  # no-store is for /api only


def test_backups_create_list_download(auth_client: TestClient, settings: Settings) -> None:
    created = auth_client.post("/api/system/backups")
    assert created.status_code == 201
    entry = created.json()
    assert set(entry) == {"name", "size_bytes", "created_at"}
    assert entry["name"].startswith("app-") and entry["name"].endswith(".db")
    assert entry["size_bytes"] > 0
    assert entry["created_at"].endswith("Z")
    assert (settings.backups_dir / entry["name"]).is_file()

    listing = auth_client.get("/api/system/backups")
    assert listing.status_code == 200
    assert entry["name"] in [e["name"] for e in listing.json()]

    download = auth_client.get(f"/api/system/backups/{entry['name']}")
    assert download.status_code == 200
    assert download.headers["content-type"] == "application/octet-stream"
    assert download.headers["content-disposition"] == f'attachment; filename="{entry["name"]}"'
    assert download.headers["cache-control"] == "no-store"
    assert download.content[:16] == b"SQLite format 3\x00"
    assert len(download.content) == entry["size_bytes"]

    info = auth_client.get("/api/system/info").json()["backup"]
    assert info["count"] >= 1 and info["last_run_at"] is not None


def test_backup_download_rejects_bad_names(auth_client: TestClient, settings: Settings) -> None:
    settings.backups_dir.mkdir(parents=True, exist_ok=True)
    decoy = settings.app_data_dir / "app-20200101-000000.db"
    decoy.write_bytes(b"not a backup")
    try:
        for name in (
            "app.db",
            "..%2Fapp.db",
            "app-20200101-000000.db%00",
            "app-2020010-000000.db",
            "x-20200101-000000.db",
            "app-20200101-000000.db.bak",
            "app-20991231-235959.db",  # well-formed but absent
        ):
            response = auth_client.get(f"/api/system/backups/{name}")
            assert response.status_code == 404, name
        assert auth_client.get("/api/system/backups/../app-20200101-000000.db").status_code in (
            404,
            401,
        )
    finally:
        decoy.unlink()


def test_backup_download_requires_session(client: TestClient) -> None:
    assert client.get("/api/system/backups/app-20200101-000000.db").status_code == 401
    assert client.get("/api/system/backups").status_code == 401
    assert client.post("/api/system/backups").status_code == 401


def test_backup_manager_retention_and_catch_up(tmp_path: Path, settings: Settings) -> None:
    db_file = settings.app_data_dir / "app.db"
    manager = BackupManager(db_file, tmp_path / "backups", keep=2, hour_utc=3, enabled=True)
    assert manager.list() == []
    first = manager.catch_up()  # nothing yet -> runs
    assert first is not None and len(manager.list()) == 1
    assert manager.catch_up() is None  # fresh -> skipped
    assert manager.last_run_at == first.created_at

    manager.create()
    manager.create()
    names = [e.name for e in manager.list()]
    assert len(names) == 2  # retention keeps the newest two
    assert names == sorted(names, reverse=True)
    assert first.name not in names

    # An old backup triggers the catch-up again.
    old_name = f"app-{(datetime.now(UTC) - timedelta(days=2)):%Y%m%d-%H%M%S}.db"
    for stale in manager.list():
        (tmp_path / "backups" / stale.name).unlink()
    (tmp_path / "backups" / old_name).write_bytes(b"x")
    assert manager.newest() is not None and manager.newest().name == old_name
    assert manager.catch_up() is not None

    (tmp_path / "backups" / "notes.txt").write_text("ignored")
    assert all(e.name.endswith(".db") for e in manager.list())
    assert manager.resolve("../app.db") is None
    assert manager.resolve(manager.list()[0].name) is not None

    nxt = manager.next_run_at(datetime(2026, 1, 1, 2, 30, tzinfo=UTC))
    assert nxt == datetime(2026, 1, 1, 3, 0, tzinfo=UTC)
    nxt = manager.next_run_at(datetime(2026, 1, 1, 3, 0, tzinfo=UTC))
    assert nxt == datetime(2026, 1, 2, 3, 0, tzinfo=UTC)
    disabled = BackupManager(db_file, tmp_path / "b2", keep=1, hour_utc=3, enabled=False)
    assert disabled.next_run_at() is None


def test_startup_catch_up_runs_when_enabled(tmp_path: Path, data_dir: Path) -> None:
    backups = tmp_path / "catchup"
    app = create_app(make_settings(data_dir, backup_enabled=True, app_data_dir=data_dir))
    app.state.backups.backups_dir = backups
    with TestClient(app, headers=FETCH_HEADERS):
        time.sleep(0.05)
        assert len(list(backups.glob("app-*.db"))) == 1
        assert app.state.backups.last_run_at is not None


def test_frontend_not_built_fallback(client: TestClient) -> None:
    for path in ("/", "/login", "/projects/1", "/settings", "/deep/nested/path"):
        response = client.get(path)
        assert response.status_code == 200, path
        assert response.headers["content-type"].startswith("text/plain")
        assert "Frontend not built" in response.text
    # API still works and unknown API paths never fall through to the SPA.
    assert client.get("/api/health").status_code == 200
    assert client.get("/api/does-not-exist").status_code == 401


def test_static_spa_serving(tmp_path: Path, data_dir: Path) -> None:
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><title>SPA</title>")
    (dist / "assets" / "app-abc123.js").write_text("console.log('hi')")
    (dist / "favicon.svg").write_text("<svg/>")
    (dist / "secret.txt").write_text("not served")
    app = create_app(make_settings(data_dir, app_static_dir=dist))
    with TestClient(app, headers=FETCH_HEADERS) as c:
        for path in ("/", "/login", "/projects/7?tab=design", "/anything/else"):
            r = c.get(path)
            assert r.status_code == 200 and "<title>SPA</title>" in r.text, path
            assert r.headers["content-type"].startswith("text/html")
        js = c.get("/assets/app-abc123.js")
        assert js.status_code == 200 and "console.log" in js.text
        assert c.get("/assets/missing.js").status_code == 404
        assert c.get("/favicon.svg").status_code == 200
        assert c.get("/favicon.svg").headers["content-type"].startswith("image/svg+xml")
        assert c.get("/robots.txt").status_code == 404  # allowlisted but absent
        # No filesystem path is ever derived from the URL.
        for path in ("/secret.txt", "/index.html", "/..%2Fsecret.txt", "/assets/../secret.txt"):
            r = c.get(path)
            assert r.status_code in (200, 404), path
            assert "not served" not in r.text, path
        assert c.get("/api/nope").status_code == 401
        c.post("/api/auth/login", json={"password": PASSWORD})
        assert c.get("/api/nope").status_code == 404
        assert c.get("/api/nope").json()["detail"].startswith("No API route")
        assert c.post("/login").status_code == 405


def test_static_dir_resolution_order(
    tmp_path: Path, data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app import main

    built = tmp_path / "built"
    built.mkdir()
    (built / "index.html").write_text("<html></html>")
    empty = tmp_path / "empty"
    empty.mkdir()

    # An explicit directory wins, and one without index.html counts as "not built".
    assert resolve_static_dir(make_settings(data_dir, app_static_dir=built)) == built.resolve()
    assert resolve_static_dir(make_settings(data_dir, app_static_dir=empty)) is None
    # Without APP_STATIC_DIR the defaults are searched in order.
    monkeypatch.setattr(main, "DEFAULT_STATIC_CANDIDATES", (empty, built))
    assert resolve_static_dir(make_settings(data_dir, app_static_dir=None)) == built.resolve()
    monkeypatch.setattr(main, "DEFAULT_STATIC_CANDIDATES", (empty, tmp_path / "missing"))
    assert resolve_static_dir(make_settings(data_dir, app_static_dir=None)) is None


def test_secret_values_never_leak(data_dir: Path) -> None:
    s = make_settings(data_dir, anthropic_api_key=SecretStr("sk-ant-secret"))
    dumped = str(s.model_dump()) + repr(s) + str(s)
    assert "sk-ant-secret" not in dumped
    assert PASSWORD not in dumped
    assert "test-secret-key" not in dumped

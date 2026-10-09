"""Test fixtures: one temporary data directory and SQLite file per test session.

Tables are created by running the Alembic migration (not ``create_all``) so the migration is
exercised. Each test gets a fresh ``TestClient`` (which runs the app lifespan) and the data
tables are emptied afterwards.
"""

from __future__ import annotations

import logging
import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import text

from app.config import Settings
from app.main import create_app

BACKEND_DIR = Path(__file__).resolve().parent.parent
PASSWORD = "test-password"
SECRET = "test-secret-key-that-is-long-enough-0123456789"
FETCH_HEADERS = {"X-Requested-With": "fetch"}


def make_settings(data_dir: Path, **overrides: object) -> Settings:
    values: dict[str, object] = {
        "app_env": "test",
        "app_password": SecretStr(PASSWORD),
        "app_secret_key": SecretStr(SECRET),
        "app_data_dir": data_dir,
        "backup_enabled": False,
        "validation_on_startup": False,
        "app_static_dir": data_dir / "no-static-here",
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


def run_migrations(database_url: str) -> None:
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    cfg.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(cfg, "head")
    logging.getLogger().setLevel(logging.WARNING)


@pytest.fixture(scope="session")
def data_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tmp_path_factory.mktemp("vtol-data")


@pytest.fixture(scope="session")
def settings(data_dir: Path) -> Settings:
    return make_settings(data_dir)


@pytest.fixture(scope="session")
def app(settings: Settings) -> FastAPI:
    run_migrations(settings.resolved_database_url)
    application = create_app(settings)
    # Migration 0004 seeds the real parts catalogue; tests start from an empty catalogue and
    # load it explicitly where they need it (tests/test_phase4_*.py).
    with application.state.session_factory() as db:
        db.execute(text("DELETE FROM part_listings"))
        db.execute(text("DELETE FROM parts"))
        db.commit()
    return application


@pytest.fixture(autouse=True)
def _clean_tables(app: FastAPI, settings: Settings) -> Iterator[None]:
    yield
    shutil.rmtree(settings.files_dir, ignore_errors=True)
    with app.state.session_factory() as db:
        for table in (
            "exports",
            "part_selections",
            "assistant_messages",
            "assistant_threads",
            "analyses",
            "image_readings",
            "images",
            "design_versions",
            "projects",
            "part_listings",
            "parts",
            "app_settings",
        ):
            db.execute(text(f"DELETE FROM {table}"))
        db.commit()


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app, headers=FETCH_HEADERS) as c:
        app.state.auth.rate_limiter.reset()
        yield c


@pytest.fixture
def auth_client(client: TestClient) -> TestClient:
    response = client.post("/api/auth/login", json={"password": PASSWORD})
    assert response.status_code == 200, response.text
    return client


@pytest.fixture
def project(auth_client: TestClient) -> dict:
    response = auth_client.post("/api/projects", json={"name": "Test project"})
    assert response.status_code == 201, response.text
    return response.json()

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import inspect, select, text

from app.db import TZDateTime, utcnow
from app.models import Part, Project


def test_migration_created_every_table_with_fk_actions(app: FastAPI) -> None:
    inspector = inspect(app.state.engine)
    tables = set(inspector.get_table_names())
    assert {
        "users",
        "projects",
        "design_versions",
        "parts",
        "part_listings",
        "app_settings",
        "alembic_version",
    } <= tables
    for reserved in ("images", "analyses", "flight_logs", "calibrations", "export_files"):
        assert reserved not in tables

    def fk_actions(table: str) -> dict[str, str | None]:
        return {
            fk["constrained_columns"][0]: fk["options"].get("ondelete")
            for fk in inspector.get_foreign_keys(table)
        }

    assert fk_actions("projects")["owner_id"] == "CASCADE"
    assert fk_actions("projects")["draft_based_on_version_id"] == "SET NULL"
    assert fk_actions("design_versions")["project_id"] == "CASCADE"
    assert fk_actions("design_versions")["parent_version_id"] == "SET NULL"
    assert fk_actions("part_listings")["part_id"] == "CASCADE"
    assert fk_actions("app_settings")["owner_id"] == "CASCADE"
    for table in ("projects", "design_versions", "parts"):
        assert {"id", "created_at", "updated_at"} <= {
            c["name"] for c in inspector.get_columns(table)
        }


def test_sqlite_pragmas(app: FastAPI) -> None:
    with app.state.engine.connect() as conn:
        assert conn.execute(text("PRAGMA foreign_keys")).scalar() == 1
        assert conn.execute(text("PRAGMA journal_mode")).scalar() == "wal"


def test_tzdatetime_round_trip_has_tzinfo(app: FastAPI, auth_client: TestClient) -> None:
    created = auth_client.post("/api/projects", json={"name": "Times"})
    assert created.status_code == 201
    body = created.json()
    for key in ("created_at", "updated_at"):
        assert body[key].endswith("Z"), body[key]
        datetime.fromisoformat(body[key])  # parses
    with app.state.session_factory() as db:
        project = db.get(Project, body["id"])
        assert project is not None
        assert project.created_at.tzinfo is UTC or project.created_at.utcoffset() == timedelta(0)
        assert project.draft_updated_at.tzinfo is not None
        assert abs(project.created_at - utcnow()) < timedelta(minutes=1)


def test_tzdatetime_converts_offsets_to_utc(app: FastAPI) -> None:
    plus_two = timezone(timedelta(hours=2))
    stamp = datetime(2026, 6, 1, 12, 0, tzinfo=plus_two)
    with app.state.session_factory() as db:
        part = Part(
            category="cell",
            manufacturer="x",
            model="y",
            mass_g=1,
            spec={},
            created_at=stamp,
            updated_at=stamp,
        )
        db.add(part)
        db.commit()
        pid = part.id
    with app.state.session_factory() as db:
        loaded = db.get(Part, pid)
        assert loaded is not None
        assert loaded.created_at == stamp
        assert loaded.created_at.utcoffset() == timedelta(0)
        assert loaded.created_at.hour == 10
        raw = db.execute(select(Part.created_at).where(Part.id == pid)).scalar_one()
        assert raw.tzinfo is not None


def test_tzdatetime_type_decorator_directly() -> None:
    td = TZDateTime()
    naive = datetime(2026, 1, 1, 9, 30)
    assert td.process_bind_param(naive, None) == naive
    aware = datetime(2026, 1, 1, 9, 30, tzinfo=timezone(timedelta(hours=-5)))
    assert td.process_bind_param(aware, None) == datetime(2026, 1, 1, 14, 30)
    loaded = td.process_result_value(naive, None)
    assert loaded is not None and loaded.tzinfo is UTC
    assert td.process_bind_param(None, None) is None
    assert td.process_result_value(None, None) is None

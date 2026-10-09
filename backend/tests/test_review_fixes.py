"""Regression tests for the defects found in the Phase 1 code review."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.config import ConfigurationError, get_settings
from app.defaults import DEFAULT_SETTINGS
from app.models import AppSettings, Base, DesignVersion, User
from app.security import AuthState, LoginRateLimiter
from tests.conftest import PASSWORD, make_settings

CELL_SPEC = {
    "chemistry": "li-ion",
    "capacity_mah": 5000,
    "nominal_voltage_v": 3.6,
    "max_continuous_discharge_a": 15,
    "diameter_mm": 21,
    "length_mm": 70,
    "format": "21700",
}


def _cell_part(**overrides: object) -> dict:
    body: dict = {
        "category": "cell",
        "manufacturer": "Example Cells",
        "model": "EC-5000",
        "mass_g": 70,
        "spec": CELL_SPEC,
    }
    body.update(overrides)
    return body


# SEC-1 -------------------------------------------------------------------------------------


def test_session_fingerprint_is_stable_across_restarts(data_dir: Path) -> None:
    first = AuthState.from_settings(make_settings(data_dir))
    second = AuthState.from_settings(make_settings(data_dir))
    assert first.fingerprint == second.fingerprint
    assert first.password_hash != second.password_hash  # bcrypt re-salts; that is fine
    other_secret = AuthState.from_settings(
        make_settings(data_dir, app_secret_key=SecretStr("x" * 40))
    )
    assert other_secret.fingerprint != first.fingerprint
    other_password = AuthState.from_settings(
        make_settings(data_dir, app_password=SecretStr("different"))
    )
    assert other_password.fingerprint != first.fingerprint


def test_session_survives_an_app_restart(data_dir: Path) -> None:
    from app.main import create_app
    from tests.conftest import FETCH_HEADERS, run_migrations

    settings = make_settings(data_dir / "restart")
    run_migrations(settings.resolved_database_url)
    with TestClient(create_app(settings), headers=FETCH_HEADERS) as first:
        assert first.post("/api/auth/login", json={"password": PASSWORD}).status_code == 200
        cookie = first.cookies["vtol_session"]
    # A new process with the same secret and password must accept the old cookie.
    with TestClient(
        create_app(make_settings(data_dir / "restart")), headers=FETCH_HEADERS
    ) as second:
        second.cookies.set("vtol_session", cookie)
        assert second.get("/api/auth/me").status_code == 200


# SEC-2 -------------------------------------------------------------------------------------


def test_configuration_error_never_prints_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("APP_PASSWORD", "hunter2-SECRET-password")
    monkeypatch.delenv("APP_SECRET_KEY", raising=False)
    monkeypatch.delenv("APP_PASSWORD_HASH", raising=False)
    with pytest.raises(ConfigurationError) as info:
        get_settings()
    text = str(info.value)
    assert "APP_SECRET_KEY is not set" in text
    assert "hunter2" not in text

    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("APP_PASSWORD", "ZZsecretZZ" * 10)
    with pytest.raises(ConfigurationError) as info:
        get_settings()
    text = str(info.value)
    assert "longer than 72 bytes" in text
    assert "ZZsecret" not in text


# SEC-3 -------------------------------------------------------------------------------------


def test_oversized_api_body_is_refused_before_reading(client: TestClient) -> None:
    big = b'{"password": "' + b"x" * 1_000_100 + b'"}'
    response = client.post(
        "/api/auth/login", content=big, headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 413
    assert "too large" in response.json()["detail"]
    # A normal login still works afterwards.
    assert client.post("/api/auth/login", json={"password": PASSWORD}).status_code == 200


def test_chunked_api_body_requires_content_length(client: TestClient) -> None:
    def chunks():
        yield b'{"password": "nope"}'

    response = client.post(
        "/api/auth/login", content=chunks(), headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 411


# SEC-4 -------------------------------------------------------------------------------------


def test_rate_limiter_frees_stale_buckets() -> None:
    limiter = LoginRateLimiter(window_seconds=0.01)
    for i in range(200):
        limiter.record_failure(f"10.1.{i // 250}.{i % 250}")
    assert len(limiter._by_ip) == 200
    time.sleep(0.02)
    limiter.record_failure("192.0.2.1")
    assert set(limiter._by_ip) == {"192.0.2.1"}


# DATA-1 ------------------------------------------------------------------------------------


def test_version_names_are_unique_in_the_database(
    app: FastAPI, auth_client: TestClient, project: dict
) -> None:
    owner_id = auth_client.get("/api/auth/me").json()["id"]
    with app.state.session_factory() as db:
        for number in (1, 2):
            db.add(
                DesignVersion(
                    project_id=project["id"],
                    owner_id=owner_id,
                    name="same name",
                    notes="",
                    number=number,
                    parameters=project["draft"]["parameters"],
                    mission=project["draft"]["mission"],
                )
            )
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()


# DATA-8 ------------------------------------------------------------------------------------


def test_copy_name_fits_the_name_limit(auth_client: TestClient, project: dict) -> None:
    long_name = "n" * 200
    created = auth_client.post(f"/api/projects/{project['id']}/versions", json={"name": long_name})
    assert created.status_code == 201, created.text
    copy1 = auth_client.post(f"/api/versions/{created.json()['id']}/duplicate")
    assert copy1.status_code == 201, copy1.text
    assert len(copy1.json()["name"]) <= 200
    assert copy1.json()["name"].endswith(" (copy)")
    copy2 = auth_client.post(f"/api/versions/{created.json()['id']}/duplicate")
    assert copy2.status_code == 201
    assert len(copy2.json()["name"]) <= 200
    assert copy2.json()["name"].endswith(" (copy 2)")


# DATA-2 / DATA-3 ---------------------------------------------------------------------------


def test_patch_part_with_explicit_null_is_a_422(auth_client: TestClient) -> None:
    created = auth_client.post("/api/parts", json=_cell_part())
    assert created.status_code == 201, created.text
    part_id = created.json()["id"]
    for field in ("notes", "mass_g", "manufacturer", "spec"):
        response = auth_client.patch(f"/api/parts/{part_id}", json={field: None})
        assert response.status_code == 422, (field, response.text)
    nullable = auth_client.patch(f"/api/parts/{part_id}", json={"price_eur_estimate": None})
    assert nullable.status_code == 200


def test_part_identity_is_stripped_and_never_blank(auth_client: TestClient) -> None:
    assert auth_client.post("/api/parts", json=_cell_part(manufacturer="")).status_code == 422
    assert auth_client.post("/api/parts", json=_cell_part(model="   ")).status_code == 422
    created = auth_client.post("/api/parts", json=_cell_part(manufacturer="  Acme  "))
    assert created.status_code == 201
    assert created.json()["manufacturer"] == "Acme"
    blank_patch = auth_client.patch(f"/api/parts/{created.json()['id']}", json={"model": " "})
    assert blank_patch.status_code == 422
    listing = auth_client.post(
        f"/api/parts/{created.json()['id']}/listings",
        json={"supplier_name": "  ", "country": "IE", "url": "https://example.ie/x"},
    )
    assert listing.status_code == 422


# DATA-4 ------------------------------------------------------------------------------------


def test_spec_range_validators(auth_client: TestClient) -> None:
    esc = {
        "continuous_current_a": 40,
        "burst_current_a": 60,
        "lipo_cells_min": 3,
        "lipo_cells_max": 6,
        "firmware": "AM32",
        "telemetry": True,
    }
    ok = auth_client.post(
        "/api/parts",
        json={"category": "esc", "manufacturer": "A", "model": "E1", "mass_g": 10, "spec": esc},
    )
    assert ok.status_code == 201, ok.text
    for bad in (
        {**esc, "lipo_cells_min": 6, "lipo_cells_max": 3},
        {**esc, "burst_current_a": 10},
    ):
        response = auth_client.post(
            "/api/parts",
            json={"category": "esc", "manufacturer": "A", "model": "E2", "mass_g": 10, "spec": bad},
        )
        assert response.status_code == 422, response.text
    servo = {
        "torque_kg_cm": 20,
        "speed_s_per_60deg": 0.12,
        "voltage_min_v": 9,
        "voltage_max_v": 6,
        "gear_material": "metal",
        "width_mm": 20,
        "length_mm": 40,
        "height_mm": 38,
        "digital": True,
    }
    response = auth_client.post(
        "/api/parts",
        json={"category": "servo", "manufacturer": "A", "model": "S1", "mass_g": 60, "spec": servo},
    )
    assert response.status_code == 422


# DATA-5 ------------------------------------------------------------------------------------


def test_stale_settings_overrides_are_reset_not_fatal(
    app: FastAPI, auth_client: TestClient
) -> None:
    owner_id = auth_client.get("/api/auth/me").json()["id"]
    with app.state.session_factory() as db:
        db.add(
            AppSettings(
                owner_id=owner_id,
                data={
                    "schema_version": 1,
                    "limits": {"warn_mtow_kg": 24.5},  # breaks warn <= design
                    "printer": {"name": "Kept printer"},
                    "legacy_key": 5,  # retired setting
                },
            )
        )
        db.commit()
    response = auth_client.get("/api/settings")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["settings"]["limits"] == DEFAULT_SETTINGS["limits"]
    assert body["settings"]["printer"]["name"] == "Kept printer"
    assert body["warnings"] and "limits" in body["warnings"][0]


# DATA-6 ------------------------------------------------------------------------------------


def test_non_finite_numbers_are_rejected(auth_client: TestClient, project: dict) -> None:
    draft = project["draft"]
    doc = {"parameters": draft["parameters"], "mission": draft["mission"]}
    doc["parameters"]["wing"]["z_mm"] = float("nan")
    response = auth_client.put(
        f"/api/projects/{project['id']}/draft",
        content=json.dumps(doc),  # Python emits the non-standard NaN literal
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 422
    doc["parameters"]["wing"]["z_mm"] = 0.0
    doc["mission"]["cruise_speed_mps"] = float("inf")
    response = auth_client.put(
        f"/api/projects/{project['id']}/draft",
        content=json.dumps(doc),
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 422


# DATA-7 ------------------------------------------------------------------------------------


def test_alembic_autogenerate_is_clean(app: FastAPI) -> None:
    with app.state.engine.connect() as connection:
        context = MigrationContext.configure(connection, opts={"compare_type": True})
        diff = compare_metadata(context, Base.metadata)
    assert diff == [], diff


# FE-3 --------------------------------------------------------------------------------------


def test_project_reports_next_version_number(auth_client: TestClient, project: dict) -> None:
    assert project["next_version_number"] == 1
    url = f"/api/projects/{project['id']}"
    v1 = auth_client.post(f"{url}/versions", json={"name": "v1"})
    assert v1.status_code == 201
    assert auth_client.get(url).json()["next_version_number"] == 2
    listed = auth_client.get("/api/projects").json()
    assert listed[0]["next_version_number"] == 2
    assert auth_client.delete(f"/api/versions/{v1.json()['id']}").status_code == 204
    assert auth_client.get(url).json()["next_version_number"] == 2
    v2 = auth_client.post(f"{url}/versions", json={"name": "v2"})
    assert v2.json()["number"] == 2


def test_owner_row_exists(app: FastAPI) -> None:
    with app.state.session_factory() as db:
        assert db.scalar(select(User).where(User.is_owner.is_(True))) is not None


# CON-1 / CON-6 ------------------------------------------------------------------------------


def test_count_fields_are_not_minutes(auth_client: TestClient) -> None:
    categories = {c["key"]: c for c in auth_client.get("/api/parts/categories").json()}
    for key in ("motor", "esc"):
        fields = {f["name"]: f for f in categories[key]["fields"]}
        assert fields["lipo_cells_min"]["unit"] is None
        assert fields["lipo_cells_max"]["unit"] is None
        assert fields["lipo_cells_min"]["label"] != fields["lipo_cells_max"]["label"]
        assert "min" not in fields["lipo_cells_min"]["label"].lower().split()
    mission = auth_client.get("/api/schema/mission").json()
    assert mission["target_endurance_min"]["unit"] == "min"


def test_schema_notes_expose_the_defaults_label(auth_client: TestClient) -> None:
    notes = auth_client.get("/api/schema/notes").json()
    assert "starting values" in notes["defaults"].lower()

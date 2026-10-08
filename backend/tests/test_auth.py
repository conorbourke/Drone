from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError

from app.config import Settings
from app.main import create_app
from tests.conftest import FETCH_HEADERS, PASSWORD, make_settings


def test_login_ok_sets_cookie_and_me_works(client: TestClient) -> None:
    response = client.post("/api/auth/login", json={"password": PASSWORD})
    assert response.status_code == 200
    body = response.json()
    assert set(body["user"]) == {"id", "email", "display_name"}
    cookie = response.headers["set-cookie"]
    assert cookie.startswith("vtol_session=")
    assert "HttpOnly" in cookie
    assert "SameSite=lax" in cookie
    assert "Path=/" in cookie
    assert "Max-Age=2592000" in cookie
    assert "Secure" not in cookie  # not production

    me = client.get("/api/auth/me")
    assert me.status_code == 200
    assert me.json() == body["user"]


def test_login_wrong_password(client: TestClient) -> None:
    response = client.post("/api/auth/login", json={"password": "nope"})
    assert response.status_code == 401
    assert response.json() == {"detail": "Wrong password."}
    assert "set-cookie" not in response.headers
    assert client.get("/api/auth/me").status_code == 401


def test_login_rate_limit_after_five_failures(client: TestClient) -> None:
    for _ in range(5):
        assert client.post("/api/auth/login", json={"password": "nope"}).status_code == 401
    blocked = client.post("/api/auth/login", json={"password": PASSWORD})
    assert blocked.status_code == 429
    assert "Too many failed login attempts" in blocked.json()["detail"]
    assert "Retry-After" in blocked.headers


def test_global_bucket_limits_across_ips(app: FastAPI, client: TestClient) -> None:
    limiter = app.state.auth.rate_limiter
    for i in range(30):
        limiter.record_failure(f"10.0.0.{i}")
    assert limiter.retry_after_seconds("192.168.1.1") > 0
    assert client.post("/api/auth/login", json={"password": PASSWORD}).status_code == 429


def test_logout_clears_session(auth_client: TestClient) -> None:
    response = auth_client.post("/api/auth/logout")
    assert response.status_code == 204
    assert 'vtol_session=""' in response.headers["set-cookie"]
    assert auth_client.get("/api/auth/me").status_code == 401


def test_me_requires_session(client: TestClient) -> None:
    response = client.get("/api/auth/me")
    assert response.status_code == 401
    assert response.json() == {"detail": "Not signed in."}


def test_password_over_72_bytes_rejected_before_bcrypt(app: FastAPI, client: TestClient) -> None:
    long_password = "é" * 40  # 80 bytes in UTF-8, 40 characters
    response = client.post("/api/auth/login", json={"password": long_password})
    assert response.status_code == 401
    # Counted as a failed attempt.
    assert len(app.state.auth.rate_limiter._by_ip) == 1


def test_fingerprint_invalidates_sessions_when_password_changes(
    app: FastAPI, client: TestClient, data_dir: Path
) -> None:
    assert client.post("/api/auth/login", json={"password": PASSWORD}).status_code == 200
    cookie = client.cookies["vtol_session"]

    other = create_app(make_settings(data_dir, app_password=SecretStr("a-new-password")))
    with TestClient(other, headers=FETCH_HEADERS) as c:
        c.cookies.set("vtol_session", cookie)
        assert c.get("/api/auth/me").status_code == 401
        # And the new password works on the new instance.
        assert c.post("/api/auth/login", json={"password": "a-new-password"}).status_code == 200


def test_tampered_cookie_rejected(client: TestClient) -> None:
    client.cookies.set("vtol_session", "eyJ1aWQiOjF9.bogus.signature")
    assert client.get("/api/auth/me").status_code == 401


def test_state_changing_request_without_fetch_header_is_403(auth_client: TestClient) -> None:
    response = auth_client.post(
        "/api/projects", json={"name": "x"}, headers={"X-Requested-With": "XMLHttpRequest"}
    )
    assert response.status_code == 403
    assert "X-Requested-With" in response.json()["detail"]
    # GET never mutates and is not gated.
    assert auth_client.get("/api/projects", headers={"X-Requested-With": ""}).status_code == 200


def test_settings_reject_long_app_password() -> None:
    with pytest.raises(ValidationError, match="72 bytes"):
        Settings(app_env="test", app_password=SecretStr("x" * 73))


def test_production_requires_secret_and_password() -> None:
    with pytest.raises(ValidationError, match="APP_SECRET_KEY"):
        Settings(app_env="production", app_password=SecretStr("pw"))
    with pytest.raises(ValidationError, match="password"):
        Settings(app_env="production", app_secret_key=SecretStr("k" * 32))
    ok = Settings(
        app_env="production", app_secret_key=SecretStr("k" * 32), app_password=SecretStr("pw")
    )
    assert ok.is_production
    assert "pw" not in repr(ok)
    assert "pw" not in str(ok.model_dump())


def test_development_generates_secret_when_missing() -> None:
    a = Settings(app_env="development")
    b = Settings(app_env="development")
    assert a.secret_key and a.secret_key != b.secret_key


def test_missing_password_fails_at_startup(data_dir: Path) -> None:
    missing = create_app(make_settings(data_dir, app_password=None))
    with pytest.raises(RuntimeError, match="APP_PASSWORD"), TestClient(missing):
        pass


def test_production_cookie_is_secure_and_hsts_set(data_dir: Path) -> None:
    prod = create_app(
        make_settings(
            data_dir,
            app_env="production",
            app_secret_key=SecretStr("k" * 40),
            app_password=SecretStr(PASSWORD),
        )
    )
    with TestClient(prod, headers=FETCH_HEADERS) as c:
        response = c.post(
            "/api/auth/login", json={"password": PASSWORD}, headers={"Fly-Client-IP": "203.0.113.9"}
        )
        assert response.status_code == 200
        assert "Secure" in response.headers["set-cookie"]
        assert response.headers["strict-transport-security"] == "max-age=31536000"
        # Fly-Client-IP keys the limiter in production.
        for _ in range(5):
            c.post(
                "/api/auth/login",
                json={"password": "nope"},
                headers={"Fly-Client-IP": "198.51.100.7"},
            )
        blocked = c.post(
            "/api/auth/login",
            json={"password": PASSWORD},
            headers={"Fly-Client-IP": "198.51.100.7"},
        )
        assert blocked.status_code == 429
        other_ip = c.post(
            "/api/auth/login",
            json={"password": PASSWORD},
            headers={"Fly-Client-IP": "198.51.100.8"},
        )
        assert other_ip.status_code == 200

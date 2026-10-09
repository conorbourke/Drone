"""Every /api route except POST /api/auth/login and GET /api/health must answer 401
without a session. Walks the live route table so a new router cannot forget the dependency."""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator

from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from tests.conftest import FETCH_HEADERS

ALLOWLIST = {("POST", "/api/auth/login"), ("GET", "/api/health")}


def _concrete(path: str) -> str:
    return re.sub(r"\{[^}]+\}", "1", path)


def iter_api_routes(app: FastAPI) -> Iterator[tuple[str, set[str]]]:
    """(path, methods) for every route, recursing into included routers.

    FastAPI 0.143+ keeps included routers nested (``_IncludedRouter`` carrying the original
    router and an include-time prefix); older versions flatten them into ``APIRoute``s.
    """

    def walk(routes: Iterable[object], prefix: str) -> Iterator[tuple[str, set[str]]]:
        for route in routes:
            if isinstance(route, APIRoute):
                yield prefix + route.path, set(route.methods or ())
            elif hasattr(route, "original_router"):
                context = getattr(route, "include_context", None)
                extra = getattr(context, "prefix", "") or ""
                yield from walk(route.original_router.routes, prefix + extra)

    yield from walk(app.routes, "")


def test_every_api_route_requires_session(app: FastAPI) -> None:
    checked: set[tuple[str, str]] = set()
    with TestClient(app, headers=FETCH_HEADERS) as client:
        for path, methods in iter_api_routes(app):
            if not path.startswith("/api"):
                continue
            for method in sorted(methods):
                if method == "HEAD":
                    continue
                key = (method, path)
                checked.add(key)
                response = client.request(method, _concrete(path), json={})
                if key in ALLOWLIST:
                    assert response.status_code != 401, key
                else:
                    assert response.status_code == 401, (key, response.status_code, response.text)
                    assert response.json() == {"detail": "Not signed in."}
    expected = {
        ("GET", "/api/projects"),
        ("POST", "/api/projects"),
        ("PUT", "/api/projects/{project_id}/draft"),
        ("POST", "/api/projects/{project_id}/versions"),
        ("DELETE", "/api/versions/{version_id}"),
        ("GET", "/api/parts/categories"),
        ("PUT", "/api/settings"),
        ("GET", "/api/schema/design"),
        ("GET", "/api/system/backups/{name}"),
        ("POST", "/api/auth/logout"),
        ("GET", "/api/{path:path}"),
        ("GET", "/api/airfoils"),
        ("GET", "/api/airfoils/{airfoil_id}"),
        ("POST", "/api/projects/{project_id}/images"),
        ("GET", "/api/projects/{project_id}/images"),
        ("PATCH", "/api/images/{image_id}"),
        ("DELETE", "/api/images/{image_id}"),
        ("GET", "/api/images/{image_id}/file"),
        ("POST", "/api/projects/{project_id}/image-readings"),
        ("GET", "/api/projects/{project_id}/image-readings"),
        ("GET", "/api/image-readings/status"),
    }
    assert expected <= checked, expected - checked
    assert len(checked) > 35

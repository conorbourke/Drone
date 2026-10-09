"""Application factory: routers, middleware, static SPA serving and lifespan."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
from collections.abc import AsyncIterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from starlette.datastructures import MutableHeaders
from starlette.requests import cookie_parser
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app import __version__
from app.backup import BackupManager
from app.config import Settings, get_settings
from app.db import make_engine, make_session_factory, sqlite_path
from app.deps import current_user
from app.jobs import AnalysisWorker, recover_analyses, validation_report_path
from app.models import User
from app.routers import (
    airfoils,
    analyses,
    assistant,
    auth,
    images,
    parts,
    projects,
    readings,
    schema,
    system,
    validation,
    versions,
)
from app.routers import settings as settings_router
from app.security import SESSION_COOKIE, AuthState

log = logging.getLogger("app")

STATE_CHANGING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
# The largest legitimate /api body is a settings or draft document (a few kB). The image upload
# route is the one exception: a 15 MB file plus the multipart envelope.
MAX_API_BODY_BYTES = 1_000_000
MAX_UPLOAD_BODY_BYTES = 16 * 1024 * 1024
UPLOAD_ROUTE = re.compile(r"^/api/projects/\d+/images$")
STATIC_ROOT_FILES: dict[str, str] = {
    "favicon.svg": "image/svg+xml",
    "manifest.webmanifest": "application/manifest+json",
    "robots.txt": "text/plain; charset=utf-8",
}
FRONTEND_MISSING_MESSAGE = "Frontend not built. The API is available under /api.\n"


class SecurityHeadersMiddleware:
    """Adds the security headers from the contract to every response."""

    def __init__(self, app: ASGIApp, production: bool) -> None:
        self.app = app
        self.production = production

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        is_api = scope.get("path", "").startswith("/api/") or scope.get("path") == "/api"

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers["X-Content-Type-Options"] = "nosniff"
                headers["Referrer-Policy"] = "same-origin"
                headers["X-Frame-Options"] = "DENY"
                # API responses are never cached, except where a route sets its own policy
                # (the image file route allows the browser a private copy for an hour).
                if is_api and "cache-control" not in headers:
                    headers["Cache-Control"] = "no-store"
                if self.production:
                    headers["Strict-Transport-Security"] = "max-age=31536000"
            await send(message)

        await self.app(scope, receive, send_with_headers)


def _has_valid_session(scope: Scope, headers: dict[bytes, bytes]) -> bool:
    """True when the request carries a session cookie that the app's signer accepts, with
    the current password fingerprint (the same checks as ``deps.current_user``, short of
    loading the user row)."""
    raw = headers.get(b"cookie")
    app = scope.get("app")
    auth: AuthState | None = getattr(getattr(app, "state", None), "auth", None)
    if not raw or auth is None:
        return False
    token = cookie_parser(raw.decode("latin-1")).get(SESSION_COOKIE)
    if not token:
        return False
    data = auth.signer.load(token)
    return data is not None and data.get("pw") == auth.fingerprint


class ApiBodyLimitMiddleware:
    """Refuses oversized /api request bodies before they are read into memory."""

    def __init__(
        self,
        app: ASGIApp,
        limit: int = MAX_API_BODY_BYTES,
        upload_limit: int = MAX_UPLOAD_BODY_BYTES,
    ) -> None:
        self.app = app
        self.limit = limit
        self.upload_limit = upload_limit

    def _limit_for(self, scope: Scope, headers: dict[bytes, bytes]) -> tuple[int, str]:
        """The image upload route (POST only) gets its own larger limit, and only for a
        signed-in owner; nothing else does. Anonymous clients get the normal limit there
        too, so they cannot make the server receive 15 MB bodies before the 401."""
        if (
            scope.get("method", "GET").upper() == "POST"
            and UPLOAD_ROUTE.match(scope.get("path", ""))
            and _has_valid_session(scope, headers)
        ):
            return self.upload_limit, f"{self.upload_limit // (1024 * 1024)} MB"
        return self.limit, f"{self.limit // 1000} kB"

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope.get("path", "").startswith("/api"):
            headers = {k: v for k, v in scope["headers"]}
            length = headers.get(b"content-length")
            if length is not None:
                limit, label = self._limit_for(scope, headers)
                try:
                    declared = int(length)
                except ValueError:
                    declared = limit + 1
                if declared > limit:
                    response = JSONResponse(
                        status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                        content={"detail": f"The request body is too large (limit {label})."},
                    )
                    await response(scope, receive, send)
                    return
            elif b"chunked" in headers.get(b"transfer-encoding", b"").lower():
                response = JSONResponse(
                    status_code=status.HTTP_411_LENGTH_REQUIRED,
                    content={"detail": "API requests must declare their Content-Length."},
                )
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)


class RequireFetchHeaderMiddleware:
    """CSRF guard: state-changing /api requests must carry ``X-Requested-With: fetch``."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] == "http"
            and scope.get("path", "").startswith("/api")
            and scope.get("method", "GET").upper() in STATE_CHANGING_METHODS
        ):
            requested_with = next(
                (v.decode("latin-1") for k, v in scope["headers"] if k == b"x-requested-with"),
                "",
            )
            if requested_with.strip().lower() != "fetch":
                response = JSONResponse(
                    status_code=status.HTTP_403_FORBIDDEN,
                    content={
                        "detail": "Missing X-Requested-With: fetch header. Requests that change "
                        "data must come from the app itself."
                    },
                )
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)


# Searched in order when APP_STATIC_DIR is unset: the container image, then a local build.
DEFAULT_STATIC_CANDIDATES: tuple[Path, ...] = (
    Path("/app/static"),
    Path(__file__).resolve().parent.parent.parent / "frontend" / "dist",
)


def resolve_static_dir(settings: Settings) -> Path | None:
    """Where the built frontend lives, or None when it is not built."""
    candidates: tuple[Path, ...]
    if settings.app_static_dir is not None:
        candidates = (settings.app_static_dir,)
    else:
        candidates = DEFAULT_STATIC_CANDIDATES
    for candidate in candidates:
        if (candidate / "index.html").is_file():
            return candidate.resolve()
    return None


def _ensure_owner(app: FastAPI) -> None:
    settings: Settings = app.state.settings
    with app.state.session_factory() as db:
        owner = db.scalar(select(User).where(User.is_owner.is_(True)).order_by(User.id))
        if owner is None:
            owner = User(email=settings.app_owner_email, display_name="Owner", is_owner=True)
            db.add(owner)
            log.info("Created owner user")
        elif owner.email != settings.app_owner_email:
            owner.email = settings.app_owner_email
        db.commit()


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    settings.app_data_dir.mkdir(parents=True, exist_ok=True)
    # Fail loudly, with a plain message, when no password is configured. The hash is derived
    # once per app instance; the test suite restarts the lifespan for every test.
    if getattr(app.state, "auth", None) is None:
        app.state.auth = AuthState.from_settings(settings)
    _ensure_owner(app)
    # Claude image readings run one at a time off the request path (see app.routers.readings).
    # Rows a previous process left "running" can never finish, so they are closed first.
    interrupted = readings.mark_interrupted_readings(app.state.session_factory)
    if interrupted:
        log.warning("Marked %d interrupted image reading(s) as failed", interrupted)
    app.state.reading_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="reading")
    # Analyses, scale jobs and the validation suite run strictly one at a time on their own
    # worker thread (see app.jobs), separate from the image readings.
    failed, queued = recover_analyses(app.state.session_factory)
    if failed:
        log.warning("Marked %d interrupted analysis(es) as failed", failed)
    worker = AnalysisWorker(app.state.session_factory, settings)
    worker.start()
    app.state.analysis_worker = worker
    for analysis_id in queued:
        worker.submit_analysis(analysis_id)
    if queued:
        log.info("Re-queued %d analysis(es)", len(queued))
    if settings.validation_on_startup and not validation_report_path(settings).is_file():
        log.info("No validation report yet: running the validation suite in the background")
        worker.submit_validation("startup")
    manager: BackupManager = app.state.backups
    manager.ensure_dir()
    scheduler: asyncio.Task[None] | None = None
    if manager.enabled:
        try:
            await asyncio.to_thread(manager.catch_up)
        except Exception:  # a failed catch-up must not block startup
            log.exception("Startup backup failed")
        scheduler = asyncio.create_task(manager.run_scheduler(), name="backup-scheduler")
    log.info(
        "Started: version=%s env=%s data_dir=%s static=%s backups=%s",
        settings.version,
        settings.app_env,
        settings.app_data_dir,
        app.state.static_dir or "(frontend not built)",
        "on" if manager.enabled else "off",
    )
    try:
        yield
    finally:
        if scheduler is not None:
            scheduler.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await scheduler
        # Drop queued readings and let the one in progress store its result (bounded by the
        # Claude request timeout) before the database engine goes away.
        executor: ThreadPoolExecutor = app.state.reading_executor
        app.state.reading_executor = None
        await asyncio.to_thread(executor.shutdown, wait=True, cancel_futures=True)
        # The running analysis stops at its next progress report and goes back to the queue;
        # the AVL/XFOIL subprocess is stopped too.
        app.state.analysis_worker = None
        await asyncio.to_thread(worker.stop)
        app.state.engine.dispose()


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    app = FastAPI(
        title="VTOL Drone Designer",
        version=settings.version,
        lifespan=lifespan,
        openapi_url=None,  # no public schema or docs; every /api route needs a session
        docs_url=None,
        redoc_url=None,
    )
    app.state.settings = settings
    app.state.engine = make_engine(settings.resolved_database_url)
    app.state.session_factory = make_session_factory(app.state.engine)
    app.state.backups = BackupManager(
        db_path=sqlite_path(settings.resolved_database_url),
        backups_dir=settings.backups_dir,
        keep=settings.backup_keep,
        hour_utc=settings.backup_hour_utc,
        enabled=settings.backup_enabled,
    )
    app.state.static_dir = resolve_static_dir(settings)

    app.add_middleware(RequireFetchHeaderMiddleware)
    app.add_middleware(ApiBodyLimitMiddleware)
    app.add_middleware(SecurityHeadersMiddleware, production=settings.is_production)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_request: Request, exc: RequestValidationError) -> JSONResponse:
        # FastAPI's default handler echoes the offending input, which (for NaN or Infinity)
        # cannot itself be serialised as JSON. The UI only needs loc and msg.
        errors = [
            {key: value for key, value in err.items() if key not in ("input", "ctx", "url")}
            for err in exc.errors()
        ]
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            content={"detail": jsonable_encoder(errors)},
        )

    @app.exception_handler(IntegrityError)
    async def _integrity_error(_request: Request, exc: IntegrityError) -> JSONResponse:
        log.warning("Integrity error: %s", exc.orig)
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={"detail": "That change conflicts with existing data."},
        )

    app.include_router(system.public_router)
    app.include_router(auth.public_router)
    app.include_router(auth.router)
    app.include_router(projects.router)
    app.include_router(versions.router)
    app.include_router(parts.router)
    app.include_router(settings_router.router)
    app.include_router(schema.router)
    app.include_router(system.router)
    app.include_router(airfoils.router)
    app.include_router(images.router)
    app.include_router(readings.router)
    app.include_router(analyses.router)
    app.include_router(validation.router)
    app.include_router(assistant.router)

    @app.api_route(
        "/api/{path:path}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        include_in_schema=False,
        dependencies=[Depends(current_user)],
    )
    def _unknown_api(path: str) -> None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"No API route /api/{path}.")

    _mount_static(app, app.state.static_dir)
    return app


def _mount_static(app: FastAPI, static_dir: Path | None) -> None:
    if static_dir is None:

        @app.api_route("/{path:path}", methods=["GET", "HEAD"], include_in_schema=False)
        def _frontend_missing(path: str) -> PlainTextResponse:
            return PlainTextResponse(FRONTEND_MISSING_MESSAGE)

        return

    assets = static_dir / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    for filename, media_type in STATIC_ROOT_FILES.items():
        _add_root_file(app, static_dir / filename, filename, media_type)

    index = static_dir / "index.html"

    @app.api_route("/{path:path}", methods=["GET", "HEAD"], include_in_schema=False)
    def _spa(path: str) -> FileResponse:
        # Always index.html: no filesystem path is ever derived from the URL.
        return FileResponse(index, media_type="text/html")


def _add_root_file(app: FastAPI, file: Path, filename: str, media_type: str) -> None:
    def handler() -> Response:
        if not file.is_file():
            raise HTTPException(status.HTTP_404_NOT_FOUND)
        return FileResponse(file, media_type=media_type)

    app.add_api_route(f"/{filename}", handler, methods=["GET", "HEAD"], include_in_schema=False)


app = create_app()

__all__ = ["__version__", "app", "create_app"]

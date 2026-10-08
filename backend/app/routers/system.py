"""Health (public), system info and backups."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy import text

from app import PHASE
from app.backup import BackupManager
from app.deps import AppSettings, DbSession, current_user
from app.schemas.system import BackupEntry, BackupState, HealthOut, SystemInfoOut

log = logging.getLogger("app.system")

public_router = APIRouter(prefix="/api", tags=["system"])
router = APIRouter(prefix="/api/system", tags=["system"], dependencies=[Depends(current_user)])


def _backups(request: Request) -> BackupManager:
    return request.app.state.backups


@public_router.get("/health", response_model=HealthOut)
def health(db: DbSession, settings: AppSettings) -> Any:
    try:
        db.execute(text("SELECT 1"))
    except Exception:  # any failure means the database is unreachable
        log.exception("Health check: database unreachable")
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"status": "error", "version": settings.version, "db": "unreachable"},
        )
    return HealthOut(status="ok", version=settings.version, db="ok")


@router.get("/info", response_model=SystemInfoOut)
def system_info(request: Request, settings: AppSettings) -> SystemInfoOut:
    manager = _backups(request)
    return SystemInfoOut(
        version=settings.version,
        phase=PHASE,
        environment=settings.app_env,
        data_dir=str(settings.app_data_dir),
        backup=BackupState(
            enabled=manager.enabled,
            last_run_at=manager.last_run_at,
            next_run_at=manager.next_run_at(),
            count=len(manager.list()),
        ),
    )


@router.get("/backups", response_model=list[BackupEntry])
def list_backups(request: Request) -> list[dict[str, Any]]:
    return [entry.as_dict() for entry in _backups(request).list()]


@router.post("/backups", response_model=BackupEntry, status_code=status.HTTP_201_CREATED)
def create_backup(request: Request) -> dict[str, Any]:
    manager = _backups(request)
    if not manager.available:
        raise HTTPException(
            status.HTTP_409_CONFLICT, detail="Backups need a file-based SQLite database."
        )
    return manager.create().as_dict()


@router.get("/backups/{name}")
def download_backup(name: str, request: Request) -> FileResponse:
    path = _backups(request).resolve(name)
    if path is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Backup not found.")
    return FileResponse(
        path,
        media_type="application/octet-stream",
        filename=name,
        headers={"Cache-Control": "no-store"},
    )

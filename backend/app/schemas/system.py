"""Health, system info and backup response bodies."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class HealthOut(BaseModel):
    status: str = Field(description="'ok' when the app and database respond.")
    version: str
    db: str = Field(description="'ok' or 'unreachable'.")


class BackupState(BaseModel):
    enabled: bool = Field(description="Whether the daily scheduler is running.")
    last_run_at: datetime | None = Field(description="When the newest backup was taken (UTC).")
    next_run_at: datetime | None = Field(description="Next scheduled backup (UTC).")
    count: int = Field(description="Number of backup files on disk.")


class SystemInfoOut(BaseModel):
    version: str
    phase: int
    environment: str
    data_dir: str
    backup: BackupState


class BackupEntry(BaseModel):
    name: str = Field(description="File name, app-YYYYMMDD-HHMMSS.db.")
    size_bytes: int
    created_at: datetime

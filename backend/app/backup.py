"""In-app SQLite backups: online copy, retention, startup catch-up and a daily scheduler.

These files live on the same volume as the database, so they protect against mistakes, not
disasters; the owner's downloaded copy is the off-site one. See docs/ARCHITECTURE.md.
"""

from __future__ import annotations

import asyncio
import logging
import re
import sqlite3
import threading
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

log = logging.getLogger("app.backup")

BACKUP_NAME_RE = re.compile(r"^app-\d{8}-\d{6}\.db$")
CATCH_UP_AGE = timedelta(hours=24)


@dataclass(frozen=True)
class BackupInfo:
    name: str
    size_bytes: int
    created_at: datetime

    def as_dict(self) -> dict[str, object]:
        return {"name": self.name, "size_bytes": self.size_bytes, "created_at": self.created_at}


def _created_at_from_name(name: str, path: Path) -> datetime:
    try:
        return datetime.strptime(name[4:19], "%Y%m%d-%H%M%S").replace(tzinfo=UTC)
    except ValueError:
        return datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)


class BackupManager:
    """Owns the backups directory. Safe to call from any thread."""

    def __init__(
        self,
        db_path: Path | None,
        backups_dir: Path,
        keep: int,
        hour_utc: int,
        enabled: bool,
    ) -> None:
        self.db_path = db_path
        self.backups_dir = backups_dir
        self.keep = max(1, keep)
        self.hour_utc = hour_utc
        self.enabled = enabled and db_path is not None
        self._lock = threading.Lock()
        self.last_run_at: datetime | None = None

    # -- queries -----------------------------------------------------------------------

    @property
    def available(self) -> bool:
        """False when the database is not a SQLite file (nothing to copy)."""
        return self.db_path is not None

    def ensure_dir(self) -> None:
        self.backups_dir.mkdir(parents=True, exist_ok=True)

    def list(self) -> list[BackupInfo]:
        """Backups on disk, newest first. Only well-formed names are reported."""
        if not self.backups_dir.is_dir():
            return []
        entries: list[BackupInfo] = []
        for path in self.backups_dir.iterdir():
            if not path.is_file() or not BACKUP_NAME_RE.fullmatch(path.name):
                continue
            entries.append(
                BackupInfo(path.name, path.stat().st_size, _created_at_from_name(path.name, path))
            )
        entries.sort(key=lambda e: e.name, reverse=True)
        return entries

    def newest(self) -> BackupInfo | None:
        entries = self.list()
        return entries[0] if entries else None

    def resolve(self, name: str) -> Path | None:
        """The file for a backup name, or None if the name is invalid or escapes the directory."""
        if not BACKUP_NAME_RE.fullmatch(name):
            return None
        base = self.backups_dir.resolve()
        candidate = (self.backups_dir / name).resolve()
        if candidate.parent != base or not candidate.is_file():
            return None
        return candidate

    def next_run_at(self, now: datetime | None = None) -> datetime | None:
        if not self.enabled:
            return None
        now = now or datetime.now(UTC)
        candidate = now.replace(hour=self.hour_utc, minute=0, second=0, microsecond=0)
        if candidate <= now:
            candidate += timedelta(days=1)
        return candidate

    # -- actions -----------------------------------------------------------------------

    def create(self) -> BackupInfo:
        """Copy the live database with SQLite's online backup API, then prune."""
        if self.db_path is None:
            raise RuntimeError("Backups need a file-based SQLite database.")
        with self._lock:
            self.ensure_dir()
            now = datetime.now(UTC).replace(microsecond=0)
            target = self.backups_dir / f"app-{now:%Y%m%d-%H%M%S}.db"
            while target.exists():
                now += timedelta(seconds=1)
                target = self.backups_dir / f"app-{now:%Y%m%d-%H%M%S}.db"
            tmp = target.with_suffix(".db.part")
            source = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
            try:
                dest = sqlite3.connect(tmp)
                try:
                    source.backup(dest)
                finally:
                    dest.close()
            finally:
                source.close()
            tmp.replace(target)
            self.last_run_at = now
            info = BackupInfo(target.name, target.stat().st_size, now)
            self._prune_locked()
            log.info("Backup written: %s (%d bytes)", info.name, info.size_bytes)
            return info

    def _prune_locked(self) -> None:
        for stale in self.list()[self.keep :]:
            try:
                (self.backups_dir / stale.name).unlink()
                log.info("Backup pruned: %s", stale.name)
            except OSError as exc:  # pragma: no cover - best effort
                log.warning("Could not delete backup %s: %s", stale.name, exc)

    def catch_up(self) -> BackupInfo | None:
        """Run a backup now if the newest one is older than 24 h or none exists."""
        newest = self.newest()
        if newest is not None:
            self.last_run_at = newest.created_at
            if datetime.now(UTC) - newest.created_at < CATCH_UP_AGE:
                return None
        log.info("No backup in the last 24 h; running one now.")
        return self.create()

    async def run_scheduler(self) -> None:
        """Sleep until the next daily slot, back up, repeat. Cancelled on shutdown."""
        while True:
            next_run = self.next_run_at()
            if next_run is None:
                return
            delay = (next_run - datetime.now(UTC)).total_seconds()
            await asyncio.sleep(max(delay, 1.0))
            try:
                await asyncio.to_thread(self.create)
            except Exception:  # keep the scheduler alive whatever went wrong
                log.exception("Scheduled backup failed")

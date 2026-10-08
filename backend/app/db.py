"""Database engine, session factory and the shared timezone-aware datetime type.

SQLite ignores ``DateTime(timezone=True)``, so every model uses :class:`TZDateTime`, which
converts to UTC on the way in and attaches ``timezone.utc`` on the way out.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import Request
from sqlalchemy import DateTime, Engine, create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.types import TypeDecorator


def utcnow() -> datetime:
    """The current time as an aware UTC datetime."""
    return datetime.now(UTC)


class TZDateTime(TypeDecorator[datetime]):
    """Store datetimes as naive UTC and load them as aware UTC.

    Naive values on bind are assumed to already be UTC. Aware values are converted.
    """

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Any) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is not None:
            value = value.astimezone(UTC)
        return value.replace(tzinfo=None)

    def process_result_value(self, value: datetime | None, dialect: Any) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


def sqlite_path(database_url: str) -> Path | None:
    """The file behind a SQLite URL, or None for other drivers and in-memory databases."""
    url = make_url(database_url)
    if url.get_backend_name() != "sqlite":
        return None
    database = url.database
    if not database or database == ":memory:":
        return None
    return Path(database)


def make_engine(database_url: str) -> Engine:
    """Create the engine. For SQLite the parent directory is created and PRAGMAs applied."""
    connect_args: dict[str, Any] = {}
    path = sqlite_path(database_url)
    is_sqlite = make_url(database_url).get_backend_name() == "sqlite"
    if is_sqlite:
        # Sessions are used from FastAPI's worker threads, not the thread that created them.
        connect_args["check_same_thread"] = False
    engine = create_engine(database_url, connect_args=connect_args, future=True)
    if is_sqlite:
        if path is not None:

            @event.listens_for(engine, "do_connect")
            def _ensure_directory(*_args: Any) -> None:
                # Lazily, on first connection: importing the app never touches the disk.
                path.parent.mkdir(parents=True, exist_ok=True)

        @event.listens_for(engine, "connect")
        def _set_sqlite_pragmas(dbapi_connection: Any, _record: Any) -> None:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.execute("PRAGMA busy_timeout=5000")
            cursor.close()

    return engine


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=True)


def get_db(request: Request) -> Iterator[Session]:
    """FastAPI dependency: one session per request, closed afterwards (rolls back if open)."""
    factory: sessionmaker[Session] = request.app.state.session_factory
    db = factory()
    try:
        yield db
    finally:
        db.close()

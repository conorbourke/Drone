"""Application settings read from environment variables.

See the "Environment variables" table in docs/ARCHITECTURE.md. Secret values are typed
``SecretStr`` so that they never appear in logs, tracebacks or API responses.
"""

from __future__ import annotations

import secrets
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, ValidationError, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BCRYPT_MAX_PASSWORD_BYTES = 72


def _default_data_dir() -> Path:
    """`/data` when the container volume exists, else `./data` next to the working directory."""
    container_dir = Path("/data")
    if container_dir.is_dir():
        return container_dir
    return Path("data").resolve()


class Settings(BaseSettings):
    """Runtime configuration. Every field maps to the upper-cased environment variable."""

    # hide_input_in_errors: a validation failure must never echo the password or the
    # secret key into the startup log.
    model_config = SettingsConfigDict(
        extra="ignore", case_sensitive=False, hide_input_in_errors=True
    )

    app_env: Literal["development", "test", "production"] = Field(
        default="development",
        description="Deployment environment. 'production' enables Secure cookies, HSTS and "
        "fail-loud checks for missing secrets.",
    )
    app_secret_key: SecretStr | None = Field(
        default=None,
        description="Signs session cookies. Required in production; in development a random "
        "key is generated per process so sessions reset on restart.",
    )
    app_password: SecretStr | None = Field(
        default=None,
        description="Owner password in plain text, hashed in memory at startup. "
        "At most 72 bytes (bcrypt limit).",
    )
    app_password_hash: SecretStr | None = Field(
        default=None,
        description="bcrypt hash of the owner password; overrides APP_PASSWORD when set.",
    )
    app_owner_email: str = Field(
        default="owner@example.com",
        description="Display identity of the single owner user.",
    )
    app_data_dir: Path = Field(
        default_factory=_default_data_dir,
        description="Directory holding the SQLite database, files and backups.",
    )
    database_url: str | None = Field(
        default=None,
        description="SQLAlchemy URL. Defaults to sqlite:///{APP_DATA_DIR}/app.db.",
    )
    app_base_url: str | None = Field(
        default=None, description="Public URL of the app, used for links in later phases."
    )
    app_static_dir: Path | None = Field(
        default=None,
        description="Directory with the built frontend (index.html, assets/). When unset the "
        "app looks for /app/static, then ../frontend/dist relative to the backend package.",
    )
    app_version: str | None = Field(
        default=None,
        description="Overrides the reported application version (for example a git SHA).",
    )
    anthropic_api_key: SecretStr | None = Field(
        default=None,
        description="Claude API key, server only. Enables reading reference images (Phase 2) "
        "and the assistant (Phase 3). Without it those features show a plain message.",
    )
    claude_model: str = Field(
        default="claude-opus-5-5",
        min_length=1,
        description="Claude model used for image reading and the assistant.",
    )
    claude_fake_response_file: Path | None = Field(
        default=None,
        description="Test seam: outside production, image reading returns this JSON file "
        "instead of calling the Claude API. Ignored when APP_ENV=production.",
    )
    claude_fake_chat_file: Path | None = Field(
        default=None,
        description="Test seam: outside production, the assistant replays this scripted JSON "
        "conversation (tool calls and answers) instead of calling the Claude API. Ignored "
        "when APP_ENV=production.",
    )
    claude_fake_supplier_file: Path | None = Field(
        default=None,
        description="Test seam: outside production, the supplier lookup (refresh listings) "
        "returns this JSON file instead of calling Claude with web search. Ignored when "
        "APP_ENV=production.",
    )
    export_timeout_s: float = Field(
        default=600.0,
        gt=0,
        description="Phase 5: hard time limit of one file export (the CAD child process is "
        "killed after it). Default 10 minutes.",
    )
    export_memory_limit_mb: float = Field(
        default=1500.0,
        gt=0,
        description="Phase 5: the CAD child process is killed when its resident memory goes "
        "above this (the machine has 2 GB; the API process keeps the rest).",
    )
    export_fake_generator: str | None = Field(
        default=None,
        description="Test seam: outside production, file exports call this 'module:function' "
        "(same signature as app.cad.generate_files) in the child process instead of the CAD "
        "kernel. Ignored when APP_ENV=production.",
    )
    validation_on_startup: bool = Field(
        default=True,
        description="Run the validation suite once in the background at startup when no "
        "report exists yet. Tests set this to false.",
    )
    backup_hour_utc: int = Field(
        default=3, ge=0, le=23, description="Hour (UTC) of the daily in-app database backup."
    )
    backup_keep: int = Field(
        default=14, ge=1, description="Number of in-app backups retained (newest kept)."
    )
    backup_enabled: bool = Field(
        default=True,
        description="Run the startup catch-up backup and the daily scheduler. "
        "Tests set this to false; manual backups still work when disabled.",
    )

    @field_validator("app_password")
    @classmethod
    def _password_fits_bcrypt(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None:
            raw = value.get_secret_value()
            if len(raw.encode("utf-8")) > BCRYPT_MAX_PASSWORD_BYTES:
                raise ValueError(
                    "APP_PASSWORD is longer than 72 bytes, which bcrypt cannot hash. "
                    "Choose a shorter password."
                )
            if not raw:
                raise ValueError("APP_PASSWORD is empty. Set a password.")
        return value

    @model_validator(mode="after")
    def _production_checks(self) -> Settings:
        if self.app_env == "production":
            if self.app_secret_key is None or not self.app_secret_key.get_secret_value():
                raise ValueError(
                    "APP_SECRET_KEY is not set. In production it must be a long random "
                    "string (for example the output of: python -c "
                    "'import secrets; print(secrets.token_hex(32))')."
                )
            if not self.has_password:
                raise ValueError(
                    "No owner password configured. Set APP_PASSWORD (or APP_PASSWORD_HASH) "
                    "in the server environment."
                )
        if self.app_secret_key is None or not self.app_secret_key.get_secret_value():
            # Development / test convenience: nothing forgeable lives in the repository.
            self.app_secret_key = SecretStr(secrets.token_hex(32))
        return self

    @property
    def has_password(self) -> bool:
        return bool(
            (self.app_password_hash and self.app_password_hash.get_secret_value())
            or (self.app_password and self.app_password.get_secret_value())
        )

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def secret_key(self) -> str:
        assert self.app_secret_key is not None  # set by the validator
        return self.app_secret_key.get_secret_value()

    @property
    def resolved_database_url(self) -> str:
        if self.database_url:
            return self.database_url
        return f"sqlite:///{self.app_data_dir.resolve() / 'app.db'}"

    @property
    def backups_dir(self) -> Path:
        return self.app_data_dir / "backups"

    @property
    def files_dir(self) -> Path:
        return self.app_data_dir / "files"

    @property
    def images_dir(self) -> Path:
        return self.files_dir / "images"

    @property
    def exports_dir(self) -> Path:
        return self.files_dir / "exports"

    @property
    def flight_logs_dir(self) -> Path:
        """Phase 6 flight logs: ``{APP_DATA_DIR}/files/logs/{storage_name}``."""
        return self.files_dir / "logs"

    @property
    def fake_export_generator(self) -> str | None:
        """The export generator test seam, only outside production."""
        if self.is_production:
            return None
        return self.export_fake_generator

    @property
    def fake_claude_response_file(self) -> Path | None:
        """The test-seam file, only outside production."""
        if self.is_production:
            return None
        return self.claude_fake_response_file

    @property
    def fake_claude_chat_file(self) -> Path | None:
        """The assistant's scripted test-seam file, only outside production."""
        if self.is_production:
            return None
        return self.claude_fake_chat_file

    @property
    def fake_claude_supplier_file(self) -> Path | None:
        """The supplier lookup's test-seam file, only outside production."""
        if self.is_production:
            return None
        return self.claude_fake_supplier_file

    @property
    def version(self) -> str:
        from app import __version__

        return self.app_version or __version__


class ConfigurationError(SystemExit):
    """Raised (as a clean process exit) when the environment is not usable."""


def _plain_messages(exc: ValidationError) -> list[str]:
    messages: list[str] = []
    for err in exc.errors(include_url=False, include_input=False):
        msg = str(err.get("msg", ""))
        msg = msg.removeprefix("Value error, ")
        messages.append(msg)
    return messages


def get_settings() -> Settings:
    """Build settings from the current environment (used by the app, CLI entry points and
    Alembic). On a misconfiguration the process exits with the plain messages only: no
    traceback and never the offending values."""
    try:
        return Settings()
    except ValidationError as exc:
        lines = ["Configuration error:"] + [f"  - {m}" for m in _plain_messages(exc)]
        raise ConfigurationError("\n".join(lines)) from None

"""SQLAlchemy ORM models. See "Data model" in docs/ARCHITECTURE.md.

Every table has ``id``, ``created_at`` and ``updated_at``; datetimes use :class:`TZDateTime`.
Foreign-key delete actions are declared here *and* in the Alembic migration so that the DDL
(not the ORM) enforces cascades and SET NULL.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from app.db import TZDateTime, utcnow


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        TZDateTime, nullable=False, default=utcnow, onupdate=utcnow
    )


class User(TimestampMixin, Base):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False, default="Owner")
    is_owner: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class Project(TimestampMixin, Base):
    __tablename__ = "projects"
    __table_args__ = (UniqueConstraint("owner_id", "name", name="uq_projects_owner_name"),)

    owner_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    draft_parameters: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    draft_mission: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    draft_based_on_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("design_versions.id", ondelete="SET NULL", use_alter=True), nullable=True
    )
    draft_updated_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False, default=utcnow)
    next_version_number: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    versions: Mapped[list[DesignVersion]] = relationship(
        back_populates="project",
        foreign_keys="DesignVersion.project_id",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class DesignVersion(TimestampMixin, Base):
    __tablename__ = "design_versions"
    __table_args__ = (
        UniqueConstraint("project_id", "number", name="uq_versions_project_number"),
        UniqueConstraint("project_id", "name", name="uq_versions_project_name"),
    )

    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    owner_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    number: Mapped[int] = mapped_column(Integer, nullable=False)
    parameters: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    mission: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    parent_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("design_versions.id", ondelete="SET NULL"), nullable=True
    )

    project: Mapped[Project] = relationship(back_populates="versions", foreign_keys=[project_id])


class Part(TimestampMixin, Base):
    __tablename__ = "parts"
    __table_args__ = (
        UniqueConstraint("category", "manufacturer", "model", name="uq_parts_identity"),
    )

    category: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    manufacturer: Mapped[str] = mapped_column(String(200), nullable=False)
    model: Mapped[str] = mapped_column(String(200), nullable=False)
    mass_g: Mapped[float] = mapped_column(Float, nullable=False)
    price_eur_estimate: Mapped[float | None] = mapped_column(Float, nullable=True)
    spec: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False, default="")
    verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # Phase 4 supplier lookup: when Claude last refreshed this part's listings (the one-per-hour
    # rate limit), and the outcome of the latest refresh for the UI.
    listings_refreshed_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    listings_refresh_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    listings_refresh_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    # cascade handles children already loaded in the session; passive_deletes leaves the
    # rest to the DDL ON DELETE CASCADE instead of nulling their foreign key.
    listings: Mapped[list[PartListing]] = relationship(
        back_populates="part",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="PartListing.id",
    )


class PartListing(TimestampMixin, Base):
    __tablename__ = "part_listings"

    part_id: Mapped[int] = mapped_column(
        ForeignKey("parts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    supplier_name: Mapped[str] = mapped_column(String(200), nullable=False)
    country: Mapped[str] = mapped_column(String(2), nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    price_eur: Mapped[float | None] = mapped_column(Float, nullable=True)
    in_stock: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    last_checked_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    # Phase 4: result of the server-side link check (HEAD/GET, 200-399 is working); null when
    # never checked (seed data).
    url_ok: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    url_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    url_checked_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)

    part: Mapped[Part] = relationship(back_populates="listings")


class AppSettings(TimestampMixin, Base):
    __tablename__ = "app_settings"

    owner_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    data: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)


class Image(TimestampMixin, Base):
    """A reference image uploaded to a project. The file lives at
    ``{APP_DATA_DIR}/files/images/{project_id}/{storage_name}``; deleting the row (through the
    API) deletes the file, and deleting the project deletes its directory."""

    __tablename__ = "images"

    owner_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(50), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    width_px: Mapped[int] = mapped_column(Integer, nullable=False)
    height_px: Mapped[int] = mapped_column(Integer, nullable=False)
    view: Mapped[str] = mapped_column(String(20), nullable=False)
    storage_name: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)


class ImageReading(TimestampMixin, Base):
    """One Claude reading of a project's reference images and the proposal derived from it."""

    __tablename__ = "image_readings"

    owner_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    model: Mapped[str] = mapped_column(String(100), nullable=False)
    reference: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    image_ids: Mapped[list[int]] = mapped_column(JSON, nullable=False)
    proposal: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    usage: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)


class Analysis(TimestampMixin, Base):
    """One full analysis or scale-to-weight job and its result (Phase 3).

    ``version_id`` is ``ON DELETE RESTRICT`` (Phase 1 policy): a version with analyses cannot be
    deleted silently; ``DELETE /api/versions/{vid}`` answers 409 unless ``with_analyses=true``.
    """

    __tablename__ = "analyses"

    owner_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version_id: Mapped[int | None] = mapped_column(
        ForeignKey("design_versions.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    inputs: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    inputs_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    progress: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    stage: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    result: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration_s: Mapped[float | None] = mapped_column(Float, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    reused_from_id: Mapped[int | None] = mapped_column(
        ForeignKey("analyses.id", ondelete="SET NULL"), nullable=True
    )


class AssistantThread(TimestampMixin, Base):
    """The assistant conversation of one project (one thread per project)."""

    __tablename__ = "assistant_threads"

    owner_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, unique=True
    )


class AssistantMessage(TimestampMixin, Base):
    """One Messages API turn, stored exactly as sent or received (``content`` is the full list
    of content blocks, thinking and tool blocks included) so the conversation continues
    append-only. ``meta`` holds what the UI shows beside it (tool-call labels, proposals,
    stop reason, usage); it is never sent to Claude."""

    __tablename__ = "assistant_messages"

    thread_id: Mapped[int] = mapped_column(
        ForeignKey("assistant_threads.id", ondelete="CASCADE"), nullable=False, index=True
    )
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    content: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)
    meta: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)


class PartSelection(TimestampMixin, Base):
    """The part chosen for one role of a project's draft (``version_id`` null) or of a version
    (Phase 4). ``locked`` rows are the owner's choices and win over the engine; unlocked rows
    record the engine's latest pick.

    ``version_id`` is ``ON DELETE CASCADE``, not the Phase 1 RESTRICT policy: a selection is
    derived design data, not a measurement, so it goes with its version (and its project) and
    must never block deleting one. ``part_id`` is ``ON DELETE CASCADE`` too: deleting a part
    from the catalogue drops the selections that used it and the engine fills the role again.
    One row per role: unique ``(version_id, role)`` for versions and a partial unique index on
    ``(project_id, role) WHERE version_id IS NULL`` for the draft (migration 0004).
    """

    __tablename__ = "part_selections"
    __table_args__ = (
        Index(
            "uq_part_selections_version_role",
            "version_id",
            "role",
            unique=True,
            sqlite_where=text("version_id IS NOT NULL"),
        ),
        Index(
            "uq_part_selections_draft_role",
            "project_id",
            "role",
            unique=True,
            sqlite_where=text("version_id IS NULL"),
        ),
    )

    owner_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version_id: Mapped[int | None] = mapped_column(
        ForeignKey("design_versions.id", ondelete="CASCADE"), nullable=True, index=True
    )
    role: Mapped[str] = mapped_column(String(40), nullable=False)
    category: Mapped[str] = mapped_column(String(50), nullable=False)
    part_id: Mapped[int] = mapped_column(
        ForeignKey("parts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    locked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class Export(TimestampMixin, Base):
    """One "Generate files" job of a project's draft or version and its manifest (Phase 5).

    The files live under ``{APP_DATA_DIR}/files/{files_dir}`` (``exports/{id}``); deleting the
    row through the API, its version or its project removes that directory. ``version_id`` is
    ``ON DELETE CASCADE``, not the Phase 1 RESTRICT policy: exports are reproducible from the
    version, so they must never block deleting it (migration 0005).
    """

    __tablename__ = "exports"
    __table_args__ = {"sqlite_autoincrement": True}  # ids name directories: never reused

    owner_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version_id: Mapped[int | None] = mapped_column(
        ForeignKey("design_versions.id", ondelete="CASCADE"), nullable=True, index=True
    )
    source: Mapped[str] = mapped_column(String(20), nullable=False)  # draft | version
    inputs: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    inputs_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False)  # queued|running|done|error
    progress: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    stage: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    manifest: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    files_dir: Mapped[str | None] = mapped_column(String(255), nullable=True)
    total_size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration_s: Mapped[float | None] = mapped_column(Float, nullable=True)
    peak_rss_mb: Mapped[float | None] = mapped_column(Float, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    reused_from_id: Mapped[int | None] = mapped_column(
        ForeignKey("exports.id", ondelete="SET NULL"), nullable=True
    )


class FlightLog(TimestampMixin, Base):
    """One uploaded ArduPilot log of a project and what the worker made of it (Phase 6).

    The file lives at ``{APP_DATA_DIR}/files/logs/{storage_name}``; deleting the row through
    the API (or the project) removes it. ``version_id`` (the version that flew, null for the
    draft) is ``ON DELETE RESTRICT`` (Phase 1 policy): a version with flight logs cannot be
    deleted, so measurements are never orphaned silently. ``result`` is the full
    ``process_log`` output (phases, statistics, 5 Hz chart series), ``comparison`` the
    ``compare_log`` output against the design's analysis (migration 0006).
    """

    __tablename__ = "flight_logs"
    __table_args__ = {"sqlite_autoincrement": True}

    owner_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version_id: Mapped[int | None] = mapped_column(
        ForeignKey("design_versions.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    storage_name: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    sample: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    takeoff_mass_kg: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False)  # queued|running|done|error
    progress: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    stage: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    firmware: Mapped[str | None] = mapped_column(String(200), nullable=True)
    vehicle_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    log_start_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    flight_duration_s: Mapped[float | None] = mapped_column(Float, nullable=True)
    summary: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    comparison: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    analysis_id: Mapped[int | None] = mapped_column(
        ForeignKey("analyses.id", ondelete="SET NULL"), nullable=True
    )
    duration_s: Mapped[float | None] = mapped_column(Float, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)


class Calibration(TimestampMixin, Base):
    """One applied calibration factor of a project (Phase 6): a multiplier on the model's
    prediction (1.0 = the model was right) with its uncertainty, how many logs it combines and
    which ones. Rows exist only while applied; "undo" deletes them. ``version_id`` (set when
    every source log flew the same version) is ``ON DELETE RESTRICT``."""

    __tablename__ = "calibrations"
    __table_args__ = (UniqueConstraint("project_id", "name", name="uq_calibrations_project_name"),)

    owner_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version_id: Mapped[int | None] = mapped_column(
        ForeignKey("design_versions.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    name: Mapped[str] = mapped_column(String(40), nullable=False)
    value: Mapped[float] = mapped_column(Float, nullable=False)
    uncertainty: Mapped[float] = mapped_column(Float, nullable=False)
    n_logs: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    source_log_ids: Mapped[list[int]] = mapped_column(JSON, nullable=False)
    details: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)


class BuiltWeight(TimestampMixin, Base):
    """The weighed (as-built) mass of one component of a project's aircraft (Phase 6), next to
    the mass model's prediction at the time it was entered. Structure items feed the structural
    mass calibration factor."""

    __tablename__ = "built_weights"
    __table_args__ = (UniqueConstraint("project_id", "key", name="uq_built_weights_project_key"),)

    owner_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    key: Mapped[str] = mapped_column(String(60), nullable=False)
    label: Mapped[str] = mapped_column(String(200), nullable=False)
    group: Mapped[str] = mapped_column(String(40), nullable=False)
    subgroup: Mapped[str] = mapped_column(String(40), nullable=False)
    predicted_g: Mapped[float] = mapped_column(Float, nullable=False)
    measured_g: Mapped[float] = mapped_column(Float, nullable=False)
    note: Mapped[str] = mapped_column(Text, nullable=False, default="")

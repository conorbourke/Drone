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
    Integer,
    String,
    Text,
    UniqueConstraint,
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

    part: Mapped[Part] = relationship(back_populates="listings")


class AppSettings(TimestampMixin, Base):
    __tablename__ = "app_settings"

    owner_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    data: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)

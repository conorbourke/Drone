"""Upgrade stored documents to the current schema version.

Policy (docs/ARCHITECTURE.md): every schema change ships an upgrader step here; stored rows are
never rewritten in place; documents are upgraded on read and on restore; new fields must have
defaults so the Pydantic model fills them.

Each ``_*_STEPS`` table maps a version ``n`` to a function that turns a version-``n`` document
into version ``n + 1``. Phase 1 ships version 1 of every document, so the tables are empty.
"""

from __future__ import annotations

import copy
from collections.abc import Callable
from typing import Any

from app.defaults import DESIGN_SCHEMA_VERSION, MISSION_SCHEMA_VERSION, SETTINGS_SCHEMA_VERSION

Upgrader = Callable[[dict[str, Any]], dict[str, Any]]


class UnknownSchemaVersion(ValueError):
    """The stored document is newer than this build understands."""


_PARAMETER_STEPS: dict[int, Upgrader] = {}
_MISSION_STEPS: dict[int, Upgrader] = {}
_SETTINGS_STEPS: dict[int, Upgrader] = {}


def _upgrade(
    doc: dict[str, Any], steps: dict[int, Upgrader], current: int, kind: str
) -> dict[str, Any]:
    doc = copy.deepcopy(doc)
    version = int(doc.get("schema_version", 1))
    if version > current:
        raise UnknownSchemaVersion(
            f"This {kind} document is schema version {version}, but this build only "
            f"understands up to version {current}. Update the app."
        )
    while version < current:
        step = steps.get(version)
        if step is None:
            raise UnknownSchemaVersion(
                f"No upgrade step from {kind} schema version {version} to {version + 1}."
            )
        doc = step(doc)
        version += 1
        doc["schema_version"] = version
    doc["schema_version"] = current
    return doc


def upgrade_parameters(doc: dict[str, Any]) -> dict[str, Any]:
    """Design parameters document -> current schema version."""
    return _upgrade(doc, _PARAMETER_STEPS, DESIGN_SCHEMA_VERSION, "design parameters")


def upgrade_mission(doc: dict[str, Any]) -> dict[str, Any]:
    """Mission document -> current schema version."""
    return _upgrade(doc, _MISSION_STEPS, MISSION_SCHEMA_VERSION, "mission")


def upgrade_settings(doc: dict[str, Any]) -> dict[str, Any]:
    """Settings document (full or partial overrides) -> current schema version."""
    return _upgrade(doc, _SETTINGS_STEPS, SETTINGS_SCHEMA_VERSION, "settings")

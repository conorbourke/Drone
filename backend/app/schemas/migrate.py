"""Upgrade stored documents to the current schema version.

Policy (docs/ARCHITECTURE.md): every schema change ships an upgrader step here; stored rows are
never rewritten in place; documents are upgraded on read and on restore; new fields must have
defaults so the Pydantic model fills them.

Each ``_*_STEPS`` table maps a version ``n`` to a function that turns a version-``n`` document
into version ``n + 1``. Steps add new fields with the values they had when the step was
written (not the live defaults), so an upgrade gives the same result whatever later defaults do.

Design parameters: 1 -> 2 (Phase 2) adds wing twist, boom diameter, the tail V angle and airfoil,
and the propulsion, battery and allowances blocks.

Settings: 1 -> 2 (Phase 3) adds ``checks.manoeuvre_load_factor`` (3.0),
``checks.structural_safety_factor`` (1.5), ``checks.transition_thrust_margin_min`` (1.3) and
``analysis.ncrit`` (9); 2 -> 3 (Phase 4) adds ``budget.prototype_eur`` (5000). Stored
settings rows hold only the owner's overrides; the settings router
diffs the upgraded document against the live defaults again, so a value the step added is not
mistaken for an override.
"""

from __future__ import annotations

import copy
from collections.abc import Callable
from typing import Any

from app.defaults import DESIGN_SCHEMA_VERSION, MISSION_SCHEMA_VERSION, SETTINGS_SCHEMA_VERSION

Upgrader = Callable[[dict[str, Any]], dict[str, Any]]


class UnknownSchemaVersion(ValueError):
    """The stored document is newer than this build understands."""


def _setdefaults(doc: dict[str, Any], block: str, values: dict[str, Any]) -> None:
    current = doc.get(block)
    if not isinstance(current, dict):
        current = {}
        doc[block] = current
    for key, value in values.items():
        current.setdefault(key, copy.deepcopy(value))


def _parameters_1_to_2(doc: dict[str, Any]) -> dict[str, Any]:
    _setdefaults(doc, "wing", {"twist_deg": 0.0})
    _setdefaults(doc, "booms", {"diameter_mm": 20.0})
    _setdefaults(doc, "tail", {"v_angle_deg": 40.0, "airfoil": "naca0009"})
    _setdefaults(
        doc,
        "propulsion",
        {"prop_diameter_mm": 330.0, "prop_pitch_mm": 140.0, "prop_blades": 2},
    )
    _setdefaults(
        doc,
        "battery",
        {
            "chemistry": "lipo",
            "cells_series": 6,
            "cells_parallel": 1,
            "capacity_mah": 5000.0,
            "x_mm": 380.0,
        },
    )
    _setdefaults(doc, "allowances", {"avionics_g": 220.0, "wiring_fraction": 0.06})
    return doc


def _settings_1_to_2(doc: dict[str, Any]) -> dict[str, Any]:
    """Phase 3: structure and transition thresholds, and the XFOIL Ncrit."""
    _setdefaults(
        doc,
        "checks",
        {
            "manoeuvre_load_factor": 3.0,
            "structural_safety_factor": 1.5,
            "transition_thrust_margin_min": 1.3,
        },
    )
    _setdefaults(doc, "analysis", {"ncrit": 9.0})
    return doc


def _settings_2_to_3(doc: dict[str, Any]) -> dict[str, Any]:
    """Phase 4: the prototype parts budget."""
    _setdefaults(doc, "budget", {"prototype_eur": 5000.0})
    return doc


_PARAMETER_STEPS: dict[int, Upgrader] = {1: _parameters_1_to_2}
_MISSION_STEPS: dict[int, Upgrader] = {}
_SETTINGS_STEPS: dict[int, Upgrader] = {1: _settings_1_to_2, 2: _settings_2_to_3}


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

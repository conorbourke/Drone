"""Phase 4 parts list service: catalogue snapshot, generic analysis, selection and storage.

The parts list is always computed from the design, the catalogue and the owner's locked
choices (:func:`app.engine.selection.build_parts_list`). ``part_selections`` holds the locked
rows (the owner's choices) and a record of the engine's latest picks (``locked = false``),
written whenever the list is computed by a state-changing request (recompute, replace or lock,
unlock, an analysis being queued, a version saved). ``GET`` requests never write; they report
whether the stored record still matches (``stored.in_sync``). The draft and version payloads
carry the stored record as ``parts_selection`` (see :func:`selection_payload`).
"""

from __future__ import annotations

import copy
import hashlib
import threading
from collections import OrderedDict
from datetime import datetime
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session, selectinload

from app.config import Settings
from app.db import utcnow
from app.engine.analysis import ENGINE_VERSION, apply_parts, resolve_settings, run_analysis
from app.engine.geometry import build_geometry
from app.engine.mass import solve_mass
from app.engine.selection import (
    ROLE_CATEGORIES,
    ROLE_LABELS,
    ROLE_ORDER,
    SELECTION_VERSION,
    SYSTEM_LABELS,
    build_parts_list,
    part_name,
)
from app.jobs import canonical_json, polar_cache_dir
from app.models import DesignVersion, Part, PartSelection, Project

_CACHE_SIZE = 16
_cache: OrderedDict[str, dict[str, Any]] = OrderedDict()
_cache_lock = threading.Lock()


class PartsListError(Exception):
    """The list cannot be made (invalid design); ``str(exc)`` is a plain message."""


# ---------------------------------------------------------------------------
# Catalogue
# ---------------------------------------------------------------------------


def _iso(value: datetime | None) -> str | None:
    return value.isoformat().replace("+00:00", "Z") if value is not None else None


def part_dict(part: Part) -> dict[str, Any]:
    return {
        "id": part.id,
        "category": part.category,
        "manufacturer": part.manufacturer,
        "model": part.model,
        "mass_g": part.mass_g,
        "price_eur_estimate": part.price_eur_estimate,
        "spec": copy.deepcopy(part.spec),
        "source": part.source,
        "verified": part.verified,
        "notes": part.notes,
        "listings": [
            {
                "id": li.id,
                "supplier_name": li.supplier_name,
                "country": li.country,
                "url": li.url,
                "price_eur": li.price_eur,
                "in_stock": li.in_stock,
                "last_checked_at": _iso(li.last_checked_at),
                "url_ok": li.url_ok,
                "url_status": li.url_status,
            }
            for li in part.listings
        ],
    }


def load_catalogue(db: Session) -> list[dict[str, Any]]:
    parts = db.scalars(select(Part).options(selectinload(Part.listings)).order_by(Part.id)).all()
    return [part_dict(p) for p in parts]


# ---------------------------------------------------------------------------
# Generic-parts analysis (cached; the fast path, about a second)
# ---------------------------------------------------------------------------


def generic_analysis(
    parameters: dict[str, Any],
    mission: dict[str, Any],
    settings_doc: dict[str, Any],
    cache_dir: str | None,
) -> dict[str, Any]:
    key = hashlib.sha256(
        canonical_json([parameters, mission, settings_doc, ENGINE_VERSION]).encode("ascii")
    ).hexdigest()
    with _cache_lock:
        if key in _cache:
            _cache.move_to_end(key)
            return _cache[key]
    result = run_analysis(
        parameters, mission, settings_doc, mode="fast", cache_dir=cache_dir, uncertainty=False
    )
    with _cache_lock:
        _cache[key] = result
        while len(_cache) > _CACHE_SIZE:
            _cache.popitem(last=False)
    return result


def _mass_solver(
    parameters: dict[str, Any],
    mission: dict[str, Any],
    settings_doc: dict[str, Any],
    analysis: dict[str, Any],
) -> Any:
    sizing = (analysis.get("structure") or {}).get("spar_sizing") or {}
    tube = (
        {"outer_mm": sizing["outer_mm"], "wall_mm": sizing["wall_mm"]}
        if sizing.get("outer_mm")
        else None
    )

    def solve(parts: dict[str, Any]) -> dict[str, Any]:
        q, mass_parts, _used = apply_parts(parameters, parts)
        return solve_mass(
            q, build_geometry(q), mission, settings_doc, spar_tube=tube, parts=mass_parts
        )

    return solve


# ---------------------------------------------------------------------------
# Stored selections
# ---------------------------------------------------------------------------


def _rows(db: Session, project_id: int, version_id: int | None) -> list[PartSelection]:
    stmt = select(PartSelection).where(PartSelection.project_id == project_id)
    stmt = stmt.where(
        PartSelection.version_id.is_(None)
        if version_id is None
        else PartSelection.version_id == version_id
    )
    return list(db.scalars(stmt).all())


def locked_choices(
    db: Session, project_id: int, version_id: int | None
) -> dict[str, dict[str, Any]]:
    return {
        r.role: {"part_id": r.part_id, "quantity": r.quantity}
        for r in _rows(db, project_id, version_id)
        if r.locked
    }


def persist_selection(
    db: Session,
    owner_id: int,
    project_id: int,
    version_id: int | None,
    selections: dict[str, dict[str, Any]],
) -> None:
    """Store the computed picks (unlocked rows) next to the owner's locked rows. Roles the
    engine could not fill lose their unlocked row; locked rows are never touched here."""
    existing = {r.role: r for r in _rows(db, project_id, version_id)}
    for role, row in existing.items():
        if not row.locked and role not in selections:
            db.delete(row)
    for role, sel in selections.items():
        row = existing.get(role)
        if row is not None and row.locked:
            # The owner's part stays; record the quantity the design needs for it.
            if row.part_id == sel["part_id"] and row.quantity != int(sel["quantity"]):
                row.quantity = int(sel["quantity"])
            continue
        if row is None:
            row = PartSelection(
                owner_id=owner_id,
                project_id=project_id,
                version_id=version_id,
                role=role,
                locked=False,
            )
            db.add(row)
        row.part_id = sel["part_id"]
        row.category = sel["category"]
        row.quantity = int(sel["quantity"])
    db.flush()


def set_lock(
    db: Session,
    owner_id: int,
    project_id: int,
    version_id: int | None,
    role: str,
    part: Part,
    quantity: int | None,
    locked: bool,
) -> None:
    row = next((r for r in _rows(db, project_id, version_id) if r.role == role), None)
    if row is None:
        row = PartSelection(
            owner_id=owner_id, project_id=project_id, version_id=version_id, role=role
        )
        db.add(row)
    row.part_id = part.id
    row.category = part.category
    row.quantity = int(quantity or 1)
    row.locked = locked
    db.flush()


def clear_lock(db: Session, project_id: int, version_id: int | None, role: str) -> bool:
    row = next((r for r in _rows(db, project_id, version_id) if r.role == role), None)
    if row is None or not row.locked:
        return False
    row.locked = False
    db.flush()
    return True


def copy_selection(
    db: Session,
    project_id: int,
    src_version_id: int | None,
    dst_version_id: int | None,
    *,
    locked_only: bool = False,
) -> None:
    """Copy a source's stored rows to another source of the same project (replacing what the
    destination had): draft -> new version, version -> duplicate, version -> draft on
    restore."""
    db.execute(
        delete(PartSelection).where(
            PartSelection.project_id == project_id,
            PartSelection.version_id.is_(None)
            if dst_version_id is None
            else PartSelection.version_id == dst_version_id,
        )
    )
    for r in _rows(db, project_id, src_version_id):
        if locked_only and not r.locked:
            continue
        db.add(
            PartSelection(
                owner_id=r.owner_id,
                project_id=project_id,
                version_id=dst_version_id,
                role=r.role,
                category=r.category,
                part_id=r.part_id,
                quantity=r.quantity,
                locked=r.locked,
            )
        )
    db.flush()


def selection_payload(
    db: Session, project_id: int, version_id: int | None
) -> dict[str, Any] | None:
    """The stored selection for the draft or version payload (``parts_selection``), or None
    when nothing is stored. Shape::

        {"updated_at": ISO, "roles": {role: {part_id, category, manufacturer, model, name,
          quantity, locked, unit_mass_g, line_mass_g, verified, spec}}, "masses_g": {...}}

    ``line_mass_g`` is the installed mass (tubes: cut length is not known here, so it is the
    bought quantity x the part mass; the parts list gives the installed figure).
    ``masses_g`` sums by the Tier 1 mass-model component they replace: ``lift_motor_each``,
    ``lift_prop_each``, ``esc_each``, ``cruise_motor``, ``pusher_prop``, ``tilt_servo_each``,
    ``battery`` (pack, or cells x 1.08 for a custom pack), ``avionics`` (autopilot, GPS,
    receiver, telemetry and a 20 g power module), ``spar_tube_per_m``, ``boom_tube_per_m``.
    """
    rows = _rows(db, project_id, version_id)
    if not rows:
        return None
    parts = {
        p.id: p for p in db.scalars(select(Part).where(Part.id.in_({r.part_id for r in rows})))
    }
    out: dict[str, Any] = {}
    masses: dict[str, float] = {}
    avionics = 0.0
    for r in sorted(rows, key=lambda r: ROLE_ORDER.index(r.role) if r.role in ROLE_ORDER else 99):
        part = parts.get(r.part_id)
        if part is None:
            continue
        line = part.mass_g * r.quantity
        out[r.role] = {
            "part_id": part.id,
            "category": part.category,
            "manufacturer": part.manufacturer,
            "model": part.model,
            "name": f"{part.manufacturer} {part.model}",
            "quantity": r.quantity,
            "locked": r.locked,
            "unit_mass_g": part.mass_g,
            "line_mass_g": round(line, 1),
            "verified": part.verified,
            "spec": part.spec,
        }
        if r.role in ("lift_motor", "lift_prop", "esc", "tilt_servo"):
            masses[f"{r.role}_each"] = part.mass_g
        elif r.role in ("cruise_motor", "pusher_prop"):
            masses[r.role] = part.mass_g
        elif r.role == "battery":
            masses["battery"] = round(line * (1.08 if part.category == "cell" else 1.0), 1)
        elif r.role in ("autopilot", "gps", "radio", "telemetry"):
            avionics += line
        elif r.role in ("spar_tube", "boom_tube"):
            masses[f"{r.role}_per_m"] = part.spec.get("mass_per_m_g", part.mass_g)
    if avionics:
        masses["avionics"] = round(avionics + 20.0, 1)
    return {
        "updated_at": _iso(max(r.updated_at for r in rows)),
        "roles": out,
        "masses_g": masses,
    }


# ---------------------------------------------------------------------------
# The list
# ---------------------------------------------------------------------------


def compute(
    db: Session,
    app_settings: Settings,
    owner_id: int,
    project: Project,
    version: DesignVersion | None,
    parameters: dict[str, Any],
    mission: dict[str, Any],
    settings_doc: dict[str, Any],
) -> dict[str, Any]:
    """Run the selection for one source. Raises :class:`PartsListError` for a design that
    cannot be analysed."""
    catalogue = load_catalogue(db)
    settings_doc = resolve_settings(settings_doc)
    analysis = generic_analysis(
        parameters, mission, settings_doc, str(polar_cache_dir(app_settings))
    )
    if not analysis.get("valid"):
        failed = [c["message"] for c in analysis.get("checks", []) if c.get("level") == "fail"]
        raise PartsListError(
            "The design could not be analysed, so no parts can be chosen"
            + (": " + " ".join(failed[:3]) if failed else ".")
        )
    version_id = version.id if version else None
    locked = locked_choices(db, project.id, version_id)
    result = build_parts_list(
        parameters,
        mission,
        settings_doc,
        analysis,
        catalogue,
        locked=locked,
        mass_solver=_mass_solver(parameters, mission, settings_doc, analysis),
    )
    result["generated_at"] = _iso(utcnow())
    result["source"] = {
        "kind": "version" if version else "draft",
        "version_id": version_id,
        "version_number": version.number if version else None,
    }
    result["system_labels"] = SYSTEM_LABELS
    result["catalogue_size"] = len(catalogue)
    stored = {r.role: r for r in _rows(db, project.id, version_id)}
    in_sync = (
        bool(stored)
        and set(stored) == set(result["selections"])
        and all(
            stored[r].part_id == s["part_id"] and stored[r].quantity == s["quantity"]
            for r, s in result["selections"].items()
        )
    )
    result["stored"] = {
        "in_sync": in_sync,
        "stored_at": _iso(max(r.updated_at for r in stored.values())) if stored else None,
    }
    return result


def public(result: dict[str, Any]) -> dict[str, Any]:
    """The API shape (drops the engine-only ``analysis_parts``)."""
    return {k: v for k, v in result.items() if k != "analysis_parts"}


def analysis_parts_for(
    db: Session,
    app_settings: Settings,
    owner_id: int,
    project: Project,
    version: DesignVersion | None,
    parameters: dict[str, Any],
    mission: dict[str, Any],
    settings_doc: dict[str, Any],
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """(``parts`` for ``run_analysis``, short summary) for an analysis being queued, storing
    the picks; (None, None) when the catalogue is empty or the design cannot be analysed."""
    if db.scalar(select(Part.id).limit(1)) is None:
        return None, None
    try:
        result = compute(
            db, app_settings, owner_id, project, version, parameters, mission, settings_doc
        )
    except PartsListError:
        return None, None
    persist_selection(
        db, owner_id, project.id, version.id if version else None, result["selections"]
    )
    summary = {
        role: {"part_id": s["part_id"], "quantity": s["quantity"], "locked": s["locked"]}
        for role, s in result["selections"].items()
    }
    return result["analysis_parts"] or None, summary


def role_for(role: str) -> tuple[str, tuple[str, ...]]:
    return ROLE_LABELS[role], ROLE_CATEGORIES[role]


def assistant_summary(result: dict[str, Any]) -> dict[str, Any]:
    """Compact parts list for the assistant's ``get_parts_list`` tool."""
    roles = []
    for r in result["roles"]:
        roles.append(
            {
                "role": r["label"],
                "part": r["part"]["name"] if r["part"] else None,
                "quantity": r["quantity"],
                "line_mass_g": r["line_mass_g"],
                "line_price_eur": r["line_price_eur"],
                "locked_by_owner": r["locked"],
                "verified": r["part"]["verified"] if r["part"] else None,
                "why": " ".join(r["reasoning"][:2]),
                "flags": [f["message"] for f in r["flags"] if f["code"] != "unverified"],
                "not_filled": r["unfilled_reason"],
            }
        )
    t = result["totals"]
    return {
        "source": result["source"],
        "roles": roles,
        "consumables": {
            "mass_g": result["consumables"]["mass_g"],
            "cost_eur": result["consumables"]["cost_eur"],
        },
        "totals": {
            "parts_mass_g": t["mass_g"],
            "parts_mass_vs_statistical_estimate_g": t["mass_vs_tier1_g"],
            "takeoff_mass_with_parts_kg": t["takeoff_mass_kg"],
            "takeoff_mass_generic_parts_kg": t["takeoff_mass_generic_kg"],
            "estimated_endurance_min": t["estimated_endurance_min"],
            "endurance_note": t["endurance_note"],
            "cost_eur": t["cost_eur"],
            "budget_eur": t["budget_eur"],
            "budget_status": t["budget_status"],
            "budget_message": t["budget_message"],
        },
        "upgrades": [
            {
                "role": u["role_label"],
                "part": u["label"],
                "extra_cost_eur": u["extra_cost_eur"],
                "endurance_gain_min": u["endurance_gain_min"],
                "eur_per_min": u["eur_per_min"],
                "trade_off": u["trade_off"],
            }
            for u in result["upgrades"][:5]
        ],
        "uk_import_note": result["uk_import_note"],
        "note": "Every spec is unverified catalogue data until the owner checks it. Prices are "
        "the best Irish or UK listing found; endurance here is an estimate - quote the full "
        "analysis (get_latest_analysis) for performance.",
    }


__all__ = [
    "SELECTION_VERSION",
    "PartsListError",
    "analysis_parts_for",
    "assistant_summary",
    "clear_lock",
    "compute",
    "copy_selection",
    "generic_analysis",
    "load_catalogue",
    "locked_choices",
    "part_dict",
    "part_name",
    "persist_selection",
    "public",
    "selection_payload",
    "set_lock",
]

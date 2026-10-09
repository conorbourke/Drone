"""Owner settings: defaults merged with stored overrides, diff-only persistence."""

from __future__ import annotations

import copy
import logging
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.defaults import DEFAULT_SETTINGS, SETTINGS_META, SETTINGS_SCHEMA_VERSION
from app.deps import CurrentUser, DbSession, current_user
from app.models import AppSettings
from app.schemas.migrate import upgrade_settings
from app.schemas.settings import SettingsDocument, SettingsMeta, SettingsResponse

router = APIRouter(prefix="/api/settings", tags=["settings"], dependencies=[Depends(current_user)])
log = logging.getLogger("app.settings")


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Return ``base`` with ``override`` applied recursively (neither is mutated)."""
    out = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def diff_against(defaults: dict[str, Any], doc: dict[str, Any]) -> dict[str, Any]:
    """Only the leaves of ``doc`` that differ from ``defaults`` (nested dict, same shape)."""
    out: dict[str, Any] = {}
    for key, value in doc.items():
        default = defaults.get(key)
        if isinstance(value, dict) and isinstance(default, dict):
            sub = diff_against(default, value)
            if sub:
                out[key] = sub
        elif key not in defaults or value != default:
            out[key] = value
    return out


def flatten(doc: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in doc.items():
        path = f"{prefix}{key}"
        if isinstance(value, dict):
            out.update(flatten(value, f"{path}."))
        else:
            out[path] = value
    return out


def prune_unknown(defaults: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    """Drop override keys that no longer exist in the defaults (retired settings)."""
    out: dict[str, Any] = {}
    for key, value in overrides.items():
        if key not in defaults:
            continue
        default = defaults[key]
        if isinstance(value, dict) and isinstance(default, dict):
            sub = prune_unknown(default, value)
            if sub:
                out[key] = sub
        elif not isinstance(default, dict):
            out[key] = value
    return out


def _drop_path(overrides: dict[str, Any], path: list[str]) -> bool:
    """Remove ``path`` (a list of keys) from the nested overrides; True when something went."""
    if not path:
        return False
    node: Any = overrides
    for key in path[:-1]:
        node = node.get(key) if isinstance(node, dict) else None
        if not isinstance(node, dict):
            return False
    if isinstance(node, dict) and path[-1] in node:
        del node[path[-1]]
        return True
    return False


def resilient_merge(overrides: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Merge stored overrides over the defaults so that the result always validates.

    Defaults may change between phases while the owner's overrides stay stored. An override
    that no longer fits (an unknown key, or a value that now breaks an invariant) is dropped
    and reported, instead of making every GET /api/settings fail with a 500 the owner could
    never repair from the browser.
    """
    overrides = prune_unknown(DEFAULT_SETTINGS, copy.deepcopy(overrides))
    dropped: list[str] = []
    for _ in range(20):
        merged = deep_merge(DEFAULT_SETTINGS, overrides)
        try:
            SettingsDocument.model_validate(merged)
            return merged, dropped
        except ValidationError as exc:
            progressed = False
            for err in exc.errors(include_url=False):
                path = [p for p in err.get("loc", ()) if isinstance(p, str)]
                if path and _drop_path(overrides, path):
                    dropped.append(".".join(path))
                    progressed = True
            if not progressed:
                break
    dropped.append("*")
    return copy.deepcopy(DEFAULT_SETTINGS), dropped


def build_response(merged: dict[str, Any], warnings: list[str] | None = None) -> SettingsResponse:
    document = SettingsDocument.model_validate(merged)
    flat = flatten(document.model_dump(exclude={"schema_version"}))
    flat_defaults = flatten({k: v for k, v in DEFAULT_SETTINGS.items() if k != "schema_version"})
    meta = {
        path: SettingsMeta(
            label=SETTINGS_META.get(path, {}).get("label", path.rsplit(".", 1)[-1]),
            description=SETTINGS_META.get(path, {}).get("description", ""),
            source=SETTINGS_META.get(path, {}).get("source", ""),
            is_default=(flat_defaults.get(path) == value),
        )
        for path, value in flat.items()
    }
    return SettingsResponse(settings=document, meta=meta, warnings=warnings or [])


def _stored_overrides(db: Session, owner_id: int) -> tuple[AppSettings | None, dict[str, Any]]:
    row = db.scalar(select(AppSettings).where(AppSettings.owner_id == owner_id))
    if row is None:
        return None, {}
    doc = upgrade_settings(row.data)
    doc.pop("schema_version", None)
    # Only true overrides: an upgrade step fills new fields with their values at the time,
    # which must not pin them against later improvements of the defaults.
    return row, diff_against(DEFAULT_SETTINGS, doc)


def effective_settings(db: Session, owner_id: int) -> tuple[dict[str, Any], dict[str, Any]]:
    """The owner's settings document (defaults merged with stored overrides, current schema)
    and its ``meta`` (dotted path -> label, description, source, is_default), as plain dicts.
    The analysis engine reads thresholds from the first and names their sources from the
    second."""
    _, overrides = _stored_overrides(db, owner_id)
    merged, _dropped = resilient_merge(overrides)
    response = build_response(merged)
    return (
        response.settings.model_dump(),
        {path: meta.model_dump() for path, meta in response.meta.items()},
    )


@router.get("", response_model=SettingsResponse)
def get_settings(db: DbSession, user: CurrentUser) -> SettingsResponse:
    _, overrides = _stored_overrides(db, user.id)
    merged, dropped = resilient_merge(overrides)
    warnings: list[str] = []
    if dropped:
        log.warning("Stored settings no longer validate; reset to defaults: %s", dropped)
        if "*" in dropped:
            warnings.append(
                "Your saved settings no longer fit this version and were reset "
                "to the defaults. Review them and save again."
            )
        else:
            warnings.append(
                "Some saved values no longer fit this version and were reset to their "
                f"defaults: {', '.join(dropped)}. Review them and save again."
            )
    return build_response(merged, warnings)


@router.put("", response_model=SettingsResponse)
def put_settings(body: SettingsDocument, db: DbSession, user: CurrentUser) -> SettingsResponse:
    """Validate the whole document, store only what differs from the defaults."""
    full = body.model_dump()
    full.pop("schema_version", None)
    overrides = diff_against(DEFAULT_SETTINGS, full)
    stored = {"schema_version": SETTINGS_SCHEMA_VERSION, **overrides}
    row, _ = _stored_overrides(db, user.id)
    if row is None:
        row = AppSettings(owner_id=user.id, data=stored)
        db.add(row)
    else:
        row.data = stored
    db.commit()
    return build_response(deep_merge(DEFAULT_SETTINGS, overrides))

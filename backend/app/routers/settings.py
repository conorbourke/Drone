"""Owner settings: defaults merged with stored overrides, diff-only persistence."""

from __future__ import annotations

import copy
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import select

from app.defaults import DEFAULT_SETTINGS, SETTINGS_META, SETTINGS_SCHEMA_VERSION
from app.deps import CurrentUser, DbSession, current_user
from app.models import AppSettings
from app.schemas.migrate import upgrade_settings
from app.schemas.settings import SettingsDocument, SettingsMeta, SettingsResponse

router = APIRouter(prefix="/api/settings", tags=["settings"], dependencies=[Depends(current_user)])


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


def build_response(merged: dict[str, Any]) -> SettingsResponse:
    document = SettingsDocument.model_validate(merged)
    flat = flatten(document.model_dump(exclude={"schema_version"}))
    flat_defaults = flatten({k: v for k, v in DEFAULT_SETTINGS.items() if k != "schema_version"})
    meta = {
        path: SettingsMeta(
            description=SETTINGS_META.get(path, {}).get("description", ""),
            source=SETTINGS_META.get(path, {}).get("source", ""),
            is_default=(flat_defaults.get(path) == value),
        )
        for path, value in flat.items()
    }
    return SettingsResponse(settings=document, meta=meta)


def _stored_overrides(db: DbSession, owner_id: int) -> tuple[AppSettings | None, dict[str, Any]]:
    row = db.scalar(select(AppSettings).where(AppSettings.owner_id == owner_id))
    if row is None:
        return None, {}
    doc = upgrade_settings(row.data)
    doc.pop("schema_version", None)
    return row, doc


@router.get("", response_model=SettingsResponse)
def get_settings(db: DbSession, user: CurrentUser) -> SettingsResponse:
    _, overrides = _stored_overrides(db, user.id)
    return build_response(deep_merge(DEFAULT_SETTINGS, overrides))


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

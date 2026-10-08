"""Generate field metadata (labels, units, types, explanations) from Pydantic models.

Used by ``GET /api/schema/design``, ``GET /api/schema/mission`` and
``GET /api/parts/categories`` so that nothing is hand-copied into the frontend.
"""

from __future__ import annotations

import types
import typing
from typing import Any, Literal

from annotated_types import Ge, Gt, Le, Lt
from pydantic import BaseModel
from pydantic.fields import FieldInfo

# Unit derived from the field-name suffix when the field does not declare one explicitly.
UNIT_SUFFIXES: tuple[tuple[str, str], ...] = (
    ("_rpm_per_v", "rpm/V"),
    ("_s_per_60deg", "s per 60°"),
    ("_kg_cm", "kg·cm"),
    ("_km_los", "km"),
    ("_c_continuous", "C"),
    ("_c_burst", "C"),
    ("_per_m_g", "g/m"),
    ("_eur_estimate", "€"),
    ("_mah", "mAh"),
    ("_mhz", "MHz"),
    ("_kbps", "kbit/s"),
    ("_gpa", "GPa"),
    ("_mpa", "MPa"),
    ("_ohm", "Ω"),
    ("_rpm", "rpm"),
    ("_mps", "m/s"),
    ("_pct", "%"),
    ("_eur", "€"),
    ("_min", "min"),
    ("_deg", "°"),
    ("_mm", "mm"),
    ("_kg", "kg"),
    ("_wh", "Wh"),
    ("_hz", "Hz"),
    ("_g", "g"),
    ("_w", "W"),
    ("_a", "A"),
    ("_v", "V"),
)


def unit_for(name: str, explicit: str | None) -> str | None:
    if explicit is not None:
        return explicit or None
    for suffix, unit in UNIT_SUFFIXES:
        if name.endswith(suffix):
            return unit
    return None


def humanize(name: str) -> str:
    """``root_chord_mm`` -> ``Root chord``."""
    base = name
    for suffix, _ in UNIT_SUFFIXES:
        if base.endswith(suffix) and len(base) > len(suffix):
            base = base[: -len(suffix)]
            break
    words = base.replace("_", " ").strip()
    return words[:1].upper() + words[1:]


def _unwrap_optional(annotation: Any) -> tuple[Any, bool]:
    origin = typing.get_origin(annotation)
    if origin in (typing.Union, types.UnionType):
        args = [a for a in typing.get_args(annotation) if a is not type(None)]
        if len(args) == 1:
            return args[0], True
    return annotation, False


def _is_model(annotation: Any) -> bool:
    return isinstance(annotation, type) and issubclass(annotation, BaseModel)


def _json_type(annotation: Any) -> str:
    origin = typing.get_origin(annotation)
    if origin is Literal:
        return "string"
    if origin in (list, tuple, set) or annotation in (list, tuple, set):
        return "list"
    if origin is dict or annotation is dict:
        return "object"
    if annotation is bool:
        return "boolean"
    if annotation is int:
        return "integer"
    if annotation is float:
        return "number"
    if annotation is str:
        return "string"
    if _is_model(annotation):
        return "object"
    return "string"


def _bounds(info: FieldInfo) -> dict[str, float]:
    out: dict[str, float] = {}
    for meta in info.metadata:
        if isinstance(meta, Ge | Gt):
            out["min"] = float(meta.ge if isinstance(meta, Ge) else meta.gt)
        elif isinstance(meta, Le | Lt):
            out["max"] = float(meta.le if isinstance(meta, Le) else meta.lt)
    return out


def _enum(annotation: Any, extra: dict[str, Any]) -> list[dict[str, Any]] | None:
    if "enum" in extra:
        return [dict(e) for e in extra["enum"]]
    if typing.get_origin(annotation) is Literal:
        return [{"value": v, "label": humanize(str(v))} for v in typing.get_args(annotation)]
    return None


def _extra(info: FieldInfo) -> dict[str, Any]:
    extra = info.json_schema_extra
    return dict(extra) if isinstance(extra, dict) else {}


def field_meta(name: str, info: FieldInfo) -> dict[str, Any]:
    """Metadata for one scalar field."""
    annotation, _optional = _unwrap_optional(info.annotation)
    extra = _extra(info)
    meta: dict[str, Any] = {
        "label": extra.get("label") or humanize(name),
        "unit": unit_for(name, extra.get("unit")),
        "type": _json_type(annotation),
        "description": info.description or "",
    }
    meta.update(_bounds(info))
    enum = _enum(annotation, extra)
    if enum is not None:
        meta["enum"] = enum
    return meta


def document_schema(
    model: type[BaseModel], prefix: str = "", skip: frozenset[str] = frozenset({"schema_version"})
) -> dict[str, dict[str, Any]]:
    """Flatten a (nested) model into ``{"dotted.path": meta}`` for every scalar field."""
    out: dict[str, dict[str, Any]] = {}
    for name, info in model.model_fields.items():
        if name in skip:
            continue
        path = f"{prefix}{name}"
        annotation, _ = _unwrap_optional(info.annotation)
        if _is_model(annotation):
            out.update(document_schema(annotation, prefix=f"{path}.", skip=skip))
        else:
            out[path] = field_meta(name, info)
    return out


def field_list(model: type[BaseModel]) -> list[dict[str, Any]]:
    """Top-level fields of a flat model as a list (used for the parts category forms)."""
    fields: list[dict[str, Any]] = []
    for name, info in model.model_fields.items():
        meta = field_meta(name, info)
        fields.append(
            {
                "name": name,
                "label": meta["label"],
                "unit": meta["unit"],
                "type": meta["type"],
                "required": info.is_required(),
                "description": meta["description"],
                **({"enum": meta["enum"]} if "enum" in meta else {}),
                **({"min": meta["min"]} if "min" in meta else {}),
                **({"max": meta["max"]} if "max" in meta else {}),
            }
        )
    return fields

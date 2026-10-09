"""Turn Claude's proportions into a proposal in millimetres.

``proposal = {layout, layout_confidence, layout_reason, parameters: {dotted.path: {value, unit,
confidence, note}}, unmapped_notes, warnings}``. Ratios are scaled by the reference dimension
the owner entered, every value is clamped to a documented plausible range (see
``app.assistant.vision.MEASUREMENTS``), and parameters Claude could not see are omitted, so the
draft keeps its current value for them.
"""

from __future__ import annotations

import copy
from typing import Any

from pydantic import ValidationError

from app.assistant.vision import (
    CHOICES,
    FUSELAGE_TO_SPAN_RANGE,
    LAYOUTS,
    MEASUREMENTS,
    VisionAnswer,
)
from app.schemas.design import DesignParameters
from app.schemas.introspect import document_schema

LAYOUT_ALIASES: dict[str, str] = {
    "front_tilt": "front_tilt",
    "fronttilt": "front_tilt",
    "tilt_front": "front_tilt",
    "tiltrotor": "front_tilt",
    "tilt_rotor": "front_tilt",
    "rear_tilt": "rear_tilt",
    "reartilt": "rear_tilt",
    "tilt_rear": "rear_tilt",
    "quad_pusher": "quad_pusher",
    "quadpusher": "quad_pusher",
    "quad_plus_pusher": "quad_pusher",
    "quadplane": "quad_pusher",
    "quad_plane": "quad_pusher",
    "lift_cruise": "quad_pusher",
    "lift_and_cruise": "quad_pusher",
}

DESIGN_META = document_schema(DesignParameters)


def normalise_layout(value: str) -> str | None:
    key = value.strip().lower().replace("-", "_").replace(" ", "_").replace("+", "_plus_")
    key = "_".join(part for part in key.split("_") if part)
    return LAYOUT_ALIASES.get(key)


def _confidence(value: float) -> float:
    return round(min(1.0, max(0.0, value)), 2)


def _clamp(value: float, lo: float, hi: float) -> tuple[float, bool]:
    clamped = min(hi, max(lo, value))
    return clamped, clamped != value


def _label(path: str) -> str:
    return str(DESIGN_META.get(path, {}).get("label") or path)


def _fmt_ratio(value: float) -> str:
    return f"{value * 100:.1f} %"


def _get(doc: dict[str, Any], path: str) -> Any:
    node: Any = doc
    for part in path.split("."):
        node = node[part]
    return node


def _set(doc: dict[str, Any], path: str, value: Any) -> None:
    parts = path.split(".")
    node = doc
    for part in parts[:-1]:
        node = node.setdefault(part, {})
    node[parts[-1]] = value


def build_proposal(
    answer: VisionAnswer,
    reference_parameter: str,
    reference_mm: float,
    draft_parameters: dict[str, Any],
) -> dict[str, Any]:
    """Proposal from a validated answer. ``draft_parameters`` is the current draft (v2)."""
    warnings: list[str] = []
    unmapped: list[str] = []
    params: dict[str, dict[str, Any]] = {}

    def put(path: str, value: Any, confidence: float, note: str) -> None:
        params[path] = {
            "value": value,
            "unit": DESIGN_META.get(path, {}).get("unit"),
            "confidence": _confidence(confidence),
            "note": note.strip(),
        }

    # Layout -----------------------------------------------------------------------------
    layout = normalise_layout(answer.layout.value)
    layout_reason = answer.layout.reason.strip()
    layout_confidence = _confidence(answer.layout.confidence)
    if layout is None:
        layout = str(draft_parameters.get("layout", LAYOUTS[0]))
        layout_confidence = 0.0
        warnings.append(
            f"Claude suggested a layout the tool does not support ('{answer.layout.value}'); "
            "the current layout is kept."
        )
        if layout_reason:
            unmapped.append(f"Layout: {layout_reason}")

    # Scale: the reference dimension and the fuselage-to-span ratio ------------------------
    ratio = answer.fuselage_length_to_span
    fus_ratio, clamped = _clamp(ratio.value, *FUSELAGE_TO_SPAN_RANGE)
    if clamped:
        warnings.append(
            f"Fuselage length: Claude's estimate ({_fmt_ratio(ratio.value)} of the wingspan) "
            f"was outside the plausible range {_fmt_ratio(FUSELAGE_TO_SPAN_RANGE[0])} to "
            f"{_fmt_ratio(FUSELAGE_TO_SPAN_RANGE[1])}; clamped to {_fmt_ratio(fus_ratio)}."
        )
    if reference_parameter == "wing.span_mm":
        span_mm = reference_mm
        fuselage_mm = reference_mm * fus_ratio
        put("wing.span_mm", round(span_mm), 1.0, "The wingspan you entered.")
        put(
            "fuselage.length_mm",
            round(fuselage_mm),
            ratio.confidence,
            ratio.note or f"{_fmt_ratio(fus_ratio)} of the wingspan.",
        )
    else:
        fuselage_mm = reference_mm
        span_mm = reference_mm / fus_ratio
        put("fuselage.length_mm", round(fuselage_mm), 1.0, "The fuselage length you entered.")
        put(
            "wing.span_mm",
            round(span_mm),
            ratio.confidence,
            ratio.note or f"Fuselage length is {_fmt_ratio(fus_ratio)} of the wingspan.",
        )
    if ratio.confidence < 0.3:
        other = "fuselage length" if reference_parameter == "wing.span_mm" else "wingspan"
        warnings.append(
            f"Claude was unsure of the fuselage-to-wingspan ratio, so the {other} is a rough "
            "guess. Check it before applying."
        )

    # Measurements -------------------------------------------------------------------------
    seen: set[str] = set()
    for item in answer.measurements:
        spec = MEASUREMENTS.get(item.key)
        if spec is None:
            unmapped.append(f"{item.key}: {item.note}".strip())
            continue
        if item.key in seen:
            continue
        seen.add(item.key)
        value, clamped = _clamp(item.value, spec.lo, spec.hi)
        if spec.basis == "deg":
            mm_or_deg: float | int = round(value * 2) / 2
            shown = (f"{item.value:g}°", f"{spec.lo:g}° to {spec.hi:g}°", f"{mm_or_deg:g}°")
        elif spec.basis == "count":
            mm_or_deg = round(value)
            shown = (f"{item.value:g}", f"{spec.lo:g} to {spec.hi:g}", f"{mm_or_deg:g}")
        else:
            base = span_mm if spec.basis == "span" else fuselage_mm
            mm_or_deg = round(value * base)
            of = "wingspan" if spec.basis == "span" else "fuselage length"
            shown = (
                f"{_fmt_ratio(item.value)} of the {of}",
                f"{_fmt_ratio(spec.lo)} to {_fmt_ratio(spec.hi)}",
                f"{_fmt_ratio(value)}",
            )
        if clamped:
            warnings.append(
                f"{_label(item.key)}: Claude's estimate ({shown[0]}) was outside the plausible "
                f"range {shown[1]}; clamped to {shown[2]}."
            )
        put(item.key, mm_or_deg, item.confidence, item.note)

    # Choices ------------------------------------------------------------------------------
    for item in answer.choices:
        options = CHOICES.get(item.key)
        if options is None:
            unmapped.append(f"{item.key}: {item.value}. {item.note}".strip())
            continue
        value = item.value.strip().lower().replace("-", "_").replace(" ", "_")
        if value not in options:
            unmapped.append(f"{_label(item.key)}: {item.value}. {item.note}".strip())
            warnings.append(
                f"{_label(item.key)}: '{item.value}' is not an option the tool supports; "
                "the current value is kept."
            )
            continue
        put(item.key, value, item.confidence, item.note)

    unmapped.extend(f"Not visible: {text.strip()}" for text in answer.not_visible if text.strip())

    # Consistency --------------------------------------------------------------------------
    root = params.get("wing.root_chord_mm", {}).get(
        "value", _get(draft_parameters, "wing.root_chord_mm")
    )
    tip = params.get("wing.tip_chord_mm")
    if tip is not None and tip["value"] > root:
        tip["value"] = root
        warnings.append("Tip chord: the estimate was larger than the root chord; set equal to it.")
    front = params.get("motors.front_x_mm", {}).get(
        "value", _get(draft_parameters, "motors.front_x_mm")
    )
    rear = params.get("motors.rear_x_mm")
    if rear is not None and rear["value"] <= front:
        del params["motors.rear_x_mm"]
        warnings.append(
            "Rear motor position: the estimate was not behind the front motor, so it is left out."
        )

    merged = copy.deepcopy(draft_parameters)
    for path, entry in params.items():
        _set(merged, path, entry["value"])
    merged["layout"] = layout
    try:
        DesignParameters.model_validate(merged)
    except ValidationError as exc:
        messages = sorted({str(e["msg"]).removeprefix("Value error, ") for e in exc.errors()})
        warnings.append(
            "Applying every proposed value as it stands would not pass the design checks: "
            + " ".join(messages)
        )

    return {
        "layout": layout,
        "layout_confidence": layout_confidence,
        "layout_reason": layout_reason,
        "parameters": params,
        "unmapped_notes": unmapped,
        "warnings": warnings,
    }

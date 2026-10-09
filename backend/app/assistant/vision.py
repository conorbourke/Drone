"""Read reference images with Claude and return proportions, not millimetres.

The call follows the bundled Claude API guidance (official ``anthropic`` SDK, model
``claude-opus-5-5`` by default, adaptive thinking with an explicit effort, structured output via
``output_config.format``, server-side fallbacks on refusal, ``stop_reason`` checked before any
content is read, typed SDK errors caught most-specific first). Claude's answer is validated
again here and converted to a proposal in millimetres by :mod:`app.assistant.proposal`.

Test seam: outside production, ``CLAUDE_FAKE_RESPONSE_FILE`` makes :func:`read_images` return
that file instead of calling the API. The file holds either Claude's answer itself (the JSON
object described by :data:`ANSWER_SCHEMA`) or an envelope
``{"stop_reason": "end_turn", "output": {...}}`` /
``{"stop_reason": "refusal", "stop_details": {"category": "cyber"}}``.
"""

from __future__ import annotations

import base64
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import anthropic
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.config import Settings
from app.imaging import jpeg_for_claude

log = logging.getLogger("app.assistant.vision")

MISSING_KEY_MESSAGE = (
    "Add the ANTHROPIC_API_KEY secret in GitHub and redeploy to enable image reading"
)
FALLBACK_BETA = "server-side-fallback-2026-07-01"
EFFORT = "high"
MAX_TOKENS = 16000
REQUEST_TIMEOUT_S = 300.0

LAYOUTS = ("front_tilt", "rear_tilt", "quad_pusher")

# What Claude may estimate. Every length is a ratio: to the wingspan ("span") or to the
# fuselage length ("fuselage"); angles are degrees; counts are whole numbers. The server
# turns ratios into millimetres with the reference dimension and clamps to the plausible
# range (lo, hi) given in the same unit as Claude's value.
MeasureBasis = Literal["span", "fuselage", "deg", "count"]


@dataclass(frozen=True)
class MeasureSpec:
    basis: MeasureBasis
    lo: float
    hi: float
    meaning: str


MEASUREMENTS: dict[str, MeasureSpec] = {
    "wing.root_chord_mm": MeasureSpec(
        "span", 0.04, 0.25, "wing chord where it meets the fuselage, / wingspan"
    ),
    "wing.tip_chord_mm": MeasureSpec("span", 0.02, 0.25, "wing chord at the tip, / wingspan"),
    "wing.sweep_deg": MeasureSpec(
        "deg", -5.0, 35.0, "leading-edge sweep, degrees, positive swept back"
    ),
    "wing.dihedral_deg": MeasureSpec(
        "deg", -10.0, 15.0, "dihedral of each wing panel, degrees, positive tips up"
    ),
    "wing.x_le_mm": MeasureSpec(
        "fuselage", 0.05, 0.75, "nose tip to the wing root leading edge, / fuselage length"
    ),
    "fuselage.width_mm": MeasureSpec(
        "fuselage", 0.04, 0.30, "maximum fuselage width, / fuselage length"
    ),
    "fuselage.height_mm": MeasureSpec(
        "fuselage", 0.04, 0.30, "maximum fuselage height, / fuselage length"
    ),
    "booms.lateral_offset_mm": MeasureSpec(
        "span", 0.05, 0.40, "aircraft centreline to one motor boom, / wingspan"
    ),
    "booms.length_mm": MeasureSpec("span", 0.15, 0.90, "length of one motor boom, / wingspan"),
    "booms.x_offset_mm": MeasureSpec(
        "span",
        -0.50,
        0.20,
        "boom front relative to the wing root leading edge, / wingspan (negative = ahead)",
    ),
    "booms.diameter_mm": MeasureSpec("span", 0.005, 0.04, "boom tube diameter, / wingspan"),
    "motors.front_x_mm": MeasureSpec(
        "span", 0.0, 0.30, "boom front to the front lift motor, along the boom, / wingspan"
    ),
    "motors.rear_x_mm": MeasureSpec(
        "span", 0.10, 0.90, "boom front to the rear lift motor, along the boom, / wingspan"
    ),
    "tail.span_mm": MeasureSpec("span", 0.10, 0.60, "tail tip-to-tip width, / wingspan"),
    "tail.chord_mm": MeasureSpec("span", 0.03, 0.20, "tail surface chord, / wingspan"),
    "tail.arm_mm": MeasureSpec(
        "span", 0.15, 0.80, "wing quarter-chord to tail quarter-chord, / wingspan"
    ),
    "tail.height_mm": MeasureSpec(
        "span", 0.0, 0.30, "tail root height above the boom or fuselage centreline, / wingspan"
    ),
    "tail.v_angle_deg": MeasureSpec(
        "deg", 15.0, 60.0, "V or inverted-V tail: panel angle from horizontal, degrees"
    ),
    "propulsion.prop_diameter_mm": MeasureSpec(
        "span", 0.08, 0.35, "lift propeller diameter, / wingspan"
    ),
    "propulsion.prop_blades": MeasureSpec("count", 2, 6, "blades per lift propeller"),
    "pusher.prop_diameter_mm": MeasureSpec(
        "span", 0.06, 0.30, "pusher propeller diameter, / wingspan"
    ),
    "nose_bay.length_mm": MeasureSpec(
        "fuselage", 0.05, 0.40, "nose (camera) bay length, / fuselage length"
    ),
    "landing_gear.height_mm": MeasureSpec(
        "span", 0.02, 0.20, "ground clearance of the landing gear, / wingspan"
    ),
}
FUSELAGE_TO_SPAN_RANGE = (0.2, 1.5)

CHOICES: dict[str, tuple[str, ...]] = {
    "tail.type": ("conventional", "v_tail", "inverted_v", "twin_boom_h"),
    "fuselage.cross_section": ("ellipse", "rounded_rect"),
    "landing_gear.type": ("skids", "legs", "none"),
}


def _estimate_item(keys: list[str], value_type: str) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "key": {"type": "string", "enum": keys},
            "value": {"type": value_type},
            "confidence": {"type": "number"},
            "note": {"type": "string"},
        },
        "required": ["key", "value", "confidence", "note"],
        "additionalProperties": False,
    }


ANSWER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "layout": {
            "type": "object",
            "properties": {
                "value": {"type": "string", "enum": list(LAYOUTS)},
                "confidence": {"type": "number"},
                "reason": {"type": "string"},
            },
            "required": ["value", "confidence", "reason"],
            "additionalProperties": False,
        },
        "fuselage_length_to_span": {
            "type": "object",
            "properties": {
                "value": {"type": "number"},
                "confidence": {"type": "number"},
                "note": {"type": "string"},
            },
            "required": ["value", "confidence", "note"],
            "additionalProperties": False,
        },
        "measurements": {"type": "array", "items": _estimate_item(list(MEASUREMENTS), "number")},
        "choices": {"type": "array", "items": _estimate_item(list(CHOICES), "string")},
        "not_visible": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["layout", "fuselage_length_to_span", "measurements", "choices", "not_visible"],
    "additionalProperties": False,
}


# --- Server-side validation of Claude's answer (again, after the API's own schema check) ---


class _Strict(BaseModel):
    model_config = ConfigDict(extra="ignore", allow_inf_nan=False)


class LayoutAnswer(_Strict):
    value: str = Field(max_length=50)
    confidence: float
    reason: str = Field("", max_length=2000)


class RatioAnswer(_Strict):
    value: float
    confidence: float
    note: str = Field("", max_length=2000)


class MeasurementAnswer(_Strict):
    key: str = Field(max_length=100)
    value: float
    confidence: float
    note: str = Field("", max_length=2000)


class ChoiceAnswer(_Strict):
    key: str = Field(max_length=100)
    value: str = Field(max_length=100)
    confidence: float
    note: str = Field("", max_length=2000)


class VisionAnswer(_Strict):
    layout: LayoutAnswer
    fuselage_length_to_span: RatioAnswer
    measurements: list[MeasurementAnswer] = Field(default_factory=list, max_length=100)
    choices: list[ChoiceAnswer] = Field(default_factory=list, max_length=50)
    not_visible: list[str] = Field(default_factory=list, max_length=100)


# --- Results and errors ---


class VisionError(Exception):
    """Claude could not produce a reading. ``str(exc)`` is a plain message for the owner."""


class VisionNotConfigured(VisionError):
    """No API key and no test seam."""


@dataclass(frozen=True)
class VisionImage:
    view: str
    path: Path


@dataclass
class VisionResult:
    status: Literal["ok", "refused"]
    model: str
    answer: VisionAnswer | None = None
    refusal_category: str | None = None
    usage: dict[str, Any] = field(default_factory=dict)


def is_available(settings: Settings) -> bool:
    return settings.fake_claude_response_file is not None or _api_key(settings) is not None


def _api_key(settings: Settings) -> str | None:
    if settings.anthropic_api_key is None:
        return None
    key = settings.anthropic_api_key.get_secret_value().strip()
    return key or None


VIEW_LABELS = {
    "front": "front view",
    "side": "side view",
    "top": "top view (plan)",
    "three_quarter": "three-quarter view",
    "other": "other view",
}

SYSTEM_PROMPT = """\
You read reference pictures of a small fixed-wing VTOL drone for a design tool. The owner wants \
a starting model for their own design, so estimate the aircraft's proportions as well as the \
pictures allow. The tool converts your ratios to millimetres using one real dimension the \
owner knows; you never need absolute sizes.

The tool supports three layouts. Pick the nearest one even if the pictured aircraft differs:
- front_tilt: four lift motors on two booms; the front pair tilts forward for cruise.
- rear_tilt: as front_tilt, but the rear pair tilts (rarer).
- quad_pusher: four fixed upward-facing lift motors on two booms plus a separate pusher \
propeller for cruise.

Conventions: x runs from the nose tip backwards, y to the right wing, z up. Lengths are \
ratios: "/ wingspan" means divide by the tip-to-tip wingspan, "/ fuselage length" means divide \
by the nose-to-tail fuselage length. Angles are degrees.

Rules:
- Only report a measurement when you can see it in at least one picture. Leave out what is \
hidden, and list it in not_visible in a few words.
- Perspective distorts three-quarter views; prefer top, side and front views for lengths and \
lower your confidence when only a perspective view shows a dimension.
- confidence is 0 to 1: 0.9 for a clear plan-view measurement, 0.5 for a rough estimate, 0.2 \
for a guess.
- Each note is one short plain sentence saying what you measured or assumed.
- fuselage_length_to_span is always required; give your best estimate with an honest \
confidence.
"""


def _instructions(reference_parameter: str) -> str:
    known = "wingspan" if reference_parameter == "wing.span_mm" else "fuselage length"
    lines = [
        f"The owner knows the real {known}. Estimate these keys where visible:",
        *(f"- {key}: {spec.meaning}" for key, spec in MEASUREMENTS.items()),
        "Choices (value must be one of the listed options):",
        *(f"- {key}: {' | '.join(options)}" for key, options in CHOICES.items()),
        "Also give the nearest layout with a one-sentence reason, and fuselage_length_to_span.",
    ]
    return "\n".join(lines)


def _content(images: list[VisionImage], reference_parameter: str) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    for index, image in enumerate(images, start=1):
        label = VIEW_LABELS.get(image.view, "other view")
        blocks.append({"type": "text", "text": f"Image {index}: {label}."})
        data = base64.standard_b64encode(jpeg_for_claude(image.path)).decode("ascii")
        blocks.append(
            {
                "type": "image",
                "source": {"type": "base64", "media_type": "image/jpeg", "data": data},
            }
        )
    blocks.append({"type": "text", "text": _instructions(reference_parameter)})
    return blocks


def _validate(payload: Any) -> VisionAnswer:
    try:
        return VisionAnswer.model_validate(payload)
    except ValidationError:
        log.warning("Claude's answer did not match the expected shape")
        raise VisionError(
            "Claude's answer could not be read. Try again, or use clearer images."
        ) from None


def _fake(path: Path, model: str) -> VisionResult:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise VisionError("The fake Claude response file could not be read.") from None
    usage = {"input_tokens": 0, "output_tokens": 0, "fake": True}
    if isinstance(payload, dict) and "stop_reason" in payload:
        if payload["stop_reason"] == "refusal":
            details = payload.get("stop_details") or {}
            return VisionResult(
                "refused", model, refusal_category=details.get("category"), usage=usage
            )
        payload = payload.get("output")
    return VisionResult("ok", model, answer=_validate(payload), usage=usage)


def _send(client: anthropic.Anthropic, **kwargs: Any) -> Any:
    """The one place the API is called (tests replace it)."""
    return client.beta.messages.create(**kwargs)


def _usage(message: Any) -> dict[str, Any]:
    usage = getattr(message, "usage", None)
    out: dict[str, Any] = {
        "input_tokens": getattr(usage, "input_tokens", None),
        "output_tokens": getattr(usage, "output_tokens", None),
    }
    iterations = getattr(usage, "iterations", None) or []
    if any(getattr(entry, "type", None) == "fallback_message" for entry in iterations):
        out["served_by_fallback"] = True
    return out


def read_images(
    settings: Settings, images: list[VisionImage], reference_parameter: str
) -> VisionResult:
    """Ask Claude for the layout and proportions shown in ``images``.

    Raises :class:`VisionNotConfigured` without a key, :class:`VisionError` with a plain
    message for every other failure. A refusal is a result, not an error.
    """
    model = settings.claude_model
    fake = settings.fake_claude_response_file
    if fake is not None:
        return _fake(fake, model)
    key = _api_key(settings)
    if key is None:
        raise VisionNotConfigured(MISSING_KEY_MESSAGE)

    client = anthropic.Anthropic(api_key=key, timeout=REQUEST_TIMEOUT_S, max_retries=2)
    try:
        message = _send(
            client,
            model=model,
            max_tokens=MAX_TOKENS,
            thinking={"type": "adaptive"},
            output_config={
                "effort": EFFORT,
                "format": {"type": "json_schema", "schema": ANSWER_SCHEMA},
            },
            betas=[FALLBACK_BETA],
            fallbacks="default",
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": _content(images, reference_parameter)}],
        )
    except anthropic.AuthenticationError:
        raise VisionError(
            "Claude did not accept the API key. Check the ANTHROPIC_API_KEY secret and redeploy."
        ) from None
    except anthropic.PermissionDeniedError:
        raise VisionError(
            "The Claude API key is not allowed to use this model. Check the key's workspace."
        ) from None
    except anthropic.NotFoundError:
        raise VisionError(f"The Claude model '{model}' is not available to this API key.") from None
    except anthropic.BadRequestError as exc:
        log.warning("Claude rejected the request: request_id=%s", exc.request_id)
        raise VisionError(
            "Claude could not process these images. Try fewer or smaller images."
        ) from None
    except anthropic.RateLimitError:
        raise VisionError(
            "Claude is handling too many requests right now. Wait a minute and try again."
        ) from None
    except anthropic.APIStatusError as exc:
        log.warning("Claude API error: status=%s request_id=%s", exc.status_code, exc.request_id)
        if exc.status_code >= 500:
            raise VisionError(
                "Claude is temporarily unavailable. Try again in a few minutes."
            ) from None
        raise VisionError(f"Claude returned an error (HTTP {exc.status_code}).") from None
    except anthropic.APITimeoutError:
        raise VisionError("Claude took too long to answer. Try again with fewer images.") from None
    except anthropic.APIConnectionError:
        raise VisionError(
            "The server could not reach Claude. Try again in a few minutes."
        ) from None

    usage = _usage(message)
    served_by = getattr(message, "model", None) or model
    stop_reason = getattr(message, "stop_reason", None)
    if stop_reason == "refusal":
        details = getattr(message, "stop_details", None)
        return VisionResult(
            "refused",
            served_by,
            refusal_category=getattr(details, "category", None),
            usage=usage,
        )
    if stop_reason == "max_tokens":
        raise VisionError("Claude's answer was cut off. Try again with fewer images.")
    if stop_reason != "end_turn":
        log.warning("Unexpected stop_reason from Claude: %s", stop_reason)
        raise VisionError("Claude stopped before finishing its answer. Try again.")

    text = next(
        (block.text for block in message.content if getattr(block, "type", None) == "text"),
        None,
    )
    if text is None:
        raise VisionError("Claude's answer was empty. Try again.")
    try:
        payload = json.loads(text)
    except ValueError:
        raise VisionError(
            "Claude's answer could not be read. Try again, or use clearer images."
        ) from None
    return VisionResult("ok", served_by, answer=_validate(payload), usage=usage)

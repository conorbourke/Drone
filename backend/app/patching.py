"""Parameter patches: ``{dotted.path: value}`` applied to a design (and optionally its mission).

Used by "Try as new version" (``POST /api/projects/{id}/versions/from-patch``), by the
assistant's ``run_quick_analysis`` and ``propose_change`` tools, and matching the ``patch`` of
each engine recommendation. Paths name existing leaves of the design parameters document
(``wing.span_mm``); a ``mission.`` prefix names a mission field (``mission.cruise_speed_mps``).
The patched documents are validated with the same rules as a draft save.
"""

from __future__ import annotations

import copy
import math
from typing import Any

from pydantic import ValidationError

from app.schemas.design import DesignParameters
from app.schemas.mission import Mission

MAX_PATCH_ENTRIES = 40
MISSION_PREFIX = "mission."


class PatchError(ValueError):
    """The patch cannot be applied. ``str(exc)`` is a plain message."""


def _plain(exc: ValidationError) -> str:
    messages = []
    for err in exc.errors(include_url=False, include_input=False):
        loc = ".".join(str(p) for p in err.get("loc", ()) if isinstance(p, str | int))
        msg = str(err.get("msg", "")).removeprefix("Value error, ")
        messages.append(f"{loc}: {msg}" if loc else msg)
    return "; ".join(messages[:5])


def _set(doc: dict[str, Any], path: str, value: Any, label: str) -> None:
    keys = path.split(".")
    if not path or any(not k for k in keys) or keys[0] == "schema_version":
        raise PatchError(f"Unknown parameter '{label}'.")
    node: Any = doc
    for key in keys[:-1]:
        node = node.get(key) if isinstance(node, dict) else None
        if not isinstance(node, dict):
            raise PatchError(f"Unknown parameter '{label}'.")
    if not isinstance(node, dict) or keys[-1] not in node or isinstance(node[keys[-1]], dict):
        raise PatchError(f"Unknown parameter '{label}'.")
    if isinstance(value, bool) or value is None:
        if not isinstance(node[keys[-1]], bool):
            raise PatchError(f"'{label}' needs a number or a choice, not {value!r}.")
    elif isinstance(value, int | float):
        if not math.isfinite(float(value)):
            raise PatchError(f"'{label}' must be a finite number.")
    elif not isinstance(value, str):
        raise PatchError(f"'{label}' must be a number, a word or true/false.")
    node[keys[-1]] = value


def apply_patch(
    parameters: dict[str, Any], mission: dict[str, Any], patch: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return validated copies of ``parameters`` and ``mission`` with ``patch`` applied.

    Both inputs must already be at the current schema version (``current_parameters`` /
    ``current_mission``). Raises :class:`PatchError` with a plain message.
    """
    if not patch:
        raise PatchError("The change is empty: name at least one parameter.")
    if len(patch) > MAX_PATCH_ENTRIES:
        raise PatchError(f"At most {MAX_PATCH_ENTRIES} parameters can change at once.")
    p = copy.deepcopy(parameters)
    m = copy.deepcopy(mission)
    for path, value in patch.items():
        if not isinstance(path, str):
            raise PatchError("Parameter names must be text.")
        if path.startswith(MISSION_PREFIX):
            _set(m, path[len(MISSION_PREFIX) :], value, path)
        else:
            _set(p, path, value, path)
    try:
        p = DesignParameters.model_validate(p).model_dump()
    except ValidationError as exc:
        raise PatchError(f"The changed design is not valid: {_plain(exc)}") from None
    try:
        m = Mission.model_validate(m).model_dump()
    except ValidationError as exc:
        raise PatchError(f"The changed mission is not valid: {_plain(exc)}") from None
    return p, m


def changes_to_patch(changes: list[dict[str, Any]]) -> dict[str, Any]:
    """The assistant tools send ``[{path, value}]`` (strict tool schemas cannot describe a
    free-form map); turn that into a patch. Later entries win."""
    patch: dict[str, Any] = {}
    for item in changes:
        if not isinstance(item, dict) or not isinstance(item.get("path"), str):
            raise PatchError("Each change needs a 'path' and a 'value'.")
        patch[item["path"].strip()] = item.get("value")
    return patch

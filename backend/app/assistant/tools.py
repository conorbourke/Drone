"""The assistant's tools: definitions (strict JSON schemas) and their server-side execution.

Every tool is owner- and project-scoped: the executor is built for one signed-in owner and one
project, and never reads another project's rows. Results are compact JSON (keys sorted, numbers
rounded to four significant figures) so Claude can quote them with their ranges.

Strict tool use needs ``additionalProperties: false`` on every object, so a free-form
``{dotted.path: value}`` map cannot be described; the tools take a list of
``{path, value}`` pairs instead and the server turns it into a patch
(:func:`app.patching.changes_to_patch`).
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.jobs import polar_cache_dir
from app.models import Analysis, DesignVersion, Project
from app.patching import PatchError, apply_patch, changes_to_patch
from app.routers.common import current_mission, current_parameters
from app.routers.settings import effective_settings

MAX_SECTION_CHARS = 40_000

KEY_NUMBERS = (
    "takeoff_mass",
    "empty_mass",
    "endurance_cruise",
    "endurance_total",
    "range",
    "cruise_power",
    "hover_power",
    "stall_speed",
    "cruise_to_stall",
    "lift_to_drag",
    "static_margin_min_payload",
    "static_margin_max_payload",
    "hover_thrust_to_weight",
    "peak_current",
    "transition_min_margin",
)
SECTIONS = (
    "summary",
    "checks",
    "recommendations",
    "mass",
    "balance",
    "aero",
    "drag",
    "propulsion",
    "battery",
    "transition",
    "structure",
    "mission",
    "performance",
    "geometry",
    "tier1_comparison",
    "assumptions",
    "notes",
)

_CHANGE_ITEM: dict[str, Any] = {
    "type": "object",
    "properties": {
        "path": {
            "type": "string",
            "description": "Dotted parameter path, for example 'wing.span_mm'; mission fields "
            "take a 'mission.' prefix, for example 'mission.cruise_speed_mps'.",
        },
        "value": {
            "anyOf": [{"type": "number"}, {"type": "string"}, {"type": "boolean"}],
            "description": "New value in the parameter's own unit (mm, degrees, g, ...) or "
            "the option name for a choice.",
        },
    },
    "required": ["path", "value"],
    "additionalProperties": False,
}

_EMPTY: dict[str, Any] = {"type": "object", "properties": {}, "additionalProperties": False}

#: Tool definitions, in a fixed order (they render first in the prompt, under the cache).
TOOLS: list[dict[str, Any]] = [
    {
        "name": "get_design",
        "description": "Read the design parameters and the mission of the project's current "
        "draft or of a saved version. Call this before explaining or changing anything about "
        "the design, so you use the real values.",
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "source": {"type": "string", "enum": ["draft", "version"]},
                "version_number": {
                    "anyOf": [{"type": "integer"}, {"type": "null"}],
                    "description": "The version's number (v3 -> 3) when source is 'version', "
                    "else null.",
                },
            },
            "required": ["source", "version_number"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_latest_analysis",
        "description": "Read the newest finished full analysis of this project (AVL, XFOIL "
        "and the detailed models): headline numbers with ranges, the checks, and one more "
        "section on request. Call it when the owner asks about analysis results, checks or "
        "recommendations. It says whether the draft has changed since.",
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "section": {
                    "type": "string",
                    "enum": list(SECTIONS),
                    "description": "Extra section to include; 'summary' adds nothing extra.",
                }
            },
            "required": ["section"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_tier1_estimates",
        "description": "Quick estimates for the current draft (the fast analysis path: no new "
        "XFOIL runs), with headline numbers, ranges and checks, plus how the detailed model "
        "differs from the simple in-browser (Tier 1) estimates. Call it when no full "
        "analysis exists or the draft changed since the last one.",
        "strict": True,
        "input_schema": _EMPTY,
    },
    {
        "name": "get_parts_list",
        "description": "The parts selected or recommended for this design (motors, "
        "propellers, battery, ...). Call it before talking about specific parts.",
        "strict": True,
        "input_schema": _EMPTY,
    },
    {
        "name": "get_versions",
        "description": "List the project's saved versions with their notes and, where one "
        "exists, the key numbers of their latest analysis. Call it to compare versions.",
        "strict": True,
        "input_schema": _EMPTY,
    },
    {
        "name": "get_flight_comparisons",
        "description": "Measured flight data compared with the predictions. Call it when the "
        "owner asks how the real aircraft flew.",
        "strict": True,
        "input_schema": _EMPTY,
    },
    {
        "name": "run_quick_analysis",
        "description": "Run the fast analysis on the current draft with some parameters "
        "changed and return key numbers and checks before and after. Call it for every "
        "'what if' question and before proposing a change; never estimate the effect "
        "yourself.",
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "parameter_changes": {
                    "type": "array",
                    "items": _CHANGE_ITEM,
                    "description": "One entry per changed parameter.",
                }
            },
            "required": ["parameter_changes"],
            "additionalProperties": False,
        },
    },
    {
        "name": "propose_change",
        "description": "Offer the owner a concrete change to the design. The app shows it "
        "with a 'Try as new version' button; nothing changes until the owner presses it. "
        "Only propose changes you have checked with run_quick_analysis in this "
        "conversation, and describe them in your answer as well.",
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "summary": {
                    "type": "string",
                    "description": "One plain sentence: what changes and why, with the "
                    "quick-analysis effect it is based on.",
                },
                "changes": {"type": "array", "items": _CHANGE_ITEM},
            },
            "required": ["summary", "changes"],
            "additionalProperties": False,
        },
    },
]

TOOL_NAMES = tuple(t["name"] for t in TOOLS)

#: Plain labels for the UI while a tool runs.
TOOL_LABELS = {
    "get_design": "Reading the design…",
    "get_latest_analysis": "Reading the latest analysis…",
    "get_tier1_estimates": "Working out quick estimates…",
    "get_parts_list": "Checking the parts list…",
    "get_versions": "Looking at the saved versions…",
    "get_flight_comparisons": "Checking flight data…",
    "run_quick_analysis": "Running a quick analysis…",
    "propose_change": "Preparing a suggested change…",
}


# ---------------------------------------------------------------------------
# Compact JSON
# ---------------------------------------------------------------------------


def _round(x: Any) -> Any:
    if isinstance(x, bool) or x is None:
        return x
    if isinstance(x, float):
        if not math.isfinite(x):
            return None
        if x == 0:
            return 0.0
        return float(f"{x:.4g}")
    if isinstance(x, dict):
        return {k: _round(v) for k, v in x.items()}
    if isinstance(x, list | tuple):
        return [_round(v) for v in x]
    return x


def dumps(value: Any) -> str:
    return json.dumps(_round(value), sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def compact_quantity(q: Any) -> Any:
    if not isinstance(q, dict) or "value" not in q:
        return q
    return {k: q.get(k) for k in ("label", "value", "low", "high", "unit") if k in q}


def key_numbers(result: dict[str, Any]) -> dict[str, Any]:
    summary = result.get("summary") or {}
    return {k: compact_quantity(summary[k]) for k in KEY_NUMBERS if k in summary}


def compact_checks(result: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {k: c.get(k) for k in ("key", "label", "level", "message")}
        for c in result.get("checks") or []
    ]


def compact_recommendations(recs: dict[str, Any] | None) -> Any:
    if not recs:
        return {"note": "The recommendation sweep has not finished for this analysis."}
    return {
        "valid": recs.get("valid"),
        "message": recs.get("message"),
        "recommendations": [
            {
                "rank": r.get("rank"),
                "sentence": r.get("sentence"),
                "patch": r.get("patch"),
                "endurance_gain_min": r.get("endurance_gain_min"),
            }
            for r in recs.get("recommendations") or []
        ],
        "fixes": recs.get("fixes") or [],
    }


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------


@dataclass
class ToolOutcome:
    content: str
    is_error: bool = False
    proposal: dict[str, Any] | None = None
    data: Any = None  # the parsed result, for the scripted test seam's templates


@dataclass
class ToolContext:
    """Everything a tool may touch: one owner, one project."""

    session_factory: sessionmaker[Session]
    settings: Settings
    owner_id: int
    project_id: int
    _fast_cache: dict[str, Any] = field(default_factory=dict)

    # -- helpers ---------------------------------------------------------------------------

    def _project(self, db: Session) -> Project:
        project = db.get(Project, self.project_id)
        if project is None or project.owner_id != self.owner_id:
            raise ToolFailure("The project no longer exists.")
        return project

    def _fast(
        self, parameters: dict[str, Any], mission: dict[str, Any], db: Session
    ) -> dict[str, Any]:
        from app.engine.analysis import run_analysis

        settings_doc, meta = effective_settings(db, self.owner_id)
        key = dumps([parameters, mission, settings_doc])
        if key not in self._fast_cache:
            self._fast_cache[key] = run_analysis(
                parameters,
                mission,
                settings_doc,
                mode="fast",
                cache_dir=str(polar_cache_dir(self.settings)),
                settings_meta=meta,
            )
        return self._fast_cache[key]

    # -- dispatch --------------------------------------------------------------------------

    def execute(self, name: str, tool_input: Any) -> ToolOutcome:
        if name not in TOOL_NAMES:
            return ToolOutcome(f"Unknown tool '{name}'.", is_error=True)
        if not isinstance(tool_input, dict):
            return ToolOutcome("The tool input must be an object.", is_error=True)
        try:
            data, proposal = getattr(self, f"_tool_{name}")(tool_input)
        except ToolFailure as exc:
            return ToolOutcome(str(exc), is_error=True)
        except PatchError as exc:
            return ToolOutcome(str(exc), is_error=True)
        return ToolOutcome(dumps(data), proposal=proposal, data=_round(data))

    # -- tools -----------------------------------------------------------------------------

    def _tool_get_design(self, args: dict[str, Any]) -> tuple[Any, None]:
        with self.session_factory() as db:
            project = self._project(db)
            if args.get("source") == "version":
                number = args.get("version_number")
                if not isinstance(number, int):
                    raise ToolFailure("Give the version number (v3 -> 3).")
                version = db.scalar(
                    select(DesignVersion).where(
                        DesignVersion.project_id == project.id, DesignVersion.number == number
                    )
                )
                if version is None:
                    raise ToolFailure(f"There is no version {number} in this project.")
                return {
                    "source": "version",
                    "version": {"number": version.number, "name": version.name},
                    "notes": version.notes[:1000],
                    "parameters": current_parameters(version.parameters),
                    "mission": current_mission(version.mission),
                }, None
            based_on = None
            if project.draft_based_on_version_id is not None:
                v = db.get(DesignVersion, project.draft_based_on_version_id)
                based_on = v.number if v else None
            return {
                "source": "draft",
                "based_on_version_number": based_on,
                "parameters": current_parameters(project.draft_parameters),
                "mission": current_mission(project.draft_mission),
                "units_note": "Lengths mm, angles degrees, masses g, speeds m/s.",
            }, None

    def _tool_get_latest_analysis(self, args: dict[str, Any]) -> tuple[Any, None]:
        section = args.get("section", "summary")
        if section not in SECTIONS:
            raise ToolFailure(f"Unknown section '{section}'.")
        with self.session_factory() as db:
            project = self._project(db)
            row = db.scalar(
                select(Analysis)
                .where(
                    Analysis.project_id == project.id,
                    Analysis.kind == "full",
                    Analysis.status.in_(("done", "running")),
                    Analysis.result.is_not(None),
                )
                .order_by(Analysis.id.desc())
            )
            if row is None:
                return {
                    "available": False,
                    "note": "No full analysis has been run for this project yet. Suggest the "
                    "owner presses Analyse on the Design tab, or use get_tier1_estimates.",
                }, None
            result = row.result or {}
            version_number = None
            if row.version_id is not None:
                v = db.get(DesignVersion, row.version_id)
                version_number = v.number if v else None
            draft_changed = None
            if row.version_id is None:
                draft_changed = current_parameters(project.draft_parameters) != row.inputs.get(
                    "parameters"
                ) or current_mission(project.draft_mission) != row.inputs.get("mission")
        out: dict[str, Any] = {
            "available": True,
            "analysis_id": row.id,
            "status": row.status,
            "source": "version" if row.version_id is not None else "draft",
            "version_number": version_number,
            "finished_at": row.finished_at.isoformat() if row.finished_at else None,
            "draft_changed_since": draft_changed,
            "valid": result.get("valid"),
            "key_numbers": key_numbers(result),
            "checks": compact_checks(result),
            "a3_note": result.get("a3_note"),
        }
        if section == "recommendations":
            out["recommendations"] = compact_recommendations(result.get("recommendations"))
        elif section not in ("summary", "checks"):
            text = json.dumps(result.get(section), default=str)
            if len(text) > MAX_SECTION_CHARS:
                out[section] = {
                    "truncated": True,
                    "note": f"The {section} section is large; this is its first part.",
                    "text": text[:MAX_SECTION_CHARS],
                }
            else:
                out[section] = result.get(section)
        return out, None

    def _tool_get_tier1_estimates(self, args: dict[str, Any]) -> tuple[Any, None]:
        with self.session_factory() as db:
            project = self._project(db)
            parameters = current_parameters(project.draft_parameters)
            mission = current_mission(project.draft_mission)
            result = self._fast(parameters, mission, db)
        if not result.get("valid"):
            return {"valid": False, "checks": compact_checks(result)}, None
        return {
            "valid": True,
            "method": "Fast analysis path of the detailed engine on the current draft "
            "(cached or tabulated airfoil polars, no new XFOIL runs).",
            "key_numbers": key_numbers(result),
            "checks": compact_checks(result),
            "tier1_vs_detailed": result.get("tier1_comparison"),
            "a3_note": result.get("a3_note"),
        }, None

    def _tool_get_parts_list(self, args: dict[str, Any]) -> tuple[Any, None]:
        return {
            "parts": [],
            "note": "Part selection arrives in Phase 4. Until then the engine sizes generic "
            "motors, propellers and a battery from the design and reports them as assumed; "
            "do not name specific products.",
        }, None

    def _tool_get_versions(self, args: dict[str, Any]) -> tuple[Any, None]:
        with self.session_factory() as db:
            project = self._project(db)
            versions = db.scalars(
                select(DesignVersion)
                .where(DesignVersion.project_id == project.id)
                .order_by(DesignVersion.number.desc())
            ).all()
            numbers = {v.id: v.number for v in versions}
            items = []
            for v in versions:
                row = db.scalar(
                    select(Analysis)
                    .where(
                        Analysis.version_id == v.id,
                        Analysis.kind == "full",
                        Analysis.status == "done",
                    )
                    .order_by(Analysis.id.desc())
                )
                items.append(
                    {
                        "number": v.number,
                        "name": v.name,
                        "notes": v.notes[:300],
                        "parent_number": numbers.get(v.parent_version_id or 0),
                        "created_at": v.created_at.isoformat(),
                        "latest_analysis": (
                            {"analysis_id": row.id, "key_numbers": key_numbers(row.result)}
                            if row is not None and row.result
                            else None
                        ),
                    }
                )
        return {
            "versions": items,
            "note": "Versions without an analysis have no numbers yet; run_quick_analysis "
            "works on the draft only.",
        }, None

    def _tool_get_flight_comparisons(self, args: dict[str, Any]) -> tuple[Any, None]:
        return {
            "flights": [],
            "note": "Flight logs and comparisons with the predictions arrive in Phase 6. "
            "No flight data exists yet.",
        }, None

    def _tool_run_quick_analysis(self, args: dict[str, Any]) -> tuple[Any, None]:
        patch = changes_to_patch(args.get("parameter_changes") or [])
        with self.session_factory() as db:
            project = self._project(db)
            parameters = current_parameters(project.draft_parameters)
            mission = current_mission(project.draft_mission)
            new_p, new_m = apply_patch(parameters, mission, patch)
            before = self._fast(parameters, mission, db)
            after = self._fast(new_p, new_m, db)
        if not before.get("valid") or not after.get("valid"):
            return {
                "valid": False,
                "changes": patch,
                "checks_before": compact_checks(before),
                "checks_after": compact_checks(after),
            }, None
        kb, ka = key_numbers(before), key_numbers(after)
        differences = {
            k: {
                "before": kb[k]["value"],
                "after": ka[k]["value"],
                "change": ka[k]["value"] - kb[k]["value"],
                "unit": ka[k].get("unit"),
            }
            for k in ka
            if k in kb
            and isinstance(ka[k].get("value"), int | float)
            and isinstance(kb[k].get("value"), int | float)
        }
        levels_b = {c["key"]: c["level"] for c in compact_checks(before)}
        checks_changed = [
            {**c, "level_before": levels_b.get(c["key"])}
            for c in compact_checks(after)
            if levels_b.get(c["key"]) != c["level"]
        ]
        return {
            "valid": True,
            "method": "Fast analysis of the current draft and of the draft with these changes "
            "(AVL, cached or tabulated polars, drag, propulsion, battery, transition, "
            "structure, mass). A full analysis can differ slightly.",
            "changes": {
                path: {"from": _get(parameters, mission, path), "to": value}
                for path, value in patch.items()
            },
            "before": {"key_numbers": kb, "checks": compact_checks(before)},
            "after": {"key_numbers": ka, "checks": compact_checks(after)},
            "differences": differences,
            "checks_changed": checks_changed,
            "a3_note": after.get("a3_note"),
        }, None

    def _tool_propose_change(self, args: dict[str, Any]) -> tuple[Any, dict[str, Any]]:
        patch = changes_to_patch(args.get("changes") or [])
        summary = str(args.get("summary") or "").strip()[:1000]
        if not summary:
            raise ToolFailure("Give a one-sentence summary of the change.")
        with self.session_factory() as db:
            project = self._project(db)
            apply_patch(
                current_parameters(project.draft_parameters),
                current_mission(project.draft_mission),
                patch,
            )
        return (
            {
                "shown": True,
                "note": "The owner sees the change with a 'Try as new version' button.",
            },
            {"patch": patch, "summary": summary, "base": "draft"},
        )


class ToolFailure(Exception):
    """A tool could not answer; ``str(exc)`` goes back to Claude as an error result."""


def _get(parameters: dict[str, Any], mission: dict[str, Any], path: str) -> Any:
    node: Any = mission if path.startswith("mission.") else parameters
    for key in path.removeprefix("mission.").split("."):
        node = node.get(key) if isinstance(node, dict) else None
    return node

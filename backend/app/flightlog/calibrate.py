"""Calibration factors from one or more flight comparisons and the built weights.

Factors (each a multiplier on the model's prediction, 1.0 = the model was right):

* ``hover_power``: measured steady hover power / predicted at the weighed mass.
* ``cruise_power``: measured steady cruise power / predicted at the measured airspeed and mass.
* ``cruise_drag``: the drag multiplier that reproduces the measured cruise power through the
  analysis' propeller and motor model, i.e. the cruise power ratio corrected for propulsive
  efficiency (more thrust moves the propeller to another efficiency).
* ``battery_usable_energy``: energy delivered per charge against the design pack's model over
  the same state-of-charge window.
* ``structural_mass``: weighed / predicted mass of the structure groups (built weights; no log).

Several logs are combined by inverse-variance weighting, x̄ = Σ wᵢxᵢ / Σ wᵢ with wᵢ = 1/σᵢ².
The stated uncertainty is the larger of the propagated one, √(1/Σ wᵢ), and the scatter between
logs, √(Σ wᵢ(xᵢ - x̄)² / ((n - 1) Σ wᵢ)), so logs that disagree widen the band instead of
narrowing it (Birge ratio check; Taylor, *An Introduction to Error Analysis*, ch. 7).
"""

from __future__ import annotations

import math
from typing import Any

SCHEMA = "flightlog-calibration/1"

FACTOR_INFO = {
    "hover_power": (
        "Hover power factor",
        "Multiplies the predicted hover power (propeller figure of merit, download and motor "
        "losses together).",
    ),
    "cruise_power": (
        "Cruise power factor",
        "Measured cruise power over the predicted one at the same airspeed and mass.",
    ),
    "cruise_drag": (
        "Cruise drag factor",
        "Multiplies the predicted cruise drag; the propeller and motor are re-solved, so this is "
        "the power ratio corrected for propulsive efficiency.",
    ),
    "battery_usable_energy": (
        "Battery usable-energy factor",
        "Multiplies the design pack's usable energy (energy per charge against the cell model).",
    ),
    "structural_mass": (
        "Structural mass factor",
        "Weighed structure over the predicted structure (printed shell, wing, tail, booms).",
    ),
}


def combine(values: list[tuple[float, float]]) -> dict[str, float]:
    """Inverse-variance weighted mean of ``(value, sigma)`` pairs with a scatter check."""
    pts = [(x, s) for x, s in values if x is not None and math.isfinite(x) and s and s > 0]
    if not pts:
        return {}
    w = [1 / (s * s) for _, s in pts]
    sw = sum(w)
    mean = sum(wi * x for wi, (x, _) in zip(w, pts, strict=True)) / sw
    internal = math.sqrt(1 / sw)
    if len(pts) > 1:
        chi = sum(wi * (x - mean) ** 2 for wi, (x, _) in zip(w, pts, strict=True))
        external = math.sqrt(chi / ((len(pts) - 1) * sw))
        birge = external / internal
    else:
        external, birge = 0.0, 1.0
    return {
        "value": mean,
        "uncertainty": max(internal, external),
        "internal_uncertainty": internal,
        "scatter_uncertainty": external,
        "birge_ratio": birge,
    }


def structural_factor(built_weights: list[dict[str, Any]]) -> dict[str, Any]:
    """``built_weights``: ``[{group, predicted_g, measured_g, uncertainty_g?}]`` per component.

    Groups are summed (printed shell, wing, tail, booms, ...); the factor per group is
    measured/predicted, the overall factor is Σmeasured/Σpredicted over structure groups.
    Weighing uncertainty defaults to 1 g + 1 % per item (kitchen scale, estimate)."""
    groups: dict[str, dict[str, float]] = {}
    for item in built_weights:
        g = str(item.get("group") or "other")
        pred = float(item.get("predicted_g") or 0)
        meas = item.get("measured_g")
        if meas is None or pred <= 0:
            continue
        meas = float(meas)
        u = float(item.get("uncertainty_g") or (1.0 + 0.01 * meas))
        d = groups.setdefault(g, {"predicted_g": 0.0, "measured_g": 0.0, "var": 0.0, "items": 0})
        d["predicted_g"] += pred
        d["measured_g"] += meas
        d["var"] += u * u
        d["items"] += 1
    if not groups:
        return {"name": "structural_mass", "valid": False, "reason": "No built weights entered."}
    per_group = {}
    for g, d in groups.items():
        f = d["measured_g"] / d["predicted_g"]
        per_group[g] = {
            "predicted_g": round(d["predicted_g"], 1),
            "measured_g": round(d["measured_g"], 1),
            "factor": f,
            "uncertainty": math.sqrt(d["var"]) / d["predicted_g"],
            "items": int(d["items"]),
        }
    tp = sum(d["predicted_g"] for d in groups.values())
    tm = sum(d["measured_g"] for d in groups.values())
    tv = sum(d["var"] for d in groups.values())
    label, explain = FACTOR_INFO["structural_mass"]
    return {
        "name": "structural_mass",
        "label": label,
        "explain": explain,
        "valid": True,
        "value": tm / tp,
        "uncertainty": math.sqrt(tv) / tp,
        "n_logs": 0,
        "source_logs": [],
        "per_group": per_group,
        "method": "Σ weighed / Σ predicted over the entered structure items; weighing "
        "uncertainty 1 g + 1 % per item unless given.",
        "source": "built weights",
    }


def derive_calibration(
    comparisons: list[dict[str, Any]],
    *,
    built_weights: list[dict[str, Any]] | None = None,
    log_ids: list[Any] | None = None,
) -> dict[str, Any]:
    """Combine ``compare_log`` results (one per log) into calibration factors.

    Returns ``{schema, n_logs, factors: {name: {name, label, explain, valid, value,
    uncertainty, n_logs, source_logs, per_log: [{log, value, uncertainty, weight}],
    internal_uncertainty, scatter_uncertainty, birge_ratio, method}}, notes}``."""
    notes: list[str] = []
    ids = log_ids or [c.get("log_id", c.get("log", i)) for i, c in enumerate(comparisons)]
    factors: dict[str, Any] = {}
    for name in ("hover_power", "cruise_power", "cruise_drag", "battery_usable_energy"):
        per_log = []
        pts = []
        reasons = []
        for lid, comp in zip(ids, comparisons, strict=True):
            f = (comp.get("factors") or {}).get(name) or {}
            if not f.get("valid"):
                if f.get("reason"):
                    reasons.append(f"{lid}: {f['reason']}")
                continue
            per_log.append(
                {
                    "log": lid,
                    "value": f["value"],
                    "uncertainty": f["uncertainty"],
                    "basis": f.get("basis"),
                }
            )
            pts.append((f["value"], f["uncertainty"]))
        label, explain = FACTOR_INFO[name]
        comb = combine(pts)
        if not comb:
            factors[name] = {
                "name": name,
                "label": label,
                "explain": explain,
                "valid": False,
                "n_logs": 0,
                "source_logs": [],
                "per_log": [],
                "reason": "; ".join(reasons) or "No log had a usable phase for this factor.",
            }
            continue
        if comb["birge_ratio"] > 2:
            notes.append(
                f"{label}: the logs disagree more than their own uncertainties allow (Birge "
                f"ratio {comb['birge_ratio']:.1f}); the stated uncertainty uses the scatter."
            )
        factors[name] = {
            "name": name,
            "label": label,
            "explain": explain,
            "valid": True,
            **comb,
            "n_logs": len(per_log),
            "source_logs": [p["log"] for p in per_log],
            "per_log": per_log,
            "method": "Inverse-variance weighted mean; uncertainty = max(propagated, scatter).",
        }
    factors["structural_mass"] = (
        structural_factor(built_weights)
        if built_weights
        else {
            "name": "structural_mass",
            "label": FACTOR_INFO["structural_mass"][0],
            "explain": FACTOR_INFO["structural_mass"][1],
            "valid": False,
            "n_logs": 0,
            "source_logs": [],
            "reason": "No built weights entered.",
        }
    )
    return {
        "schema": SCHEMA,
        "n_logs": len(comparisons),
        "factors": factors,
        "notes": notes,
        "method": __doc__.strip() if __doc__ else "",
    }

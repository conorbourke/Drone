"""Shared fixtures for the flight-data tests: one fast analysis of the default design and
synthetic logs of it with known perturbation factors."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest

from app.defaults import DEFAULT_DESIGN_PARAMETERS, DEFAULT_MISSION, DEFAULT_SETTINGS
from app.engine.analysis import run_analysis
from app.flightlog import process_log
from app.flightlog.synth import synthesize_from_analysis

LOGS = Path(__file__).resolve().parents[1] / "fixtures" / "logs"
HOVER_FACTOR = 0.95
CRUISE_FACTOR = 1.12


@pytest.fixture(scope="session")
def analysis(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    cache = Path(tmp_path_factory.getbasetemp()) / "polar-cache"
    cache.mkdir(exist_ok=True)
    r = run_analysis(
        copy.deepcopy(DEFAULT_DESIGN_PARAMETERS),
        copy.deepcopy(DEFAULT_MISSION),
        copy.deepcopy(DEFAULT_SETTINGS),
        mode="fast",
        cache_dir=str(cache),
        uncertainty=True,
    )
    assert r["valid"]
    return r


@pytest.fixture(scope="session")
def synthetic_logs(
    analysis: dict[str, Any], tmp_path_factory: pytest.TempPathFactory
) -> list[dict[str, Any]]:
    """Three flights with cruise +12 %, hover -5 %, different noise, speeds and masses."""
    out = []
    d = tmp_path_factory.mktemp("synthetic-logs")
    cases = [
        {"seed": 1, "noise": 0.03, "mass_kg": 3.30, "airspeed_mps": 16.0, "cruise_s": 150.0},
        {"seed": 2, "noise": 0.05, "mass_kg": 3.45, "airspeed_mps": 17.0, "cruise_s": 120.0},
        {"seed": 3, "noise": 0.02, "mass_kg": 3.20, "airspeed_mps": 15.0, "cruise_s": 180.0},
    ]
    for c in cases:
        path = d / f"synthetic-{c['seed']}.bin"
        truth = synthesize_from_analysis(
            str(path),
            analysis,
            hover_factor=HOVER_FACTOR,
            cruise_factor=CRUISE_FACTOR,
            **c,
        )
        out.append({"path": path, "truth": truth, "processed": process_log(path)})
    return out

"""Regenerate the synthetic QuadPlane fixture logs (run from backend/):

    uv run python tests/fixtures/logs/make_synthetic.py

Writes ``synthetic_quadplane.bin`` (the default design flown with hover power -5 % and cruise
power +12 % against the fast analysis, 3 % noise) and ``synthetic_quadplane.json`` (the truth).
"""

from __future__ import annotations

import copy
import json
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2]))

from app.defaults import DEFAULT_DESIGN_PARAMETERS, DEFAULT_MISSION, DEFAULT_SETTINGS  # noqa: E402
from app.engine.analysis import run_analysis  # noqa: E402
from app.flightlog.synth import synthesize_from_analysis  # noqa: E402


def main() -> None:
    with tempfile.TemporaryDirectory() as cache:
        result = run_analysis(
            copy.deepcopy(DEFAULT_DESIGN_PARAMETERS),
            copy.deepcopy(DEFAULT_MISSION),
            copy.deepcopy(DEFAULT_SETTINGS),
            mode="fast",
            cache_dir=cache,
        )
    truth = synthesize_from_analysis(
        str(HERE / "synthetic_quadplane.bin"),
        result,
        mass_kg=3.35,
        airspeed_mps=16.0,
        hover_factor=0.95,
        cruise_factor=1.12,
        noise=0.03,
        cruise_s=120.0,
        seed=7,
    )
    truth["design"] = "default design (app/defaults.py), run_analysis(mode='fast')"
    truth["phases"] = {k: [round(a, 2), round(b, 2)] for k, (a, b) in truth["phases"].items()}
    (HERE / "synthetic_quadplane.json").write_text(json.dumps(truth, indent=1) + "\n")
    print(json.dumps({k: truth[k] for k in ("hover_factor", "cruise_factor", "mass_kg")}))


if __name__ == "__main__":
    main()

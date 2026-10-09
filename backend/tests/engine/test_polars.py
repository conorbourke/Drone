"""Polars: XFOIL at the operating Re with a disk cache; table fallback; strip integration."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.engine.polars import PolarStore, round_re, strip_profile_drag


def test_round_re() -> None:
    assert round_re(196_400) == 200_000
    assert round_re(143_000) == 140_000
    assert round_re(1_234_567) == 1_200_000


def test_xfoil_runs_once_then_cache(polar_cache: str) -> None:
    store = PolarStore(polar_cache, ncrit=9.0)
    pol = store.get("sd7037", 240_000)
    path = Path(polar_cache) / "sd7037-240000-9.json"
    assert path.is_file(), "cache file named {airfoil}-{re_rounded}-{ncrit}.json"
    raw = json.loads(path.read_text())
    assert raw["converged_points"] >= 15
    assert pol.source == "xfoil"
    assert 1.1 < pol.cl_max < 1.5
    assert 0.006 < pol.summary["cd_min"] < 0.012
    again = PolarStore(polar_cache, ncrit=9.0)
    pol2 = again.get("sd7037", 241_000)
    assert again.log == []  # loaded from disk, no XFOIL run
    assert pol2.cd_at_cl(0.5)[0] == pytest.approx(pol.cd_at_cl(0.5)[0])


def test_fallback_to_table_when_xfoil_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = PolarStore(tmp_path, ncrit=9.0)
    monkeypatch.setattr(store, "_run_xfoil", lambda *args: None)
    pol = store.get("e387", 300_000)
    assert pol.source == "table"
    assert "did not converge" in pol.note
    cd, outside = pol.cd_at_cl(0.6)
    assert 0.005 < cd < 0.02 and not outside


def test_fast_path_never_runs_xfoil(tmp_path: Path) -> None:
    store = PolarStore(tmp_path, allow_xfoil=False)
    pol = store.get("mh32", 180_000)
    assert pol.source == "table"
    assert "fast path" in pol.note
    assert store.log == []
    generic = store.get("not-an-airfoil", 180_000)
    assert generic.source == "generic"


def test_strip_integration_matches_constant_polar(tmp_path: Path) -> None:
    store = PolarStore(tmp_path, allow_xfoil=False)
    pol = store.get("sd7037", 200_000)
    strips = [{"chord": 0.2, "width": 0.1, "cl": 0.5, "y": 0.05 + 0.1 * i} for i in range(10)]
    out = strip_profile_drag(strips, [pol], speed=200_000 * 1.789e-5 / (1.225 * 0.2), s_ref=0.2)
    assert out["cd"] == pytest.approx(pol.cd_at_cl(0.5)[0], rel=1e-6)
    assert out["strips_outside_polar"] == 0

"""Parsing: pymavlink compatibility (ArduPlane 3.8 test.BIN), the sample log, missing messages,
decimation and bounded memory."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from app.flightlog import process_log
from app.flightlog.parse import BUCKET_S, parse_dataflash
from app.flightlog.synth import SynthFlight, synthesize_quadplane_log
from tests.flightlog.conftest import LOGS

BACKEND = Path(__file__).resolve().parents[2]


def test_pymavlink_test_bin_compatibility() -> None:
    r = process_log(LOGS / "test.BIN")
    json.dumps(r, allow_nan=False)
    assert r["firmware"].startswith("ArduPlane V3.8")
    assert r["vehicle"]["type"] == "plane"
    assert r["vehicle"]["configuration"] == "fixed wing (no QuadPlane)"
    assert r["messages"]["BAT"] == 8 and r["messages"]["PARM"] == 809
    assert 0.9 < r["duration_s"] < 1.1
    assert r["start_time_utc"].startswith("2017-10-29")
    # one second on the ground: no flight, but the whole-log statistics exist
    assert r["phases"] == [] and r["flight"] is None
    assert any("No flight found" in n for n in r["phase_detection"].get("notes", []) + r["notes"])
    assert 12.3 < r["whole_log"]["voltage_v"]["mean"] < 12.5
    assert {m["type"] for m in r["missing"]} >= {"ARSP", "QTUN"}


def test_series_are_decimated_to_5_hz(synthetic_logs: list[dict[str, Any]]) -> None:
    r = synthetic_logs[0]["processed"]
    s = r["series"]
    assert s["rate_hz"] == 1 / BUCKET_S == 5
    n = len(s["t_s"])
    assert abs(n - r["duration_s"] * 5) <= 2
    for name, ch in s["channels"].items():
        assert len(ch["values"]) == n, name
    # ATT is written at 25 Hz in the log, the series holds 5 values per second
    assert r["messages"]["ATT"] > 4 * n
    assert {"power_w", "current_a", "voltage_v", "airspeed_mps", "alt_m", "vibe_z"} <= set(
        s["channels"]
    )


def test_robust_to_missing_messages(tmp_path: Path, analysis: dict[str, Any]) -> None:
    """No RCOU, ARSP, MSG, ARM/EV or POS: phases still come out from the fallbacks."""
    path = tmp_path / "sparse.bin"
    synthesize_quadplane_log(
        str(path),
        SynthFlight(
            hover_power_w=400,
            cruise_power_w=220,
            cruise_s=90,
            omit=("RCOU", "ARSP", "MSG", "EV", "ARM", "POS", "ESC", "VIBE", "VER"),
        ),
    )
    r = process_log(path)
    sig = r["phase_detection"]["signals"]
    assert sig["lift"].startswith("QTUN")
    assert "CTUN" in sig["airspeed"]
    assert "whole log is treated as armed" in sig["armed"]
    assert sig["altitude"].startswith("BARO")
    keys = [p["key"] for p in r["phases"]]
    assert keys == ["takeoff_hover", "transition", "cruise", "back_transition", "landing_hover"]
    assert {m["type"] for m in r["missing"]} >= {"RCOU", "ARSP", "MSG", "VIBE"}
    tr = r["phases"][1]
    assert "Transition done" not in tr["decided_by"]["end"]


def test_no_battery_messages(tmp_path: Path) -> None:
    path = tmp_path / "nobat.bin"
    synthesize_quadplane_log(
        str(path), SynthFlight(hover_power_w=400, cruise_power_w=220, cruise_s=60, omit=("BAT",))
    )
    r = process_log(path)
    assert next(p["key"] for p in r["phases"]) == "takeoff_hover"
    assert "power_w" not in r["phases"][0]
    assert r["energy_check"]["agrees"] is None
    assert any("No battery messages" in n for n in r["notes"])


def test_progress_reports(tmp_path: Path) -> None:
    path = tmp_path / "p.bin"
    synthesize_quadplane_log(
        str(path), SynthFlight(hover_power_w=400, cruise_power_w=220, cruise_s=30)
    )
    seen: list[tuple[float, str]] = []
    process_log(path, progress=lambda f, s: seen.append((f, s)))
    fracs = [f for f, _ in seen]
    assert fracs == sorted(fracs) and fracs[-1] == 1.0 and len(seen) > 5


def test_bounded_memory_on_a_large_log(tmp_path: Path) -> None:
    """A ~40 MB log (IMU at 400 Hz) parses with a peak RSS far below the 300 MB target.

    The peak is the child's own ``VmHWM`` (/proc/self/status), not ``ru_maxrss``: Linux keeps
    the pre-exec high-water mark in ``signal->maxrss`` across ``execve``, and Python starts
    the child with vfork, so ``ru_maxrss`` reports the *parent* pytest process (about 850 MB
    late in a full CI run) rather than the parser. ``VmHWM`` counts resident file-backed pages
    of the mapped log too (the parser hands them back with ``MADV_DONTNEED``, which works
    without memory pressure), and the anonymous part is checked on its own."""
    path = tmp_path / "large.bin"
    synthesize_quadplane_log(
        str(path),
        SynthFlight(hover_power_w=400, cruise_power_w=220, cruise_s=1200, imu_hz=400, esc=False),
    )
    size_mb = path.stat().st_size / 1e6
    assert size_mb > 30
    code = (
        "import sys, json\n"
        "from app.flightlog import process_log\n"
        "r = process_log(sys.argv[1])\n"
        "st = dict(l.split(':', 1) for l in open('/proc/self/status') if ':' in l)\n"
        "kb = lambda k: int(st[k].split()[0]) / 1024\n"
        "print(json.dumps({'rss_mb': kb('VmHWM'), 'anon_mb': kb('RssAnon'),"
        " 'file_mb': kb('RssFile'), 'phases': len(r['phases'])}))\n"
    )
    out = subprocess.run(
        [sys.executable, "-c", code, str(path)],
        cwd=BACKEND,
        capture_output=True,
        text=True,
        check=True,
        timeout=300,
    )
    res = json.loads(out.stdout.strip().splitlines()[-1])
    assert res["phases"] == 5
    assert res["rss_mb"] < 300, res
    assert res["anon_mb"] < 200, res
    # the mapped log does not stay resident: at most part of it after the parse
    assert res["file_mb"] < 40 + size_mb * 0.75, res


def test_parsed_log_keeps_only_decimated_arrays(synthetic_logs: list[dict[str, Any]]) -> None:
    log = parse_dataflash(str(synthetic_logs[0]["path"]))
    n = log.n_buckets
    for c in log.channels.values():
        assert len(c.mean) == n
    assert all(len(c.mean) == n for c in log.rcou.values())
    assert len(log.messages) < 50 and len(log.modes) == 3

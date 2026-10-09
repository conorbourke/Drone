"""Phase 6 API: flight-log upload and sample, the worker job (read, phases, comparison),
chart series, calibration apply/undo (and the analysis using it), built weights, upload limits
and the RESTRICT policy on versions."""

from __future__ import annotations

import copy
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import flight_data as fd
from app.config import Settings
from app.defaults import DEFAULT_DESIGN_PARAMETERS, DEFAULT_MISSION, DEFAULT_SETTINGS
from app.engine.analysis import run_analysis
from app.models import Analysis, FlightLog, User
from tests.conftest import FETCH_HEADERS

LOGS = Path(__file__).resolve().parent / "fixtures" / "logs"
SYNTHETIC = LOGS / "synthetic_quadplane.bin"
PHASES = ["takeoff_hover", "transition", "cruise", "back_transition", "landing_hover"]


def _wait(client: TestClient, log_id: int, timeout: float = 90.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while True:
        body = client.get(f"/api/flight-logs/{log_id}").json()
        if body["status"] in ("done", "error"):
            return body
        assert time.monotonic() < deadline, body
        time.sleep(0.1)


def _sample(client: TestClient, project_id: int) -> dict[str, Any]:
    r = client.post(f"/api/projects/{project_id}/flight-logs/sample")
    assert r.status_code == 202, r.text
    assert r.json()["status"] in ("queued", "running", "done")
    body = _wait(client, r.json()["id"])
    assert body["status"] == "done", body
    return body


def _upload(client: TestClient, project_id: int, data: bytes, filename: str, **params: Any) -> Any:
    return client.post(
        f"/api/projects/{project_id}/flight-logs",
        params={"filename": filename, **params},
        content=data,
        headers={"Content-Type": "application/octet-stream"},
    )


def test_logging_guide(auth_client: TestClient) -> None:
    body = auth_client.get("/api/flight-data/guide").json()
    assert body["log_bitmask"]["value"] == 11199
    params = {p["param"] for p in body["parameters"]}
    assert {"LOG_BITMASK", "LOG_DISARMED", "BATT_MONITOR"} <= params
    assert all(p["reason"] for p in body["parameters"])
    assert body["max_upload_mb"] == 200
    assert body["accepted"] == [".bin", ".log"]
    assert body["sample_available"] is True


def test_sample_log_is_read_and_compared(
    auth_client: TestClient, project: dict, settings: Settings
) -> None:
    assert fd.SAMPLE_LOG_PATH.is_file()
    body = _sample(auth_client, project["id"])
    assert body["sample"] is True and body["sample_note"]
    assert body["takeoff_mass_kg"] == fd.SAMPLE_MASS_KG
    assert body["firmware"] and "ArduPlane" in body["firmware"]
    assert body["vehicle_type"] == "plane (quadplane)"
    assert body["log_start_at"].endswith("Z")
    assert [p["key"] for p in body["summary"]["phases"]] == PHASES
    assert body["result"] and "series" not in body["result"]
    comp = body["comparison"]
    assert comp["available"] is True
    assert comp["reference"]["kind"] == "quick"  # no full analysis stored for the draft
    rows = {r["key"]: r for r in comp["comparisons"]}
    assert rows["hover_power"]["measured"] == pytest.approx(530, rel=0.05)
    assert rows["cruise_power"]["status"] in ("inside", "outside")
    assert sum(body["counts"].values()) == len(comp["comparisons"])
    # the file is stored under files/logs and listed
    with auth_client.app.state.session_factory() as db:  # type: ignore[attr-defined]
        row = db.get(FlightLog, body["id"])
        assert row is not None
        assert (settings.flight_logs_dir / row.storage_name).is_file()
    listed = auth_client.get(f"/api/projects/{project['id']}/flight-logs").json()
    assert [i["id"] for i in listed] == [body["id"]]
    assert "result" not in listed[0]


def test_series_are_downsampled_with_phases(auth_client: TestClient, project: dict) -> None:
    body = _sample(auth_client, project["id"])
    s = auth_client.get(f"/api/flight-logs/{body['id']}/series", params={"points": 200}).json()
    assert 100 <= len(s["t_s"]) <= 200
    assert s["interval_s"] >= 0.2
    for name in ("power_w", "voltage_v", "airspeed_mps", "alt_m", "vibe_z"):
        assert len(s["channels"][name]["values"]) == len(s["t_s"])
    # peaks survive the averaging
    peak = max(v for v in s["channels"]["power_w_max"]["values"] if v is not None)
    mean_peak = max(v for v in s["channels"]["power_w"]["values"] if v is not None)
    assert peak >= mean_peak
    assert [p["key"] for p in s["phases"]] == PHASES
    full = auth_client.get(f"/api/flight-logs/{body['id']}/series", params={"points": 5000})
    assert len(full.json()["t_s"]) <= fd.MAX_SERIES_POINTS


def test_downsample_keeps_extremes() -> None:
    series = {
        "rate_hz": 5.0,
        "t_s": [i * 0.2 for i in range(10)],
        "channels": {
            "power_w": {"unit": "W", "label": "P", "values": [1, 2, 3, None, 5, 6, 7, 8, 9, 10]},
            "power_w_max": {"unit": "W", "label": "P", "values": [1, 9, 3, 4, 5, 6, 7, 8, 9, 10]},
            "voltage_v_min": {"unit": "V", "label": "V", "values": [5] * 9 + [1]},
        },
    }
    out = fd.downsample_series(series, 10)
    assert len(out["t_s"]) == 10  # already small enough
    assert out["channels"]["power_w"]["values"][3] is None
    long = {
        "rate_hz": 5.0,
        "t_s": [i * 0.2 for i in range(40)],
        "channels": {k: {**v, "values": v["values"] * 4} for k, v in series["channels"].items()},
    }
    out = fd.downsample_series(long, 10)
    assert len(out["t_s"]) == 10 and out["interval_s"] == pytest.approx(0.8)
    assert out["channels"]["power_w_max"]["values"][0] == 9  # bucket maximum
    assert out["channels"]["voltage_v_min"]["values"][2] == 1  # bucket minimum (index 9)
    assert out["channels"]["power_w"]["values"][0] == pytest.approx(2.0)  # mean of 1, 2, 3


def test_upload_synthetic_log_against_a_version_and_restrict(
    auth_client: TestClient, project: dict
) -> None:
    v = auth_client.post(f"/api/projects/{project['id']}/versions", json={"name": "v1"}).json()
    r = _upload(
        auth_client,
        project["id"],
        SYNTHETIC.read_bytes(),
        "C:\\logs\\00000042.BIN",
        version_id=v["id"],
        takeoff_mass_kg=3.3,
    )
    assert r.status_code == 202, r.text
    item = r.json()
    assert item["filename"] == "00000042.BIN"
    assert item["version_number"] == 1 and item["source"] == "version"
    body = _wait(auth_client, item["id"])
    assert body["status"] == "done", body
    assert [p["key"] for p in body["summary"]["phases"]] == PHASES
    assert body["comparison"]["mass_kg"] == pytest.approx(3.3)
    assert body["comparison"]["reference"]["source"] == "version 1"

    # RESTRICT: the version cannot be deleted while the log refers to it
    d = auth_client.delete(f"/api/versions/{v['id']}")
    assert d.status_code == 409
    assert "flight log" in d.json()["detail"]
    assert auth_client.delete(f"/api/flight-logs/{item['id']}").status_code == 204
    assert auth_client.get(f"/api/flight-logs/{item['id']}").status_code == 404
    assert auth_client.delete(f"/api/versions/{v['id']}").status_code == 204


def test_reference_prefers_a_matching_full_analysis(
    auth_client: TestClient, project: dict, app: FastAPI, settings: Settings
) -> None:
    """A stored, finished, uncalibrated full analysis of the same draft is used as is."""
    draft = auth_client.get(f"/api/projects/{project['id']}/draft").json()
    result = run_analysis(
        copy.deepcopy(draft["parameters"]),
        copy.deepcopy(draft["mission"]),
        copy.deepcopy(DEFAULT_SETTINGS),
        mode="fast",
        cache_dir=str(settings.app_data_dir / "cache" / "polars"),
    )
    with app.state.session_factory() as db:
        row = Analysis(
            owner_id=db.scalar(select(User.id).where(User.is_owner.is_(True))),
            project_id=project["id"],
            version_id=None,
            kind="full",
            inputs={"parameters": draft["parameters"], "mission": draft["mission"]},
            inputs_hash="x" * 64,
            status="done",
            progress=1.0,
            stage="Done",
            result=result,
        )
        db.add(row)
        db.commit()
        analysis_id = row.id
    body = _sample(auth_client, project["id"])
    ref = body["comparison"]["reference"]
    assert ref["kind"] == "stored" and ref["analysis_id"] == analysis_id


def test_upload_limits_and_formats(
    auth_client: TestClient, project: dict, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    pid = project["id"]
    r = _upload(auth_client, pid, b"\xfe\x10" * 100, "flight.tlog")
    assert r.status_code == 415 and ".bin" in r.json()["detail"]
    r = _upload(auth_client, pid, b"hello", "flight.txt")
    assert r.status_code == 415
    r = _upload(auth_client, pid, b"not a dataflash log at all", "flight.bin")
    assert r.status_code == 415 and "DataFlash" in r.json()["detail"]
    r = _upload(auth_client, pid, b"", "flight.bin")
    assert r.status_code == 422
    r = _upload(auth_client, pid, b"\xa3\x95\x80", "flight.bin", takeoff_mass_kg=-1)
    assert r.status_code == 422
    # a declared length over 200 MB is refused before the body is read
    big = auth_client.post(
        f"/api/projects/{pid}/flight-logs",
        params={"filename": "big.bin"},
        content=b"\xa3\x95",
        headers={"Content-Length": str(fd.MAX_LOG_BYTES + 1)},
    )
    assert big.status_code == 413 and "200 MB" in big.json()["detail"]
    # just under the limit is allowed past the middleware (the route then reads the stream)
    monkeypatch.setattr(fd, "MAX_LOG_BYTES", 1000)
    r = _upload(auth_client, pid, b"\xa3\x95" + b"\0" * 2000, "flight.bin")
    assert r.status_code == 413
    # nothing is left behind by refused uploads
    assert not list(settings.flight_logs_dir.glob("*"))
    assert auth_client.get(f"/api/projects/{pid}/flight-logs").json() == []


def test_large_bodies_only_for_signed_in_owner(app: FastAPI) -> None:
    with TestClient(app, headers=FETCH_HEADERS) as anon:
        r = anon.post(
            "/api/projects/1/flight-logs",
            params={"filename": "x.bin"},
            content=b"\xa3\x95",
            headers={"Content-Length": str(2_000_000)},
        )
        assert r.status_code == 413
        assert "kB" in r.json()["detail"]


def test_unreadable_log_is_an_error_with_a_plain_message(
    auth_client: TestClient, project: dict
) -> None:
    r = _upload(auth_client, project["id"], b"\xa3\x95" + b"\x01" * 5000, "broken.bin")
    assert r.status_code == 202
    body = _wait(auth_client, r.json()["id"])
    assert body["status"] in ("error", "done")
    if body["status"] == "error":
        assert "could not be read" in body["error"]
    else:  # pymavlink may read zero messages without failing
        assert body["summary"]["phases"] == []


def test_patch_mass_rereads_the_log(auth_client: TestClient, project: dict) -> None:
    body = _sample(auth_client, project["id"])
    r = auth_client.patch(f"/api/flight-logs/{body['id']}", json={"takeoff_mass_kg": 4.2})
    assert r.status_code == 202, r.text
    again = _wait(auth_client, body["id"])
    assert again["takeoff_mass_kg"] == 4.2
    assert again["comparison"]["mass_kg"] == pytest.approx(4.2)
    r = auth_client.patch(f"/api/flight-logs/{body['id']}", json={"takeoff_mass_kg": 99})
    assert r.status_code == 422
    other = auth_client.post("/api/projects", json={"name": "Other"}).json()
    v = auth_client.post(f"/api/projects/{other['id']}/versions", json={"name": "v1"}).json()
    r = auth_client.patch(f"/api/flight-logs/{body['id']}", json={"version_id": v["id"]})
    assert r.status_code == 422
    r = auth_client.post(f"/api/flight-logs/{body['id']}/reprocess")
    assert r.status_code == 202
    assert _wait(auth_client, body["id"])["status"] == "done"


def test_calibration_apply_preview_and_undo(
    auth_client: TestClient, project: dict, app: FastAPI
) -> None:
    pid = project["id"]
    empty = auth_client.get(f"/api/projects/{pid}/calibration").json()
    assert empty["applied"] is None
    assert not any(f["applicable"] for f in empty["proposed"]["factors"].values())
    r = auth_client.post(f"/api/projects/{pid}/calibration", json={})
    assert r.status_code == 409

    log = _sample(auth_client, pid)
    state = auth_client.get(f"/api/projects/{pid}/calibration").json()
    factors = state["proposed"]["factors"]
    assert factors["hover_power"]["applicable"] is True
    assert 0.5 < factors["hover_power"]["value"] < 1.2
    assert factors["cruise_power"]["applicable"] is False and factors["cruise_power"]["why_not"]
    assert factors["battery_usable_energy"]["applicable"] is False  # 3S sample vs 6S design
    assert state["proposed"]["log_ids"] == [log["id"]]

    preview = auth_client.get(f"/api/projects/{pid}/calibration/preview").json()
    rows = {r["key"]: r for r in preview["rows"]}
    assert preview["factors"] == ["hover_power"]
    assert rows["hover_power"]["after"] < rows["hover_power"]["before"]
    assert rows["takeoff_mass"]["after"] == pytest.approx(rows["takeoff_mass"]["before"])

    applied = auth_client.post(f"/api/projects/{pid}/calibration", json={}).json()["applied"]
    assert set(applied["factors"]) == {"hover_power"}
    assert applied["n_logs"] == 1 and applied["source_log_ids"] == [log["id"]]
    assert applied["applied_at"].endswith("Z")

    # analyses queued from now on carry the calibration (and hash differently)
    a = auth_client.post(f"/api/projects/{pid}/analyses", json={"source": "draft"})
    assert a.status_code == 202, a.text
    with app.state.session_factory() as db:
        row = db.get(Analysis, a.json()["id"])
        assert row is not None
        cal = row.inputs["calibration"]
        assert cal["n_logs"] == 1
        assert cal["factors"]["hover_power"]["value"] == pytest.approx(
            applied["factors"]["hover_power"]["value"]
        )

    undone = auth_client.delete(f"/api/projects/{pid}/calibration").json()
    assert undone["applied"] is None
    assert undone["proposed"]["factors"]["hover_power"]["applicable"] is True


def test_engine_applies_calibration(settings: Settings) -> None:
    cache = str(settings.app_data_dir / "cache" / "polars")
    args = (DEFAULT_DESIGN_PARAMETERS, DEFAULT_MISSION, DEFAULT_SETTINGS)
    base = run_analysis(*copy.deepcopy(args), mode="fast", cache_dir=cache)
    cal = {
        "factors": {
            "hover_power": {"value": 1.1, "uncertainty": 0.03},
            "cruise_drag": {"value": 1.2, "uncertainty": 0.05},
            "battery_usable_energy": {"value": 0.95, "uncertainty": 0.02},
            "structural_mass": {"value": 1.1, "uncertainty": 0.02},
        },
        "n_logs": 2,
    }
    calibrated = run_analysis(*copy.deepcopy(args), mode="fast", cache_dir=cache, calibration=cal)
    s0, s1 = base["summary"], calibrated["summary"]
    assert s1["takeoff_mass"]["value"] > s0["takeoff_mass"]["value"]
    assert s1["hover_power"]["value"] > 1.1 * s0["hover_power"]["value"]
    assert s1["cruise_power"]["value"] > s0["cruise_power"]["value"]
    assert s1["endurance_cruise"]["value"] < s0["endurance_cruise"]["value"]
    assert calibrated["calibration"]["label"] == "Calibrated with 2 flights"
    assert any(n["key"] == "analysis.calibrated" for n in calibrated["notes"])
    assert "calibration" not in base
    # the measured drag uncertainty replaces the model's profile/parasite/induced bands
    w0 = s0["cruise_power"]["high"] - s0["cruise_power"]["low"]
    w1 = s1["cruise_power"]["high"] - s1["cruise_power"]["low"]
    assert w1 / s1["cruise_power"]["value"] < w0 / s0["cruise_power"]["value"]


def test_built_weights_feed_the_structural_factor(auth_client: TestClient, project: dict) -> None:
    pid = project["id"]
    body = auth_client.get(f"/api/projects/{pid}/built-weights").json()
    items = {i["key"]: i for i in body["items"]}
    assert "wing_structure" in items and "battery" in items
    assert "payload" not in items
    assert items["wing_structure"]["subgroup"] == "wing"
    assert body["structural"]["valid"] is False
    wing = items["wing_structure"]["predicted_g"]
    booms = items["booms"]["predicted_g"]
    r = auth_client.put(
        f"/api/projects/{pid}/built-weights",
        json={
            "items": [
                {"key": "wing_structure", "measured_g": round(wing * 1.2, 1), "note": "scale"},
                {"key": "booms", "measured_g": round(booms * 1.2, 1)},
                {"key": "battery", "measured_g": 900},
            ]
        },
    )
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["totals"]["weighed_items"] == 3
    assert out["structural"]["valid"] is True
    assert out["structural"]["value"] == pytest.approx(1.2, abs=0.01)
    assert set(out["structural"]["per_group"]) == {"wing", "booms"}
    saved = {i["key"]: i for i in out["items"]}
    assert saved["wing_structure"]["note"] == "scale"

    # built weights alone can be applied (structure mass)
    state = auth_client.get(f"/api/projects/{pid}/calibration").json()
    assert state["proposed"]["factors"]["structural_mass"]["applicable"] is True
    applied = auth_client.post(
        f"/api/projects/{pid}/calibration", json={"factors": ["structural_mass"]}
    ).json()["applied"]
    assert set(applied["factors"]) == {"structural_mass"} and applied["n_logs"] == 0

    # clearing and validation
    r = auth_client.put(
        f"/api/projects/{pid}/built-weights",
        json={"items": [{"key": "booms", "measured_g": None}]},
    )
    assert r.json()["totals"]["weighed_items"] == 2
    r = auth_client.put(
        f"/api/projects/{pid}/built-weights",
        json={"items": [{"key": "nonsense", "measured_g": 5}]},
    )
    assert r.status_code == 422
    r = auth_client.put(
        f"/api/projects/{pid}/built-weights",
        json={"items": [{"key": "booms", "measured_g": -5}]},
    )
    assert r.status_code == 422


def test_project_delete_removes_logs_and_files(
    auth_client: TestClient, project: dict, settings: Settings
) -> None:
    pid = project["id"]
    v = auth_client.post(f"/api/projects/{pid}/versions", json={"name": "v1"}).json()
    log = _sample(auth_client, pid)
    auth_client.patch(f"/api/flight-logs/{log['id']}", json={"version_id": v["id"]})
    _wait(auth_client, log["id"])
    auth_client.post(f"/api/projects/{pid}/calibration", json={})
    assert any(settings.flight_logs_dir.iterdir())
    assert auth_client.delete(f"/api/projects/{pid}").status_code == 204
    assert not any(settings.flight_logs_dir.iterdir())
    assert auth_client.get(f"/api/flight-logs/{log['id']}").status_code == 404


def test_other_owners_and_missing_rows(auth_client: TestClient, project: dict) -> None:
    assert auth_client.get("/api/flight-logs/999999").status_code == 404
    assert auth_client.get("/api/flight-logs/999999/series").status_code == 404
    assert auth_client.delete("/api/flight-logs/999999").status_code == 404
    assert auth_client.get("/api/projects/999999/calibration").status_code == 404
    assert auth_client.post("/api/projects/999999/flight-logs/sample").status_code == 404

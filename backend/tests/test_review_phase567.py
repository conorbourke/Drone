"""Regression tests for the Phase 5-7 review fixes: the calibrated drag uncertainty band, the
startup sweep of orphan flight-log files, the free-space guard on the data volume and the
one-at-a-time preview mesh builds."""

from __future__ import annotations

import copy
import shutil
import threading
import time
from collections import namedtuple
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app import disk_space
from app import exports as ex
from app.config import Settings
from app.defaults import DEFAULT_DESIGN_PARAMETERS, DEFAULT_MISSION, DEFAULT_SETTINGS
from app.engine.analysis import run_analysis
from app.models import Export
from tests.conftest import FETCH_HEADERS, PASSWORD

Usage = namedtuple("Usage", "total used free")


def _login(client: TestClient) -> None:
    assert client.post("/api/auth/login", json={"password": PASSWORD}).status_code == 200


def _upload(client: TestClient, project_id: int, data: bytes) -> Any:
    return client.post(
        f"/api/projects/{project_id}/flight-logs",
        params={"filename": "flight.bin"},
        content=data,
        headers={"Content-Type": "application/octet-stream"},
    )


# ---------------------------------------------------------------------------------------------
# Calibration: a measured cruise drag factor carries its uncertainty on the whole drag
# ---------------------------------------------------------------------------------------------


def test_calibrated_drag_band_covers_the_whole_drag(settings: Settings) -> None:
    cache = str(settings.app_data_dir / "cache" / "polars")
    args = (DEFAULT_DESIGN_PARAMETERS, DEFAULT_MISSION, DEFAULT_SETTINGS)
    u = 0.2
    cal = {"factors": {"cruise_drag": {"value": 1.0, "uncertainty": u}}, "n_logs": 1}
    result = run_analysis(*copy.deepcopy(args), mode="fast", cache_dir=cache, calibration=cal)
    drag = result["aero"]["drag_cruise"]
    # +/-20 % on the measured drag factor moves the whole drag by 20 % (the mass band adds a
    # little more). Perturbing only the profile term gave about +/-6 % here.
    assert drag["high"] / drag["value"] - 1 >= 0.95 * u
    assert 1 - drag["low"] / drag["value"] >= 0.95 * u


# ---------------------------------------------------------------------------------------------
# Orphan flight-log files are removed at startup
# ---------------------------------------------------------------------------------------------


def test_startup_removes_flight_log_files_without_a_row(app: FastAPI, settings: Settings) -> None:
    with TestClient(app, headers=FETCH_HEADERS) as client:
        _login(client)
        pid = client.post("/api/projects", json={"name": "Orphans"}).json()["id"]
        r = _upload(client, pid, b"\xa3\x95" + b"\0" * 64)
        assert r.status_code == 202, r.text
    kept = list(settings.flight_logs_dir.iterdir())
    assert len(kept) == 1
    # An upload cut off by a crash leaves its file without a row.
    orphan = settings.flight_logs_dir / "0123456789abcdef0123456789abcdef.bin"
    orphan.write_bytes(b"\xa3\x95partial")
    with TestClient(app, headers=FETCH_HEADERS):
        pass
    assert not orphan.exists()
    assert kept[0].exists()


# ---------------------------------------------------------------------------------------------
# Free-space guard
# ---------------------------------------------------------------------------------------------


@pytest.fixture
def nearly_full(monkeypatch: pytest.MonkeyPatch) -> None:
    free = disk_space.RESERVE_BYTES + 1024 * 1024  # 1 MB above the reserve
    monkeypatch.setattr(
        disk_space.shutil, "disk_usage", lambda _p: Usage(3 << 30, (3 << 30) - free, free)
    )


def test_uploads_and_jobs_are_refused_when_the_volume_is_nearly_full(
    app: FastAPI,
    auth_client: TestClient,
    project: dict,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    nearly_full: None,
) -> None:
    monkeypatch.setattr(settings, "export_fake_generator", "tests.export_fakes:generate_files")
    monkeypatch.setattr(settings, "mould_fake_generator", "tests.export_fakes:generate_moulds")
    pid = project["id"]
    # 2 MB would leave less than the reserve
    r = _upload(auth_client, pid, b"\xa3\x95" + b"\0" * (2 * 1024 * 1024))
    assert r.status_code == 507 and "free space" in r.json()["detail"]
    assert not settings.flight_logs_dir.exists() or not any(settings.flight_logs_dir.iterdir())
    # a small log still fits
    assert _upload(auth_client, pid, b"\xa3\x95" + b"\0" * 64).status_code == 202
    r = auth_client.post(f"/api/projects/{pid}/flight-logs/sample")
    assert r.status_code == 507  # the 8.9 MB sample does not
    r = auth_client.post(f"/api/projects/{pid}/exports", json={"source": "draft"})
    assert r.status_code == 507 and "make the files" in r.json()["detail"]
    r = auth_client.post(f"/api/projects/{pid}/moulds", json={})
    assert r.status_code == 507 and "make the moulds" in r.json()["detail"]
    with app.state.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(Export)) == 0


def test_free_bytes_measures_the_nearest_existing_parent(tmp_path: Path) -> None:
    missing = tmp_path / "a" / "b" / "c"
    free = disk_space.free_bytes(missing)
    assert free is not None and free > 0
    assert abs(free - shutil.disk_usage(tmp_path).free) < 64 * 1024 * 1024
    disk_space.ensure_free_space(missing, 0, "test")  # plenty of room: no error


# ---------------------------------------------------------------------------------------------
# Preview meshes are built one at a time
# ---------------------------------------------------------------------------------------------


def _write_stl(path: Path, offset: float) -> None:
    rec = np.zeros(2, dtype=np.dtype([("n", "<f4", (3,)), ("v", "<f4", (3, 3)), ("attr", "<u2")]))
    rec["v"][0] = [[0, 0, offset], [10, 0, offset], [0, 10, offset]]
    rec["v"][1] = [[10, 0, offset], [10, 10, offset], [0, 10, offset]]
    path.write_bytes(b"\0" * 80 + len(rec).to_bytes(4, "little") + rec.tobytes())


def test_preview_meshes_are_built_one_at_a_time(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    files = []
    for i in range(4):
        files.append(tmp_path / f"p{i}.stl")
        _write_stl(files[-1], float(i))
    files.append(files[0])  # a second request for the same piece is served from the cache
    active = 0
    peak = 0
    reads: list[Path] = []
    lock = threading.Lock()
    real_read = ex._read_stl

    def slow_read(path: Path) -> np.ndarray:
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
            reads.append(path)
        time.sleep(0.05)
        try:
            return real_read(path)
        finally:
            with lock:
                active -= 1

    monkeypatch.setattr(ex, "_read_stl", slow_read)
    monkeypatch.setattr(ex, "_mesh_cache", type(ex._mesh_cache)())
    results: list[dict[str, Any]] = []
    threads = [threading.Thread(target=lambda p=p: results.append(ex.piece_mesh(p))) for p in files]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert len(results) == len(files)
    assert peak == 1
    assert len(reads) == 4  # p0 was built once
    assert all(r["triangles"] == 2 for r in results)

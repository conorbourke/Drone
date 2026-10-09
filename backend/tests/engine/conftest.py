"""Shared fixtures for the Phase 3 engine tests.

One polar cache per test session (shared with the validation tests through pytest's base
temporary directory), so XFOIL runs once for each Reynolds number.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest

from app.defaults import DEFAULT_DESIGN_PARAMETERS, DEFAULT_MISSION, DEFAULT_SETTINGS
from app.engine.analysis import resolve_settings, run_analysis


@pytest.fixture(scope="session")
def polar_cache(tmp_path_factory: pytest.TempPathFactory) -> str:
    path = Path(tmp_path_factory.getbasetemp()) / "polar-cache"
    path.mkdir(exist_ok=True)
    return str(path)


@pytest.fixture
def params() -> dict[str, Any]:
    return copy.deepcopy(DEFAULT_DESIGN_PARAMETERS)


@pytest.fixture
def mission() -> dict[str, Any]:
    return copy.deepcopy(DEFAULT_MISSION)


@pytest.fixture
def engine_settings() -> dict[str, Any]:
    return resolve_settings(copy.deepcopy(DEFAULT_SETTINGS))


@pytest.fixture(scope="session")
def default_full(polar_cache: str) -> dict[str, Any]:
    """Full analysis (XFOIL runs on a cold cache) of the default design, timed."""
    return run_analysis(
        copy.deepcopy(DEFAULT_DESIGN_PARAMETERS),
        copy.deepcopy(DEFAULT_MISSION),
        copy.deepcopy(DEFAULT_SETTINGS),
        mode="full",
        cache_dir=polar_cache,
    )

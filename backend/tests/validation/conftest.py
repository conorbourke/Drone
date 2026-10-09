from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(scope="session")
def polar_cache(tmp_path_factory: pytest.TempPathFactory) -> str:
    path = Path(tmp_path_factory.getbasetemp()) / "polar-cache"
    path.mkdir(exist_ok=True)
    return str(path)

"""Battery: voltage sag, energy, ratings."""

from __future__ import annotations

import pytest

from app.engine import battery as bat


def _pack(chem: str = "lipo") -> dict:
    return bat.pack_model(
        {"chemistry": chem, "cells_series": 6, "cells_parallel": 1, "capacity_mah": 5000}
    )


def test_sag_by_hand() -> None:
    pack = _pack()
    r = 6 * 0.015 / 5 + 0.002  # 6 cells of 3 mOhm + 2 mOhm wiring
    assert pack["r_pack_ohm"] == pytest.approx(r)
    lv = bat.loaded_voltage(pack, 400.0)
    i, v = lv["current_a"], lv["voltage_v"]
    assert v == pytest.approx(22.2 - i * r)
    assert v * i == pytest.approx(400.0)
    assert lv["loss_w"] == pytest.approx(i * i * r)
    assert bat.loaded_voltage(pack, 0.0)["voltage_v"] == pytest.approx(22.2)


def test_more_power_more_sag_and_limits() -> None:
    pack = _pack("li-ion")
    v1 = bat.loaded_voltage(pack, 200)["voltage_v"]
    v2 = bat.loaded_voltage(pack, 800)["voltage_v"]
    assert v2 < v1
    impossible = bat.loaded_voltage(pack, 1e6)
    assert impossible["feasible"] is False
    assert pack["i_continuous_a"] == pytest.approx(15.0)  # 3 C placeholder x 5 Ah


def test_energy_bookkeeping() -> None:
    pack = _pack()
    assert pack["energy_wh"] == pytest.approx(111.0)
    assert bat.usable_energy_wh(pack, 0.2) == pytest.approx(111 * 0.8 * 0.95)
    e = bat.phase_energy_wh(pack, 300.0, 3600)
    assert e["energy_wh"] > 300.0  # internal losses come out of the pack too
    assert bat.temperature_rise_k(pack, 30.0, 60) > 0

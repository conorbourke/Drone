"""Resting (open-circuit) cell voltage against state of charge."""

from __future__ import annotations

import itertools

#: Resting (open-circuit) cell voltage against state of charge, typical curves (estimates):
#: LiPo from widely published hobby-pack rest-voltage tables (e.g. 3.7 V nominal LiCoO2
#: polymer); Li-ion NMC 21700 from datasheet discharge curves at C/5 (e.g. Molicel P45B,
#: Samsung 50E). Uncertainty about ±0.03 V per cell.
OCV_TABLE = {
    "lipo": (
        (0.0, 3.27),
        (0.05, 3.61),
        (0.10, 3.69),
        (0.20, 3.73),
        (0.30, 3.77),
        (0.40, 3.79),
        (0.50, 3.82),
        (0.60, 3.87),
        (0.70, 3.93),
        (0.80, 4.02),
        (0.90, 4.08),
        (1.00, 4.20),
    ),
    "li-ion": (
        (0.0, 2.90),
        (0.05, 3.20),
        (0.10, 3.30),
        (0.20, 3.43),
        (0.30, 3.50),
        (0.40, 3.58),
        (0.50, 3.65),
        (0.60, 3.73),
        (0.70, 3.83),
        (0.80, 3.93),
        (0.90, 4.05),
        (1.00, 4.18),
    ),
}


def ocv_per_cell(chem: str, soc: float) -> float:
    tab = OCV_TABLE.get(chem, OCV_TABLE["lipo"])
    s = min(1.0, max(0.0, soc))
    for (s0, v0), (s1, v1) in itertools.pairwise(tab):
        if s <= s1:
            return v0 + (v1 - v0) * (s - s0) / (s1 - s0)
    return tab[-1][1]


def soc_from_ocv(chem: str, v_cell: float) -> float:
    tab = OCV_TABLE.get(chem, OCV_TABLE["lipo"])
    if v_cell <= tab[0][1]:
        return 0.0
    for (s0, v0), (s1, v1) in itertools.pairwise(tab):
        if v_cell <= v1:
            return s0 + (s1 - s0) * (v_cell - v0) / (v1 - v0)
    return 1.0


def ocv_integral(chem: str, soc_lo: float, soc_hi: float, steps: int = 200) -> float:
    """∫ OCV(s) ds per cell over [soc_lo, soc_hi] (V x fraction of capacity)."""
    if soc_hi <= soc_lo:
        return 0.0
    h = (soc_hi - soc_lo) / steps
    return sum(ocv_per_cell(chem, soc_lo + (k + 0.5) * h) for k in range(steps)) * h

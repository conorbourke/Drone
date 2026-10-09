"""Validation cases (docs/phases/PHASE3.md section 5).

Each case function returns a list of result rows::

    {id, group, name, compared, reference: {value, unit, source}, engine: {value, unit},
     error_pct, tolerance_pct, status: "pass" | "fail" | "skipped" | "info", note}

Groups: ``textbook`` (closed-form and lifting-line results), ``avl_reference`` (published
vortex-lattice values and the Tier 1 estimates), ``xfoil_reference`` (wind-tunnel data), and
``published_design`` (two commercial VTOL aircraft modelled from their published numbers).

References are cited per case. Where a published number could not be found, the case says so and
is skipped instead of inventing a value.
"""

from __future__ import annotations

import copy
import math
from collections.abc import Callable
from typing import Any

from app.engine import mass as mass_mod
from app.engine.analysis import run_analysis, tier1_reference
from app.engine.avl_model import run_avl_text
from app.engine.drag import cf_laminar, cf_turbulent
from app.engine.polars import PolarStore, SectionPolar
from app.engine.propulsion import generic_propeller
from app.engine.quantity import P_SL, R_AIR, RHO_SL, T_SL

Row = dict[str, Any]


def row(
    cid: str,
    group: str,
    name: str,
    compared: str,
    ref: float | None,
    ref_unit: str,
    ref_source: str,
    eng: float | None,
    tol_pct: float | None,
    note: str = "",
    status: str | None = None,
) -> Row:
    err = None
    if ref not in (None, 0) and eng is not None and math.isfinite(eng):
        err = (eng - ref) / abs(ref) * 100
    if status is None:
        if err is None or tol_pct is None:
            status = "info"
        else:
            status = "pass" if abs(err) <= tol_pct else "fail"
    return {
        "id": cid,
        "group": group,
        "name": name,
        "compared": compared,
        "reference": {"value": ref, "unit": ref_unit, "source": ref_source},
        "engine": {"value": eng, "unit": ref_unit},
        "error_pct": err,
        "tolerance_pct": tol_pct,
        "status": status,
        "note": note,
    }


# ---------------------------------------------------------------------------
# Closed-form helpers
# ---------------------------------------------------------------------------


def helmbold(ar: float, a0: float = 2 * math.pi) -> float:
    """Helmbold's equation (Anderson, Fundamentals of Aerodynamics, eq. 5.81)."""
    k = a0 / (math.pi * ar)
    return a0 / (math.sqrt(1 + k * k) + k)


def lifting_line(
    chord_fn: Callable[[float], float],
    span: float,
    alpha_rad: float,
    n_terms: int = 40,
    a0: float = 2 * math.pi,
) -> dict[str, float]:
    """Prandtl lifting-line theory solved with Glauert's Fourier series (Anderson, Fundamentals
    of Aerodynamics, section 5.3.2): sum A_n sin(n th) (mu n + sin th) = mu alpha sin th,
    mu = c(th) a0 / (4 b); CL = pi AR A1, CDi = pi AR sum n A_n^2 (symmetric: odd n only)."""
    import numpy as np

    ns = np.arange(1, 2 * n_terms, 2)
    th = np.linspace(0.01, math.pi / 2, n_terms)
    y = -span / 2 * np.cos(th)
    c = np.array([chord_fn(abs(v)) for v in y])
    mu = c * a0 / (4 * span)
    m = np.sin(np.outer(th, ns)) * (mu[:, None] * ns[None, :] + np.sin(th)[:, None])
    rhs = mu * alpha_rad * np.sin(th)
    a = np.linalg.solve(m, rhs)
    yy = np.linspace(-span / 2, span / 2, 4001)
    area = float(np.trapezoid([chord_fn(abs(v)) for v in yy], yy))
    ar = span * span / area
    cl = math.pi * ar * a[0]
    cdi = math.pi * ar * float(np.sum(ns * a * a))
    return {"CL": cl, "CDi": cdi, "e": cl * cl / (math.pi * ar * cdi), "AR": ar}


def _wing_avl(
    name: str,
    sections: list[tuple[float, float, float]],
    sref: float,
    cref: float,
    bref: float,
    nchord: int = 8,
    nspan: int = 24,
    sspace: float = 1.0,
) -> str:
    """Flat-plate wing (thin-airfoil camber line: a0 = 2 pi) from (x_le, y, chord) sections."""
    lines = [
        name,
        "0.0",
        "0 0 0.0",
        f"{sref:.6f} {cref:.6f} {bref:.6f}",
        "0.0 0.0 0.0",
        "0.0",
        "SURFACE",
        "Wing",
        f"{nchord} 1.0 {nspan} {sspace}",
        "YDUPLICATE",
        "0.0",
    ]
    for x, y, c in sections:
        lines += ["SECTION", f"{x:.6f} {y:.6f} 0.0 {c:.6f} 0.0"]
    return "\n".join(lines) + "\n"


def _tier1_slope(ar: float, sweep_t: float, cla_ratio: float = 1.0) -> float:
    """Tier 1 lift slope (Helmbold/DATCOM with sweep, Mach 0, no fuselage factor)."""
    eta = cla_ratio
    return 2 * math.pi * ar / (2 + math.sqrt(4 + (ar / eta) ** 2 * (1 + math.tan(sweep_t) ** 2)))


def _avl_alpha(text: str, alpha: float = 4.0, xref: float = 0.0) -> dict[str, Any]:
    return run_avl_text(text, [{"name": "a", "alpha": alpha, "xref": xref}])[0]


# ---------------------------------------------------------------------------
# 1. Textbook
# ---------------------------------------------------------------------------


def textbook_cases() -> list[Row]:
    out: list[Row] = []
    g = "textbook"
    # Elliptic wing, AR 8: lifting-line theory gives e = 1 exactly.
    b, c0 = 8.0, 4 / math.pi * 1.0  # area = pi b c0 / 4 = 8 -> AR 8
    secs = []
    n = 24
    for i in range(n + 1):
        th = math.pi / 2 * i / n
        y = b / 2 * math.sin(th)
        c = c0 * math.sqrt(max(0.0, 1 - (2 * y / b) ** 2))
        secs.append((0.25 * c0 - 0.25 * max(c, 1e-3), y, max(c, 1e-3)))
    area = math.pi * b * c0 / 4
    res = _avl_alpha(_wing_avl("Elliptic AR 8", secs, area, area / b, b, 8, 40, 1.0))
    e = res["totals"]["e"]
    out.append(
        row(
            "textbook.elliptic_e",
            g,
            "Elliptic wing AR 8: span efficiency",
            "AVL span efficiency e (Trefftz plane) vs lifting-line theory (e = 1)",
            1.0,
            "",
            "Anderson, Fundamentals of Aerodynamics, 6th ed., sec. 5.3.1 (elliptic lift "
            "distribution, e = 1)",
            e,
            2.0,
            "A discretised elliptic planform in AVL; small deviations come from the finite "
            "number of sections.",
        )
    )
    cl = res["totals"]["CL"]
    out.append(
        row(
            "textbook.elliptic_cdi",
            g,
            "Elliptic wing AR 8: induced drag",
            "AVL CDi vs CL^2 / (pi AR) at the same CL",
            cl * cl / (math.pi * 8),
            "",
            "Anderson, Fundamentals of Aerodynamics, eq. 5.62",
            res["totals"]["CDff"],
            3.0,
        )
    )
    # Rectangular wing AR 8 vs lifting-line (Glauert series), same alpha.
    alpha = math.radians(4.0)
    llt = lifting_line(lambda _y: 1.0, 8.0, alpha)
    res = _avl_alpha(_wing_avl("Rect AR 8", [(0, 0, 1.0), (0, 4.0, 1.0)], 8.0, 1.0, 8.0))
    out.append(
        row(
            "textbook.rect8_e",
            g,
            "Rectangular wing AR 8: span efficiency",
            "AVL e vs Prandtl lifting-line theory (Glauert series, 40 terms)",
            llt["e"],
            "",
            "Anderson, Fundamentals of Aerodynamics, sec. 5.3.2 (general lifting-line "
            "solution; induced drag factor delta for a rectangular wing, fig. 5.20)",
            res["totals"]["e"],
            5.0,
            "Lifting-line theory represents the square tips poorly (it gives a larger delta "
            "than lifting-surface methods such as AVL); a few per cent difference is "
            "expected, hence 5 %.",
        )
    )
    cl = res["totals"]["CL"]
    out.append(
        row(
            "textbook.rect8_cdi",
            g,
            "Rectangular wing AR 8: induced drag",
            "AVL CDi vs lifting-line CL^2 / (pi AR e_LLT) at AVL's CL",
            cl * cl / (math.pi * 8 * llt["e"]),
            "",
            "Anderson, Fundamentals of Aerodynamics, eq. 5.61-5.62 with the lifting-line e",
            res["totals"]["CDff"],
            5.0,
        )
    )
    # Lift-curve slope vs Helmbold for AR 4, 8, 16.
    for ar in (4, 8, 16):
        span = float(ar)
        c_root = 4 / math.pi  # elliptic: area = pi b c0 / 4 = b -> AR = b
        secs = []
        for i in range(25):
            th = math.pi / 2 * i / 24
            y = span / 2 * math.sin(th)
            c = max(1e-3, c_root * math.sqrt(max(0.0, 1 - (2 * y / span) ** 2)))
            secs.append((0.25 * c_root - 0.25 * c, y, c))
        res = _avl_alpha(_wing_avl(f"Elliptic AR {ar}", secs, span, 1.0, span, 8, 40, 1.0))
        cla = res["stab"]["dCL/dalpha"]
        out.append(
            row(
                f"textbook.helmbold_ar{ar}",
                g,
                f"Lift-curve slope, elliptic wing AR {ar}",
                "AVL CL_alpha (flat-plate elliptic planform) vs Helmbold's equation (a0 = 2 pi)",
                helmbold(ar),
                "/rad",
                "Helmbold (1942) as given in Anderson, Fundamentals of Aerodynamics, "
                "eq. 5.81 (derived for elliptic loading, valid down to low aspect ratio)",
                cla,
                4.0,
            )
        )
    # ISA density.
    rho = P_SL / (R_AIR * T_SL)
    out.append(
        row(
            "textbook.isa_density",
            g,
            "Sea-level standard air density",
            "p / (R T) at 101 325 Pa and 288.15 K vs the tabulated ISA density",
            1.225,
            "kg/m³",
            "ICAO Doc 7488 / ISO 2533",
            rho,
            0.1,
        )
    )
    out.append(
        row(
            "textbook.isa_engine",
            g,
            "Engine air density constant",
            "Engine RHO_SL vs ISA",
            1.225,
            "kg/m³",
            "ICAO Doc 7488 / ISO 2533",
            RHO_SL,
            0.01,
        )
    )
    # Momentum theory hover power: T = 20 N on a 15 in (0.381 m) disc.
    thrust, d = 20.0, 0.381
    area = math.pi * d * d / 4
    ref = thrust * math.sqrt(thrust / (2 * RHO_SL * area))
    out.append(
        row(
            "textbook.momentum_hover",
            g,
            "Momentum-theory hover power",
            "Engine ideal power T^1.5/sqrt(2 rho A) vs T x induced velocity (20 N, 381 mm)",
            ref,
            "W",
            "Leishman, Principles of Helicopter Aerodynamics, 2nd ed., eq. 2.15-2.17",
            mass_mod.ideal_hover_power(thrust, area),
            0.1,
        )
    )
    fm = generic_propeller(381, 140, 2).figure_of_merit()
    out.append(
        row(
            "textbook.figure_of_merit",
            g,
            "Generic propeller static figure of merit",
            "Static FM of the generic 15x5.5 in propeller vs the small-propeller band "
            "0.5-0.7 (centre 0.6)",
            0.6,
            "",
            "Leishman ch. 2 (well-designed rotors 0.7-0.8; small fixed-pitch propellers "
            "lower); Brandt & Selig AIAA 2011-1255 static data",
            fm,
            17.0,
        )
    )
    # Skin friction.
    out.append(
        row(
            "textbook.cf_laminar",
            g,
            "Laminar flat-plate skin friction at Re 1e6",
            "Engine Blasius Cf vs 1.328/sqrt(Re)",
            1.328e-3,
            "",
            "Blasius solution; Raymer, Aircraft Design, eq. 12.25",
            cf_laminar(1e6),
            0.1,
        )
    )
    ks = _karman_schoenherr(1e7)
    out.append(
        row(
            "textbook.cf_turbulent",
            g,
            "Turbulent flat-plate skin friction at Re 1e7",
            "Engine (Raymer eq. 12.27, Prandtl-Schlichting form) vs the independent "
            "Kármán-Schoenherr relation 0.242/sqrt(Cf) = log10(Re Cf)",
            ks,
            "",
            "Schoenherr (1932); Schlichting, Boundary-Layer Theory, ch. 21",
            cf_turbulent(1e7, 0.0),
            3.0,
        )
    )
    return out


def _karman_schoenherr(re: float) -> float:
    from scipy.optimize import brentq

    return brentq(lambda cf: 0.242 / math.sqrt(cf) - math.log10(re * cf), 1e-4, 1e-2)


# ---------------------------------------------------------------------------
# 2. AVL reference
# ---------------------------------------------------------------------------


def avl_reference_cases(cache_dir: str | None = None) -> list[Row]:
    out: list[Row] = []
    g = "avl_reference"
    # (a) Rectangular AR 16 at 5 deg: Helmbold and lifting line; Tier 1 within +/-8 %.
    res = _avl_alpha(_wing_avl("Rect AR 16", [(0, 0, 1.0), (0, 8.0, 1.0)], 16.0, 1.0, 16.0), 5.0)
    cl = res["totals"]["CL"]
    llt = lifting_line(lambda _y: 1.0, 16.0, math.radians(5.0))
    out.append(
        row(
            "avl.rect16_cl",
            g,
            "Rectangular AR 16 at 5°: lift coefficient",
            "AVL CL vs Prandtl lifting-line theory (high aspect ratio, where it is accurate)",
            llt["CL"],
            "",
            "Anderson, Fundamentals of Aerodynamics, sec. 5.3.2",
            cl,
            5.0,
            "Rectangular tips: lifting-line and lifting-surface methods differ by a few "
            "per cent (5 % tolerance).",
        )
    )
    out.append(
        row(
            "avl.rect16_e",
            g,
            "Rectangular AR 16 at 5°: span efficiency",
            "AVL e vs lifting-line e",
            llt["e"],
            "",
            "Anderson, Fundamentals of Aerodynamics, sec. 5.3.2",
            res["totals"]["e"],
            6.0,
            "Lifting-line theory over-predicts the induced drag factor of square tips as "
            "the aspect ratio grows (6 % tolerance).",
        )
    )
    t1_slope = helmbold(16)  # Tier 1 uses Helmbold for an unswept wing
    out.append(
        row(
            "avl.rect16_tier1_slope",
            g,
            "Rectangular AR 16: Tier 1 lift slope within its uncertainty",
            "Tier 1 Helmbold CL_alpha (a0 = 2 pi) vs AVL (Tier 1 states +/-8 %)",
            res["stab"]["dCL/dalpha"],
            "/rad",
            "AVL (this engine)",
            t1_slope,
            8.0,
        )
    )
    e_t1 = mass_mod.clamp(1.78 * (1 - 0.045 * 16**0.68) - 0.64, 0.5, 0.95)
    out.append(
        row(
            "avl.rect16_tier1_oswald",
            g,
            "Rectangular AR 16: Tier 1 Oswald vs AVL span efficiency",
            "Tier 1 Raymer Oswald e vs AVL inviscid e",
            res["totals"]["e"],
            "",
            "AVL (this engine)",
            e_t1,
            None,
            "Informational: Raymer's Oswald fit includes the viscous drag that grows with "
            "lift, AVL's e is inviscid only (the engine adds XFOIL profile drag separately), "
            "so they are not expected to agree; Tier 1 is conservative here.",
            status="info",
        )
    )
    # (b) Swept wing: AR 5, taper 1, quarter-chord sweep 45 deg (Bertin & Smith example).
    b = 5.0
    sweep = math.radians(45)
    semi = b / 2
    secs = [(0, 0, 1.0), (semi * math.tan(sweep), semi, 1.0)]
    coarse = _avl_alpha(_wing_avl("Swept AR 5 coarse", secs, 5.0, 1.0, 5.0, 1, 4, 0.0), 2.0)
    out.append(
        row(
            "avl.swept_ar5_published_lattice",
            g,
            "Swept wing (AR 5, taper 1, 45° sweep): published vortex-lattice result",
            "AVL with the published lattice (1 chordwise x 4 spanwise panels per side) vs "
            "the published VLM CL_alpha",
            3.443,
            "/rad",
            "Bertin & Smith, Aerodynamics for Engineers, vortex-lattice example for this "
            "wing (3.443/rad); Tornado VLM gives 3.450 (Melin, KTH 2000)",
            coarse["stab"]["dCL/dalpha"],
            1.0,
        )
    )
    res = _avl_alpha(_wing_avl("Swept AR 5", secs, 5.0, 1.0, 5.0, 8, 20, 1.0), 2.0)
    cla = res["stab"]["dCL/dalpha"]
    out.append(
        row(
            "avl.swept_ar5_converged",
            g,
            "Swept wing (AR 5, 45°): converged lattice",
            "AVL with the engine's lattice (8 x 20) vs the coarse published lattice",
            3.443,
            "/rad",
            "Bertin & Smith (coarse 4-panel lattice)",
            cla,
            None,
            "Information: refining the lattice lowers the lift slope by about 8 %; the "
            "published 4-panel value is not converged. The engine uses converged lattices.",
            status="info",
        )
    )
    t1 = _tier1_slope(5.0, math.radians(45), 1.0)
    out.append(
        row(
            "avl.swept_ar5_tier1",
            g,
            "Swept wing (AR 5, 45°): Tier 1 lift slope within its uncertainty",
            "Tier 1 Helmbold/DATCOM with sweep vs converged AVL (+/-8 %)",
            cla,
            "/rad",
            "AVL (this engine)",
            t1,
            8.0,
        )
    )
    # (c) Tapered swept wing: AR 6, taper 0.4, 30 deg leading-edge sweep.
    ar, lam, sw_le = 6.0, 0.4, math.radians(30)
    span = 6.0
    area = span * span / ar
    c_r = 2 * area / (span * (1 + lam))
    secs = [(0, 0, c_r), ((span / 2) * math.tan(sw_le), span / 2, c_r * lam)]
    mac = (2 / 3) * c_r * (1 + lam + lam * lam) / (1 + lam)
    res = _avl_alpha(_wing_avl("Tapered swept", secs, area, mac, span, 8, 24, 1.0), 2.0)
    cla = res["stab"]["dCL/dalpha"]
    sweep_t = math.atan(math.tan(sw_le) - 4 * 0.3 / ar * (1 - lam) / (1 + lam))
    t1 = _tier1_slope(ar, sweep_t, 1.0)
    out.append(
        row(
            "avl.tapered_swept_tier1",
            g,
            "Tapered swept wing (AR 6, taper 0.4, 30° LE sweep): Tier 1 within its uncertainty",
            "Tier 1 Helmbold/DATCOM (sweep of the 30 % chord line) vs AVL (+/-8 %)",
            cla,
            "/rad",
            "AVL (this engine). A published vortex-lattice value for a tapered "
            "swept benchmark wing (e.g. the Warren-12 planform) could not be confirmed, so "
            "only the Tier 1 comparison is made.",
            t1,
            8.0,
        )
    )
    # (c) Default design: Tier 1 lift slope and neutral point vs AVL (informational + tolerance).
    from app.defaults import DEFAULT_DESIGN_PARAMETERS, DEFAULT_MISSION, DEFAULT_SETTINGS
    from app.engine.geometry import build_geometry

    r = run_analysis(
        DEFAULT_DESIGN_PARAMETERS,
        DEFAULT_MISSION,
        DEFAULT_SETTINGS,
        mode="fast",
        cache_dir=cache_dir,
        uncertainty=False,
    )
    if r.get("valid"):
        geo = build_geometry(DEFAULT_DESIGN_PARAMETERS)
        store = PolarStore(cache_dir, allow_xfoil=False)
        tw: SectionPolar = store.table_polar("sd7037", r["aero"]["reynolds_cruise"]["value"])  # type: ignore[assignment]
        tt: SectionPolar = store.table_polar("naca0009", r["aero"]["polars"]["tail"]["re"])  # type: ignore[assignment]
        t1r = tier1_reference(geo, tw, tt, r["summary"]["takeoff_mass"]["value"], 16.0, 0.044)
        np_avl = r["balance"]["neutral_point_x"]["value"]
        mac = r["geometry"]["wing"]["mac_mm"]
        diff_mac = (t1r["neutral_point_x_mm"] - np_avl) / mac * 100
        out.append(
            row(
                "avl.default_np_tier1",
                g,
                "Default design: Tier 1 neutral point vs AVL",
                "Tier 1 neutral point (wing + tail + Multhopp fuselage) vs AVL Xnp, as % MAC "
                "difference (Tier 1 states +/-5 % MAC)",
                0.0,
                "% MAC",
                "AVL (this engine)",
                diff_mac,
                None,
                f"Information (not one of the contract cases): difference {diff_mac:+.1f} % MAC "
                f"({'inside' if abs(diff_mac) <= 5 else 'outside'} Tier 1's stated +/-5 % MAC). "
                "AVL's slender-body fuselage is more destabilising than Multhopp's strip "
                "method and the inverted V sits near the wing wake; the analysis keeps the "
                "more conservative AVL value.",
                status="info",
            )
        )
    return out


# ---------------------------------------------------------------------------
# 3. XFOIL reference
# ---------------------------------------------------------------------------


def xfoil_reference_cases(cache_dir: str | None = None) -> list[Row]:
    out: list[Row] = []
    g = "xfoil_reference"
    store = PolarStore(cache_dir, ncrit=9.0, allow_xfoil=True)
    pol = store.get("naca0012", 1_000_000, "wing")  # full sweep (cl_max is reported)
    src_note = f"Engine polar: {pol.note}."
    cla_deg = (pol.summary["cl_alpha_per_rad"] or float("nan")) * math.pi / 180
    out.append(
        row(
            "xfoil.naca0012_cla",
            g,
            "NACA 0012 at Re 1e6: lift-curve slope",
            "XFOIL lift slope (fit -2...6°) vs the wind-tunnel fit beta*cl_alpha = 0.1025 + "
            "0.00485 log10(Re/1e6) per degree",
            0.1025,
            "/deg",
            "McCroskey, 'A critical assessment of wind tunnel results for the NACA 0012 "
            "airfoil', NASA TM-100019 / AGARD CP-429 (1987), as reproduced on the NASA "
            "Turbulence Modeling Resource NACA 0012 validation page; data incl. Ladson NASA "
            "TM-4074 (Re 2-12e6, so Re 1e6 is a slight extrapolation)",
            cla_deg,
            10.0,
            src_note + " XFOIL is known to over-predict the viscous lift slope by several "
            "per cent at these Reynolds numbers.",
        )
    )
    cdmin_ref = 0.0044 + 0.018 * 1e6**-0.15
    out.append(
        row(
            "xfoil.naca0012_cdmin",
            g,
            "NACA 0012 at Re 1e6: minimum drag",
            "XFOIL cd_min vs the untripped (free-transition) wind-tunnel fit cd0 = 0.0044 + "
            "0.018 Re^-0.15",
            cdmin_ref,
            "",
            "McCroskey (1987) fit to untripped data, as reproduced on the NASA Turbulence "
            "Modeling Resource NACA 0012 page (fit range starts near Re 2e6)",
            pol.summary["cd_min"],
            20.0,
            src_note + " Tunnel-to-tunnel scatter of NACA 0012 drag at these Reynolds "
            "numbers is 10-20 % (McCroskey 1987).",
        )
    )
    out.append(
        row(
            "xfoil.naca0012_clmax",
            g,
            "NACA 0012 at Re 1e6: maximum lift",
            "XFOIL cl_max vs wind tunnel",
            None,
            "",
            "Not found: Abbott & von Doenhoff give NACA 0012 data only at Re 3-9e6 (cl_max "
            "about 1.1-1.6 across that range) and the Ladson TM-4074 tables at Re 1e6 were "
            "not available, so no reliable published value at Re 1e6 was found.",
            pol.summary["cl_max"],
            None,
            "Skipped rather than compared with an invented "
            "value. Engine value shown for information.",
            status="skipped",
        )
    )
    sd = store.get("sd7037", 200_000)
    out.append(
        row(
            "xfoil.sd7037_uiuc",
            g,
            "SD7037 at Re 200k: UIUC wind-tunnel data",
            "XFOIL cl_max and cd_min vs Selig et al., Summary of Low-Speed Airfoil Data "
            "vol. 1 (1995)",
            None,
            "",
            "Not found: the UIUC data files and the vol. 1 tables could not be retrieved "
            "from this build environment, and no secondary source quoted the Re 200k values.",
            sd.summary["cd_min"],
            None,
            f"Skipped rather than compared with an invented value. Engine values: cl_max "
            f"{sd.summary['cl_max']:.3f}, cd_min {sd.summary['cd_min']:.5f}. Add the UIUC "
            "numbers here when available.",
            status="skipped",
        )
    )
    table = store.table_polar("sd7037", 200_000)
    if table is not None:
        out.append(
            row(
                "xfoil.sd7037_reproducible",
                g,
                "SD7037 at Re 200k: reproducibility",
                "On-the-fly XFOIL cd_min vs the committed Phase 2 XFOIL table (same code and "
                "settings; a consistency check, not a validation)",
                table.summary["cd_min"],
                "",
                "backend/app/engine/data/airfoil_polars.json (Phase 2)",
                sd.summary["cd_min"],
                2.0,
            )
        )
    return out


# ---------------------------------------------------------------------------
# 4. Published designs
# ---------------------------------------------------------------------------

#: Every assumption for numbers that are not published.
TRINITY = {
    "name": "Quantum Systems Trinity F90+",
    "published": {
        "mtow_kg": 5.0,
        "span_m": 2.394,
        "cruise_mps": 17.0,
        "battery_kg": 1.5,
        "endurance_min": (60.0, 90.0),
        "payload_max_kg": 0.7,
    },
    "sources": "Quantum Systems Trinity F90+ datasheet and overview (2022): MTOW 5.0 kg, span "
    "2.394 m, optimal cruise 17 m/s, battery 1.5 kg, payload up to 0.7 kg, flight time 60 to "
    "90+ min depending on version and payload.",
    "assumptions": [
        "Modelled as a front-tilt quad (the real aircraft has two tilting front motors and one "
        "fixed rear motor; the tool supports four-rotor layouts only).",
        "Wing area 0.72 m² (aspect ratio 8, not published), taper 0.5, 5° sweep, SD7037 airfoil.",
        "V-tail sized for tail volumes 0.4 / 0.03 (the aircraft is a blended wing with a small "
        "tail; geometry not published).",
        "Fuselage 1.0 m long, 160 x 140 mm (not published).",
        "Lift propellers 15 x 7 in (381 x 178 mm), two blades (not published).",
        "Battery: Li-ion 6S, energy so that the Tier 1 pack model gives the published 1.5 kg "
        "(300 Wh at 200 Wh/kg).",
        "Carbon/composite construction (mission scale 'final'); the payload is adjusted so the "
        "take-off mass equals the published 5.0 kg (the mass model is not what is validated here).",
        "Published flight time is compared with the engine's total flight time (VTOL phases "
        "included) with a 5 % reserve instead of the app's 20 %, because manufacturer figures fly "
        "the pack close to empty.",
    ],
    "parameters": {
        "layout": "front_tilt",
        "wing": {
            "span_mm": 2394,
            "root_chord_mm": 401,
            "tip_chord_mm": 200,
            "sweep_deg": 5,
            "dihedral_deg": 2,
            "incidence_deg": 2,
            "twist_deg": -1,
            "airfoil": "sd7037",
            "x_le_mm": 300,
            "z_mm": 0,
        },
        "fuselage": {
            "length_mm": 1000,
            "width_mm": 160,
            "height_mm": 140,
            "cross_section": "ellipse",
        },
        "booms": {
            "count": 2,
            "lateral_offset_mm": 330,
            "length_mm": 1000,
            "x_offset_mm": -260,
            "diameter_mm": 20,
        },
        "motors": {"front_x_mm": 40, "rear_x_mm": 900, "height_mm": 25},
        "tilt": {"axis_x_mm": 40, "max_angle_deg": 90},
        "pusher": {"prop_diameter_mm": 254, "x_mm": 980},
        "tail": {
            "type": "v_tail",
            "span_mm": 640,
            "chord_mm": 170,
            "arm_mm": 700,
            "height_mm": 40,
            "v_angle_deg": 35,
            "airfoil": "naca0009",
        },
        "nose_bay": {"length_mm": 200, "width_mm": 130, "height_mm": 120},
        "landing_gear": {"type": "skids", "height_mm": 80},
        "propulsion": {"prop_diameter_mm": 381, "prop_pitch_mm": 178, "prop_blades": 2},
        "battery": {
            "chemistry": "li-ion",
            "cells_series": 6,
            "cells_parallel": 1,
            "capacity_mah": 13900,
            "x_mm": 420,
        },
        "allowances": {"avionics_g": 300, "wiring_fraction": 0.06},
    },
    "mission": {
        "scale": "final",
        "target_takeoff_mass_kg": 5.0,
        "target_endurance_min": 90,
        "cruise_speed_mps": 17.0,
        "payload_min_g": 300,
        "payload_max_g": 700,
    },
}

DELTAQUAD = {
    "name": "DeltaQuad Evo",
    "published": {
        "mtow_kg": 10.0,
        "span_m": 2.69,
        "wing_area_m2": 0.84,
        "empty_kg": 4.8,
        "one_battery_kg": 6.8,
        "cruise_mps": 16.5,
        "endurance_min": (244.0, 272.0),
    },
    "sources": "DeltaQuad Evo vehicle specifications and operations manual "
    "(docs.deltaquad.com): length 75 cm, span 269 cm, wing area 84 dm², empty 4.8 kg, 6.8 kg with "
    "one battery, MTOW 10 kg, cruise about 16.5 m/s, 6-cell 22 Ah semi-solid Li-ion per battery, "
    "244-272 min with two batteries (1 kg payload), pusher APC 15x10E.",
    "assumptions": [
        "Modelled as quad + pusher with a conventional wing and a small V-tail standing in for the "
        "flying wing's winglets and elevons (the tool cannot model tailless wings: trim drag and "
        "the fin contribution are approximate).",
        "Wing: published span and area, taper 0.45, 20° leading-edge sweep, MH 32 airfoil (not "
        "published; a low-drag section typical of flying-wing VTOLs).",
        "Fuselage 0.75 m long (published), 160 x 130 mm (not published).",
        "Lift propellers 15 x 5.5 in (381 x 140 mm, not published); pusher 15 x 10 in (published "
        "APC 15x10E; the engine uses P/D 0.7).",
        "Two packs of 6S 22 Ah modelled as Li-ion 6S2P of 22 Ah per group at 3.6 V nominal "
        "(950 Wh); the published semi-solid cells may be 3.7 V nominal (+3 %).",
        "Payload adjusted so the take-off mass is the published dual-battery mass with 1 kg "
        "payload (4.8 + 2 x 2.0 + 1.0 = 9.8 kg).",
        "Published flight time compared with the engine's total flight time with a 5 % reserve "
        "(manufacturer figures fly close to empty).",
    ],
    "parameters": {
        "layout": "quad_pusher",
        "wing": {
            "span_mm": 2690,
            "root_chord_mm": 431,
            "tip_chord_mm": 194,
            "sweep_deg": 20,
            "dihedral_deg": 0,
            "incidence_deg": 1,
            "twist_deg": -2,
            "airfoil": "mh32",
            "x_le_mm": 180,
            "z_mm": 0,
        },
        "fuselage": {
            "length_mm": 750,
            "width_mm": 160,
            "height_mm": 130,
            "cross_section": "ellipse",
        },
        "booms": {
            "count": 2,
            "lateral_offset_mm": 330,
            "length_mm": 1080,
            "x_offset_mm": -300,
            "diameter_mm": 20,
        },
        "motors": {"front_x_mm": 40, "rear_x_mm": 1000, "height_mm": 25},
        "tilt": {"axis_x_mm": 40, "max_angle_deg": 90},
        "pusher": {"prop_diameter_mm": 381, "x_mm": 740},
        "tail": {
            "type": "v_tail",
            "span_mm": 500,
            "chord_mm": 160,
            "arm_mm": 600,
            "height_mm": 30,
            "v_angle_deg": 45,
            "airfoil": "naca0009",
        },
        "nose_bay": {"length_mm": 160, "width_mm": 120, "height_mm": 110},
        "landing_gear": {"type": "skids", "height_mm": 80},
        "propulsion": {"prop_diameter_mm": 381, "prop_pitch_mm": 140, "prop_blades": 2},
        "battery": {
            "chemistry": "li-ion",
            "cells_series": 6,
            "cells_parallel": 2,
            "capacity_mah": 22000,
            "x_mm": 330,
        },
        "allowances": {"avionics_g": 350, "wiring_fraction": 0.06},
    },
    "mission": {
        "scale": "final",
        "target_takeoff_mass_kg": 9.8,
        "target_endurance_min": 244,
        "cruise_speed_mps": 16.5,
        "payload_min_g": 500,
        "payload_max_g": 1000,
    },
}


def _published_design(
    spec: dict[str, Any], target_mass_kg: float, cache_dir: str | None, prefix: str
) -> list[Row]:
    from app.engine.analysis import validate_inputs
    from app.engine.geometry import build_geometry

    g = "published_design"
    params = copy.deepcopy(spec["parameters"])
    params["schema_version"] = 2
    mission = {**spec["mission"], "schema_version": 1}
    settings = {"checks": {"battery_reserve_fraction": 0.05}}
    # Payload so the predicted take-off mass is the published one (stated assumption).
    p, m, _ = validate_inputs(params, mission)
    assert p is not None and m is not None
    from app.engine.analysis import resolve_settings

    s = resolve_settings(settings)
    geo = build_geometry(p)
    sol = mass_mod.solve_mass(p, geo, m, s)
    empty_pred = sol["result"]["empty"]["value"]
    for _ in range(6):
        sol = mass_mod.solve_mass(p, geo, m, s)
        diff_g = target_mass_kg * 1000 - sol["total_max_g"]
        if abs(diff_g) < 5:
            break
        m["payload_max_g"] = max(0.0, m["payload_max_g"] + diff_g)
        m["payload_min_g"] = min(m["payload_min_g"], m["payload_max_g"])
    r = run_analysis(p, m, s, mode="full", cache_dir=cache_dir)
    rows: list[Row] = []
    if not r.get("valid"):
        return [
            row(
                f"{prefix}.endurance",
                g,
                f"{spec['name']}: endurance",
                "analysis failed",
                None,
                "min",
                spec["sources"],
                None,
                None,
                "; ".join(c["message"] for c in r["checks"]),
                status="fail",
            )
        ]
    lo, hi = spec["published"]["endurance_min"]
    mid = (lo + hi) / 2
    tot = r["performance"]["endurance_total"]
    note = (
        f"Engine {tot['value']:.0f} min (range {tot['low']:.0f}-{tot['high']:.0f}) against the "
        f"published {lo:.0f}-{hi:.0f} min (compared with the middle, {mid:.0f} min). Tolerance "
        "is wide (+/-30 %) on purpose: the geometry is partly assumed, so this shows the size "
        f"of the error honestly. Cruise power {r['summary']['cruise_power']['value']:.0f} W, "
        f"L/D {r['summary']['lift_to_drag']['value']:.1f}, payload set to "
        f"{m['payload_max_g']:.0f} g to match {target_mass_kg:g} kg. Assumptions: "
        + " ".join(spec["assumptions"])
    )
    rows.append(
        row(
            f"{prefix}.endurance",
            g,
            f"{spec['name']}: flight time",
            "Predicted total flight time vs published",
            mid,
            "min",
            spec["sources"],
            tot["value"],
            30.0,
            note,
        )
    )
    rows.append(
        row(
            f"{prefix}.cruise_power",
            g,
            f"{spec['name']}: cruise power (implied)",
            "Engine cruise power vs the power implied by the published flight time and "
            "the modelled usable energy (information only)",
            r["battery"]["usable_energy"]["value"] * 0.95 / (mid / 60),
            "W",
            "Derived: usable energy / published mid flight time",
            r["summary"]["cruise_power"]["value"],
            None,
            "Information: shows whether the error comes from power or from energy.",
            status="info",
        )
    )
    if "empty_kg" in spec["published"]:
        rows.append(
            row(
                f"{prefix}.empty_mass",
                g,
                f"{spec['name']}: empty mass (mass model)",
                "Tier 1 mass model empty mass (no battery, no payload) vs published",
                spec["published"]["empty_kg"],
                "kg",
                spec["sources"],
                empty_pred,
                30.0,
                "The composite areal densities and statistical motor masses are estimates; "
                "this shows how far the mass model is from a production airframe.",
            )
        )
    return rows


def published_design_cases(cache_dir: str | None = None) -> list[Row]:
    return _published_design(TRINITY, 5.0, cache_dir, "published.trinity_f90") + _published_design(
        DELTAQUAD, 9.8, cache_dir, "published.deltaquad_evo"
    )


__all__ = [
    "DELTAQUAD",
    "RHO_SL",
    "TRINITY",
    "avl_reference_cases",
    "helmbold",
    "lifting_line",
    "published_design_cases",
    "textbook_cases",
    "xfoil_reference_cases",
]

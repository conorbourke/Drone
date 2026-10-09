"""AVL (Drela & Youngren vortex lattice) model of the design, run through ``optvl``.

What it does (docs/phases/PHASE3.md section 2):

* builds an AVL geometry file from the design (metres): the wing with root and tip sections,
  the library airfoil coordinates (``AFILE``), incidence and twist, dihedral and sweep; the tail
  surfaces by tail type (conventional H + fin, twin-boom H + two fins, V-tail, inverted V) with an
  elevator (ruddervator for V tails, symmetric deflection) on the last 30 % of the chord; the
  fuselage as an AVL slender body with the same cross-section areas as the loft;
* sets Sref, Cref and Bref to the wing area, mean aerodynamic chord and span (the Tier 1
  references), and the moment reference at the centre of gravity of each case;
* trims each case to the requested lift coefficient with Cm = 0 about the CG using the elevator,
  and returns CL, the Trefftz-plane induced drag (CDff) and span efficiency, Cm, alpha, the trim
  deflection, the stability derivatives (CL_alpha, Cm_alpha, Cn_beta, Cl_beta, Cm_q), the neutral
  point Xnp, and the strip loads (local cl, chord, c*cl) of every surface.

Booms and motor pods are not modelled as AVL bodies: a slender-body line singularity running
inside the wing's vortex sheet (the booms sit in the wing plane) gives unreliable loads, and
their destabilising volume is under ~5 % of the fuselage's for usual designs (the analysis
reports the actual ratio). Propeller slipstream is not modelled.

Discretisation: wing 6 chordwise x 48 spanwise (cosine) vortices per side, tail 6 x 10, fin
6 x 8. The spanwise count converges slowly near the fuselage body: on the default design the
neutral point moves 4 mm (2 % MAC) from 20 to 60 strips and less than 1 mm (0.4 % MAC) from
6 x 48 to 10 x 72, which also changes CL_alpha and CDi by under 0.3 %
(tests/engine/test_avl_model.py checks this), so 6 x 48 is used.

Process isolation: ``optvl`` loads a private copy of the AVL library for every solver instance
(about 12 MB that is never released) and the Fortran code writes to standard output. AVL and
XFOIL therefore run in a child "solver worker" process (``python -m app.engine.avl_model
--worker``) that answers JSON requests over pipes, silences its own standard output, and is
recycled after a fixed number of solver instances. A crash or hang in Fortran code cannot take
the API process down: the request times out and the worker is restarted.
"""

from __future__ import annotations

import atexit
import contextlib
import json
import math
import os
import selectors
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from pathlib import Path
from typing import Any

from app.engine import airfoils as airfoil_lib
from app.engine.geometry import section_area

BACKEND_DIR = Path(__file__).resolve().parents[2]

#: Vortex counts (see the module docstring for the convergence check).
WING_NCHORD = 6
WING_NSPAN = 48
TAIL_NCHORD = 6
TAIL_NSPAN = 10
FIN_NSPAN = 8
BODY_NODES = 24
#: Elevator / ruddervator hinge at 70 % chord (a 30 % chord control surface, typical for small
#: UAV tails; Raymer ch. 6 gives 25-50 % for elevators).
ELEVATOR_HINGE = 0.70
#: Trim deflection limit used by the checks (degrees); typical servo throw on small UAVs is
#: +/-20-25 deg, leaving the rest for manoeuvre and gust control.
TRIM_LIMIT_DEG = 15.0

# ---------------------------------------------------------------------------
# Solver worker (child process): AVL and XFOIL requests over JSON lines
# ---------------------------------------------------------------------------


def _worker_main() -> None:  # pragma: no cover - runs in the child process
    """Serve JSON-line requests. Standard output (fd 1) goes to /dev/null because the Fortran
    codes print there; replies go to a private duplicate of the original stdout."""
    reply = os.fdopen(os.dup(1), "w", buffering=1)
    devnull = os.open(os.devnull, os.O_WRONLY)
    os.dup2(devnull, 1)
    sys.stdout = open(os.devnull, "w")  # noqa: SIM115 - lives for the process
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
            op = req.get("op")
            if op == "avl":
                out = {"ok": True, "result": _avl_solve(req)}
            elif op == "xfoil":
                from app.engine.polars import xfoil_polar_raw

                out = {"ok": True, "result": xfoil_polar_raw(**req["args"])}
            elif op == "ping":
                out = {"ok": True, "result": "pong"}
            else:
                out = {"ok": False, "error": f"unknown op {op!r}"}
        except Exception as exc:
            out = {
                "ok": False,
                "error": f"{type(exc).__name__}: {exc}",
                "trace": traceback.format_exc()[-2000:],
            }
        reply.write(json.dumps(out, allow_nan=True) + "\n")
        reply.flush()


class SolverError(RuntimeError):
    """The worker failed, timed out or returned an error."""


class SolverWorker:
    """Parent-side handle to the child process. Thread-safe; one request at a time."""

    def __init__(self, max_jobs: int = 16, max_rss_mb: float | None = None) -> None:
        self.max_jobs = max_jobs
        #: The worker is restarted once its resident memory passes this (each AVL solver instance
        #: leaves ~10-30 MB behind). Default 250 MB, env SOLVER_WORKER_MAX_RSS_MB.
        self.max_rss_mb = max_rss_mb or float(os.environ.get("SOLVER_WORKER_MAX_RSS_MB", "250"))
        self._proc: subprocess.Popen[bytes] | None = None
        self._jobs = 0
        self._lock = threading.Lock()
        self._buf = b""

    def _start(self) -> None:
        env = dict(os.environ)
        env["PYTHONPATH"] = str(BACKEND_DIR) + os.pathsep + env.get("PYTHONPATH", "")
        env.setdefault("OMP_NUM_THREADS", "1")
        self._proc = subprocess.Popen(
            [sys.executable, "-m", "app.engine.avl_model", "--worker"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            cwd=str(BACKEND_DIR),
            env=env,
        )
        self._jobs = 0
        self._buf = b""

    def close(self) -> None:
        proc = self._proc
        self._proc = None
        if proc is None:
            return
        with contextlib.suppress(Exception):
            if proc.stdin:
                proc.stdin.close()
            proc.terminate()
            proc.wait(timeout=2)
        with contextlib.suppress(Exception):
            proc.kill()

    def request(self, payload: dict[str, Any], timeout: float = 60.0, cost: int = 1) -> Any:
        with self._lock:
            if self._proc is None or self._proc.poll() is not None or self._jobs >= self.max_jobs:
                self.close()
                self._start()
            assert self._proc is not None and self._proc.stdin and self._proc.stdout
            self._jobs += cost
            try:
                self._proc.stdin.write((json.dumps(payload) + "\n").encode())
                self._proc.stdin.flush()
                line = self._readline(timeout)
            except Exception as exc:
                self.close()
                raise SolverError(f"solver worker failed: {exc}") from exc
            out = json.loads(line)
            if self.rss_mb() > self.max_rss_mb:
                self.close()
            if not out.get("ok"):
                raise SolverError(out.get("error", "unknown solver error"))
            return out["result"]

    def rss_mb(self) -> float:
        """Resident memory of the worker in MB (Linux /proc; 0 when unknown)."""
        proc = self._proc
        if proc is None:
            return 0.0
        try:
            with open(f"/proc/{proc.pid}/status") as fh:
                for line in fh:
                    if line.startswith("VmRSS:"):
                        return int(line.split()[1]) / 1024
        except OSError:
            return 0.0
        return 0.0

    def _readline(self, timeout: float) -> bytes:
        assert self._proc is not None and self._proc.stdout
        fd = self._proc.stdout.fileno()
        deadline = time.monotonic() + timeout
        sel = selectors.DefaultSelector()
        sel.register(fd, selectors.EVENT_READ)
        try:
            while b"\n" not in self._buf:
                left = deadline - time.monotonic()
                if left <= 0:
                    raise TimeoutError(f"no answer within {timeout:.0f} s")
                if not sel.select(left):
                    continue
                chunk = os.read(fd, 65536)
                if not chunk:
                    raise RuntimeError("worker exited")
                self._buf += chunk
        finally:
            sel.close()
        line, _, self._buf = self._buf.partition(b"\n")
        return line


_WORKER: SolverWorker | None = None
_WORKER_LOCK = threading.Lock()


def solver_worker() -> SolverWorker:
    """The process-wide solver worker (started on first use, closed at interpreter exit)."""
    global _WORKER
    with _WORKER_LOCK:
        if _WORKER is None:
            _WORKER = SolverWorker()
            atexit.register(_WORKER.close)
        return _WORKER


def shutdown_solver_worker() -> None:
    """Stop the worker process (for an application shutdown hook)."""
    with _WORKER_LOCK:
        if _WORKER is not None:
            _WORKER.close()


# ---------------------------------------------------------------------------
# AVL input file
# ---------------------------------------------------------------------------


def _dat_text(airfoil_id: str) -> str:
    pts = airfoil_lib.coordinates(airfoil_id)
    return airfoil_id.upper() + "\n" + "\n".join(f"{x:.6f} {y:.6f}" for x, y in pts) + "\n"


def _section(
    x: float,
    y: float,
    z: float,
    chord: float,
    ainc: float,
    afile: str | None,
    claf: float,
    control: str | None = None,
) -> str:
    lines = ["SECTION", f"{x:.6f} {y:.6f} {z:.6f} {chord:.6f} {ainc:.4f}"]
    if afile:
        lines += ["AFILE", afile]
    lines += ["CLAF", f"{claf:.4f}"]
    if control:
        lines += ["CONTROL", control]
    return "\n".join(lines)


def build_avl_input(
    g: dict[str, Any],
    claf_wing: float = 1.0,
    claf_tail: float = 1.0,
    xref_m: float | None = None,
    include_body: bool = True,
) -> dict[str, Any]:
    """AVL geometry text plus the auxiliary files it references, from the derived geometry."""
    w = g["wing"]
    t = g["tail"]
    files: dict[str, str] = {}

    def afile(airfoil_id: str) -> str | None:
        if airfoil_id not in airfoil_lib.LIBRARY_BY_ID:
            return None  # flat plate camber line; reported by the caller
        name = f"{airfoil_id}.dat"
        files[name] = _dat_text(airfoil_id)
        return name

    mm = 1e-3
    s_ref = w["area_m2"]
    c_ref = w["mac_mm"] * mm
    b_ref = w["span_mm"] * mm
    x_ref = xref_m if xref_m is not None else w["ac_x_mm"] * mm
    out = [
        "VTOL design (generated by app.engine.avl_model)",
        "0.0",
        "0 0 0.0",
        f"{s_ref:.6f} {c_ref:.6f} {b_ref:.6f}",
        f"{x_ref:.6f} 0.0 0.0",
        "0.0",
    ]
    # ----- Wing -----
    wing_af = afile(w["airfoil"])
    semi = w["span_mm"] / 2 * mm
    rx, _, rz = (v * mm for v in w["root_le"])
    tx, ty, tz = (v * mm for v in w["tip_le"])
    out += [
        "SURFACE",
        "Wing",
        f"{WING_NCHORD} 1.0 {WING_NSPAN} 1.0",
        "YDUPLICATE",
        "0.0",
        "COMPONENT",
        "1",
        _section(rx, 0.0, rz, w["root_chord_mm"] * mm, w["incidence_deg"], wing_af, claf_wing),
        _section(
            tx,
            ty,
            tz,
            w["tip_chord_mm"] * mm,
            w["incidence_deg"] + w["twist_deg"],
            wing_af,
            claf_wing,
        ),
    ]
    assert abs(ty - semi) < 1e-9
    # ----- Tail -----
    tail_af = afile(t["airfoil"])
    le = t["le_x_mm"] * mm
    c = t["chord_mm"] * mm
    zt = t["z_mm"] * mm
    half = t["span_mm"] / 2 * mm
    elevator = f"elevator 1.0 {ELEVATOR_HINGE:.2f} 0.0 0.0 0.0 1.0"
    if t["type"] in ("v_tail", "inverted_v"):
        gam = math.radians(t["panel_angle_deg"])
        out += [
            "SURFACE",
            "Tail",
            f"{TAIL_NCHORD} 1.0 {TAIL_NSPAN} 1.0",
            "YDUPLICATE",
            "0.0",
            "COMPONENT",
            "2",
            _section(le, 0.0, zt, c, 0.0, tail_af, claf_tail, elevator),
            _section(le, half, zt + half * math.tan(gam), c, 0.0, tail_af, claf_tail, elevator),
        ]
    else:
        out += [
            "SURFACE",
            "Tail",
            f"{TAIL_NCHORD} 1.0 {TAIL_NSPAN} 1.0",
            "YDUPLICATE",
            "0.0",
            "COMPONENT",
            "2",
            _section(le, 0.0, zt, c, 0.0, tail_af, claf_tail, elevator),
            _section(le, half, zt, c, 0.0, tail_af, claf_tail, elevator),
        ]
        height = t["height_mm"] * mm
        if height > 0:
            if t["type"] == "twin_boom_h":
                yb = g["booms"][1]["start"][1] * mm
                zb = g["booms"][1]["start"][2] * mm
                out += [
                    "SURFACE",
                    "Fin",
                    f"{TAIL_NCHORD} 1.0 {FIN_NSPAN} 1.0",
                    "YDUPLICATE",
                    "0.0",
                    "COMPONENT",
                    "3",
                    _section(le, yb, zb, c, 0.0, tail_af, claf_tail),
                    _section(le, yb, zb + height, c, 0.0, tail_af, claf_tail),
                ]
            else:
                out += [
                    "SURFACE",
                    "Fin",
                    f"{TAIL_NCHORD} 1.0 {FIN_NSPAN} 1.0",
                    "COMPONENT",
                    "3",
                    _section(le, 0.0, 0.0, c, 0.0, tail_af, claf_tail),
                    _section(le, 0.0, height, c, 0.0, tail_af, claf_tail),
                ]
    # ----- Fuselage body (equivalent-area axisymmetric profile) -----
    if include_body:
        f = g["fuselage"]
        st = f["stations"]
        pts_top = []
        for s in st:
            a = section_area(f["cross_section"], s["width_mm"], s["height_mm"]) * 1e-6
            pts_top.append((s["x_mm"] * mm, math.sqrt(max(a, 0.0) / math.pi)))
        top = list(reversed(pts_top))
        bottom = [(x, -r) for x, r in pts_top[1:]]
        body = "fuselage\n" + "\n".join(f"{x:.6f} {z:.6f}" for x, z in top + bottom) + "\n"
        files["fuse.dat"] = body
        out += [
            "BODY",
            "Fuselage",
            f"{BODY_NODES} 1.0",
            "TRANSLATE",
            "0.0 0.0 0.0",
            "BFIL",
            "fuse.dat",
        ]
    return {
        "avl": "\n".join(out) + "\n",
        "files": files,
        "sref": s_ref,
        "cref": c_ref,
        "bref": b_ref,
        "has_wing_airfoil": wing_af is not None,
        "has_tail_airfoil": tail_af is not None,
    }


# ---------------------------------------------------------------------------
# Running AVL (inside the worker)
# ---------------------------------------------------------------------------


def _f(x: Any) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _avl_solve(req: dict[str, Any]) -> dict[str, Any]:  # pragma: no cover - worker side
    from optvl import OVLSolver

    with tempfile.TemporaryDirectory(prefix="avl-") as tmp:
        for name, text in req["files"].items():
            Path(tmp, name).write_text(text)
        Path(tmp, "design.avl").write_text(req["avl"])
        cwd = os.getcwd()
        os.chdir(tmp)
        try:
            solver = OVLSolver(geo_file="design.avl")
        finally:
            os.chdir(cwd)
        controls = solver.get_control_names()
        results = []
        for case in req["cases"]:
            ref = solver.get_reference_data()
            ref["XYZref"] = [case.get("xref", 0.0), 0.0, case.get("zref", 0.0)]
            solver.set_reference_data(ref)
            for name in controls:
                solver.set_control_deflection(name, 0.0)
            if "alpha" in case:
                solver.set_constraint("alpha", "alpha", float(case["alpha"]))
            else:
                solver.set_constraint("alpha", "CL", float(case["cl"]))
            if case.get("trim", False) and "elevator" in controls:
                solver.set_constraint("elevator", "Cm", 0.0)
            elif "elevator" in controls:
                solver.set_constraint("elevator", "elevator", float(case.get("elevator", 0.0)))
            solver.execute_run()
            tot = {k: _f(v) for k, v in solver.get_total_forces().items()}
            sd = {k: _f(v) for k, v in solver.get_stab_derivs().items()}
            cd = {k: _f(v) for k, v in solver.get_control_stab_derivs().items()}
            defl = {k: _f(v) for k, v in solver.get_control_deflections().items()}
            alpha = _f(solver.get_variable("alpha"))
            strips_raw = solver.get_strip_forces()
            strips: dict[str, Any] = {}
            for surf, data in strips_raw.items():
                strips[surf] = {
                    "x_le": [float(v) for v in data["X LE"]],
                    "y_le": [float(v) for v in data["Y LE"]],
                    "z_le": [float(v) for v in data["Z LE"]],
                    "chord": [float(v) for v in data["chord"]],
                    "width": [float(v) for v in data["width"]],
                    "cl": [float(v) for v in data["CL strip"]],
                    "cl_perp": [float(v) for v in data["CL perp"]],
                }
            surf_forces = {
                k: {kk: _f(vv) for kk, vv in v.items() if kk in ("CL", "CDi", "area")}
                for k, v in solver.get_surface_forces().items()
            }
            results.append(
                {
                    "name": case.get("name"),
                    "totals": tot,
                    "stab": sd,
                    "control_derivs": cd,
                    "deflections": defl,
                    "alpha_deg": alpha,
                    "strips": strips,
                    "surfaces": surf_forces,
                }
            )
        body = {}
        with contextlib.suppress(Exception):
            body = {
                k: {kk: _f(vv) for kk, vv in v.items()} for k, v in solver.get_body_forces().items()
            }
        return {"cases": results, "controls": controls, "body": body}


# ---------------------------------------------------------------------------
# Parent-side API
# ---------------------------------------------------------------------------


def run_avl(
    g: dict[str, Any],
    cases: list[dict[str, Any]],
    claf_wing: float = 1.0,
    claf_tail: float = 1.0,
    include_body: bool = True,
    timeout: float = 60.0,
    avl_input: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run trimmed AVL cases. Each case: ``{name, xref (m), cl | alpha, trim: bool}``.

    Returns ``{"cases": [...], "input": {...}}`` with per-case totals, derivatives, the elevator
    deflection, the strip loads and ``trim_converged``.
    """
    inp = avl_input or build_avl_input(g, claf_wing, claf_tail, include_body=include_body)
    raw = solver_worker().request(
        {"op": "avl", "avl": inp["avl"], "files": inp["files"], "cases": cases}, timeout=timeout
    )
    out_cases = []
    for case, res in zip(cases, raw["cases"], strict=True):
        tot = res["totals"]
        elev = res["deflections"].get("elevator")
        converged = (
            tot.get("CL") is not None
            and tot.get("Cm") is not None
            and ("cl" not in case or abs(tot["CL"] - case["cl"]) < 1e-3)
            and (not case.get("trim") or abs(tot["Cm"]) < 1e-3)
            and res.get("alpha_deg") is not None
            and abs(res["alpha_deg"]) < 30
            and (elev is None or abs(elev) < 60)
        )
        out_cases.append({**res, "case": case, "elevator_deg": elev, "trim_converged": converged})
    return {
        "cases": out_cases,
        "controls": raw["controls"],
        "body": raw.get("body", {}),
        "input": {"avl": inp["avl"]},
    }


def run_avl_text(
    avl_text: str,
    cases: list[dict[str, Any]],
    files: dict[str, str] | None = None,
    timeout: float = 60.0,
) -> list[dict[str, Any]]:
    """Run a hand-written AVL geometry (validation cases). Returns the raw per-case results."""
    raw = solver_worker().request(
        {"op": "avl", "avl": avl_text, "files": files or {}, "cases": cases}, timeout=timeout
    )
    return raw["cases"]


def wing_strips(res: dict[str, Any]) -> list[dict[str, float]]:
    """Wing strips of both halves as dicts (m): y, chord, width, cl, c*cl, sorted by y."""
    out = []
    for surf, s in res["strips"].items():
        if not surf.startswith("Wing"):
            continue
        for i in range(len(s["y_le"])):
            c = s["chord"][i]
            out.append(
                {
                    "y": s["y_le"][i] + 0.0,
                    "x_le": s["x_le"][i],
                    "chord": c,
                    "width": s["width"][i],
                    "cl": s["cl"][i],
                    "ccl": c * s["cl"][i],
                }
            )
    return sorted(out, key=lambda r: r["y"])


def tail_strips(res: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for surf, s in res["strips"].items():
        if surf.startswith("Wing"):
            continue
        for i in range(len(s["y_le"])):
            out.append(
                {
                    "surface": surf,
                    "y": s["y_le"][i],
                    "chord": s["chord"][i],
                    "width": s["width"][i],
                    "cl": s["cl"][i],
                }
            )
    return out


if __name__ == "__main__":  # pragma: no cover
    if "--worker" in sys.argv:
        _worker_main()

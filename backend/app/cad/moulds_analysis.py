"""Mould analysis on the part's outer surface: face sampling, draft report, silhouette and
parting plane, ray-cast demould check, mould wall thickness.

All functions are pure (numpy + OpenCascade queries) and unit-tested with simple solids
(``tests/cad/test_moulds.py``): a box pulled along z has 0 deg draft on its sides and is
flagged; a sphere split at its equator is demouldable with draft only below the minimum in the
parting band.

Conventions: ``pull`` is the unit vector from the parting plane towards the first half
(half "A"); the second half pulls along ``-pull``. A surface point p belongs to half A when
``(p - origin) . pull > 0``. Draft of a point in half A is ``asin(n . pull)`` (degrees) with n
the part's outward normal; negative draft is an undercut.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import cadquery as cq
import numpy as np

UNDERCUT_TOLERANCE_DEG = 0.5
FLAG_AREA_FRACTION = 0.002  # a face is flagged when this much of it is below the minimum
FLAG_AREA_MIN_MM2 = 5.0
SAMPLE_SPACING_MM = 3.0
SAMPLE_MAX = 6000


def parting_band(depth_mm: float, min_draft_deg: float) -> float:
    """Height of the band next to the parting line where a smooth convex surface is
    unavoidably below the minimum draft: about depth x sin(min draft) for a round section,
    x 1.5 for elliptic ones; at least 1 mm."""
    return max(1.0, 1.5 * depth_mm * math.sin(math.radians(min_draft_deg)))


@dataclass
class FaceSamples:
    index: int
    points: np.ndarray  # (n, 3)
    normals: np.ndarray  # (n, 3) outward unit normals
    weights: np.ndarray  # (n,) area of each sample, mm^2
    area: float
    geom: str


def _grid_counts(
    face: cq.Face, uv: tuple[float, float, float, float], n_min: int
) -> tuple[int, int]:
    """Samples along u and v: about one per ``SAMPLE_SPACING_MM`` of the iso-curve lengths
    (a long periodic airfoil loop needs many more than a short ruled strip), at least
    ``n_min`` along the longer direction and capped at ``SAMPLE_MAX`` in total."""
    from OCP.BRepGProp import BRepGProp_Face
    from OCP.gp import gp_Pnt, gp_Vec

    u0, u1, v0, v1 = uv
    prop = BRepGProp_Face(face.wrapped)
    p, vn = gp_Pnt(), gp_Vec()

    def length(along_u: bool) -> float:
        best = 0.0
        for frac in (0.25, 0.5, 0.75):
            pts = []
            for i in range(41):
                t = i / 40
                u = u0 + (u1 - u0) * (t if along_u else frac)
                v = v0 + (v1 - v0) * (frac if along_u else t)
                prop.Normal(u, v, p, vn)
                pts.append((p.X(), p.Y(), p.Z()))
            a = np.asarray(pts)
            best = max(best, float(np.linalg.norm(np.diff(a, axis=0), axis=1).sum()))
        return best

    lu, lv = length(True), length(False)
    nu = max(2, int(lu / SAMPLE_SPACING_MM))
    nv = max(2, int(lv / SAMPLE_SPACING_MM))
    if lu >= lv:
        nu = max(nu, n_min)
    else:
        nv = max(nv, n_min)
    k = math.sqrt(SAMPLE_MAX / (nu * nv)) if nu * nv > SAMPLE_MAX else 1.0
    return max(2, int(nu * k)), max(2, int(nv * k))


def face_samples(shape: cq.Shape, n_uv: int = 14) -> list[FaceSamples]:
    """Area-weighted point/normal samples of every face on a uv grid (cell centres)."""
    from OCP.BRepGProp import BRepGProp_Face
    from OCP.BRepTools import BRepTools
    from OCP.BRepTopAdaptor import BRepTopAdaptor_FClass2d
    from OCP.gp import gp_Pnt, gp_Pnt2d, gp_Vec
    from OCP.TopAbs import TopAbs_OUT

    out = []
    for i, f in enumerate(shape.Faces()):
        u0, u1, v0, v1 = BRepTools.UVBounds_s(f.wrapped)
        nu, nv = _grid_counts(f, (u0, u1, v0, v1), n_uv)
        du, dv = (u1 - u0) / nu, (v1 - v0) / nv
        prop = BRepGProp_Face(f.wrapped)
        trimmed = BRepTopAdaptor_FClass2d(f.wrapped, 1e-6)
        p, vn = gp_Pnt(), gp_Vec()
        pts, nrm, wts = [], [], []
        for a in range(nu):
            for b in range(nv):
                u = u0 + (a + 0.5) * du
                v = v0 + (b + 0.5) * dv
                if trimmed.Perform(gp_Pnt2d(u, v)) == TopAbs_OUT:
                    continue
                prop.Normal(u, v, p, vn)
                mag = vn.Magnitude()
                if mag <= 0:
                    continue
                pts.append((p.X(), p.Y(), p.Z()))
                nrm.append((vn.X() / mag, vn.Y() / mag, vn.Z() / mag))
                wts.append(mag * du * dv)
        if not pts:
            continue
        w = np.asarray(wts)
        area = float(f.Area())
        w = w * (area / w.sum()) if w.sum() > 0 else w
        out.append(
            FaceSamples(i, np.asarray(pts, float), np.asarray(nrm, float), w, area, f.geomType())
        )
    return out


def drop_caps(
    samples: list[FaceSamples], caps: list[tuple[np.ndarray, float]], tol: float = 0.05
) -> tuple[list[FaceSamples], list[int]]:
    """Remove planar end caps (open ends of the part: not moulded surface)."""
    keep: list[FaceSamples] = []
    dropped: list[int] = []
    for fs in samples:
        is_cap = any(
            fs.geom == "PLANE"
            and bool(np.all(np.abs(fs.points @ n - off) < tol))
            and abs(float(np.mean(fs.normals @ n))) > 0.99
            for n, off in caps
        )
        if is_cap:
            dropped.append(fs.index)
        else:
            keep.append(fs)
    return keep, dropped


def parting_plane(
    samples: list[FaceSamples],
    pull: np.ndarray,
    eps: float = 0.1,
    caps: list[tuple[np.ndarray, float]] | None = None,
) -> dict[str, Any]:
    """Parting plane at the maximum silhouette in the pull direction.

    Silhouette samples are those whose normal is (nearly) perpendicular to the pull. The plane
    offset is the area-weighted median of their heights; ``deviation_mm`` is how far the
    silhouette strays from that plane (0 for shapes symmetric about it)."""
    pts = np.vstack([s.points for s in samples])
    nrm = np.vstack([s.normals for s in samples])
    wts = np.concatenate([s.weights for s in samples])
    nd = nrm @ pull
    h = pts @ pull
    sil = np.abs(nd) < eps
    for n_cap, off in caps or []:
        sil &= np.abs(pts @ n_cap - off) > 2.0  # an open end is not a silhouette
    if not sil.any():
        sil = np.abs(nd) <= np.quantile(np.abs(nd), 0.02)
    # Keep only the maximum silhouette: candidates on the outline of the part seen along the
    # pull (a wall parallel to the pull inside the outline, e.g. a fillet meeting a fuselage
    # side, is not where the mould parts).
    e1 = np.cross(pull, [1.0, 0.0, 0.0])
    if np.linalg.norm(e1) < 0.1:
        e1 = np.cross(pull, [0.0, 1.0, 0.0])
    e1 /= np.linalg.norm(e1)
    e2 = np.cross(pull, e1)
    uv = np.column_stack([pts @ e1, pts @ e2])
    try:
        from scipy.spatial import ConvexHull

        hull = uv[ConvexHull(uv).vertices]
        a, b = hull, np.roll(hull, -1, axis=0)
        ab = b - a
        size = float(np.ptp(uv, axis=0).max())
        tol = max(2.0, 0.01 * size)
        q = uv[sil]
        t = np.clip(
            np.einsum("qmk,mk->qm", q[:, None, :] - a[None], ab)
            / np.maximum((ab * ab).sum(1), 1e-12),
            0,
            1,
        )
        dist = np.linalg.norm(q[:, None, :] - (a[None] + t[..., None] * ab[None]), axis=2).min(1)
        on_outline = dist <= tol
        if on_outline.any():
            idx = np.flatnonzero(sil)
            sil = np.zeros_like(sil)
            sil[idx[on_outline]] = True
    except Exception:
        pass
    hs, ws = h[sil], wts[sil]
    order = np.argsort(hs)
    cw = np.cumsum(ws[order])
    h0 = float(hs[order][np.searchsorted(cw, 0.5 * cw[-1])])
    dev = float(np.max(np.abs(hs - h0)))
    return {
        "offset_mm": h0,
        "deviation_mm": dev,
        "depth_positive_mm": float(h.max() - h0),
        "depth_negative_mm": float(h0 - h.min()),
        "silhouette_points": pts[sil],
    }


def draft_report(
    samples: list[FaceSamples],
    pull: np.ndarray,
    h0: float,
    min_draft_deg: float,
    band_mm: float,
    half_names: tuple[str, str] = ("A", "B"),
    describe: Any = None,
) -> dict[str, Any]:
    """Draft per face and half: min angle, area below the minimum (outside the parting band),
    undercut area; flagged faces. ``describe(points) -> str`` labels a face for people."""
    faces = []
    total = {"area": 0.0, "below": 0.0, "below_band": 0.0, "undercut": 0.0}
    halves_min = {half_names[0]: 90.0, half_names[1]: 90.0}
    colours = []
    for fs in samples:
        h = fs.points @ pull - h0
        nd = fs.normals @ pull
        rows = []
        for sgn, name in ((1.0, half_names[0]), (-1.0, half_names[1])):
            sel = (h * sgn) > 0 if sgn > 0 else (h * sgn) >= 0
            if not sel.any():
                continue
            draft = np.degrees(np.arcsin(np.clip(sgn * nd[sel], -1, 1)))
            w = fs.weights[sel]
            in_band = np.abs(h[sel]) < band_mm
            below = draft < min_draft_deg - 1e-9
            under = draft < -UNDERCUT_TOLERANCE_DEG
            out_band = ~in_band
            min_all = float(draft.min())
            min_out = float(draft[out_band].min()) if out_band.any() else None
            a = float(w.sum())
            a_below_out = float(w[below & out_band].sum())
            a_under = float(w[under & out_band].sum())
            flagged = a_below_out > max(FLAG_AREA_FRACTION * a, FLAG_AREA_MIN_MM2)
            rows.append(
                {
                    "half": name,
                    "area_mm2": round(a, 1),
                    "min_draft_deg": round(min_all, 2),
                    "min_draft_outside_band_deg": None if min_out is None else round(min_out, 2),
                    "area_below_min_mm2": round(float(w[below].sum()), 1),
                    "area_below_min_outside_band_mm2": round(a_below_out, 1),
                    "fraction_below_min_outside_band": round(a_below_out / a, 4) if a else 0.0,
                    "undercut_area_mm2": round(a_under, 1),
                    "flagged": bool(flagged),
                }
            )
            total["area"] += a
            total["below"] += float(w[below].sum())
            total["below_band"] += a_below_out
            total["undercut"] += a_under
            if min_out is not None:
                halves_min[name] = min(halves_min[name], min_out)
            colours.append(
                np.column_stack(
                    [fs.points[sel], draft, np.full(int(sel.sum()), sgn), in_band.astype(float)]
                )
            )
        if not rows:
            continue
        faces.append(
            {
                "face": fs.index,
                "surface": fs.geom,
                "area_mm2": round(fs.area, 1),
                "where": describe(fs.points) if describe else "",
                "centroid_mm": [
                    round(float(v), 1) for v in np.average(fs.points, axis=0, weights=fs.weights)
                ],
                "halves": rows,
                "min_draft_deg": min(r["min_draft_deg"] for r in rows),
                "flagged": any(r["flagged"] for r in rows),
            }
        )
    flagged = [f["face"] for f in faces if f["flagged"]]
    a = total["area"] or 1.0
    return {
        "min_draft_deg_setting": min_draft_deg,
        "parting_band_mm": band_mm,
        "faces": faces,
        "flagged_faces": flagged,
        "summary": {
            "faces": len(faces),
            "flagged": len(flagged),
            "area_mm2": round(total["area"], 0),
            "fraction_below_min": round(total["below"] / a, 4),
            "fraction_below_min_outside_band": round(total["below_band"] / a, 4),
            "undercut_fraction": round(total["undercut"] / a, 5),
            "min_draft_outside_band_deg": {k: round(v, 2) for k, v in halves_min.items()},
        },
        "_colours": np.vstack(colours) if colours else np.zeros((0, 6)),
    }


# ---------------------------------------------------------------------------
# Ray casting
# ---------------------------------------------------------------------------


def ray_first_hit(
    origins: np.ndarray,
    dirs: np.ndarray,
    tris: np.ndarray,
    chunk: int = 64,
    entering_only: bool = False,
) -> np.ndarray:
    """Distance to the first triangle hit along each ray (Moller-Trumbore), inf if none.

    ``tris``: (m, 3, 3), wound outward. ``entering_only`` counts only hits where the ray
    enters the solid (it meets the triangle's front side), which ignores grazing hits on
    surfaces parallel to the ray. Brute force in chunks; meshes here have ~10^4-10^5
    triangles."""
    o = np.asarray(origins, float)
    d = np.asarray(dirs, float)
    v0 = tris[:, 0]
    e1 = tris[:, 1] - v0
    e2 = tris[:, 2] - v0
    nrm = np.cross(e1, e2)
    nrm /= np.maximum(np.linalg.norm(nrm, axis=1, keepdims=True), 1e-12)
    out = np.full(len(o), np.inf)
    eps = 1e-9
    chunk = max(1, min(chunk, int(400_000 // max(len(tris), 1))))
    for s in range(0, len(o), chunk):
        oo = o[s : s + chunk, None, :]
        dd = d[s : s + chunk, None, :]
        pvec = np.cross(dd, e2[None])
        det = np.einsum("rmk,mk->rm", pvec, e1)
        ok = np.abs(det) > eps
        inv = np.where(ok, 1.0 / np.where(ok, det, 1.0), 0.0)
        tvec = oo - v0[None]
        u = np.einsum("rmk,rmk->rm", tvec, pvec) * inv
        del pvec
        qvec = np.cross(tvec, e1[None])
        del tvec
        v = np.einsum("rk,rmk->rm", dd[:, 0], qvec) * inv
        t = np.einsum("rmk,mk->rm", qvec, e2) * inv
        del qvec
        hit = ok & (u >= -1e-9) & (v >= -1e-9) & (u + v <= 1 + 1e-9) & (t > 1e-6)
        if entering_only:
            hit &= (dd[:, 0] @ nrm.T) < -0.05
        t = np.where(hit, t, np.inf)
        out[s : s + chunk] = t.min(axis=1)
    return out


def mesh_absolute(shape: cq.Shape, tol: float, ang: float = 0.15) -> None:
    """Triangulate with an absolute chordal deviation (CadQuery's ``tessellate`` meshes with a
    tolerance relative to the edge length, which is millimetres off on large faces); a later
    ``tessellate(tol)`` reuses this triangulation."""
    from OCP.BRepMesh import BRepMesh_IncrementalMesh
    from OCP.BRepTools import BRepTools

    BRepTools.Clean_s(shape.wrapped)
    BRepMesh_IncrementalMesh(shape.wrapped, tol, False, ang, True)


def mesh_triangles(shape: cq.Shape, tol: float = 0.2, ang: float = 0.2) -> np.ndarray:
    mesh_absolute(shape, tol, ang)
    verts, tris = shape.tessellate(tol, ang)
    v = np.array([(p.x, p.y, p.z) for p in verts], float)
    return v[np.asarray(tris, int)]


def demould_check(
    tris: np.ndarray,
    pull: np.ndarray,
    h0: float,
    band_mm: float,
    n_samples: int = 1500,
    hit_tol_mm: float = 0.1,
    caps: list[tuple[np.ndarray, float]] | None = None,
    seed: int = 7,
) -> dict[str, Any]:
    """Ray-casting demould test of both halves.

    Samples triangle centroids of the part's outer mesh (area-weighted). For a sample in half
    A, a ray starts just outside the surface and travels along +pull (the direction the mould
    half moves away): if it hits the part again, mould material there would be trapped (an
    undercut). Half B uses -pull. Samples inside the parting band (|h| < band) are skipped:
    there the surface is parallel to the pull by construction."""
    c = tris.mean(axis=1)
    n = np.cross(tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0])
    area = 0.5 * np.linalg.norm(n, axis=1)
    good = area > 1e-9
    n = n[good] / (2 * area[good])[:, None]
    c, area = c[good], area[good]
    keep = np.ones(len(c), bool)
    for nn, off in caps or []:
        keep &= ~((np.abs(c @ nn - off) < 0.05) & (np.abs(n @ nn) > 0.99))
    h = c @ pull - h0
    keep &= np.abs(h) >= band_mm
    idx = np.flatnonzero(keep)
    rng = np.random.default_rng(seed)
    if len(idx) > n_samples:
        p = area[idx] / area[idx].sum()
        idx = rng.choice(idx, size=n_samples, replace=False, p=p)
    res = {}
    for sgn, name in ((1.0, "A"), (-1.0, "B")):
        sel = idx[(h[idx] * sgn) > 0]
        if len(sel) == 0:
            res[name] = {"samples": 0, "undercut_samples": 0, "undercut_fraction": 0.0}
            continue
        d = np.tile(pull * sgn, (len(sel), 1))
        o = c[sel] + 0.5 * n[sel]
        dist = ray_first_hit(o, d, tris, entering_only=True)
        local = (n[sel] @ (pull * sgn)) < -math.sin(math.radians(UNDERCUT_TOLERANCE_DEG))
        trapped = (dist > hit_tol_mm) & np.isfinite(dist)
        res[name] = {
            "samples": len(sel),
            "undercut_samples": int(trapped.sum()),
            "undercut_fraction": float(area[sel][trapped].sum() / area[sel].sum()),
            "mesh_negative_draft_samples": int(local.sum()),
        }
    return res


def wall_thickness(
    part_tris: np.ndarray,
    mould_tris: np.ndarray,
    pull: np.ndarray,
    h0: float,
    sgn: float,
    band_mm: float,
    n_samples: int = 300,
    caps: list[tuple[np.ndarray, float]] | None = None,
    seed: int = 11,
) -> dict[str, Any]:
    """Mould wall thickness sampled along the part's outward normal (cavity face to back)."""
    c = part_tris.mean(axis=1)
    n = np.cross(part_tris[:, 1] - part_tris[:, 0], part_tris[:, 2] - part_tris[:, 0])
    area = 0.5 * np.linalg.norm(n, axis=1)
    good = area > 1e-9
    n = n[good] / (2 * area[good])[:, None]
    c, area = c[good], area[good]
    keep = (c @ pull - h0) * sgn > band_mm
    for nn, off in caps or []:
        keep &= ~((np.abs(c @ nn - off) < 0.05) & (np.abs(n @ nn) > 0.99))
    idx = np.flatnonzero(keep)
    if len(idx) == 0:
        return {"samples": 0}
    rng = np.random.default_rng(seed)
    if len(idx) > n_samples:
        p = area[idx] / area[idx].sum()
        idx = rng.choice(idx, size=n_samples, replace=False, p=p)
    o = c[idx] + 0.4 * n[idx]
    dist = ray_first_hit(o, n[idx], mould_tris)
    fin = dist[np.isfinite(dist)] + 0.4
    if not len(fin):
        return {"samples": len(idx), "hits": 0}
    return {
        "samples": len(idx),
        "hits": len(fin),
        "min_mm": round(float(fin.min()), 2),
        "p05_mm": round(float(np.quantile(fin, 0.05)), 2),
        "median_mm": round(float(np.median(fin)), 2),
    }

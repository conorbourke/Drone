"""PDF sheet per moulded part (A3 landscape, ReportLab, invariant mode).

Sheet 1: plan view along the pull direction with the parting line, the draft map (colour per
sample: undercut, below the minimum, below 5 deg, fine; the parting band in grey), flange bolts,
registration keys and tile cuts with tile labels; side view with the parting plane.
Sheet 2: draft report (summary and flagged faces), tile table (size, fit, print orientation,
mass and time), assembly order, laminate allowance and finishing notes.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
from reportlab.lib.pagesizes import A3, landscape
from reportlab.lib.units import mm
from reportlab.pdfgen.canvas import Canvas

from app.cad.drawings import DIM, INK, MARK, Sheet, pick_scale

PAGE_W, PAGE_H = landscape(A3)
RED = (0.80, 0.10, 0.10)
DARK_RED = (0.45, 0.0, 0.25)
AMBER = (0.90, 0.60, 0.05)
GREEN = (0.15, 0.60, 0.25)
GREY = (0.65, 0.65, 0.65)
PAGES = 2


def draft_colour(draft: float, min_draft: float, in_band: bool) -> tuple[float, float, float]:
    if in_band:
        return GREY
    if draft < -0.5:
        return DARK_RED
    if draft < min_draft:
        return RED
    if draft < 5.0:
        return AMBER
    return GREEN


def _title(c: Canvas, sheet: int, title: str, info: dict[str, Any], scale: float | None) -> None:
    w, h = 180 * mm, 32 * mm
    x0, y0 = PAGE_W - w - 10 * mm, 10 * mm
    c.setStrokeColorRGB(*INK)
    c.setLineWidth(0.8)
    c.rect(10 * mm, 10 * mm, PAGE_W - 20 * mm, PAGE_H - 20 * mm)
    c.rect(x0, y0, w, h)
    c.line(x0, y0 + 20 * mm, x0 + w, y0 + 20 * mm)
    c.line(x0 + 110 * mm, y0, x0 + 110 * mm, y0 + 20 * mm)
    c.setFillColorRGB(*INK)
    c.setFont("Helvetica-Bold", 11)
    c.drawString(x0 + 3 * mm, y0 + 24 * mm, title[:80])
    c.setFont("Helvetica", 7.5)
    rows = [
        f"Project: {info.get('project', '-')}",
        f"Version: {info.get('version', '-')}",
        f"Date: {info.get('date', date.today().isoformat())}",
    ]
    for i, r in enumerate(rows):
        c.drawString(x0 + 3 * mm, y0 + (14 - 6 * i) * mm, r)
    rows2 = [
        f"Scale: 1:{scale:g} (A3)" if scale else "Scale: none (tables)",
        "Units: mm, degrees",
        f"Sheet {sheet} of {PAGES}",
    ]
    for i, r in enumerate(rows2):
        c.drawString(x0 + 113 * mm, y0 + (14 - 6 * i) * mm, r)


def _text_block(
    c: Canvas,
    x: float,
    y: float,
    lines: list[str],
    size: float = 7.0,
    width_chars: int = 120,
    lead: float = 1.25,
) -> float:
    """Draw wrapped lines from the top-left (points); returns the y below the block."""
    import textwrap

    c.setFillColorRGB(*INK)
    for line in lines:
        bold = line.startswith("## ")
        txt = line[3:] if bold else line
        c.setFont("Helvetica-Bold" if bold else "Helvetica", size + (1.5 if bold else 0))
        for part in textwrap.wrap(txt, width_chars) or [""]:
            c.drawString(x, y, part)
            y -= size * lead
        if bold:
            y -= 1
    return y


def write_mould_sheet(
    path: Path, part: dict[str, Any], colours: np.ndarray, frame: Any, info: dict[str, Any]
) -> int:
    c = Canvas(str(path), pagesize=landscape(A3), invariant=1)
    c.setTitle(f"{part['label']} - mould sheet")
    c.setAuthor("VTOL designer")
    min_d = float(part["draft"]["min_draft_deg"])
    halves = part["halves"]
    names = [h["key"] for h in halves]
    poly = np.array(part["parting_line"]["polyline_mm"], float)
    pl = frame.local(poly)
    cols = colours[:: max(1, len(colours) // 6000)] if len(colours) else colours
    loc = frame.local(cols[:, :3]) if len(cols) else np.zeros((0, 3))

    # ---------- Sheet 1: views ----------
    u_all = np.concatenate([pl[:, 0], loc[:, 0]]) if len(loc) else pl[:, 0]
    v_all = np.concatenate([pl[:, 1], loc[:, 1]]) if len(loc) else pl[:, 1]
    cuts = part["tiling"]["cuts_mm"]
    u_lo = min(float(u_all.min()), *(t["u_range_mm"][0] for t in halves[0]["tiles"]))
    u_hi = max(float(u_all.max()), *(t["u_range_mm"][1] for t in halves[0]["tiles"]))
    ext_v = float(v_all.max() - v_all.min()) + 2 * part["mould"]["flange_width_mm"] + 20
    h_all = loc[:, 2] if len(loc) else np.array([0.0])
    ext_h = float(h_all.max() - h_all.min()) + 40
    box_w = PAGE_W / mm - 40
    scale = pick_scale((u_hi - u_lo + 40, ext_v + ext_h + 60), (box_w, 190))
    k = 1.0 / scale
    plan = Sheet(c, (20 * mm - (u_lo - 20) * k * mm, 0), scale)
    v_c = 0.5 * (float(v_all.max()) + float(v_all.min()))
    h_c = 0.5 * (float(loc[:, 2].max()) + float(loc[:, 2].min())) if len(loc) else 0.0
    plan_cy = (PAGE_H / mm - 30 - ext_v * k / 2) * mm
    plan.oy = plan_cy - v_c * k * mm
    side = Sheet(c, (plan.ox, 0), scale)
    side.oy = plan_cy - ((ext_v / 2 + ext_h / 2) * k + 15) * mm - h_c * k * mm

    _title(c, 1, f"{part['label']} - mould: parting line, draft, tiles", info, scale)
    c.setFont("Helvetica-Bold", 9)
    c.setFillColorRGB(*INK)
    c.drawString(
        20 * mm,
        PAGE_H - 18 * mm,
        f"Plan view along the pull direction ({names[0]} half shown above the parting line)",
    )
    for (u, v, _h), row in zip(loc, cols, strict=True):
        if row[4] <= 0:
            continue
        col = draft_colour(float(row[3]), min_d, bool(row[5]))
        c.setFillColorRGB(*col)
        x, y = plan.p(u, v)
        c.circle(x, y, 0.55, stroke=0, fill=1)
    plan.poly(pl[:, :2], close=True, width=0.9, color=INK)
    for b in part["mould"]["flange_bolts_mm"]:
        u, v, _ = frame.local(np.array([b]))[0]
        plan.circle((u, v), 2.75, 0.4, DIM)
    for kp in part["mould"]["registration_keys"]["positions_mm"]:
        u, v, _ = frame.local(np.array([kp]))[0]
        plan.circle((u, v), 5.0, 0.7, MARK)
    vmin, vmax = float(v_all.min()) - 30, float(v_all.max()) + 30
    for cu in cuts:
        plan.line((cu, vmin), (cu, vmax), 0.6, MARK, dash=(4, 2))
    for t in halves[0]["tiles"]:
        ua, ub = t["u_range_mm"]
        plan.text(((ua + ub) / 2, vmax + 4), t["label"], 7, MARK, "middle")
    plan.dim((u_lo, vmin - 8), (u_hi, vmin - 8), -6, f"{u_hi - u_lo:.0f}")
    # side view
    c.setFont("Helvetica-Bold", 9)
    c.setFillColorRGB(*INK)
    sx, sy = side.p(u_lo, float(h_all.max()) + 12)
    c.drawString(sx, sy, "Side view: draft of both halves (parting plane dashed)")
    for (u, _v, h), row in zip(loc, cols, strict=True):
        col = draft_colour(float(row[3]), min_d, bool(row[5]))
        c.setFillColorRGB(*col)
        x, y = side.p(u, h)
        c.circle(x, y, 0.5, stroke=0, fill=1)
    side.line((u_lo - 10, 0), (u_hi + 10, 0), 0.6, INK, dash=(5, 2))
    side.text((u_hi + 12, 1), "parting plane", 6.5, INK)
    for cu in cuts:
        side.line((cu, float(h_all.min()) - 10), (cu, float(h_all.max()) + 10), 0.5, MARK, (4, 2))
    # legend
    lx, ly = 20 * mm, 50 * mm
    legend = [
        (DARK_RED, "undercut (< -0.5°)"),
        (RED, f"below the {min_d:g}° minimum"),
        (AMBER, f"{min_d:g}°-5°"),
        (GREEN, "5° or more"),
        (GREY, f"parting band (±{part['draft']['parting_band_mm']:g} mm, not flagged)"),
    ]
    c.setFont("Helvetica", 7)
    for i, (col, txt) in enumerate(legend):
        c.setFillColorRGB(*col)
        c.circle(lx + 2 * mm, ly - i * 5 * mm, 1.5 * mm, stroke=0, fill=1)
        c.setFillColorRGB(*INK)
        c.drawString(lx + 6 * mm, ly - i * 5 * mm - 2, txt)
    c.drawString(
        lx + 70 * mm,
        ly,
        "Blue circles: M5 flange bolts. Red circles: registration "
        "cones/sockets. Dashed red: tile joints.",
    )
    c.drawString(lx + 70 * mm, ly - 5 * mm, part["parting_line"]["description"][:150])
    c.showPage()

    # ---------- Sheet 2: tables ----------
    _title(c, 2, f"{part['label']} - mould: draft report, tiles, assembly", info, None)
    dr = part["draft"]
    s = dr["summary"]
    lines = [
        "## Draft report",
        f"Minimum draft {dr['min_draft_deg']:g}° against each half's pull direction; "
        f"{s['faces']} faces sampled, {s['flagged']} flagged. Area below the minimum outside "
        f"the parting band: {s['fraction_below_min_outside_band'] * 100:.2f} % "
        f"({s['fraction_below_min'] * 100:.2f} % including the band); undercut area "
        f"{s['undercut_fraction'] * 100:.3f} %. Smallest draft outside the band: "
        + ", ".join(f"{k} {v:.1f}°" for k, v in s["min_draft_outside_band_deg"].items())
        + ".",
        dr["note"],
    ]
    for f in dr["flagged_detail"][:14]:
        lines.append(
            f"  Face {f['face']}: min {f['min_draft_deg']:.1f}°, "
            f"{f['area_below_min_mm2']:.0f} mm² below the minimum; {f['where']}"
        )
    if len(dr["flagged_detail"]) > 14:
        lines.append(f"  ... and {len(dr['flagged_detail']) - 14} more (see the manifest).")
    dm = part["demould"]["halves"]
    lines.append(
        "Demould (ray casting): "
        + "; ".join(
            f"{k}: {v['undercut_samples']} of {v['samples']} samples trapped "
            f"({'demouldable' if v['demouldable'] else 'UNDERCUT'})"
            for k, v in dm.items()
        )
    )
    lines += ["", "## Tiles"]
    for h in halves:
        wt = h.get("wall_thickness_mm") or {}
        lines.append(
            f"{h['label']} - pull {h['pull_note']} STEP: {Path(h['step_file']).name}. Wall "
            f"sampled: min {wt.get('min_mm', '?')} mm, median {wt.get('median_mm', '?')} mm."
        )
        for t in h["tiles"]:
            po = t["print_orientation"]
            lines.append(
                f"  {t['label']}: {t['size_mm'][0]:.0f} x {t['size_mm'][1]:.0f} x "
                f"{t['size_mm'][2]:.0f} mm, {'fits' if t['fits'] else 'DOES NOT FIT'}; "
                f"{po['name']}; supports: {po['supports']}; about {t['estimated_mass_g']:.0f} g, "
                f"{t['estimated_print_time']}."
            )
    y = _text_block(c, 20 * mm, PAGE_H - 22 * mm, lines, 7.0, 200)
    lam = part["mould"]["laminate_allowance"]
    lines2 = [
        "",
        "## Assembly order",
        *[f"{i + 1}. {a}" for i, a in enumerate(part["assembly_order"])],
        "",
        "## Laminate allowance and notes",
        f"Laminate {lam['laminate_mm']} mm ({lam['layup']})"
        + (f", {lam['core']} (sandwich {lam['sandwich_mm']} mm)" if lam.get("core") else "")
        + f", {lam['direction']}.",
        f"Flange {part['mould']['flange_width_mm']:g} mm wide, "
        f"{part['mould']['flange_thickness_mm']:g} mm thick; {part['mould']['bolt']} holes at "
        f"{part['mould']['bolt_pitch_mm']:g} mm pitch; trim line "
        f"{part['mould']['trim_line']['offset_mm']:g} mm outside the net edge.",
        *part["print_notes"]["finishing"],
        *part.get("notes", []),
    ]
    _text_block(c, 20 * mm, y, lines2, 7.0, 200)
    c.showPage()
    c.save()
    return path.stat().st_size

"""Printing notes: one Markdown sheet per part and a combined PDF (one page per part)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen.canvas import Canvas

from app.cad.model import FILAMENTS, PRINT_PROFILES, SHELL_WALL_MM

TEMPERATURES = {
    "LW-PLA": (
        "Nozzle 230-250 °C: LW-PLA foams more the hotter it is. Start at 240 °C with the flow "
        "reduced to about 50 % and calibrate on a single-wall test cube so the wall measures "
        "0.4 mm (two walls = 0.8 mm). Bed 50-60 °C, part fan 50-100 %, retraction short "
        "(0.5-1 mm) or off with travel 'avoid crossing walls', no ironing. Foamed walls are "
        "about 0.7 g/cm³."
    ),
    "PETG": "Nozzle 235-250 °C, bed 70-80 °C, fan 30-50 %; textured PEI sheet or glue stick.",
    "PA-CF": (
        "Hardened steel nozzle. Dry the filament (70-80 °C for 8 h) and print from a dry box. "
        "Nozzle 270-290 °C, bed 80-100 °C (glue stick), fan low, enclosure closed."
    ),
    "ASA": "Nozzle 250-265 °C, bed 95-105 °C, enclosure closed, fan 0-30 %.",
}

POST = {
    "lw_surface": [
        "Remove any tree supports and lightly sand the joint faces flat (240 grit).",
        "Dry-fit the pieces on the spar tube before gluing; the keys should slide in by hand.",
        "Bond each joint with thin CA (activator on one face) or 30-minute epoxy in the glue "
        "grooves; press the faces together on the spar tube so the channel stays aligned.",
        "Glue the spar tube in with epoxy once all pieces of the panel are joined.",
        "Cut the aileron free along the marked hinge line only if you build a separate aileron; "
        "otherwise use the groove as a guide for a tape hinge.",
    ],
    "lw_shell": [
        "Sand the frame-ring faces flat; check the alignment pins enter the sockets.",
        "Bond the rings with 30-minute epoxy (fills the groove); tape the outside while it cures.",
        "Press M3 heat-set inserts into the flange holes with a soldering iron at 200 °C.",
    ],
    "petg_mount": [
        "Ream the tube bores with a drill of the tube diameter if they are tight.",
        "Bond to the tube with epoxy after test-fitting; tighten clamp bolts evenly.",
    ],
    "petg_light": [
        "Ream the boom bore with a drill of the boom diameter if it is tight.",
        "Drill the two rear bolt holes through the wing using the clamp as a jig (the wing "
        "piece has pilot holes), bond the lower half to the boom with epoxy, then bolt.",
    ],
    "pacf_mount": [
        "Ream the bores and the hinge-pin hole with a drill of the nominal size.",
        "Bond to the tube with epoxy; check the mechanism moves freely before the glue cures.",
    ],
    "asa_mount": [
        "Bond the firewall with epoxy; check motor screw length (no contact with windings)."
    ],
}


def part_note(part: dict[str, Any]) -> str:
    prof = PRINT_PROFILES[part["profile"]]
    fil = FILAMENTS[prof["filament"]]
    lines = [
        f"# {part['label']}",
        "",
        part.get("description", ""),
        "",
        f"- Filament: {fil['name']}",
        f"- Nozzle: {prof['nozzle_mm']} mm"
        + (" (hardened)" if prof["filament"] == "PA-CF" else ""),
        f"- Layer height: {prof['layer_mm']} mm",
        f"- Walls: {prof['walls']} perimeters of {prof['line_mm']} mm"
        + (
            f" (the {SHELL_WALL_MM} mm shell for LW-PLA: two perimeters)"
            if prof["filament"] == "LW-PLA"
            else ""
        ),
        f"- Infill: {prof['infill'] * 100:.0f} % {prof['infill_pattern']}",
        f"- Top/bottom layers: {prof['top_bottom_layers']}",
        f"- Copies to print: {part['quantity']}",
        "",
        "## Temperatures",
        "",
        TEMPERATURES[prof["filament"]],
        "",
        "## Pieces",
        "",
        "| Piece | Orientation | Size (mm) | Supports | Mass (g) | Print time |",
        "|---|---|---|---|---|---|",
    ]
    for pc in part["pieces"]:
        o = pc["orientation"]
        sup = (
            "tree supports from the plate only"
            if o["tilt_deg"] or o["lean_deg"]
            else (
                "none"
                if o["policy"] in ("le_down", "upright_x")
                else "only if the slicer flags overhangs"
            )
        )
        size = " x ".join(f"{v:.0f}" for v in pc["size_mm"])
        lines.append(
            f"| {pc['label']} | {o['description']} | {size} | {sup} | {pc['mass_g']:.0f} | "
            f"{pc['print_time_band']} |"
        )
    lines += ["", "## Orientation", ""]
    reasons = sorted({pc["orientation"]["reason"] for pc in part["pieces"]})
    lines += [f"- {r}" for r in reasons]
    joints = [j for pc in part["pieces"] for j in pc["joints"] if j.get("side") == "pins"]
    if joints:
        lines += ["", "## Joints", ""]
        for j in joints:
            ch = j.get("channel") or {}
            chan = f"; {ch['kind']} {ch['diameter_mm']:g} mm passes through" if ch else ""
            lines.append(
                f"- Station {j['station_mm']:.0f} mm: {len(j['keys'])} tapered keys "
                f"(clearance {j['keys'][0]['clearance_mm']} mm), {j['bonding']['kind']}{chan}."
            )
    lines += ["", "## Post-processing and bonding", ""]
    lines += [f"- {s}" for s in POST[part["profile"]]]
    lines += [
        "",
        "Masses and times are estimates (CAD volume x filament density with the wall and "
        "infill model; time from a typical volumetric rate for the filament).",
        "",
    ]
    return "\n".join(lines)


def write_notes(out_dir: Path, parts: list[dict[str, Any]], pdf_path: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    texts = []
    for part in parts:
        text = part_note(part)
        p = out_dir / f"{part['key']}.md"
        p.write_text(text, encoding="utf-8")
        paths.append(p)
        texts.append(text)
    c = Canvas(str(pdf_path), pagesize=A4, invariant=1)
    c.setTitle("Printing notes")
    _, h = A4
    for text in texts:
        y = h - 18 * mm
        for raw in text.splitlines():
            if raw.startswith("# "):
                c.setFont("Helvetica-Bold", 14)
                s = raw[2:]
            elif raw.startswith("## "):
                c.setFont("Helvetica-Bold", 10.5)
                y -= 2 * mm
                s = raw[3:]
            elif raw.startswith("|---"):
                continue
            else:
                c.setFont("Helvetica", 7.8)
                s = raw.replace("|", "  ").strip() if raw.startswith("|") else raw
            for chunk in _wrap(s, 120 if not raw.startswith("#") else 80):
                if y < 15 * mm:
                    c.showPage()
                    y = h - 18 * mm
                    c.setFont("Helvetica", 7.8)
                c.drawString(15 * mm, y, chunk)
                y -= 4.2 * mm
        c.showPage()
    c.save()
    return paths


def _wrap(s: str, width: int) -> list[str]:
    if len(s) <= width:
        return [s]
    out, line = [], ""
    for word in s.split(" "):
        if len(line) + len(word) + 1 > width:
            out.append(line)
            line = "    " + word
        else:
            line = f"{line} {word}" if line else word
    if line:
        out.append(line)
    return out

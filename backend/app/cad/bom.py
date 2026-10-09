"""Bill of materials (CSV).

Columns (docs/phases/PHASE5.md section 3): item, category, role, manufacturer, model, quantity,
unit mass g, line mass g, unit price €, line price €, best supplier, country, URL, last checked,
notes. The last row holds the totals of the mass and price columns.

Sources, in order:

* the Phase 4 parts selection (``parts_selection``: a list of dicts with ``role``, ``category``,
  ``manufacturer``, ``model``, ``quantity``, ``mass_g`` and either ``price_eur`` or
  ``listings`` [{supplier_name, country, url, price_eur, last_checked_at}]); the cheapest
  listing is the best supplier;
* generic components from the engine's mass model for every role that is not selected, marked
  "to be selected in Phase 4" (prices are rough estimates);
* the printed parts (filament mass and cost), the carbon tubes and rods with their cut lengths,
  fasteners and consumables.
"""

from __future__ import annotations

import csv
import math
from pathlib import Path
from typing import Any

from app.cad.model import FILAMENTS, CadModel
from app.engine.mass import carbon_tube_mass_per_m

COLUMNS = [
    "item",
    "category",
    "role",
    "manufacturer",
    "model",
    "quantity",
    "unit mass g",
    "line mass g",
    "unit price €",
    "line price €",
    "best supplier",
    "country",
    "URL",
    "last checked",
    "notes",
]
PHASE4 = "to be selected in Phase 4"
CARBON_DENSITY_G_CM3 = 1.55


def _row(**kw: Any) -> dict[str, Any]:
    r = {c: "" for c in COLUMNS}
    r.update(kw)
    q = float(r["quantity"] or 0)
    um = r["unit mass g"]
    up = r["unit price €"]
    r["line mass g"] = round(q * float(um), 2) if um != "" else ""
    r["line price €"] = round(q * float(up), 2) if up not in ("", None) else ""
    if um != "":
        r["unit mass g"] = round(float(um), 2)
    if up not in ("", None):
        r["unit price €"] = round(float(up), 2)
    return r


def _best_listing(item: dict[str, Any]) -> dict[str, Any]:
    listings = [x for x in item.get("listings") or [] if isinstance(x, dict)]
    priced = [x for x in listings if isinstance(x.get("price_eur"), int | float)]
    if priced:
        return min(priced, key=lambda x: (x["price_eur"], str(x.get("supplier_name", ""))))
    return listings[0] if listings else {}


def selection_rows(selection: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for item in selection:
        best = _best_listing(item)
        price = item.get("price_eur")
        if price is None:
            price = best.get("price_eur", item.get("price_eur_estimate"))
        rows.append(
            _row(
                item=item.get("label")
                or f"{item.get('manufacturer', '')} {item.get('model', '')}".strip(),
                category=item.get("category", ""),
                role=item.get("role", ""),
                manufacturer=item.get("manufacturer", ""),
                model=item.get("model", ""),
                quantity=int(item.get("quantity", 1)),
                **{
                    "unit mass g": float(item.get("mass_g", 0.0)),
                    "unit price €": "" if price is None else float(price),
                    "best supplier": best.get("supplier_name", item.get("supplier", "")),
                    "country": best.get("country", item.get("country", "")),
                    "URL": best.get("url", item.get("url", "")),
                    "last checked": str(
                        best.get("last_checked_at", item.get("last_checked", "")) or ""
                    ),
                    "notes": item.get("notes", "Phase 4 selection"),
                },
            )
        )
    return rows


def generic_rows(model: CadModel, selected_roles: set[str]) -> list[dict[str, Any]]:
    ms = model.mass
    comps = {c["key"]: c for c in ms["result"]["components"]}
    p = model.params
    rows = []

    def add(role: str, item: str, category: str, qty: int, mass: float, price: float, note: str):
        if role in selected_roles or qty <= 0:
            return
        rows.append(
            _row(
                item=item,
                category=category,
                role=role,
                manufacturer="generic",
                model=PHASE4,
                quantity=qty,
                **{
                    "unit mass g": mass,
                    "unit price €": price,
                    "notes": f"{PHASE4}; {note}",
                },
            )
        )

    m = model.lift_motor
    add(
        "lift_motor",
        "Lift motor",
        "motor",
        4,
        m["mass_g"],
        45.0,
        f"~{ms['lift_motor_max_power_w']:.0f} W, {m['mount_pattern']} mount, price estimate",
    )
    esc = (
        comps.get("escs_front", {}).get("mass_g", 0) + comps.get("escs_rear", {}).get("mass_g", 0)
    ) / 4
    add(
        "lift_esc",
        "Lift ESC",
        "esc",
        4,
        esc,
        25.0,
        f"≥ {ms['lift_esc_rating_a']:.0f} A, price estimate",
    )
    prop = (
        comps.get("props_front", {}).get("mass_g", 0) + comps.get("props_rear", {}).get("mass_g", 0)
    ) / 4
    add(
        "lift_propeller",
        "Lift propeller",
        "propeller",
        4,
        prop,
        12.0,
        f"{p['propulsion']['prop_diameter_mm']:.0f} x {p['propulsion']['prop_pitch_mm']:.0f} mm, "
        f"{p['propulsion']['prop_blades']} blades, 2 CW + 2 CCW, price estimate",
    )
    if model.pusher_motor is not None:
        add(
            "pusher_motor",
            "Pusher motor",
            "motor",
            1,
            model.pusher_motor["mass_g"],
            40.0,
            "price estimate",
        )
        add(
            "pusher_esc",
            "Pusher ESC",
            "esc",
            1,
            comps.get("pusher_esc", {}).get("mass_g", 20.0),
            25.0,
            "price estimate",
        )
        add(
            "pusher_propeller",
            "Pusher propeller",
            "propeller",
            1,
            comps.get("pusher_prop", {}).get("mass_g", 15.0),
            10.0,
            f"{p['pusher']['prop_diameter_mm']:.0f} mm, price estimate",
        )
    if model.tilt_servo is not None:
        ts = model.tilt_servo
        add(
            "tilt_servo",
            "Tilt servo",
            "servo",
            2,
            ts["mass_g"],
            25.0,
            f"{ts['class']} case {ts['length_mm']:g} x {ts['width_mm']:g} x "
            f"{ts['height_mm']:g} mm, metal gear, price estimate",
        )
    cs = model.control_servo
    add(
        "control_servo",
        "Control servo (2 aileron, 2 tail)",
        "servo",
        4,
        cs["mass_g"],
        12.0,
        f"{cs['class']} case, price estimate",
    )
    add(
        "battery",
        f"Battery {p['battery']['cells_series']}S{p['battery']['cells_parallel']}P "
        f"{p['battery']['chemistry']}",
        "battery",
        1,
        ms["pack"]["mass_g"],
        round(0.9 * ms["pack"]["energy_wh"], 0),
        f"{ms['pack']['energy_wh']:.0f} Wh, price estimate 0.9 €/Wh",
    )
    add(
        "avionics",
        "Autopilot, GPS, receiver, telemetry, power module",
        "avionics",
        1,
        p["allowances"]["avionics_g"],
        250.0,
        "owner allowance, price estimate",
    )
    return rows


def printed_rows(parts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for part in parts:
        mass = sum(pc["mass_g"] for pc in part["pieces"])
        fil = FILAMENTS[part["filament"]]
        rows.append(
            _row(
                item=part["label"],
                category="printed part",
                role=part["key"],
                manufacturer="3D printed",
                model=part["filament"],
                quantity=part["quantity"],
                **{
                    "unit mass g": mass,
                    "unit price €": mass / 1000 * fil["price_eur_per_kg"],
                    "notes": f"{len(part['pieces'])} piece(s); filament cost at "
                    f"{fil['price_eur_per_kg']:g} €/kg (estimate)",
                },
            )
        )
    return rows


def tube_rows(
    tubes: list[dict[str, Any]], extra_rods: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    groups: dict[tuple[str, float, float, float], int] = {}
    labels: dict[tuple[str, float, float, float], str] = {}
    for t in tubes:
        base = t["label"].split(" (")[0]
        key = (base, t["od_mm"], round(t.get("wall_mm", 0.0), 2), round(t["length_mm"] + 0.49))
        groups[key] = groups.get(key, 0) + 1
        labels[key] = base
    for r in extra_rods:
        key = (r["label"], r["od_mm"], 0.0, round(r["length_mm"]))
        groups[key] = groups.get(key, 0) + r.get("quantity", 1)
        labels[key] = r["label"]
    rows = []
    for key in sorted(groups):
        name, od, wall, length = key
        solid = wall <= 0 or od - 2 * wall < 0.5
        if solid:
            per_m = CARBON_DENSITY_G_CM3 * math.pi * (od / 10) ** 2 / 4 * 100  # g per metre
        else:
            per_m = carbon_tube_mass_per_m(od, wall)
        rows.append(
            _row(
                item=f"{name}: carbon {'rod' if solid else 'tube'} {od:g}"
                + ("" if solid else f" x {wall:g} wall")
                + f", cut to {length:.0f} mm",
                category="carbon tube" if not solid else "carbon rod",
                role=name.lower().replace(" ", "_"),
                manufacturer="generic",
                model=f"roll-wrapped {od:g} mm" if not solid else f"pultruded {od:g} mm",
                quantity=groups[key],
                **{
                    "unit mass g": per_m * length / 1000,
                    "unit price €": max(2.0, od * 1.0) * length / 1000 + 1.0,
                    "notes": f"cut length {length:.0f} mm; price estimate",
                },
            )
        )
    return rows


def hardware_rows(hardware: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for h in hardware:
        k = h["item"]
        if k in merged:
            merged[k]["quantity"] += h["quantity"]
            merged[k]["uses"].add(h.get("use", ""))
        else:
            merged[k] = {**h, "uses": {h.get("use", "")}}
    rows = []
    for k in sorted(merged):
        h = merged[k]
        rows.append(
            _row(
                item=k,
                category=h.get("category", "fastener"),
                role="hardware",
                manufacturer="generic",
                model=h.get("model", ""),
                quantity=h["quantity"],
                **{
                    "unit mass g": h["mass_g"],
                    "unit price €": h["price_eur"],
                    "notes": "for " + ", ".join(sorted(u for u in h["uses"] if u)),
                },
            )
        )
    return rows


CONSUMABLES = [
    (
        "Thin CA glue with activator",
        "consumable",
        1,
        10.0,
        9.0,
        "piece joints (wipe activator on one face)",
    ),
    ("30-minute epoxy", "consumable", 1, 15.0, 12.0, "spar tubes, booms and clamps"),
    ("Sandpaper 240 / 400", "consumable", 1, 0.0, 4.0, "joint faces and leading edges"),
]


def consumable_rows() -> list[dict[str, Any]]:
    return [
        _row(
            item=n,
            category=c,
            role="consumable",
            manufacturer="generic",
            quantity=q,
            **{
                "unit mass g": m,
                "unit price €": pr,
                "notes": f"{note}; mass is the amount left on the aircraft",
            },
        )
        for n, c, q, m, pr, note in CONSUMABLES
    ]


def build_bom(
    model: CadModel,
    parts: list[dict[str, Any]],
    tubes: list[dict[str, Any]],
    extra_rods: list[dict[str, Any]],
    hardware: list[dict[str, Any]],
    selection: list[dict[str, Any]] | None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    sel = [x for x in selection or [] if isinstance(x, dict)]
    sel_rows = selection_rows(sel)
    roles = {str(x.get("role")) for x in sel}
    rows = (
        sel_rows
        + generic_rows(model, roles)
        + printed_rows(parts)
        + tube_rows(tubes, extra_rods)
        + hardware_rows(hardware)
        + consumable_rows()
    )

    def total(rs: list[dict[str, Any]], col: str) -> float:
        return round(sum(float(r[col]) for r in rs if r[col] != ""), 2)

    totals = {
        "line_mass_g": total(rows, "line mass g"),
        "line_price_eur": total(rows, "line price €"),
        "selection_mass_g": total(sel_rows, "line mass g"),
        "selection_price_eur": total(sel_rows, "line price €"),
        "printed_mass_g": total(
            [r for r in rows if r["category"] == "printed part"], "line mass g"
        ),
        "rows": len(rows),
        "unpriced_rows": sum(1 for r in rows if r["line price €"] == ""),
    }
    return rows, totals


def write_bom(path: Path, rows: list[dict[str, Any]], totals: dict[str, Any]) -> int:
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS, lineterminator="\n")
        w.writeheader()
        for r in rows:
            w.writerow(r)
        w.writerow(
            {
                **{c: "" for c in COLUMNS},
                "item": "TOTAL",
                "line mass g": totals["line_mass_g"],
                "line price €": totals["line_price_eur"],
                "notes": "sum of the line columns; estimates marked in the notes",
            }
        )
    return path.stat().st_size

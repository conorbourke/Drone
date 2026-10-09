"""DXF (ezdxf) for flat parts that can be CNC-cut from carbon plate.

Plates: lift motor plate (and pusher plate) with the motor's bolt pattern, tilt-servo plate
with the servo cut-out and screw holes, and the nose-bay payload base with the four M3 holes on
the nose-bay bosses. Layers: CUT (outline and inner cut-outs), HOLES (drilled holes), NOTES.
Units millimetres ($INSUNITS = 4). Hole positions are returned for the manifest.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import ezdxf

from app.cad.model import CadModel

PLATE_THICKNESS_MM = 2.0


def _doc() -> Any:
    # R2010 keeps $INSUNITS (millimetres); R12 cannot store drawing units.
    doc = ezdxf.new("R2010", setup=False)
    doc.units = 4  # millimetres
    doc.header["$INSUNITS"] = 4
    doc.header["$MEASUREMENT"] = 1
    doc.layers.add("CUT", color=1)
    doc.layers.add("HOLES", color=3)
    doc.layers.add("NOTES", color=7)
    return doc


def _rounded_rect(msp: Any, w: float, h: float, r: float, layer: str = "CUT") -> None:
    """Closed polyline with bulged (arc) corners, centred on the origin."""
    b = math.tan(math.radians(90) / 4)
    x, y = w / 2, h / 2
    pts = [
        (x - r, -y, 0, 0, 0),
        (x, -y + r, 0, 0, 0),
        (x, y - r, 0, 0, 0),
        (x - r, y, 0, 0, 0),
        (-x + r, y, 0, 0, 0),
        (-x, y - r, 0, 0, 0),
        (-x, -y + r, 0, 0, 0),
        (-x + r, -y, 0, 0, 0),
    ]
    bulges = [b, 0, b, 0, b, 0, b, 0]
    pts = [(p[0], p[1], 0, 0, bulges[i]) for i, p in enumerate(pts)]
    msp.add_lwpolyline(pts, format="xyseb", close=True, dxfattribs={"layer": layer})


def _holes(msp: Any, holes: list[tuple[float, float, float]]) -> list[dict[str, float]]:
    out = []
    for x, y, d in holes:
        msp.add_circle((x, y), d / 2, dxfattribs={"layer": "HOLES"})
        out.append({"x_mm": round(x, 3), "y_mm": round(y, 3), "diameter_mm": round(d, 2)})
    return out


def _note(msp: Any, text: str, y: float) -> None:
    msp.add_text(text, height=2.5, dxfattribs={"layer": "NOTES"}).set_placement((0, y))


def _pattern_holes(pattern: dict[str, Any]) -> list[tuple[float, float, float]]:
    a, b = pattern["a_mm"] / 2, pattern["b_mm"] / 2
    d = pattern["screw_mm"] + 0.2
    c = math.sqrt(0.5)
    pts = [(a, 0.0), (-a, 0.0), (0.0, b), (0.0, -b)]
    return [(c * (x - y), c * (x + y), d) for x, y in pts]


def motor_plate(path: Path, motor: dict[str, Any], title: str) -> dict[str, Any]:
    doc = _doc()
    msp = doc.modelspace()
    d = motor["diameter_mm"] + 6.0
    msp.add_circle((0, 0), d / 2, dxfattribs={"layer": "CUT"})
    holes = [*_pattern_holes(motor["pattern"]), (0.0, 0.0, 10.0)]
    # two M3 holes to bolt the plate to the printed mount, clear of the motor pattern
    r = d / 2 - 3.5
    holes += [(r, 0.0, 3.2), (-r, 0.0, 3.2)]
    out = _holes(msp, holes)
    _note(
        msp,
        f"{title}: {PLATE_THICKNESS_MM:g} mm carbon plate, {motor['mount_pattern']}",
        -d / 2 - 6,
    )
    doc.saveas(path)
    return {"outline": f"circle diameter {d:.1f} mm", "holes": out}


def servo_plate(path: Path, servo: dict[str, Any], title: str) -> dict[str, Any]:
    doc = _doc()
    msp = doc.modelspace()
    sl, sw = servo["length_mm"], servo["width_mm"]
    w, h = sl + 16.0, sw + 12.0
    _rounded_rect(msp, w, h, 3.0)
    cw, ch = sl + 0.5, sw + 0.5
    msp.add_lwpolyline(
        [(-cw / 2, -ch / 2), (cw / 2, -ch / 2), (cw / 2, ch / 2), (-cw / 2, ch / 2)],
        close=True,
        dxfattribs={"layer": "CUT"},
    )
    out = _holes(msp, [(sl / 2 + 4.0, 0.0, 2.2), (-sl / 2 - 4.0, 0.0, 2.2)])
    _note(
        msp,
        f"{title}: {PLATE_THICKNESS_MM:g} mm carbon plate, servo {sl:g} x {sw:g} mm",
        -h / 2 - 6,
    )
    doc.saveas(path)
    return {"outline": f"{w:.1f} x {h:.1f} mm, R3 corners", "cutout": [cw, ch], "holes": out}


def nose_bay_base(path: Path, model: CadModel) -> dict[str, Any]:
    """Payload base plate on the four nose-bay bosses (positions match parts.build_nose_bay)."""
    doc = _doc()
    msp = doc.modelspace()
    L = model.fuselage.nose_bay_length
    wb = model.fuselage.nose_bay_width
    xs = (0.6 * L, 0.88 * L)
    ys = (-0.22 * wb, 0.22 * wb)
    cx = 0.5 * (xs[0] + xs[1])
    w = (xs[1] - xs[0]) + 16.0
    h = (ys[1] - ys[0]) + 16.0
    _rounded_rect(msp, w, h, 4.0)
    holes = [(x - cx, y, 3.2) for x in xs for y in ys]
    out = _holes(msp, holes)
    _note(
        msp,
        f"Nose-bay payload base: {PLATE_THICKNESS_MM:g} mm carbon plate, plate x = 0 at "
        f"{cx:.1f} mm from the nose, 4 x M3 into the heat-set bosses",
        -h / 2 - 6,
    )
    doc.saveas(path)
    return {"outline": f"{w:.1f} x {h:.1f} mm, R4 corners", "origin_x_mm": cx, "holes": out}


def write_dxfs(out_dir: Path, model: CadModel) -> list[dict[str, Any]]:
    out_dir.mkdir(parents=True, exist_ok=True)
    files = []
    p = out_dir / "motor_plate_lift.dxf"
    files.append(
        {
            "path": p,
            "part": "Lift motor plate",
            **motor_plate(p, model.lift_motor, "Lift motor plate"),
        }
    )
    if model.pusher_motor is not None:
        p = out_dir / "motor_plate_pusher.dxf"
        files.append(
            {
                "path": p,
                "part": "Pusher motor plate",
                **motor_plate(p, model.pusher_motor, "Pusher motor plate"),
            }
        )
    if model.tilt_servo is not None:
        p = out_dir / "servo_plate_tilt.dxf"
        files.append(
            {
                "path": p,
                "part": "Tilt servo plate",
                **servo_plate(p, model.tilt_servo, "Tilt servo plate"),
            }
        )
    p = out_dir / "servo_plate_control.dxf"
    files.append(
        {
            "path": p,
            "part": "Control servo plate",
            **servo_plate(p, model.control_servo, "Control servo plate"),
        }
    )
    p = out_dir / "nose_bay_base.dxf"
    files.append({"path": p, "part": "Nose-bay payload base", **nose_bay_base(p, model)})
    return files

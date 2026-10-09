"""Synthetic ArduPilot QuadPlane DataFlash logs (``.bin``) for tests and the fallback sample.

:class:`DataFlashWriter` writes the binary DataFlash format that ArduPilot's ``AP_Logger``
produces and pymavlink reads: every message is ``0xA3 0x95 <type id>`` followed by the packed
fields; ``FMT`` (type 128) messages define each type's name, format characters and columns, and
``UNIT`` / ``MULT`` / ``FMTU`` add units. The format strings below are copied from ArduPilot
4.x (``LogStructure.h`` files), so the parser sees the same shapes as in a real log.

:func:`synthesize_quadplane_log` flies a scripted QuadPlane mission through a simple kinematic
model: ground idle, take-off climb in QLOITER and a steady hover, a transition in FBWA, a
cruise leg with two turns, a back-transition and a landing hover in QLAND, touchdown and
disarm. Battery power in each phase is set by the caller (normally the design's predicted
powers multiplied by known factors), with AR(1) noise, so the calibration can be checked
against known answers. The battery follows a resting-voltage curve (:mod:`.ocv`
``OCV_TABLE``) with a series resistance, so voltage sag and the energy totals are consistent.
"""

from __future__ import annotations

import math
import random
import struct
from dataclasses import dataclass, field
from typing import Any, BinaryIO

from app.flightlog.ocv import ocv_per_cell

HEAD = b"\xa3\x95"
FMT_TYPE = 0x80

#: format char -> (struct code, multiplier applied by readers)
FMT_CHARS: dict[str, tuple[str, float | None]] = {
    "b": ("b", None),
    "B": ("B", None),
    "h": ("h", None),
    "H": ("H", None),
    "i": ("i", None),
    "I": ("I", None),
    "f": ("f", None),
    "d": ("d", None),
    "n": ("4s", None),
    "N": ("16s", None),
    "Z": ("64s", None),
    "c": ("h", 0.01),
    "C": ("H", 0.01),
    "e": ("i", 0.01),
    "E": ("I", 0.01),
    "L": ("i", 1e-7),
    "M": ("B", None),
    "q": ("q", None),
    "Q": ("Q", None),
}

#: name -> (format, columns, units, multipliers), from ArduPilot 4.x LogStructure.h files.
MESSAGES: dict[str, tuple[str, str, str, str]] = {
    "UNIT": ("QbZ", "TimeUS,Id,Label", "s--", "F--"),
    "MULT": ("Qbd", "TimeUS,Id,Mult", "s--", "F--"),
    "FMTU": ("QBNN", "TimeUS,FmtType,UnitIds,MultIds", "s---", "F---"),
    "PARM": ("QNff", "TimeUS,Name,Value,Default", "s---", "F---"),
    "MSG": ("QZ", "TimeUS,Message", "s-", "F-"),
    "VER": (
        "QBHBBBBIZHBBII",
        "TimeUS,BT,BST,Maj,Min,Pat,FWT,GH,FWS,APJ,BU,FV,IMI,ICI",
        "s-------------",
        "F-------------",
    ),
    "MODE": ("QMBB", "TimeUS,Mode,ModeNum,Rsn", "s---", "F---"),
    "EV": ("QB", "TimeUS,Id", "s-", "F-"),
    "ARM": ("QBIBB", "TimeUS,ArmState,ArmChecks,Forced,Method", "s----", "F----"),
    "BAT": (
        "QBfffffcfBBB",
        "TimeUS,Inst,Volt,VoltR,Curr,CurrTot,EnrgTot,Temp,Res,RemPct,H,SH",
        "s#vvAaXOw%-%",
        "F-000C0?0000",
    ),
    "CTUN": (
        "QccccffffBfi",
        "TimeUS,NavRoll,Roll,NavPitch,Pitch,ThO,RdO,ThD,As,AsT,E2T,GU",
        "sdddd---n--n",
        "FBBBB---00-B",
    ),
    "ARSP": (
        "QBffcffBBffB",
        "TimeUS,I,Airspeed,DiffPress,Temp,RawPress,Offset,U,H,Hp,TR,Pri",
        "s#nPOPP-----",
        "F-00B00-----",
    ),
    "ATT": (
        "QffffffB",
        "TimeUS,DesRoll,Roll,DesPitch,Pitch,DesYaw,Yaw,AEKF",
        "sddddhh-",
        "F000000-",
    ),
    "VIBE": ("QBfffI", "TimeUS,IMU,VibeX,VibeY,VibeZ,Clip", "s#ooo-", "F-000-"),
    "RCOU": (
        "QHHHHHHHHHHHHHH",
        "TimeUS,C1,C2,C3,C4,C5,C6,C7,C8,C9,C10,C11,C12,C13,C14",
        "sYYYYYYYYYYYYYY",
        "F--------------",
    ),
    "GPS": (
        "QBBIHBcLLeffffB",
        "TimeUS,I,Status,GMS,GWk,NSats,HDop,Lat,Lng,Alt,Spd,GCrs,VZ,Yaw,U",
        "s#-s-S-DUmnhnh-",
        "F--C-0BGGB000--",
    ),
    "QTUN": (
        "QffffffeccfBB",
        "TimeUS,ThI,ABst,ThO,ThH,DAlt,Alt,BAlt,DCRt,CRt,TMix,Trn,Ast",
        "s----mmmnn---",
        "F----00000---",
    ),
    "BARO": (
        "QBfffcfIffBf",
        "TimeUS,I,Alt,AltAMSL,Press,Temp,CRt,SMS,Offset,GndTemp,Health,CPress",
        "s#mmPOnsmO-P",
        "F-000B0C?0-0",
    ),
    "POS": ("QLLfff", "TimeUS,Lat,Lng,Alt,RelHomeAlt,RelOriginAlt", "sDUmmm", "FGG000"),
    "ESC": (
        "QBffffcfcf",
        "TimeUS,Instance,RPM,RawRPM,Volt,Curr,Temp,CTot,MotTemp,Err",
        "s#qqvAOaO%",
        "F-00--BCB-",
    ),
    "IMU": (
        "QBffffffIIfBBHH",
        "TimeUS,I,GyrX,GyrY,GyrZ,AccX,AccY,AccZ,EG,EA,T,GH,AH,GHz,AHz",
        "s#EEEooo--O--zz",
        "F-000000-----00",
    ),
}

UNITS = {
    "-": "",
    "#": "instance",
    "s": "s",
    "v": "V",
    "A": "A",
    "a": "Ah",
    "X": "Wh",
    "O": "degC",
    "w": "Ohm",
    "%": "%",
    "d": "deg",
    "n": "m/s",
    "m": "m",
    "P": "Pa",
    "o": "m/s/s",
    "Y": "us",
    "D": "deglatitude",
    "U": "deglongitude",
    "S": "satellites",
    "h": "degheading",
    "q": "rpm",
    "E": "rad/s",
    "z": "Hz",
}
MULTS = {"-": 0.0, "?": 1.0, "0": 1.0, "B": 1e-2, "C": 1e-3, "F": 1e-6, "G": 1e-7}


def _enc(v: Any, size: int) -> bytes:
    b = v.encode("ascii", "replace") if isinstance(v, str) else bytes(v)
    return b[:size].ljust(size, b"\0")


class DataFlashWriter:
    """Minimal DataFlash (.bin) writer: FMT/UNIT/MULT/FMTU headers then messages."""

    def __init__(
        self,
        fh: BinaryIO,
        messages: dict[str, tuple[str, str, str, str]] | None = None,
        omit: tuple[str, ...] = (),
    ):
        self.fh = fh
        self.omit = set(omit)
        self.defs = messages or MESSAGES
        self.types: dict[str, dict[str, Any]] = {}
        self._next = 0x81
        self.counts: dict[str, int] = {}
        self._fmt_struct = struct.Struct("<BB4s16s64s")
        self.define(
            "FMT", "BBnNZ", "Type,Length,Name,Format,Columns", type_id=FMT_TYPE, write=False
        )
        for name in ("UNIT", "MULT", "FMTU"):
            self.define(name, *self.defs[name][:2])
        for uid, label in UNITS.items():
            self.write("UNIT", TimeUS=0, Id=ord(uid), Label=label)
        for mid, mult in MULTS.items():
            self.write("MULT", TimeUS=0, Id=ord(mid), Mult=mult)
        for name, (fmt, cols, units, mults) in self.defs.items():
            if name in ("UNIT", "MULT", "FMTU"):
                continue
            self.define(name, fmt, cols)
            self.write(
                "FMTU",
                TimeUS=0,
                FmtType=self.types[name]["id"],
                UnitIds=units,
                MultIds=mults,
            )

    def define(
        self, name: str, fmt: str, columns: str, type_id: int | None = None, write: bool = True
    ) -> None:
        codes = []
        conv = []
        for c in fmt:
            code, mult = FMT_CHARS[c]
            codes.append(code)
            size = {"4s": 4, "16s": 16, "64s": 64}.get(code)
            conv.append((mult, size))
        st = struct.Struct("<" + "".join(codes))
        tid = type_id if type_id is not None else self._next
        if type_id is None:
            self._next += 1
        cols = columns.split(",")
        self.types[name] = {
            "id": tid,
            "struct": st,
            "cols": cols,
            "conv": conv,
            "codes": codes,
            "head": HEAD + bytes([tid]),
            "len": st.size + 3,
        }
        if write:
            self.fh.write(
                HEAD
                + bytes([FMT_TYPE])
                + self._fmt_struct.pack(
                    tid, st.size + 3, _enc(name, 4), _enc(fmt, 16), _enc(columns, 64)
                )
            )

    def write(self, name: str, **values: Any) -> None:
        if name in self.omit:
            return
        t = self.types[name]
        out: list[Any] = []
        for col, (mult, size), code in zip(t["cols"], t["conv"], t["codes"], strict=True):
            v = values.get(col, 0)
            if size is not None:
                out.append(_enc(v if v is not None else "", size))
            elif mult is not None:
                out.append(round(v / mult))
            elif code in ("f", "d"):
                out.append(float(v))
            else:
                out.append(round(v))
        self.fh.write(t["head"] + t["struct"].pack(*out))
        self.counts[name] = self.counts.get(name, 0) + 1


@dataclass
class SynthFlight:
    """A scripted QuadPlane flight. Powers are battery (terminal) powers in W."""

    hover_power_w: float
    cruise_power_w: float
    mass_kg: float = 3.3
    cruise_airspeed_mps: float = 16.0
    airspeed_min_mps: float = 12.0
    cells: int = 6
    capacity_mah: float = 5000.0
    chemistry: str = "lipo"
    r_pack_ohm: float = 0.02
    energy_factor: float = 1.0  # scales the resting-voltage curve (1.0 = the model's pack)
    avionics_w: float = 8.0
    ground_s: float = 4.0
    hover_alt_m: float = 30.0
    climb_rate_mps: float = 1.5
    takeoff_hold_s: float = 25.0
    accel_mps2: float = 1.5
    cruise_s: float = 180.0
    turns: int = 2
    decel_mps2: float = 1.2
    landing_hold_s: float = 20.0
    descent_rate_mps: float = 1.0
    noise: float = 0.03  # relative power noise (AR(1), 2 s correlation)
    front_share: float = 0.6
    vibe_hover: float = 12.0
    vibe_cruise: float = 7.0
    esc: bool = True
    imu_hz: float = 0.0  # extra IMU rows (to make large logs)
    seed: int = 1
    firmware: str = "ArduPlane V4.6.0 (synthetic, app/flightlog/synth.py)"
    extra_params: dict[str, float] = field(default_factory=dict)
    omit: tuple[str, ...] = ()  # message types left out (robustness tests)


QUADPLANE_PARAMS: dict[str, float] = {
    "Q_ENABLE": 1,
    "Q_FRAME_CLASS": 1,
    "Q_FRAME_TYPE": 1,
    "Q_M_PWM_MIN": 1000,
    "Q_M_PWM_MAX": 2000,
    "Q_M_SPIN_ARM": 0.1,
    "Q_M_SPIN_MIN": 0.15,
    "Q_ASSIST_SPEED": 0,
    "Q_TRANSITION_MS": 2000,
    "SERVO1_FUNCTION": 4,
    "SERVO2_FUNCTION": 19,
    "SERVO3_FUNCTION": 70,
    "SERVO4_FUNCTION": 21,
    "SERVO5_FUNCTION": 33,
    "SERVO6_FUNCTION": 34,
    "SERVO7_FUNCTION": 35,
    "SERVO8_FUNCTION": 36,
    "ARSPD_TYPE": 1,
    "ARSPD_USE": 1,
    "BATT_MONITOR": 4,
    "LOG_DISARMED": 0,
    "SCHED_LOOP_RATE": 300,
}


def synthesize_quadplane_log(path: str, f: SynthFlight) -> dict[str, Any]:
    """Write a synthetic QuadPlane flight to ``path``; return the truth used to make it."""
    rng = random.Random(f.seed)
    dt = 0.02
    g = 9.80665
    home = (-35.3632621, 149.1652374)
    params = {
        **QUADPLANE_PARAMS,
        "AIRSPEED_MIN": f.airspeed_min_mps,
        "AIRSPEED_CRUISE": f.cruise_airspeed_mps,
        "BATT_CAPACITY": f.capacity_mah,
        "LOG_BITMASK": 2 + 4 + 8 + 16 + 32 + 128 + 512 + 2048 + 8192,
        **f.extra_params,
    }
    # ---- schedule ----
    t_arm = 2.0
    t_to = t_arm + f.ground_s
    climb_s = f.hover_alt_m / f.climb_rate_mps
    t_hold = t_to + climb_s
    t_tr = t_hold + f.takeoff_hold_s
    acc_s = f.cruise_airspeed_mps / f.accel_mps2
    t_cr = t_tr + acc_s + 2.0  # +2 s lift-motor ramp-down after the airspeed is reached
    t_bt = t_cr + f.cruise_s
    dec_s = f.cruise_airspeed_mps / f.decel_mps2
    t_lh = t_bt + dec_s
    t_desc = t_lh + f.landing_hold_s
    t_td = t_desc + f.hover_alt_m / f.descent_rate_mps
    t_disarm = t_td + 3.0
    t_end = t_disarm + 2.0
    v_reach_t = t_tr + (f.airspeed_min_mps / f.accel_mps2)
    turn_times = [t_cr + f.cruise_s * (k + 1) / (f.turns + 1) for k in range(f.turns)]

    # weight per motor (quad X: 1 FR, 2 RL, 3 FL, 4 RR) and hover output fractions
    w = f.mass_kg * g
    tmax = 2.2 * w / 4
    hov_out = {
        1: math.sqrt(w * f.front_share / 2 / tmax),
        3: math.sqrt(w * f.front_share / 2 / tmax),
        2: math.sqrt(w * (1 - f.front_share) / 2 / tmax),
        4: math.sqrt(w * (1 - f.front_share) / 2 / tmax),
    }

    truth = {
        "hover_power_w": f.hover_power_w,
        "cruise_power_w": f.cruise_power_w,
        "mass_kg": f.mass_kg,
        "cruise_airspeed_mps": f.cruise_airspeed_mps,
        "phases": {
            "takeoff_hover": (t_to, t_tr),
            "transition": (t_tr, t_cr),
            "cruise": (t_cr, t_bt),
            "back_transition": (t_bt, t_lh),
            "landing_hover": (t_lh, t_td),
        },
    }

    with open(path, "wb") as fh:
        wr = DataFlashWriter(fh, omit=f.omit)
        us = lambda t: int(t * 1e6) + 1_000_000  # noqa: E731 (boot time starts at 1 s)

        wr.write("MSG", TimeUS=us(0.0), Message=f.firmware)
        wr.write("MSG", TimeUS=us(0.0), Message="QuadPlane Frame: QUAD/X")
        wr.write("VER", TimeUS=us(0.0), BT=3, FWS=f.firmware, Maj=4, Min=6, BU=3)
        for k, v in params.items():
            wr.write("PARM", TimeUS=us(0.01), Name=k, Value=float(v), Default=float(v))
        wr.write("MODE", TimeUS=us(0.05), Mode=19, ModeNum=19, Rsn=1)

        soc = 1.0
        cap_as = f.capacity_mah / 1000 * 3600
        curr_tot = 0.0
        enrg_tot = 0.0
        noise = 0.0
        alpha = math.exp(-dt / 2.0)
        x = y = 0.0
        heading = 0.0
        v = 0.0
        alt = 0.0
        armed = False
        mode = 19
        clip = 0
        msgs_done: set[str] = set()
        t = 0.0
        next_t = {
            "ATT": 0.0,
            "BAT": 0.0,
            "CTUN": 0.0,
            "ARSP": 0.0,
            "VIBE": 0.0,
            "RCOU": 0.0,
            "GPS": 0.0,
            "QTUN": 0.0,
            "BARO": 0.0,
            "POS": 0.0,
            "ESC": 0.0,
            "IMU": 0.0,
        }
        period = {
            "ATT": 0.04,
            "BAT": 0.1,
            "CTUN": 0.1,
            "ARSP": 0.1,
            "VIBE": 0.1,
            "RCOU": 0.1,
            "GPS": 0.2,
            "QTUN": 0.04,
            "BARO": 0.1,
            "POS": 0.1,
            "ESC": 0.1,
            "IMU": 1 / f.imu_hz if f.imu_hz > 0 else 1e9,
        }
        while t < t_end:
            # ---- kinematics and power by phase ----
            vz = 0.0
            lift = 0.0  # fraction of the hover lift the motors provide
            thr_fw = 0.0
            roll = rng.gauss(0, 1.5)
            pitch = rng.gauss(0, 1.0)
            p_base = 0.0
            if t < t_arm:
                p_base = f.avionics_w
            elif t < t_to:
                if not armed:
                    armed = True
                    wr.write("EV", TimeUS=us(t), Id=10)
                    wr.write("ARM", TimeUS=us(t), ArmState=1, Method=2)
                lift = 0.1
                p_base = f.avionics_w + 0.04 * f.hover_power_w
            elif t < t_hold:
                vz = f.climb_rate_mps
                lift = 1.0
                p_base = f.hover_power_w + f.mass_kg * g * vz / 0.6
            elif t < t_tr:
                lift = 1.0
                p_base = f.hover_power_w
            elif t < t_cr:
                if mode != 5:
                    mode = 5
                    wr.write("MODE", TimeUS=us(t), Mode=5, ModeNum=5, Rsn=1)
                v = min(f.cruise_airspeed_mps, (t - t_tr) * f.accel_mps2)
                if t < v_reach_t:
                    lift = max(0.15, 1 - (v / f.airspeed_min_mps) ** 2)
                else:
                    if "reached" not in msgs_done:
                        msgs_done.add("reached")
                        wr.write(
                            "MSG",
                            TimeUS=us(t),
                            Message=f"Transition airspeed reached {v:.1f}",
                        )
                    lift = max(0.0, 0.15 * (1 - (t - v_reach_t) / 2.0))
                thr_fw = 0.75
                p_fw = f.cruise_power_w * (0.4 + 0.6 * v / f.cruise_airspeed_mps) + (
                    f.mass_kg * f.accel_mps2 * v / 0.5 if v < f.cruise_airspeed_mps else 0.0
                )
                p_base = lift * f.hover_power_w + p_fw
                pitch += 2.0
            elif t < t_bt:
                if "done" not in msgs_done:
                    msgs_done.add("done")
                    wr.write("MSG", TimeUS=us(t), Message="Transition done")
                v = f.cruise_airspeed_mps + rng.gauss(0, 0.15)
                thr_fw = 0.55
                p_base = f.cruise_power_w
                pitch += 3.0
                for tt in turn_times:
                    if tt <= t < tt + 6.0:
                        roll = 30.0 + rng.gauss(0, 2)
                        heading += 15.0 * dt
                        p_base = f.cruise_power_w * 1.15
            elif t < t_lh:
                if mode != 20:
                    mode = 20
                    wr.write("MODE", TimeUS=us(t), Mode=20, ModeNum=20, Rsn=1)
                v = max(0.0, f.cruise_airspeed_mps - (t - t_bt) * f.decel_mps2)
                lift = 0.6 + 0.4 * (1 - v / f.cruise_airspeed_mps)
                p_base = f.hover_power_w * (0.85 + 0.15 * (1 - v / f.cruise_airspeed_mps))
                pitch -= 4.0
            elif t < t_desc:
                v = 0.0
                lift = 1.0
                p_base = f.hover_power_w
            elif t < t_td:
                vz = -f.descent_rate_mps
                lift = 0.95
                p_base = f.hover_power_w - 0.3 * f.mass_kg * g * f.descent_rate_mps / 0.6
            elif t < t_disarm:
                vz = 0.0
                alt = 0.0
                lift = 0.1
                p_base = f.avionics_w + 0.04 * f.hover_power_w
            else:
                if armed:
                    armed = False
                    wr.write("EV", TimeUS=us(t), Id=11)
                    wr.write("ARM", TimeUS=us(t), ArmState=0, Method=2)
                p_base = f.avionics_w
            if t >= t_lh or t < t_tr:
                v = 0.0 if t >= t_lh or t < t_tr else v
            alt = max(0.0, alt + vz * dt)
            noise = alpha * noise + math.sqrt(1 - alpha * alpha) * rng.gauss(0, f.noise)
            p = max(1.0, p_base * (1 + noise))
            # ---- battery ----
            voc = f.cells * ocv_per_cell(f.chemistry, soc) * f.energy_factor
            disc = voc * voc - 4 * f.r_pack_ohm * p
            i = (voc - math.sqrt(max(disc, 0.0))) / (2 * f.r_pack_ohm)
            vt = voc - i * f.r_pack_ohm
            soc -= i * dt / cap_as
            curr_tot += i * dt / 3.6
            enrg_tot += vt * i * dt / 3600
            # ---- position ----
            hr = math.radians(heading)
            x += v * math.cos(hr) * dt
            y += v * math.sin(hr) * dt
            lat = home[0] + x / 111_320.0
            lng = home[1] + y / (111_320.0 * math.cos(math.radians(home[0])))
            vtol_active = lift > 0.0 and armed
            hov_scale = math.sqrt(lift) if lift > 0 else 0.0

            def due(name: str, now: float = t) -> bool:
                if now + 1e-9 >= next_t[name]:
                    next_t[name] += period[name]
                    return True
                return False

            if due("ATT"):
                wr.write(
                    "ATT",
                    TimeUS=us(t),
                    DesRoll=roll,
                    Roll=roll + rng.gauss(0, 0.3),
                    DesPitch=pitch,
                    Pitch=pitch + rng.gauss(0, 0.3),
                    DesYaw=heading % 360,
                    Yaw=heading % 360,
                    AEKF=3,
                )
            if due("BAT"):
                wr.write(
                    "BAT",
                    TimeUS=us(t),
                    Inst=0,
                    Volt=vt,
                    VoltR=voc,
                    Curr=i,
                    CurrTot=curr_tot,
                    EnrgTot=enrg_tot,
                    Temp=25.0 + 10 * (1 - soc),
                    Res=f.r_pack_ohm,
                    RemPct=int(max(0, soc) * 100),
                    H=1,
                    SH=0,
                )
            if due("CTUN"):
                wr.write(
                    "CTUN",
                    TimeUS=us(t),
                    NavRoll=roll,
                    Roll=roll,
                    NavPitch=pitch,
                    Pitch=pitch,
                    ThO=thr_fw * 100,
                    RdO=0.0,
                    ThD=thr_fw * 100,
                    As=v + rng.gauss(0, 0.2),
                    AsT=0,
                    E2T=0.0,
                    GU=0,
                )
            if due("ARSP"):
                wr.write(
                    "ARSP",
                    TimeUS=us(t),
                    I=0,
                    Airspeed=max(0.0, v + rng.gauss(0, 0.2)),
                    DiffPress=0.5 * 1.225 * v * v,
                    Temp=25.0,
                    RawPress=0.5 * 1.225 * v * v,
                    Offset=0.0,
                    U=1,
                    H=1,
                    Hp=1.0,
                    TR=1.0,
                    Pri=0,
                )
            if due("VIBE"):
                base = f.vibe_hover if lift > 0.2 else f.vibe_cruise
                if not armed:
                    base = 0.2
                wr.write(
                    "VIBE",
                    TimeUS=us(t),
                    IMU=0,
                    VibeX=abs(rng.gauss(base * 0.8, base * 0.1)),
                    VibeY=abs(rng.gauss(base * 0.8, base * 0.1)),
                    VibeZ=abs(rng.gauss(base, base * 0.12)),
                    Clip=clip,
                )
            if due("RCOU"):
                ch = {k: 1500 for k in range(1, 15)}
                ch[3] = int(1000 + 1000 * thr_fw) if armed else 1000
                for mn in range(1, 5):
                    out = hov_out[mn] * hov_scale * (1 + 0.01 * rng.gauss(0, 1))
                    if lift <= 0.1 and lift > 0:
                        out = 0.1
                    ch[4 + mn] = int(1000 + 1000 * min(1.0, out)) if armed and lift > 0 else 1000
                wr.write("RCOU", TimeUS=us(t), **{f"C{k}": v_ for k, v_ in ch.items()})
            if vtol_active and lift > 0.12 and due("QTUN"):
                wr.write(
                    "QTUN",
                    TimeUS=us(t),
                    ThI=0.5 * lift,
                    ABst=0.0,
                    ThO=sum(hov_out.values()) / 4 * hov_scale,
                    ThH=0.45,
                    DAlt=alt,
                    Alt=alt,
                    BAlt=alt,
                    DCRt=vz * 100,
                    CRt=vz * 100,
                    TMix=0.0,
                    Trn=2,
                    Ast=0,
                )
            elif not (vtol_active and lift > 0.12):
                next_t["QTUN"] = t + period["QTUN"]
            if due("GPS"):
                gms = int((1_400_000 + t) * 1000) % (7 * 86400 * 1000)
                wr.write(
                    "GPS",
                    TimeUS=us(t),
                    I=0,
                    Status=6 if t > 0.5 else 1,
                    GMS=gms,
                    GWk=2335,
                    NSats=16,
                    HDop=0.7,
                    Lat=lat,
                    Lng=lng,
                    Alt=100.0 + alt,
                    Spd=max(0.0, v + rng.gauss(0, 0.1)),
                    GCrs=heading % 360,
                    VZ=-vz,
                    Yaw=0.0,
                    U=1,
                )
            if due("BARO"):
                wr.write(
                    "BARO",
                    TimeUS=us(t),
                    I=0,
                    Alt=alt + rng.gauss(0, 0.05),
                    AltAMSL=100.0 + alt,
                    Press=101325 - 12 * alt,
                    Temp=25.0,
                    CRt=vz,
                    SMS=int(t * 1000),
                    Offset=0.0,
                    GndTemp=25.0,
                    Health=1,
                    CPress=101325 - 12 * alt,
                )
            if due("POS"):
                wr.write(
                    "POS",
                    TimeUS=us(t),
                    Lat=lat,
                    Lng=lng,
                    Alt=100.0 + alt,
                    RelHomeAlt=alt,
                    RelOriginAlt=alt,
                )
            if f.esc and due("ESC"):
                for inst in range(5):
                    if inst < 4:
                        frac = hov_out[inst + 1] * hov_scale if armed and lift > 0 else 0.0
                        cur = (p - thr_fw * f.cruise_power_w) / vt / 4 if frac > 0 else 0.0
                    else:
                        frac = thr_fw
                        cur = thr_fw * f.cruise_power_w / max(vt, 1) if thr_fw > 0 else 0.0
                    wr.write(
                        "ESC",
                        TimeUS=us(t),
                        Instance=inst,
                        RPM=frac * 9000,
                        RawRPM=frac * 9000,
                        Volt=vt,
                        Curr=max(0.0, cur),
                        Temp=30 + 10 * frac,
                        CTot=0.0,
                        MotTemp=0,
                        Err=0.0,
                    )
            if f.imu_hz > 0:
                tt = t
                while tt < t + dt and due("IMU", tt):
                    wr.write(
                        "IMU",
                        TimeUS=us(tt),
                        I=0,
                        GyrX=rng.gauss(0, 0.01),
                        GyrY=0.0,
                        GyrZ=0.0,
                        AccX=rng.gauss(0, 1),
                        AccY=rng.gauss(0, 1),
                        AccZ=-9.8 + rng.gauss(0, 1),
                        EG=0,
                        EA=0,
                        T=40.0,
                        GH=1,
                        AH=1,
                        GHz=int(f.imu_hz),
                        AHz=int(f.imu_hz),
                    )
                    tt += period["IMU"]
            t += dt
        truth["counts"] = wr.counts
        truth["energy_wh"] = enrg_tot
        truth["charge_mah"] = curr_tot
    return truth


def synthesize_from_analysis(
    path: str,
    analysis_result: dict[str, Any],
    *,
    mass_kg: float | None = None,
    hover_factor: float = 1.0,
    cruise_factor: float = 1.0,
    airspeed_mps: float | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """A synthetic flight of an analysed design: its predicted hover power at ``mass_kg`` x
    ``hover_factor`` and cruise power at ``airspeed_mps`` x ``cruise_factor``; the design's
    pack (cells, capacity, chemistry, resistance). Returns the truth, including the factors."""
    from app.flightlog.compare import Design

    d = Design(analysis_result)
    m = mass_kg or d.mass_kg
    v = airspeed_mps or d.v_design
    hover = d.hover(m)["power_w"]
    cruise = d.cruise(v, m)["power_w"]
    stall = analysis_result["summary"]["stall_speed"]["value"]
    flight = SynthFlight(
        hover_power_w=hover * hover_factor,
        cruise_power_w=cruise * cruise_factor,
        mass_kg=m,
        cruise_airspeed_mps=v,
        airspeed_min_mps=round(min(v - 1.0, max(stall * 1.1, 0.75 * v)), 1),
        cells=int(d.pack["cells_series"]),
        capacity_mah=d.pack["capacity_ah"] * 1000,
        chemistry=d.pack.get("chemistry", "lipo"),
        r_pack_ohm=d.pack["r_pack_ohm"],
        avionics_w=d.avionics_w,
        front_share=d.front_share,
        **kwargs,
    )
    truth = synthesize_quadplane_log(path, flight)
    truth.update(
        hover_factor=hover_factor,
        cruise_factor=cruise_factor,
        predicted_hover_w=hover,
        predicted_cruise_w=cruise,
        energy_factor=flight.energy_factor,
    )
    return truth

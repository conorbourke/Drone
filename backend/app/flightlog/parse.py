"""Streaming DataFlash parser: ArduPilot ``.bin`` (and text ``.log``) logs through pymavlink.

The parser reads the log once, message by message (pymavlink ``DFReader``), and keeps only:

* time series decimated to fixed 0.2 s buckets (5 Hz) per channel: mean, minimum, maximum and
  the last value in each bucket, with gaps as NaN;
* full-rate integrals that must not be decimated: battery energy (volts x amps, trapezoid) and
  charge (amps), stored as cumulative values at the end of each bucket;
* the events that phase detection needs (flight modes, text messages, arming), the parameters
  and a count of every message type.

Memory is bounded by the log's duration (about 40 floats per channel-second at 5 Hz), not its
size: pymavlink maps the file and builds an offset index, and the parser hands consumed pages
back to the kernel (``madvise(MADV_DONTNEED)``) so the mapped file does not stay resident.

Missing message types are tolerated: every channel is optional and the result lists what was
absent and what that means for the analysis.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import math
import mmap
import os
import re
import time
from array import array
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

BUCKET_S = 0.2  # 5 Hz decimation
MAX_TEXT_MESSAGES = 2000
MAX_MODE_EVENTS = 5000

ProgressFn = Callable[[float, str], None]

#: ArduPlane flight mode numbers (ArduPlane/mode.h, enum Mode::Number).
PLANE_MODES = {
    0: "MANUAL",
    1: "CIRCLE",
    2: "STABILIZE",
    3: "TRAINING",
    4: "ACRO",
    5: "FBWA",
    6: "FBWB",
    7: "CRUISE",
    8: "AUTOTUNE",
    10: "AUTO",
    11: "RTL",
    12: "LOITER",
    13: "TAKEOFF",
    14: "AVOID_ADSB",
    15: "GUIDED",
    16: "INITIALISING",
    17: "QSTABILIZE",
    18: "QHOVER",
    19: "QLOITER",
    20: "QLAND",
    21: "QRTL",
    22: "QAUTOTUNE",
    23: "QACRO",
    24: "THERMAL",
    25: "LOITERALTQLAND",
    26: "AUTOLAND",
}
VTOL_MODES = {17, 18, 19, 20, 21, 22, 23, 25}
#: Modes that fly on the wing (AUTO and GUIDED can also fly VTOL legs; the motor signals decide).
FIXED_WING_MODES = {0, 1, 2, 3, 4, 5, 6, 7, 8, 11, 12, 13, 14, 24, 26}

#: SERVOn_FUNCTION values (libraries/SRV_Channel/SRV_Channel.h).
MOTOR_FUNCTIONS = {
    **{33 + i: i + 1 for i in range(8)},
    **{82 + i: i + 9 for i in range(4)},
    **{160 + i: i + 13 for i in range(20)},
}
THROTTLE_FUNCTIONS = {70, 73, 74}
TILT_FUNCTIONS = {41, 45, 46, 47, 75, 76}

#: Message types the parser reads (others are skipped through pymavlink's offset index).
WANTED_TYPES = [
    "BAT",
    "CURR",
    "BAT2",
    "CTUN",
    "ARSP",
    "ATT",
    "VIBE",
    "RCOU",
    "GPS",
    "QTUN",
    "TILT",
    "ESC",
    "BARO",
    "POS",
    "TECS",
    "EV",
    "ARM",
    "MODE",
    "MSG",
    "PARM",
    "VER",
]

#: Channel name -> (unit, plain label).
CHANNELS: dict[str, tuple[str, str]] = {
    "voltage_v": ("V", "Battery voltage"),
    "voltage_rest_v": ("V", "Battery resting voltage (estimated by the autopilot)"),
    "current_a": ("A", "Battery current"),
    "power_w": ("W", "Battery power (volts x amps)"),
    "batt_temp_c": ("°C", "Battery temperature"),
    "energy_wh": ("Wh", "Energy used (integrated volts x amps)"),
    "charge_mah": ("mAh", "Charge used (integrated amps)"),
    "airspeed_sensor_mps": ("m/s", "Airspeed (sensor)"),
    "airspeed_est_mps": ("m/s", "Airspeed (autopilot estimate, CTUN)"),
    "groundspeed_mps": ("m/s", "Ground speed (GPS)"),
    "alt_m": ("m", "Altitude above home"),
    "roll_deg": ("°", "Roll"),
    "pitch_deg": ("°", "Pitch"),
    "vibe_x": ("m/s²", "Vibration X"),
    "vibe_y": ("m/s²", "Vibration Y"),
    "vibe_z": ("m/s²", "Vibration Z"),
    "clip": ("count", "Accelerometer clipping (cumulative)"),
    "throttle_fw": ("", "Forward-flight throttle (CTUN, 0-1)"),
    "throttle_vtol": ("", "VTOL throttle (QTUN, 0-1)"),
    "qtun": ("", "QTUN rows in the bucket (VTOL motors active)"),
    "tilt_deg": ("°", "Rotor tilt (0 vertical, 90 forward)"),
}


@dataclass
class Channel:
    """One decimated channel: per-bucket mean, min, max and last value (NaN where empty)."""

    mean: array = field(default_factory=lambda: array("d"))
    lo: array = field(default_factory=lambda: array("d"))
    hi: array = field(default_factory=lambda: array("d"))
    last: array = field(default_factory=lambda: array("d"))
    _b: int = -1
    _sum: float = 0.0
    _n: int = 0
    _lo: float = math.inf
    _hi: float = -math.inf
    _last: float = math.nan
    samples: int = 0

    def add(self, b: int, v: float) -> None:
        if v is None or not math.isfinite(v):
            return
        if b != self._b and b > self._b:
            self._flush()
            self._b = b
            # an out-of-order sample from an earlier bucket goes into the current one
        self._sum += v
        self._n += 1
        if v < self._lo:
            self._lo = v
        if v > self._hi:
            self._hi = v
        self._last = v
        self.samples += 1

    def _flush(self) -> None:
        if self._b < 0 or self._n == 0:
            return
        nan = math.nan
        while len(self.mean) < self._b:
            self.mean.append(nan)
            self.lo.append(nan)
            self.hi.append(nan)
            self.last.append(nan)
        self.mean.append(self._sum / self._n)
        self.lo.append(self._lo)
        self.hi.append(self._hi)
        self.last.append(self._last)
        self._sum, self._n, self._lo, self._hi = 0.0, 0, math.inf, -math.inf

    def finish(self, n_buckets: int) -> None:
        self._flush()
        nan = math.nan
        for arr in (self.mean, self.lo, self.hi, self.last):
            while len(arr) < n_buckets:
                arr.append(nan)
            del arr[n_buckets:]


@dataclass
class ParsedLog:
    """Everything the later stages need, with memory bounded by the flight duration."""

    path: str
    size_bytes: int
    bucket_s: float = BUCKET_S
    t0_us: int | None = None
    t_end_us: int | None = None
    n_buckets: int = 0
    channels: dict[str, Channel] = field(default_factory=dict)
    rcou: dict[int, Channel] = field(default_factory=dict)
    esc: dict[int, dict[str, Channel]] = field(default_factory=dict)
    counts: dict[str, int] = field(default_factory=dict)
    params: dict[str, float] = field(default_factory=dict)
    modes: list[dict[str, Any]] = field(default_factory=list)
    messages: list[dict[str, Any]] = field(default_factory=list)
    arm_events: list[dict[str, Any]] = field(default_factory=list)
    firmware: str | None = None
    gps_time_utc: str | None = None
    battery: dict[str, Any] = field(default_factory=dict)
    sources: dict[str, str] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    parse_s: float = 0.0

    def t(self, i: int) -> float:
        """Centre of bucket ``i`` in seconds from the start of the log."""
        return (i + 0.5) * self.bucket_s

    @property
    def duration_s(self) -> float:
        if self.t0_us is None or self.t_end_us is None:
            return 0.0
        return (self.t_end_us - self.t0_us) / 1e6

    def ch(self, name: str) -> Channel | None:
        c = self.channels.get(name)
        return c if c is not None and c.samples > 0 else None


def _f(m: Any, *names: str) -> float | None:
    """First present, finite numeric field among ``names``."""
    for n in names:
        v = getattr(m, n, None)
        if v is None:
            continue
        try:
            v = float(v)
        except (TypeError, ValueError):
            continue
        if math.isfinite(v):
            return v
    return None


def _instance(m: Any, *names: str) -> int:
    for n in names:
        v = getattr(m, n, None)
        if v is not None:
            try:
                return int(v)
            except (TypeError, ValueError):
                return 0
    return 0


GPS_EPOCH = dt.datetime(1980, 1, 6, tzinfo=dt.UTC)
GPS_LEAP_S = 18  # GPS - UTC since 2017 (IERS Bulletin C)


def _open_reader(path: str, progress: ProgressFn | None) -> Any:
    from pymavlink import DFReader

    with open(path, "rb") as fh:
        head = fh.read(8000)
    if not head.startswith(b"\xa3\x95") and b"FMT," in head:
        return DFReader.DFReader_text(path)
    reader = DFReader.DFReader_binary.__new__(DFReader.DFReader_binary)

    def index_progress(pct: int) -> None:
        # Hand the indexed part of the mapped file back to the kernel as the indexer moves on.
        dm = getattr(reader, "data_map", None)
        if dm is not None and pct % 5 == 0:
            upto = (reader.data_len * pct // 100) // mmap.PAGESIZE * mmap.PAGESIZE
            if upto > 0:
                with contextlib.suppress(OSError, ValueError, AttributeError):
                    dm.madvise(mmap.MADV_DONTNEED, 0, upto)
        if progress:
            progress(0.25 * pct / 100, "Indexing the log")

    reader.__init__(path, progress_callback=index_progress)
    # Drop the offset index of every type the parser never reads (IMU and friends are most of
    # a log): it is the bulk of the reader's memory.
    keep = {reader.name_to_id[t] for t in (*WANTED_TYPES, "STAT", "ORGN") if t in reader.name_to_id}
    for tid in range(len(reader.offsets)):
        if tid not in keep and reader.offsets[tid]:
            reader.offsets[tid] = []
    return reader


def parse_dataflash(path: str, progress: ProgressFn | None = None) -> ParsedLog:
    """Read ``path`` once and return the decimated series, integrals, events and parameters."""
    t_start = time.time()
    size = os.path.getsize(path)
    log = ParsedLog(path=path, size_bytes=size)
    for name in CHANNELS:
        log.channels[name] = Channel()
    reader = _open_reader(path, progress)
    if progress:
        progress(0.25, "Reading messages")

    chans = log.channels
    counts: dict[str, int] = {}
    bucket = BUCKET_S
    t0 = None
    t_last = None
    # battery integration state (instance 0 only: further monitors often watch the same pack)
    bat_prev: tuple[float, float, float] | None = None  # (t_s, power_w, current_a)
    energy_wh = 0.0
    charge_mah = 0.0
    loss_a2s = 0.0  # integral of I^2 dt, for the pack's internal losses
    bat_first: dict[str, float] = {}
    bat_last: dict[str, float] = {}
    bat_source = None
    gps_alt0 = None
    have_pos_alt = False
    have_baro_alt = False
    gps_time_done = False
    last_madvise = 0
    dm = getattr(reader, "data_map", None)
    last_report = 0.0

    while True:
        m = reader.recv_match(type=WANTED_TYPES)
        if m is None:
            break
        typ = m.get_type()
        counts[typ] = counts.get(typ, 0) + 1
        if typ == "PARM":
            name = getattr(m, "Name", None)
            val = _f(m, "Value")
            if name is not None and val is not None:
                log.params[str(name)] = val
            continue
        tus = getattr(m, "TimeUS", None)
        if tus is None:
            tms = getattr(m, "TimeMS", None)
            tus = tms * 1000 if tms is not None else None
        if tus is None:
            continue
        tus = int(tus)
        if t0 is None:
            t0 = tus
        if tus < t0:
            continue
        if t_last is None or tus > t_last:
            t_last = tus
        ts = (tus - t0) / 1e6
        b = int(ts / bucket)

        if typ in ("BAT", "CURR"):
            if _instance(m, "Inst", "Instance", "I") != 0:
                continue
            bat_source = bat_source or typ
            v = _f(m, "Volt")
            i = _f(m, "Curr")
            if v is not None:
                chans["voltage_v"].add(b, v)
            vr = _f(m, "VoltR")
            if vr is not None and vr > 0:
                chans["voltage_rest_v"].add(b, vr)
            if i is not None:
                chans["current_a"].add(b, i)
            temp = _f(m, "Temp")
            if temp is not None and temp != 0.0:
                chans["batt_temp_c"].add(b, temp)
            if v is not None and i is not None:
                p = v * i
                chans["power_w"].add(b, p)
                if bat_prev is not None:
                    dt_s = ts - bat_prev[0]
                    if 0 < dt_s < 5.0:  # longer gaps are not bridged
                        energy_wh += 0.5 * (p + bat_prev[1]) * dt_s / 3600
                        charge_mah += 0.5 * (i + bat_prev[2]) * dt_s / 3.6
                        loss_a2s += 0.5 * (i * i + bat_prev[2] ** 2) * dt_s
                bat_prev = (ts, p, i)
                chans["energy_wh"].add(b, energy_wh)
                chans["charge_mah"].add(b, charge_mah)
            for key in ("CurrTot", "EnrgTot"):
                val = _f(m, key)
                if val is not None:
                    bat_first.setdefault(key, val)
                    bat_last[key] = val
            for key in ("Volt", "VoltR", "Curr"):
                val = _f(m, key)
                if val is not None:
                    bat_first.setdefault(key, val)
                    bat_first.setdefault(key + "_t", ts)
        elif typ == "CTUN":
            a = _f(m, "As", "Aspd")
            if a is not None:
                chans["airspeed_est_mps"].add(b, a)
            th = _f(m, "ThO", "ThrOut")
            if th is not None:
                chans["throttle_fw"].add(b, th / 100 if abs(th) > 1.5 else th)
        elif typ == "ARSP":
            if _instance(m, "I", "Instance") != 0:
                continue
            a = _f(m, "Airspeed")
            if a is not None:
                chans["airspeed_sensor_mps"].add(b, a)
        elif typ == "ATT":
            r = _f(m, "Roll")
            p_ = _f(m, "Pitch")
            if r is not None:
                chans["roll_deg"].add(b, r)
            if p_ is not None:
                chans["pitch_deg"].add(b, p_)
        elif typ == "VIBE":
            if _instance(m, "IMU", "I") != 0:
                continue
            for ax in ("X", "Y", "Z"):
                val = _f(m, "Vibe" + ax)
                if val is not None:
                    chans["vibe_" + ax.lower()].add(b, val)
            clip = _f(m, "Clip")
            if clip is None:
                parts = [_f(m, f"Clip{k}") for k in range(3)]
                if any(x is not None for x in parts):
                    clip = sum(x for x in parts if x is not None)
            if clip is not None:
                chans["clip"].add(b, clip)
        elif typ == "RCOU":
            for k in range(1, 15):
                val = _f(m, f"C{k}")
                if val is None:
                    continue
                ch = log.rcou.get(k)
                if ch is None:
                    ch = log.rcou[k] = Channel()
                ch.add(b, val)
        elif typ == "GPS":
            if _instance(m, "I", "Instance") != 0:
                continue
            status = _f(m, "Status") or 0
            if status < 3:
                continue
            spd = _f(m, "Spd")
            if spd is not None:
                chans["groundspeed_mps"].add(b, spd)
            alt = _f(m, "Alt")
            if alt is not None and not have_pos_alt and not have_baro_alt:
                if gps_alt0 is None:
                    gps_alt0 = alt
                chans["alt_m"].add(b, alt - gps_alt0)
            if not gps_time_done:
                wk, ms = _f(m, "GWk", "Week"), _f(m, "GMS", "TimeMS")
                if wk and ms is not None and wk > 0:
                    utc = GPS_EPOCH + dt.timedelta(
                        weeks=wk, milliseconds=ms, seconds=-GPS_LEAP_S - ts
                    )
                    log.gps_time_utc = utc.isoformat().replace("+00:00", "Z")
                    gps_time_done = True
        elif typ == "POS":
            alt = _f(m, "RelHomeAlt")
            if alt is not None:
                if not have_pos_alt:
                    have_pos_alt = True
                    chans["alt_m"] = Channel()
                chans["alt_m"].add(b, alt)
        elif typ == "BARO":
            if have_pos_alt or _instance(m, "I", "Instance") != 0:
                continue
            alt = _f(m, "Alt")
            if alt is not None:
                if not have_baro_alt:
                    have_baro_alt = True
                    chans["alt_m"] = Channel()
                chans["alt_m"].add(b, alt)
        elif typ == "QTUN":
            chans["qtun"].add(b, 1.0)
            th = _f(m, "ThO")
            if th is not None:
                chans["throttle_vtol"].add(b, th)
        elif typ == "TILT":
            val = _f(m, "Tilt")
            if val is not None:
                chans["tilt_deg"].add(b, val)
        elif typ == "ESC":
            inst = _instance(m, "Instance", "I")
            d = log.esc.get(inst)
            if d is None:
                d = log.esc[inst] = {
                    "rpm": Channel(),
                    "curr": Channel(),
                    "volt": Channel(),
                    "temp": Channel(),
                }
            for key, fname in (
                ("rpm", "RPM"),
                ("curr", "Curr"),
                ("volt", "Volt"),
                ("temp", "Temp"),
            ):
                val = _f(m, fname)
                if val is not None:
                    d[key].add(b, val)
        elif typ == "MODE":
            num = getattr(m, "ModeNum", None)
            if num is None:
                raw = getattr(m, "Mode", None)
                num = raw if isinstance(raw, int) else None
            num = int(num) if num is not None else -1
            name = PLANE_MODES.get(num)
            if name is None:
                raw = getattr(m, "Mode", None)
                name = str(raw).upper() if raw is not None else f"MODE {num}"
            if len(log.modes) < MAX_MODE_EVENTS:
                log.modes.append({"t_s": ts, "num": num, "name": name, "reason": _f(m, "Rsn")})
        elif typ == "MSG":
            text = str(getattr(m, "Message", "") or "")
            if log.firmware is None and re.match(r"^Ardu\w+ V\d", text):
                log.firmware = text.strip()
            if len(log.messages) < MAX_TEXT_MESSAGES:
                log.messages.append({"t_s": ts, "text": text})
        elif typ == "VER":
            fws = getattr(m, "FWS", None)
            if fws:
                log.firmware = str(fws).strip()
        elif typ == "EV":
            eid = _f(m, "Id")
            if eid in (10.0, 11.0):
                log.arm_events.append({"t_s": ts, "armed": eid == 10.0, "source": "EV"})
        elif typ == "ARM":
            st = _f(m, "ArmState")
            if st is not None:
                log.arm_events.append({"t_s": ts, "armed": st > 0, "source": "ARM"})

        if dm is not None:
            off = getattr(reader, "offset", 0)
            if off - last_madvise > 32 * 1024 * 1024:
                upto = (off - 1024 * 1024) // mmap.PAGESIZE * mmap.PAGESIZE
                if upto > 0:
                    with contextlib.suppress(OSError, ValueError):
                        dm.madvise(mmap.MADV_DONTNEED, 0, upto)
                last_madvise = off
        if progress:
            frac = 0.25 + 0.7 * (getattr(reader, "percent", 0.0) or 0.0) / 100
            if frac - last_report >= 0.01:
                last_report = frac
                progress(min(frac, 0.95), "Reading messages")

    with contextlib.suppress(Exception):
        reader.close()

    log.t0_us = t0
    log.t_end_us = t_last
    log.counts = dict(sorted(counts.items()))
    n = int(log.duration_s / bucket) + 1 if t0 is not None else 0
    log.n_buckets = n
    for c in log.channels.values():
        c.finish(n)
    for c in log.rcou.values():
        c.finish(n)
    for d in log.esc.values():
        for c in d.values():
            c.finish(n)
    # Duplicate EV/ARM events (newer logs write both) collapse to state changes.
    log.arm_events.sort(key=lambda e: e["t_s"])
    collapsed: list[dict[str, Any]] = []
    for e in log.arm_events:
        if not collapsed or collapsed[-1]["armed"] != e["armed"]:
            collapsed.append(e)
    log.arm_events = collapsed

    log.battery = {
        "source": bat_source,
        "integrated_wh": energy_wh,
        "integrated_mah": charge_mah,
        "i2_dt_a2s": loss_a2s,
        "first": bat_first,
        "last": bat_last,
    }
    if have_pos_alt:
        log.sources["alt_m"] = "POS.RelHomeAlt"
    elif have_baro_alt:
        log.sources["alt_m"] = "BARO.Alt (barometric, relative to the ground at start-up)"
    elif gps_alt0 is not None:
        log.sources["alt_m"] = "GPS.Alt relative to the first GPS fix (less accurate)"
    log.parse_s = time.time() - t_start
    if progress:
        progress(0.96, "Detecting flight phases")
    return log

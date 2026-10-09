"""ArduPilot logging set-up for useful flight-data comparisons (Phase 6 "Set up logging" card).

``LOG_BITMASK`` bits are ArduPlane's (``ArduPlane/defines.h`` ``MASK_LOG_*`` and the parameter
documentation in ``ArduPlane/Parameters.cpp``, ArduPilot 4.x). ``QTUN`` (VTOL throttle,
25 Hz while the lift motors run), ``TILT`` (tiltrotors), ``ESC`` (whenever ESC telemetry
arrives), ``MODE``, ``MSG`` and ``PARM`` are written regardless of the bitmask.
"""

from __future__ import annotations

from typing import Any

#: ArduPlane LOG_BITMASK bits: bit -> (name, what it logs).
PLANE_LOG_BITS: dict[int, tuple[str, str]] = {
    0: ("Fast Attitude", "ATT at the fast loop rate (attitude during transitions)"),
    1: ("Medium Attitude", "ATT at 10 Hz"),
    2: ("GPS", "GPS position, ground speed and time"),
    3: ("Performance", "PM: loop timing, so slow logging can be spotted"),
    4: ("Control Tuning", "CTUN: airspeed estimate, throttle, roll and pitch demands"),
    5: ("Navigation Tuning", "NTUN: navigation and airspeed errors"),
    7: ("IMU", "IMU and VIBE: vibration levels and accelerometer clipping"),
    8: ("Mission Commands", "CMD: which mission item was flying"),
    9: ("Battery Monitor", "BAT: voltage, current, mAh and Wh used"),
    10: ("Compass", "MAG"),
    11: ("TECS", "TECS: speed and height control (airspeed demand)"),
    12: ("Camera", "CAM"),
    13: ("RC Input-Output", "RCIN/RCOU: motor and servo outputs (lift-motor state, balance)"),
    14: ("Rangefinder", "RFND"),
    19: ("Raw IMU", "high-rate raw IMU (large logs)"),
    20: ("Fullrate Attitude", "ATT at the full loop rate (large logs)"),
    21: ("Video Stabilization", ""),
    22: ("Fullrate Notch", "notch filter data (large logs)"),
}

#: Bits recommended for a QuadPlane comparison log.
QUADPLANE_BITS = [0, 1, 2, 3, 4, 5, 7, 8, 9, 11, 13]


def log_bitmask(bits: list[int]) -> int:
    return sum(1 << b for b in sorted(set(bits)))


def quadplane_log_bitmask() -> dict[str, Any]:
    value = log_bitmask(QUADPLANE_BITS)
    return {
        "value": value,
        "bits": [
            {"bit": b, "value": 1 << b, "name": PLANE_LOG_BITS[b][0], "logs": PLANE_LOG_BITS[b][1]}
            for b in QUADPLANE_BITS
        ],
        "computation": " + ".join(str(1 << b) for b in QUADPLANE_BITS) + f" = {value}",
        "explain": (
            "LOG_BITMASK is the sum of 2^bit for each log type wanted. This value enables fast "
            "and medium attitude, GPS, performance, control and navigation tuning, IMU "
            "(vibration), mission commands, battery, TECS and RC input/output; it leaves out "
            "compass, camera, rangefinder and the full-rate raw data that make logs large. "
            "ArduPilot's own suggestion of 65535 (all basic types) also works."
        ),
    }


def logging_parameters() -> list[dict[str, Any]]:
    """The parameters to set, each with the value and a one-line reason."""
    bm = quadplane_log_bitmask()
    return [
        {
            "param": "LOG_BITMASK",
            "value": bm["value"],
            "reason": "Logs attitude, GPS, CTUN, NTUN, IMU/VIBE, battery, TECS and RC outputs: "
            "everything the phase split and the comparison read (" + bm["computation"] + ").",
        },
        {
            "param": "LOG_DISARMED",
            "value": 0,
            "reason": "Log only while armed, so each flight is one compact log.",
        },
        {
            "param": "BATT_MONITOR",
            "value": "4 (analog voltage and current) or your power module's type",
            "reason": "Without a current sensor there is no power or energy to compare.",
        },
        {
            "param": "BATT_AMP_PERVLT / BATT_VOLT_MULT",
            "value": "calibrated",
            "reason": "Calibrate the current sensor against a clamp meter or a charger's mAh "
            "count: every power comparison inherits its error.",
        },
        {
            "param": "BATT_CAPACITY",
            "value": "your pack's mAh",
            "reason": "Lets the autopilot report the remaining percentage; the comparison uses "
            "the design's pack.",
        },
        {
            "param": "SERVO_BLH_TRATE (BLHeli) or SERVO_DSHOT_ESC + SERVO_BLH_BDMASK (DShot)",
            "value": "10 Hz telemetry / bidirectional DShot",
            "reason": "ESC telemetry (rpm, current, temperature per motor) shows motor balance "
            "and propeller loading; only if the ESCs support it.",
        },
        {
            "param": "ARSPD_TYPE (and ARSPD_USE = 1)",
            "value": "your airspeed sensor type",
            "reason": "An airspeed sensor makes the cruise comparison far better than the "
            "autopilot's synthetic airspeed or GPS ground speed, which wind biases.",
        },
        {
            "param": "ARSPD_RATIO (airspeed calibration)",
            "value": "calibrated in flight (ARSPD_AUTOCAL)",
            "reason": "A 5 % airspeed error moves the predicted cruise power by about 10-15 %.",
        },
        {
            "param": "Q_TILT_MASK / Q_TILT_TYPE (tilt layouts)",
            "value": "as configured",
            "reason": "Tiltrotors write TILT automatically; the mask tells the tool which motors "
            "tilt, so tilted motors in cruise are not counted as lifting.",
        },
    ]


DOWNLOAD_STEPS = [
    "SD card: with the aircraft powered off, take the autopilot's microSD card and copy the "
    "newest .BIN file from APM/LOGS/ (the number in LASTLOG.TXT is the last log).",
    "Mission Planner: connect over USB, open the DataFlash Logs tab, press 'Download DataFlash "
    "Log Via Mavlink' and pick the flight's log (the .bin is saved in the logs folder).",
    "QGroundControl: connect, open Analyze Tools > Log Download, Refresh, tick the log and "
    "Download.",
    "No extra software runs on the drone: ArduPilot writes these logs itself.",
]


def logging_guide() -> dict[str, Any]:
    return {
        "parameters": logging_parameters(),
        "log_bitmask": quadplane_log_bitmask(),
        "download": DOWNLOAD_STEPS,
        "always_logged": [
            "QTUN (VTOL throttle, while the lift motors run)",
            "TILT (tiltrotors)",
            "ESC (when ESC telemetry arrives)",
            "MODE, MSG, PARM, EV/ARM",
        ],
        "source": "ArduPlane LOG_BITMASK documentation (ArduPlane/Parameters.cpp) and "
        "MASK_LOG_* bits (ArduPlane/defines.h), ArduPilot 4.x.",
    }

"""Drive ArduPlane SITL (quadplane frame) through a scripted VTOL mission and keep the log.

Mission: arm in QLOITER, climb to ~30 m with RC throttle, switch to AUTO (fixed-wing waypoint
loop -> transition), fly a cruise leg, switch to QLAND (back-transition + vertical landing),
wait for the automatic disarm.
"""

from __future__ import annotations

import math
import sys
import time

from pymavlink import mavutil

CONN = sys.argv[1] if len(sys.argv) > 1 else "tcp:127.0.0.1:5760"
CRUISE_S = float(sys.argv[2]) if len(sys.argv) > 2 else 100.0
EXTRA_PARAMS = {}
for kv in sys.argv[3:]:
    k, v = kv.split("=")
    EXTRA_PARAMS[k] = float(v)

m = mavutil.mavlink_connection(CONN, source_system=255)
m.wait_heartbeat(timeout=60)
print("heartbeat", m.target_system, flush=True)
m.mav.request_data_stream_send(m.target_system, m.target_component, 0, 10, 1)
T0 = time.time()


def log(*a):
    print(f"[{time.time() - T0:7.1f}]", *a, flush=True)


def drain(timeout=0.05):
    t_end = time.time() + 0.25
    while time.time() < t_end:
        msg = m.recv_match(blocking=True, timeout=timeout)
        if msg is None:
            return
        if msg.get_type() == "STATUSTEXT":
            log("TEXT:", msg.text)


def set_param(name, value):
    for _ in range(5):
        m.mav.param_set_send(
            m.target_system,
            m.target_component,
            name.encode(),
            float(value),
            mavutil.mavlink.MAV_PARAM_TYPE_REAL32,
        )
        t = time.time()
        while time.time() - t < 2:
            msg = m.recv_match(type=["PARAM_VALUE", "STATUSTEXT"], blocking=True, timeout=0.5)
            if msg is None:
                continue
            if msg.get_type() == "STATUSTEXT":
                log("TEXT:", msg.text)
                continue
            if msg.param_id == name and abs(msg.param_value - value) < 1e-3 * max(1, abs(value)):
                return
    log("WARNING: could not set", name)


def set_mode(name):
    mode_id = m.mode_mapping()[name]
    for _ in range(10):
        m.mav.set_mode_send(
            m.target_system, mavutil.mavlink.MAV_MODE_FLAG_CUSTOM_MODE_ENABLED, mode_id
        )
        t = time.time()
        while time.time() - t < 1.5:
            hb = m.recv_match(type=["HEARTBEAT", "STATUSTEXT"], blocking=True, timeout=0.5)
            if hb is None:
                continue
            if hb.get_type() == "STATUSTEXT":
                log("TEXT:", hb.text)
                continue
            if hb.custom_mode == mode_id:
                log("mode", name)
                return
    raise SystemExit(f"could not set mode {name}")


rc = {i: 0 for i in range(1, 9)}


def send_rc():
    vals = [rc[i] for i in range(1, 9)]
    m.mav.rc_channels_override_send(m.target_system, m.target_component, *vals)


def state():
    pos = m.messages.get("GLOBAL_POSITION_INT")
    vfr = m.messages.get("VFR_HUD")
    hb = m.messages.get("HEARTBEAT")
    alt = pos.relative_alt / 1000 if pos else float("nan")
    aspd = vfr.airspeed if vfr else float("nan")
    armed = bool(hb.base_mode & mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED) if hb else False
    return alt, aspd, armed


def wait(seconds, cond=None, label=""):
    t = time.time()
    last = 0
    while time.time() - t < seconds:
        send_rc()
        drain(0.1)
        if time.time() - last > 5:
            last = time.time()
            log(label, "alt {:.1f} aspd {:.1f} armed {}".format(*state()))
        if cond and cond():
            return True
    return False


# --- parameters ---
params = {
    "LOG_DISARMED": 0,
    "LOG_FILE_DSRMROT": 1,
    "Q_RTL_MODE": 0,
    "WP_RADIUS": 60,
    "ARSPD_USE": 1,
    "RTL_AUTOLAND": 0,
    "Q_OPTIONS": 0,
}
params.update(EXTRA_PARAMS)
for k, v in params.items():
    set_param(k, v)
log("params set")

# --- mission: a fixed-wing loop around home, ~700 m legs at 40 m ----------------------------
home = None
while home is None:
    msg = m.recv_match(type="GLOBAL_POSITION_INT", blocking=True, timeout=5)
    if msg and msg.lat != 0:
        home = (msg.lat / 1e7, msg.lon / 1e7)
log("home", home)
dlat = 700 / 111_320.0

dlon = 700 / (111_320.0 * math.cos(math.radians(home[0])))
wps = [
    (home[0] + dlat, home[1]),
    (home[0] + dlat, home[1] + dlon),
    (home[0], home[1] + dlon),
    (home[0], home[1]),
]
items = [(home[0], home[1], 0, mavutil.mavlink.MAV_CMD_NAV_WAYPOINT)]
for la, lo in wps * 3:
    items.append((la, lo, 40, mavutil.mavlink.MAV_CMD_NAV_WAYPOINT))
items.append((0, 0, 0, mavutil.mavlink.MAV_CMD_DO_JUMP))


def upload_mission():
    m.mav.mission_clear_all_send(m.target_system, m.target_component)
    m.recv_match(type="MISSION_ACK", blocking=True, timeout=3)
    m.mav.mission_count_send(m.target_system, m.target_component, len(items))
    sent = set()
    t = time.time()
    while time.time() - t < 30:
        msg = m.recv_match(
            type=["MISSION_REQUEST", "MISSION_REQUEST_INT", "MISSION_ACK"], blocking=True, timeout=1
        )
        if msg is None:
            continue
        if msg.get_type() == "MISSION_ACK":
            log("mission ack", msg.type, "items", len(sent))
            return
        seq = msg.seq
        la, lo, alt, cmd = items[seq]
        p1 = 1 if cmd == mavutil.mavlink.MAV_CMD_DO_JUMP else 0
        p2 = -1 if cmd == mavutil.mavlink.MAV_CMD_DO_JUMP else 0
        m.mav.mission_item_int_send(
            m.target_system,
            m.target_component,
            seq,
            mavutil.mavlink.MAV_FRAME_GLOBAL_RELATIVE_ALT_INT,
            cmd,
            0,
            1,
            p1,
            p2,
            0,
            0,
            int(la * 1e7),
            int(lo * 1e7),
            alt,
        )
        sent.add(seq)
    raise SystemExit("mission upload failed")


upload_mission()

# --- wait for the EKF and arm ---
set_mode("QLOITER")
rc[3] = 1000
armed = False
t = time.time()
while time.time() - t < 240 and not armed:
    send_rc()
    m.mav.command_long_send(
        m.target_system,
        m.target_component,
        mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM,
        0,
        1,
        0,
        0,
        0,
        0,
        0,
        0,
    )
    wait(3, lambda: state()[2], "arming")
    armed = state()[2]
if not armed:
    raise SystemExit("could not arm")
log("ARMED")

# --- take-off hover in QLOITER --------------------------------------------------------------------
rc[3] = 1800
wait(60, lambda: state()[0] > 30, "climb")
rc[3] = 1500
wait(8, None, "hover")

# --- transition + cruise in AUTO ---
set_mode("AUTO")
wait(CRUISE_S, None, "cruise")

# --- back-transition and landing in QLAND ---------------------------------------------------------
set_mode("QLAND")
ok = wait(240, lambda: not state()[2], "qland")
log("disarmed" if ok else "still armed after timeout")
wait(3)

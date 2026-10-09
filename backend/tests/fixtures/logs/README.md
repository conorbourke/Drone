# Flight-log fixtures (Phase 6)

| File | What | Size |
|---|---|---|
| `sitl_quadplane.bin` | **Real ArduPilot log** from ArduPlane SITL, `quadplane` frame, scripted VTOL mission (the Phase 6 sample log) | 8.9 MB |
| `synthetic_quadplane.bin` + `.json` | Synthetic QuadPlane log written by `app/flightlog/synth.py` with known perturbations (hover power −5 %, cruise power +12 %, 3 % noise) and its truth | 1.6 MB |
| `test.BIN` | pymavlink's parser test log (ArduPlane V3.8.2-dev, 1 s on the ground), parser compatibility fixture | 64 kB |

## `sitl_quadplane.bin`: how it was produced

- **ArduPilot**: `https://github.com/ArduPilot/ardupilot` master at commit
  `568ef763623c1572edb17aa1d4b46614f70d8e9a` (2026-10-09), firmware string
  `ArduPlane V4.8.0-dev (568ef763)`. Cloned with `--depth 1` plus the submodules
  `modules/waf`, `modules/mavlink`, `modules/DroneCAN/{DSDL,libcanard,pydronecan,dronecan_dsdlc}`,
  `modules/littlefs`, `modules/lwip` and `modules/gtest`.
- **Build**: a Python 3.12 virtualenv with `empy==3.3.4 pexpect future pymavlink setuptools lxml
  dronecan`, then `./waf configure --board sitl && ./waf plane` (about 4 minutes on 4 cores).
- **Frame**: `-M quadplane` (ArduPilot's `SIM_QuadPlane`: the simple fixed-wing model with the
  Skywalker 2013 aerodynamic coefficients from `SIM_Plane.h`, wing area 0.45 m², span 1.88 m,
  plus a quad-X lift frame; mass `SIM_FRM_MASS` 3.0 kg × 1.5 = 4.5 kg; 3S pack, 12.6 V full,
  `SIM_BATT_CAP_AH` 5 Ah). Home CMAC, `-O -35.363261,149.165230,584,353`, `--speedup 5`.
- **Parameters**: `Tools/autotest/default_params/quadplane.parm` (AIRSPEED_MIN 13,
  AIRSPEED_CRUISE 25, Q_ENABLE 1, lift motors on SERVO5–8, ...) plus `sitl/logging.parm`
  (`BATT_MONITOR 4`, `BATT_CAPACITY 5000`, `LOG_BITMASK 11199` = the tool's recommended
  QuadPlane value, `LOG_FILE_RATEMAX 10` to keep every message type at ≤ 10 Hz and the file
  under 10 MB, `LOG_DISARMED 0`, `SIM_BATT_CAP_AH 5`), plus, set over MAVLink by the script,
  `LOG_FILE_DSRMROT 1`, `Q_RTL_MODE 0`, `WP_RADIUS 60`, `ARSPD_USE 1`, `RTL_AUTOLAND 0`,
  `Q_OPTIONS 0`.
- **Mission** (`sitl/fly_quadplane.py`, pymavlink): upload a fixed-wing loop of 700 m legs at
  40 m; arm in **QLOITER**; climb to about 32 m with RC throttle 1800 then hold (take-off hover);
  switch to **AUTO** (the first waypoint makes ArduPlane transition: "Transition started",
  "Transition airspeed reached 18.0", "Transition done"); cruise the loop at 25 m/s for about
  190 s; switch to **QLAND** (back-transition and vertical landing at the current position);
  automatic disarm after "Land complete". Script time runs in wall seconds at 5× speed-up.
- **Reproduce**: build SITL as above, then
  `ARDUPILOT=/path/to/ardupilot PYTHON=/path/to/venv/bin/python sitl/run_sitl.sh quadplane /tmp/run_quad 40`
  and copy `/tmp/run_quad/logs/00000001.BIN`. SITL is deterministic in lock-step but the
  MAVLink timing of the script is wall-clock, so phase times differ by a few seconds per run.

What the parser finds in it (`process_log`): 383 s log, phases take-off hover 49.8–110.8 s
(steady 531 W, 45 A), transition 110.8–120.2 s (peak 816 W), cruise 120.6–312.0 s at 25.0 m/s
(steady 147 W, 12 A), back-transition 312.0–317.2 s, landing hover 317.2–361.6 s (527 W);
24.51 Wh / 2065 mAh integrated against BAT.EnrgTot / CurrTot 24.51 Wh / 2065 mAh (−0.01 %).
SITL does not simulate vibration (VIBE ≈ 0.3 m/s²), its forward-motor current is a fixed
`20 A × throttle` and its hover current comes from the frame model, so the powers are the
simulator's, not a physical aircraft's: the sample proves the pipeline, not the engine.

### Tilt frame (`quadplane-tilttri`), not committed

The same script was run twice on `-M quadplane-tilttri` (Q_FRAME_CLASS 7, Q_TILT_MASK 3). With
the defaults the aircraft stayed at `Q_TILT_MAX` (45°) at 12.5 m/s, below AIRSPEED_MIN 13, and
never finished the transition; with `AIRSPEED_MIN=10 Q_TILT_MAX=60` it reported "Transition
airspeed reached 18.0" but restarted the transition and flew the whole leg at 14 m/s with the
rotors at 60° (the SITL tilttri model gives the tilted rotors all the forward thrust). The
parser labels that flight take-off hover / *forward flight on the lift motors (transition not
completed)* / landing hover, from the TILT angle and the motor outputs. Those logs (8 MB) are
not committed; rerun `sitl/run_sitl.sh quadplane-tilttri /tmp/run_tilt 30 AIRSPEED_MIN=10 Q_TILT_MAX=60`
to get one (SITL loads the frame's own defaults from its embedded ROMFS).

## `synthetic_quadplane.bin`

Regenerate with `uv run python tests/fixtures/logs/make_synthetic.py` (from `backend/`). The
default design is analysed with `run_analysis(mode="fast")`; the log flies it at 3.35 kg and
16 m/s with the predicted hover power × 0.95 and cruise power × 1.12 (AR(1) noise 3 %, 2 s
correlation), two 6 s turns in the cruise, the design's 6S 5 Ah LiPo on a typical
resting-voltage curve with its internal resistance. `synthetic_quadplane.json` holds the truth
(powers, factors, phase times). The calibration tests generate three more such logs on the fly.

## `test.BIN`

Copied unchanged from pymavlink's source tree (`tests/test.BIN`, pymavlink 2.4.x,
https://github.com/ArduPilot/pymavlink, LGPL-3.0 test data): ArduPlane V3.8.2-dev, one second
on the ground, 809 parameters, old field names (`CTUN.ThrOut`, `VIBE.Clip0..2`).

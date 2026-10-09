# Phase 6 contract: Flight data

Read `docs/BRIEF.md` ("Flight data"), `docs/phases/PHASE3.md` (analysis results and mission profile). This file adds to them.

**Acceptance (from the brief):** a sample ArduPilot log is parsed and compared against its design's predictions.

## 1. Logging guidance

A "Set up logging" card states the ArduPilot parameters to enable for useful comparisons, with a one-line reason each: `LOG_BITMASK` including fast attitude, GPS, PM, CTUN, NTUN, IMU, RCIN/RCOU, battery and ESC telemetry (state the value for a QuadPlane and how it is computed); `LOG_DISARMED = 0`; `BATT_MONITOR` for a current sensor and `BATT_CAPACITY`; `ESC telemetry` (`SERVO_BLH_TRATE` or DShot telemetry) if the ESCs support it; `ARSPD_TYPE` and calibration (an airspeed sensor makes cruise comparisons far better); `Q_TILT_*` logging for tilt layouts. How to download the `.bin` from the SD card or Mission Planner / QGroundControl, no extra software on the drone.

## 2. Log parsing and phase detection

- Upload `.bin` DataFlash logs (and `.log` text logs if easy) up to 200 MB per file to a version (`flight_logs` table: owner, project, version_id (FK `ON DELETE RESTRICT`), filename, size, firmware string, vehicle type, start time, duration, summary JSON, status; files under `{APP_DATA_DIR}/files/logs/`). Parsing with `pymavlink` (`mavutil.mavlink_connection`, DFReader) runs on the worker with progress; the parser streams messages and keeps decimated time series (≤ 5 Hz per channel) for charts plus per-phase statistics.
- Phase detection from `QTUN`/`MODE`/`MSG` (QuadPlane modes QHOVER/QLOITER/QLAND/QRTL vs FBWA/CRUISE/AUTO fixed-wing), `TECS`/`CTUN` airspeed, `Q_TILT` or motor outputs (`RCOU`) and transition messages ("Transition done", "Transition airspeed reached"): segments labelled take-off hover, transition, cruise, back-transition, landing hover, with start/end times; robust to logs without some messages (falls back to airspeed and throttle thresholds, stated).
- Per phase: duration; mean and percentile power (`BAT`/`CURR` volts × amps), current, voltage and sag, energy used (Wh), airspeed and ground speed, altitude, attitude (roll/pitch mean and RMS), vibration (`VIBE` X/Y/Z and clipping counts with ArduPilot's guidance thresholds), battery temperature if logged, motor outputs (balance across motors in hover hints at CG offset), ESC telemetry when present.

## 3. Comparison and calibration

- Compare measured against predicted for each phase: hover power and current, transition peak power, cruise power at the measured airspeed (re-evaluate the analysis' cruise power curve at that speed and the logged mass), energy per phase, endurance extrapolated from measured cruise power and the usable energy, and the stall/transition speeds observed. Each comparison: predicted (with its range), measured, error %, inside/outside the predicted range, and a plain explanation of likely causes when outside.
- Calibration factors per airframe version (`calibrations` table: version_id RESTRICT, factor name, value, uncertainty, n_logs, source log ids): hover power factor, cruise drag factor (cruise power ratio corrected for propulsive efficiency), structural mass factor (from built weights), and battery usable-energy factor. Combine multiple logs with weighted averaging and a stated uncertainty. The owner can apply the factors to the version and to new versions derived from it; Tier 1 and Phase 3 analyses use them when applied (shown as "calibrated with N flights") and the uncertainty bands narrow accordingly.

## 4. Built weights

Per version, a form listing every component from the Tier 1 mass breakdown and the parts list where the owner enters the weighed mass (g) and an optional photo note; totals compare to predicted; a structural mass factor per component group (printed shell, wing, tail, booms) feeds the calibration.

## 5. Sample log

- Preferred: a real ArduPilot QuadPlane log produced by ArduPilot's SITL simulator (build `arduplane` SITL from the ArduPilot repository with `waf`, run the `quadplane` frame (and `quadplane-tilttri` or the closest tilt frame if it exists) through a scripted mission: take-off in QLOITER, transition, a cruise leg, back-transition, QLAND), stored at `backend/tests/fixtures/logs/sitl_quadplane.bin` with a README stating how it was produced, the ArduPilot version and the parameters. Also keep `pymavlink`'s small `tests/test.BIN` (ArduPlane 3.8) as a parser compatibility fixture.
- Fallback if SITL cannot be built here: a synthetic DataFlash writer (`backend/app/flightlog/synth.py`) that emits valid FMT/FMTU/PARM/MODE/MSG/BAT/CTUN/ATT/VIBE/RCOU/GPS/QTUN messages for a full flight with the design's predicted powers perturbed by known factors (for example cruise +12 %, hover −5 %) and noise, readable by pymavlink, so the calibration test can check it recovers the factors.
- The sample is attached to a "Sample project" that the owner can open from the Flight data tab ("Try with a sample log").

## 6. User interface

Flight data tab: logging setup card; upload area per version; log list with status; per log a timeline with phases coloured, charts (power, current, voltage, airspeed, altitude, vibration) and the per-phase comparison table; calibration panel with factors, uncertainty and "Apply to this version"; built-weights form. Every number explained, metric units.

## 7. Tests

- pytest: parse the SITL (or synthetic) log and `test.BIN`; phase detection finds the expected segments; per-phase energy integrates correctly; comparison against the design's analysis; calibration recovers known factors from synthetic logs within tolerance; built weights feed the mass factor; upload limits; RESTRICT delete returns 409.
- Playwright `e2e/tests/phase6.spec.ts`: open the sample project, see the parsed phases and the predicted-versus-measured table, apply calibration and see the analysis numbers change.

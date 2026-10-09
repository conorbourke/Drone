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

## 8. As built

Built in commits `9af321d` (flight-log library and the SITL sample) and `86b1e39` (storage, worker job, endpoints and the Flight data tab). These notes record the choices the contract left open and where the build differs from it.

**Library** `backend/app/flightlog/`:

- `parse.py`: streaming DataFlash reader on pymavlink. It reads the log once and keeps 0.2 s (5 Hz) buckets per channel (mean, minimum, maximum, last), full-rate energy and charge integrals (so energy per phase does not depend on the decimation), the events phase detection needs, the parameters and a count of every message type. Memory grows with flight duration, not file size. Missing message types are tolerated and listed with what that means.
- `phases.py`: lift motors running (from `RCOU` on the motor channels, falling back to `QTUN`, then to the flight mode), airspeed (`ARSP`, then `CTUN`, then GPS ground speed, stated) and `MODE`/`MSG` events decide take-off hover, transition, cruise, back-transition and landing hover. Each segment records which signal decided its start and end. Short lift-motor blips in wing flight (Q assist) stay part of the cruise; a tilt aircraft that never finishes its transition is labelled "forward flight on the lift motors (transition not completed)".
- `stats.py`: per phase duration, power (mean, percentiles, peak), current, voltage and sag, energy (Wh), charge, airspeed and ground speed, altitude, climb rate, roll and pitch, vibration against ArduPilot's guidance (warn 30 m/s², fail 60 m/s²) and clipping, motor balance, ESC telemetry when present. "Steady" subsets exclude climbs, turns and speed changes; the comparison and calibration use those. The energy total is cross-checked against the autopilot's own `BAT` counters.
- `compare.py`: predictions are re-evaluated at the flight's conditions with the analysis' own models: hover power at the logged mass, cruise power at the measured airspeed and mass, transition peak, energy per phase, endurance from measured cruise power, stall and transition speeds. Each row has predicted value and range, measured value, error %, inside/outside, and likely causes when outside.
- `calibrate.py`: factors combined across logs by inverse-variance weighted mean with a scatter check (Birge ratio), each with an uncertainty.
- `synth.py`: synthetic QuadPlane DataFlash writer with known perturbations, used by the tests to check the calibration recovers them.
- `logging_guide.py`: the "Set up logging" card. Recommended QuadPlane `LOG_BITMASK` is 11199.

**Sample log** `backend/tests/fixtures/logs/sitl_quadplane.bin` (8.9 MB): a real ArduPlane 4.8.0-dev SITL flight on the `quadplane` frame (4.5 kg, 3S): take-off hover, transition, cruise at 25 m/s, back-transition, landing hover. How it was made is in that folder's `README.md`. SITL does not simulate vibration and its motor currents are simplified, so the sample proves the pipeline, not the engine. *Load sample flight* copies it into the project (each load is another 8.9 MB on the volume).

**Tables** (migration `0006`):

- `flight_logs`: owner, project (cascade), `version_id` (the version that flew, null for the draft; `ON DELETE RESTRICT`, so a version with logs cannot be deleted), `filename` (label only), `storage_name` (random file name under `{APP_DATA_DIR}/files/logs/`), `size_bytes`, `sample`, `takeoff_mass_kg`, `status`, `progress`, `stage`, `error`, `firmware`, `vehicle_type`, `log_start_at`, `flight_duration_s`, `summary`, `result` (phases, statistics, chart series), `comparison`, `analysis_id`, timings.
- `calibrations`: one row per applied factor of a project (unique per project and name): `name`, `value`, `uncertainty`, `n_logs`, `source_log_ids`, `details`, and `version_id` (`ON DELETE RESTRICT`) set when every source log flew the same version. Rows exist only while applied; *Undo* deletes them.
- `built_weights`: one row per weighed component of a project (unique per project and key): `label`, `group`, `subgroup` (printed shell, wing, tail, booms for structure), `predicted_g` (the model's figure when entered), `measured_g`, `note`.

**Worker job** (`backend/app/flight_data.py`). Reads the log, then compares it with the newest finished full analysis of the same source (the version that flew, or the draft) whose parameters and mission still match and which was not itself calibrated; otherwise it runs a quick analysis for the log. Comparisons are always against the uncalibrated model, so applying factors never compounds them. Changing the take-off mass or the version of a log, or *Compare again*, reads it again.

**Calibration.** Proposed factors come from every finished log of the project plus the structure built weights. Four factors can be applied: hover power, cruise drag, battery usable energy and structural mass. The cruise power ratio is shown but not applied (cruise drag carries the same measurement, corrected for propeller efficiency). A factor outside 0.5 to 2 is not offered, with a plain reason (most likely a wrong take-off mass, an uncalibrated current sensor or a log of another aircraft). Full analyses (*Analyse* on the Design tab) queued after *Apply* include the factors (they are part of the analysis hash), replace the matching uncertainty bands with the measured ones and say "Calibrated with N flights". The preview runs quick analyses of the draft with and without the factors.

**Endpoints** (router-level auth; `X-Requested-With: fetch` on state-changing calls):

- `GET /api/flight-data/guide` → logging card, upload limit, accepted extensions, whether the sample is installed.
- `POST /api/projects/{id}/flight-logs?filename=…&version_id=…&takeoff_mass_kg=…` with the raw file as the body (`application/octet-stream`, streamed to disk, at most 200 MB) → 202. 415 for `.tlog` (with how to get the `.bin`), another extension or a file that is not a DataFlash log; 413 above 200 MB; 422 for an empty file or a mass outside 0 to 30 kg; 503 while the worker starts.
- `POST /api/projects/{id}/flight-logs/sample` → 202.
- `GET /api/projects/{id}/flight-logs`, `GET /api/flight-logs/{lid}`, `GET /api/flight-logs/{lid}/series?points=600` (at most 2000 per channel), `PATCH /api/flight-logs/{lid}` `{takeoff_mass_kg?, version_id?}`, `POST /api/flight-logs/{lid}/reprocess`, `DELETE /api/flight-logs/{lid}`.
- `GET /api/projects/{id}/calibration` → `{proposed, applied}`; `GET …/calibration/preview`; `POST …/calibration` `{factors?: [name]}` (409 when nothing can be applied); `DELETE …/calibration`.
- `GET /api/projects/{id}/built-weights`; `PUT …/built-weights` `{items: [{key, measured_g, note?}]}` (422 for a key that is not in the design's mass breakdown).

**Flight data tab** (`frontend/src/tabs/FlightDataTab.tsx`, pieces in `frontend/src/tabs/flight/`, helpers `frontend/src/lib/flightData.ts`, API `frontend/src/api/flightData.ts`): the logging card; *Add a flight log* (file, the version that flew or the current draft, optional weighed take-off mass) and *Load sample flight*; the log list with status and progress; per log the phase timeline, per-phase numbers, SVG charts (power, current, voltage, speeds, altitude, vibration) with phases shaded and a keyboard-usable crosshair, and the *Predicted against measured* table; the calibration panel (factors with uncertainty, why a factor is not offered, preview, *Apply*, *Undo*); the built-weights form with totals and the structural mass factor.

**Tests**: `backend/tests/flightlog/` (parser, phases, statistics, comparison, calibration recovering known factors from synthetic logs), `backend/tests/test_phase6_api.py` (upload limits, `.tlog` refusal, job, calibration apply and undo, built weights, RESTRICT delete returns 409), `e2e/tests/phase6.spec.ts`.

**Known limits.**

- Telemetry logs (`.tlog`) are refused with an explanation. Only DataFlash logs (`.bin`, or `.log` text logs) are read, because a `.tlog` lacks the battery and tuning messages at a useful rate.
- Calibration and built weights are kept per project, not per version as the contract asked. A calibration records which version flew when all its logs share one, but it applies to every analysis of the project.
- Calibration is used only by the full analysis (*Analyse* on the Design tab), and by the full-scale checks when they reuse such an analysis. It is not used by the recommendation sweep (the ranked recommendations of that same analysis are worked out uncalibrated), by scale-to-weight, or by the in-browser quick estimate (Tier 1), which still show uncalibrated figures. The calibration preview is the exception: it runs its own quick analyses with the factors.
- Deleting a log does not change a calibration already applied; press *Undo* and apply again to drop that log's contribution.
- Each log stays on the volume until deleted (up to 200 MB each).

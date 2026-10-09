# Phase 2 contract: Inputs and design

Read `docs/BRIEF.md` (Phase 2 rows and "Workflow and tabs"), `docs/ARCHITECTURE.md` (the Phase 1 contract, still binding) and this file. This file adds to the architecture; where it changes something it says so.

**Acceptance (from the brief):** uploaded images produce a sensible starting model, edits update the model and estimates instantly, and two versions can be compared side by side.

Owner decisions recorded for this phase: rear tilt stays a full layout option, labelled "less common in ArduPilot than front tilt". The Claude API key may not be set yet: every Claude feature must show a plain message ("Add the ANTHROPIC_API_KEY secret in GitHub and redeploy to enable image reading") instead of failing.

## 1. Coordinate system and units (binding for every later phase)

- Origin at the fuselage nose tip on the centreline. **x** points aft, **y** to starboard (right wing), **z** up. Millimetres and degrees in documents; SI (m, kg, N, W) inside engine maths.
- Wing root leading edge sits at `(wing.x_le_mm, 0, wing.z_mm)`. Sweep is leading-edge sweep. Dihedral rotates the panel about the root chord line. Incidence and twist are about the quarter-chord, positive nose up; `twist_deg` is the tip incidence relative to the root (negative = washout).
- Booms run parallel to x at `y = ±booms.lateral_offset_mm`, `z = wing.z_mm`; the boom front is at `x = wing.x_le_mm + booms.x_offset_mm`. Motor stations are measured along the boom from its front (`motors.front_x_mm`, `motors.rear_x_mm`); motors sit `motors.height_mm` above the boom centreline. Tilt axis at `tilt.axis_x_mm` along the boom.
- Tail: `tail.arm_mm` is from the wing mean-aerodynamic-chord quarter point to the tail quarter-chord. `tail.height_mm` is the tail root above the boom/fuselage centreline.
- Payload (nose bay) mass sits at the centre of the nose bay: `x = nose_bay.length_mm / 2`.

## 2. Design parameters schema version 2

`DesignParameters.schema_version` becomes 2. `app/schemas/migrate.py` gets `upgrade_parameters` step 1→2 that adds the new blocks with defaults; stored rows are never rewritten. New fields (all with defaults, all with label, unit and plain-language description in the Pydantic field metadata so `/api/schema/design` serves them):

```
wing.twist_deg            0      tip incidence relative to root, negative = washout
booms.diameter_mm         20     outer diameter of the carbon boom tube
tail.v_angle_deg          40     for v_tail / inverted_v: angle of each panel above (or below) horizontal
tail.airfoil              "naca0009"
propulsion.prop_diameter_mm   330   the four lift (and, for tilt layouts, cruise) propellers
propulsion.prop_pitch_mm      140
propulsion.prop_blades        2
battery.chemistry         "lipo"   "lipo" | "li-ion"
battery.cells_series      6
battery.cells_parallel    1
battery.capacity_mah      5000     per parallel group
battery.x_mm              380      pack centre, from the nose
allowances.avionics_g     220      autopilot, GPS, receiver, telemetry radio, power module (replaced by real parts in Phase 4)
allowances.wiring_fraction 0.06    wiring, connectors and fasteners as a fraction of the empty mass
```

Validation stays shape-and-sanity only (positive values, `tip_chord <= root_chord`, `x_le + root_chord <= fuselage.length`, `cells_series` 1–14, `cells_parallel` 1–10). The 24 kg limit stays a check, never a validation error.

## 3. Airfoil library (backend)

- Coordinates for a small low-Reynolds library are copied (once, committed) from the AeroSandbox package's UIUC database (`aerosandbox/geometry/airfoil/airfoil_database/*.dat`, UIUC Airfoil Coordinates Database, Selig format) into `backend/app/engine/data/airfoils/`. Library: `sd7037`, `sd7062`, `e387`, `mh32`, `s3021`, `ag35`, `clarky`, `naca2412`, `naca4412` (wing) and `naca0009`, `naca0012` (tail; NACA 4-digit generated analytically, 160 points, cosine spacing). Each entry has a plain-language description ("thin, gentle stall, good all-rounder for small UAV wings at low speed" and similar), max thickness and camber and their positions (computed), and the source.
- Polars for each airfoil are computed with XFOIL (the `xfoil` Python package, built from `git+https://github.com/DARcorporation/xfoil-python@0a8c2fce02ba73b7f89f72306e43d78291d1e024`; needs gfortran and cmake at install time) at Re = 60k, 100k, 200k, 400k, 800k, 1.5M, 3M, Ncrit 9, alpha −6…16° step 0.5. A committed script `backend/scripts/build_airfoil_tables.py` writes `backend/app/engine/data/airfoil_polars.json` with, per airfoil and Re: `cl_max`, `alpha_cl_max_deg`, `alpha_zero_lift_deg`, `cl_alpha_per_rad` (fit −2…6°), `cd_min`, `cl_at_cd_min`, `cm0`, and the raw polar arrays (alpha, cl, cd, cm) for converged points. Non-converged points are dropped and counted. The JSON records the XFOIL version and settings.
- `GET /api/airfoils` → `[{id, name, description, use: "wing"|"tail", thickness_pct, x_thickness_pct, camber_pct, x_camber_pct, source, polar_summary: [{re, cl_max, alpha_cl_max_deg, alpha_zero_lift_deg, cl_alpha_per_rad, cd_min, cl_at_cd_min, cm0}]}]`.
- `GET /api/airfoils/{id}` → the above plus `coordinates: [[x, y], ...]` (unit chord, Selig order) and the full polars.

## 4. Reference images and Claude image reading (backend)

Tables (Alembic migration `0002`):

- `images`: `owner_id`, `project_id` (FK projects, `ON DELETE CASCADE`), `filename` (original, sanitised), `content_type`, `size_bytes`, `width_px`, `height_px`, `view` (`front` | `side` | `top` | `three_quarter` | `other`), `storage_name` (random hex + extension), timestamps. Files live in `{APP_DATA_DIR}/files/images/{project_id}/{storage_name}`; deleting the row deletes the file; deleting a project deletes its directory.
- `image_readings`: `owner_id`, `project_id` (cascade), `model`, `reference` JSON (`{parameter: "wing.span_mm" | "fuselage.length_mm", value_mm}`), `image_ids` JSON, `proposal` JSON, `status` (`ok` | `refused` | `error`), `error` text, `usage` JSON (input/output tokens), timestamps.

Endpoints (session required, CSRF header as before):

- `POST /api/projects/{id}/images` multipart (`file`, `view`): JPEG, PNG or WebP only (check magic bytes with Pillow, not the extension), at most 15 MB per file and 6 images per project. Pillow normalises EXIF orientation and records the size. The request-body limit middleware allows up to 16 MB on this route only. → 201 image object `{id, filename, view, width_px, height_px, size_bytes, url, created_at}` where `url` is `/api/images/{id}/file`.
- `GET /api/projects/{id}/images`, `PATCH /api/images/{id}` (`{view}`), `DELETE /api/images/{id}`, `GET /api/images/{id}/file` (served with the stored content type, `Cache-Control: private, max-age=3600`, `X-Content-Type-Options: nosniff`, never derived from the URL).
- `POST /api/projects/{id}/image-readings` body `{reference: {parameter, value_mm}, image_ids?: [...]}` (default: all project images, 1–4 required) → 201 reading with `proposal` or a plain 503 `{detail}` when the API key is missing, 502 with a plain message when Claude fails, 200-with-`status: "refused"` when Claude declines.
- `GET /api/projects/{id}/image-readings` (newest first).

Claude call (`backend/app/assistant/vision.py`). Read the bundled Claude API skill before writing it: `/tmp/claude-0/bundled-skills/2.1.295/4cf3759f2451930f3ce68daa5ec5a985/claude-api/` (`SKILL.md` if present, then `python/claude-api/README.md`, `python/claude-api/tool-use.md` structured outputs section, `shared/model-migration.md` → "Migrating to Claude Opus 5.5" and the refusal section). Rules from it that are binding here:

- Official `anthropic` Python SDK only. Model `claude-opus-5-5` (configurable via `CLAUDE_MODEL` env, default that). Adaptive thinking (omit `thinking` or send `{type: "adaptive"}`), `output_config.effort` set explicitly to `"high"`. Never send `budget_tokens`, `temperature`, prefill, or forced `tool_choice`.
- Structured output through `output_config.format` (JSON schema) or `client.messages.parse()` with a Pydantic model. Validate the result again server-side.
- Opt into server-side fallbacks (`betas=["server-side-fallback-2026-07-01"]`, `fallbacks="default"` on the beta messages endpoint) and always check `stop_reason` before reading content; `refusal` is stored as `status: "refused"` with the `stop_details` category.
- Images resized with Pillow to at most 1568 px on the long side, re-encoded JPEG quality 88, sent as base64 image blocks before the text block, each labelled with its `view`.
- Catch the SDK's typed errors most-specific first (rate limit, API status, connection) and return plain messages; never echo the key.
- The prompt asks Claude to identify which of the three supported layouts (`front_tilt`, `rear_tilt`, `quad_pusher`) is nearest and to estimate **proportions**, not millimetres: every length as a ratio to the wingspan (or fuselage length where noted), angles in degrees, enum choices, a 0–1 confidence and a one-line note per field, and a list of things it could not see. The server converts ratios to millimetres using the reference dimension, clamps each value to a documented plausible range (for example root chord 4–25 % of span, sweep −5…35°), and produces `proposal = {layout, layout_confidence, layout_reason, parameters: {dotted.path: {value, unit, confidence, note}}, unmapped_notes, warnings}`. Parameters not visible keep their current draft value and are omitted.
- Test seam: when `APP_ENV != "production"` and `CLAUDE_FAKE_RESPONSE_FILE` is set, the vision module returns that JSON instead of calling the API (used by pytest and Playwright). Production ignores it.

## 5. Tier 1 engine (frontend, runs in the browser on every edit)

Location `frontend/src/engine/`, pure TypeScript, no React, no DOM; fully unit-tested with vitest. Public API:

```ts
estimate(input: EngineInput): Estimates           // < 5 ms for one design
compareLayouts(input: EngineInput): LayoutComparison[]   // the same design re-estimated for each of the three layouts
buildGeometry(params: DesignParameters): Geometry // shared by 3D view, drawings and estimates
```

`EngineInput = {parameters, mission, settings, airfoils: AirfoilSummary map}`; `settings` is the Phase 1 settings document (thresholds, limits). Every output number is a `Quantity = {value, low, high, unit, label, explain, source}`; `explain` is one or two plain sentences; `source` names the method and reference. Statuses are `{key, label, level: "ok"|"warn"|"fail"|"info", message}` with plain messages.

Geometry (`geometry.ts`): wing area, aspect ratio, taper ratio, mean aerodynamic chord (length, spanwise station, leading-edge x), aerodynamic centre (MAC quarter chord), tail areas (horizontal and vertical equivalents for V and inverted V by projection), horizontal and vertical tail volume coefficients, boom/motor/prop positions for all four lift motors and the pusher, fuselage wetted area (ellipse or rounded rectangle, nose and tail tapers), wing and tail wetted areas (2 × planform × (1 + 0.25 t/c × … ) per Raymer), and render primitives: wing and tail as lofted airfoil sections (from the coordinates endpoint, with a NACA fallback while loading), fuselage as a lofted body with nose bay highlighted, booms, motor cans, prop discs, tilt hinge, landing gear.

Mass and balance (`mass.ts`) with every component listed, its mass, x position and a source:
- Structure from areal and linear densities chosen by `mission.scale`: prototype (3D-printed lightweight foaming PLA shells with carbon spar) and final (carbon-fibre composite). Starting densities and their sources go in `docs/ENGINE.md`; flag them clearly as estimates to be replaced by Phase 6 built weights.
- Booms from carbon tube mass per metre (diameter based), motors from a published statistical motor mass-versus-power relation sized to the hover power needed for the thrust-to-weight minimum from settings, ESCs from current rating, propellers from diameter, tilt mechanism (two servos plus hinges) for tilt layouts, pusher motor for quad + pusher, battery from cell count and capacity using pack-level specific energy (LiPo and Li-ion values with sources), avionics allowance, wiring fraction, landing gear by type, payload at the minimum and maximum of the mission range.
- The motor sizing depends on total mass, so iterate mass to convergence (fixed-point, at most 20 iterations, report convergence).
- Centre of gravity at minimum and maximum payload, also as % of MAC.

Aerodynamics and performance (`aero.ts`, `performance.ts`): sea-level ISA air (ρ = 1.225 kg/m³, μ = 1.789e-5). Wing loading; Reynolds number at cruise on the MAC; airfoil data interpolated in Re from the polar summary; 3D lift-curve slope (Helmbold/DATCOM with sweep); maximum lift 0.9 × section `cl_max` (state source); stall speed; cruise lift coefficient and the cruise/stall ratio; parasite drag by component build-up (flat-plate turbulent skin friction with a laminar fraction, Raymer form factors, interference factors, stopped propellers as flat plates edge-on with a stated factor, landing gear and booms); Oswald efficiency (Raymer); induced drag; lift-to-drag; neutral point (wing aerodynamic centre plus the tail contribution with downwash gradient 2·CLα/(π·AR)) and static margin at both payloads; cruise power = drag × speed / (η_prop × η_motor × η_esc) with stated efficiencies; hover power from momentum theory with figure of merit 0.65 (stated) and motor/ESC efficiencies; peak current (transition taken as 1.25 × hover power, stated) and the C-rate it needs. Mission profile: take-off hover 45 s, transition 15 s, cruise, transition 15 s, landing hover 45 s, reserve from settings; usable energy = nominal pack energy × (1 − reserve) × 0.95 (stated). Endurance and range in cruise with low/high bounds from stated uncertainty factors (for example ±15 % on drag, ±10 % on mass, ±5 % on battery energy).

Checks (Tier 1, informative): mass against warn/design/legal limits; static margin at both payloads against the settings range; cruise-to-stall ratio; endurance against the mission target; battery C-rate against typical continuous ratings (LiPo 25 C, Li-ion 3 C, stated as placeholders until real packs in Phase 4); hover thrust-to-weight shown as "assumed, motors sized to the settings minimum" until Phase 4. Each with a plain message telling the owner what to change.

`compareLayouts` returns, for each layout, mass, endurance, cruise power, hover power, a complexity rating with reasons (tilt mechanism, extra motor, transition simplicity) and the ArduPilot note.

Golden fixtures: `shared/fixtures/tier1_cases.json` holds three designs (the default, a 24 kg final-scale scaling of it, and a quad + pusher variant) with their computed geometry numbers; Phase 3's Python engine must reproduce the geometry numbers to 0.1 %. `docs/ENGINE.md` documents every method, constant and source used, in plain language.

## 6. User interface

- **Inputs tab:** reference image area (drag-and-drop or button, view selector per image, thumbnails, delete), reference dimension form (which dimension, value in mm), "Read images with Claude" button with progress and cost note, and a proposal review table (parameter, current value, proposed value, confidence, note, accept checkbox; layout suggestion with reason). "Apply selected" writes the accepted values into the draft. A layout comparison card shows the three layouts side by side (mass, endurance, hover and cruise power, complexity, ArduPilot note) with "Use this layout".
- **Design tab:** a 3D view (three.js, orbit controls, lazy-loaded so the first page stays fast) and three SVG drawings (top, side, front) with dimension lines in mm. Drag handles on both: span (wing tip), root chord and tip chord (trailing edges), wing position (root leading edge), boom offset, tail arm, fuselage length (nose). Dragging updates the draft live; the draft autosaves as before. Every parameter keeps its number box and gains a slider with a sensible range from the schema. Live estimates panel beside the views: mass, balance (CG and static margin at both payloads), wing loading, stall speed, cruise/stall, cruise and hover power, endurance (as a range) with green/amber/red status per area and an explanation on every number. Updates within one animation frame of an edit.
- **Compare:** from the Versions panel select two or three versions → a comparison view: top and side outlines overlaid in distinct colours with a legend, and a table of key numbers (from `estimate`) with differences highlighted. Route `/projects/:id/compare?versions=3,5`.
- Every number on screen keeps an explanation on hover or click, units metric, and the layout stays usable at phone width (the 3D view and drawings stack).

## 7. Tests

- Backend pytest: schema upgrade 1→2 (stored v1 rows read back as v2 with defaults), images (type sniffing, size limits, count limit, view update, file served, cascade delete of files), image readings with the fake response (ratio-to-mm conversion, clamping, layout mapping, refusal path, missing-key 503), airfoil endpoints, the polar JSON present for every library airfoil.
- Frontend vitest: geometry against hand-checked values (rectangular and tapered wings: area, AR, MAC by formula), mass convergence, stall speed and induced drag against textbook worked examples (cite them in the test), momentum-theory hover power, endurance arithmetic, layout comparison.
- Playwright `e2e/tests/phase2.spec.ts`: upload two images, run a reading with the fake response, accept the proposal, see the model and estimates change, drag a handle in the top drawing and see the span number and the estimates change, save two versions and open the comparison view showing both outlines and the table.

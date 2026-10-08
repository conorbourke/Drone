# VTOL Drone Designer — Build Brief

8 Oct 2026 · @Conor

## Overview

Build a browser-based design tool for a carbon-fibre VTOL camera drone: upload reference images, set the mission, get an editable 3D model, analyse its aerodynamics and performance, get a parts list with Irish/UK suppliers, and export print and CAD files. The owner is a hobbyist with no CAD experience, so the software must do the engineering and explain it in plain language.

**How to use this brief with Claude Code:** build one phase at a time (see Phased build plan). Paste this whole brief at the start, then say which phase to build. Do not start a later phase until the current phase's acceptance criteria pass.

## Goals

1. Turn a rough idea (AI-generated reference images plus a mission) into a flyable, buildable design.
2. Give fast, trustworthy feedback on how every change affects aerodynamics, weight, balance and endurance.
3. Produce files that go straight to a home 3D printer for a flying prototype, and later to mould-making for carbon fibre.
4. Learn from real flights: compare predictions with flight logs and get more accurate over time.

## User and constraints

There is one user, the owner, working on a laptop. These constraints are non-negotiable:

- **Browser only.** Nothing is installed or run on the user's machine: no local scripts, no command line, no desktop app. All heavy computation runs on a hosted server.
- **Hosted and deployed by Claude Code.** Claude Code sets up hosting and deployment and gives the user a URL. Budget: about €10–30 a month for hosting, plus pay-as-you-go Claude API usage.
- **Single user, simple login.** No multi-user features, teams or sharing for now. Keep the data model clean enough to add users later.
- **Claude API for the built-in assistant.** The API key lives on the server, never in the browser.
- **Metric units throughout** (mm, m, g, kg, W, Wh, m/s, km/h). Euro for prices.
- **Plain-language explanations.** Every number shown has a short explanation available on hover or click. Avoid unexplained jargon.

## The aircraft being designed

The tool designs one family of aircraft: an electric VTOL fixed-wing camera drone that takes off and lands like a quadcopter and cruises on a wing.

### Configurations the tool must support and compare

| Layout | How it works in cruise | Notes |
|---|---|---|
| Front tilt | Front pair of motors tilts forward to pull; rear pair stops | Common; supported by ArduPilot QuadPlane tiltrotor |
| Rear tilt | Rear pair tilts to push; front pair stops | The owner's original idea; check autopilot support before enabling |
| Quad + pusher | Four fixed lift motors plus a separate pusher motor | No tilt mechanism; simplest transition, a little heavier |

Only offer layouts the autopilot (ArduPilot) can fly. Show all three side by side with weight, endurance and complexity so the owner can choose.

### Scale

- **Prototype:** 2–3 kg take-off weight, airframe 3D-printed (lightweight foaming filament for wing and shell, tougher filament for mounts and tilt parts) with off-the-shelf carbon tubes for spar and booms. It must fly.
- **Final:** up to 24 kg maximum take-off weight (including batteries and the heaviest camera), carbon fibre, same layout scaled up.
- Scaling is not linear. The tool must re-run the full analysis at each target weight and show what changes (wing loading, speeds, motor and battery class). Never just multiply dimensions.

### Mission inputs (entered by the user)

- Target take-off weight
- Target endurance (the owner's goal for the carbon version is about two hours in wing flight)
- Cruise speed
- Camera/payload weight range

### Interchangeable nose camera bay

The nose is a swappable module with the camera built into its lower edge for low drag. Treat it as a payload bay with a minimum and maximum weight. Check balance and stability at both ends of that range. Camera design itself is out of scope for now; reserve the space and mounting points.

### Rules (Republic of Ireland only)

Homebuilt drones from 250 g to 25 kg fly in EU Open category subcategory A3: away from uninvolved people and at least 150 m from residential, commercial, industrial or recreational areas, within visual line of sight. The tool should show a warning if maximum take-off weight approaches 25 kg, and keep a configurable safety margin (default: design limit 24 kg). Range figures should note that flight beyond visual line of sight needs IAA authorisation.

### 3D printer

Bambu Lab P2S: 256 × 256 × 256 mm build volume, enclosed. Use a usable part envelope of 240 × 240 × 240 mm by default. Printer size must be a setting, not hard-coded.

## Workflow and tabs

The app is a set of tabs the user moves through left to right, with versions and the Claude assistant available on every tab.

1. **Inputs.** Upload 3–4 reference images (AI-generated renders, different angles) and fill in the mission form. Ask for one real dimension (for example wingspan) so the images can be scaled.
2. **Design.** The app generates an editable 3D model plus technical drawings (top, side, front views).
   - The model is parametric: a template of named parameters (wingspan, chord, sweep, dihedral, fuselage length and shape, boom positions, motor positions, tilt axis, tail type, nose bay size). Images do not become a mesh.
   - Claude (vision) reads the images and proposes starting values for those parameters, picking the nearest supported layout. The user confirms or corrects them.
   - Editing: drag handles on the 2D views and the 3D view, plus sliders and number boxes for precise values. Changes update the model instantly.
   - Live panel: instant estimates (weight, balance point, wing loading, stall speed, endurance) with green/amber/red status per area.
   - Analyse button: runs the full aerodynamics and performance analysis, then shows results and a ranked list of the changes that would most improve efficiency.
3. **Parts.** Recommended components for the current design (motors, propellers, ESCs, tilt servos, battery, autopilot, GPS, radio, telemetry, wiring), each with the reasoning and live links to Irish and UK suppliers.
4. **Files.** 3D print files split to fit the printer, CAD files and dimensioned drawings. Mould files later.
5. **Flight data.** Enter actual built weights, upload autopilot flight logs, and compare prediction against reality.

### Across all tabs

- **Versions:** save, name, duplicate and restore designs; compare two or three versions side by side (geometry overlay plus key numbers).
- **Claude assistant:** a chat panel that can see the current design, latest analysis and flight data.

## Aerodynamics and performance engine

This is the most important part of the software: nothing else will check the design, so the numbers must come from established engineering methods, be tested, and show their uncertainty. The AI assistant never produces the numbers; it only reads and explains them.

### Tier 1: instant estimates (run in the browser on every edit)

- Weight build-up from the parts database plus structural estimates per component; balance point (CG).
- Wing loading, stall speed, cruise lift coefficient.
- Cruise power from a simple drag estimate; hover power from momentum theory with a figure of merit.
- Endurance from usable battery energy across a mission profile (take-off hover, transition, cruise, transition, landing hover, reserve).

### Tier 2: full analysis (server, on "Analyse")

- **Whole aircraft:** vortex lattice method using AVL (open source) for lift distribution, induced drag, trim, neutral point, static margin and stability derivatives.
- **Wing sections:** XFOIL (open source) polars at the actual Reynolds numbers for the chosen airfoils; offer a small library of suitable low-Reynolds airfoils.
- **Parasite drag:** component build-up (fuselage, booms, stopped propellers, nose bay, landing gear) with standard form factors.
- **Propulsion:** motor model (Kv, resistance, no-load current) matched to propeller data, at hover and cruise operating points separately.
- **Batteries:** LiPo and Li-ion models, including whether the pack can supply peak hover/transition current, not just cruise energy.
- **Transition:** check thrust margin and speed range through the tilt or pusher transition.
- **Structure (basic):** spar and boom bending checks for chosen carbon tubes under a manoeuvre load factor.

### Checks with clear pass/warn/fail

- Hover thrust-to-weight above a configurable minimum.
- Static margin within a configurable stable range, at both minimum and maximum payload.
- CG inside the allowed envelope for every nose-bay payload.
- Stall speed comfortably below cruise speed.
- Battery current within rating at peak demand.
- Maximum take-off weight under the 24 kg design limit (25 kg legal limit).

Suggested default thresholds must be documented with their source and editable in settings.

### Recommendations

After each analysis, run a sensitivity sweep: nudge each key parameter (span, chord, aspect ratio, airfoil, fuselage cross-section, battery size, prop diameter) by a small step and rank the changes by endurance gained, while respecting every check. Show the top changes in plain language with their predicted effect.

### Scale to target weight

A feature that re-solves the same layout at a new take-off weight (for example 3 kg to 24 kg) and shows what changes and why.

### Uncertainty and validation

- Show key outputs as ranges (for example endurance 95–120 min), not single precise numbers.
- Ship a validation suite: unit tests against textbook worked examples and AVL/XFOIL reference cases, plus at least two real published VTOL or fixed-wing drone designs modelled in the tool and compared to their published figures. Record the sources and the error found.
- Once flight logs exist, use them to calibrate correction factors (see Flight data).

## Assistant, parts, files and flight data

### Claude assistant

- Chat panel on every tab, using the Claude API from the server.
- Has tool access to: the current design parameters, the latest analysis results, the parts list, version history and flight-data comparisons.
- Its job: explain results in plain language, answer questions ("why is my endurance low?"), and recommend changes with reasons, quoting the engine's numbers.
- It must not invent figures. Any predicted effect it mentions comes from running the engine (it can request a quick re-analysis of a proposed change).
- Image reading for the Inputs tab also uses Claude (vision) to propose starting parameters.

### Parts and suppliers

- A parts database (motors, propellers, ESCs, tilt servos, batteries and cells, autopilots, GPS, radio, telemetry, carbon tubes) with the specs the engine needs: weight, dimensions, Kv, resistance, thrust data, current ratings, capacity, discharge rating.
- Recommendations are picked by the engine to suit the current design, with the reasoning shown, and selected parts feed back into weight and analysis.
- Supplier lookup for Irish and UK shops, using Claude with web search: price, stock, link and a "last checked" date on every listing.
- Running totals for cost and weight, tracked against the €5,000 prototype budget. Flag upgrades worth paying for, with the trade-off.

### Files

- **3D print files:** STL and 3MF, every part split to fit the 240 mm envelope. Splits avoid high-stress zones and include alignment keys, bonding surfaces and channels for carbon tubes. Printing notes per part (filament type, orientation).
- **CAD:** STEP files of the full assembly and each part.
- **Drawings:** dimensioned 2D drawings as PDF, plus DXF for flat parts that can be CNC-cut in carbon plate.
- **Bill of materials:** CSV of all parts, quantities, weights and suppliers.
- **Moulds (later phase):** mould halves for curved carbon parts with flanges, registration keys and draft, tiled to fit the printer.

### Flight data

- ArduPilot already records detailed onboard logs (DataFlash .bin files), so no extra software is needed on the drone. The tool should state which logging settings to enable.
- Upload a log; the tool splits the flight into phases (hover, transition, cruise, landing) and shows actual power, current, speed, attitude, vibration and battery behaviour.
- Compare measured against predicted for each phase, then derive correction factors that improve future predictions. Keep these per airframe version.
- Also record actual built weights per part, to correct the weight model.

## Suggested architecture and hosting

The user's laptop only runs a web page; everything heavy runs on one hosted server. Claude Code may change these choices if it has good reason, but must keep the browser-only constraint.

The aero engine is the critical component; the others support it.

- **Front end:** a modern web framework (for example React with TypeScript) with Three.js for the 3D view and SVG for the 2D drawings. Tier 1 estimates run here for instant feedback.
- **Back end:** Python (for example FastAPI), because the engineering tools live in Python. Runs AVL and XFOIL as compiled programs, CadQuery (OpenCascade) for geometry and file export, and pymavlink for ArduPilot logs.
- **Data:** a database for projects, versions, parts and calibration data; file storage for images, exports and logs.
- **Hosting:** a single container-based host that can run the compiled tools, about €10–30 a month. Claude Code handles deployment, backups and HTTPS.
- **Secrets:** the Claude API key stays on the server.

## Phased build plan

Build in seven phases, each deployed and usable before the next starts. Each phase ends with acceptance criteria the owner can check in the browser.

1. **Foundations.** Hosting, login, project and version storage, parts database structure, deployment pipeline.
   - Done when: the owner opens a URL, logs in, creates a project and saves two versions.
2. **Inputs and design.** Image upload, mission form, Claude image reading, parametric model for all three layouts, 3D view and 2D drawings with handles, sliders and number boxes, Tier 1 instant estimates, version comparison.
   - Done when: uploaded images produce a sensible starting model, edits update the model and estimates instantly, and two versions can be compared side by side.
3. **Full analysis and assistant.** AVL and XFOIL on the server, drag, propulsion, battery and transition models, checks, ranked recommendations, scale-to-weight, validation suite, Claude assistant panel.
   - Done when: the validation suite passes and its report is viewable in the app; Analyse returns results, checks and ranked recommendations; the assistant explains them correctly.
4. **Parts and suppliers.** Parts database seeded with real components, engine-driven recommendations, Irish/UK supplier lookup, cost and weight totals.
   - Done when: a complete prototype parts list is produced with working supplier links and a total under or against the €5,000 budget.
5. **Files.** Split STL/3MF, STEP, PDF/DXF drawings, bill of materials.
   - Done when: every printed part fits the 240 mm envelope and opens in Bambu Studio, and the STEP files open in a free CAD viewer.
6. **Flight data.** Log upload, phase detection, predicted-vs-actual comparison, calibration factors, built-weight entry.
   - Done when: a sample ArduPilot log is parsed and compared against its design's predictions.
7. **Moulds and full scale.** Mould generation for carbon parts, tiled moulds, full 24 kg design checks.
   - Done when: mould files are generated for the nose, fuselage and wing-root fairings of a test design.

## Scope, safety and open decisions

### Out of scope for now

- Camera and gimbal design (reserve the nose bay only).
- Full CFD simulation; the tool uses the engineering methods listed above.
- Multi-user accounts, sharing, or selling the tool.

### Safety

- Predictions are engineering estimates. First flights should be short, in an open area, with conservative settings.
- Transition is the riskiest phase; recommend practising in a flight simulator first.
- Show the A3 operating limits next to range and endurance figures.

### Decisions for Claude Code to confirm with the owner before building

- [ ] Rear-tilt layout: confirm ArduPilot support before enabling it as a full option.
- [ ] Assistant: advise only, or also a "try this as a new version" button for its suggestions.
- [ ] Default thresholds for the checks (thrust-to-weight, static margin, reserve), with sources.
- [ ] Which published designs to use in the validation suite.
- [ ] Hosting provider, within the €10–30/month budget.

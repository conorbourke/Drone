# VTOL Drone Designer

A browser-based design tool for a carbon-fibre VTOL camera drone: describe the mission, shape the aircraft, check its aerodynamics and performance with established engineering methods, pick parts from Irish and UK suppliers, and export files for a home 3D printer and later for carbon-fibre moulds. Everything runs on a small hosted server; the owner only needs a web browser.

The full product brief is in [`docs/BRIEF.md`](docs/BRIEF.md). The build is in seven phases; progress and acceptance criteria are in [`docs/PHASES.md`](docs/PHASES.md).

**Status: all seven phases are built and live at https://conor-vtol-designer.fly.dev.** Phase 1 (Foundations) is accepted; Phases 2 to 7 are deployed and waiting for the owner's check. Known limits of the newest phases are listed in [`docs/PHASES.md`](docs/PHASES.md) and the phase files in [`docs/phases/`](docs/phases/).

## Getting it running (owner)

Follow [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md). In short: create a Fly.io account and token, add three secrets to this GitHub repository, press *Run workflow* on the *Deploy to Fly.io* action, and open the address it prints. About 15 minutes, all in the browser. Hosting costs roughly €13 a month (the 2 GB machine the analysis and CAD tools need).

## Using the app

- **Projects.** One project per aircraft idea. Each project has a live *draft* that saves itself as you type.
- **Tabs, left to right.** *Inputs* (mission: take-off weight, endurance, cruise speed, payload range, layout), *Design* (the aircraft's parameters, 3D view, drawings, instant estimates and the full analysis), *Parts* (recommended parts with Irish and UK suppliers, against the budget), *Files*, *Flight data* and *Full scale* (below).
- **Files.** Press *Generate files* for the draft or a saved version. The server makes, in a minute or two: 3D-print files for every printed part, already cut into pieces that fit the printer (240 mm by default) with pins and glue joints; CAD files (STEP); dimensioned drawings (PDF); outlines of flat carbon plates (DXF); a bill of materials (CSV, opens in a spreadsheet); and printing notes. Each piece has a fit tick and a 3D preview on the printer bed. Download one file or *Download all (ZIP)*. Open `.3mf` and `.stl` in Bambu Studio, `.step` in FreeCAD or an online STEP viewer, `.dxf` in LibreCAD. Old exports stay listed until you delete them.
- **Flight data.** After a flight, copy the log from the autopilot's SD card (or download it with Mission Planner or QGroundControl) and upload it here; the *Set up logging* card says which ArduPilot settings to use first. Upload the DataFlash `.bin` file (up to 200 MB); telemetry `.tlog` files are refused because they lack the battery data. The app finds the hover, transition and cruise parts of the flight, draws charts and compares measured power and energy with the design's predictions. *Calibration* turns one or more flights into correction factors; press *Apply* and the next *Analyse* on the Design tab uses them ("Calibrated with N flights"), or *Undo* to go back. The in-browser quick estimate, the ranked recommendations and scale-to-weight do not use the factors yet. *Built weights* is where you enter what each part really weighs. Logs, factors and built weights belong to the project, not to one version. *Load sample flight* shows how it all works with a simulated flight of a different aircraft.
- **Full scale.** For the final carbon aircraft (up to 24 kg; scale the prototype up with *Scale to weight* on the Design tab and set the mission scale to final). *Full-scale checks* adds the 23 / 24 / 25 kg mass limits, whether the aircraft can still hover with one motor failed (and an eight-motor layout if not), the carbon tubes the wing and booms need, landing gear loads, battery current, and a suggested carbon layup with its weight (shown and checked against the mass limits, but not yet used in the analysis' own mass estimate). *Moulds for the carbon parts* makes two-part moulds for the nose, fuselage and wing-root fairing, cut into tiles that fit the printer, with a draft report, tile previews, a PDF sheet per part and a ZIP. This takes several minutes. Print the fairing tiles twice and mirror the second set in the slicer for the left side, and write each tile's label on it after printing (labels are not printed on).
- **Versions** (right-hand panel on every tab). *Save version* snapshots the draft with a name and notes. Restore, duplicate, rename, delete or compare versions side by side. A version that has flight logs (or an applied calibration) cannot be deleted until those are removed.
- **Assistant** (right-hand panel). Explains the numbers; it reads the engine's results and never invents its own.
- **Settings.** Printer envelope, take-off weight limits, check thresholds with their sources, backups.
- Every number has a short plain-language explanation: hover or tap the small (?) beside it.

Units are metric throughout and prices are in euro.

## Decisions that need the owner

[`docs/DECISIONS.md`](docs/DECISIONS.md) records the defaults taken so far (hosting provider, rear-tilt support, assistant behaviour, check thresholds, validation designs) and what still needs confirming before later phases.

## Repository layout

```
backend/    Python FastAPI API, database models, migrations, tests
frontend/   React + TypeScript web app
e2e/        Playwright end-to-end tests (the acceptance criterion, automated)
docs/       Brief, architecture contract, decisions, deployment, phases
Dockerfile  One container: the API serves the built web app
fly.toml    Fly.io app definition (one machine, one volume, always on)
.github/    CI (lint, tests, build, e2e, container smoke test) and the deploy workflow
```

## Development (Claude Code)

The architecture contract is [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md); later phases must follow it.

```
make install          # backend (uv), frontend (npm), e2e (npm)
make dev-backend      # API on :8000 with development settings
make dev-frontend     # Vite dev server on :5173, /api proxied to :8000
make lint unit        # ruff, pytest, tsc, eslint, vitest
make e2e              # build the frontend, run Playwright against the real server
make docker-build     # build the production image locally
```

# VTOL Drone Designer

A browser-based design tool for a carbon-fibre VTOL camera drone: describe the mission, shape the aircraft, check its aerodynamics and performance with established engineering methods, pick parts from Irish and UK suppliers, and export files for a home 3D printer and later for carbon-fibre moulds. Everything runs on a small hosted server; the owner only needs a web browser.

The full product brief is in [`docs/BRIEF.md`](docs/BRIEF.md). The build is in seven phases; progress and acceptance criteria are in [`docs/PHASES.md`](docs/PHASES.md).

**Status: Phase 1 (Foundations) is built.** Login, projects, design versions, the parts database structure, settings, backups and a no-terminal deployment pipeline are in place. The design tools arrive in Phase 2 and the engineering engine in Phase 3.

## Getting it running (owner)

Follow [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md). In short: create a Fly.io account and token, add three secrets to this GitHub repository, press *Run workflow* on the *Deploy to Fly.io* action, and open the address it prints. About 15 minutes, all in the browser, roughly €7 a month.

## Using the app

- **Projects.** One project per aircraft idea. Each project has a live *draft* that saves itself as you type.
- **Tabs, left to right.** *Inputs* (mission: take-off weight, endurance, cruise speed, payload range, layout), *Design* (the named parameters of the aircraft; 3D view and drawings arrive in Phase 2), *Parts* (database structure now, recommendations in Phase 4), *Files* (Phase 5) and *Flight data* (Phase 6).
- **Versions** (right-hand panel on every tab). *Save version* snapshots the draft with a name and notes. Restore, duplicate, rename or delete any version. Side-by-side comparison arrives in Phase 2.
- **Assistant** (right-hand panel). Arrives in Phase 3.
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

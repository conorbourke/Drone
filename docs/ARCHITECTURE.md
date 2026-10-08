# Architecture (Phase 1 contract)

This document is the shared contract for the codebase. Builder agents and later phases must follow it. Read `docs/BRIEF.md` first for the product goals and constraints.

Phase 1 scope: hosting, login, project and version storage, parts database structure, deployment pipeline. Acceptance: the owner opens a URL, logs in, creates a project and saves two versions.

## Principles

- Browser only for the owner. Everything runs on one hosted container. The owner never runs a command.
- Single user today, but every owned row carries `owner_id` so users can be added later.
- Metric units everywhere. Field names carry the unit as a suffix: `span_mm`, `mass_g`, `speed_ms`, `power_w`, `energy_wh`, `price_eur`.
- Every number shown in the UI has a plain-language explanation available on hover or click (the `Explain` component).
- The engine (later phases) produces every number. The assistant (Phase 3) only reads and explains.
- Secrets live only in server environment variables.

## Repository layout

```
README.md                  owner-facing guide (what it is, deploy steps, how to use)
docs/                      BRIEF.md, ARCHITECTURE.md (this), DECISIONS.md, DEPLOYMENT.md, PHASES.md
backend/                   Python 3.12+ FastAPI app (package `app`), tests, alembic migrations, seed data
frontend/                  Vite + React 19 + TypeScript SPA
e2e/                       Playwright end-to-end tests that drive the built app
Dockerfile                 multi-stage: build frontend, then python runtime that serves API + static SPA
docker-compose.yml         local development only (used by Claude Code, not by the owner)
fly.toml                   Fly.io app definition (single machine, persistent volume at /data)
.github/workflows/         ci.yml (lint, test, build, e2e, docker build) and deploy.yml (Fly.io)
Makefile                   developer shortcuts (install, dev, test, e2e, lint)
```

## Backend

Stack: Python 3.12+, FastAPI, SQLAlchemy 2.x (sync engine, SQLite), Alembic, Pydantic v2, `bcrypt`, `itsdangerous`, `uvicorn`. Dependency management with `uv` and `pyproject.toml` (PEP 621, hatchling build backend not required: use `[project]` plus `[tool.uv]`; include a `uv.lock`). Lint and format with `ruff`. Tests with `pytest` + `httpx` TestClient.

Package layout under `backend/`:

```
pyproject.toml, uv.lock, alembic.ini, ruff.toml (or [tool.ruff] in pyproject)
app/
  __init__.py
  main.py          create_app(): routers, static SPA serving, lifespan (ensure owner user, start backup scheduler)
  config.py        Settings (pydantic-settings) read from env; see Environment below
  db.py            engine, SessionLocal, get_db dependency; SQLite with WAL + foreign_keys=ON
  models.py        SQLAlchemy ORM models (see Data model)
  security.py      password hashing (bcrypt), session token signing (itsdangerous), login rate limiter
  deps.py          current_user dependency (401 if no valid session cookie)
  schemas/         Pydantic models: auth.py, project.py, version.py, design.py, mission.py, parts.py, settings.py, system.py
  routers/         auth.py, projects.py, versions.py, parts.py, settings.py, system.py
  parts_catalog/   category definitions (spec schema per category) and fixture loader
  backup.py        SQLite online backup to /data/backups, daily scheduler, retention
  defaults.py      DEFAULT_DESIGN_PARAMETERS, DEFAULT_MISSION, DEFAULT_SETTINGS (single source of truth)
migrations/        alembic env.py + versions/0001_initial.py
seed/parts.example.json
tests/             pytest; use a temp SQLite file per test session
```

### Environment variables (`app/config.py`)

| Variable | Required | Default | Meaning |
|---|---|---|---|
| `APP_SECRET_KEY` | yes in prod | dev fallback with warning | Signs session cookies. Any long random string. |
| `APP_PASSWORD` | yes | none | Owner password (plain). Hashed in memory at startup. |
| `APP_PASSWORD_HASH` | no | none | bcrypt hash; if set, overrides `APP_PASSWORD`. |
| `APP_OWNER_EMAIL` | no | `owner@example.com` | Display identity of the single user. |
| `APP_DATA_DIR` | no | `./data` locally, `/data` in container | DB, files, backups live here. |
| `DATABASE_URL` | no | `sqlite:///{APP_DATA_DIR}/app.db` | SQLAlchemy URL. |
| `APP_ENV` | no | `development` | `production` enables Secure cookies and strict checks. |
| `APP_BASE_URL` | no | none | Public URL, used for links in later phases. |
| `ANTHROPIC_API_KEY` | no (Phase 3) | none | Claude API key, server only. |
| `BACKUP_HOUR_UTC` | no | `3` | Hour of the daily backup. |
| `BACKUP_KEEP` | no | `14` | Number of backups retained. |

In production, startup fails loudly if `APP_SECRET_KEY` or a password is missing.

### Data model (`app/models.py`)

All tables have integer primary key `id`, `created_at`, `updated_at` (UTC, timezone-aware stored as ISO text in SQLite via SQLAlchemy DateTime(timezone=True)).

- `users`: `email` (unique), `password_hash` (nullable; the single owner authenticates against env, but the column exists for later), `display_name`, `is_owner` (bool).
- `projects`: `owner_id` FK users, `name`, `description`, `draft_parameters` (JSON), `draft_mission` (JSON), `draft_based_on_version_id` (nullable FK versions, no cascade), `draft_updated_at`. Unique `(owner_id, name)`.
- `design_versions`: `project_id` FK projects (cascade delete), `owner_id`, `name`, `notes`, `number` (per-project sequence 1,2,3...), `parameters` (JSON), `mission` (JSON), `parent_version_id` (nullable self FK; set on duplicate), `schema_version` (int). Unique `(project_id, number)`.
- `parts`: `category` (string enum, see Parts), `manufacturer`, `model` (name), `mass_g`, `price_eur_estimate` (nullable), `spec` (JSON validated against the category schema), `source` (text: where the spec came from), `verified` (bool, false for placeholders), `notes`. Unique `(category, manufacturer, model)`.
- `part_listings`: `part_id` FK parts (cascade), `supplier_name`, `country` (`IE` or `UK`), `url`, `price_eur` (nullable), `in_stock` (nullable bool), `last_checked_at` (nullable datetime).
- `app_settings`: `owner_id` FK users (unique), `data` (JSON, full settings document, see Settings).
- Reserved for later phases (do not create yet, but do not block): `images`, `analyses`, `flight_logs`, `calibrations`, `export_files`.

JSON columns use SQLAlchemy `JSON` type. Design parameters and mission are validated by Pydantic on write; stored JSON always includes `schema_version`.

### Design parameters and mission (`app/schemas/design.py`, `mission.py`, `app/defaults.py`)

`DesignParameters` (schema_version 1). All lengths mm, angles degrees, masses g. Pydantic model with field descriptions (these descriptions are the plain-language explanations; the frontend mirrors them).

```
layout: "front_tilt" | "rear_tilt" | "quad_pusher"
wing: { span_mm, root_chord_mm, tip_chord_mm, sweep_deg, dihedral_deg, incidence_deg, airfoil (str id, default "sd7037") }
fuselage: { length_mm, width_mm, height_mm, cross_section: "ellipse" | "rounded_rect" }
booms: { count (2), lateral_offset_mm (distance from centreline), length_mm, x_offset_mm (boom front relative to wing leading edge, negative = ahead) }
motors: { front_x_mm, rear_x_mm (along boom from boom front), height_mm (above boom centreline) }
tilt: { axis_x_mm (position of tilt axis along boom), max_angle_deg (default 90) }   # ignored for quad_pusher
pusher: { prop_diameter_mm, x_mm }   # only used for quad_pusher
tail: { type: "conventional" | "v_tail" | "inverted_v" | "twin_boom_h", span_mm, chord_mm, arm_mm (wing quarter chord to tail quarter chord), height_mm }
nose_bay: { length_mm, width_mm, height_mm, payload_min_g, payload_max_g }
landing_gear: { type: "skids" | "legs" | "none", height_mm }
```

`Mission` (schema_version 1):

```
scale: "prototype" | "final"
target_takeoff_mass_kg (default 2.5)
target_endurance_min (default 45 for prototype)
cruise_speed_ms (default 16)
payload_min_g (default 150), payload_max_g (default 400)
```

Validation: positive numbers, `payload_max_g >= payload_min_g`, `tip_chord_mm <= root_chord_mm`, `target_takeoff_mass_kg` must not exceed `settings.limits.design_mtow_kg` (return 422 with a plain-language message). Defaults in `app/defaults.py` describe a plausible 2.5 kg front-tilt prototype (span 1800 mm, root chord 260, tip chord 180, fuselage 900 long, booms ±300 mm) and are explicitly labelled as starting values, not an analysed design.

### Parts catalogue (`app/parts_catalog/`)

Categories and the spec fields the engine will need. Each category has a Pydantic spec model; `GET /api/parts/categories` returns the field list (name, unit, type, description, required) generated from the models so the UI can build forms.

| Category | Spec fields |
|---|---|
| `motor` | `kv_rpm_per_v`, `resistance_ohm`, `no_load_current_a`, `max_current_a`, `max_power_w`, `lipo_cells_min`, `lipo_cells_max`, `stator_size` (str), `shaft_mm`, `mount_pattern` (str), `thrust_data` (list of {prop (str), voltage_v, throttle_pct, thrust_g, current_a, power_w, rpm}) |
| `propeller` | `diameter_in`, `pitch_in`, `blades`, `folding` (bool), `hub_bore_mm`, `material`, `max_rpm` (nullable) |
| `esc` | `continuous_current_a`, `burst_current_a`, `lipo_cells_min`, `lipo_cells_max`, `firmware` (str), `bec_v` (nullable), `telemetry` (bool) |
| `servo` | `torque_kg_cm`, `speed_s_per_60deg`, `voltage_min_v`, `voltage_max_v`, `gear_material`, `width_mm`, `length_mm`, `height_mm`, `digital` (bool) |
| `battery` | `chemistry` ("lipo" or "li-ion"), `cells_series`, `cells_parallel`, `capacity_mah`, `nominal_voltage_v`, `discharge_c_continuous`, `discharge_c_burst`, `length_mm`, `width_mm`, `height_mm`, `connector` |
| `cell` | `chemistry`, `capacity_mah`, `nominal_voltage_v`, `max_continuous_discharge_a`, `diameter_mm`, `length_mm`, `format` (e.g. "21700") |
| `autopilot` | `firmware` ("ardupilot"), `pwm_outputs`, `can_ports`, `uarts`, `imu_count`, `voltage_in_min_v`, `voltage_in_max_v` |
| `gps` | `constellations` (list str), `rtk` (bool), `update_rate_hz`, `interface` |
| `radio` | `kind` ("rc_link"), `frequency_mhz`, `range_km_los`, `channels`, `telemetry` (bool) |
| `telemetry` | `frequency_mhz`, `range_km_los`, `air_rate_kbps`, `interface` |
| `carbon_tube` | `outer_diameter_mm`, `inner_diameter_mm`, `length_mm`, `layup` ("pultruded" or "roll_wrapped"), `mass_per_m_g`, `youngs_modulus_gpa` (nullable), `tensile_strength_mpa` (nullable) |

All parts also carry `mass_g` and optional dimensions at the top level. Seed file `backend/seed/parts.example.json` contains a few clearly labelled example entries with `verified: false` and `source: "example placeholder, not verified"`. Phase 4 seeds real components. The loader (`python -m app.parts_catalog.load seed/parts.example.json`) upserts by `(category, manufacturer, model)` and is idempotent; the app does not auto-seed in production.

### Settings document (`app/schemas/settings.py`, defaults in `app/defaults.py`)

```
printer: { name: "Bambu Lab P2S", build_volume_mm: {x:256,y:256,z:256}, usable_envelope_mm: {x:240,y:240,z:240} }
limits: { design_mtow_kg: 24.0, legal_mtow_kg: 25.0, warn_mtow_kg: 23.0 }
checks: {
  hover_thrust_to_weight_min: 2.0,
  static_margin_min: 0.05, static_margin_max: 0.20,
  stall_speed_ratio_max: 0.77 (cruise must be at least 1.3 × stall),
  battery_reserve_fraction: 0.20,
  battery_current_margin: 0.80 (peak draw at most 80 % of continuous rating),
}
units: { system: "metric" }
```

Each threshold has a `description` and `source` string in the API response (from a static table in `defaults.py`) so the UI can show them. Sources are marked "proposed, confirm in Phase 3" where the brief asks for owner confirmation. `GET /api/settings` merges stored overrides over defaults; `PUT /api/settings` validates and stores the full document.

### Authentication (`app/security.py`, `app/routers/auth.py`)

- Single owner. Password verified against bcrypt hash derived from env at startup. Timing-safe.
- On success set cookie `vtol_session`: `itsdangerous.URLSafeTimedSerializer` token containing `{"uid": user.id}`; `HttpOnly`, `SameSite=Lax`, `Secure` when `APP_ENV=production`, `Path=/`, max age 30 days. Server checks `max_age` on every request.
- Rate limit: 5 failed logins per 15 minutes per client IP (in-memory). Respond 429 with a plain message.
- CSRF: cookie is SameSite=Lax; additionally every state-changing `/api` request must carry header `X-Requested-With: fetch` (frontend client sets it) or it is rejected with 403. GET never mutates.
- `GET /api/auth/me` returns the user or 401. Unauthenticated API calls return 401 JSON `{detail: "..."}`; the SPA redirects to `/login`.

### API (all JSON, prefix `/api`)

Errors: FastAPI default `{detail: ...}`; validation errors 422 with field messages. IDs are integers. Timestamps ISO 8601 UTC.

Auth
- `POST /api/auth/login` body `{password}` → 200 `{user: {id, email, display_name}}`; 401 on wrong password; 429 when rate limited.
- `POST /api/auth/logout` → 204.
- `GET /api/auth/me` → `{id, email, display_name}` or 401.

Projects (all require auth; only the owner's rows)
- `GET /api/projects` → `[{id, name, description, created_at, updated_at, version_count, latest_version: {id, number, name} | null}]` ordered by `updated_at` desc.
- `POST /api/projects` body `{name, description?}` → 201 project with draft initialised from defaults. 409 if the name exists.
- `GET /api/projects/{id}` → `{id, name, description, created_at, updated_at, draft: {parameters, mission, based_on_version_id, updated_at}, version_count}`.
- `PATCH /api/projects/{id}` body `{name?, description?}` → project.
- `DELETE /api/projects/{id}` → 204 (cascades versions).
- `GET /api/projects/{id}/draft` → `{parameters, mission, based_on_version_id, updated_at}`.
- `PUT /api/projects/{id}/draft` body `{parameters, mission}` → same shape, 422 on validation failure.

Versions
- `GET /api/projects/{id}/versions` → `[{id, number, name, notes, parent_version_id, created_at}]` ordered by number desc.
- `POST /api/projects/{id}/versions` body `{name, notes?, parameters?, mission?}` → 201 full version. When `parameters`/`mission` are omitted the current draft is snapshotted. Sets `draft_based_on_version_id` to the new version. 409 if `name` already exists in the project.
- `GET /api/versions/{vid}` → `{id, project_id, number, name, notes, parameters, mission, parent_version_id, schema_version, created_at}`.
- `PATCH /api/versions/{vid}` body `{name?, notes?}` → version.
- `POST /api/versions/{vid}/duplicate` body `{name?}` → 201 new version in the same project with `parent_version_id = vid`; default name `"{name} (copy)"`, de-duplicated with a numeric suffix.
- `POST /api/versions/{vid}/restore` → 200 draft; copies the version's parameters and mission into the project draft and sets `draft_based_on_version_id`.
- `DELETE /api/versions/{vid}` → 204. If the draft was based on it, `draft_based_on_version_id` becomes null.

Parts
- `GET /api/parts/categories` → `[{key, label, description, fields: [{name, label, unit, type, required, description}]}]`.
- `GET /api/parts?category=motor&q=text` → list of parts with listings.
- `POST /api/parts` body `{category, manufacturer, model, mass_g, price_eur_estimate?, spec, source?, verified?, notes?, listings?: [...]}` → 201. 422 if spec fails the category schema.
- `GET /api/parts/{id}`, `PATCH /api/parts/{id}`, `DELETE /api/parts/{id}`.
- `POST /api/parts/{id}/listings`, `DELETE /api/parts/listings/{lid}`.

Settings
- `GET /api/settings` → `{settings: {...}, meta: {"<dotted.path>": {description, source}}}`.
- `PUT /api/settings` body full settings document → same shape.

System
- `GET /api/health` (no auth) → `{status: "ok", version, db: "ok"}`; 503 if the DB is unreachable.
- `GET /api/system/info` → `{version, phase: 1, environment, data_dir, backup: {last_run_at, next_run_at, count}}`.
- `GET /api/system/backups` → `[{name, size_bytes, created_at}]`.
- `POST /api/system/backups` → 201 created backup entry.
- `GET /api/system/backups/{name}` → file download (`application/octet-stream`). Name must match `^app-\d{8}-\d{6}\.db$`.

Static SPA: everything not under `/api` serves `frontend/dist` (copied into the image at `/app/static`). Unknown paths return `index.html` so client-side routing works. `/api/*` unknown paths return 404 JSON.

### Backups (`app/backup.py`)

Daily at `BACKUP_HOUR_UTC` an asyncio task runs `sqlite3.Connection.backup()` into `{APP_DATA_DIR}/backups/app-YYYYMMDD-HHMMSS.db`, keeps the newest `BACKUP_KEEP`, and records `last_run_at`. Restore procedure is in `docs/DEPLOYMENT.md` (download the backup in the browser; to restore, upload via a Fly machine console or ask Claude Code).

### Migrations

Alembic with one initial migration. The container entrypoint runs `alembic upgrade head` before starting uvicorn. Tests create tables from the migration (not `create_all`) so the migration is exercised.

## Frontend

Stack: Vite 6+, React 19, TypeScript strict, `react-router` v7, no UI framework. Plain CSS with variables in `src/styles/` supporting light and dark via `prefers-color-scheme`. `npm` with `package-lock.json`. Lint with `eslint` (typescript-eslint, react-hooks) and `tsc --noEmit`. Unit tests with `vitest` for pure helpers only.

```
frontend/
  index.html, vite.config.ts (dev proxy /api → http://localhost:8000), tsconfig.json, package.json, eslint.config.js
  src/
    main.tsx, App.tsx (router, auth gate)
    api/client.ts      fetch wrapper: credentials include, X-Requested-With: fetch, JSON, 401 → redirect to /login, typed errors
    api/types.ts       TS types mirroring the API schemas (Project, DesignVersion, DesignParameters, Mission, Part, Settings)
    auth/AuthContext.tsx
    pages/LoginPage.tsx, ProjectsPage.tsx, ProjectPage.tsx, SettingsPage.tsx, NotFoundPage.tsx
    layout/AppShell.tsx (top bar: project name, tabs, settings link, logout; right rail: Versions + Assistant panels)
    tabs/InputsTab.tsx, DesignTab.tsx, PartsTab.tsx, FilesTab.tsx, FlightDataTab.tsx
    panels/VersionsPanel.tsx, AssistantPanel.tsx
    components/Explain.tsx (hover/click explanation), NumberField.tsx (unit-aware number input with Explain), Modal.tsx, Toast.tsx, StatusPill.tsx, EmptyState.tsx
    lib/draft.ts (draft state, dirty tracking, debounced save), lib/format.ts (metric formatting)
    styles/tokens.css, base.css, components.css
```

Routes: `/login`, `/` (projects list), `/projects/:id` with `?tab=inputs|design|parts|files|flight`, `/settings`.

Phase 1 behaviour per screen:
- Login: password field, error message, rate-limit message.
- Projects: list, create (name + description), rename, delete with confirm.
- Project workspace: tabs left to right. Inputs tab shows the mission form (NumberFields with explanations, layout selector, scale selector) and a placeholder card for reference-image upload labelled "Phase 2". Design tab shows grouped parameter NumberFields (wing, fuselage, booms, motors, tilt or pusher, tail, nose bay, landing gear) and a placeholder for the 3D view and drawings labelled "Phase 2". Parts tab lists categories and parts from the API with a note that recommendations arrive in Phase 4. Files and Flight data tabs are placeholders with the phase that delivers them.
- Draft editing: fields update local state immediately; a debounced (800 ms) `PUT /draft` saves; a status indicator shows "Saved" / "Saving…" / "Unsaved changes" / error. A warning banner appears when `target_takeoff_mass_kg >= warn_mtow_kg` and an error state when it exceeds `design_mtow_kg`.
- Versions panel (right rail, every tab): "Save version" (name, notes), list with number, name, date; per-version menu: Restore (confirm if draft is dirty), Duplicate, Rename, Delete. Shows "Draft based on v3 (modified)" when dirty relative to the version it was restored from (compare JSON).
- Assistant panel: collapsed card "Assistant arrives in Phase 3".
- Settings page: printer envelope, limits, thresholds with description and source text, Save; backups list with "Back up now" and download links; system info.

Accessibility and polish: labelled inputs, keyboard-usable menus, no layout shift on load, 16 px gutters on narrow screens.

## End-to-end tests (`e2e/`)

Playwright (`@playwright/test`), Chromium only. `playwright.config.ts` starts the backend with `APP_PASSWORD=test-password`, a temporary `APP_DATA_DIR`, and serves the built frontend from `frontend/dist` (so the test exercises the production static-serving path). Honour `PLAYWRIGHT_BROWSERS_PATH`; when `/opt/pw-browsers/chromium` exists use it via `executablePath`.

`tests/phase1.spec.ts` covers the acceptance criterion: open URL → redirected to login → wrong password shows error → correct password → create project → set mission take-off mass → save version "v1" → change wingspan → save version "v2" → both versions listed with numbers 1 and 2 → restore v1 → wingspan reverts → reload keeps login and data → logout → protected route redirects.

## Hosting and deployment

Provider: Fly.io (see `docs/DECISIONS.md` for the reasoning and alternatives). One machine, region `lhr`, `shared-cpu-1x` 1024 MB to start, persistent volume `data` mounted at `/data`, automatic HTTPS, `auto_stop_machines = "suspend"` and `min_machines_running = 0` so idle time is cheap.

Deployment is driven entirely from GitHub Actions so the owner never installs anything:
- `deploy.yml` on push to `main` and on manual dispatch: install `flyctl`, create the app if missing (`FLY_APP_NAME` repo variable), create the volume if missing, stage secrets from GitHub secrets (`APP_SECRET_KEY`, `APP_PASSWORD`, `ANTHROPIC_API_KEY` if set), then `flyctl deploy --remote-only`. Prints the public URL.
- Required GitHub secrets: `FLY_API_TOKEN`, `APP_SECRET_KEY`, `APP_PASSWORD`. Optional: `ANTHROPIC_API_KEY`. Repository variable: `FLY_APP_NAME` (default `vtol-drone-designer`), `FLY_REGION` (default `lhr`).
- `ci.yml` on pull requests and pushes: ruff, pytest, tsc, eslint, vite build, Playwright e2e, `docker build` (no push).

Container: `python:3.12-slim-bookworm` runtime, `uv` for dependency install, non-root user, `/data` volume, port 8080, entrypoint runs migrations then `uvicorn app.main:app --host 0.0.0.0 --port 8080`. Leave a clearly commented place for the Phase 3 build stage that compiles AVL and XFOIL with `gfortran` so the layout does not change later.

## Conventions

- Python: ruff (line length 100), type hints everywhere, `from __future__ import annotations`.
- TypeScript: strict, no `any` without a comment, named exports.
- Commit messages: imperative, scoped (`backend: ...`, `frontend: ...`, `infra: ...`, `docs: ...`).
- Never put secrets, API keys or the owner's email in the repository.

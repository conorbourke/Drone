# Architecture (Phase 1 contract)

This document is the shared contract for the codebase. Builder agents and later phases must follow it. Read `docs/BRIEF.md` first for the product goals and constraints.

Phase 1 scope: hosting, login, project and version storage, parts database structure, deployment pipeline. Acceptance: the owner opens a URL, logs in, creates a project and saves two versions.

## Principles

- Browser only for the owner. Everything runs on one hosted container. The owner never runs a command.
- Single user today, but every owned row carries `owner_id` so users can be added later.
- Metric units everywhere. Field names carry the unit as a suffix: `span_mm`, `mass_g`, `speed_mps` (metres per second; never `_ms`, which reads as milliseconds), `power_w`, `energy_wh`, `price_eur`.
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
| `APP_SECRET_KEY` | yes in prod | in development a random `secrets.token_hex(32)` per process (sessions reset on restart; nothing forgeable lives in the repo) | Signs session cookies. Any long random string. |
| `APP_PASSWORD` | yes | none | Owner password (plain). Hashed in memory at startup. Must be at most 72 bytes (bcrypt limit); startup fails with a plain message otherwise. |
| `APP_PASSWORD_HASH` | no | none | bcrypt hash; if set, overrides `APP_PASSWORD`. |
| `APP_OWNER_EMAIL` | no | `owner@example.com` | Display identity of the single user. |
| `APP_DATA_DIR` | no | `./data` locally, `/data` in container | DB, files, backups live here. |
| `DATABASE_URL` | no | `sqlite:///{APP_DATA_DIR}/app.db` | SQLAlchemy URL. |
| `APP_ENV` | no | `development` | `production` enables Secure cookies, HSTS and the fail-loud startup checks; `test` is used by the test suite. |
| `APP_BASE_URL` | no | none | Public URL, used for links in later phases. |
| `ANTHROPIC_API_KEY` | no (Phase 3) | none | Claude API key, server only. |
| `BACKUP_HOUR_UTC` | no | `3` | Hour of the daily backup. |
| `BACKUP_KEEP` | no | `14` | Number of backups retained. |
| `BACKUP_ENABLED` | no | `true` | Runs the startup catch-up backup and the daily scheduler; tests and the e2e suite set `false`. Manual backups still work. |
| `APP_STATIC_DIR` | no | `/app/static` if present, else `../frontend/dist` | Directory with the built frontend. When neither exists the API still runs and `/` says the frontend is not built. |
| `APP_VERSION` | no | package version | Overrides the reported version (for example a git SHA). |

In production, startup fails loudly if `APP_SECRET_KEY` or a password is missing. Secret values (`APP_PASSWORD`, `APP_PASSWORD_HASH`, `APP_SECRET_KEY`, `ANTHROPIC_API_KEY`) are typed `pydantic.SecretStr` so they never appear in logs, tracebacks or `/api/system/info`. FastAPI `debug` is never enabled.

### Data model (`app/models.py`)

All tables have integer primary key `id`, `created_at`, `updated_at`. All datetimes are stored as UTC through a `TZDateTime` `TypeDecorator` in `db.py` that converts to UTC on bind and attaches `timezone.utc` on load (SQLite ignores `DateTime(timezone=True)`, so a plain column would silently drop the offset). Pydantic serialises every datetime with an explicit `Z`. One shared type, used by every model.

- `users`: `email` (unique), `password_hash` (nullable; the single owner authenticates against env, but the column exists for later), `display_name`, `is_owner` (bool).
- `projects`: `owner_id` FK users, `name`, `description`, `draft_parameters` (JSON), `draft_mission` (JSON), `draft_based_on_version_id` (nullable FK design_versions, `ON DELETE SET NULL` at DDL level), `draft_updated_at`, `next_version_number` (int, default 1). Unique `(owner_id, name)`.
- `design_versions`: `project_id` FK projects (`ON DELETE CASCADE`), `owner_id`, `name`, `notes`, `number` (permanent per-project label: `POST /versions` and `/duplicate` read and increment `projects.next_version_number` in the same transaction as the insert, so numbers are monotonic and never reused after a delete), `parameters` (JSON), `mission` (JSON), `parent_version_id` (nullable self FK, `ON DELETE SET NULL`). Unique `(project_id, number)` and unique `(project_id, name)` at DDL level, so the one-name-per-project rule holds under concurrent saves; `/duplicate` retries with the next `(copy N)` suffix when it loses such a race. `id` remains the target of every foreign key; `number` is only a label.
- `parts`: `category` (string enum, see Parts), `manufacturer`, `model` (name), `mass_g`, `price_eur_estimate` (nullable), `spec` (JSON validated against the category schema), `source` (text: where the spec came from), `verified` (bool, false for placeholders), `notes`. Unique `(category, manufacturer, model)`.
- `part_listings`: `part_id` FK parts (cascade), `supplier_name`, `country` (`IE` or `UK`), `url`, `price_eur` (nullable), `in_stock` (nullable bool), `last_checked_at` (nullable datetime).
- `app_settings`: `owner_id` FK users (unique), `data` (JSON, full settings document, see Settings).
- Reserved for later phases (do not create yet, but do not block): `images`, `analyses`, `flight_logs`, `calibrations`, `export_files`. Policy: rows in those tables that reference a version use `ON DELETE RESTRICT`, and `DELETE /api/versions/{vid}` returns 409 with a plain message when a version is still referenced, so flight logs and calibrations can never be orphaned silently.

#### Tables added in Phases 5 to 7

Full column lists and the reasons behind them are in the "As built" sections of `docs/phases/PHASE5.md` to `PHASE7.md`. The reserved name `export_files` became `exports`.

- `exports` (migration `0005`, `kind` added by `0007`): one file-generation job and its manifest. `kind` is `"files"` (Phase 5: print, CAD, drawings, BOM, notes) or `"moulds"` (Phase 7: mould tiles, STEP halves, PDF sheets). Files under `{APP_DATA_DIR}/files/exports/{id}/`. `version_id` is `ON DELETE CASCADE`, an intended exception to the RESTRICT policy: exports can be made again from the version and never block deleting it.
- `flight_logs` (`0006`): one uploaded ArduPilot DataFlash log and what the worker made of it (phases, statistics, chart series, comparison). File under `{APP_DATA_DIR}/files/logs/`. `version_id` (the version that flew) is RESTRICT.
- `calibrations` (`0006`): one row per applied calibration factor of a project (hover power, cruise drag, battery usable energy, structural mass), with uncertainty and source logs; deleted on undo. `version_id` is RESTRICT.
- `built_weights` (`0006`): one weighed component mass per project, next to the model's prediction.

Calibrations and built weights are per project, not per version.

JSON columns use SQLAlchemy `JSON` type. Design parameters and mission are validated by Pydantic on write; stored JSON always includes `schema_version` inside the document (there is no separate column). Upgrade policy: every schema change ships an upgrader step in `app/schemas/migrate.py` (`upgrade_parameters(doc)`, `upgrade_mission(doc)`, `upgrade_settings(doc)`); stored documents are upgraded on read and on restore and returned at the current schema version; stored rows are never rewritten in place; new fields must have defaults. Alembic `env.py` sets `render_as_batch=True` so later constraint changes work on SQLite.

### Design parameters and mission (`app/schemas/design.py`, `mission.py`, `app/defaults.py`)

`DesignParameters` (schema_version 1). All lengths mm, angles degrees, masses g. Pydantic model with field descriptions (these descriptions are the plain-language explanations; the frontend mirrors them).

```
layout: "front_tilt" | "rear_tilt" | "quad_pusher"
wing: { span_mm, root_chord_mm, tip_chord_mm, sweep_deg, dihedral_deg, incidence_deg, airfoil (str id, default "sd7037"), x_le_mm (wing root leading edge measured from the fuselage nose), z_mm (vertical offset from the fuselage centreline, 0 = mid-wing, positive = up) }
fuselage: { length_mm, width_mm, height_mm, cross_section: "ellipse" | "rounded_rect" }
booms: { count (2), lateral_offset_mm (distance from centreline), length_mm, x_offset_mm (boom front relative to wing leading edge, negative = ahead) }
motors: { front_x_mm, rear_x_mm (along boom from boom front), height_mm (above boom centreline) }
tilt: { axis_x_mm (position of tilt axis along boom), max_angle_deg (default 90) }   # ignored for quad_pusher
pusher: { prop_diameter_mm, x_mm }   # only used for quad_pusher
tail: { type: "conventional" | "v_tail" | "inverted_v" | "twin_boom_h", span_mm, chord_mm, arm_mm (wing quarter chord to tail quarter chord), height_mm }
nose_bay: { length_mm, width_mm, height_mm }   # geometry only; the payload mass range lives in Mission
landing_gear: { type: "skids" | "legs" | "none", height_mm }
```

`Mission` (schema_version 1):

```
scale: "prototype" | "final"
target_takeoff_mass_kg (default 2.5)
target_endurance_min (default 45 for prototype)
cruise_speed_mps (default 16)
payload_min_g (default 150), payload_max_g (default 400)
```

Validation is limited to shape and sanity: positive numbers, `payload_max_g >= payload_min_g`, `tip_chord_mm <= root_chord_mm`, `x_le_mm + root_chord_mm <= fuselage.length_mm`. The 24 kg design limit is a check, not an input constraint: the server never rejects a draft or version for exceeding it (otherwise the autosave would strand edits), the UI shows the warn/error banner, and the Phase 3 check reads the limit from settings at evaluation time. Defaults in `app/defaults.py` describe a plausible 2.5 kg front-tilt prototype (span 1800 mm, root chord 260, tip chord 180, fuselage 900 long, wing leading edge 300 mm from the nose, booms ±300 mm) and are explicitly labelled as starting values, not an analysed design.

### Parts catalogue (`app/parts_catalog/`)

Categories and the spec fields the engine will need. Each category has a Pydantic spec model; `GET /api/parts/categories` returns the field list (name, unit, type, description, required) generated from the models so the UI can build forms.

| Category | Spec fields |
|---|---|
| `motor` | `kv_rpm_per_v`, `resistance_ohm`, `no_load_current_a`, `max_current_a`, `max_power_w`, `lipo_cells_min`, `lipo_cells_max`, `stator_size` (str), `shaft_mm`, `mount_pattern` (str), `thrust_data` (list of {prop (str), voltage_v, throttle_pct, thrust_g, current_a, power_w, rpm}) |
| `propeller` | `diameter_mm`, `pitch_mm`, `trade_size` (optional text such as "15x5.5", shown beside the metric values), `blades`, `folding` (bool), `hub_bore_mm`, `material`, `max_rpm` (nullable) |
| `esc` | `continuous_current_a`, `burst_current_a`, `lipo_cells_min`, `lipo_cells_max`, `firmware` (str), `bec_v` (nullable), `telemetry` (bool) |
| `servo` | `torque_kg_cm`, `speed_s_per_60deg`, `voltage_min_v`, `voltage_max_v`, `gear_material`, `width_mm`, `length_mm`, `height_mm`, `digital` (bool) |
| `battery` | `chemistry` ("lipo" or "li-ion"), `cells_series`, `cells_parallel`, `capacity_mah`, `nominal_voltage_v`, `discharge_c_continuous`, `discharge_c_burst`, `length_mm`, `width_mm`, `height_mm`, `connector` |
| `cell` | `chemistry`, `capacity_mah`, `nominal_voltage_v`, `max_continuous_discharge_a`, `diameter_mm`, `length_mm`, `format` (e.g. "21700") |
| `autopilot` | `firmware` ("ardupilot"), `pwm_outputs`, `can_ports`, `uarts`, `imu_count`, `voltage_in_min_v`, `voltage_in_max_v` |
| `gps` | `constellations` (list str), `rtk` (bool), `update_rate_hz`, `interface` |
| `radio` | `kind` ("rc_link"), `frequency_mhz`, `range_km_los`, `channels`, `telemetry` (bool) |
| `telemetry` | `frequency_mhz`, `range_km_los`, `air_rate_kbps`, `interface` |
| `carbon_tube` | `outer_diameter_mm`, `inner_diameter_mm`, `length_mm`, `layup` ("pultruded" or "roll_wrapped"), `mass_per_m_g`, `youngs_modulus_gpa` (nullable), `tensile_strength_mpa` (nullable) |

All parts carry `mass_g` at the top level; dimensions live inside each category's spec (for example `length_mm`, `width_mm`, `height_mm` on batteries and servos) because they mean different things per category. Seed file `backend/seed/parts.example.json` contains a few clearly labelled example entries with `verified: false` and `source: "example placeholder, not verified"`. Phase 4 seeds real components. The loader (`python -m app.parts_catalog.load seed/parts.example.json`) upserts by `(category, manufacturer, model)` and is idempotent; the app does not auto-seed in production.

### Settings document (`app/schemas/settings.py`, defaults in `app/defaults.py`)

```
printer: { name: "Bambu Lab P2S", build_volume_mm: {x:256,y:256,z:256}, usable_envelope_mm: {x:240,y:240,z:240} }
limits: { design_mtow_kg: 24.0, legal_mtow_kg: 25.0, warn_mtow_kg: 23.0 }
checks: {
  hover_thrust_to_weight_min: 2.0,
  static_margin_min: 0.05, static_margin_max: 0.20,
  cruise_to_stall_speed_ratio_min: 1.3 (cruise speed must be at least 1.3 × stall speed; stored the way the source states it),
  battery_reserve_fraction: 0.20,
  battery_current_max_fraction_of_rating: 0.80 (peak draw at most 80 % of the pack's continuous rating),
}
units: { system: "metric" }
```

Invariants enforced by `PUT /api/settings` with plain-language 422 messages: `warn_mtow_kg <= design_mtow_kg <= legal_mtow_kg <= 25`; `usable_envelope_mm <= build_volume_mm` per axis and all positive; `0 < static_margin_min < static_margin_max`; `battery_reserve_fraction` and `battery_current_max_fraction_of_rating` in (0, 1); `hover_thrust_to_weight_min > 1`; `cruise_to_stall_speed_ratio_min >= 1`.

The document carries `schema_version: 1`. Each threshold has a `description` and `source` string in the API response (from a static table in `defaults.py`) so the UI can show them. Sources are marked "proposed, confirm in Phase 3" where the brief asks for owner confirmation. `GET /api/settings` merges stored overrides over defaults; `PUT /api/settings` validates the full document and persists only the keys whose value differs from `DEFAULT_SETTINGS` (diff computed server-side), so improved defaults in later phases reach the owner unless they changed that value themselves. `meta` reports `label`, `description`, `source` and `is_default` per dotted path; the UI renders labels from here and never derives them from keys.

### Authentication (`app/security.py`, `app/routers/auth.py`)

- Single owner. Password verified against bcrypt hash derived from env at startup. Timing-safe.
- On success set cookie `vtol_session`: `itsdangerous.URLSafeTimedSerializer(secret, salt="session")` token containing `{"uid": user.id, "pw": fingerprint}`; `HttpOnly`, `SameSite=Lax`, `Secure` when `APP_ENV=production`, `Path=/`, max age 30 days. The fingerprint must be stable across restarts (a deploy must not sign the owner out): with `APP_PASSWORD_HASH` it is `sha256(hash)[:16]`; with `APP_PASSWORD` (whose bcrypt hash is re-salted on every start) it is `HMAC-SHA256(secret_key, password)[:16]`. Either way, changing the password or the secret signs out every device (documented in `docs/DEPLOYMENT.md`). The server checks `max_age` and the fingerprint on every request.
- Passwords longer than 72 bytes (byte length, not characters) are rejected before bcrypt is called (401, counted as a failed attempt) because bcrypt 5 raises on them.
- Scope: every `/api` route requires `current_user` except `POST /api/auth/login` and `GET /api/health`. Enforce it as router-level `dependencies=[Depends(current_user)]` on every router, and ship a pytest that walks `app.routes` and asserts 401 for every `/api` path outside that allowlist.
- Headers: one middleware adds `Cache-Control: no-store` on `/api/*`, `X-Content-Type-Options: nosniff`, `Referrer-Policy: same-origin`, `X-Frame-Options: DENY`, and `Strict-Transport-Security: max-age=31536000` when `APP_ENV=production`. No `CORSMiddleware`: the SPA is same-origin and Vite proxies `/api` in development.
- Request bodies: `/api` requests with `Content-Length` above 1 MB are refused with 413 before the body is read, and chunked bodies without a length get 411 (the largest legitimate body is a settings or draft document; Phase 2 uploads get their own route and limit).
- Misconfiguration at startup (missing secret or password in production, a password over 72 bytes) exits with the plain messages only: `Settings` hides input values in validation errors and `get_settings()` converts them to a clean process exit, so the Fly log never shows a secret.
- Rate limit: 5 failed logins per 15 minutes per client IP, plus a global bucket of 30 failures per 15 minutes across all IPs (in-memory; resets on restart, which is acceptable). Client IP is the `Fly-Client-IP` header when `APP_ENV=production` (set by Fly's proxy from its own view of the connection), else `request.client.host`. Never parse `X-Forwarded-For`: its leftmost entries are client-supplied. Respond 429 with a plain message.
- CSRF: cookie is SameSite=Lax; additionally every state-changing `/api` request must carry header `X-Requested-With: fetch` (frontend client sets it) or it is rejected with 403. GET never mutates.
- `GET /api/auth/me` returns the user or 401. Unauthenticated API calls return 401 JSON `{detail: "..."}`; the SPA redirects to `/login`.

### API (all JSON, prefix `/api`)

Errors: FastAPI default `{detail: ...}`; validation errors 422 with `detail: [{type, loc, msg}]` (the offending input is not echoed back, so a NaN or Infinity in a request can never break the error response). Documents reject non-finite numbers (`allow_inf_nan=False`). IDs are integers. Timestamps ISO 8601 UTC.

Auth
- `POST /api/auth/login` body `{password}` → 200 `{user: {id, email, display_name}}`; 401 on wrong password; 429 when rate limited.
- `POST /api/auth/logout` → 204.
- `GET /api/auth/me` → `{id, email, display_name}` or 401.

Projects (all require auth; only the owner's rows)
- `GET /api/projects` → `[{id, name, description, created_at, updated_at, version_count, latest_version: {id, number, name} | null, next_version_number}]` ordered by `updated_at` desc.
- `POST /api/projects` body `{name, description?}` → 201 project with draft initialised from defaults. 409 if the name exists.
- `GET /api/projects/{id}` → `{id, name, description, created_at, updated_at, draft: {parameters, mission, based_on_version_id, updated_at}, version_count, next_version_number}` (the UI uses `next_version_number` for the suggested name of the next version).
- `PATCH /api/projects/{id}` body `{name?, description?}` → project.
- `DELETE /api/projects/{id}` → 204 (cascades versions).
- `GET /api/projects/{id}/draft` → `{parameters, mission, based_on_version_id, updated_at}`.
- `PUT /api/projects/{id}/draft` body `{parameters, mission}` → same shape, 422 on validation failure.

Versions
- `GET /api/projects/{id}/versions` → `[{id, number, name, notes, parent_version_id, created_at}]` ordered by number desc.
- `POST /api/projects/{id}/versions` body `{name, notes?, parameters?, mission?}` → 201 full version. When `parameters`/`mission` are omitted the current draft is snapshotted, `parent_version_id` is set to the draft's `based_on_version_id`, and `draft_based_on_version_id` then points at the new version. When `parameters`/`mission` are supplied explicitly, `parent_version_id` is null and the draft pointer is left untouched. 409 if `name` already exists in the project.
- `GET /api/versions/{vid}` → `{id, project_id, number, name, notes, parameters, mission, parent_version_id, created_at}` (documents are returned upgraded to the current schema version).
- `PATCH /api/versions/{vid}` body `{name?, notes?}` → version.
- `POST /api/versions/{vid}/duplicate` body `{name?}` → 201 new version in the same project with `parent_version_id = vid`; default name `"{name} (copy)"`, de-duplicated with a numeric suffix.
- `POST /api/versions/{vid}/restore` → 200 draft; copies the version's parameters and mission into the project draft and sets `draft_based_on_version_id`.
- `DELETE /api/versions/{vid}` → 204. The DDL `ON DELETE SET NULL` clears `draft_based_on_version_id` and any child's `parent_version_id`; a version referenced by a later-phase RESTRICT table returns 409.

Parts
- `GET /api/parts/categories` → `[{key, label, description, fields: [{name, label, unit, type, required, description}]}]`.
- `GET /api/parts?category=motor&q=text` → list of parts with listings.
- `POST /api/parts` body `{category, manufacturer, model, mass_g, price_eur_estimate?, spec, source?, verified?, notes?, listings?: [...]}` → 201. 422 if spec fails the category schema.
- `GET /api/parts/{id}`, `PATCH /api/parts/{id}` (an explicit `null` for a non-nullable field is a 422, not a conflict; `manufacturer`, `model` and `supplier_name` are stripped and may not be blank), `DELETE /api/parts/{id}`.
- `POST /api/parts/{id}/listings`, `DELETE /api/parts/listings/{lid}`.

Settings
- `GET /api/settings` → `{settings: {...}, meta: {"<dotted.path>": {label, description, source, is_default}}, warnings: [...]}`. Stored overrides that no longer fit the current defaults (a retired key, or a value that now breaks an invariant) are dropped for that path and reported in `warnings` instead of failing the request, so the Settings page can always load.
- `PUT /api/settings` body full settings document → same shape (only values that differ from the defaults are stored).

System
- `GET /api/health` (no auth) → `{status: "ok", version, db: "ok"}`; 503 if the DB is unreachable.
- `GET /api/system/info` → `{version, phase: 1, environment, data_dir, backup: {last_run_at, next_run_at, count}}`.
- `GET /api/system/backups` → `[{name, size_bytes, created_at}]`.
- `POST /api/system/backups` → 201 created backup entry.
- `GET /api/system/backups/{name}` → file download (`application/octet-stream`, `Content-Disposition: attachment`, `Cache-Control: no-store`). Name must match `^app-\d{8}-\d{6}\.db$` and the resolved path must stay inside the backups directory.

Schema (plain-language explanations served from one source of truth)
- `GET /api/schema/design` and `GET /api/schema/mission` → `{"<dotted.path>": {label, unit, type, description, min?, max?, enum?: [{value, label, note?}]}}` generated from the Pydantic models' field metadata. The frontend renders labels, units and `Explain` text from these; nothing is hand-copied into TypeScript. For `layout`, the `rear_tilt` option carries the note "Less common in ArduPilot than front tilt. ArduPilot supports it through Q_TILT_MASK; check your setup in a simulator before flying." (text changed in Phase 2).
- `GET /api/schema/notes` → `{defaults: "..."}`: the plain-language note that a new project's numbers are starting values, not an analysed design; the Inputs and Design tabs show it.

Files (Phase 5; details in `docs/phases/PHASE5.md` section 6)
- `POST /api/projects/{id}/exports` `{source}` → 202 job; `GET /api/projects/{id}/exports`; `GET`/`DELETE /api/exports/{eid}`.
- `GET /api/exports/{eid}/files/{path}`, `GET /api/exports/{eid}/zip`, `GET /api/exports/{eid}/pieces/{piece_id}/mesh`.

Flight data (Phase 6; details in `docs/phases/PHASE6.md` section 8)
- `GET /api/flight-data/guide`.
- `POST /api/projects/{id}/flight-logs?filename=…` (raw `.bin`/`.log` body, streamed, at most 200 MB; `.tlog` refused with 415) and `POST /api/projects/{id}/flight-logs/sample` → 202 job; `GET /api/projects/{id}/flight-logs`.
- `GET`/`PATCH`/`DELETE /api/flight-logs/{lid}`, `GET /api/flight-logs/{lid}/series`, `POST /api/flight-logs/{lid}/reprocess`.
- `GET`/`POST`/`DELETE /api/projects/{id}/calibration`, `GET /api/projects/{id}/calibration/preview`.
- `GET`/`PUT /api/projects/{id}/built-weights`.

Moulds and full scale (Phase 7; details in `docs/phases/PHASE7.md` section 4)
- `POST /api/projects/{id}/moulds` `{source, parts?, min_draft_deg?, vent_channels?}` → 202 job; `GET /api/projects/{id}/moulds`; `GET`/`DELETE /api/moulds/{mid}`.
- `GET /api/moulds/{mid}/files/{path}`, `GET /api/moulds/{mid}/zip`, `GET /api/moulds/{mid}/tiles/{tile_id}/mesh`.
- `POST /api/projects/{id}/fullscale` `{source}` → full-scale checks (synchronous).

Like the Phase 2 image upload, the flight-log upload has its own body limit (200 MB, only for a signed-in owner); every other `/api` route keeps the 1 MB limit.

Static SPA: Vite's hashed output is mounted with Starlette `StaticFiles` at `/assets`; a fixed allowlist of root files from `frontend/dist` (`favicon.svg`, `manifest.webmanifest`, `robots.txt`) is served by explicit routes; every other non-`/api` GET returns `FileResponse(static_dir / "index.html")` unconditionally, so client-side routing works and no filesystem path is ever derived from the URL. `/api/*` unknown paths return 404 JSON ahead of the catch-all. `frontend/dist` is copied into the image at `/app/static`.

### Backups (`app/backup.py`)

Two layers, with honest labels:

1. **Undo-a-mistake backups (in app).** Daily at `BACKUP_HOUR_UTC` an asyncio task runs `sqlite3.Connection.backup()` into `{APP_DATA_DIR}/backups/app-YYYYMMDD-HHMMSS.db`, keeps the newest `BACKUP_KEEP`, and records `last_run_at`. On startup, if the newest backup is older than 24 h (or none exists), a backup runs immediately, so a restarted machine never silently skips a day. The app creates the `backups` directory itself with `mkdir -p`. The owner can download any backup in the browser; that download is the owner's off-site copy. These files live on the same volume as the database, so they are not disaster protection.
2. **Disaster recovery (Fly volume snapshots).** Fly takes daily snapshots of the volume; `snapshot_retention = 14` in `fly.toml` keeps two weeks. Restore is a Claude Code task (not browser-only, and the Fly dashboard has no shell): either recreate the volume from a snapshot (`fly volumes create data --snapshot-id <id>` then redeploy) or copy the owner's downloaded backup file into `/data` with `fly ssh sftp` and restart. `docs/DEPLOYMENT.md` documents both paths and promises nothing else.

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
    api/types.ts       TS types mirroring the API schemas (Project, DesignVersion, DesignParameters, Mission, Part, Settings, FieldMeta)
    api/schema.ts      loads /api/schema/design and /api/schema/mission once; labels, units and explanations come from here
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
- Project workspace: tabs left to right. Inputs tab shows the mission form (NumberFields with explanations, layout selector, scale selector) and a placeholder card for reference-image upload labelled "Phase 2". The layout selector shows the `rear_tilt` note from the schema endpoint ("Less common in ArduPilot; support to be confirmed in Phase 2") through the `Explain` component. Design tab shows grouped parameter NumberFields (wing, fuselage, booms, motors, tilt or pusher, tail, nose bay, landing gear) and a placeholder for the 3D view and drawings labelled "Phase 2". Parts tab lists categories and parts from the API with a note that recommendations arrive in Phase 4. Files and Flight data tabs are placeholders with the phase that delivers them.
- Draft editing: fields update local state immediately; a debounced (800 ms) `PUT /draft` saves; a status indicator shows "Saved" / "Saving…" / "Unsaved changes" / error. A warning banner appears when `target_takeoff_mass_kg >= warn_mtow_kg` and an error state when it exceeds `design_mtow_kg`.
- Versions panel (right rail, every tab): "Save version" (name, notes), list with number, name, date; per-version menu: Restore (confirm if draft is dirty), Duplicate, Rename, Delete. Shows "Draft based on v3 (modified)" when dirty relative to the version it was restored from (compare JSON).
- Assistant panel: collapsed card "Assistant arrives in Phase 3".
- Settings page: printer envelope, limits, thresholds with description and source text, Save; backups list with "Back up now" and download links; system info.

Accessibility and polish: labelled inputs, keyboard-usable menus, no layout shift on load, 16 px gutters on narrow screens.

Test IDs (stable `data-testid` attributes the e2e suite relies on; do not rename):
`login-password`, `login-submit`, `login-error`; `projects-new`, `project-name`, `project-description`, `project-create`, `project-card` (one per project, with `data-project-id`), `project-delete`; `tab-inputs`, `tab-design`, `tab-parts`, `tab-files`, `tab-flight`; every mission or design input is `field-<dotted.path>` (for example `field-target_takeoff_mass_kg`, `field-wing.span_mm`, `field-layout`); `draft-status` (text: "Saved", "Saving…", "Unsaved changes", or an error), `mtow-banner`; `versions-save`, `version-name`, `version-notes`, `version-save-confirm`, `version-item` (one per version, with `data-version-number` and `data-version-id`), `version-menu`, `version-restore`, `version-duplicate`, `version-rename`, `version-delete`, `confirm-ok`, `confirm-cancel`, `draft-basis` (text such as "Draft based on v1" or "Draft based on v1 (modified)"); `logout`, `settings-link`, `backup-now`.

## End-to-end tests (`e2e/`)

Playwright (`@playwright/test`), Chromium only. `playwright.config.ts` starts the backend with `APP_PASSWORD=test-password`, a temporary `APP_DATA_DIR`, and serves the built frontend from `frontend/dist` (so the test exercises the production static-serving path). Honour `PLAYWRIGHT_BROWSERS_PATH`; when `/opt/pw-browsers/chromium` exists use it via `executablePath`.

`tests/phase1.spec.ts` covers the acceptance criterion: open URL → redirected to login → wrong password shows error → correct password → create project → set mission take-off mass (`field-target_takeoff_mass_kg`) → save version "v1" → change wingspan (`field-wing.span_mm`) and wait for `draft-status` to read "Saved" → save version "v2" → both versions listed with numbers 1 and 2 → restore v1 → wingspan reverts → reload keeps login and data → logout → protected route redirects. A second spec checks that `/api/system/backups/<name>` without a session returns 401 and that the SPA fallback never serves a file outside `dist`. A third spec covers the Settings page: every row has an explanation, an impossible value is refused with a plain message and not stored, a valid change persists across a reload, and a backup can be made and downloaded.

## Hosting and deployment

Provider: Fly.io (see `docs/DECISIONS.md` for the reasoning and alternatives). Exactly one machine, one volume, always on (no auto-stop: an in-process backup scheduler and a SQLite file cannot live on a machine that is stopped or suspended when idle; always-on shared-cpu-1x with 1 GB is about USD 7 a month, well inside the budget).

`fly.toml` (pinned here because the details matter):

```
app = "vtol-drone-designer"        # overridden by the FLY_APP_NAME repository variable via --app
primary_region = "lhr"              # the single source of truth for the region; no FLY_REGION variable

[build]
  dockerfile = "Dockerfile"

[env]
  APP_ENV = "production"
  APP_DATA_DIR = "/data"

[http_service]
  internal_port = 8080
  force_https = true
  auto_stop_machines = "off"
  auto_start_machines = true
  min_machines_running = 1

  [[http_service.checks]]
    method = "GET"
    path = "/api/health"
    interval = "15s"
    timeout = "5s"
    grace_period = "30s"

[mounts]
  source = "data"
  destination = "/data"
  initial_size = "3gb"
  snapshot_retention = 14

[[vm]]
  size = "shared-cpu-1x"
  memory = "1gb"
```

Deployment is driven entirely from GitHub Actions so the owner never installs anything:
- **Token:** the owner creates a *personal access token* (or an org token) in the Fly dashboard in the browser and stores it as the `FLY_API_TOKEN` GitHub secret. Not an app-scoped deploy token: that needs the CLI to create and cannot create the app or recreate Fly's remote builder.
- **No remote builder.** `deploy.yml` builds the image on the GitHub runner and deploys with `flyctl deploy --local-only --ha=false --app "$FLY_APP_NAME"`. `--ha=false` is mandatory: the default creates two machines, which with a volume mount means two SQLite databases. The `[mounts] initial_size` lets the first deploy create exactly one volume; there is no separate volume-create step (volume names are not unique, so a bare create is not idempotent).
- **Steps in `deploy.yml`** (on push to `main` and on manual dispatch; `concurrency: deploy` so two runs cannot race the bootstrap): install `flyctl`; `flyctl apps create "$FLY_APP_NAME" --org personal` only if `flyctl apps list --json` does not already contain it, and on a name clash print a plain-language message telling the owner to set the `FLY_APP_NAME` repository variable to a unique name and re-run; stage secrets (`APP_SECRET_KEY`, `APP_PASSWORD`, and `ANTHROPIC_API_KEY` when set) with `flyctl secrets set --stage`, treating Fly's non-zero "No change detected" exit as success; `flyctl deploy --local-only --ha=false`; print the public URL `https://$FLY_APP_NAME.fly.dev`.
- Required GitHub secrets: `FLY_API_TOKEN`, `APP_SECRET_KEY`, `APP_PASSWORD`. Optional: `ANTHROPIC_API_KEY` (staged only when the secret exists; nothing reads it until Phase 3). Repository variable: `FLY_APP_NAME` (falls back to `vtol-drone-designer`).
- Hygiene: every action pinned to a release tag; top-level `permissions: contents: read`; secret values are exposed to the step through `env:` and passed to `flyctl secrets set --stage` as individually quoted `KEY=VALUE` arguments (`"APP_PASSWORD=$APP_PASSWORD"`), never interpolated into the script text, so `#`, quotes, spaces and `=` reach Fly unchanged. The `secrets import` line format is deliberately not used: its dotenv-style parser strips quotes and cuts values at `#`. The Fly token is org-scoped because the workflow creates the app; `docs/DEPLOYMENT.md` says so plainly and tells the owner how to rotate it in the Fly dashboard.
- `ci.yml` on pull requests and pushes: ruff, pytest, tsc, eslint, vite build, Playwright e2e, `docker build` (no push).

Container: `python:3.12-slim-bookworm` runtime, `uv` for dependency install, `/data` volume, port 8080. The final image user is a non-root `app` user set with `USER app` (no gosu or su-exec entrypoint: Fly chowns the mount to the image's `USER`, and the volume root contains a root-owned `lost+found`, so the app must never chown `/data` recursively). The entrypoint runs `alembic upgrade head` then `uvicorn app.main:app --host 0.0.0.0 --port 8080 --proxy-headers --forwarded-allow-ips='*'` so the app sees the request scheme behind Fly's proxy; the login rate limiter keys on `Fly-Client-IP` in production (see Authentication). Leave a clearly commented place for the Phase 3 build stage that compiles AVL and XFOIL with `gfortran` so the layout does not change later.

## Conventions

- Python: ruff (line length 100), type hints everywhere, `from __future__ import annotations`.
- TypeScript: strict, no `any` without a comment, named exports.
- Commit messages: imperative, scoped (`backend: ...`, `frontend: ...`, `infra: ...`, `docs: ...`).
- Never put secrets, API keys or the owner's email in the repository. `.gitignore` and `.dockerignore` list `.env`, `data/`, `*.db`, `backups/`, `node_modules/`, `.venv/`, `dist/`, `test-results/`, `playwright-report/`.

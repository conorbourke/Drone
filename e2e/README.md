# End-to-end tests

Playwright (`@playwright/test`, Chromium only) drives the **built** app: the real backend is
started from `backend/` through its entrypoint (Alembic migration, then uvicorn) and serves
`frontend/dist` itself, so the suite exercises the production static-serving path.

```
cd frontend && npm ci && npm run build      # the suite serves frontend/dist
cd backend  && uv sync                      # the backend's virtual environment
cd e2e      && npm ci && npx playwright install chromium   # once, unless Chromium is preinstalled
cd e2e      && npm test
```

- `tests/phase1.spec.ts`: the Phase 1 acceptance story (redirect to login, wrong password,
  sign in, create a project, edit the mission, save v1, change the wingspan, save v2, numbers
  1 and 2, restore, reload, log out) plus project deletion with confirmation.
- `tests/security.spec.ts`: `/api/system/backups/<name>` and every other `/api` route need a
  session, backup name hardening, the CSRF header, the SPA fallback never serving a file
  outside `dist`, and the security headers.

`playwright.config.ts` starts the backend on port 4173 with `APP_PASSWORD=test-password`,
`APP_ENV=development`, `BACKUP_ENABLED=false` and a data directory that
`scripts/start-backend.sh` wipes before every start (default `<tmpdir>/vtol-e2e-data`;
override with `E2E_DATA_DIR`, the name must start with `vtol-e2e`). Set `E2E_PORT` to use
another port. Outside CI a server already answering on that port is reused. A preinstalled
Chromium build 1194 under `PLAYWRIGHT_BROWSERS_PATH` or `/opt/pw-browsers` is used directly;
otherwise Playwright resolves its own download.

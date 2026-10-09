# Backend

FastAPI application (package `app`) with SQLite storage, Alembic migrations and static serving of
the built frontend. See `docs/ARCHITECTURE.md` at the repository root for the contract.

Common commands (run from this directory):

```
uv sync                                   # create .venv and install everything (incl. dev tools)
uv run alembic upgrade head               # create or migrate the database in $APP_DATA_DIR
APP_PASSWORD=... uv run uvicorn app.main:app --reload --port 8000
uv run pytest -q
uv run ruff check . && uv run ruff format --check .
uv run python -m app.parts_catalog.load seed/parts.example.json   # idempotent example parts
./entrypoint.sh                           # what the container runs (migrate, then serve on $PORT)
```

Phase 2 additions:

```
uv run python scripts/build_airfoil_tables.py   # rewrite NACA .dat files and airfoil_polars.json (XFOIL, ~40 s)
```

- `uv sync` builds the `xfoil` package from source: it needs `gfortran` and `cmake` on the PATH.
- Image reading uses `ANTHROPIC_API_KEY` (and optionally `CLAUDE_MODEL`, default
  `claude-opus-5-5`). Without a key the endpoint answers 503 with a plain message.
- Tests and the e2e suite set `CLAUDE_FAKE_RESPONSE_FILE=tests/fixtures/vision_fake_response.json`
  (or `vision_fake_refusal.json`) so no API call is made; production ignores it.
- Uploaded images live in `$APP_DATA_DIR/files/images/{project_id}/`.

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

#!/bin/sh
# Container entrypoint: migrate the database, then serve the API and the built frontend.
# Works as a non-root user: it writes only to $APP_DATA_DIR (the database and backups).
set -eu

HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

# Prefer the project's own virtual environment when it exists and is not already active.
if [ -x "$HERE/.venv/bin/uvicorn" ]; then
    PATH="$HERE/.venv/bin:$PATH"
    export PATH
fi

PORT="${PORT:-8080}"

echo "entrypoint: applying database migrations"
alembic upgrade head

echo "entrypoint: starting uvicorn on 0.0.0.0:${PORT}"
exec uvicorn app.main:app \
    --host 0.0.0.0 \
    --port "$PORT" \
    --proxy-headers \
    --forwarded-allow-ips='*'

#!/bin/sh
# Starts the backend for the Playwright suite on a fresh, empty data directory.
#
# playwright.config.ts runs this as its webServer command with APP_DATA_DIR, APP_PASSWORD,
# APP_SECRET_KEY, APP_ENV, APP_STATIC_DIR, BACKUP_ENABLED and PORT in the environment. The
# data directory is wiped and recreated here, right before the server starts, so every run
# begins with an empty database and the Alembic migration is exercised each time.
set -eu

HERE="$(cd "$(dirname "$0")" && pwd)"
BACKEND="$(cd "$HERE/../../backend" && pwd)"

: "${APP_DATA_DIR:?APP_DATA_DIR must be set (playwright.config.ts sets it)}"

# Only ever clear a directory that is clearly ours.
case "$(basename "$APP_DATA_DIR")" in
    vtol-e2e*) ;;
    *)
        echo "start-backend.sh: refusing to clear '$APP_DATA_DIR' (expected a name starting with vtol-e2e)" >&2
        exit 1
        ;;
esac

rm -rf "$APP_DATA_DIR"
mkdir -p "$APP_DATA_DIR"
echo "start-backend.sh: fresh data directory $APP_DATA_DIR"

cd "$BACKEND"

# entrypoint.sh runs `alembic upgrade head` and then uvicorn on $PORT. Prefer uv so the
# project's virtual environment is created or synced when needed; fall back to the existing
# venv (entrypoint.sh prepends .venv/bin to PATH itself) when uv is not installed.
if command -v uv >/dev/null 2>&1; then
    exec uv run ./entrypoint.sh
fi
exec ./entrypoint.sh

# VTOL Drone Designer — single container: FastAPI API + built React SPA.
# Built on the GitHub Actions runner and deployed to Fly.io (see .github/workflows/deploy.yml).

# ---------------------------------------------------------------------------
# Stage 1: build the frontend
# ---------------------------------------------------------------------------
FROM node:22-bookworm-slim AS frontend
WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# ---------------------------------------------------------------------------
# Stage 2 (reserved for Phase 3): compile the aerodynamics tools.
# AVL and XFOIL are Fortran programs; they will be built here with gfortran
# and copied into the runtime image, so the runtime stage below does not need
# a compiler. Keep this stage in place so the layout does not change later.
#
# FROM debian:bookworm-slim AS aero-tools
# RUN apt-get update && apt-get install -y --no-install-recommends gfortran make ca-certificates curl \
#  && rm -rf /var/lib/apt/lists/*
# ... download and build AVL and XFOIL into /opt/aero/bin/{avl,xfoil}
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Stage 3: runtime
# ---------------------------------------------------------------------------
FROM python:3.12-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PYTHON=/usr/local/bin/python3.12 \
    UV_PROJECT_ENVIRONMENT=/app/.venv

# uv: fast, reproducible installs from uv.lock. Pinned to the version used to create the lock.
COPY --from=ghcr.io/astral-sh/uv:0.11.32 /uv /uvx /usr/local/bin/

RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl sqlite3 \
 && rm -rf /var/lib/apt/lists/* \
 && groupadd --system --gid 1000 app \
 && useradd --system --uid 1000 --gid app --home-dir /app --shell /usr/sbin/nologin app \
 && mkdir -p /app /data \
 && chown app:app /app /data

WORKDIR /app

# Install dependencies first (cached unless the lock changes), then the app itself.
COPY --chown=app:app backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY --chown=app:app backend/ ./
RUN uv sync --frozen --no-dev && chmod +x /app/entrypoint.sh

# Built SPA, served by the API process.
COPY --from=frontend --chown=app:app /build/frontend/dist /app/static

# Phase 3 will add: COPY --from=aero-tools /opt/aero/bin/ /usr/local/bin/

ENV APP_ENV=production \
    APP_DATA_DIR=/data \
    APP_STATIC_DIR=/app/static \
    PORT=8080 \
    PATH="/app/.venv/bin:${PATH}"

# Final image user is the non-root app user. Fly chowns the /data mount to this user at attach time.
# Never drop privileges in the entrypoint instead; see docs/ARCHITECTURE.md.
USER app
EXPOSE 8080
VOLUME ["/data"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD curl -fsS http://127.0.0.1:8080/api/health || exit 1

ENTRYPOINT ["/app/entrypoint.sh"]

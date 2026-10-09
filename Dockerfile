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
# Stage 2: build the Python environment (Phase 2).
# The xfoil package (XFOIL compiled from Fortran, used to build and later to extend the airfoil
# tables) is built from source here, so this stage carries the compilers; the runtime stage
# below only needs the Fortran runtime library.
# ---------------------------------------------------------------------------
FROM python:3.12-slim-bookworm AS backend-build

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PYTHON=/usr/local/bin/python3.12 \
    UV_PROJECT_ENVIRONMENT=/app/.venv

# uv: fast, reproducible installs from uv.lock. Pinned to the version used to create the lock.
COPY --from=ghcr.io/astral-sh/uv:0.11.32 /uv /uvx /usr/local/bin/

RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential gfortran cmake git ca-certificates \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY backend/pyproject.toml backend/uv.lock ./
# The backend is not an installable package (tool.uv package = false): this installs only the
# locked dependencies, xfoil included, into /app/.venv.
RUN uv sync --frozen --no-dev --no-install-project

# ---------------------------------------------------------------------------
# Stage 3 (reserved for Phase 3): further aerodynamics tools.
# AVL comes from the optvl wheel (a Python dependency in uv.lock, installed by stage 2), so no
# separate Fortran build is expected. If a tool ever needs its own compiled binary, build it in
# a stage here and copy only the result into the runtime image, for example:
#
# FROM backend-build AS aero-tools
# RUN ... build into /opt/aero/bin/
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Stage 4: runtime
# ---------------------------------------------------------------------------
FROM python:3.12-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PYTHON=/usr/local/bin/python3.12 \
    UV_PROJECT_ENVIRONMENT=/app/.venv

# uv stays available in the image for maintenance commands; nothing installs at start-up.
COPY --from=ghcr.io/astral-sh/uv:0.11.32 /uv /uvx /usr/local/bin/

# libgfortran5 libgl1 is the only runtime library the compiled xfoil extension needs.
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl sqlite3 libgfortran5 libgl1 \
 && rm -rf /var/lib/apt/lists/* \
 && groupadd --system --gid 1000 app \
 && useradd --system --uid 1000 --gid app --home-dir /app --shell /usr/sbin/nologin app \
 && mkdir -p /app /data \
 && chown app:app /app /data

WORKDIR /app

# The ready-built virtual environment (same base image, so its python symlink resolves).
COPY --from=backend-build --chown=app:app /app/.venv /app/.venv
# The app itself (backend/.venv and local data are excluded by .dockerignore).
COPY --chown=app:app backend/ ./
# The import check fails the build if the copied environment or the Fortran runtime is broken.
RUN chmod +x /app/entrypoint.sh \
 && /app/.venv/bin/python -c "import anthropic, fastapi, PIL, python_multipart; from xfoil import XFoil; XFoil()"

# Built SPA, served by the API process.
COPY --from=frontend --chown=app:app /build/frontend/dist /app/static

# Phase 3 will add (only if a tool needs its own binary): COPY --from=aero-tools /opt/aero/bin/ /usr/local/bin/

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

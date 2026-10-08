# Developer shortcuts (used by Claude Code during development; the owner never runs these).
SHELL := /bin/bash
BACKEND := backend
FRONTEND := frontend
E2E := e2e
SCRATCH ?= /tmp/vtol-dev
export PLAYWRIGHT_BROWSERS_PATH ?= /opt/pw-browsers

.PHONY: install install-backend install-frontend install-e2e dev dev-backend dev-frontend \
        lint test unit e2e build docker-build docker-run clean

install: install-backend install-frontend install-e2e

install-backend:
	cd $(BACKEND) && uv sync

install-frontend:
	cd $(FRONTEND) && npm ci --no-audit --no-fund

install-e2e:
	cd $(E2E) && npm ci --no-audit --no-fund

## Run the API on :8000 (development settings) and the Vite dev server on :5173 with /api proxied.
dev:
	@echo "Run 'make dev-backend' and 'make dev-frontend' in two terminals."

dev-backend:
	mkdir -p $(SCRATCH)
	cd $(BACKEND) && APP_ENV=development APP_PASSWORD=dev-password APP_SECRET_KEY=dev-secret \
		APP_DATA_DIR=$(SCRATCH) uv run uvicorn app.main:app --reload --port 8000

dev-frontend:
	cd $(FRONTEND) && npm run dev

lint:
	cd $(BACKEND) && uv run ruff check . && uv run ruff format --check .
	cd $(FRONTEND) && npm run typecheck && npm run lint

unit:
	cd $(BACKEND) && uv run pytest -q
	cd $(FRONTEND) && npm test

build:
	cd $(FRONTEND) && npm run build

e2e: build
	cd $(E2E) && npx playwright test

test: lint unit e2e

docker-build:
	docker build -t vtol-drone-designer:local .

docker-run:
	docker compose up --build

clean:
	rm -rf $(FRONTEND)/dist $(E2E)/test-results $(E2E)/playwright-report $(BACKEND)/.pytest_cache $(BACKEND)/.ruff_cache

.PHONY: setup setup-backend setup-frontend test test-backend test-frontend test-e2e lint format run dev dev-wsl run-api run-frontend run-huey build-frontend install uninstall service-status service-start service-stop clean benchmark

# Default Python and Node
PYTHON ?= python3
PIP ?= pip
NPM ?= npm
# Extra flags forwarded to the Vite dev server (e.g. VITE_ARGS=--host to expose on the LAN/WSL2).
VITE_ARGS ?=
# Backend API port for the dev stack. Forwarded to BOTH the API (YELIZTLI_PORT) and
# the Vite proxy (VITE_API_PORT) so `make dev API_PORT=8010` moves the whole stack off
# 8000 — e.g. when a foreign process holds :8000 on WSL2. Defaults to any exported
# YELIZTLI_PORT, else 8000. Only `dev`/`dev-wsl` apply it; `make run` is untouched.
API_PORT ?= $(or $(YELIZTLI_PORT),8000)

# ──────────────────────────────────────────────
# Setup
# ──────────────────────────────────────────────

setup: setup-backend setup-frontend  ## Full project setup
	@echo "✓ Yeliztli setup complete"

setup-backend:  ## Install Python dependencies
	$(PIP) install -e ".[dev]"

setup-frontend:  ## Install frontend dependencies
	@if [ -f frontend/package.json ]; then \
		cd frontend && $(NPM) install; \
	else \
		echo "frontend/package.json not found — skipping frontend setup"; \
	fi

# ──────────────────────────────────────────────
# Run
# ──────────────────────────────────────────────

run: run-api  ## Start the API server (default)

dev:  ## Start backend + frontend + Huey worker concurrently (API_PORT overrides 8000)
	@echo "Starting API server (port $(API_PORT)), Vite dev server (port 5173), and Huey worker..."
	@trap 'kill 0' INT TERM; YELIZTLI_PORT=$(API_PORT) $(MAKE) run-api & VITE_API_PORT=$(API_PORT) $(MAKE) run-frontend & $(MAKE) run-huey & wait

dev-wsl:  ## Like `dev`, but binds servers to 0.0.0.0 so a Windows-host browser can reach WSL2
	@echo "Starting Yeliztli for WSL2 — backend on 0.0.0.0:$(API_PORT), Vite on 0.0.0.0:5173."
	@echo "Open http://localhost:5173 in your Windows browser; if localhost is blocked, use http://$$(hostname -I 2>/dev/null | awk '{print $$1}'):5173 (see docs/install/wsl2.md)."
	@trap 'kill 0' INT TERM; YELIZTLI_HOST=0.0.0.0 YELIZTLI_PORT=$(API_PORT) $(MAKE) run-api & VITE_API_PORT=$(API_PORT) $(MAKE) run-frontend VITE_ARGS=--host & $(MAKE) run-huey & wait

run-api:  ## Start FastAPI dev server
	YELIZTLI_DEBUG=$${YELIZTLI_DEBUG:-true} $(PYTHON) -m backend.main

run-frontend:  ## Start Vite dev server (VITE_ARGS forwards flags, e.g. --host for LAN/WSL2)
	cd frontend && $(NPM) run dev -- $(VITE_ARGS)

run-huey:  ## Start Huey consumer
	huey_consumer backend.tasks.huey_tasks.huey -w 1

build-frontend:  ## Build frontend for production
	cd frontend && $(NPM) run build

# ──────────────────────────────────────────────
# Test
# ──────────────────────────────────────────────

test: test-backend test-frontend  ## Run all tests (excluding E2E)

test-backend:  ## Run backend tests
	$(PYTHON) -m pytest tests/ -v

test-frontend:  ## Run frontend tests
	@if [ -f frontend/package.json ]; then \
		cd frontend && $(NPM) test; \
	else \
		echo "frontend not set up — skipping"; \
	fi

test-e2e:  ## Run Playwright E2E tests
	npx playwright test

benchmark:  ## Run annotation pipeline performance benchmark (600k SNPs)
	$(PYTHON) scripts/benchmark.py

# ──────────────────────────────────────────────
# Code quality
# ──────────────────────────────────────────────

lint:  ## Lint Python code with Ruff
	$(PYTHON) -m ruff check backend/ tests/

format:  ## Format Python code with Ruff
	$(PYTHON) -m ruff format backend/ tests/
	$(PYTHON) -m ruff check --fix backend/ tests/

# ──────────────────────────────────────────────
# Install / Uninstall (native services)
# ──────────────────────────────────────────────

install:  ## Install native services (launchd/systemd)
	$(PYTHON) -m backend.installer install

uninstall:  ## Uninstall native services
	$(PYTHON) -m backend.installer uninstall

service-status:  ## Show native service status
	$(PYTHON) -m backend.installer status

service-start:  ## Start native services
	$(PYTHON) -m backend.installer start

service-stop:  ## Stop native services
	$(PYTHON) -m backend.installer stop

# ──────────────────────────────────────────────
# Clean
# ──────────────────────────────────────────────

clean:  ## Remove build artifacts
	rm -rf dist/ build/ *.egg-info .pytest_cache .ruff_cache .mypy_cache
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true

help:  ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

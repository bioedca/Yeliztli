.PHONY: setup setup-backend setup-frontend test test-backend test-frontend test-e2e lint format run dev run-api run-frontend run-huey build-frontend install uninstall service-status service-start service-stop clean benchmark

# Default Python and Node
PYTHON ?= python3
PIP ?= pip
NPM ?= npm

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

dev:  ## Start backend + frontend + Huey worker concurrently
	@echo "Starting API server, Vite dev server (port 5173), and Huey worker..."
	@trap 'kill 0' INT TERM; $(MAKE) run-api & $(MAKE) run-frontend & $(MAKE) run-huey & wait

run-api:  ## Start FastAPI dev server
	YELIZTLI_DEBUG=$${YELIZTLI_DEBUG:-true} $(PYTHON) -m backend.main

run-frontend:  ## Start Vite dev server
	cd frontend && $(NPM) run dev

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

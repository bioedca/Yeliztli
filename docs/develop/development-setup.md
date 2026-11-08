# Development setup

For a hot-reloading environment (see [system requirements](../install/system-requirements.md)
for prerequisites):

```bash
git clone https://github.com/bioedca/Yeliztli.git
cd Yeliztli
pip install -e ".[dev]"
cd frontend && npm install && cd ..
make dev
```

For backend tests and strict documentation builds, install the development and
documentation extras with `python -m pip install -e ".[dev,docs]"`. This installs
Python dependencies; it does not install a Java runtime. Java remains optional
for isolated tests, including LAI calibration planning. Tests that exercise the
real JVM use `requires_java` and skip when `java` is absent from `PATH`. To run
that integration coverage, make the supported [Java prerequisite](../install/system-requirements.md)
available and confirm `java -version` succeeds before running pytest. See the
[testing guide](testing.md) for coverage and CI expectations.

`make dev` starts three processes together:

- the **backend** (FastAPI with auto-reload) on **port 8000**,
- the **frontend** (Vite dev server with hot-module reload) on **port 5173**,
- the **Huey** background worker.

Open **[http://localhost:5173](http://localhost:5173)** — the Vite dev server proxies API
requests to the backend on 8000.

## Useful make targets

| Target | What it does |
|--------|--------------|
| `make dev` | Backend + frontend + worker, with reload |
| `make run-api` | Backend only |
| `make run-frontend` | Vite dev server only |
| `make run-huey` | Background worker only |
| `make build-frontend` | Production frontend build |
| `make test` | All tests (excluding E2E) |
| `make lint` / `make format` | Ruff lint / format |
| `make install` / `make uninstall` | Native service install/uninstall |

## Code quality

- **Python** is linted and formatted with **Ruff** (pinned for deterministic `ruff format
  --check`); type-checked with **mypy**.
- **Frontend** is linted with **ESLint** and type-checked by the TypeScript compiler during
  build.

Run `make lint` and `make format` before pushing — CI runs the same checks. See
[testing](testing.md) for the test commands, and [contributing](contributing.md) for the
assertion conventions.

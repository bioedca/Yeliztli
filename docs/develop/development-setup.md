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

## Frontend resource errors

Pass the query's structured `error` to `PageError` alongside its safe fallback
`message` and `onRetry`. Ordinary HTTP 404 resource lookups show **Data not
found**, advise checking the link or selecting another sample, and omit Retry.
Network failures and other HTTP errors retain the existing retry action.
`SamplesApiError` and `IndividualsApiError` extend the shared `ApiError` so their
status survives the same path.

A 404 with the documented `patient_response_withheld` code is a withheld-results
state, not evidence that data is absent. Preserve that distinction in detail
panels; use `resourceErrorMessage` for their other errors. Never render server
response bodies or infer an error category from response text. An existing
sample with successful null merge provenance still uses the not-merged empty
state, and stale samples retain their re-annotation notice.

`AuthGuard` handles service availability probes separately: its backend recovery
action remains available even when a status endpoint returns 404. Static invalid
URL errors have no resource lookup and need no retry action.

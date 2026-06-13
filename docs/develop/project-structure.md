# Project structure

A map of the repository's top-level layout.

```text
Yeliztli/
├── backend/                 # FastAPI application
│   ├── analysis/            # Analysis modules (one per domain) + run_all
│   ├── annotation/          # Variant annotation engine + VEP bundle access
│   ├── api/routes/          # API endpoint modules
│   ├── data/panels/         # Curated panel JSON (per module)
│   ├── db/                  # SQLAlchemy Core tables, connections, schema
│   ├── ingestion/           # Vendor parsers, dispatcher, liftover
│   ├── reports/             # PDF report templates
│   ├── services/            # Cross-cutting services (e.g. sample merge)
│   ├── tasks/               # Huey task definitions
│   ├── config.py            # Pydantic Settings configuration
│   ├── installer.py         # `yeliztli-setup` CLI
│   └── main.py              # FastAPI app entry point
├── frontend/                # React 19 + TypeScript SPA (Vite)
│   └── src/
│       ├── pages/           # Route pages (incl. module views)
│       ├── components/      # Reusable UI components
│       └── hooks/           # React Query hooks
├── bundles/                 # Bundle manifest + small bundled data
├── alembic/                 # Per-sample DB migrations
├── tests/                   # Backend (pytest), with E2E specs (Playwright)
├── scripts/                 # Build & utility scripts (bundles, benchmarks)
├── systemd/  launchd/       # Service unit templates (Linux/WSL2, macOS)
├── docs/                    # This documentation site (MkDocs)
├── Dockerfile               # Container image
├── docker-compose.yml       # Two-service deployment (api + huey)
├── Makefile                 # Dev shortcuts
└── pyproject.toml           # Python project config
```

See [architecture](architecture.md) for how these fit together.

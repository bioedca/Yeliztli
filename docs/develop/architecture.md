# Architecture

Yeliztli is a local web application: a Python backend serving a JSON API and a React
single-page frontend, with a background worker for the heavy lifting. Everything runs on
`localhost`.

## Stack at a glance

**Backend** (`backend/`)

- **FastAPI** + **Starlette**, served by **Uvicorn** (`backend.main:app`).
- **SQLAlchemy 2.0 Core** — Table/Core constructs, not the ORM.
- **Huey** background task queue for annotation and analysis
  (`backend.tasks.huey_tasks.huey`).
- **Pydantic Settings** for configuration (TOML + env), **structlog** for logging,
  **bcrypt** for optional auth, **httpx** for outbound fetches.
- Scientific stack: **NumPy**, **SciPy**, **XGBoost**, **Plotly**; bioinformatics:
  **pysam**, **pyliftover**, **Biopython**.

**Frontend** (`frontend/`)

- **React 19** + **TypeScript**, built with **Vite**.
- **TanStack Query** (data fetching) and **TanStack Table** (the virtualised variant table).
- **TailwindCSS**, plus **IGV.js** (genome browser), **Plotly** (PCA/charts),
  **Nightingale** (protein tracks), **Monaco** (SQL console), and **react-querybuilder**.

## Data model — two kinds of SQLite database

Yeliztli uses **SQLite** in a dual-database layout:

- **`reference.db`** — shared reference data and app state (panels, annotation tables, jobs,
  individuals, settings). Its schema is created/kept-current at startup
  (`create_all` + an additive `ensure_reference_schema_current` backfill).
- **Per-sample databases** (`samples/<id>.db`) — one isolated database per uploaded sample,
  holding that sample's raw genotypes, annotations, and findings. These are **Alembic**-migrated.

Large reference datasets (gnomAD, the VEP bundle, dbNSFP, …) are separate SQLite files
downloaded into the data directory — see [reference data](../install/reference-data.md).

## The pipeline: upload → annotate → analyse

1. **Ingest.** A raw file is uploaded (`POST /api/ingest/upload`). A **dispatcher** detects
   the vendor (23andMe / AncestryDNA), the right parser reads it, and a **liftover** step
   normalises older builds to GRCh37. Raw genotypes land in the sample's database.
2. **Annotate.** A **Huey** job runs the annotation engine, layering on VEP consequences,
   ClinVar significance, gnomAD frequencies, dbNSFP predictions, ENCODE regulatory context,
   GWAS associations, and GTEx eQTLs.
3. **Analyse.** `run_all_analyses()` runs every [module](../modules/index.md) over the
   annotated sample, writing **findings** (with provenance and evidence levels).
4. **Serve.** The frontend polls job status, then reads variants and findings through the API.

Background jobs are tracked so an interrupted annotation is recovered on restart.

## Entry points

| Process | Command |
|---------|---------|
| API server | `uvicorn backend.main:app` |
| Background worker | `huey_consumer backend.tasks.huey_tasks.huey` |
| Frontend (dev) | `npm run dev` (in `frontend/`) |

`make dev` runs all three together — see [development setup](development-setup.md).

## Adding an analysis module (high level)

A module typically consists of: a curated **panel** (`backend/data/panels/<name>_panel.json`),
the **analysis** code (`backend/analysis/<name>.py`), an **API route**
(`backend/api/routes/`), wiring into `run_all_analyses()`, an optional **frontend view**, and
**tests** (including a `hom_ref` non-carrier control — see [contributing](contributing.md)).
The existing modules are the best templates. Some drift-guard tests assert the set of
reference tables and route modules, so adding either may require updating those locks; the
test suite will tell you.

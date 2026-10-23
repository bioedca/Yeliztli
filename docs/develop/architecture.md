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

Existing sample databases also receive versioned content repairs on open. Schema v32
removes percentile advice from exact legacy PRS ancestry caveats when the stored
result has insufficient coverage, is uncalibrated, or explicitly has no percentile.
It preserves reported-percentile results, custom wording, scores, and provenance;
no reanalysis is required for this text repair.

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
   annotated sample, writing **findings** (with provenance and evidence levels). Ancestry
   runs first because several modules (PRS caveats and calibration, APOL1, gout,
   nutrigenomics) read its stored result while scoring; a live-path test fails if any
   module reads it before it exists.
4. **Serve.** The frontend polls job status, then reads variants and findings through the API.

Background jobs are tracked so an interrupted annotation is recovered on restart.

When ClinVar supplies an exact allele identity for a marker whose multi-ALT VEP
annotation was withheld, annotation now resolves its primary VEP transcript using
that full `(chrom, pos, ref, alt)` identity. Gene-keyed views can then use the stored
gene and consequence. Unresolved identities remain withheld, and existing VEP
annotations retain their original selection. Existing samples are not rewritten
on open for this correction: re-run annotation with `POST /api/annotation/{sample_id}`
to apply it before relying on their stored gene columns.

ClinVar coordinate fallback filters reference records by the shared array-probe
class before ranking candidates: an I/D call cannot inherit a co-located SNV's
record, and a base call cannot inherit an indel's record. Own-rsID indels may use
a ClinVar anchor exactly one base upstream on the same chromosome; this bounded
compatibility rule does not apply to merged rsIDs, SNVs, or other offsets. The
raw probe position stays unchanged, while downstream VEP joins use ClinVar's
source-native `(chrom, pos, ref, alt)` identity and reselect any VEP record that
was found at the raw position. Frequencies are re-queried at that same exact
native identity; a co-located allele with the same strings at another position
cannot supply its frequency, homozygote count, or rarity flags. This correction also requires re-annotation of
existing samples; open the sample's Annotation Pipeline panel and run annotation
again (see [updating](../install/updating.md)). Technical evidence and limits are
recorded in the [issue #2391 evidence packet](https://github.com/bioedca/Yeliztli/blob/main/data/science-evidence/2026-10-23-issue-2391-clinvar-probe-identity/README.md).

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

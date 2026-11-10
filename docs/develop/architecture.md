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

PRS calibration reads typed variants' stored frequencies from the sample database.
For scored imputed-only variants it uses the separate `gnomad_af.db`, matched by
exact `(chrom, pos, ref, alt)`. PRS callers pass this store as `gnomad_engine`;
`reference_engine` remains the central `reference.db` connection for sample
metadata, gene constraints, GWAS associations and reference-version tracking.
An absent gnomAD store is not created by scoring. When a scored imputed contribution
has no matching row or no usable frequency, ancestry-continuous calibration and its
percentile are withheld. Scored typed variants with incomplete allele annotations
also withhold calibration when the frequency store is absent. Typed-only scoring
with complete allele annotations retains its existing calibration rules.
The PRS reader opens SQLite in read-only mode and rechecks existence under the
publication lease, so cleanup between engine selection and lookup cannot recreate
an empty database. When a lookup is needed, an installed store that cannot be read
fails the run instead of being treated as absent. Metabolic, FH and eBMD score
freshness includes the standalone file and its non-empty WAL; restoration,
replacement, installation or removal therefore makes prior scores stale even when
the central reference-version snapshot is unchanged.

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

Database build guards accept a single ASCII claim key: a lowercase letter followed
by at most 63 lowercase letters, digits or underscores. Every guard validates the
key before lock registration or filesystem access, including on the non-POSIX
fallback. Internal keys such as `mondo_hpo_source_bundles` use the same contract;
a syntactically valid key does not by itself authorize a database operation.

Guards consume canonical plain-string keys, so caller-defined string formatting
cannot redirect a validated name. Filenames are normalized within the relative
`.claims` namespace and checked for containment before the resulting component is
passed to descriptor-relative filesystem operations.

The database-update worker independently checks queued keys before registry access
and claim acquisition. Only registered pipeline builders, supported manifest bundles
and the direct-download runners can proceed. Unsupported inputs fail the job without
entering a build claim, even if a producer or a persisted queue supplied the input.

Cross-process claims use descriptor-relative, no-follow opens under a verified
`.claims` directory. Opened directories and regular marker files must match their
named device/inode identities and belong to the effective user; group/other write
permission is rejected. Probes open existing markers only and never create or
repair storage. Reentrancy is scoped to the selected physical storage root as well
as the key.

A probe retries once if an acquisition first publishes the shared `.anchors`
directory during its observation. The retry reopens existing storage with the same
ownership, no-follow and identity checks; it does not create or repair files.

Each marker has a second internal hard link at `.claims/.anchors/<key>.claim`.
Both names must refer to the same inode, with no external hard links. Removing
one name during a held claim therefore leaves the original lock reachable; an
acquirer restores a missing legacy marker only after winning that same lock.
Existing regular markers are adopted without replacing their inode. Normal
release and process death leave both names in place for subsequent acquirers.

Verification and alias publication take a short exclusive advisory lock on the
opened `.anchors` directory, which must be owner-private (mode `0700`) before
locking; existing permissions are checked, never repaired. This prevents a
concurrent observer from rejecting the interval where a filesystem has increased
a marker's link count but has not yet published its new name. The namespace lock
is released before yielding a
build claim; the per-database build lock remains non-blocking and different
databases can still build concurrently. Probes may briefly wait for publication,
but never create or repair storage. Exact inode, ownership, permission and
internal-link-count checks still apply after publication completes.

The storage contract requires a local POSIX filesystem with descriptor-relative
no-follow operations, hard links and working advisory `flock`. The selected root
and claims directories must be user-owned and not writable by group/other users.
Their physical ancestors must be owned by the user or root and not shared-writable,
except for sticky shared ancestors such as `/tmp`. The operator-selected root may
be a symlink to such a trusted physical location. Missing required filesystem
operations or unsafe/changed storage fail closed. Native Windows retains the documented
in-process-only fallback. Network-filesystem locking is not a supported guarantee.
Missing root components are created individually with mode `0700`, and the completed
ancestor chain is checked before a claim is granted. Existing permissions are not repaired.

All API/worker processes must stop before upgrading or repairing claim storage.
An administrator or process running as the same user can still remove both names
or replace the whole namespace; advisory locking cannot defend against that actor.
Never clean up claim markers while a builder may still be running.

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

The worker can also start on an empty data directory before the API. Once it has
a complete queue snapshot, its startup hook creates missing reference tables
before recovering orphaned jobs. It uses the same SQLite-serialized bootstrap as
the API, so concurrent starts do not race table creation. This initialization
does not depend on a persisted log write. The API still owns its broader schema
backfills and content repairs; the worker's logging path retains its separate
log-policy upgrade. Bootstrap or recovery errors propagate to Huey's startup-hook
error logging, and a failed bootstrap prevents the recovery writes.

Concurrent first connections can also encounter `SQLITE_BUSY` while enabling
WAL, before reaching the schema lock. Connection setup retries that pragma within
the configured busy-timeout budget, then restores SQLite's normal query timeout.
It does not retry other error codes. SQLite documents why a
[busy handler can be bypassed](https://www.sqlite.org/c3ref/busy_handler.html)
during lock promotion.

## Adding an analysis module (high level)

A module typically consists of: a curated **panel** (`backend/data/panels/<name>_panel.json`),
the **analysis** code (`backend/analysis/<name>.py`), an **API route**
(`backend/api/routes/`), wiring into `run_all_analyses()`, an optional **frontend view**, and
**tests** (including a `hom_ref` non-carrier control — see [contributing](contributing.md)).
The existing modules are the best templates. Some drift-guard tests assert the set of
reference tables and route modules, so adding either may require updating those locks; the
test suite will tell you.

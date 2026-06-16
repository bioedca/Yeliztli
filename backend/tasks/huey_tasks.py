"""Huey task queue configuration and tasks.

Uses SqliteHuey for persistent task state with a single worker.
In test/dev mode, immediate=True runs tasks synchronously.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path

import structlog
from huey import SqliteHuey, crontab
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from backend.config import config_toml_path, get_settings
from backend.db.build_guard import build_lock

logger = structlog.get_logger(__name__)

_settings = get_settings()
_settings.data_dir.mkdir(parents=True, exist_ok=True)
_huey_db = str(_settings.data_dir / "huey.db")

# Allow override for testing (immediate mode runs tasks inline).
_immediate = (os.environ.get("YELIZTLI_HUEY_IMMEDIATE") or "").lower() in (
    "1",
    "true",
    "yes",
)

huey = SqliteHuey(
    "yeliztli",
    filename=_huey_db,
    immediate=_immediate,
)


# ── Job record helpers ──────────────────────────────────────────────────


def create_annotation_job(sample_id: int) -> str:
    """Create a job record for an annotation run. Returns the job_id."""
    import sqlalchemy as sa

    from backend.db.connection import get_registry
    from backend.db.tables import jobs

    job_id = str(uuid.uuid4())
    now = datetime.now(UTC)
    registry = get_registry()

    with registry.reference_engine.begin() as conn:
        # Check for an already-running annotation job on this sample
        existing = conn.execute(
            sa.select(jobs.c.job_id).where(
                jobs.c.sample_id == sample_id,
                jobs.c.job_type == "annotation",
                jobs.c.status.in_(["pending", "running"]),
            )
        ).fetchone()
        if existing is not None:
            raise ValueError(
                f"Annotation already in progress for sample {sample_id} (job {existing.job_id})"
            )

        conn.execute(
            jobs.insert().values(
                job_id=job_id,
                sample_id=sample_id,
                job_type="annotation",
                status="pending",
                progress_pct=0.0,
                message="Queued for annotation",
                created_at=now,
                updated_at=now,
            )
        )

    return job_id


def recover_orphaned_jobs(engine) -> int:
    """Mark any jobs left in 'running'/'pending' state as failed.

    Called at backend startup. A worker that gets killed mid-task leaves its
    jobs row stuck — there is no in-process recovery, so the UI keeps showing
    a stale progress bar forever. This sweeps those rows on boot.
    """
    from backend.db.tables import jobs

    with engine.begin() as conn:
        result = conn.execute(
            jobs.update()
            .where(jobs.c.status.in_(("running", "pending")))
            .values(
                status="failed",
                error="Worker terminated (backend restarted while task was in progress)",
                message="Interrupted by backend restart",
                updated_at=datetime.now(UTC),
            )
        )
        count = result.rowcount or 0
    if count:
        logger.info("orphaned_jobs_recovered", count=count)
    return count


def _update_job(
    job_id: str,
    *,
    status: str,
    progress_pct: float = 0.0,
    message: str = "",
    error: str | None = None,
) -> None:
    """Update a job record in the jobs table."""

    from backend.db.connection import get_registry
    from backend.db.tables import jobs

    registry = get_registry()
    with registry.reference_engine.begin() as conn:
        result = conn.execute(
            jobs.update()
            .where(jobs.c.job_id == job_id)
            .values(
                status=status,
                progress_pct=progress_pct,
                message=message,
                error=error,
                updated_at=datetime.now(UTC),
            )
        )
        if result.rowcount == 0:
            logger.warning("_update_job: no job found", extra={"job_id": job_id})


def _is_job_cancelled(job_id: str) -> bool:
    """Check if a job has been cancelled by the user."""
    import sqlalchemy as sa

    from backend.db.connection import get_registry
    from backend.db.tables import jobs

    registry = get_registry()
    with registry.reference_engine.connect() as conn:
        row = conn.execute(sa.select(jobs.c.status).where(jobs.c.job_id == job_id)).fetchone()

    return row is not None and row.status == "cancelled"


class AnnotationCancelledError(Exception):
    """Raised when an annotation job is cancelled by the user."""


def _upsert_annotation_state(conn, key: str, value: str) -> None:
    """Upsert one row into the per-sample ``annotation_state`` kv table.

    The caller owns the transaction so multiple keys can be written atomically
    (Plan §7.3 — both reserved keys land in one ``engine.begin()`` block).
    """
    from backend.db.tables import annotation_state

    stmt = sqlite_insert(annotation_state).values(key=key, value=value)
    stmt = stmt.on_conflict_do_update(
        index_elements=[annotation_state.c.key],
        set_={
            "value": stmt.excluded.value,
            "updated_at": datetime.now(UTC),
        },
    )
    conn.execute(stmt)


def _get_sample_db_path(sample_id: int) -> str:
    """Look up the db_path for a sample from the samples table."""
    import sqlalchemy as sa

    from backend.db.connection import get_registry
    from backend.db.tables import samples

    registry = get_registry()
    with registry.reference_engine.connect() as conn:
        row = conn.execute(
            sa.select(samples.c.db_path).where(samples.c.id == sample_id)
        ).fetchone()

    if row is None:
        raise ValueError(f"Sample {sample_id} not found")

    return row.db_path


# ── Annotation task ─────────────────────────────────────────────────────


@huey.task()
def run_annotation_task(sample_id: int, job_id: str) -> None:
    """Huey background task: run the full annotation engine on a sample.

    Updates the jobs table with progress so the SSE endpoint can
    stream batch-level updates to the frontend.
    """
    from backend.annotation.engine import run_annotation
    from backend.db.connection import get_registry

    registry = get_registry()

    try:
        # Look up sample DB path and get engine
        db_path = _get_sample_db_path(sample_id)
        sample_db_full = registry.settings.data_dir / db_path
        sample_engine = registry.get_sample_engine(sample_db_full)

        _update_job(job_id, status="running", message="Annotating…")

        def progress_callback(variants_done: int, total: int) -> None:
            if _is_job_cancelled(job_id):
                raise AnnotationCancelledError(f"Job {job_id} cancelled by user")
            pct = (variants_done / total * 100) if total > 0 else 0.0
            _update_job(
                job_id,
                status="running",
                progress_pct=round(pct, 1),
                message=f"Annotated {variants_done:,}/{total:,} variants",
            )

        result = run_annotation(
            sample_engine,
            registry,
            progress_callback=progress_callback,
        )

        if result.errors:
            error_summary = "; ".join(result.errors[:5])
            logger.warning(
                "annotation_task_warnings",
                extra={"job_id": job_id, "errors": result.errors},
            )
        else:
            error_summary = None

        # SW-A4b: snapshot the prior findings BEFORE run_all_analyses clears them
        # (each module DELETEs then re-INSERTs its rows), so the finding-level
        # change diff can be computed once the fresh findings are stamped.
        # Best-effort and in its own try so a snapshot failure never blocks the
        # analysis run or holds up the staleness gate.
        prior_findings = None
        try:
            from backend.analysis.finding_diff import snapshot_findings

            prior_findings = snapshot_findings(sample_engine)
        except Exception:
            logger.exception(
                "finding_diff_snapshot_failed",
                extra={"job_id": job_id, "sample_id": sample_id},
            )

        # Run all analysis modules to populate findings
        _update_job(
            job_id,
            status="running",
            progress_pct=95.0,
            message="Analyzing…",
        )
        analysis_ok = False
        try:
            from backend.analysis.run_all import run_all_analyses

            def analysis_progress(module_name: str, index: int, total: int) -> None:
                pct = 95.0 + (index / total) * 4.0  # 95% → 99%
                _update_job(
                    job_id,
                    status="running",
                    progress_pct=round(pct, 1),
                    message=f"Analyzing: {module_name} ({index + 1}/{total})",
                )

            analysis_results = run_all_analyses(
                sample_engine,
                registry,
                sample_id=sample_id,
                progress_callback=analysis_progress,
            )
            errors = [k for k, v in analysis_results.items() if v == "error"]
            if errors:
                logger.warning(
                    "some_analysis_modules_failed",
                    extra={"job_id": job_id, "failed_modules": errors},
                )

            # Plan §7.3: success path — upsert both reserved keys atomically so
            # the staleness gate can lift only when annotated_variants AND
            # findings are fresh. A raise from run_all_analyses bypasses this
            # block via the except clause below, leaving annotation_state
            # untouched so the gate stays up.
            bundle_version = result.coverage_stats.get("bundle_version") or "v1.0.0"
            with sample_engine.begin() as conn:
                _upsert_annotation_state(conn, "vep_bundle_version", bundle_version)
                _upsert_annotation_state(
                    conn,
                    "annotation_bundle_coverage_json",
                    json.dumps(result.coverage_stats),
                )
            logger.info(
                "annotation_state_upserted",
                extra={
                    "job_id": job_id,
                    "sample_id": sample_id,
                    "vep_bundle_version": bundle_version,
                },
            )
            analysis_ok = True
        except Exception:
            logger.exception(
                "analysis_modules_failed",
                extra={"job_id": job_id, "sample_id": sample_id},
            )
            # Non-fatal: annotation succeeded, analysis is best-effort.
            # annotation_state is NOT upserted — the gate stays up so the user
            # can retry via the re-annotation banner.

        # Stamp per-finding provenance (SW-A4 #8): pin the source-release snapshot
        # used to produce each finding. Best-effort and audit-only — a failure
        # never affects findings or the staleness gate.
        try:
            from backend.analysis.provenance import stamp_findings_provenance

            stamped = stamp_findings_provenance(sample_engine, registry.reference_engine)
            logger.info(
                "findings_provenance_stamped",
                extra={"job_id": job_id, "sample_id": sample_id, "stamped": stamped},
            )
        except Exception:
            logger.exception(
                "findings_provenance_failed",
                extra={"job_id": job_id, "sample_id": sample_id},
            )

        # SW-A4b: compute + store the finding-level change diff (added / removed /
        # changed since the prior snapshot), attributed to the source-release
        # delta from provenance. Disclosure only and best-effort — never alters
        # findings or the staleness gate. Skipped when analysis did not fully
        # succeed: the findings set is then partial (the gate stays up), so a diff
        # would surface spurious "removed" findings.
        if analysis_ok:
            try:
                from backend.analysis.finding_diff import compute_and_store_finding_diff

                compute_and_store_finding_diff(
                    sample_engine, registry.reference_engine, prior_findings
                )
            except Exception:
                logger.exception(
                    "finding_diff_compute_failed",
                    extra={"job_id": job_id, "sample_id": sample_id},
                )

        # Generate SVGs for all findings (post-analysis step)
        _update_job(
            job_id,
            status="running",
            progress_pct=99.0,
            message="Generating finding SVGs",
        )
        try:
            from backend.analysis.svg_renderer import generate_svgs_for_sample

            sample_dir = Path(sample_db_full).parent
            svg_count = generate_svgs_for_sample(sample_engine, sample_dir)
            logger.info(
                "svg_generation_complete",
                extra={
                    "job_id": job_id,
                    "sample_id": sample_id,
                    "svgs_generated": svg_count,
                },
            )
        except Exception:
            logger.exception(
                "svg_generation_failed",
                extra={"job_id": job_id, "sample_id": sample_id},
            )
            # Non-fatal: annotation succeeded, SVG generation is best-effort

        # An unreadable source (locked/corrupt) means the annotation is
        # incomplete — report ``partial`` rather than silently claiming success
        # (F29). A genuinely-absent source is not a failure and stays ``complete``.
        if result.source_failures:
            failed = ", ".join(sorted(result.source_failures))
            final_status = "partial"
            status_note = f" — partial: source(s) unavailable ({failed})"
        else:
            final_status = "complete"
            status_note = ""

        _update_job(
            job_id,
            status=final_status,
            progress_pct=100.0,
            message=(
                f"Annotated {result.rows_written:,} variants "
                f"(VEP: {result.vep_matched}, ClinVar: {result.clinvar_matched}, "
                f"gnomAD: {result.gnomad_matched}, dbNSFP: {result.dbnsfp_matched}, "
                f"AlphaMissense: {result.alphamissense_matched}, "
                f"GenePhenotype: {result.gene_phenotype_matched}){status_note}"
            ),
            error=error_summary,
        )

        logger.info(
            "annotation_task_complete",
            extra={
                "job_id": job_id,
                "sample_id": sample_id,
                "rows_written": result.rows_written,
                "total_variants": result.total_variants,
            },
        )

    except AnnotationCancelledError:
        logger.info(
            "annotation_task_cancelled",
            extra={"job_id": job_id, "sample_id": sample_id},
        )
        # Status already set to "cancelled" by the cancel endpoint

    except Exception as exc:
        logger.exception(
            "annotation_task_failed",
            extra={"job_id": job_id, "sample_id": sample_id},
        )
        _update_job(
            job_id,
            status="failed",
            message="Annotation failed",
            error=str(exc),
        )


# ── LAI analysis task (AMv2 Step 4) ───────────────────────────────────


def create_lai_job(sample_id: int) -> str:
    """Create a job record for an LAI analysis run. Returns the job_id."""
    import sqlalchemy as sa

    from backend.db.connection import get_registry
    from backend.db.tables import jobs

    job_id = str(uuid.uuid4())
    now = datetime.now(UTC)
    registry = get_registry()

    with registry.reference_engine.begin() as conn:
        # Check for already-running LAI job on this sample
        existing = conn.execute(
            sa.select(jobs.c.job_id).where(
                jobs.c.sample_id == sample_id,
                jobs.c.job_type == "lai_analysis",
                jobs.c.status.in_(["pending", "running"]),
            )
        ).fetchone()
        if existing is not None:
            raise ValueError(
                f"LAI analysis already in progress for sample {sample_id} (job {existing.job_id})"
            )

        conn.execute(
            jobs.insert().values(
                job_id=job_id,
                sample_id=sample_id,
                job_type="lai_analysis",
                status="pending",
                progress_pct=0.0,
                message="Queued for local ancestry inference",
                created_at=now,
                updated_at=now,
            )
        )

    return job_id


@huey.task()
def run_lai_task(sample_id: int, job_id: str) -> None:
    """Huey background task: run LAI analysis on a sample.

    Updates the jobs table with progress so the SSE endpoint can
    stream per-chromosome updates to the frontend.
    """
    from backend.analysis.lai import run_lai_analysis
    from backend.db.connection import get_registry

    registry = get_registry()

    try:
        db_path = _get_sample_db_path(sample_id)
        sample_db_full = registry.settings.data_dir / db_path
        sample_engine = registry.get_sample_engine(sample_db_full)

        _update_job(job_id, status="running", message="Starting LAI analysis")

        def progress_callback(msg: str, fraction: float) -> None:
            if _is_job_cancelled(job_id):
                raise AnnotationCancelledError(f"Job {job_id} cancelled by user")
            pct = round(fraction * 100, 1)
            _update_job(
                job_id,
                status="running",
                progress_pct=pct,
                message=msg,
            )

        result = run_lai_analysis(
            sample_id=sample_id,
            sample_engine=sample_engine,
            progress_callback=progress_callback,
        )

        top_pop = ""
        if result.global_ancestry:
            top_pop = max(
                result.global_ancestry,
                key=lambda p: result.global_ancestry[p]["fraction"],
            )

        _update_job(
            job_id,
            status="complete",
            progress_pct=100.0,
            message=(
                f"LAI complete: {result.metadata.get('chromosomes_analyzed', 0)} "
                f"chromosomes analyzed, top ancestry: {top_pop}"
            ),
        )

        logger.info(
            "lai_task_complete",
            job_id=job_id,
            sample_id=sample_id,
            top_population=top_pop,
        )

    except AnnotationCancelledError:
        logger.info("lai_task_cancelled", job_id=job_id, sample_id=sample_id)

    except Exception as exc:
        logger.exception("lai_task_failed", job_id=job_id, sample_id=sample_id)
        _update_job(
            job_id,
            status="failed",
            message="LAI analysis failed",
            error=str(exc),
        )


# ── UniProt pre-fetch task (P4-12c) ───────────────────────────────────


@huey.task()
def prefetch_uniprot_priority_genes(job_id: str) -> None:
    """Pre-fetch UniProt protein domains for cancer/cardio panel genes.

    Called at setup completion to populate the UniProt cache with
    high-priority gene panel data. Runs with rate limiting to
    respect UniProt API limits.
    """
    from backend.db.connection import get_registry
    from backend.utils.uniprot import PRIORITY_GENES, UniProtCacheFetcher

    try:
        _update_job(job_id, status="running", message="Pre-fetching UniProt domains")

        registry = get_registry()
        fetcher = UniProtCacheFetcher(registry.reference_engine)

        def progress_callback(done: int, total: int) -> None:
            pct = (done / total * 100) if total > 0 else 0.0
            _update_job(
                job_id,
                status="running",
                progress_pct=round(pct, 1),
                message=f"Pre-fetching UniProt: {done}/{total} genes",
            )

        result_data = fetcher.prefetch_genes(
            PRIORITY_GENES,
            skip_fresh=True,
            delay_seconds=0.5,
            progress_callback=progress_callback,
        )

        _update_job(
            job_id,
            status="complete",
            progress_pct=100.0,
            message=(
                f"UniProt pre-fetch complete: {result_data.fetched} fetched, "
                f"{result_data.cached_already} already cached, "
                f"{result_data.failed} failed "
                f"(of {result_data.total_genes} genes)"
            ),
            error="; ".join(result_data.errors[:5]) if result_data.errors else None,
        )

        logger.info(
            "uniprot_prefetch_complete",
            extra={
                "job_id": job_id,
                "fetched": result_data.fetched,
                "cached": result_data.cached_already,
                "failed": result_data.failed,
            },
        )

    except Exception as exc:
        logger.exception(
            "uniprot_prefetch_failed",
            extra={"job_id": job_id},
        )
        _update_job(
            job_id,
            status="failed",
            message="UniProt pre-fetch failed",
            error=str(exc),
        )


def create_prefetch_job() -> str:
    """Create a job record for a UniProt pre-fetch task. Returns the job_id."""

    from backend.db.connection import get_registry
    from backend.db.tables import jobs

    job_id = str(uuid.uuid4())
    now = datetime.now(UTC)
    registry = get_registry()

    with registry.reference_engine.begin() as conn:
        conn.execute(
            jobs.insert().values(
                job_id=job_id,
                sample_id=None,
                job_type="uniprot_prefetch",
                status="pending",
                progress_pct=0.0,
                message="Queued for UniProt pre-fetch",
                created_at=now,
                updated_at=now,
            )
        )

    return job_id


# ── Update manager tasks (P4-16) ──────────────────────────────────────


def create_update_check_job() -> str:
    """Create a job record for an update check task. Returns the job_id."""

    from backend.db.connection import get_registry
    from backend.db.tables import jobs

    job_id = str(uuid.uuid4())
    now = datetime.now(UTC)
    registry = get_registry()

    with registry.reference_engine.begin() as conn:
        conn.execute(
            jobs.insert().values(
                job_id=job_id,
                sample_id=None,
                job_type="update_check",
                status="pending",
                progress_pct=0.0,
                message="Queued for database update check",
                created_at=now,
                updated_at=now,
            )
        )

    return job_id


@huey.task()
def run_update_check_task(job_id: str) -> None:
    """Huey background task: check for database updates and apply auto-updates.

    This is the on-demand / startup update check task.
    """
    from backend.db.connection import get_registry
    from backend.db.update_manager import run_scheduled_update_check

    try:
        _update_job(job_id, status="running", message="Checking for database updates")

        registry = get_registry()
        result = run_scheduled_update_check(registry)

        msg_parts = []
        if result.available:
            msg_parts.append(f"{len(result.available)} update(s) available")
        if result.up_to_date:
            msg_parts.append(f"{len(result.up_to_date)} up to date")
        if result.errors:
            msg_parts.append(f"{len(result.errors)} error(s)")

        _update_job(
            job_id,
            status="complete",
            progress_pct=100.0,
            message="; ".join(msg_parts) or "Update check complete",
            error="; ".join(result.errors[:5]) if result.errors else None,
        )

        logger.info(
            "update_check_task_complete",
            extra={
                "job_id": job_id,
                "available": len(result.available),
                "errors": len(result.errors),
            },
        )

    except Exception as exc:
        logger.exception(
            "update_check_task_failed",
            extra={"job_id": job_id},
        )
        _update_job(
            job_id,
            status="failed",
            message="Update check failed",
            error=str(exc),
        )


@huey.task()
def run_database_update_task(job_id: str, db_name: str) -> None:
    """Huey background task: run a specific database update.

    Thin wrapper that takes the cross-process build claim — so a setup-wizard
    build in the API process cannot race this update of the *same* SQLite file
    (the in-process ``build_lock`` cannot span processes) — then delegates to
    :func:`_execute_database_update`. If another process already holds the claim
    the update is skipped with a clear, retryable job error rather than racing.
    """
    from backend.db.build_guard import build_claim
    from backend.db.connection import get_registry

    settings = get_registry().settings
    with build_claim(db_name, settings.data_dir) as acquired:
        if not acquired:
            _update_job(
                job_id,
                status="failed",
                message=f"{db_name}: another process is updating it",
                error=(
                    "Another process is currently updating this database; "
                    "it will be available shortly."
                ),
            )
            logger.info(
                "database_update_skipped_claimed",
                extra={"job_id": job_id, "db_name": db_name},
            )
            return
        _execute_database_update(job_id, db_name)


def _execute_database_update(job_id: str, db_name: str) -> None:
    """Run a specific database update (the cross-process claim is already held).

    Uses the same build function that the setup wizard uses
    (via database_registry.get_build_fn) so all databases are
    updated through a single, tested code path.
    """
    from backend.db.connection import get_registry
    from backend.db.database_registry import DATABASES, get_build_fn

    try:
        _update_job(
            job_id,
            status="running",
            message=f"Updating {db_name}",
        )

        registry = get_registry()

        # VEP bundle uses a dedicated download-from-GitHub path
        if db_name == "vep_bundle":
            from backend.db.update_manager import run_vep_bundle_update

            _update_job(
                job_id,
                status="running",
                progress_pct=10.0,
                message="Downloading VEP bundle from GitHub",
            )
            result = run_vep_bundle_update(registry.settings)
            if result is None:
                raise RuntimeError("VEP bundle download failed or file is invalid")
            _update_job(
                job_id,
                status="complete",
                progress_pct=100.0,
                message="VEP Bundle update complete",
            )
            logger.info(
                "database_update_task_complete",
                extra={"job_id": job_id, "db_name": db_name},
            )
            return

        # LAI / ancestry-PCA / gnomAD bundles flow through their manifest-driven
        # runners (same path as the scheduler's _dispatch_auto_update), not a
        # build_fn. gnomAD is no longer in _BUILD_FN_REGISTRY, so it MUST be
        # caught here before the build_fn branch below (which would raise).
        if db_name in ("lai_bundle", "ancestry_pca", "gnomad"):
            from backend.db.manifest import get_bundle_info
            from backend.db.update_manager import (
                run_ancestry_pca_bundle_update,
                run_gnomad_bundle_update,
                run_lai_bundle_update,
            )

            _update_job(
                job_id,
                status="running",
                progress_pct=10.0,
                message=f"Downloading {db_name} bundle",
            )
            runner = {
                "lai_bundle": run_lai_bundle_update,
                "ancestry_pca": run_ancestry_pca_bundle_update,
                "gnomad": run_gnomad_bundle_update,
            }[db_name]
            result = runner(registry.settings)
            if result is None:
                # None means either a genuine failure (manifest URL present) or a
                # no-op for an out-of-band bundle with no URL (ancestry_pca). Only
                # the former is an error.
                entry = get_bundle_info(db_name)
                if entry is None or not entry.url:
                    _update_job(
                        job_id,
                        status="complete",
                        progress_pct=100.0,
                        message=f"{db_name}: no remote update available",
                    )
                    logger.info(
                        "database_update_task_noop",
                        extra={"job_id": job_id, "db_name": db_name},
                    )
                    return
                raise RuntimeError(f"{db_name} bundle update failed")
            _update_job(
                job_id,
                status="complete",
                progress_pct=100.0,
                message=f"{db_name} update complete",
            )
            logger.info(
                "database_update_task_complete",
                extra={"job_id": job_id, "db_name": db_name},
            )
            return

        build_fn = get_build_fn(db_name)
        if build_fn is None:
            raise ValueError(f"No build function registered for '{db_name}'")

        db_info = DATABASES.get(db_name)
        engine = registry.reference_engine
        settings = registry.settings

        # Build functions for reference-target DBs take the reference engine;
        # standalone DBs write to their own file and take a fresh engine.
        # Serialize per-DB so an auto-update can't race a setup-wizard build of
        # the same file (the "database is locked" failure mode).
        with build_lock(db_name):
            if db_info and db_info.target_db == "reference":
                build_fn(engine, settings.downloads_dir)
            else:
                import sqlalchemy as sa
                from sqlalchemy import event

                dest = (
                    db_info.dest_path(settings) if db_info else settings.data_dir / f"{db_name}.db"
                )
                dest.parent.mkdir(parents=True, exist_ok=True)
                standalone_engine = sa.create_engine(f"sqlite:///{dest}")

                @event.listens_for(standalone_engine, "connect")
                def _set_pragmas(dbapi_conn, _):
                    cursor = dbapi_conn.cursor()
                    cursor.execute("PRAGMA busy_timeout=30000")
                    cursor.execute("PRAGMA journal_mode=WAL")
                    cursor.close()

                try:
                    build_fn(standalone_engine, settings.downloads_dir)
                finally:
                    standalone_engine.dispose()

        msg = f"{db_name} update complete"

        _update_job(
            job_id,
            status="complete",
            progress_pct=100.0,
            message=msg,
        )

        logger.info(
            "database_update_task_complete",
            extra={"job_id": job_id, "db_name": db_name},
        )

    except Exception as exc:
        logger.exception(
            "database_update_task_failed",
            extra={"job_id": job_id, "db_name": db_name},
        )
        _update_job(
            job_id,
            status="failed",
            message=f"{db_name} update failed",
            error=str(exc),
        )


def create_backup_job() -> str:
    """Create a job record for a backup export task. Returns the job_id."""

    from backend.db.connection import get_registry
    from backend.db.tables import jobs

    job_id = str(uuid.uuid4())
    now = datetime.now(UTC)
    registry = get_registry()

    with registry.reference_engine.begin() as conn:
        conn.execute(
            jobs.insert().values(
                job_id=job_id,
                sample_id=None,
                job_type="backup_export",
                status="pending",
                progress_pct=0.0,
                message="Queued for backup export",
                created_at=now,
                updated_at=now,
            )
        )

    return job_id


@huey.task()
def run_backup_export_task(job_id: str, include_reference_dbs: bool = False) -> None:
    """Huey background task: create a .tar.gz backup archive.

    Archives sample DBs, config.toml, .disclaimer_accepted, and
    optionally reference database files.
    """
    import tarfile

    from backend.api.routes.backup import REFERENCE_DB_FILES

    archive_path: Path | None = None

    try:
        _update_job(job_id, status="running", message="Preparing backup archive")

        settings = get_settings()
        data_dir = settings.data_dir
        downloads_dir = settings.downloads_dir
        downloads_dir.mkdir(parents=True, exist_ok=True)

        # Generate timestamped filename
        timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        filename = f"yeliztli_backup_{timestamp}.tar.gz"
        archive_path = downloads_dir / filename

        # Collect files to archive
        files_to_add: list[tuple[Path, str]] = []

        # Config files. config.toml lives in the home dir (config_toml_path), the
        # single file Settings reads; the disclaimer flag stays under data_dir.
        config_path = config_toml_path()
        if config_path.exists():
            files_to_add.append((config_path, "config.toml"))

        disclaimer_path = data_dir / ".disclaimer_accepted"
        if disclaimer_path.exists():
            files_to_add.append((disclaimer_path, ".disclaimer_accepted"))

        # Sample DB files
        samples_dir = data_dir / "samples"
        if samples_dir.exists():
            for sample_db in sorted(samples_dir.glob("sample_*.db")):
                files_to_add.append((sample_db, f"samples/{sample_db.name}"))

        # Optional reference DBs
        if include_reference_dbs:
            for db_name in REFERENCE_DB_FILES:
                db_path = data_dir / db_name
                if db_path.exists():
                    files_to_add.append((db_path, db_name))

        total_files = len(files_to_add)
        if total_files == 0:
            # Create empty archive first, then mark complete
            with tarfile.open(archive_path, "w:gz") as _tf:
                pass
            _update_job(
                job_id,
                status="complete",
                progress_pct=100.0,
                message=f"Backup complete: {filename}",
            )
            logger.info("backup_export_empty", job_id=job_id, filename=filename)
            return

        _update_job(
            job_id,
            status="running",
            progress_pct=5.0,
            message=f"Archiving {total_files} file(s)",
        )

        with tarfile.open(archive_path, "w:gz") as tf:
            for idx, (file_path, arcname) in enumerate(files_to_add):
                tf.add(str(file_path), arcname=arcname)
                pct = 5.0 + (idx + 1) / total_files * 90.0
                _update_job(
                    job_id,
                    status="running",
                    progress_pct=round(pct, 1),
                    message=f"Archived {idx + 1}/{total_files}: {arcname}",
                )

        archive_size_mb = archive_path.stat().st_size / (1024 * 1024)
        _update_job(
            job_id,
            status="complete",
            progress_pct=100.0,
            message=f"Backup complete: {filename}",
        )

        logger.info(
            "backup_export_complete",
            job_id=job_id,
            filename=filename,
            files_archived=total_files,
            archive_size_mb=round(archive_size_mb, 1),
            include_reference_dbs=include_reference_dbs,
        )

    except Exception as exc:
        logger.exception("backup_export_failed", job_id=job_id)
        # Clean up partial archive on failure
        if archive_path is not None and archive_path.exists():
            try:
                archive_path.unlink()
            except OSError:
                pass
        _update_job(
            job_id,
            status="failed",
            message="Backup export failed",
            error=str(exc),
        )


def create_database_update_job(db_name: str) -> str:
    """Create a job record for a database update task. Returns the job_id."""

    from backend.db.connection import get_registry
    from backend.db.tables import jobs

    job_id = str(uuid.uuid4())
    now = datetime.now(UTC)
    registry = get_registry()

    with registry.reference_engine.begin() as conn:
        conn.execute(
            jobs.insert().values(
                job_id=job_id,
                sample_id=None,
                job_type="database_update",
                status="pending",
                progress_pct=0.0,
                message=f"Queued for {db_name} update",
                created_at=now,
                updated_at=now,
            )
        )

    return job_id


# Periodic task: fires daily at 03:00 by default.
# The actual check frequency is controlled by update_check_interval
# in settings — the periodic task reads the setting and skips if
# not yet due. Always fires once on startup via the lifespan hook.
@huey.periodic_task(crontab(hour="3", minute="0"))
def periodic_update_check() -> None:
    """Periodic Huey task: daily update check at 03:00.

    Respects the ``update_check_interval`` setting — if set to
    "startup" this task is effectively a no-op (startup check
    handled by lifespan). If "weekly", checks last run time and
    skips if < 7 days since last check.
    """
    import sqlalchemy as sa

    from backend.config import get_settings
    from backend.db.connection import get_registry
    from backend.db.tables import update_history

    settings = get_settings()

    if settings.update_check_interval == "startup":
        # Startup-only: periodic task does nothing
        return

    if settings.update_check_interval == "weekly":
        # Check if last update was within 7 days
        registry = get_registry()
        with registry.reference_engine.connect() as conn:
            last_check = conn.execute(
                sa.select(update_history.c.updated_at)
                .order_by(update_history.c.updated_at.desc())
                .limit(1)
            ).fetchone()

        if last_check and last_check.updated_at:
            from datetime import timedelta

            last_updated = last_check.updated_at
            if last_updated.tzinfo is None:
                last_updated = last_updated.replace(tzinfo=UTC)
            age = datetime.now(UTC) - last_updated
            if age < timedelta(days=7):
                logger.info("periodic_update_check_skipped_weekly", age_days=age.days)
                return

    # Run the check
    job_id = create_update_check_job()
    run_update_check_task(job_id)

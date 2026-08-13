"""Huey task queue configuration and tasks.

Uses SqliteHuey for persistent task state with a single worker.
In test/dev mode, immediate=True runs tasks synchronously.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import UTC, datetime, timedelta
from io import BytesIO
from pathlib import Path
from threading import Lock

import structlog
from huey import SqliteHuey, crontab
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import OperationalError

from backend.config import config_toml_path, get_settings
from backend.db.build_guard import build_lock
from backend.db.download_manager import HUEY_DOWNLOAD_JOB_TYPE
from backend.logging_config import configure_logging

logger = structlog.get_logger(__name__)

_worker_logging_schema_lock = Lock()
_worker_logging_schema_engine: object | None = None


def _get_reference_engine_for_logging():
    """Return a schema-current reference engine before a worker writes logs."""
    from backend.db.connection import get_registry
    from backend.db.reference_schema import (
        bootstrap_reference_schema_tables,
        ensure_log_entry_presentation_policy,
    )

    global _worker_logging_schema_engine

    engine = get_registry().reference_engine
    if _worker_logging_schema_engine is engine:
        return engine

    # Huey can start without FastAPI's lifespan.  Bootstrap once per engine so
    # the logging processor never silently loses a current-policy attestation
    # because an older reference.db has not reached application startup yet.
    with _worker_logging_schema_lock:
        if _worker_logging_schema_engine is not engine:
            # A direct worker owns job-state writes too, so retain the normal
            # missing-table bootstrap while keeping the policy migration itself
            # narrow and serialized below.
            bootstrap_reference_schema_tables(engine)
            ensure_log_entry_presentation_policy(engine)
            _worker_logging_schema_engine = engine

    return engine


def _configure_worker_logging() -> None:
    """Install structured log redaction for direct Huey worker startup."""
    configure_logging(engine_getter=_get_reference_engine_for_logging)


_configure_worker_logging()

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

HUEY_PENDING_ENQUEUE_GRACE = timedelta(minutes=5)
# How long a claimed job may go without refreshing its heartbeat before the
# lease is treated as abandoned. The heartbeat rides on progress reporting rather
# than a fixed tick, so this has to be long enough to cover the quietest stretch
# a healthy job can have between progress updates -- a slow annotation chunk must
# never look like a death.
HUEY_HEARTBEAT_GRACE = timedelta(minutes=15)

# Identity of *this* worker process. `_WORKER_OWNER_ID` is stable for the life of
# the process and `_WORKER_EPOCH` marks this particular run, so a row carrying an
# older epoch is provably held by a predecessor even if the pid was reused. Both
# are module state rather than configuration: nothing outside this process may
# claim to be it.
_WORKER_OWNER_ID = f"huey:{uuid.uuid4()}"
_WORKER_EPOCH = f"{os.getpid()}:{uuid.uuid4()}"


def worker_lease_identity() -> tuple[str, str]:
    """Return this worker's (owner_id, owner_epoch) lease identity."""
    return _WORKER_OWNER_ID, _WORKER_EPOCH


_HUEY_JOB_TASKS: dict[str, tuple[str, str, int]] = {
    "run_annotation_task": ("annotation", "job_id", 1),
    "run_lai_task": ("lai_analysis", "job_id", 1),
    "run_update_check_task": ("update_check", "job_id", 0),
    "run_database_update_task": ("database_update", "job_id", 0),
    "run_backup_export_task": ("backup_export", "job_id", 0),
}
_HUEY_OWNED_JOB_TYPES = frozenset(
    {
        *(spec[0] for spec in _HUEY_JOB_TASKS.values()),
        HUEY_DOWNLOAD_JOB_TYPE,
    }
)


# ── Job record helpers ──────────────────────────────────────────────────


def create_annotation_job(sample_id: int) -> str:
    """Create a job record for an annotation run. Returns the job_id."""
    from backend.db.connection import get_registry
    from backend.services.sample_operation_lock import (
        SampleOperationConflictError,
        reserve_annotation_job,
    )

    job_id = str(uuid.uuid4())
    now = datetime.now(UTC)
    registry = get_registry()
    try:
        reserve_annotation_job(
            registry.reference_engine,
            sample_id=sample_id,
            job_id=job_id,
            created_at=now,
        )
    except SampleOperationConflictError as exc:
        raise ValueError(str(exc)) from exc

    return job_id


def recover_orphaned_jobs(engine) -> int:
    """Fail process-owned leases abandoned by an API restart.

    Huey runs in a separate process in both systemd and Docker deployments.
    An API-only restart therefore cannot prove that a pending/running/cancelling
    Huey job is orphaned; changing one to ``failed`` would release the export
    interlock while the worker may still be mutating the sample. Immediate mode
    instead runs Huey inside the API process, so those jobs are process-owned
    and must be recovered by this sweep as well.
    """
    from backend.db.tables import jobs
    from backend.services.sample_operation_lock import (
        SAMPLE_EXPORT_JOB_TYPE,
        SAMPLE_MERGE_JOB_TYPE,
    )

    recoverable_job_types = {
        SAMPLE_EXPORT_JOB_TYPE,
        # A merge lease is synchronous and API-owned exactly like an export
        # lease, so an API restart proves it abandoned. Without this a process
        # killed mid-merge strands `running` lease rows forever: every later
        # merge on either source reports "already being merged" and every
        # delete answers 409, permanently (#2329).
        SAMPLE_MERGE_JOB_TYPE,
        "database_download",
        "download",
    }
    if huey.immediate:
        recoverable_job_types.update(_HUEY_OWNED_JOB_TYPES)

    with engine.begin() as conn:
        result = conn.execute(
            jobs.update()
            .where(jobs.c.job_type.in_(tuple(sorted(recoverable_job_types))))
            .where(jobs.c.status.in_(("pending", "running", "cancelling")))
            .values(
                status="failed",
                error="API restarted while the operation was in progress",
                message="Operation interrupted by backend restart",
                updated_at=datetime.now(UTC),
            )
        )
        count = result.rowcount or 0
    if count:
        logger.info("orphaned_jobs_recovered", count=count)
    return count


def recover_worker_orphaned_jobs(
    engine,
    *,
    queued_job_ids: set[str],
    recovery_time: datetime,
    recover_running: bool,
    owner_id: str | None = None,
    owner_epoch: str | None = None,
) -> int:
    """Release durable rows proven abandoned by the sole Huey worker.

    At worker startup, any ``running`` row belonged to the stopped predecessor.
    A pending row is abandoned only after the reservation-to-enqueue grace
    period and when its task is absent from the persistent queue. The periodic
    single-worker sweep revisits rows protected by that grace period without
    touching a task that can still be executing.
    """
    from backend.db.tables import jobs

    pending_cutoff = recovery_time - HUEY_PENDING_ENQUEUE_GRACE
    heartbeat_cutoff = recovery_time - HUEY_HEARTBEAT_GRACE
    orphaned_pending = (
        (jobs.c.status == "pending")
        & ((jobs.c.created_at.is_(None)) | (jobs.c.created_at < pending_cutoff))
        & jobs.c.job_id.not_in(queued_job_ids)
    )
    # Provably *someone else's* lease -- not merely "not proven to be mine".
    # A NULL owner is unclaimed, and unclaimed is exactly what a job type that
    # never takes a lease looks like: only annotation claims one, so a download
    # or backup export runs its whole life with `owner_id IS NULL`. Treating
    # that as abandoned would let the five-minutely sweep fail a task that is
    # still executing, which is the opposite of what the lease is for. An
    # unclaimed row is left to the startup sweep, where a restart is itself the
    # proof that nothing is still running (#2232).
    not_mine = jobs.c.owner_id.is_not(None) & (jobs.c.owner_id != owner_id)
    if owner_epoch is not None:
        not_mine |= jobs.c.owner_id.is_not(None) & (jobs.c.owner_epoch != owner_epoch)
    # A lease whose heartbeat has gone quiet past the grace period is abandoned
    # even if the owner never came back to say so.
    heartbeat_expired = (jobs.c.heartbeat_at.is_(None)) | (jobs.c.heartbeat_at < heartbeat_cutoff)
    abandoned_active = not_mine & heartbeat_expired

    orphaned = orphaned_pending
    if recover_running:
        # At startup every active row belongs to the stopped predecessor.
        orphaned |= jobs.c.status == "running"
    else:
        # Mid-flight, only release a running row whose owner is provably gone.
        orphaned |= (jobs.c.status == "running") & abandoned_active

    # `cancelling` is an ACTIVE state: it still holds the annotation/export
    # interlock, deliberately, until the worker acknowledges the cancel. It must
    # therefore be gated exactly like `running` — the previous unconditional
    # sweep flipped every cancelling row to `cancelled` on a 5-minutely tick,
    # releasing the interlock while the live worker was still inside
    # run_annotation() writing the sample database, which is the invariant the
    # state exists to protect.
    cancelling_orphaned = jobs.c.status == "cancelling"
    if not recover_running:
        cancelling_orphaned &= abandoned_active & jobs.c.job_id.not_in(queued_job_ids)

    with engine.begin() as conn:
        failed = conn.execute(
            jobs.update()
            .where(jobs.c.job_type.in_(_HUEY_OWNED_JOB_TYPES))
            .where(orphaned)
            .values(
                status="failed",
                error="Huey worker stopped before the background job completed",
                message="Background job interrupted by worker restart",
                updated_at=recovery_time,
            )
        )
        cancelled = conn.execute(
            jobs.update()
            .where(jobs.c.job_type.in_(_HUEY_OWNED_JOB_TYPES))
            .where(cancelling_orphaned)
            .values(
                status="cancelled",
                message="Cancelled during worker restart",
                updated_at=recovery_time,
            )
        )
        count = (failed.rowcount or 0) + (cancelled.rowcount or 0)
    if count:
        logger.info("worker_orphaned_jobs_recovered", count=count)
    return count


def _fail_annotation_reservation(job_id: str, *, error: str) -> None:
    """Release a pending annotation reservation after Huey enqueue fails."""
    from backend.db.connection import get_registry
    from backend.db.tables import jobs

    registry = get_registry()
    with registry.reference_engine.begin() as conn:
        result = conn.execute(
            jobs.update()
            .where(jobs.c.job_id == job_id)
            .where(jobs.c.job_type == "annotation")
            .where(jobs.c.status == "pending")
            .values(
                status="failed",
                error=error,
                message="Annotation could not be queued",
                updated_at=datetime.now(UTC),
            )
        )
        released = result.rowcount == 1
    if not released:
        logger.warning(
            "annotation_reservation_release_skipped",
            extra={"job_id": job_id},
        )


class AnnotationEnqueueError(RuntimeError):
    """Huey rejected an annotation after its durable reservation was created."""


def enqueue_annotation_job(sample_id: int) -> str:
    """Reserve and enqueue annotation, releasing the reservation on failure."""
    job_id = create_annotation_job(sample_id)
    try:
        run_annotation_task(sample_id, job_id)
    except Exception as exc:
        try:
            _fail_annotation_reservation(job_id, error=str(exc))
        except Exception as cleanup_exc:
            raise AnnotationEnqueueError(
                "Unable to queue annotation or release its reservation"
            ) from cleanup_exc
        raise AnnotationEnqueueError("Unable to queue annotation") from exc
    return job_id


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
        update = jobs.update().where(jobs.c.job_id == job_id)
        if status not in ("cancelled", "failed"):
            # A worker update racing a cancellation request must not reopen the
            # job as running/complete before the worker acknowledges it.
            update = update.where(jobs.c.status.not_in(("cancelling", "cancelled")))
        now = datetime.now(UTC)
        values: dict[str, object] = {
            "status": status,
            "progress_pct": progress_pct,
            "message": message,
            "error": error,
            "updated_at": now,
        }
        if status in ("pending", "running", "cancelling"):
            # Progress reporting doubles as the liveness signal, so a job that is
            # genuinely working can never look abandoned. A terminal status stops
            # refreshing: the lease is over, not stale.
            values["heartbeat_at"] = now
        result = conn.execute(update.values(**values))
        updated = result.rowcount != 0
    if not updated:
        logger.warning(
            "job_update_skipped",
            extra={"job_id": job_id, "attempted_status": status},
        )


def _claim_annotation_job(job_id: str) -> bool:
    """Atomically claim a pending row before touching sample data."""
    from backend.db.connection import get_registry
    from backend.db.tables import jobs

    registry = get_registry()
    now = datetime.now(UTC)
    with registry.reference_engine.begin() as conn:
        claimed = conn.execute(
            jobs.update()
            .where(jobs.c.job_id == job_id)
            .where(jobs.c.job_type == "annotation")
            .where(jobs.c.status == "pending")
            .values(
                status="running",
                message="Annotating…",
                updated_at=now,
                # Take the lease in the same atomic statement that takes the row.
                # Claiming without recording the owner would leave a running job
                # nobody can be shown to hold, which is the state #2232 is about.
                owner_id=_WORKER_OWNER_ID,
                owner_epoch=_WORKER_EPOCH,
                heartbeat_at=now,
            )
        )
        if claimed.rowcount == 1:
            return True
        conn.execute(
            jobs.update()
            .where(jobs.c.job_id == job_id)
            .where(jobs.c.job_type == "annotation")
            .where(jobs.c.status == "cancelling")
            .values(
                status="cancelled",
                message="Cancelled by user",
                updated_at=datetime.now(UTC),
            )
        )
    return False


def _is_job_cancelled(job_id: str) -> bool:
    """Check if cancellation was requested or acknowledged."""
    import sqlalchemy as sa

    from backend.db.connection import get_registry
    from backend.db.tables import jobs

    registry = get_registry()
    with registry.reference_engine.connect() as conn:
        row = conn.execute(sa.select(jobs.c.status).where(jobs.c.job_id == job_id)).fetchone()

    return row is not None and row.status in ("cancelling", "cancelled")


def _finalize_annotation_job(
    job_id: str,
    *,
    status: str,
    progress_pct: float,
    message: str,
    error: str | None = None,
) -> str | None:
    """Commit success/partial or acknowledge a racing cancellation atomically."""
    import sqlalchemy as sa

    from backend.db.connection import get_registry
    from backend.db.tables import jobs

    cancellation_requested = jobs.c.status.in_(("cancelling", "cancelled"))
    registry = get_registry()
    with registry.reference_engine.begin() as conn:
        result = conn.execute(
            jobs.update()
            .where(jobs.c.job_id == job_id)
            .where(jobs.c.job_type == "annotation")
            .values(
                status=sa.case((cancellation_requested, "cancelled"), else_=status),
                progress_pct=sa.case(
                    (cancellation_requested, jobs.c.progress_pct),
                    else_=progress_pct,
                ),
                message=sa.case(
                    (cancellation_requested, "Cancelled by user"),
                    else_=message,
                ),
                error=sa.case(
                    (cancellation_requested, jobs.c.error),
                    else_=error,
                ),
                updated_at=datetime.now(UTC),
            )
        )
        found = result.rowcount == 1
        row = (
            conn.execute(sa.select(jobs.c.status).where(jobs.c.job_id == job_id)).fetchone()
            if found
            else None
        )
    if not found:
        logger.warning(
            "_finalize_annotation_job: no annotation job found",
            extra={"job_id": job_id},
        )
    return None if row is None else row.status


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

    if not _claim_annotation_job(job_id):
        logger.info(
            "annotation_task_skipped_inactive",
            extra={"job_id": job_id, "sample_id": sample_id},
        )
        return

    try:
        # Look up sample DB path and get engine
        db_path = _get_sample_db_path(sample_id)
        sample_db_full = registry.settings.data_dir / db_path
        sample_engine = registry.get_sample_engine(sample_db_full)

        if _is_job_cancelled(job_id):
            raise AnnotationCancelledError(f"Job {job_id} cancelled by user")

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
        if _is_job_cancelled(job_id):
            raise AnnotationCancelledError(f"Job {job_id} cancelled by user")

        _update_job(
            job_id,
            status="running",
            progress_pct=95.0,
            message="Analyzing…",
        )
        analysis_ok = False
        reference_snapshot = None
        vep_db_path = (
            registry.settings.vep_bundle_db_path
            if registry.settings.vep_bundle_db_path.is_file()
            else None
        )
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
            pharmacogenomics_ok = isinstance(
                analysis_results.get("pharmacogenomics"),
                int,
            )
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
            from backend.db.sample_schema import CYP2C9_PHENYTOIN_REANALYSIS_STATE_KEY
            from backend.db.tables import annotation_state
            from backend.db.vep_version import (
                VERSIONLESS_VEP_BUNDLE_BASELINE,
                resolve_effective_vep_bundle_version,
            )
            from backend.services.reference_versions import (
                compact_reference_versions,
                read_current_reference_snapshot,
            )
            from backend.services.staleness import REFERENCE_VERSION_SNAPSHOT_KEY

            coverage_snapshot = dict(result.coverage_stats)
            bundle_version = coverage_snapshot.get("bundle_version")
            if bundle_version is None:
                try:
                    bundle_version = resolve_effective_vep_bundle_version(
                        registry.reference_engine,
                        vep_db_path,
                    )
                except OperationalError as exc:
                    bundle_version = VERSIONLESS_VEP_BUNDLE_BASELINE
                    logger.warning(
                        "vep_bundle_version_resolution_failed",
                        extra={
                            "job_id": job_id,
                            "sample_id": sample_id,
                            "fallback_version": bundle_version,
                            "error": str(exc),
                        },
                    )
            # Pin every successful-run record to one resolved value.  Copying
            # keeps the annotation result immutable for callers while ensuring
            # telemetry cannot retain ``null`` when state/provenance use the
            # resolver fallback.
            coverage_snapshot["bundle_version"] = bundle_version
            reference_snapshot = read_current_reference_snapshot(
                registry.reference_engine,
                vep_db_path,
                effective_vep_version=bundle_version,
            )
            reference_versions = compact_reference_versions(reference_snapshot)
            with sample_engine.begin() as conn:
                _upsert_annotation_state(conn, "vep_bundle_version", bundle_version)
                _upsert_annotation_state(
                    conn,
                    "annotation_bundle_coverage_json",
                    json.dumps(coverage_snapshot),
                )
                _upsert_annotation_state(
                    conn,
                    REFERENCE_VERSION_SNAPSHOT_KEY,
                    json.dumps(reference_versions),
                )
                if pharmacogenomics_ok:
                    conn.execute(
                        annotation_state.delete().where(
                            annotation_state.c.key == CYP2C9_PHENYTOIN_REANALYSIS_STATE_KEY
                        )
                    )
            if pharmacogenomics_ok:
                # The engine may already be cached as synchronized from before
                # analysis. Reconcile immediately after the authoritative local
                # marker disappears so its identity-bound correction does not
                # remain visible until restart.
                registry.reconcile_sample_reanalysis_prompt(sample_db_full)
            logger.info(
                "annotation_state_upserted",
                extra={
                    "job_id": job_id,
                    "sample_id": sample_id,
                    "vep_bundle_version": bundle_version,
                    "reference_version_count": len(reference_versions),
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

            stamped = stamp_findings_provenance(
                sample_engine,
                registry.reference_engine,
                vep_db_path,
                reference_snapshot=reference_snapshot,
            )
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
                    sample_engine,
                    registry.reference_engine,
                    prior_findings,
                    vep_db_path=vep_db_path,
                    reference_snapshot=reference_snapshot,
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

        if _is_job_cancelled(job_id):
            raise AnnotationCancelledError(f"Job {job_id} cancelled by user")

        resolved_status = _finalize_annotation_job(
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

        if resolved_status == "cancelled":
            logger.info(
                "annotation_task_cancelled",
                extra={"job_id": job_id, "sample_id": sample_id},
            )
            return

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
        _update_job(
            job_id,
            status="cancelled",
            message="Cancelled by user",
        )

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


def _worker_job_from_task(task) -> tuple[str, str] | None:
    """Extract ``(job_type, job_id)`` from a durable Huey task."""
    spec = _HUEY_JOB_TASKS.get(getattr(task, "name", None))
    if spec is None:
        return None
    job_type, parameter_name, position = spec
    try:
        args, kwargs = task.data
    except (AttributeError, TypeError, ValueError):
        return None
    job_id = kwargs.get(parameter_name) if isinstance(kwargs, dict) else None
    if job_id is None and isinstance(args, (list, tuple)) and len(args) > position:
        job_id = args[position]
    return (job_type, job_id) if isinstance(job_id, str) else None


def _queued_worker_job_ids() -> set[str] | None:
    """Return a complete queue snapshot, or ``None`` when it is unavailable."""
    try:
        queued_tasks = [*huey.pending(), *huey.scheduled()]
    except Exception:
        logger.exception("worker_queue_snapshot_failed")
        return None
    return {
        worker_job[1]
        for task in queued_tasks
        if (worker_job := _worker_job_from_task(task)) is not None
    }


@huey.on_startup(name="recover_worker_jobs")
def _recover_jobs_on_worker_startup() -> None:
    """Fence rows owned by the stopped predecessor worker."""
    from backend.db.connection import get_registry

    queued_job_ids = _queued_worker_job_ids()
    if queued_job_ids is None:
        return
    recover_worker_orphaned_jobs(
        get_registry().reference_engine,
        queued_job_ids=queued_job_ids,
        recovery_time=datetime.now(UTC),
        recover_running=True,
    )


@huey.periodic_task(crontab(minute="*/5"))
def recover_stale_pending_worker_jobs() -> None:
    """Revisit enqueue-window rows without releasing a live worker task."""
    from backend.db.connection import get_registry

    queued_job_ids = _queued_worker_job_ids()
    if queued_job_ids is None:
        return
    owner_id, owner_epoch = worker_lease_identity()
    recover_worker_orphaned_jobs(
        get_registry().reference_engine,
        queued_job_ids=queued_job_ids,
        recovery_time=datetime.now(UTC),
        recover_running=False,
        owner_id=owner_id,
        owner_epoch=owner_epoch,
    )


# ── LAI analysis task (AMv2 Step 4) ───────────────────────────────────


def create_lai_job(sample_id: int) -> str:
    """Create a job record for an LAI analysis run. Returns the job_id."""
    import sqlalchemy as sa

    from backend.db.connection import get_registry
    from backend.db.tables import jobs
    from backend.services.sample_operation_lock import ACTIVE_JOB_STATUSES

    job_id = str(uuid.uuid4())
    now = datetime.now(UTC)
    registry = get_registry()

    with registry.reference_engine.begin() as conn:
        # Check for already-running LAI job on this sample
        existing = conn.execute(
            sa.select(jobs.c.job_id).where(
                jobs.c.sample_id == sample_id,
                jobs.c.job_type == "lai_analysis",
                jobs.c.status.in_(ACTIVE_JOB_STATUSES),
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
    from backend.services.lai_production_coverage import (
        LAICoveragePolicyUnavailableError,
        encode_lai_insufficient_data_reason,
    )

    registry = get_registry()

    try:
        db_path = _get_sample_db_path(sample_id)
        sample_db_full = registry.settings.data_dir / db_path
        sample_engine = registry.get_sample_engine(sample_db_full)

        if _is_job_cancelled(job_id):
            raise AnnotationCancelledError(f"Job {job_id} cancelled by user")

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

        chromosomes_analyzed = result.metadata.get("chromosomes_analyzed", 0)
        if chromosomes_analyzed <= 0 or not result.global_ancestry:
            raise RuntimeError(
                "Insufficient data for local ancestry inference: no analyzed "
                "chromosomes or ancestry estimates were produced"
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
                f"LAI complete: {chromosomes_analyzed} "
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
        _update_job(
            job_id,
            status="cancelled",
            message="Cancelled by user",
        )

    except LAICoveragePolicyUnavailableError as exc:
        logger.info(
            "lai_task_insufficient_validation_data",
            job_id=job_id,
            sample_id=sample_id,
            reason_code=exc.reason.code,
        )
        _update_job(
            job_id,
            status="failed",
            message="Insufficient data for chromosome painting",
            error=encode_lai_insufficient_data_reason(exc.reason),
        )

    except Exception as exc:
        logger.exception("lai_task_failed", job_id=job_id, sample_id=sample_id)
        _update_job(
            job_id,
            status="failed",
            message="LAI analysis failed",
            error=str(exc),
        )


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
    from backend.db.download_manager import huey_download_job_ownership
    from backend.db.update_manager import run_scheduled_update_check

    try:
        _update_job(job_id, status="running", message="Checking for database updates")

        registry = get_registry()
        with huey_download_job_ownership():
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
    from backend.db.download_manager import huey_download_job_ownership

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
        with huey_download_job_ownership():
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

        def _run_version_staleness_check() -> None:
            from backend.db.update_manager import get_current_version, run_precheck_all_samples

            version: str | None = None
            try:
                version = get_current_version(registry.reference_engine, db_name) or "unknown"
                run_precheck_all_samples(registry, db_name=db_name, db_version=version)
            except Exception as exc:
                logger.warning(
                    "database_update_staleness_check_failed",
                    extra={
                        "job_id": job_id,
                        "db_name": db_name,
                        "db_version": version,
                        "reference_engine": str(registry.reference_engine.url),
                        "error": str(exc),
                    },
                )

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
            _run_version_staleness_check()
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

        # LAI / ancestry-PCA / gnomAD / PGS bundles flow through their manifest-driven
        # runners (same path as the scheduler's _dispatch_auto_update), not a
        # build_fn. gnomAD is no longer in _BUILD_FN_REGISTRY, so it MUST be
        # caught here before the build_fn branch below (which would raise).
        if db_name in ("lai_bundle", "ancestry_pca", "gnomad", "pgs_scores"):
            from backend.db.manifest import get_bundle_info
            from backend.db.update_manager import (
                run_ancestry_pca_bundle_update,
                run_gnomad_bundle_update,
                run_lai_bundle_update,
                run_pgs_scores_bundle_update,
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
                "pgs_scores": run_pgs_scores_bundle_update,
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
            _run_version_staleness_check()
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

        if db_name == "encode_ccres":
            from backend.db.update_manager import run_encode_ccres_update

            _update_job(
                job_id,
                status="running",
                progress_pct=10.0,
                message="Downloading ENCODE cCREs",
            )
            result = run_encode_ccres_update(registry.settings)
            if result is None:
                raise RuntimeError("ENCODE cCREs update failed")
            _run_version_staleness_check()
            _update_job(
                job_id,
                status="complete",
                progress_pct=100.0,
                message="encode_ccres update complete",
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
                from backend.db.sqlite_engine import make_sqlite_engine

                dest = (
                    db_info.dest_path(settings) if db_info else settings.data_dir / f"{db_name}.db"
                )
                dest.parent.mkdir(parents=True, exist_ok=True)
                # This build OWNS the standalone DB file, so it opts into WAL
                # (wal=True) exactly as the old hand-rolled listener did; the
                # factory also applies busy_timeout so the build waits out
                # (instead of failing on) a concurrent writer of the same file.
                standalone_engine = make_sqlite_engine(dest, wal=True, synchronous="NORMAL")

                try:
                    build_fn(standalone_engine, settings.downloads_dir)
                finally:
                    standalone_engine.dispose()

        _run_version_staleness_check()

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

    Archives sample DBs, config.toml, .disclaimer_accepted, a central sample
    registry manifest, and optionally standalone reference files.
    """
    import tarfile

    from backend.api.routes.backup import (
        REFERENCE_DB_FILES,
        REGISTRY_MANIFEST_FILE,
        build_sample_registry_manifest,
    )

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
        files_to_add: list[tuple[Path | bytes, str]] = []

        # Config files. config.toml lives in the home dir (config_toml_path), the
        # single file Settings reads; the disclaimer flag stays under data_dir.
        config_path = config_toml_path()
        if config_path.exists():
            files_to_add.append((config_path, "config.toml"))

        disclaimer_path = data_dir / ".disclaimer_accepted"
        if disclaimer_path.exists():
            files_to_add.append((disclaimer_path, ".disclaimer_accepted"))

        # Central registry metadata. This is small and required for restored
        # sample files to appear in the app; build it from the live connection
        # so WAL-mode writes are included.
        files_to_add.append((build_sample_registry_manifest(data_dir), REGISTRY_MANIFEST_FILE))

        # Sample DB files
        samples_dir = data_dir / "samples"
        if samples_dir.exists():
            for sample_db in sorted(samples_dir.glob("sample_*.db")):
                files_to_add.append((sample_db, f"samples/{sample_db.name}"))

        # Optional standalone reference files
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
                if isinstance(file_path, bytes):
                    info = tarfile.TarInfo(name=arcname)
                    info.size = len(file_path)
                    tf.addfile(info, BytesIO(file_path))
                else:
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

    if settings.update_check_interval in ("off", "startup"):
        # "off": automatic update checks disabled — no outbound check ever (#1241).
        # "startup": startup-only, so the periodic task does nothing.
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

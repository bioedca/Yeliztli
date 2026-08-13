"""Atomic per-sample exclusion between annotation and export operations.

SQLite does not provide a process-wide named-lock primitive.  The reference
database's ``jobs`` table therefore acts as a recoverable lease registry:

* annotation reservation and export-lease acquisition both use
  ``BEGIN IMMEDIATE`` so their check-and-insert steps serialize;
* an export keeps a ``running`` lease row for its full render/build lifetime;
* annotation's ``cancelling`` state remains active until the worker
  acknowledges cancellation; and
* API startup recovers only API-owned export leases, while the sole Huey
  worker reconciles annotation rows against its persistent queue at startup.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime

import sqlalchemy as sa

from backend.db.tables import jobs

logger = logging.getLogger(__name__)

SAMPLE_EXPORT_JOB_TYPE = "sample_export"
# A merge reads both source databases for its whole materialisation, so a source
# must not be deleted underneath it (#2329). One lease row per source id shares a
# batch uuid, which lets deletion ask "is any merge holding this sample?" with a
# single indexed lookup on ``sample_id`` rather than parsing a composite key.
SAMPLE_MERGE_JOB_TYPE = "sample_merge"
# Deletion's own reservation, so its exclusion with a merge is atomic rather
# than a read-then-act pre-check.
SAMPLE_DELETE_JOB_TYPE = "sample_delete"
# The one definition of "this job is still doing something". `cancelling` belongs
# here: the worker has not acknowledged the cancel yet, so the job may still be
# writing. Every "is anything in flight?" predicate must use this rather than
# spell out its own tuple -- three of them already said ("pending", "running")
# and would have let a second job start alongside one that was winding down.
ACTIVE_JOB_STATUSES = ("pending", "running", "cancelling")
ACTIVE_ANNOTATION_STATUSES = ACTIVE_JOB_STATUSES
# Exports are synchronous and API-owned, so they never reach `cancelling`.
ACTIVE_EXPORT_STATUSES = ("pending", "running")
# Merges are synchronous and API-owned like exports, so they never reach
# `cancelling` either.
ACTIVE_MERGE_STATUSES = ("pending", "running")


class SampleOperationConflictError(RuntimeError):
    """A mutually exclusive sample operation is already active."""


class SampleOperationUnavailableError(RuntimeError):
    """The lease registry could not be read or updated safely."""


@contextmanager
def _immediate_transaction(engine: sa.Engine) -> Iterator[sa.Connection]:
    """Open a SQLite write transaction before performing any lease reads."""
    conn = engine.connect()
    try:
        conn.exec_driver_sql("BEGIN IMMEDIATE")
        yield conn
    except BaseException:
        conn.rollback()
        raise
    else:
        conn.commit()
    finally:
        conn.close()


def reserve_annotation_job(
    engine: sa.Engine,
    *,
    sample_id: int,
    job_id: str,
    created_at: datetime,
) -> None:
    """Atomically reserve annotation when no annotation/export lease is active."""
    try:
        with _immediate_transaction(engine) as conn:
            annotation = conn.execute(
                sa.select(jobs.c.job_id)
                .where(jobs.c.sample_id == sample_id)
                .where(jobs.c.job_type == "annotation")
                .where(jobs.c.status.in_(ACTIVE_ANNOTATION_STATUSES))
                .limit(1)
            ).fetchone()
            if annotation is not None:
                raise SampleOperationConflictError(
                    f"Annotation already in progress for sample {sample_id} "
                    f"(job {annotation.job_id})"
                )

            export = conn.execute(
                sa.select(jobs.c.job_id)
                .where(jobs.c.sample_id == sample_id)
                .where(jobs.c.job_type == SAMPLE_EXPORT_JOB_TYPE)
                .where(jobs.c.status.in_(ACTIVE_EXPORT_STATUSES))
                .limit(1)
            ).fetchone()
            if export is not None:
                raise SampleOperationConflictError(
                    f"An export is in progress for sample {sample_id}; "
                    "retry annotation after it completes."
                )

            conn.execute(
                jobs.insert().values(
                    job_id=job_id,
                    sample_id=sample_id,
                    job_type="annotation",
                    status="pending",
                    progress_pct=0.0,
                    message="Queued for annotation",
                    created_at=created_at,
                    updated_at=created_at,
                )
            )
    except SampleOperationConflictError:
        raise
    except sa.exc.SQLAlchemyError as exc:
        raise SampleOperationUnavailableError(
            f"Unable to reserve annotation for sample {sample_id}."
        ) from exc


def _finish_export_lease(
    engine: sa.Engine,
    lease_id: str,
    *,
    status: str,
    message: str,
) -> None:
    try:
        with engine.begin() as conn:
            result = conn.execute(
                jobs.update()
                .where(jobs.c.job_id == lease_id)
                .where(jobs.c.job_type == SAMPLE_EXPORT_JOB_TYPE)
                .values(
                    status=status,
                    progress_pct=100.0,
                    message=message,
                    updated_at=datetime.now(UTC),
                )
            )
        if result.rowcount != 1:
            raise SampleOperationUnavailableError(
                f"Export lease {lease_id} could not be finalized."
            )
    except SampleOperationUnavailableError:
        raise
    except sa.exc.SQLAlchemyError as exc:
        raise SampleOperationUnavailableError(
            f"Export lease {lease_id} could not be finalized."
        ) from exc


@contextmanager
def sample_export_lease(
    engine: sa.Engine,
    sample_id: int,
    *,
    operation: str,
) -> Iterator[str]:
    """Hold an export lease for the complete render/build operation."""
    lease_id = str(uuid.uuid4())
    now = datetime.now(UTC)
    try:
        with _immediate_transaction(engine) as conn:
            annotation = conn.execute(
                sa.select(jobs.c.job_id)
                .where(jobs.c.sample_id == sample_id)
                .where(jobs.c.job_type == "annotation")
                .where(jobs.c.status.in_(ACTIVE_ANNOTATION_STATUSES))
                .limit(1)
            ).fetchone()
            if annotation is not None:
                raise SampleOperationConflictError(
                    f"Annotation is in progress for sample {sample_id}; "
                    "retry the export after it completes."
                )

            conn.execute(
                jobs.insert().values(
                    job_id=lease_id,
                    sample_id=sample_id,
                    job_type=SAMPLE_EXPORT_JOB_TYPE,
                    status="running",
                    progress_pct=0.0,
                    message=operation,
                    created_at=now,
                    updated_at=now,
                )
            )
    except SampleOperationConflictError:
        raise
    except sa.exc.SQLAlchemyError as exc:
        raise SampleOperationUnavailableError(
            f"Unable to verify annotation status for sample {sample_id}; "
            "the export was not started."
        ) from exc

    try:
        yield lease_id
    except BaseException:
        try:
            _finish_export_lease(
                engine,
                lease_id,
                status="failed",
                message=f"{operation} failed",
            )
        except SampleOperationUnavailableError:
            logger.exception(
                "sample_export_lease_cleanup_failed",
                extra={"lease_id": lease_id, "sample_id": sample_id},
            )
        raise
    else:
        _finish_export_lease(
            engine,
            lease_id,
            status="complete",
            message=f"{operation} complete",
        )


def _finish_merge_lease(
    engine: sa.Engine,
    lease_id: str,
    *,
    status: str,
    message: str,
) -> None:
    """Release every source row a merge lease reserved."""
    try:
        with engine.begin() as conn:
            conn.execute(
                jobs.update()
                .where(jobs.c.job_id.startswith(f"{lease_id}:"))
                .where(jobs.c.job_type == SAMPLE_MERGE_JOB_TYPE)
                .values(
                    status=status,
                    progress_pct=100.0,
                    message=message,
                    updated_at=datetime.now(UTC),
                )
            )
    except sa.exc.SQLAlchemyError as exc:
        raise SampleOperationUnavailableError(
            f"Merge lease {lease_id} could not be finalized."
        ) from exc


def _active_merge_sources(conn: sa.Connection, sample_ids: list[int]) -> list[int]:
    """Sources a merge currently holds, read inside the caller's transaction."""
    if not sample_ids:
        return []
    rows = conn.execute(
        sa.select(jobs.c.sample_id)
        .where(jobs.c.sample_id.in_(sample_ids))
        .where(jobs.c.job_type == SAMPLE_MERGE_JOB_TYPE)
        .where(jobs.c.status.in_(ACTIVE_MERGE_STATUSES))
        .distinct()
    ).fetchall()
    return sorted(int(row.sample_id) for row in rows)


def merge_sources_in_use(engine: sa.Engine, sample_ids: list[int]) -> list[int]:
    """Report which of ``sample_ids`` a merge holds. Diagnostics only.

    This is a plain read, so it is **not** sufficient to gate a destructive
    operation: a merge can acquire its lease between the read and the caller's
    next statement. Deletion must use :func:`sample_delete_lease`, which does
    its check and its own insert inside one ``BEGIN IMMEDIATE``.
    """
    with engine.connect() as conn:
        return _active_merge_sources(conn, sample_ids)


@contextmanager
def sample_delete_lease(
    engine: sa.Engine,
    sample_ids: list[int],
    *,
    operation: str,
) -> Iterator[str]:
    """Reserve samples for deletion, excluding merges atomically.

    A read-only pre-check cannot close this window: a merge could acquire its
    own lease between the check and the first ``unlink``, then stream a
    database that deletion is concurrently removing. Both sides therefore
    check-and-insert inside ``BEGIN IMMEDIATE``, so whichever transaction
    commits first is observed by the other.

    Scoped deliberately to merge-vs-delete exclusion. A broader deletion lease
    covering annotation, export and LAI is #2314's; this reserves the same
    ``jobs`` registry under its own job type, so the two compose rather than
    conflict.
    """
    lease_id = str(uuid.uuid4())
    now = datetime.now(UTC)
    ordered_ids = sorted(set(sample_ids))
    try:
        with _immediate_transaction(engine) as conn:
            busy = _active_merge_sources(conn, ordered_ids)
            if busy:
                raise SampleOperationConflictError(
                    f"Sample {busy[0]} is being merged; "
                    "retry the delete once that merge completes."
                )
            for sample_id in ordered_ids:
                conn.execute(
                    jobs.insert().values(
                        job_id=f"{lease_id}:{sample_id}",
                        sample_id=sample_id,
                        job_type=SAMPLE_DELETE_JOB_TYPE,
                        status="running",
                        progress_pct=0.0,
                        message=operation,
                        created_at=now,
                        updated_at=now,
                    )
                )
    except SampleOperationConflictError:
        raise
    except sa.exc.SQLAlchemyError as exc:
        raise SampleOperationUnavailableError(
            f"Unable to reserve samples {ordered_ids} for deletion; nothing was removed."
        ) from exc

    try:
        yield lease_id
    finally:
        # The rows this lease inserted name samples that no longer exist on the
        # success path, so clear them outright rather than leaving `failed`
        # rows pointing at deleted ids.
        try:
            with engine.begin() as conn:
                conn.execute(
                    jobs.delete()
                    .where(jobs.c.job_id.startswith(f"{lease_id}:"))
                    .where(jobs.c.job_type == SAMPLE_DELETE_JOB_TYPE)
                )
        except sa.exc.SQLAlchemyError:
            logger.exception(
                "sample_delete_lease_cleanup_failed",
                extra={"lease_id": lease_id, "sample_ids": ordered_ids},
            )


@contextmanager
def sample_merge_lease(
    engine: sa.Engine,
    source_sample_ids: list[int],
    *,
    operation: str,
) -> Iterator[str]:
    """Reserve every merge source for the whole materialisation.

    Acquires all sources in one ``BEGIN IMMEDIATE`` transaction, so a
    concurrent merge or deletion either observes the complete reservation or
    none of it — a partial hold would let two operations each believe they had
    won a different half of the pair.
    """
    lease_id = str(uuid.uuid4())
    now = datetime.now(UTC)
    ordered_ids = sorted(set(source_sample_ids))
    try:
        with _immediate_transaction(engine) as conn:
            for sample_id in ordered_ids:
                conflict = conn.execute(
                    sa.select(jobs.c.job_id)
                    .where(jobs.c.sample_id == sample_id)
                    .where(jobs.c.job_type == SAMPLE_MERGE_JOB_TYPE)
                    .where(jobs.c.status.in_(ACTIVE_MERGE_STATUSES))
                    .limit(1)
                ).fetchone()
                if conflict is not None:
                    raise SampleOperationConflictError(
                        f"Sample {sample_id} is already being merged; "
                        "retry once that merge completes."
                    )
                # Symmetry is what makes the exclusion mutual: without this a
                # merge could start against a sample whose deletion already
                # holds a reservation and is midway through unlinking files.
                deleting = conn.execute(
                    sa.select(jobs.c.job_id)
                    .where(jobs.c.sample_id == sample_id)
                    .where(jobs.c.job_type == SAMPLE_DELETE_JOB_TYPE)
                    .where(jobs.c.status.in_(ACTIVE_MERGE_STATUSES))
                    .limit(1)
                ).fetchone()
                if deleting is not None:
                    raise SampleOperationConflictError(
                        f"Sample {sample_id} is being deleted; the merge was not started."
                    )
            for sample_id in ordered_ids:
                conn.execute(
                    jobs.insert().values(
                        job_id=f"{lease_id}:{sample_id}",
                        sample_id=sample_id,
                        job_type=SAMPLE_MERGE_JOB_TYPE,
                        status="running",
                        progress_pct=0.0,
                        message=operation,
                        created_at=now,
                        updated_at=now,
                    )
                )
    except SampleOperationConflictError:
        raise
    except sa.exc.SQLAlchemyError as exc:
        raise SampleOperationUnavailableError(
            f"Unable to reserve merge sources {ordered_ids}; the merge was not started."
        ) from exc

    try:
        yield lease_id
    except BaseException:
        try:
            _finish_merge_lease(engine, lease_id, status="failed", message=f"{operation} failed")
        except SampleOperationUnavailableError:
            logger.exception(
                "sample_merge_lease_cleanup_failed",
                extra={"lease_id": lease_id, "source_sample_ids": ordered_ids},
            )
        raise
    else:
        _finish_merge_lease(engine, lease_id, status="complete", message=f"{operation} complete")

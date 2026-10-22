"""Atomic per-sample exclusion between ingest, annotation, export, LAI, merge and delete.

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
from collections.abc import Callable, Iterator
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
# Deletion's own reservation, so its exclusion with every operation that holds
# a sample's database open is atomic rather than a read-then-act pre-check.
SAMPLE_DELETE_JOB_TYPE = "sample_delete"
ANNOTATION_JOB_TYPE = "annotation"
# An upload publishes its ``samples`` row before it has built the database that
# row names, and since #2574 it runs off the event loop, so other requests can
# reach the sample meanwhile. The ingest job row is therefore a lease: written
# ``running`` in the transaction that allocates the id, and finished only once
# the database is complete.
SAMPLE_INGEST_JOB_TYPE = "ingest"
LAI_JOB_TYPE = "lai_analysis"
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
# Deletion is synchronous and API-owned as well.
ACTIVE_DELETE_STATUSES = ("pending", "running")
# So is ingest.
ACTIVE_INGEST_STATUSES = ("pending", "running")
# Every operation that holds a sample's database open for its whole run, and so
# must not have that database unlinked underneath it (#2314). The export lease
# also covers report renders and the GRCh38 liftover batch, which take it
# through ``sample_export_guard``. A second delete is refused too: two
# overlapping deletes of one id would let the later one act on a row the earlier
# already removed, and that rowid can be reused by the next upload. Each value
# is the operation's active statuses and how a refused delete names it.
_DELETE_BLOCKING_OPERATIONS: dict[str, tuple[tuple[str, ...], str]] = {
    SAMPLE_DELETE_JOB_TYPE: (ACTIVE_DELETE_STATUSES, "already being deleted"),
    SAMPLE_INGEST_JOB_TYPE: (ACTIVE_INGEST_STATUSES, "still being imported"),
    SAMPLE_MERGE_JOB_TYPE: (ACTIVE_MERGE_STATUSES, "being merged"),
    ANNOTATION_JOB_TYPE: (ACTIVE_ANNOTATION_STATUSES, "being annotated"),
    SAMPLE_EXPORT_JOB_TYPE: (ACTIVE_EXPORT_STATUSES, "busy with an export, report or liftover"),
    LAI_JOB_TYPE: (ACTIVE_JOB_STATUSES, "running local ancestry inference"),
}


class SampleOperationConflictError(RuntimeError):
    """A mutually exclusive sample operation is already active."""


class SampleOperationUnavailableError(RuntimeError):
    """The lease registry could not be read or updated safely."""


def _deletion_in_progress(conn: sa.Connection, sample_id: int) -> bool:
    """Whether a delete holds ``sample_id``, read inside the caller's transaction.

    Every acquirer checks this so the exclusion with deletion is mutual: an
    operation that starts after deletion reserved the sample must lose, not
    reopen a database the cascade is about to unlink.
    """
    return (
        conn.execute(
            sa.select(jobs.c.job_id)
            .where(jobs.c.sample_id == sample_id)
            .where(jobs.c.job_type == SAMPLE_DELETE_JOB_TYPE)
            .where(jobs.c.status.in_(ACTIVE_DELETE_STATUSES))
            .limit(1)
        ).fetchone()
        is not None
    )


def ingest_in_progress(conn: sa.Connection, sample_id: int) -> bool:
    """Whether ``sample_id``'s upload is still building its database.

    Read inside the caller's transaction. An operation that opened the database
    now would read a partial ``raw_variants`` table as if it were the sample.
    """
    return (
        conn.execute(
            sa.select(jobs.c.job_id)
            .where(jobs.c.sample_id == sample_id)
            .where(jobs.c.job_type == SAMPLE_INGEST_JOB_TYPE)
            .where(jobs.c.status.in_(ACTIVE_INGEST_STATUSES))
            .limit(1)
        ).fetchone()
        is not None
    )


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
    """Atomically reserve annotation when no annotation/export/delete lease is active."""
    try:
        with _immediate_transaction(engine) as conn:
            annotation = conn.execute(
                sa.select(jobs.c.job_id)
                .where(jobs.c.sample_id == sample_id)
                .where(jobs.c.job_type == ANNOTATION_JOB_TYPE)
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
            if _deletion_in_progress(conn, sample_id):
                raise SampleOperationConflictError(
                    f"Sample {sample_id} is being deleted; annotation was not started."
                )
            if ingest_in_progress(conn, sample_id):
                raise SampleOperationConflictError(
                    f"Sample {sample_id} is still being imported; annotation was not started."
                )

            conn.execute(
                jobs.insert().values(
                    job_id=job_id,
                    sample_id=sample_id,
                    job_type=ANNOTATION_JOB_TYPE,
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


def reserve_lai_job(
    engine: sa.Engine,
    *,
    sample_id: int,
    job_id: str,
    created_at: datetime,
) -> None:
    """Atomically reserve LAI when no LAI run or deletion is active."""
    try:
        with _immediate_transaction(engine) as conn:
            existing = conn.execute(
                sa.select(jobs.c.job_id)
                .where(jobs.c.sample_id == sample_id)
                .where(jobs.c.job_type == LAI_JOB_TYPE)
                .where(jobs.c.status.in_(ACTIVE_JOB_STATUSES))
                .limit(1)
            ).fetchone()
            if existing is not None:
                raise SampleOperationConflictError(
                    f"LAI analysis already in progress for sample {sample_id} "
                    f"(job {existing.job_id})"
                )
            if _deletion_in_progress(conn, sample_id):
                raise SampleOperationConflictError(
                    f"Sample {sample_id} is being deleted; LAI analysis was not started."
                )
            if ingest_in_progress(conn, sample_id):
                raise SampleOperationConflictError(
                    f"Sample {sample_id} is still being imported; LAI analysis was not started."
                )

            conn.execute(
                jobs.insert().values(
                    job_id=job_id,
                    sample_id=sample_id,
                    job_type=LAI_JOB_TYPE,
                    status="pending",
                    progress_pct=0.0,
                    message="Queued for local ancestry inference",
                    created_at=created_at,
                    updated_at=created_at,
                )
            )
    except SampleOperationConflictError:
        raise
    except sa.exc.SQLAlchemyError as exc:
        raise SampleOperationUnavailableError(
            f"Unable to reserve LAI analysis for sample {sample_id}."
        ) from exc


@contextmanager
def ingest_publication(
    engine: sa.Engine,
    *,
    job_id: str,
    created_at: datetime,
) -> Iterator[tuple[sa.Connection, Callable[[int], None]]]:
    """Allocate a sample id and take its ingest lease in one transaction.

    Yields the open ``BEGIN IMMEDIATE`` connection and a ``reserve(sample_id)``
    callable. The caller inserts the ``samples`` row, then calls ``reserve`` with
    the id it got back; the row and the lease commit together, so no other
    request can observe the sample without also observing that it is still
    being imported.

    ``reserve`` refuses an id an active deletion already holds. SQLite reuses a
    vacated rowid, so a delete that reserved an id before this upload allocated
    it would otherwise read the new row afterwards and unlink the database this
    upload is about to build.
    """
    try:
        with _immediate_transaction(engine) as conn:

            def reserve(sample_id: int) -> None:
                if _deletion_in_progress(conn, sample_id):
                    raise SampleOperationConflictError(
                        f"Sample id {sample_id} is still being deleted; retry the upload."
                    )
                conn.execute(
                    jobs.insert().values(
                        job_id=job_id,
                        sample_id=sample_id,
                        job_type=SAMPLE_INGEST_JOB_TYPE,
                        status="running",
                        progress_pct=0.0,
                        message="Importing raw data",
                        created_at=created_at,
                        updated_at=created_at,
                    )
                )

            yield conn, reserve
    except SampleOperationConflictError:
        raise
    except sa.exc.SQLAlchemyError as exc:
        raise SampleOperationUnavailableError(
            "Unable to register the upload; nothing was stored."
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
                .where(jobs.c.job_type == ANNOTATION_JOB_TYPE)
                .where(jobs.c.status.in_(ACTIVE_ANNOTATION_STATUSES))
                .limit(1)
            ).fetchone()
            if annotation is not None:
                raise SampleOperationConflictError(
                    f"Annotation is in progress for sample {sample_id}; "
                    "retry the export after it completes."
                )
            if _deletion_in_progress(conn, sample_id):
                raise SampleOperationConflictError(
                    f"Sample {sample_id} is being deleted; the export was not started."
                )
            if ingest_in_progress(conn, sample_id):
                raise SampleOperationConflictError(
                    f"Sample {sample_id} is still being imported; the export was not started."
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


def _first_delete_blocker(conn: sa.Connection, sample_ids: list[int]) -> tuple[int, str] | None:
    """The first active operation holding any of ``sample_ids``, or ``None``.

    Read inside the caller's transaction. Returns the sample id and how the
    refusal should name what is holding it.
    """
    if not sample_ids:
        return None
    row = conn.execute(
        sa.select(jobs.c.sample_id, jobs.c.job_type)
        .where(jobs.c.sample_id.in_(sample_ids))
        .where(
            sa.or_(
                *(
                    sa.and_(jobs.c.job_type == job_type, jobs.c.status.in_(statuses))
                    for job_type, (statuses, _) in _DELETE_BLOCKING_OPERATIONS.items()
                )
            )
        )
        .order_by(jobs.c.sample_id, jobs.c.job_type)
        .limit(1)
    ).fetchone()
    if row is None:
        return None
    return int(row.sample_id), _DELETE_BLOCKING_OPERATIONS[row.job_type][1]


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
    """Reserve samples for deletion, excluding every operation and other deletes.

    A read-only pre-check cannot close this window: a merge, annotation,
    export, liftover or LAI run could acquire its own lease between the check
    and the first ``unlink``, then reopen a database that deletion is
    concurrently removing -- recreating an empty ``sample_<id>.db`` that the
    next sample allocated that id would inherit (#2314). Both sides therefore
    check-and-insert inside ``BEGIN IMMEDIATE``, so whichever transaction
    commits first is observed by the other.

    Refuses rather than queues, for every operation: a lease can be held for
    minutes, and a destructive request that silently blocks that long reads as
    a hang, then fires against data the user last saw minutes earlier.
    """
    lease_id = str(uuid.uuid4())
    now = datetime.now(UTC)
    ordered_ids = sorted(set(sample_ids))
    try:
        with _immediate_transaction(engine) as conn:
            blocker = _first_delete_blocker(conn, ordered_ids)
            if blocker is not None:
                busy_id, holding = blocker
                raise SampleOperationConflictError(
                    f"Sample {busy_id} is {holding}; retry the delete once that completes."
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
                extra={"lease_id": lease_id, "sample_ids": [int(i) for i in ordered_ids]},
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
                if _deletion_in_progress(conn, sample_id):
                    raise SampleOperationConflictError(
                        f"Sample {sample_id} is being deleted; the merge was not started."
                    )
                if ingest_in_progress(conn, sample_id):
                    raise SampleOperationConflictError(
                        f"Sample {sample_id} is still being imported; the merge was not started."
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
                extra={"lease_id": lease_id, "source_sample_ids": [int(i) for i in ordered_ids]},
            )
        raise
    else:
        try:
            _finish_merge_lease(
                engine, lease_id, status="complete", message=f"{operation} complete"
            )
        except SampleOperationUnavailableError:
            # The merge itself already committed. Raising here would report an
            # error for an operation that succeeded and invite the caller to
            # retry it — worse than a stranded lease, which restart recovery
            # clears (#2329).
            logger.exception(
                "sample_merge_lease_release_failed",
                extra={
                    "lease_id": lease_id,
                    "source_sample_ids": [int(i) for i in ordered_ids],
                },
            )

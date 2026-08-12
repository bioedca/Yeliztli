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
# The one definition of "this job is still doing something". `cancelling` belongs
# here: the worker has not acknowledged the cancel yet, so the job may still be
# writing. Every "is anything in flight?" predicate must use this rather than
# spell out its own tuple -- three of them already said ("pending", "running")
# and would have let a second job start alongside one that was winding down.
ACTIVE_JOB_STATUSES = ("pending", "running", "cancelling")
ACTIVE_ANNOTATION_STATUSES = ACTIVE_JOB_STATUSES
# Exports are synchronous and API-owned, so they never reach `cancelling`.
ACTIVE_EXPORT_STATUSES = ("pending", "running")


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


def active_sample_operation(engine: sa.Engine, sample_id: int) -> str | None:
    """Describe the annotation/export lease holding ``sample_id``, else ``None``.

    A read-only counterpart to the two reservation paths below, for callers that
    must refuse rather than take a lease. Destructive work is the case that
    motivated it: deleting a sample out from under an in-flight operation lets
    that operation reconnect through its already-open engine and recreate the
    per-sample SQLite file it was writing, leaving a stale ``sample_<id>.db`` in
    a namespace the next sample will reuse (#2029).

    Raises :class:`SampleOperationUnavailableError` when the lease registry
    cannot be read, so a caller that must not fail closed can decide for itself.
    """
    try:
        with engine.connect() as conn:
            annotation = conn.execute(
                sa.select(jobs.c.job_id)
                .where(jobs.c.sample_id == sample_id)
                .where(jobs.c.job_type == "annotation")
                .where(jobs.c.status.in_(ACTIVE_ANNOTATION_STATUSES))
                .limit(1)
            ).fetchone()
            if annotation is not None:
                return f"Annotation is in progress for sample {sample_id}"

            export = conn.execute(
                sa.select(jobs.c.job_id)
                .where(jobs.c.sample_id == sample_id)
                .where(jobs.c.job_type == SAMPLE_EXPORT_JOB_TYPE)
                .where(jobs.c.status.in_(ACTIVE_EXPORT_STATUSES))
                .limit(1)
            ).fetchone()
            if export is not None:
                return f"An export is in progress for sample {sample_id}"
    except sa.exc.SQLAlchemyError as exc:
        raise SampleOperationUnavailableError(
            f"Unable to read the operation lease for sample {sample_id}."
        ) from exc
    return None


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

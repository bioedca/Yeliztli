"""Annotation API endpoints (P2-05).

POST /api/annotation/{sample_id}         — Start annotation job (202 Accepted)
GET  /api/annotation/status/{job_id}     — SSE progress stream
POST /api/annotation/cancel/{job_id}     — Cancel a running annotation job
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException

from backend.api.sse import job_progress_stream, sse_response
from backend.db.connection import get_registry
from backend.services.sample_operation_lock import (
    ACTIVE_ANNOTATION_STATUSES,
    SampleOperationUnavailableError,
)
from backend.tasks.huey_tasks import AnnotationEnqueueError, enqueue_annotation_job

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/annotation", tags=["annotation"])


@router.post("/{sample_id}", status_code=202)
async def start_annotation(sample_id: int) -> dict:
    """Start a background annotation job for a sample.

    Returns 202 Accepted with job_id for SSE progress polling.
    Rejects if an annotation is already running for this sample.
    """
    try:
        job_id = enqueue_annotation_job(sample_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except SampleOperationUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except AnnotationEnqueueError as exc:
        logger.exception(
            "annotation_enqueue_failed",
            extra={"sample_id": sample_id},
        )
        raise HTTPException(
            status_code=503,
            detail="Unable to queue annotation; retry the request.",
        ) from exc

    logger.info(
        "annotation_job_enqueued",
        extra={"sample_id": sample_id, "job_id": job_id},
    )

    return {"job_id": job_id, "sample_id": sample_id, "status": "pending"}


@router.get("/active/{sample_id}")
async def get_active_annotation(sample_id: int) -> dict:
    """Return the pending, running, or cancelling annotation job for a sample.

    Returns 200 with job info if an active job exists, or 404 if none.
    Used by the frontend to reconnect to in-progress annotations on page load.
    """
    import sqlalchemy as sa

    from backend.db.tables import jobs

    registry = get_registry()
    with registry.reference_engine.connect() as conn:
        row = conn.execute(
            sa.select(jobs.c.job_id, jobs.c.status, jobs.c.progress_pct, jobs.c.message)
            .where(jobs.c.sample_id == sample_id)
            .where(jobs.c.job_type == "annotation")
            .where(jobs.c.status.in_(ACTIVE_ANNOTATION_STATUSES))
            .order_by(jobs.c.updated_at.desc())
            .limit(1)
        ).fetchone()

    if row is None:
        raise HTTPException(status_code=404, detail="No active annotation job")

    return {
        "job_id": row.job_id,
        "sample_id": sample_id,
        "status": row.status,
        "progress_pct": row.progress_pct,
        "message": row.message,
    }


@router.get("/status/{job_id}")
async def annotation_status(job_id: str):
    """Stream annotation job progress via SSE."""
    registry = get_registry()
    stream = job_progress_stream(registry.reference_engine, job_id)
    return sse_response(stream)


@router.post("/cancel/{job_id}")
async def cancel_annotation(job_id: str) -> dict:
    """Cancel a running annotation job."""
    from datetime import UTC, datetime

    import sqlalchemy as sa

    from backend.db.tables import jobs

    registry = get_registry()

    with registry.reference_engine.begin() as conn:
        result = conn.execute(
            jobs.update()
            .where(jobs.c.job_id == job_id)
            .where(jobs.c.job_type == "annotation")
            .where(jobs.c.status.in_(("pending", "running")))
            .values(
                status="cancelling",
                message="Cancellation requested",
                updated_at=datetime.now(UTC),
            )
        )
        if result.rowcount == 1:
            return {"job_id": job_id, "status": "cancelling"}

        row = conn.execute(
            sa.select(jobs.c.status, jobs.c.job_type).where(jobs.c.job_id == job_id)
        ).fetchone()

        if row is None or row.job_type != "annotation":
            raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

        if row.status in ("complete", "partial", "failed", "cancelled"):
            raise HTTPException(
                status_code=409,
                detail=f"Job {job_id} already in terminal state: {row.status}",
            )

        if row.status == "cancelling":
            return {"job_id": job_id, "status": "cancelling"}
        raise HTTPException(
            status_code=409,
            detail=f"Job {job_id} cannot be cancelled from state: {row.status}",
        )

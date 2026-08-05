"""Server-Sent Events infrastructure for job progress streaming."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator
from dataclasses import dataclass

import sqlalchemy as sa
from starlette.responses import StreamingResponse

from backend.db.tables import jobs


@dataclass
class JobStatus:
    """Snapshot of a job's current state from the jobs table."""

    job_id: str
    status: str  # pending | running | cancelling | complete | failed | cancelled
    progress_pct: float
    message: str
    error: str | None = None


def get_job_progress(engine: sa.Engine, job_id: str) -> JobStatus | None:
    """Poll the jobs table for current status of a single job.

    This is the testable boundary for SSE progress streaming.
    Returns None if the job_id doesn't exist.
    """
    with engine.connect() as conn:
        row = conn.execute(
            sa.select(
                jobs.c.job_id,
                jobs.c.status,
                jobs.c.progress_pct,
                jobs.c.message,
                jobs.c.error,
            ).where(jobs.c.job_id == job_id)
        ).fetchone()

    if row is None:
        return None

    return JobStatus(
        job_id=row.job_id,
        status=row.status,
        progress_pct=row.progress_pct or 0.0,
        message=row.message or "",
        error=row.error,
    )


def _format_sse(event: str, data: dict) -> str:
    """Format a single SSE message."""
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


async def job_progress_stream(
    engine: sa.Engine,
    job_id: str,
    poll_interval: float = 0.5,
) -> AsyncGenerator[str, None]:
    """Async generator that yields SSE events for a job's progress.

    Polls the jobs table at ``poll_interval`` seconds. Terminates when
    the job reaches a terminal state (complete, failed, cancelled).
    """
    terminal_states = {"complete", "failed", "cancelled"}

    while True:
        status = await asyncio.to_thread(get_job_progress, engine, job_id)

        if status is None:
            yield _format_sse("error", {"message": f"Job {job_id} not found"})
            return

        yield _format_sse(
            "progress",
            {
                "job_id": status.job_id,
                "status": status.status,
                "progress_pct": status.progress_pct,
                "message": status.message,
                "error": status.error,
            },
        )

        if status.status in terminal_states:
            return

        await asyncio.sleep(poll_interval)


def sse_response(generator: AsyncGenerator[str, None]) -> StreamingResponse:
    """Wrap an async SSE generator in a proper StreamingResponse."""
    return StreamingResponse(
        generator,
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # Disable nginx buffering
        },
    )

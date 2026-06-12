"""Setup wizard API routes for database management (P1-18).

Endpoints:
    GET    /api/databases                       — List all databases with download status
    POST   /api/databases/download              — Trigger parallel download of selected databases
    GET    /api/databases/progress/{session_id}  — SSE stream with per-database progress events
    GET    /api/databases/sessions               — List all download sessions
    DELETE /api/databases/sessions/{session_id}  — Delete a download session

Health & recovery (DB hardening):
    GET    /api/databases/health                 — Fused per-DB health: state, integrity,
                                                   resumable partials, last error
    POST   /api/databases/resume                 — Resume an interrupted download from its partial
    POST   /api/databases/{db_name}/verify       — Deep integrity check (PRAGMA quick_check)
    POST   /api/databases/{db_name}/clean        — Remove a partial/corrupt artifact for re-fetch
"""

from __future__ import annotations

import asyncio
import shutil
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import sqlalchemy as sa
import structlog
from fastapi import APIRouter, HTTPException
from packaging.version import InvalidVersion, Version
from pydantic import BaseModel
from starlette.responses import StreamingResponse

from backend.api.sse import _format_sse, get_job_progress
from backend.config import Settings, get_settings
from backend.db.build_guard import build_lock, try_acquire_build_lock
from backend.db.connection import get_registry
from backend.db.database_registry import (
    DATABASES,
    DatabaseInfo,
    get_all_databases,
    get_build_fn,
    get_database,
    get_database_status,
    install_committed_bundle,
)
from backend.db.db_health import (
    clean_database_artifacts,
    find_resumable_download,
    get_all_database_health,
    get_database_health,
    validate_database,
)
from backend.db.download_manager import DownloadManager
from backend.db.manifest import get_bundle_info
from backend.db.tables import download_session_jobs, download_sessions, jobs

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/databases", tags=["databases"])


# ── Module-level download executor (singleton) ──────────────────────
# Bounded to 4 workers. Shut down via shutdown_executor() in lifespan.

_download_executor: ThreadPoolExecutor | None = None


def get_download_executor() -> ThreadPoolExecutor:
    """Return the module-level download executor, creating it if needed."""
    global _download_executor  # noqa: PLW0603
    if _download_executor is None:
        _download_executor = ThreadPoolExecutor(max_workers=8)
    return _download_executor


def shutdown_executor() -> None:
    """Shut down the download executor. Called from FastAPI lifespan."""
    global _download_executor  # noqa: PLW0603
    if _download_executor is not None:
        _download_executor.shutdown(wait=False)
        _download_executor = None


# ── Response models ──────────────────────────────────────────────────


class DatabaseStatusResponse(BaseModel):
    """Status of a single database."""

    name: str
    display_name: str
    description: str
    filename: str
    expected_size_bytes: int
    required: bool
    phase: int
    downloaded: bool
    file_size_bytes: int | None
    build_mode: str = "pipeline"


class DatabaseListResponse(BaseModel):
    """Response for GET /api/databases."""

    databases: list[DatabaseStatusResponse]
    total_size_bytes: int
    downloaded_count: int
    total_count: int


class DownloadRequest(BaseModel):
    """Request body for POST /api/databases/download."""

    databases: list[str] | None = None  # None = download all required


class DownloadResponse(BaseModel):
    """Response for POST /api/databases/download."""

    session_id: str
    downloads: list[DownloadJobInfo]


class DownloadJobInfo(BaseModel):
    """Info about a single database download job."""

    db_name: str
    job_id: str


class SessionResponse(BaseModel):
    """Status of a single download session."""

    session_id: str
    status: str
    created_at: str
    databases: list[DownloadJobInfo]


class DatabaseHealthResponse(BaseModel):
    """Fused health record for a single reference database."""

    name: str
    display_name: str
    build_mode: str
    required: bool
    state: str  # not_installed|downloading|building|partial|corrupt|ready|failed
    present: bool
    version: str | None
    downloaded_at: str | None
    file_size_bytes: int | None
    expected_size_bytes: int
    integrity_ok: bool | None
    integrity_detail: str | None
    resumable: bool
    download_id: int | None
    downloaded_bytes: int | None
    total_bytes: int | None
    progress_pct: float | None
    active_job_id: str | None
    last_error: str | None
    can_clean: bool
    can_resume: bool
    can_verify: bool


class DatabaseHealthListResponse(BaseModel):
    """Response for GET /api/databases/health."""

    databases: list[DatabaseHealthResponse]


class ResumeRequest(BaseModel):
    """Request body for POST /api/databases/resume."""

    db_name: str


class VerifyResponse(BaseModel):
    """Response for POST /api/databases/{db_name}/verify."""

    db_name: str
    ok: bool
    detail: str
    depth: str


class CleanResponse(BaseModel):
    """Response for POST /api/databases/{db_name}/clean."""

    db_name: str
    removed: list[str]


# ── Active download sessions (in-memory cache) ──────────────────────
# Maps session_id -> list of (db_name, job_id) pairs for SSE progress.

_active_sessions: dict[str, list[tuple[str, str]]] = {}


# ── GET /api/databases ──────────────────────────────────────────────


@router.get("", response_model=DatabaseListResponse)
async def list_databases() -> DatabaseListResponse:
    """List all reference databases with their download status."""
    settings = get_settings()
    all_dbs = get_all_databases()

    statuses = [get_database_status(db, settings) for db in all_dbs]
    downloaded_count = sum(1 for s in statuses if s["downloaded"])
    total_size = sum(db.expected_size_bytes for db in all_dbs)

    return DatabaseListResponse(
        databases=[DatabaseStatusResponse(**s) for s in statuses],
        total_size_bytes=total_size,
        downloaded_count=downloaded_count,
        total_count=len(all_dbs),
    )


# ── POST /api/databases/download ────────────────────────────────────


@router.post("/download", response_model=DownloadResponse, status_code=202)
async def trigger_download(body: DownloadRequest) -> DownloadResponse:
    """Trigger parallel download of selected (or all required) databases.

    Each database is downloaded in its own thread via the DownloadManager.
    A session_id is returned that can be used with the SSE progress endpoint.
    """
    settings = get_settings()
    registry = get_registry()
    engine = registry.reference_engine

    # Determine which databases to download
    if body.databases is not None:
        db_names = body.databases
        # Validate names
        for name in db_names:
            if name not in DATABASES:
                raise HTTPException(
                    status_code=400,
                    detail=f"Unknown database: {name}. Valid names: {', '.join(DATABASES.keys())}",
                )
    else:
        # Default: all required databases
        db_names = [db.name for db in get_all_databases() if db.required]

    # Deduplicate requested names (preserving order)
    db_names = list(dict.fromkeys(db_names))

    # Skip already-completed, non-buildable, and in-flight databases
    in_flight = _get_in_flight_db_names(engine)
    to_download: list[str] = []
    for name in db_names:
        db_info = get_database(name)
        if db_info is None:
            continue
        if db_info.build_mode == "manual":
            continue
        if name in in_flight:
            continue
        if db_info.build_mode == "bundled":
            # Bundled DBs (vep_bundle, ancestry_pca) install the real latest
            # release via the manifest. (Re)install when there is no recorded
            # version or the installed version trails the manifest — this is how
            # the stale committed vep_bundle fixture gets upgraded to the real
            # 358 MB v2.0.0 union catalog instead of being silently skipped.
            if _bundle_install_needed(db_info, engine):
                to_download.append(name)
            continue
        status = get_database_status(db_info, settings)
        if not status["downloaded"]:
            to_download.append(name)

    if not to_download:
        raise HTTPException(
            status_code=409,
            detail="All requested databases are already downloaded.",
        )

    # Create session and launch parallel downloads/builds
    session_id = f"dbdl-{uuid.uuid4().hex[:12]}"
    download_jobs: list[DownloadJobInfo] = []
    session_entries: list[tuple[str, str]] = []

    executor = get_download_executor()

    for name in to_download:
        db_info = get_database(name)
        if db_info is None:
            continue

        job_id = f"dbdl-{name}-{uuid.uuid4().hex[:8]}"

        # Pre-create job record so SSE can find it immediately
        _create_job_record(engine, job_id, name)

        download_jobs.append(DownloadJobInfo(db_name=name, job_id=job_id))
        session_entries.append((name, job_id))

        if db_info.build_mode == "pipeline":
            # Build from upstream sources
            executor.submit(
                _run_build,
                db_info=db_info,
                job_id=job_id,
                engine=engine,
                settings=settings,
            )
        elif db_info.build_mode == "bundled":
            # Pre-built bundle (vep_bundle, ancestry_pca): manifest download of
            # the real latest release, with the committed fixture as fallback.
            executor.submit(
                _run_bundle_install,
                db_info=db_info,
                job_id=job_id,
                engine=engine,
                settings=settings,
            )
        else:
            # HTTP download (encode_ccres, lai_bundle)
            dm = DownloadManager(engine, settings.downloads_dir)
            executor.submit(
                _run_download,
                dm=dm,
                db_info=db_info,
                job_id=job_id,
                engine=engine,
                settings=settings,
            )

    # Persist session in-memory and in database
    _active_sessions[session_id] = session_entries
    _persist_session(engine, session_id, session_entries)

    logger.info(
        "database_download_started",
        session_id=session_id,
        databases=to_download,
    )

    return DownloadResponse(
        session_id=session_id,
        downloads=download_jobs,
    )


# ── GET /api/databases/progress ──────────────────────────────────────


@router.get("/progress/{session_id}")
async def download_progress(session_id: str) -> StreamingResponse:
    """SSE stream reporting per-database download progress.

    Emits ``progress`` events with per-database status until all downloads
    in the session reach a terminal state (complete/failed).
    """
    # Try in-memory first, fall back to DB lookup
    entries = _active_sessions.get(session_id)
    if entries is None:
        entries = _load_session_jobs(get_registry().reference_engine, session_id)
        if entries is not None:
            _active_sessions[session_id] = entries

    if entries is None:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")

    engine = get_registry().reference_engine

    async def event_stream():
        terminal_states = {"complete", "failed", "cancelled"}
        poll_interval = 0.5

        while True:
            all_terminal = True
            db_statuses: list[dict[str, Any]] = []

            for db_name, job_id in entries:
                status = await asyncio.to_thread(get_job_progress, engine, job_id)
                if status is None:
                    db_statuses.append(
                        {
                            "db_name": db_name,
                            "job_id": job_id,
                            "status": "unknown",
                            "progress_pct": 0.0,
                            "message": "Job not found",
                            "error": None,
                        }
                    )
                    all_terminal = False
                else:
                    db_statuses.append(
                        {
                            "db_name": db_name,
                            "job_id": status.job_id,
                            "status": status.status,
                            "progress_pct": status.progress_pct,
                            "message": status.message,
                            "error": status.error,
                        }
                    )
                    if status.status not in terminal_states:
                        all_terminal = False

            yield _format_sse(
                "progress",
                {
                    "session_id": session_id,
                    "databases": db_statuses,
                },
            )

            if all_terminal:
                # Determine final status based on job outcomes
                _active_sessions.pop(session_id, None)
                has_failures = any(s.get("status") == "failed" for s in db_statuses)
                final_status = "failed" if has_failures else "complete"
                _update_session_status(engine, session_id, final_status)
                return

            await asyncio.sleep(poll_interval)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ── GET /api/databases/sessions ───────────────────────────────────────


@router.get("/sessions", response_model=list[SessionResponse])
async def list_sessions() -> list[SessionResponse]:
    """List all download sessions with their status."""
    engine = get_registry().reference_engine
    with engine.connect() as conn:
        rows = conn.execute(
            sa.select(
                download_sessions.c.session_id,
                download_sessions.c.status,
                download_sessions.c.created_at,
            ).order_by(download_sessions.c.created_at.desc())
        ).fetchall()

    result = []
    for row in rows:
        job_entries = _load_session_jobs(engine, row.session_id) or []
        result.append(
            SessionResponse(
                session_id=row.session_id,
                status=row.status,
                created_at=row.created_at.isoformat() if row.created_at else "",
                databases=[DownloadJobInfo(db_name=name, job_id=jid) for name, jid in job_entries],
            )
        )
    return result


# ── DELETE /api/databases/sessions/{session_id} ───────────────────────


@router.delete("/sessions/{session_id}", status_code=204)
async def delete_session(session_id: str) -> None:
    """Delete a download session record.

    Only sessions in terminal states (complete, failed, interrupted, stale)
    can be deleted. In-progress sessions must finish or be interrupted first.
    """
    engine = get_registry().reference_engine
    with engine.connect() as conn:
        row = conn.execute(
            sa.select(download_sessions.c.status).where(
                download_sessions.c.session_id == session_id
            )
        ).fetchone()

    if row is None:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")

    if row.status == "in_progress":
        raise HTTPException(
            status_code=409,
            detail="Cannot delete an in-progress session. "
            "Wait for it to finish or restart the server.",
        )

    with engine.begin() as conn:
        conn.execute(
            download_session_jobs.delete().where(download_session_jobs.c.session_id == session_id)
        )
        conn.execute(
            download_sessions.delete().where(download_sessions.c.session_id == session_id)
        )

    _active_sessions.pop(session_id, None)
    logger.info("download_session_deleted", session_id=session_id)


# ── Session lifecycle (startup cleanup) ──────────────────────────────


def cleanup_interrupted_sessions(engine: sa.Engine) -> int:
    """Mark in-progress sessions as interrupted/stale on server startup.

    Called from the FastAPI lifespan hook. Returns the number of sessions
    that were marked as interrupted or stale.
    """
    now = datetime.now(UTC)
    one_hour_ago = now - timedelta(hours=1)

    count = 0
    with engine.begin() as conn:
        # Mark recent in-progress sessions as interrupted
        result = conn.execute(
            download_sessions.update()
            .where(
                download_sessions.c.status == "in_progress",
                download_sessions.c.created_at > one_hour_ago,
            )
            .values(status="interrupted", updated_at=now)
        )
        count += result.rowcount

        # Mark old in-progress sessions (> 1 hour) as stale
        result = conn.execute(
            download_sessions.update()
            .where(
                download_sessions.c.status == "in_progress",
                download_sessions.c.created_at <= one_hour_ago,
            )
            .values(status="stale", updated_at=now)
        )
        count += result.rowcount

    if count > 0:
        logger.info("download_sessions_cleaned_up", count=count)

    return count


# ── Internal helpers ─────────────────────────────────────────────────


def _persist_session(
    engine: sa.Engine,
    session_id: str,
    entries: list[tuple[str, str]],
) -> None:
    """Persist a download session and its jobs to the database."""
    now = datetime.now(UTC)
    with engine.begin() as conn:
        conn.execute(
            download_sessions.insert().values(
                session_id=session_id,
                status="in_progress",
                created_at=now,
                updated_at=now,
            )
        )
        for db_name, job_id in entries:
            conn.execute(
                download_session_jobs.insert().values(
                    session_id=session_id,
                    db_name=db_name,
                    job_id=job_id,
                )
            )


def _load_session_jobs(engine: sa.Engine, session_id: str) -> list[tuple[str, str]] | None:
    """Load session job entries from the database. Returns None if not found."""
    with engine.connect() as conn:
        # Check session exists
        session = conn.execute(
            sa.select(download_sessions.c.session_id).where(
                download_sessions.c.session_id == session_id
            )
        ).fetchone()
        if session is None:
            return None

        rows = conn.execute(
            sa.select(download_session_jobs.c.db_name, download_session_jobs.c.job_id).where(
                download_session_jobs.c.session_id == session_id
            )
        ).fetchall()

    return [(row.db_name, row.job_id) for row in rows]


def _update_session_status(engine: sa.Engine, session_id: str, status: str) -> None:
    """Update session status in the database."""
    with engine.begin() as conn:
        conn.execute(
            download_sessions.update()
            .where(download_sessions.c.session_id == session_id)
            .values(status=status, updated_at=datetime.now(UTC))
        )


def _get_in_flight_db_names(engine: sa.Engine) -> set[str]:
    """Return database names that have a pending or running download/build job."""
    with engine.connect() as conn:
        rows = conn.execute(
            sa.select(download_session_jobs.c.db_name)
            .join(jobs, download_session_jobs.c.job_id == jobs.c.job_id)
            .where(jobs.c.status.in_(("pending", "running")))
        ).fetchall()
    return {row.db_name for row in rows}


def _create_job_record(engine: sa.Engine, job_id: str, db_name: str) -> None:
    """Create a job record for SSE tracking before download starts."""
    now = datetime.now(UTC)
    with engine.begin() as conn:
        conn.execute(
            jobs.insert().values(
                job_id=job_id,
                sample_id=None,
                job_type="database_download",
                status="pending",
                progress_pct=0.0,
                message=f"Queued download: {db_name}",
                created_at=now,
                updated_at=now,
            )
        )


def _run_build(
    *,
    db_info: DatabaseInfo,
    job_id: str,
    engine: sa.Engine,
    settings: Settings,
) -> None:
    """Build a database, serialized per-DB so two builds can't race the same file.

    A duplicate download request (the in-flight dedup has a check-then-act gap)
    or a build racing an auto-update would otherwise open two writers on the
    same SQLite file and deadlock on the WAL write lock ("database is locked").
    :func:`build_lock` makes same-DB builds run one at a time (different DBs
    still build in parallel); once we hold the lock we re-check whether a
    concurrent build already finished this DB to avoid a redundant rebuild.
    """
    with build_lock(db_info.name):
        if get_database_status(db_info, settings)["downloaded"]:
            _update_job(
                engine,
                job_id,
                status="complete",
                progress_pct=100.0,
                message=f"{db_info.display_name} already downloaded",
            )
            logger.info(
                "database_build_skipped_already_present",
                db_name=db_info.name,
                job_id=job_id,
            )
            return
        _execute_build(db_info=db_info, job_id=job_id, engine=engine, settings=settings)


def _execute_build(
    *,
    db_info: DatabaseInfo,
    job_id: str,
    engine: sa.Engine,
    settings: Settings,
) -> None:
    """Execute a database build pipeline in a background thread."""
    try:
        _update_job(
            engine,
            job_id,
            status="running",
            message=f"Building {db_info.display_name} from upstream source...",
        )

        # Hardening: a build killed mid-write can leave a corrupt/truncated
        # standalone SQLite with no version stamp. Re-opening it for the rebuild
        # would fail outright (malformed image) or splice onto stale rows. Drop
        # a corrupt partial first so the build starts clean. reference.db is
        # shared across DBs and is never removed here.
        if db_info.target_db == "standalone" and db_info.build_mode == "pipeline":
            dest = db_info.dest_path(settings)
            if dest.exists() and not validate_database(db_info.name, settings).ok:
                for stale in (dest, Path(f"{dest}-wal"), Path(f"{dest}-shm")):
                    try:
                        stale.unlink(missing_ok=True)
                    except OSError as unlink_exc:
                        logger.warning(
                            "stale_partial_unlink_failed",
                            path=str(stale),
                            error=str(unlink_exc),
                        )
                logger.info(
                    "corrupt_standalone_partial_removed",
                    db_name=db_info.name,
                    path=str(dest),
                )

        build_fn = get_build_fn(db_info.name)
        if build_fn is None:
            raise ValueError(f"No build function registered for {db_info.name}")

        # Determine which engine to pass
        if db_info.target_db == "reference":
            target_engine = engine
        else:
            registry = get_registry()
            if db_info.name == "gnomad":
                target_engine = registry.gnomad_engine
            elif db_info.name == "dbnsfp":
                target_engine = registry.dbnsfp_engine
            elif db_info.name == "alphamissense":
                target_engine = registry.alphamissense_engine
            else:
                target_engine = engine

        # Throttle progress writes to at most once per 2 seconds per callback
        _last_dl_update = 0.0
        _last_parse_update = 0.0
        _THROTTLE_INTERVAL = 2.0

        # Download progress callback -> 0-90% of job progress
        def on_download_progress(downloaded: int, total: int | None) -> None:
            nonlocal _last_dl_update
            now = time.monotonic()
            if now - _last_dl_update < _THROTTLE_INTERVAL:
                return
            if total and total > 0:
                pct = min(90.0, (downloaded / total) * 90.0)
                try:
                    _update_job(
                        engine,
                        job_id,
                        status="running",
                        progress_pct=pct,
                        message=f"Downloading {db_info.display_name}... {pct:.0f}%",
                    )
                    _last_dl_update = now
                except sa.exc.OperationalError:
                    _last_dl_update = now  # back off even on failure

        # Parse progress callback -> 90-99% of job progress
        def on_parse_progress(variants_parsed: int) -> None:
            nonlocal _last_parse_update
            now = time.monotonic()
            if now - _last_parse_update < _THROTTLE_INTERVAL:
                return
            pct = min(99.0, 90.0 + min(9.0, variants_parsed / 100_000))
            try:
                _update_job(
                    engine,
                    job_id,
                    status="running",
                    progress_pct=pct,
                    message=f"Importing {db_info.display_name}... {variants_parsed:,} records",
                )
                _last_parse_update = now
            except sa.exc.OperationalError:
                _last_parse_update = now

        # Call the build function — signatures vary slightly
        if db_info.name in ("gnomad", "dbnsfp"):
            build_fn(
                target_engine,
                settings.downloads_dir,
                download_progress=on_download_progress,
                parse_progress=on_parse_progress,
                reference_engine=engine,
            )
        elif db_info.name == "alphamissense":
            build_fn(
                target_engine,
                settings.downloads_dir,
                download_progress=on_download_progress,
                parse_progress=on_parse_progress,
                reference_engine=engine,
            )
        elif db_info.name == "mondo_hpo":
            # mondo_hpo does not accept parse_progress
            build_fn(target_engine, settings.downloads_dir, download_progress=on_download_progress)
        else:
            build_fn(
                target_engine,
                settings.downloads_dir,
                download_progress=on_download_progress,
                parse_progress=on_parse_progress,
            )

        _update_job(
            engine,
            job_id,
            status="complete",
            progress_pct=100.0,
            message=f"{db_info.display_name} ready",
        )

        logger.info(
            "database_build_complete",
            db_name=db_info.name,
            job_id=job_id,
        )

    except Exception as exc:
        error_msg = f"{type(exc).__name__}: {exc}"
        _update_job(
            engine,
            job_id,
            status="failed",
            progress_pct=0.0,
            error=error_msg,
        )
        logger.exception(
            "database_build_failed",
            db_name=db_info.name,
            job_id=job_id,
            error=error_msg,
        )


def _apply_manifest_overrides(db_info: DatabaseInfo) -> DatabaseInfo:
    """Return ``db_info`` with URL/sha256/size from the bundle manifest.

    Only applies when ``db_info.build_mode == "download"`` and
    ``db_info.sha256 is None`` — i.e. the registry has no integrity hash to
    enforce. The manifest is the single source of truth for that case.

    Manifest unreachable (``get_bundle_info`` returns ``None``) or no entry
    for this DB → return ``db_info`` unchanged so registry defaults survive.
    Empty ``url`` in the manifest also keeps the registry URL.
    """
    if db_info.build_mode != "download" or db_info.sha256 is not None:
        return db_info

    entry = get_bundle_info(db_info.name)
    if entry is None:
        return db_info

    return replace(
        db_info,
        url=entry.url or db_info.url,
        sha256=entry.sha256,
        expected_size_bytes=entry.size_bytes,
    )


def _run_download(
    *,
    dm: DownloadManager,
    db_info: DatabaseInfo,
    job_id: str,
    engine: sa.Engine,
    settings: Settings,
) -> None:
    """Execute a single database download in a background thread.

    Uses the DownloadManager for resumable HTTP downloads. On success,
    moves the file from downloads_dir to its final location in data_dir.
    """
    try:
        db_info = _apply_manifest_overrides(db_info)

        # Update job to running
        msg = f"Downloading {db_info.display_name}..."
        _update_job(engine, job_id, status="running", message=msg)

        result = dm.start(
            url=db_info.url,
            filename=db_info.filename,
            expected_sha256=db_info.sha256,
        )

        if result.error:
            _update_job(
                engine,
                job_id,
                status="failed",
                progress_pct=0.0,
                error=result.error,
            )
            return

        # Move from downloads dir to final destination
        final_dest = db_info.dest_path(settings)
        final_dest.parent.mkdir(parents=True, exist_ok=True)

        if db_info.post_download is not None:
            # Transform raw download into the final database file
            _update_job(
                engine,
                job_id,
                status="running",
                progress_pct=99.0,
                message=f"Transforming {db_info.display_name}...",
            )
            db_info.post_download(result.dest_path, final_dest)
        elif result.dest_path != final_dest:
            shutil.move(str(result.dest_path), str(final_dest))

        _update_job(
            engine,
            job_id,
            status="complete",
            progress_pct=100.0,
            message=f"{db_info.display_name} download complete",
        )

        logger.info(
            "database_download_complete",
            db_name=db_info.name,
            job_id=job_id,
            dest=str(final_dest),
        )

    except Exception as exc:
        error_msg = f"{type(exc).__name__}: {exc}"
        _update_job(
            engine,
            job_id,
            status="failed",
            progress_pct=0.0,
            error=error_msg,
        )
        logger.exception(
            "database_download_failed",
            db_name=db_info.name,
            job_id=job_id,
            error=error_msg,
        )


def _bundle_install_needed(db_info: DatabaseInfo, engine: sa.Engine) -> bool:
    """Whether a bundled DB should be (re)installed from the manifest.

    ``True`` when there is no ``database_versions`` row, or the installed
    version is strictly below the manifest version (semver-compared, falling
    back to string inequality). This upgrades a stale committed-fixture
    auto-copy — e.g. ``vep_bundle`` v1.0.0 — to the real manifest release
    (v2.0.0) while leaving an already-current bundle (``ancestry_pca`` v1.0)
    untouched so a setup re-run does not re-download it.
    """
    from backend.db.update_manager import get_current_version

    installed = get_current_version(engine, db_info.name)
    if installed is None:
        return True
    entry = get_bundle_info(db_info.name)
    if entry is None or not entry.version:
        return False
    try:
        return Version(installed.lstrip("v")) < Version(entry.version.lstrip("v"))
    except InvalidVersion:
        return installed != entry.version


def _run_bundle_install(
    *,
    db_info: DatabaseInfo,
    job_id: str,
    engine: sa.Engine,
    settings: Settings,
) -> None:
    """Install a pre-built bundle in a background thread.

    Prefers the manifest download of the real latest release (via the dedicated
    ``run_<bundle>_update`` runner, which verifies SHA-256 and records the
    version stamp). Falls back to the committed fixture when the release is
    unreachable or the manifest carries no URL (the out-of-band ``ancestry_pca``
    bundle), still recording an honest version row either way.
    """
    try:
        _update_job(
            engine,
            job_id,
            status="running",
            message=f"Downloading {db_info.display_name}...",
        )

        from backend.db.update_manager import (
            run_ancestry_pca_bundle_update,
            run_gnomad_bundle_update,
            run_vep_bundle_update,
        )

        runners = {
            "vep_bundle": run_vep_bundle_update,
            "ancestry_pca": run_ancestry_pca_bundle_update,
            "gnomad": run_gnomad_bundle_update,
        }
        runner = runners.get(db_info.name)
        result = runner(settings, timeout=3600.0) if runner is not None else None

        if result is not None:
            _update_job(
                engine,
                job_id,
                status="complete",
                progress_pct=100.0,
                message=f"{db_info.display_name} download complete",
            )
            logger.info("bundle_install_complete", db_name=db_info.name, source="manifest")
            return

        # Remote unavailable or no manifest URL — fall back to the committed copy.
        if install_committed_bundle(db_info, settings):
            _update_job(
                engine,
                job_id,
                status="complete",
                progress_pct=100.0,
                message=f"{db_info.display_name} installed (bundled copy)",
            )
            logger.info("bundle_install_complete", db_name=db_info.name, source="bundled")
            return

        _update_job(
            engine,
            job_id,
            status="failed",
            progress_pct=0.0,
            error=f"No download source available for {db_info.name}",
        )

    except Exception as exc:
        error_msg = f"{type(exc).__name__}: {exc}"
        _update_job(engine, job_id, status="failed", progress_pct=0.0, error=error_msg)
        logger.exception(
            "bundle_install_failed",
            db_name=db_info.name,
            job_id=job_id,
            error=error_msg,
        )


def _update_job(
    engine: sa.Engine,
    job_id: str,
    *,
    status: str,
    progress_pct: float = 0.0,
    message: str = "",
    error: str | None = None,
    _retries: int = 5,
) -> None:
    """Update a job record with retry on SQLite contention."""
    for attempt in range(_retries):
        try:
            with engine.begin() as conn:
                conn.execute(
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
            return
        except sa.exc.OperationalError:
            if attempt < _retries - 1:
                time.sleep(0.1 * (2**attempt))
            else:
                raise


# ── GET /api/databases/health ────────────────────────────────────────


@router.get("/health", response_model=DatabaseHealthListResponse)
async def database_health() -> DatabaseHealthListResponse:
    """Fused health for every reference database (100% observability surface).

    Reports, per database: derived ``state`` (not_installed / downloading /
    building / partial / corrupt / ready / failed), on-disk presence + size,
    version stamp, a fast structural integrity verdict (is it readable by the
    annotation code?), any resumable partial download, the active job id, and
    the last error. The structural integrity probe is cheap (open + a
    ``LIMIT 1`` per consumer table); the heavier ``PRAGMA quick_check`` is
    reserved for the explicit ``/verify`` action.
    """
    settings = get_settings()
    engine = get_registry().reference_engine
    records = await asyncio.to_thread(get_all_database_health, settings, engine)
    return DatabaseHealthListResponse(
        databases=[DatabaseHealthResponse(**asdict(r)) for r in records]
    )


# ── POST /api/databases/resume ───────────────────────────────────────


@router.post("/resume", response_model=DownloadResponse, status_code=202)
async def resume_download(body: ResumeRequest) -> DownloadResponse:
    """Resume an interrupted ``download``-mode transfer from its partial.

    Re-runs the same download path the wizard uses; :class:`DownloadManager`
    detects the checkpointed ``.tmp`` and continues via an HTTP ``Range``
    request instead of restarting. Returns a ``session_id`` so the existing
    ``/progress/{session_id}`` SSE stream tracks it identically to a fresh
    download.
    """
    settings = get_settings()
    engine = get_registry().reference_engine

    db_info = get_database(body.db_name)
    if db_info is None:
        raise HTTPException(status_code=404, detail=f"Unknown database: {body.db_name}")

    resumable = find_resumable_download(engine, db_info, settings)
    if resumable is None:
        raise HTTPException(
            status_code=409,
            detail=f"No resumable partial download for '{body.db_name}'.",
        )

    if body.db_name in _get_in_flight_db_names(engine):
        raise HTTPException(
            status_code=409,
            detail=f"A download/build for '{body.db_name}' is already in progress.",
        )

    session_id = f"dbdl-{uuid.uuid4().hex[:12]}"
    job_id = f"dbdl-{body.db_name}-{uuid.uuid4().hex[:8]}"
    _create_job_record(engine, job_id, body.db_name)

    executor = get_download_executor()
    dm = DownloadManager(engine, settings.downloads_dir)
    executor.submit(
        _run_download,
        dm=dm,
        db_info=db_info,
        job_id=job_id,
        engine=engine,
        settings=settings,
    )

    session_entries = [(body.db_name, job_id)]
    _active_sessions[session_id] = session_entries
    _persist_session(engine, session_id, session_entries)

    logger.info(
        "database_download_resumed",
        session_id=session_id,
        db_name=body.db_name,
        offset=resumable["downloaded_bytes"],
    )

    return DownloadResponse(
        session_id=session_id,
        downloads=[DownloadJobInfo(db_name=body.db_name, job_id=job_id)],
    )


# ── POST /api/databases/{db_name}/verify ─────────────────────────────


@router.post("/{db_name}/verify", response_model=VerifyResponse)
async def verify_database(db_name: str) -> VerifyResponse:
    """Run a deep integrity check (``PRAGMA quick_check`` for SQLite).

    Slower than the structural probe in ``/health`` — it scans the whole file —
    so it is an explicit user action. Confirms the artifact is not just present
    but structurally sound and readable by the annotation pipeline.
    """
    if get_database(db_name) is None:
        raise HTTPException(status_code=404, detail=f"Unknown database: {db_name}")

    settings = get_settings()
    engine = get_registry().reference_engine
    result = await asyncio.to_thread(
        validate_database, db_name, settings, engine=engine, deep=True
    )
    return VerifyResponse(db_name=db_name, ok=result.ok, detail=result.detail, depth=result.depth)


# ── POST /api/databases/{db_name}/clean ──────────────────────────────


@router.post("/{db_name}/clean", response_model=CleanResponse)
async def clean_database(db_name: str) -> CleanResponse:
    """Remove a partial/corrupt artifact so a clean re-download/build can run.

    Refuses to act while a download/build is active (409) or when the database
    is healthy (409) — a clean is only for recovering a broken state.

    The destructive work runs while holding the per-DB :func:`build_lock`, so a
    clean can never race a concurrent build of the same database (setup wizard
    OR update manager), and the health re-check happens *inside* the lock to
    close the check-then-delete TOCTOU.
    """
    db_info = get_database(db_name)
    if db_info is None:
        raise HTTPException(status_code=404, detail=f"Unknown database: {db_name}")

    settings = get_settings()
    engine = get_registry().reference_engine

    result = await asyncio.to_thread(_clean_under_lock, db_info, settings, engine)
    if result == "locked":
        raise HTTPException(
            status_code=409,
            detail=f"Cannot clean '{db_name}' while a build is in progress.",
        )
    if result == "active":
        raise HTTPException(
            status_code=409,
            detail=f"Cannot clean '{db_name}' while a download/build is active.",
        )
    if result == "ready":
        raise HTTPException(
            status_code=409,
            detail=f"'{db_name}' is healthy; nothing to clean.",
        )
    return CleanResponse(db_name=result["db_name"], removed=result["removed"])


def _clean_under_lock(
    db_info: DatabaseInfo,
    settings: Settings,
    engine: sa.Engine,
) -> dict | str:
    """Acquire the per-DB build lock, re-check health, then clean atomically.

    Returns the clean result dict on success, or a sentinel string
    (``"locked"`` / ``"active"`` / ``"ready"``) the caller maps to a 409.
    """
    with try_acquire_build_lock(db_info.name) as acquired:
        if not acquired:
            return "locked"
        health = get_database_health(db_info, settings, engine)
        if health.state in ("downloading", "building"):
            return "active"
        if health.state == "ready":
            return "ready"
        return clean_database_artifacts(db_info, settings, engine)

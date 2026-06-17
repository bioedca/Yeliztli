"""Admin panel API routes (P4-21b).

Provides endpoints for the Settings > System Health page:
  - Log explorer (paginated, filterable by level/component/time range)
  - Database stats (file sizes, row counts, last updated)
  - Disk usage (data dir, samples, reference DBs)
  - System status (uptime, active jobs, version)
"""

from __future__ import annotations

import shutil
import time
from datetime import datetime
from pathlib import Path

import sqlalchemy as sa
from fastapi import APIRouter, Query
from pydantic import BaseModel
from sqlalchemy.pool import NullPool

from backend.config import get_settings
from backend.db.connection import get_registry
from backend.db.database_registry import DATABASES, DatabaseInfo
from backend.db.db_health import (
    _REFERENCE_TABLE_SPEC,
    _STANDALONE_TABLE_SPEC,
    _artifact_path,
    _file_size,
    validate_database,
)
from backend.db.tables import (
    database_versions,
    jobs,
    log_entries,
    samples,
)

router = APIRouter(prefix="/admin", tags=["admin"])

# Track app start time for uptime calculation
_APP_START_TIME = time.time()


def _format_ts(val: object) -> str | None:
    """Format a timestamp value to ISO string."""
    if isinstance(val, datetime):
        return val.isoformat()
    return str(val) if val is not None else None


# ── Response models ──────────────────────────────────────────────────


class LogEntry(BaseModel):
    id: int
    timestamp: str | None
    level: str
    logger: str | None
    message: str | None
    event_data: str | None


class LogResponse(BaseModel):
    entries: list[LogEntry]
    total: int
    page: int
    page_size: int
    has_more: bool


class DatabaseStat(BaseModel):
    name: str
    display_name: str
    file_path: str | None
    file_size_bytes: int | None
    exists: bool
    row_count: int | None
    last_updated: str | None
    version: str | None


class SampleStat(BaseModel):
    sample_id: int
    name: str
    db_path: str
    file_size_bytes: int | None
    exists: bool


class DiskUsage(BaseModel):
    data_dir: str
    total_bytes: int
    free_bytes: int
    used_bytes: int
    reference_dbs_bytes: int
    sample_dbs_bytes: int
    logs_bytes: int
    other_bytes: int


class ActiveJob(BaseModel):
    job_id: str
    job_type: str
    status: str
    progress_pct: float | None
    message: str | None
    created_at: str | None


class SystemStatus(BaseModel):
    version: str
    uptime_seconds: float
    data_dir: str
    active_jobs: list[ActiveJob]
    total_samples: int
    auth_enabled: bool
    log_level: str


# ── Log explorer ─────────────────────────────────────────────────────


@router.get("/logs", response_model=LogResponse)
def get_logs(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    level: str | None = Query(
        None, description="Filter by log level (INFO, WARNING, ERROR, etc.)"
    ),
    component: str | None = Query(None, description="Filter by logger name (substring match)"),
    since: str | None = Query(None, description="ISO datetime lower bound"),
    until: str | None = Query(None, description="ISO datetime upper bound"),
    search: str | None = Query(None, description="Search in message text"),
) -> LogResponse:
    """Paginated log explorer with faceted filtering."""
    registry = get_registry()
    engine = registry.reference_engine

    with engine.connect() as conn:
        # Build WHERE clause
        conditions: list[sa.ColumnElement] = []
        if level:
            conditions.append(log_entries.c.level == level.upper())
        if component:
            conditions.append(log_entries.c.logger.contains(component))
        if since:
            conditions.append(log_entries.c.timestamp >= since)
        if until:
            conditions.append(log_entries.c.timestamp <= until)
        if search:
            conditions.append(log_entries.c.message.contains(search))

        where = sa.and_(*conditions) if conditions else sa.true()

        # Total count
        count_q = sa.select(sa.func.count()).select_from(log_entries).where(where)
        total = conn.execute(count_q).scalar() or 0

        # Paginated query (newest first)
        offset = (page - 1) * page_size
        q = (
            sa.select(log_entries)
            .where(where)
            .order_by(log_entries.c.id.desc())
            .limit(page_size)
            .offset(offset)
        )
        rows = conn.execute(q).mappings().all()

    entries = [
        LogEntry(
            id=r["id"],
            timestamp=_format_ts(r["timestamp"]),
            level=r["level"],
            logger=r["logger"],
            message=r["message"],
            event_data=r["event_data"],
        )
        for r in rows
    ]

    return LogResponse(
        entries=entries,
        total=total,
        page=page,
        page_size=page_size,
        has_more=(offset + page_size) < total,
    )


# ── Database stats ───────────────────────────────────────────────────


def _get_file_size(path: Path) -> int | None:
    """Return file size in bytes, or None if file doesn't exist."""
    try:
        return path.stat().st_size if path.exists() else None
    except OSError:
        return None


def _count_rows(engine: sa.Engine, table_name: str) -> int | None:
    """Count rows in a table, returning None on error."""
    try:
        with engine.connect() as conn:
            result = conn.execute(sa.text(f'SELECT COUNT(*) FROM "{table_name}"'))  # noqa: S608
            return result.scalar()
    except Exception:
        return None


def _primary_data_table(db_info: DatabaseInfo) -> str | None:
    """Return the data-bearing table whose row count represents this DB, or None.

    Reuses db_health's per-DB table specs — the single source of truth for which
    tables each consumer reads — picking the required (data-bearing) table, then
    falling back to the first listed. Databases with no SQL table (the LAI
    directory bundle, the ancestry_pca npz) return ``None``. Centralizing here
    avoids the drift the previous local map had (it omitted cpic/clingen/etc.).
    """
    spec = (
        _REFERENCE_TABLE_SPEC.get(db_info.name)
        if db_info.target_db == "reference"
        else _STANDALONE_TABLE_SPEC.get(db_info.name)
    )
    if not spec:
        return None
    for table, must_have_rows in spec:
        if must_have_rows:
            return table
    return spec[0][0]


@router.get("/db-stats", response_model=list[DatabaseStat])
def get_db_stats() -> list[DatabaseStat]:
    """Return stats for all reference databases.

    Presence (``exists``) is decided by :func:`backend.db.db_health.validate_database`,
    the same readability probe the Database Health panel uses, so the answer is
    correct for every artifact class: reference-resident DBs (clinvar / cpic /
    clingen — data lives in reference.db, no standalone ``<name>.db`` file ever
    exists), the LAI directory bundle (the tarball is deleted post-extract), the
    ancestry_pca npz, and standalone SQLite files. The previous
    ``dest_path().exists()`` check reported the first three as "Missing" even
    when installed.
    """
    settings = get_settings()
    registry = get_registry()

    # Gather version + recorded artifact size from the database_versions table.
    versions: dict[str, tuple[str | None, str | None, int | None]] = {}
    try:
        with registry.reference_engine.connect() as conn:
            rows = conn.execute(sa.select(database_versions)).mappings().all()
            for r in rows:
                downloaded_at = r["downloaded_at"]
                if isinstance(downloaded_at, datetime):
                    downloaded_at = downloaded_at.isoformat()
                elif downloaded_at is not None:
                    downloaded_at = str(downloaded_at)
                versions[r["db_name"]] = (r["version"], downloaded_at, r["file_size_bytes"])
    except Exception:
        pass

    stats: list[DatabaseStat] = []

    # Reference.db
    ref_path = settings.reference_db_path
    ref_size = _get_file_size(ref_path)
    stats.append(
        DatabaseStat(
            name="reference",
            display_name="Reference DB",
            file_path=str(ref_path),
            file_size_bytes=ref_size,
            exists=ref_path.exists(),
            row_count=None,  # Multiple tables, skip
            last_updated=None,
            version=None,
        )
    )

    for db_info in DATABASES.values():
        db_name = db_info.name
        artifact_path = _artifact_path(db_info, settings)
        version, downloaded_at, stamp_size = versions.get(db_name, (None, None, None))

        # Readable-by-its-consumer presence, correct for reference-resident /
        # directory / npz / standalone artifacts alike.
        exists = validate_database(db_name, settings, engine=registry.reference_engine).ok

        # Size: reference-resident DBs share reference.db, so a per-DB on-disk size
        # is not meaningful — use the recorded artifact size from the version
        # stamp. Standalone files and the LAI directory report their real size.
        if exists:
            file_size = (
                stamp_size if db_info.target_db == "reference" else _file_size(db_info, settings)
            )
        else:
            file_size = db_info.expected_size_bytes

        # Row count from the data-bearing table, counted against the engine that
        # actually holds it (reference.db for reference-resident DBs).
        row_count = None
        if exists:
            table = _primary_data_table(db_info)
            if table is not None:
                if db_info.target_db == "reference":
                    row_count = _count_rows(registry.reference_engine, table)
                else:
                    tmp_engine = sa.create_engine(f"sqlite:///{artifact_path}", poolclass=NullPool)
                    try:
                        row_count = _count_rows(tmp_engine, table)
                    finally:
                        tmp_engine.dispose()

        stats.append(
            DatabaseStat(
                name=db_name,
                display_name=db_info.display_name,
                file_path=str(artifact_path),
                file_size_bytes=file_size,
                exists=exists,
                row_count=row_count,
                last_updated=downloaded_at,
                version=version,
            )
        )

    return stats


# ── Sample stats ─────────────────────────────────────────────────────


@router.get("/sample-stats", response_model=list[SampleStat])
def get_sample_stats() -> list[SampleStat]:
    """Return file stats for all sample databases."""
    settings = get_settings()
    registry = get_registry()

    result: list[SampleStat] = []
    try:
        with registry.reference_engine.connect() as conn:
            rows = conn.execute(sa.select(samples)).mappings().all()
            for r in rows:
                db_path = settings.data_dir / r["db_path"]
                result.append(
                    SampleStat(
                        sample_id=r["id"],
                        name=r["name"],
                        db_path=str(db_path),
                        file_size_bytes=_get_file_size(db_path),
                        exists=db_path.exists(),
                    )
                )
    except Exception:
        pass

    return result


# ── Disk usage ───────────────────────────────────────────────────────


def _dir_size(path: Path) -> int:
    """Compute total size of all files in a directory (non-recursive for top level)."""
    total = 0
    if not path.exists():
        return 0
    try:
        for entry in path.rglob("*"):
            if entry.is_file():
                try:
                    total += entry.stat().st_size
                except OSError:
                    pass
    except OSError:
        pass
    return total


@router.get("/disk-usage", response_model=DiskUsage)
def get_disk_usage() -> DiskUsage:
    """Return disk usage breakdown for the data directory."""
    settings = get_settings()
    data_dir = settings.data_dir

    # Filesystem stats (cross-platform)
    try:
        usage = shutil.disk_usage(str(data_dir))
        total_bytes = usage.total
        free_bytes = usage.free
        used_bytes = usage.used
    except OSError:
        total_bytes = 0
        free_bytes = 0
        used_bytes = 0

    # Reference DB sizes
    ref_db_paths = [
        settings.reference_db_path,
        settings.vep_bundle_db_path,
        settings.gnomad_db_path,
        settings.dbnsfp_db_path,
        settings.encode_ccres_db_path,
    ]
    ref_dbs_bytes = sum(_get_file_size(p) or 0 for p in ref_db_paths)

    # Sample DB sizes
    sample_dbs_bytes = _dir_size(settings.samples_dir)

    # Log sizes
    logs_bytes = _dir_size(settings.resolved_log_dir)

    # Everything else
    total_data_dir = _dir_size(data_dir)
    other_bytes = max(0, total_data_dir - ref_dbs_bytes - sample_dbs_bytes - logs_bytes)

    return DiskUsage(
        data_dir=str(data_dir),
        total_bytes=total_bytes,
        free_bytes=free_bytes,
        used_bytes=used_bytes,
        reference_dbs_bytes=ref_dbs_bytes,
        sample_dbs_bytes=sample_dbs_bytes,
        logs_bytes=logs_bytes,
        other_bytes=other_bytes,
    )


# ── System status ────────────────────────────────────────────────────


@router.get("/status", response_model=SystemStatus)
def get_system_status() -> SystemStatus:
    """Return system status: uptime, active jobs, version."""
    from backend.main import VERSION

    settings = get_settings()
    registry = get_registry()

    uptime = time.time() - _APP_START_TIME

    # Active jobs
    active_jobs: list[ActiveJob] = []
    try:
        with registry.reference_engine.connect() as conn:
            q = sa.select(jobs).where(jobs.c.status.in_(["pending", "running"]))
            rows = conn.execute(q).mappings().all()
            for r in rows:
                created = r["created_at"]
                if isinstance(created, datetime):
                    created = created.isoformat()
                elif created is not None:
                    created = str(created)
                active_jobs.append(
                    ActiveJob(
                        job_id=r["job_id"],
                        job_type=r["job_type"],
                        status=r["status"],
                        progress_pct=r["progress_pct"],
                        message=r["message"],
                        created_at=created,
                    )
                )
    except Exception:
        pass

    # Total samples
    total_samples = 0
    try:
        with registry.reference_engine.connect() as conn:
            q = sa.select(sa.func.count()).select_from(samples)
            total_samples = conn.execute(q).scalar() or 0
    except Exception:
        pass

    return SystemStatus(
        version=VERSION,
        uptime_seconds=round(uptime, 1),
        data_dir=str(settings.data_dir),
        active_jobs=active_jobs,
        total_samples=total_samples,
        auth_enabled=settings.auth_enabled,
        log_level=settings.log_level,
    )

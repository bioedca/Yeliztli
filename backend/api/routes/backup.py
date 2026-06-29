"""Backup export API routes (P4-21c).

Endpoints:
    GET  /api/backup/estimate            — Estimate archive size before export
    POST /api/backup/export              — Start background export as Huey task
    GET  /api/backup/status/{job_id}     — Poll export job status
    GET  /api/backup/download/{filename} — Download completed archive
"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any

import sqlalchemy as sa
import structlog
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from backend.config import config_toml_path, get_settings
from backend.db.database_registry import DATABASES

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/backup", tags=["backup"])

# The small central sample registry is part of every backup. It is exported as
# JSON from the live SQLite connection instead of copying reference.db directly:
# reference.db may be in WAL mode, and it also contains unrelated reference and
# runtime tables.
REGISTRY_MANIFEST_FILE = "sample_registry.json"

# Standalone reference/annotation files that can optionally be included in the
# archive. Reference-resident datasets are intentionally excluded because
# copying reference.db wholesale would overwrite runtime/sample registry state.
REFERENCE_DB_FILES = tuple(
    db.filename for db in DATABASES.values() if db.target_db == "standalone" and db.filename
)

# Older archives may contain reference-resident datasets as standalone files.
# Accept those on import for compatibility, but do not create new archives with
# them.
LEGACY_REFERENCE_DB_FILES = tuple(
    db.filename for db in DATABASES.values() if db.target_db == "reference" and db.filename
)
RESTORABLE_REFERENCE_DB_FILES = tuple(
    dict.fromkeys((*REFERENCE_DB_FILES, *LEGACY_REFERENCE_DB_FILES))
)


class BackupRegistryManifestError(RuntimeError):
    """Raised when a backup cannot safely export sample registry metadata."""


# ── Response models ──────────────────────────────────────────────────


class BackupEstimateResponse(BaseModel):
    """Estimated archive size before export."""

    sample_bytes: int
    config_bytes: int
    reference_bytes: int
    total_without_ref_bytes: int
    total_with_ref_bytes: int
    total_without_ref_mb: float
    total_with_ref_mb: float
    sample_count: int
    reference_db_count: int


class BackupExportRequest(BaseModel):
    """Request to start a backup export."""

    include_reference_dbs: bool = False


class BackupExportResponse(BaseModel):
    """Response after starting a backup export job."""

    job_id: str
    message: str


class BackupStatusResponse(BaseModel):
    """Status of a backup export job."""

    job_id: str
    status: str
    progress_pct: float
    message: str
    error: str | None = None
    download_filename: str | None = None


# ── Helpers ──────────────────────────────────────────────────────────


def _get_file_size(path: Path) -> int:
    """Return file size in bytes, 0 if not found."""
    try:
        return path.stat().st_size if path.exists() else 0
    except OSError:
        return 0


def _collect_sample_files(data_dir: Path) -> list[Path]:
    """Collect all sample DB files from the samples directory."""
    samples_dir = data_dir / "samples"
    if not samples_dir.exists():
        return []
    return sorted(samples_dir.glob("sample_*.db"))


def _collect_reference_files(data_dir: Path) -> list[Path]:
    """Collect existing optional standalone reference files."""
    files = []
    for name in REFERENCE_DB_FILES:
        p = data_dir / name
        if p.exists():
            files.append(p)
    return files


def _jsonable(value: Any) -> Any:
    """Return a JSON-safe representation of a registry value."""
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def build_sample_registry_manifest(data_dir: Path) -> bytes:
    """Serialize registry rows for the sample DBs included in this backup."""
    from backend.db.connection import get_registry
    from backend.db.tables import individuals, samples

    sample_db_paths = [f"samples/{path.name}" for path in _collect_sample_files(data_dir)]
    manifest: dict[str, Any] = {"version": 1, "individuals": [], "samples": []}
    if not sample_db_paths:
        return json.dumps(manifest, separators=(",", ":"), sort_keys=True).encode()

    try:
        registry = get_registry()
        with registry.reference_engine.connect() as conn:
            inspector = sa.inspect(conn)
            if "samples" not in inspector.get_table_names():
                raise BackupRegistryManifestError(
                    "Sample registry table is missing; backup would lose sample metadata."
                )
            sample_rows = conn.execute(
                sa.select(samples)
                .where(samples.c.db_path.in_(sample_db_paths))
                .order_by(samples.c.id.asc())
            ).fetchall()
            manifest["samples"] = [
                {col.name: _jsonable(row._mapping[col.name]) for col in samples.c}
                for row in sample_rows
            ]
            individual_ids = sorted(
                {
                    row._mapping["individual_id"]
                    for row in sample_rows
                    if row._mapping.get("individual_id") is not None
                }
            )
            if individual_ids and "individuals" in inspector.get_table_names():
                individual_rows = conn.execute(
                    sa.select(individuals)
                    .where(individuals.c.id.in_(individual_ids))
                    .order_by(individuals.c.id.asc())
                ).fetchall()
                manifest["individuals"] = [
                    {col.name: _jsonable(row._mapping[col.name]) for col in individuals.c}
                    for row in individual_rows
                ]
    except (sa.exc.SQLAlchemyError, BackupRegistryManifestError) as exc:
        logger.warning("backup_registry_manifest_skipped", error=str(exc))
        raise BackupRegistryManifestError(
            "Could not export the sample registry; backup would omit sample names/groupings."
        ) from exc

    return json.dumps(manifest, separators=(",", ":"), sort_keys=True).encode()


def _has_running_backup() -> bool:
    """Check if a backup export is already running."""
    import sqlalchemy as sa

    from backend.db.connection import get_registry
    from backend.db.tables import jobs

    registry = get_registry()
    with registry.reference_engine.connect() as conn:
        row = conn.execute(
            sa.select(jobs.c.job_id).where(
                jobs.c.job_type == "backup_export",
                jobs.c.status.in_(["pending", "running"]),
            )
        ).fetchone()
    return row is not None


# ── GET /api/backup/estimate ─────────────────────────────────────────


@router.get("/estimate", response_model=BackupEstimateResponse)
async def backup_estimate() -> BackupEstimateResponse:
    """Estimate backup archive size.

    Returns raw file sizes (actual .tar.gz will be smaller due to compression).
    """
    settings = get_settings()
    data_dir = settings.data_dir

    sample_files = _collect_sample_files(data_dir)
    sample_bytes = sum(_get_file_size(f) for f in sample_files)

    config_path = config_toml_path()
    disclaimer_path = data_dir / ".disclaimer_accepted"
    registry_manifest_bytes = len(build_sample_registry_manifest(data_dir))
    config_bytes = (
        _get_file_size(config_path) + _get_file_size(disclaimer_path) + registry_manifest_bytes
    )

    ref_files = _collect_reference_files(data_dir)
    reference_bytes = sum(_get_file_size(f) for f in ref_files)

    total_without = sample_bytes + config_bytes
    total_with = total_without + reference_bytes

    return BackupEstimateResponse(
        sample_bytes=sample_bytes,
        config_bytes=config_bytes,
        reference_bytes=reference_bytes,
        total_without_ref_bytes=total_without,
        total_with_ref_bytes=total_with,
        total_without_ref_mb=round(total_without / (1024 * 1024), 1),
        total_with_ref_mb=round(total_with / (1024 * 1024), 1),
        sample_count=len(sample_files),
        reference_db_count=len(ref_files),
    )


# ── POST /api/backup/export ──────────────────────────────────────────


@router.post("/export", response_model=BackupExportResponse)
async def backup_export(
    body: BackupExportRequest,
) -> BackupExportResponse:
    """Start a backup export as a background Huey task.

    Creates a .tar.gz archive containing sample DBs, config.toml,
    and optionally standalone reference files.
    """
    if _has_running_backup():
        raise HTTPException(
            status_code=409,
            detail="A backup export is already in progress.",
        )

    from backend.tasks.huey_tasks import create_backup_job, run_backup_export_task

    job_id = create_backup_job()
    run_backup_export_task(job_id, body.include_reference_dbs)

    logger.info(
        "backup_export_started",
        job_id=job_id,
        include_reference_dbs=body.include_reference_dbs,
    )

    return BackupExportResponse(
        job_id=job_id,
        message="Backup export started.",
    )


# ── GET /api/backup/status/{job_id} ──────────────────────────────────


@router.get("/status/{job_id}", response_model=BackupStatusResponse)
async def backup_status(job_id: str) -> BackupStatusResponse:
    """Check the status of a backup export job."""
    import sqlalchemy as sa

    from backend.db.connection import get_registry
    from backend.db.tables import jobs

    registry = get_registry()
    with registry.reference_engine.connect() as conn:
        row = conn.execute(
            sa.select(
                jobs.c.status,
                jobs.c.progress_pct,
                jobs.c.message,
                jobs.c.error,
            ).where(
                jobs.c.job_id == job_id,
                jobs.c.job_type == "backup_export",
            )
        ).fetchone()

    if row is None:
        raise HTTPException(status_code=404, detail=f"Backup job {job_id} not found.")

    # Extract download filename from message if complete
    download_filename = None
    if row.status == "complete" and row.message:
        # Message format: "Backup complete: <filename>"
        prefix = "Backup complete: "
        if row.message.startswith(prefix):
            download_filename = row.message[len(prefix) :]

    return BackupStatusResponse(
        job_id=job_id,
        status=row.status,
        progress_pct=row.progress_pct,
        message=row.message,
        error=row.error,
        download_filename=download_filename,
    )


# ── GET /api/backup/download/{filename} ──────────────────────────────


@router.get("/download/{filename}")
async def backup_download(filename: str) -> FileResponse:
    """Download a completed backup archive.

    Only serves files from the downloads directory matching
    the yeliztli_backup_*.tar.gz pattern. The legacy genomeinsight_backup_*
    prefix is also accepted for one release so pre-rebrand archives still
    download (back-compat shim; restore is filename-agnostic).
    """
    # Validate filename to prevent path traversal
    if "/" in filename or "\\" in filename or ".." in filename:
        raise HTTPException(status_code=400, detail="Invalid filename.")

    valid_prefix = filename.startswith(("yeliztli_backup_", "genomeinsight_backup_"))
    if not valid_prefix or not filename.endswith(".tar.gz"):
        raise HTTPException(status_code=400, detail="Invalid backup filename.")

    settings = get_settings()
    archive_path = (settings.downloads_dir / filename).resolve()

    # Defense-in-depth: ensure resolved path stays within downloads_dir
    if not str(archive_path).startswith(str(settings.downloads_dir.resolve())):
        raise HTTPException(status_code=400, detail="Invalid filename.")

    if not archive_path.exists():
        raise HTTPException(status_code=404, detail="Backup file not found.")

    return FileResponse(
        path=str(archive_path),
        filename=filename,
        media_type="application/gzip",
    )

"""vcfanno overlay API (P4-12).

Upload BED/VCF annotation overlays, manage stored overlays, and apply
them to samples. Overlay annotations become visible in the variant table.

POST   /api/overlays/upload              — Upload and save an overlay
POST   /api/overlays/parse               — Parse an overlay file (preview)
GET    /api/overlays                     — List all saved overlays
GET    /api/overlays/{overlay_id}        — Get a single overlay
DELETE /api/overlays/{overlay_id}        — Delete an overlay
POST   /api/overlays/{overlay_id}/apply  — Apply overlay to a sample
GET    /api/overlays/{overlay_id}/results — Get overlay results for a sample
"""

from __future__ import annotations

import logging
from typing import Any

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile
from pydantic import BaseModel

from backend.annotation.vcfanno_runner import (
    MAX_OVERLAY_FILE_SIZE,
    OverlayConfig,
    apply_overlay,
    delete_overlay,
    delete_overlay_results,
    detect_and_parse_overlay,
    get_overlay,
    get_overlay_results,
    list_overlays,
    save_overlay_config,
)
from backend.api.dependencies import require_fresh_sample
from backend.db.connection import get_registry
from backend.db.sample_schema import ensure_sample_schema_current
from backend.db.tables import samples

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/overlays", tags=["overlays"])


# ── Request / response models ────────────────────────────────────────


class OverlayConfigResponse(BaseModel):
    """A saved overlay config."""

    id: int
    name: str
    description: str = ""
    file_type: str
    column_names: list[str]
    region_count: int
    created_at: str | None = None


class OverlayListResponse(BaseModel):
    """List of all saved overlays."""

    items: list[OverlayConfigResponse]
    total: int


class OverlayParsePreviewResponse(BaseModel):
    """Preview result from parsing an overlay file."""

    file_type: str
    column_names: list[str]
    record_count: int
    warnings: list[str]


class OverlayUploadResponse(BaseModel):
    """Response after uploading and saving an overlay."""

    overlay: OverlayConfigResponse
    warnings: list[str]


class OverlayApplyResponse(BaseModel):
    """Response from applying an overlay to a sample."""

    overlay_id: int
    overlay_name: str
    variants_matched: int
    records_checked: int


class OverlayResultsResponse(BaseModel):
    """Overlay results for a sample."""

    overlay_id: int
    overlay_name: str
    results: list[dict[str, Any]]
    total: int


# ── Helpers ──────────────────────────────────────────────────────────


def _config_to_response(config: OverlayConfig) -> OverlayConfigResponse:
    return OverlayConfigResponse(
        id=config.id,
        name=config.name,
        description=config.description,
        file_type=config.file_type,
        column_names=config.column_names,
        region_count=config.region_count,
        created_at=config.created_at,
    )


def _get_sample_engine(sample_id: int) -> sa.Engine:
    """Resolve sample_id to a per-sample DB engine."""
    registry = get_registry()
    with registry.reference_engine.connect() as conn:
        row = conn.execute(
            sa.select(samples.c.db_path).where(samples.c.id == sample_id)
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"Sample {sample_id} not found.")

    sample_db_path = registry.settings.data_dir / row.db_path
    if not sample_db_path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"Sample database file not found for sample {sample_id}.",
        )
    return registry.get_sample_engine(sample_db_path)


# ── Endpoints ────────────────────────────────────────────────────────


@router.post("/parse")
async def parse_overlay_preview(file: UploadFile) -> OverlayParsePreviewResponse:
    """Parse an overlay file without saving (preview mode).

    Accepts .bed or .vcf files. Returns detected format, column names,
    record count, and any parse warnings.

    Example: ``POST /api/overlays/parse`` with multipart file upload.
    """
    if not file.filename:
        raise HTTPException(status_code=400, detail="No filename provided.")

    content_bytes = await file.read()
    if len(content_bytes) > MAX_OVERLAY_FILE_SIZE:
        raise HTTPException(
            status_code=400,
            detail=f"File too large. Maximum size is {MAX_OVERLAY_FILE_SIZE // 1024} KB.",
        )

    try:
        content = content_bytes.decode("utf-8")
    except UnicodeDecodeError:
        raise HTTPException(status_code=400, detail="File must be UTF-8 encoded text.") from None

    try:
        parsed = detect_and_parse_overlay(content, file.filename)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    return OverlayParsePreviewResponse(
        file_type=parsed.file_type,
        column_names=parsed.column_names,
        record_count=parsed.record_count,
        warnings=parsed.warnings,
    )


@router.post("/upload")
async def upload_overlay(
    file: UploadFile,
    name: str = Query(..., min_length=1, max_length=200, description="Overlay name"),
    description: str = Query(default="", max_length=1000, description="Overlay description"),
) -> OverlayUploadResponse:
    """Upload an overlay file, parse it, and save config to the database.

    Accepts .bed or .vcf files containing annotation data. The parsed
    overlay config is stored and can be applied to samples.

    Example: ``POST /api/overlays/upload?name=My+Overlay`` with multipart file upload.
    """
    if not file.filename:
        raise HTTPException(status_code=400, detail="No filename provided.")

    content_bytes = await file.read()
    if len(content_bytes) > MAX_OVERLAY_FILE_SIZE:
        raise HTTPException(
            status_code=400,
            detail=f"File too large. Maximum size is {MAX_OVERLAY_FILE_SIZE // 1024} KB.",
        )

    try:
        content = content_bytes.decode("utf-8")
    except UnicodeDecodeError:
        raise HTTPException(status_code=400, detail="File must be UTF-8 encoded text.") from None

    try:
        parsed = detect_and_parse_overlay(content, file.filename)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    registry = get_registry()

    # Store the raw file content first — if this fails, no orphan DB record
    # We need an ID to name the file, so save config first, then file, and
    # roll back the DB record if the file save fails.
    overlay_id = save_overlay_config(name, description, parsed, registry.reference_engine)

    try:
        _save_overlay_file(overlay_id, content, registry)
    except OSError:
        # File save failed — remove the orphan DB record
        delete_overlay(overlay_id, registry.reference_engine)
        raise HTTPException(status_code=500, detail="Failed to save overlay file to disk.")

    config = get_overlay(overlay_id, registry.reference_engine)
    if config is None:
        raise HTTPException(status_code=500, detail="Failed to retrieve saved overlay.")

    return OverlayUploadResponse(
        overlay=_config_to_response(config),
        warnings=parsed.warnings,
    )


def _save_overlay_file(overlay_id: int, content: str, registry: Any) -> None:
    """Save the raw overlay file content to the data directory."""
    overlay_dir = registry.settings.data_dir / "overlays"
    overlay_dir.mkdir(exist_ok=True)
    file_path = overlay_dir / f"overlay_{overlay_id}.txt"
    file_path.write_text(content, encoding="utf-8")


def _load_overlay_file(overlay_id: int, registry: Any) -> str | None:
    """Load the raw overlay file content from the data directory."""
    file_path = registry.settings.data_dir / "overlays" / f"overlay_{overlay_id}.txt"
    if not file_path.exists():
        return None
    return file_path.read_text(encoding="utf-8")


@router.get("")
def list_overlay_configs() -> OverlayListResponse:
    """List all saved overlay configs.

    Returns overlays sorted by creation date (newest first).

    Example: ``GET /api/overlays``
    """
    registry = get_registry()
    configs = list_overlays(registry.reference_engine)
    items = [_config_to_response(c) for c in configs]
    return OverlayListResponse(items=items, total=len(items))


@router.get("/{overlay_id}")
def get_overlay_config(overlay_id: int) -> OverlayConfigResponse:
    """Get a single overlay config by ID.

    Example: ``GET /api/overlays/1``
    """
    registry = get_registry()
    config = get_overlay(overlay_id, registry.reference_engine)
    if config is None:
        raise HTTPException(status_code=404, detail=f"Overlay {overlay_id} not found.")
    return _config_to_response(config)


@router.delete("/{overlay_id}")
def delete_overlay_config(overlay_id: int) -> dict[str, str]:
    """Delete an overlay config and its stored file.

    Example: ``DELETE /api/overlays/1``
    """
    registry = get_registry()
    deleted = delete_overlay(overlay_id, registry.reference_engine)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Overlay {overlay_id} not found.")

    # The per-sample applied results (variant_overlays) live in each sample DB,
    # which is a different database from the reference.db config row just deleted —
    # there is no cross-database FK/cascade to remove them. Without this explicit
    # sweep a deleted overlay orphans its rows in every sample it was applied to,
    # and because SQLite reuses the deleted INTEGER PRIMARY KEY, a later overlay can
    # inherit that id and adopt the stale annotations (#854). Best-effort per sample:
    # a missing DB file or absent variant_overlays table just means nothing to clear.
    with registry.reference_engine.connect() as conn:
        sample_rows = conn.execute(sa.select(samples.c.id, samples.c.db_path)).fetchall()
    for sample_row in sample_rows:
        sample_db_path = registry.settings.data_dir / sample_row.db_path
        if not sample_db_path.exists():
            continue
        try:
            delete_overlay_results(overlay_id, registry.get_sample_engine(sample_db_path))
        except (OSError, sa.exc.SQLAlchemyError):
            logger.warning(
                "Failed to clear overlay %s results from sample %s",
                overlay_id,
                sample_row.id,
            )

    # Clean up stored file
    file_path = registry.settings.data_dir / "overlays" / f"overlay_{overlay_id}.txt"
    if file_path.exists():
        try:
            file_path.unlink()
        except OSError:
            logger.warning("Failed to delete overlay file: %s", file_path)

    return {"status": "deleted", "overlay_id": str(overlay_id)}


@router.post("/{overlay_id}/apply", dependencies=[Depends(require_fresh_sample)])
def apply_overlay_to_sample(
    overlay_id: int,
    sample_id: int = Query(..., description="Sample ID"),
) -> OverlayApplyResponse:
    """Apply a saved overlay to a sample's variants.

    Re-parses the stored overlay file and intersects it with the
    sample's variant positions. Results are stored in the
    ``variant_overlays`` table in the sample database.

    Example: ``POST /api/overlays/1/apply?sample_id=1``
    """
    registry = get_registry()
    config = get_overlay(overlay_id, registry.reference_engine)
    if config is None:
        raise HTTPException(status_code=404, detail=f"Overlay {overlay_id} not found.")

    # Load and re-parse the overlay file
    content = _load_overlay_file(overlay_id, registry)
    if content is None:
        raise HTTPException(
            status_code=404,
            detail=f"Overlay file not found for overlay {overlay_id}. "
            "The file may have been deleted. Please re-upload.",
        )

    try:
        parsed = detect_and_parse_overlay(content, f"overlay.{config.file_type}")
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    sample_engine = _get_sample_engine(sample_id)

    # Ensure variant_overlays table exists
    ensure_sample_schema_current(sample_engine)

    result = apply_overlay(parsed, overlay_id, config.name, sample_engine)

    return OverlayApplyResponse(
        overlay_id=result.overlay_id,
        overlay_name=result.overlay_name,
        variants_matched=result.variants_matched,
        records_checked=result.records_checked,
    )


@router.get("/{overlay_id}/results", dependencies=[Depends(require_fresh_sample)])
def get_overlay_sample_results(
    overlay_id: int,
    sample_id: int = Query(..., description="Sample ID"),
) -> OverlayResultsResponse:
    """Get overlay results for a specific overlay applied to a sample.

    Returns all matched variants with their overlay annotations.

    Example: ``GET /api/overlays/1/results?sample_id=1``
    """
    registry = get_registry()
    config = get_overlay(overlay_id, registry.reference_engine)
    if config is None:
        raise HTTPException(status_code=404, detail=f"Overlay {overlay_id} not found.")

    sample_engine = _get_sample_engine(sample_id)
    results = get_overlay_results(overlay_id, sample_engine)

    return OverlayResultsResponse(
        overlay_id=overlay_id,
        overlay_name=config.name,
        results=results,
        total=len(results),
    )

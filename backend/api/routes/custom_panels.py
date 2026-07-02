"""Custom gene panel API (P4-11).

Upload gene list or BED file, manage saved panels, run against the
rare variant finder.

POST /api/panels/upload             — Upload and save a custom panel
GET  /api/panels                    — List all saved panels
GET  /api/panels/{panel_id}         — Get a single panel
DELETE /api/panels/{panel_id}       — Delete a panel
POST /api/panels/{panel_id}/search  — Run rare variant finder with panel
POST /api/panels/parse              — Parse a file without saving (preview)
"""

from __future__ import annotations

import logging
from typing import Any

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field

from backend.analysis.custom_panels import (
    MAX_FILE_SIZE_BYTES,
    CustomPanel,
    ParsedPanel,
    delete_custom_panel,
    detect_and_parse,
    get_custom_panel,
    list_custom_panels,
    save_custom_panel,
)
from backend.analysis.rare_variant_finder import (
    DEFAULT_AF_THRESHOLD,
    RareVariantFilter,
    find_rare_variants,
    store_rare_variant_findings,
)
from backend.api.dependencies import require_fresh_sample
from backend.db.connection import get_registry
from backend.db.tables import samples
from backend.services.sex_inference import (
    get_recorded_biological_sex,
    infer_biological_sex,
    resolve_biological_sex,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/panels", tags=["custom-panels"])


# ── Request / response models ────────────────────────────────────────


class CustomPanelResponse(BaseModel):
    """A saved custom gene panel."""

    id: int
    name: str
    description: str = ""
    gene_symbols: list[str]
    bed_regions: list[dict[str, Any]] | None = None
    source_type: str
    gene_count: int
    created_at: str | None = None


class CustomPanelListResponse(BaseModel):
    """List of all saved custom panels."""

    items: list[CustomPanelResponse]
    total: int


class ParsePreviewResponse(BaseModel):
    """Preview result of parsing a panel file without saving."""

    gene_symbols: list[str]
    gene_count: int
    region_count: int
    source_type: str
    warnings: list[str]


class PanelUploadResponse(BaseModel):
    """Response after uploading and saving a panel."""

    panel: CustomPanelResponse
    warnings: list[str]


class PanelSearchRequest(BaseModel):
    """Filters for running rare variant search with a saved panel."""

    af_threshold: float = Field(
        default=DEFAULT_AF_THRESHOLD,
        ge=0.0,
        le=1.0,
        description="Maximum gnomAD global allele frequency",
    )
    consequences: list[str] | None = Field(default=None)
    clinvar_significance: list[str] | None = Field(default=None)
    include_novel: bool = Field(default=True)
    zygosity: str | None = Field(default=None)


class PanelSearchResponse(BaseModel):
    """Response from running rare variant search with a panel."""

    panel_name: str
    variants_found: int
    findings_stored: int
    total_variants_scanned: int
    novel_count: int
    pathogenic_count: int
    genes_with_findings: list[str]


# ── Helpers ──────────────────────────────────────────────────────────


def _panel_to_response(panel: CustomPanel) -> CustomPanelResponse:
    return CustomPanelResponse(
        id=panel.id,
        name=panel.name,
        description=panel.description,
        gene_symbols=panel.gene_symbols,
        bed_regions=panel.bed_regions,
        source_type=panel.source_type,
        gene_count=panel.gene_count,
        created_at=panel.created_at,
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


def _resolve_biological_sex_for_sample(sample_engine: sa.Engine, sample_id: int) -> str | None:
    """Resolve recorded-over-inferred biological sex for rare-variant gates."""
    registry = get_registry()
    resolved = resolve_biological_sex(
        recorded_sex=get_recorded_biological_sex(registry.reference_engine, sample_id),
        inferred_sex=infer_biological_sex(sample_engine),
    )
    return resolved.sex


# ── Endpoints ────────────────────────────────────────────────────────


async def _read_and_parse_upload(file: UploadFile) -> ParsedPanel:
    """Read, validate, and parse an uploaded panel file."""
    if not file.filename:
        raise HTTPException(status_code=400, detail="No filename provided.")

    content_bytes = await file.read()
    if len(content_bytes) > MAX_FILE_SIZE_BYTES:
        raise HTTPException(
            status_code=400,
            detail=f"File too large. Maximum size is {MAX_FILE_SIZE_BYTES // 1024} KB.",
        )

    try:
        content = content_bytes.decode("utf-8")
    except UnicodeDecodeError:
        raise HTTPException(
            status_code=400,
            detail="File must be UTF-8 encoded text.",
        )

    try:
        return detect_and_parse(content, file.filename)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


@router.post("/parse")
async def parse_panel_preview(
    file: UploadFile,
) -> ParsePreviewResponse:
    """Parse a gene panel file without saving (preview mode).

    Accepts .txt, .csv, .tsv, or .bed files. Returns extracted gene
    symbols and any parse warnings for user review before saving.

    Example: ``POST /api/panels/parse`` with multipart file upload.
    """
    parsed = await _read_and_parse_upload(file)

    return ParsePreviewResponse(
        gene_symbols=parsed.gene_symbols,
        gene_count=parsed.gene_count,
        region_count=parsed.region_count,
        source_type=parsed.source_type,
        warnings=parsed.warnings,
    )


@router.post("/upload")
async def upload_custom_panel(
    file: UploadFile,
    name: str = Query(..., min_length=1, max_length=200, description="Panel name"),
    description: str = Query(default="", max_length=1000, description="Panel description"),
) -> PanelUploadResponse:
    """Upload a gene panel file, parse it, and save to the database.

    Accepts .txt, .csv, .tsv, or .bed files containing gene symbols
    or genomic regions. The parsed panel is stored and can be used
    with the rare variant finder.

    Example: ``POST /api/panels/upload?name=My+Panel`` with multipart file upload.
    """
    parsed = await _read_and_parse_upload(file)

    registry = get_registry()
    panel_id = save_custom_panel(name, description, parsed, registry.reference_engine)
    panel = get_custom_panel(panel_id, registry.reference_engine)
    if panel is None:
        raise HTTPException(status_code=500, detail="Failed to retrieve saved panel.")

    return PanelUploadResponse(
        panel=_panel_to_response(panel),
        warnings=parsed.warnings,
    )


@router.get("")
def list_panels() -> CustomPanelListResponse:
    """List all saved custom gene panels.

    Returns panels sorted by creation date (newest first).

    Example: ``GET /api/panels``
    """
    registry = get_registry()
    panels = list_custom_panels(registry.reference_engine)
    items = [_panel_to_response(p) for p in panels]
    return CustomPanelListResponse(items=items, total=len(items))


@router.get("/{panel_id}")
def get_panel(panel_id: int) -> CustomPanelResponse:
    """Get a single custom gene panel by ID.

    Example: ``GET /api/panels/1``
    """
    registry = get_registry()
    panel = get_custom_panel(panel_id, registry.reference_engine)
    if panel is None:
        raise HTTPException(status_code=404, detail=f"Panel {panel_id} not found.")
    return _panel_to_response(panel)


@router.delete("/{panel_id}")
def delete_panel(panel_id: int) -> dict[str, str]:
    """Delete a custom gene panel.

    Example: ``DELETE /api/panels/1``
    """
    registry = get_registry()
    deleted = delete_custom_panel(panel_id, registry.reference_engine)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Panel {panel_id} not found.")
    return {"status": "deleted", "panel_id": str(panel_id)}


@router.post("/{panel_id}/search", dependencies=[Depends(require_fresh_sample)])
def search_with_panel(
    panel_id: int,
    sample_id: int = Query(..., description="Sample ID"),
    body: PanelSearchRequest | None = None,
) -> PanelSearchResponse:
    """Run the rare variant finder using a saved custom panel's gene list.

    Loads the panel's gene symbols and passes them as the gene filter
    to the rare variant finder. Additional filters (AF, consequence,
    ClinVar) can be specified in the request body.

    Example: ``POST /api/panels/1/search?sample_id=1``
    """
    registry = get_registry()
    panel = get_custom_panel(panel_id, registry.reference_engine)
    if panel is None:
        raise HTTPException(status_code=404, detail=f"Panel {panel_id} not found.")

    if not panel.gene_symbols:
        raise HTTPException(
            status_code=422,
            detail="Panel has no gene symbols to search with.",
        )

    sample_engine = _get_sample_engine(sample_id)
    biological_sex = _resolve_biological_sex_for_sample(sample_engine, sample_id)

    # Panel search persists findings (via store_rare_variant_findings below), so
    # apply the same carriage and sex gates as the automated run_all path: a
    # hom-ref (non-carrier), unscoreable, or biologically impossible call in a
    # panel gene must not be counted as found. NULL zygosity is excluded by the
    # carriage gate too.
    filters = RareVariantFilter(
        gene_symbols=panel.gene_symbols,
        af_threshold=body.af_threshold if body else DEFAULT_AF_THRESHOLD,
        consequences=body.consequences if body else None,
        clinvar_significance=body.clinvar_significance if body else None,
        include_novel=body.include_novel if body else True,
        zygosity=body.zygosity if body else None,
        carried_only=True,
        inferred_sex=biological_sex,
        biological_sex=biological_sex,
    )

    result = find_rare_variants(filters, sample_engine)
    stored = store_rare_variant_findings(result, sample_engine)

    return PanelSearchResponse(
        panel_name=panel.name,
        variants_found=result.count,
        findings_stored=stored,
        total_variants_scanned=result.total_variants_scanned,
        novel_count=result.novel_count,
        pathogenic_count=result.pathogenic_count,
        genes_with_findings=result.genes_with_findings,
    )

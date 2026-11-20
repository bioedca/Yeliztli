"""ENCODE cCREs API routes for IGV.js track data (P2-27).

Endpoints:
    GET /api/encode-ccres/region — Query cCREs overlapping a genomic region
    GET /api/encode-ccres/summary — Get cCRE counts by classification
    GET /api/encode-ccres/status — Check if ENCODE cCREs data is loaded
"""

from __future__ import annotations

import structlog
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from backend.db.connection import get_registry
from backend.diagnostics import DiagnosticError

logger = structlog.get_logger(__name__)


# Helper to get the encode_ccres engine with proper error handling
def _get_encode_engine():
    """Get the ENCODE cCREs engine, raising 503 if unavailable."""
    registry = get_registry()
    try:
        return registry.encode_ccres_engine
    except Exception as exc:
        logger.warning("encode_ccres_engine_unavailable", error=DiagnosticError(exc))
        raise HTTPException(
            status_code=503,
            detail="ENCODE cCREs database not available. Download it via the setup wizard.",
        ) from exc


router = APIRouter(prefix="/encode-ccres", tags=["encode-ccres"])


# ── Response models ──────────────────────────────────────────────────


class CCREItem(BaseModel):
    """A single cCRE record."""

    accession: str
    chrom: str
    start_pos: int
    end_pos: int
    ccre_class: str


class RegionResponse(BaseModel):
    """Response for region queries."""

    chrom: str
    start: int
    end: int
    count: int
    ccres: list[CCREItem]


class SummaryResponse(BaseModel):
    """cCRE classification summary."""

    total: int
    by_class: dict[str, int]


class StatusResponse(BaseModel):
    """ENCODE cCREs data status."""

    loaded: bool
    record_count: int


# ── GET /api/encode-ccres/region ─────────────────────────────────────


@router.get("/region", response_model=RegionResponse)
async def query_region(
    chrom: str = Query(..., description="Chromosome (e.g., '1', 'X')"),
    start: int = Query(..., ge=0, description="Region start position (0-based)"),
    end: int = Query(..., gt=0, description="Region end position (0-based)"),
) -> RegionResponse:
    """Query ENCODE cCREs overlapping a genomic region.

    Used by IGV.js to load track data for the visible region.
    """
    from backend.annotation.encode_ccres import is_loaded, query_ccres_by_region

    if start >= end:
        raise HTTPException(
            status_code=400,
            detail="'start' must be less than 'end'.",
        )

    engine = _get_encode_engine()

    if not is_loaded(engine):
        raise HTTPException(
            status_code=503,
            detail="ENCODE cCREs data has not been loaded yet.",
        )

    results = query_ccres_by_region(chrom, start, end, engine)

    return RegionResponse(
        chrom=chrom,
        start=start,
        end=end,
        count=len(results),
        ccres=[
            CCREItem(
                accession=r.accession,
                chrom=r.chrom,
                start_pos=r.start_pos,
                end_pos=r.end_pos,
                ccre_class=r.ccre_class,
            )
            for r in results
        ],
    )


# ── GET /api/encode-ccres/summary ────────────────────────────────────


@router.get("/summary", response_model=SummaryResponse)
async def get_summary() -> SummaryResponse:
    """Get cCRE counts grouped by classification."""
    from backend.annotation.encode_ccres import get_ccre_summary, is_loaded

    engine = _get_encode_engine()

    if not is_loaded(engine):
        raise HTTPException(
            status_code=503,
            detail="ENCODE cCREs data has not been loaded yet.",
        )

    by_class = get_ccre_summary(engine)
    total = sum(by_class.values())

    return SummaryResponse(total=total, by_class=by_class)


# ── GET /api/encode-ccres/status ─────────────────────────────────────


@router.get("/status", response_model=StatusResponse)
async def get_status() -> StatusResponse:
    """Check whether ENCODE cCREs data is loaded."""
    import sqlalchemy as sa

    from backend.annotation.encode_ccres import is_loaded

    registry = get_registry()

    try:
        engine = registry.encode_ccres_engine
    except Exception as exc:
        logger.debug("encode_ccres_status_unavailable", error=DiagnosticError(exc))
        return StatusResponse(loaded=False, record_count=0)

    loaded = is_loaded(engine)
    record_count = 0
    if loaded:
        with engine.connect() as conn:
            record_count = conn.execute(sa.text("SELECT COUNT(*) FROM encode_ccres")).scalar() or 0

    return StatusResponse(loaded=loaded, record_count=record_count)

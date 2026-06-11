"""Osteoporosis / heel-eBMD PRS API (SW-B7).

A bring-your-own (BYO) heel estimated-bone-mineral-density polygenic score
(PGS000657 "gSOS"), framed strictly as NOT a substitute for DXA or FRAX.

GET  /api/analysis/ebmd/prs?sample_id=N   — eBMD PRS + framing + availability
POST /api/analysis/ebmd/run?sample_id=N   — compute/store the eBMD PRS
"""

from __future__ import annotations

import json
import logging
from typing import Any

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from backend.analysis.ebmd_prs import EBMD_CONTEXT, EBMD_PGS_ID
from backend.api.dependencies import require_fresh_sample
from backend.db.connection import get_registry
from backend.db.tables import findings, samples

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/analysis/ebmd",
    tags=["ebmd"],
    dependencies=[Depends(require_fresh_sample)],
)


class EbmdPrsResponse(BaseModel):
    name: str = ""
    calibrated: bool = False
    percentile: float | None = None
    snps_used: int = 0
    snps_total: int = 0
    coverage_fraction: float = 0.0
    is_sufficient: bool = False
    source_study: str = ""
    source_pmid: str = ""
    pgs_id: str | None = None
    pgs_license: str | None = None
    development_method: str | None = None
    ancestry_mismatch: bool = False
    ancestry_warning_text: str | None = None
    evidence_level: int = 1


class EbmdResponse(BaseModel):
    """eBMD view: PRS (when installed) + framing + availability."""

    available: bool  # the BYO gSOS score is installed + scored
    recommended_pgs_id: str = EBMD_PGS_ID
    prs: EbmdPrsResponse | None = None
    context: dict[str, str] = {}
    research_use_only: bool = True


class EbmdRunResponse(BaseModel):
    findings_count: int
    prs_computed: bool


def _get_sample_engine(sample_id: int) -> sa.Engine:
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
            status_code=404, detail=f"Sample database file not found for sample {sample_id}."
        )
    return registry.get_sample_engine(sample_db_path)


def _parse_detail(row: sa.Row) -> dict[str, Any]:
    if not row.detail_json:
        return {}
    try:
        return json.loads(row.detail_json)
    except (json.JSONDecodeError, TypeError):
        return {}


@router.get("/prs")
def get_ebmd_prs(
    sample_id: int = Query(..., description="Sample ID"),
) -> EbmdResponse:
    """Return the eBMD PRS (if the BYO score is installed) + framing context."""
    sample_engine = _get_sample_engine(sample_id)
    with sample_engine.connect() as conn:
        row = conn.execute(
            sa.select(findings).where(findings.c.module == "ebmd", findings.c.category == "prs")
        ).fetchone()

    prs = None
    if row is not None:
        d = _parse_detail(row)
        prs = EbmdPrsResponse(
            name=d.get("name", ""),
            calibrated=d.get("calibrated", False),
            percentile=row.prs_percentile,
            snps_used=d.get("snps_used", 0),
            snps_total=d.get("snps_total", 0),
            coverage_fraction=d.get("coverage_fraction", 0.0),
            is_sufficient=d.get("is_sufficient", False),
            source_study=d.get("source_study", ""),
            source_pmid=d.get("source_pmid", ""),
            pgs_id=d.get("pgs_id"),
            pgs_license=d.get("pgs_license"),
            development_method=d.get("development_method"),
            ancestry_mismatch=d.get("ancestry_mismatch", False),
            ancestry_warning_text=d.get("ancestry_warning_text"),
            evidence_level=row.evidence_level or 1,
        )

    return EbmdResponse(available=prs is not None, prs=prs, context=EBMD_CONTEXT)


@router.post("/run")
def run_ebmd_analysis(
    sample_id: int = Query(..., description="Sample ID"),
) -> EbmdRunResponse:
    """Compute + store the eBMD PRS (no-op finding when the BYO score is absent)."""
    from backend.analysis.ancestry import get_inferred_ancestry, get_top_ancestry_fraction
    from backend.analysis.ebmd_prs import score_ebmd_prs, store_ebmd_findings
    from backend.analysis.pgs_bridge import get_pgs_scores_engine

    sample_engine = _get_sample_engine(sample_id)
    pgs_engine = get_pgs_scores_engine()
    try:
        inferred = get_inferred_ancestry(sample_engine)
        top_fraction = get_top_ancestry_fraction(sample_engine)
        prs = score_ebmd_prs(sample_engine, pgs_engine, inferred, top_fraction)
        count = store_ebmd_findings(prs, sample_engine)
    finally:
        if pgs_engine is not None:
            pgs_engine.dispose()

    return EbmdRunResponse(findings_count=count, prs_computed=prs is not None)

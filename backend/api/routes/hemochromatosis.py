"""Hereditary haemochromatosis (HFE) findings API — EXPANSION_STRATEGY.md #23.

Directly-typed HFE risk genotype (C282Y / H63D) with sex-stratified penetrance.

GET  /api/analysis/hemochromatosis/disclaimer        — Module disclaimer
GET  /api/analysis/hemochromatosis/findings?sample_id=N — Stored risk findings
POST /api/analysis/hemochromatosis/run?sample_id=N   — Run/re-run assessment
"""

from __future__ import annotations

import json
import logging
from typing import Any

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from backend.api.dependencies import require_fresh_sample
from backend.db.connection import get_registry
from backend.db.tables import findings, samples
from backend.disclaimers import (
    HEMOCHROMATOSIS_DISCLAIMER_TEXT,
    HEMOCHROMATOSIS_DISCLAIMER_TITLE,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/analysis/hemochromatosis", tags=["hemochromatosis"])


# ── Response models ──────────────────────────────────────────────


class HemochromatosisFindingResponse(BaseModel):
    """A single HFE risk-genotype finding."""

    rsid: str
    gene_symbol: str
    risk_classification: str
    zygosity: str | None = None
    evidence_level: int = 1
    finding_text: str
    genotype_calls: dict[str, str | None] = {}
    penetrance_text: str = ""
    caveats: list[str] = []
    indeterminate_loci: list[str] = []
    indeterminate_reasons: dict[str, str] = {}
    sex_used: str | None = None
    pmids: list[str] = []


class HemochromatosisFindingsListResponse(BaseModel):
    items: list[HemochromatosisFindingResponse]
    total: int


class HemochromatosisDisclaimerResponse(BaseModel):
    title: str
    text: str


class HemochromatosisRunResponse(BaseModel):
    findings_count: int
    indeterminate_loci: list[str]
    indeterminate_reasons: dict[str, str] = {}


# ── Helpers ──────────────────────────────────────────────────────


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
            status_code=404,
            detail=f"Sample database file not found for sample {sample_id}.",
        )
    return registry.get_sample_engine(sample_db_path)


def _fetch_findings(sample_engine: sa.Engine) -> list[dict[str, Any]]:
    with sample_engine.connect() as conn:
        stmt = (
            sa.select(findings)
            .where(
                findings.c.module == "hemochromatosis",
                findings.c.category == "risk_genotype",
            )
            .order_by(findings.c.evidence_level.desc())
        )
        rows = conn.execute(stmt).fetchall()

    result: list[dict[str, Any]] = []
    for row in rows:
        detail: dict[str, Any] = {}
        if row.detail_json:
            try:
                detail = json.loads(row.detail_json)
            except (json.JSONDecodeError, TypeError):
                logger.warning("Failed to parse detail_json for finding id=%s", row.id)
        pmids: list[str] = []
        if row.pmid_citations:
            try:
                pmids = json.loads(row.pmid_citations)
            except (json.JSONDecodeError, TypeError):
                pmids = []
        result.append(
            {
                "rsid": row.rsid or "",
                "gene_symbol": row.gene_symbol or "",
                "risk_classification": row.conditions or "",
                "zygosity": row.zygosity,
                "evidence_level": row.evidence_level or 1,
                "finding_text": row.finding_text or "",
                "genotype_calls": detail.get("genotype_calls", {}),
                "penetrance_text": detail.get("penetrance_text", ""),
                "caveats": detail.get("caveats", []),
                "indeterminate_loci": detail.get("indeterminate_loci", []),
                "indeterminate_reasons": detail.get("indeterminate_reasons", {}),
                "sex_used": detail.get("sex_used"),
                "pmids": pmids,
            }
        )
    return result


# ── Endpoints ────────────────────────────────────────────────────


@router.get("/disclaimer")
def get_hemochromatosis_disclaimer() -> HemochromatosisDisclaimerResponse:
    """Return the haemochromatosis module disclaimer text."""
    return HemochromatosisDisclaimerResponse(
        title=HEMOCHROMATOSIS_DISCLAIMER_TITLE,
        text=HEMOCHROMATOSIS_DISCLAIMER_TEXT,
    )


@router.get("/findings", dependencies=[Depends(require_fresh_sample)])
def list_hemochromatosis_findings(
    sample_id: int = Query(..., description="Sample ID"),
) -> HemochromatosisFindingsListResponse:
    """List stored HFE risk-genotype findings for a sample."""
    sample_engine = _get_sample_engine(sample_id)
    raw = _fetch_findings(sample_engine)
    items = [HemochromatosisFindingResponse(**f) for f in raw]
    return HemochromatosisFindingsListResponse(items=items, total=len(items))


@router.post("/run", dependencies=[Depends(require_fresh_sample)])
def run_hemochromatosis_analysis(
    sample_id: int = Query(..., description="Sample ID"),
) -> HemochromatosisRunResponse:
    """Run or re-run the HFE risk-genotype assessment for a sample."""
    from backend.analysis.hemochromatosis import (
        assess_hemochromatosis,
        load_hemochromatosis_panel,
        store_hemochromatosis_findings,
    )

    sample_engine = _get_sample_engine(sample_id)
    panel = load_hemochromatosis_panel()
    # Recorded biological sex (if the user set one) is authoritative over array
    # inference for the sex-stratified C282Y penetrance (#399).
    assessment = assess_hemochromatosis(
        panel,
        sample_engine,
        reference_engine=get_registry().reference_engine,
        sample_id=sample_id,
    )
    count = store_hemochromatosis_findings(assessment, sample_engine)
    return HemochromatosisRunResponse(
        findings_count=count,
        indeterminate_loci=assessment.indeterminate_loci,
        indeterminate_reasons=assessment.indeterminate_reasons,
    )

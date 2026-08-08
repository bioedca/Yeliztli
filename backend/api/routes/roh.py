"""Runs-of-Homozygosity (ROH / FROH) findings API — EXPANSION_STRATEGY.md #29.

GET  /api/analysis/roh/disclaimer
GET  /api/analysis/roh/findings?sample_id=N
POST /api/analysis/roh/run?sample_id=N
"""

from __future__ import annotations

import json
import logging
from typing import Any

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from backend.analysis.pharmacogenomics import is_patient_presentable_finding_payload
from backend.analysis.roh import CATEGORY, MODULE
from backend.api.dependencies import require_fresh_sample
from backend.api.routes.risk_common import resolve_sample_engine
from backend.db.tables import findings
from backend.disclaimers import ROH_DISCLAIMER_TEXT, ROH_DISCLAIMER_TITLE

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/analysis/roh", tags=["roh"])


class RohSegmentResponse(BaseModel):
    chrom: str
    start: int
    end: int
    length_kb: float
    n_snps: int


class RohFindingResponse(BaseModel):
    finding_text: str
    froh: float = 0.0
    total_roh_kb: float = 0.0
    longest_kb: float = 0.0
    n_segments: int = 0
    autosomal_snps_used: int = 0
    segments: list[RohSegmentResponse] = []
    segments_truncated: bool = False


class RohDisclaimerResponse(BaseModel):
    title: str
    text: str


class RohRunResponse(BaseModel):
    findings_count: int


@router.get("/disclaimer")
def get_disclaimer() -> RohDisclaimerResponse:
    return RohDisclaimerResponse(title=ROH_DISCLAIMER_TITLE, text=ROH_DISCLAIMER_TEXT)


@router.get("/findings", dependencies=[Depends(require_fresh_sample)])
def list_findings(
    sample_id: int = Query(..., description="Sample ID"),
) -> RohFindingResponse | None:
    engine = resolve_sample_engine(sample_id)
    with engine.connect() as conn:
        row = conn.execute(
            sa.select(findings).where(findings.c.module == MODULE, findings.c.category == CATEGORY)
        ).fetchone()
    if row is None or not is_patient_presentable_finding_payload(row._mapping):
        return None
    # Parse detail_json and build the response defensively: a malformed or
    # schema-drifted detail blob (e.g. a row written by an older version, or an
    # unexpected segment shape) must not 500 — fall back to the plain
    # finding_text with zeroed metrics.
    try:
        detail: dict[str, Any] = json.loads(row.detail_json) if row.detail_json else {}
        return RohFindingResponse(
            finding_text=row.finding_text or "",
            froh=detail.get("froh", 0.0),
            total_roh_kb=detail.get("total_roh_kb", 0.0),
            longest_kb=detail.get("longest_kb", 0.0),
            n_segments=detail.get("n_segments", 0),
            autosomal_snps_used=detail.get("autosomal_snps_used", 0),
            segments=[RohSegmentResponse(**s) for s in detail.get("segments", [])],
            segments_truncated=detail.get("segments_truncated", False),
        )
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        logger.warning("Failed to build ROH response for finding id=%s: %s", row.id, exc)
        return RohFindingResponse(finding_text=row.finding_text or "")


@router.post("/run", dependencies=[Depends(require_fresh_sample)])
def run(sample_id: int = Query(..., description="Sample ID")) -> RohRunResponse:
    from backend.analysis.roh import detect_roh, store_roh_findings

    engine = resolve_sample_engine(sample_id)
    result = detect_roh(engine)
    count = store_roh_findings(result, engine)
    return RohRunResponse(findings_count=count)

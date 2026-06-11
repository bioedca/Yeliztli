"""Cross-source pharmacogenomic evidence API — SW-E2.

For each of a sample's CPIC prescribing alerts, surfaces the corroborating
PharmGKB Level of Evidence + DPWG guideline presence + FDA pharmacogenomic
labeling (see ``backend.analysis.pgx_guidelines``). Additive, context-only: it
never changes a finding, a CPIC recommendation, or a metabolizer status.

GET /api/analysis/pgx-guidelines?sample_id=N
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from backend.analysis.pgx_guidelines import assess_sample_pgx_guidelines
from backend.api.dependencies import require_fresh_sample
from backend.api.routes.risk_common import resolve_sample_engine

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/analysis/pgx-guidelines",
    tags=["pgx-guidelines"],
    dependencies=[Depends(require_fresh_sample)],
)


class PgxAlertSourcesResponse(BaseModel):
    """Cross-source evidence for one CPIC prescribing alert."""

    finding_id: int
    gene_symbol: str | None = None
    drug: str | None = None
    metabolizer_status: str | None = None
    has_sources: bool
    pharmgkb_loe: str | None = None
    dpwg_guideline: bool | None = None
    fda_pgx_level: str | None = None


class PgxGuidelinesResponse(BaseModel):
    """Cross-source pharmacogenomic evidence layered over the sample's CPIC alerts."""

    alerts: list[PgxAlertSourcesResponse]
    context_only: bool
    note: str
    pmid_citations: list[str] = []


@router.get("", response_model=PgxGuidelinesResponse)
def get_pgx_guidelines(
    sample_id: int = Query(..., description="Sample ID"),
) -> PgxGuidelinesResponse:
    """Cross-source evidence (PharmGKB LoE / DPWG / FDA) for the sample's CPIC alerts.

    Joins each stored ``prescribing_alert`` finding to its (gene, drug)
    cross-source evidence. ``has_sources=false`` means the pair is not in the
    curated table (absence of corroboration, not a downgrade). Context-only — it
    never changes the CPIC recommendation.
    """
    sample_engine = resolve_sample_engine(sample_id)
    return PgxGuidelinesResponse(**assess_sample_pgx_guidelines(sample_engine))

"""DRAFT ACMG/AMP variant-classification API — SW-F1 / roadmap #13.

A read-only, NON-CLINICAL automated ACMG/AMP classification for the notable
variants in a sample (see ``backend.analysis.acmg``). Additive only: it never
changes a finding's evidence level or ClinVar significance, never auto-upgrades a
classification, and writes nothing back to the ``findings`` table.

GET /api/analysis/acmg?sample_id=N
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from backend.analysis.acmg import assess_sample_acmg
from backend.api.dependencies import require_fresh_sample
from backend.api.routes.risk_common import resolve_sample_engine
from backend.db.connection import get_registry

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/analysis/acmg",
    tags=["acmg"],
    dependencies=[Depends(require_fresh_sample)],
)


class AcmgCriterionResponse(BaseModel):
    code: str
    direction: str
    strength: str
    points: int
    rationale: str


class AcmgVariantResponse(BaseModel):
    rsid: str | None = None
    gene_symbol: str | None = None
    genotype: str | None = None
    zygosity: str | None = None
    consequence: str | None = None
    clinvar_significance: str | None = None
    acmg_classification: str
    points: int
    is_draft: bool
    criteria: list[AcmgCriterionResponse] = []
    note: str
    pmid_citations: list[str] = []


class AcmgResponse(BaseModel):
    variants: list[AcmgVariantResponse]
    truncated: bool
    total_candidates: int


@router.get("", response_model=AcmgResponse)
def list_acmg(
    sample_id: int = Query(..., description="Sample ID"),
) -> AcmgResponse:
    """DRAFT ACMG/AMP classifications for the sample's notable variants.

    Scope: carried ClinVar-listed, predicted loss-of-function / in-frame, and
    PP3-eligible (REVEL ≥ 0.644) missense variants. ``acmg_classification`` is a
    DRAFT, non-clinical estimate (Tavtigian points over a computable ACMG/AMP
    subset) and is shown alongside ``clinvar_significance`` for context — it
    never overrides it.
    """
    sample_engine = resolve_sample_engine(sample_id)
    reference_engine = get_registry().reference_engine
    result = assess_sample_acmg(sample_engine, reference_engine)
    return AcmgResponse(**result)

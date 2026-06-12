"""BCHE succinylcholine/mivacurium apnea-risk context API — SW-E6.

A read-only, context-only summary of the two array-typeable BChE reduced-activity
variants (atypical/dibucaine-resistant rs1799807, K/Kalow rs1803274) for a sample
— see ``backend.analysis.bche``. Additive only: it emits no diagnosis, changes no
finding's evidence level or ClinVar significance, and writes nothing back.

GET /api/analysis/bche?sample_id=N
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from backend.analysis.bche import assess_bche
from backend.api.dependencies import require_fresh_sample
from backend.api.routes.risk_common import resolve_sample_engine

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/analysis/bche",
    tags=["bche"],
    dependencies=[Depends(require_fresh_sample)],
)


class BcheVariantResponse(BaseModel):
    """One array-typeable BChE reduced-activity variant."""

    name: str
    rsid: str
    protein_change: str
    observed_genotype: str | None = None
    called: bool
    reduced_activity_alleles: int | None = None


class BcheResponse(BaseModel):
    """Context-only BChE succinylcholine/mivacurium apnea-risk summary."""

    variants: list[BcheVariantResponse]
    any_called: bool
    coverage_complete: bool
    risk_category: str | None = None
    phenotype: str | None = None
    detail: str
    context_only: bool
    note: str
    pmid_citations: list[str] = []


@router.get("", response_model=BcheResponse)
def get_bche(
    sample_id: int = Query(..., description="Sample ID"),
) -> BcheResponse:
    """BChE (succinylcholine/mivacurium) prolonged-apnea risk context for the sample.

    Combines the atypical and K variants into a single deficiency-risk category
    (``high`` / ``intermediate`` / ``mild`` / ``typical`` / ``indeterminate`` when a
    K allele is seen but the major-determinant atypical variant was not callable;
    ``null`` if neither variant is callable). This is interpretive background only —
    never a diagnosis
    and never a change to any finding. BChE deficiency is confirmed by an
    enzyme-activity (dibucaine-number) assay.
    """
    sample_engine = resolve_sample_engine(sample_id)
    return BcheResponse(**assess_bche(sample_engine))

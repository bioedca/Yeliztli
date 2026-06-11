"""VKORC1 + CYP4F2 warfarin-dosing context API — SW-E1 warfarin layer / roadmap #13.

A read-only, context-only summary of the two array-callable warfarin-dosing genes
(VKORC1 c.-1639G>A, CYP4F2 *3) for a sample — see ``backend.analysis.warfarin``.
Additive only: it never changes a finding's evidence level or ClinVar significance,
emits no milligram dose (CPIC dosing requires a validated algorithm with CYP2C9
plus clinical factors and INR monitoring), and writes nothing back.

GET /api/analysis/warfarin?sample_id=N
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from backend.analysis.warfarin import assess_warfarin
from backend.api.dependencies import require_fresh_sample
from backend.api.routes.risk_common import resolve_sample_engine

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/analysis/warfarin",
    tags=["warfarin"],
    dependencies=[Depends(require_fresh_sample)],
)


class WarfarinGeneResponse(BaseModel):
    """One warfarin-dosing gene's context-only assessment."""

    gene: str
    rsid: str
    variant: str
    observed_genotype: str | None = None
    called: bool
    diplotype: str | None = None
    phenotype: str | None = None
    dose_effect: str | None = None
    detail: str


class WarfarinResponse(BaseModel):
    """Context-only VKORC1 + CYP4F2 warfarin-dosing summary for a sample."""

    genes: list[WarfarinGeneResponse]
    any_called: bool
    context_only: bool
    note: str
    pmid_citations: list[str] = []


@router.get("", response_model=WarfarinResponse)
def get_warfarin(
    sample_id: int = Query(..., description="Sample ID"),
) -> WarfarinResponse:
    """VKORC1 + CYP4F2 warfarin dose-effect context for the sample.

    Each gene reports its direction of effect on the warfarin dose requirement
    (VKORC1 A allele → lower dose / higher sensitivity; CYP4F2 *3 → modestly higher
    dose). This is interpretive background only — never a dose and never a change to
    any finding. Uncalled variants return ``called=false``.
    """
    sample_engine = resolve_sample_engine(sample_id)
    return WarfarinResponse(**assess_warfarin(sample_engine))

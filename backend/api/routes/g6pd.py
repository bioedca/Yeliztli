"""G6PD deficiency X-linked pharmacogenomic context API — SW-E6.

A read-only, context-only, sex-aware summary of the array-typeable G6PD
deficiency variants for a sample — see ``backend.analysis.g6pd``. Additive only:
it emits no diagnosis, changes no finding's evidence level or ClinVar
significance, and writes nothing back.

GET /api/analysis/g6pd?sample_id=N
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from backend.analysis.g6pd import assess_g6pd
from backend.api.dependencies import require_fresh_sample
from backend.api.routes.risk_common import resolve_sample_engine

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/analysis/g6pd",
    tags=["g6pd"],
    dependencies=[Depends(require_fresh_sample)],
)


class G6pdVariantResponse(BaseModel):
    """One array-typeable G6PD deficiency variant."""

    name: str
    rsid: str
    cdna: str
    observed_genotype: str | None = None
    called: bool
    deficiency_alleles: int | None = None


class G6pdResponse(BaseModel):
    """Context-only, sex-aware G6PD deficiency summary for a sample."""

    inferred_sex: str
    variants: list[G6pdVariantResponse]
    any_called: bool
    phenotype: str
    detail: str
    at_risk: bool
    a_plus_nondeficient_present: bool
    high_risk_drugs: list[str] = []
    context_only: bool
    note: str
    pmid_citations: list[str] = []


@router.get("", response_model=G6pdResponse)
def get_g6pd(
    sample_id: int = Query(..., description="Sample ID"),
) -> G6pdResponse:
    """Sex-aware G6PD deficiency context for the sample.

    ``phenotype`` is ``normal`` / ``variable`` / ``phase_indeterminate`` /
    ``deficient`` / ``indeterminate``. A heterozygous female is reported as
    ``variable`` (X-inactivation gives a wide activity range — never reassuring
    "normal"); an XX sample heterozygous at two *different* deficiency loci is
    ``phase_indeterminate`` (an array cannot phase trans compound-het vs cis, which
    differ in phenotype — variable-or-deficient); when biological sex cannot be
    inferred, a zygosity-dependent phenotype is withheld (``indeterminate``). This
    is interpretive background only — never a diagnosis and never a change to any
    finding. G6PD status is confirmed by an enzyme-activity assay.
    """
    sample_engine = resolve_sample_engine(sample_id)
    return G6pdResponse(**assess_g6pd(sample_engine))

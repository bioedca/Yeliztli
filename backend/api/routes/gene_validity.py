"""ClinGen gene-disease validity guardrail API — SW-A11 / roadmap #14.

A read-only guardrail flag (ClinGen gene-disease validity; Strande 2017) for
every actionable ClinVar Pathogenic / Likely-pathogenic finding. Additive only —
it never changes a finding's evidence level or ClinVar significance and writes
nothing back to the ``findings`` table (see ``backend.analysis.gene_validity``).
The companion Weedon array-reliability half of SW-A11 lives at
``GET /api/analysis/array-confidence``.

GET /api/analysis/gene-validity?sample_id=N
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from backend.analysis.gene_validity import assess_finding_gene_validity
from backend.api.dependencies import require_fresh_sample
from backend.api.routes.risk_common import resolve_sample_engine
from backend.db.connection import get_registry

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/analysis/gene-validity",
    tags=["gene-validity"],
    dependencies=[Depends(require_fresh_sample)],
)


class GeneValidityCuration(BaseModel):
    """One ClinGen gene-disease validity curation."""

    disease_label: str
    disease_id: str | None = None
    moi: str | None = None
    sop: str | None = None
    classification: str
    classification_date: str | None = None
    gcep: str | None = None
    report_url: str | None = None


class GeneValidityResponse(BaseModel):
    """Gene-disease validity guardrail for one ClinVar P/LP finding."""

    finding_id: int
    module: str
    gene_symbol: str | None = None
    rsid: str | None = None
    clinvar_significance: str | None = None
    finding_text: str
    disease_context: str | None = None
    disease_context_match: str | None = None
    matched_disease_label: str | None = None
    has_clingen_curation: bool
    best_classification: str | None = None
    validity_established: bool
    caution: bool
    label: str
    detail: str
    curations: list[GeneValidityCuration] = []
    context_only: bool
    note: str
    pmid_citations: list[str] = []


@router.get("", response_model=list[GeneValidityResponse])
def list_gene_validity(
    sample_id: int = Query(..., description="Sample ID"),
) -> list[GeneValidityResponse]:
    """Gene-disease validity guardrail for every ClinVar P/LP finding in the sample.

    ``caution`` is true when the finding's matched ClinGen gene-disease relationship
    is not Moderate-or-stronger, when no curation matches the finding's disease
    context, or when mixed disease-specific curations cannot be resolved to this
    finding. Genes ClinGen has not curated return ``has_clingen_curation=false``
    with ``caution=false`` (absence of curation is not evidence either way).
    """
    sample_engine = resolve_sample_engine(sample_id)
    reference_engine = get_registry().reference_engine
    return [
        GeneValidityResponse(**item)
        for item in assess_finding_gene_validity(sample_engine, reference_engine)
    ]

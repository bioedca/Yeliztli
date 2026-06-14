"""Array-genotyping confidence API — EXPANSION_STRATEGY second-wave SW-A11 / #14.

A read-only reliability flag (Weedon 2021 PPV-by-allele-frequency) for every
actionable ClinVar Pathogenic / Likely-pathogenic finding. Additive only — it
never changes a finding's evidence level or ClinVar significance and writes
nothing back to the ``findings`` table (see ``backend.analysis.array_confidence``).

GET /api/analysis/array-confidence?sample_id=N
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from backend.analysis.array_confidence import assess_pathogenic_findings
from backend.api.dependencies import require_fresh_sample
from backend.api.routes.risk_common import resolve_sample_engine

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/analysis/array-confidence",
    tags=["array-confidence"],
    dependencies=[Depends(require_fresh_sample)],
)


class ArrayConfidenceResponse(BaseModel):
    """Reliability flag for one ClinVar P/LP finding."""

    finding_id: int
    module: str
    gene_symbol: str | None = None
    rsid: str | None = None
    clinvar_significance: str | None = None
    finding_text: str
    reliability: str
    label: str
    detail: str
    gnomad_af_popmax: float | None = None
    is_novel: bool
    confirm_in_clia_recommended: bool
    context_only: bool
    pmid_citations: list[str] = []
    note: str
    # Locus-specific weak-spot override (#636): True when ``reliability`` was set
    # to ``locus_low`` by rsID regardless of frequency; ``frequency_band`` then
    # records what allele frequency alone would have rated the call.
    locus_low_reliability: bool = False
    frequency_band: str | None = None


@router.get("", response_model=list[ArrayConfidenceResponse])
def list_array_confidence(
    sample_id: int = Query(..., description="Sample ID"),
) -> list[ArrayConfidenceResponse]:
    """Reliability flag for every ClinVar P/LP finding in the sample.

    Every returned finding is ClinVar-catalogued by definition, so ``is_novel``
    is always ``False`` and reliability is one of ``high`` / ``moderate`` /
    ``low`` / ``unknown`` (never ``very_low``), or ``locus_low`` for a finding at
    a locus-specific array weak spot (#636) regardless of its frequency.

    For a ``locus_low`` finding, ``locus_low_reliability`` is ``True`` and
    ``frequency_band`` records the band the variant's allele frequency alone would
    have produced (e.g. ``high`` for the common APOE ε-SNPs), so the override is
    transparent; for every other finding ``locus_low_reliability`` is ``False``
    and ``frequency_band`` is ``null``.
    """
    engine = resolve_sample_engine(sample_id)
    return [ArrayConfidenceResponse(**item) for item in assess_pathogenic_findings(engine)]

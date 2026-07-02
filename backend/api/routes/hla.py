"""HLA / HIBAG API (Wave D).

- ``GET /api/hla/status`` (SW-D1): whether the operator-installed HIBAG HLA-
  imputation runtime is usable (``Rscript`` present + a BYO ancestry model), so the
  UI can choose between full HIBAG calls and the single-tag HLA proxy fallback.
  Runtime/config status, not sample-specific.
- ``GET /api/hla/drug-hypersensitivity?sample_id=N`` (SW-D2): per-drug
  hypersensitivity-risk assessments from a sample's imputed classical-HLA calls
  (well-established CPIC-grade HLA–drug contraindications). Imputed, not typed —
  every response carries the confirmatory-typing caveat.
"""

from __future__ import annotations

import sqlalchemy as sa
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from backend.analysis.hibag_runner import hibag_runtime_status
from backend.analysis.hla_drug_hypersensitivity import assess_drug_hypersensitivity
from backend.analysis.hla_resolver import read_hla_calls
from backend.db.connection import get_registry
from backend.db.tables import samples

router = APIRouter(prefix="/hla", tags=["hla"])


class HibagStatusResponse(BaseModel):
    """HIBAG runtime availability."""

    rscript_available: bool
    model_dir_configured: bool
    ancestry_models: list[str]
    available: bool


@router.get("/status", response_model=HibagStatusResponse)
async def get_hla_status() -> HibagStatusResponse:
    """Report HIBAG HLA-engine availability (Rscript + BYO ancestry models)."""
    settings = get_registry().settings
    status = hibag_runtime_status(settings.hibag_rscript, settings.hibag_model_dir)
    return HibagStatusResponse(
        rscript_available=status.rscript_available,
        model_dir_configured=status.model_dir is not None,
        ancestry_models=status.ancestry_models,
        available=status.available,
    )


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


class DrugRiskAssessmentResponse(BaseModel):
    """The sample's assessment against one HLA drug-hypersensitivity association."""

    allele: str
    drugs: list[str]
    reaction: str
    status: str  # at_risk | no_risk_allele | not_typed
    carried: bool
    zygosity: str | None
    copies: int
    prob: float | None
    low_confidence: bool
    recommendation: str
    guideline: str
    citations: list[str]
    notes: list[str]


class DrugHypersensitivityResponse(BaseModel):
    """HLA drug-hypersensitivity report for a sample."""

    available: bool
    any_at_risk: bool
    assessments: list[DrugRiskAssessmentResponse] = []
    caveat: str = ""
    unavailable_note: str | None = None
    research_use_only: bool = True


@router.get("/drug-hypersensitivity", response_model=DrugHypersensitivityResponse)
def get_hla_drug_hypersensitivity(
    sample_id: int = Query(..., description="Sample ID"),
) -> DrugHypersensitivityResponse:
    """Assess a sample's imputed HLA calls for drug-hypersensitivity contraindications."""
    engine = _get_sample_engine(sample_id)
    report = assess_drug_hypersensitivity(read_hla_calls(engine))
    return DrugHypersensitivityResponse(
        available=report.available,
        any_at_risk=report.any_at_risk,
        assessments=[DrugRiskAssessmentResponse(**vars(a)) for a in report.assessments],
        caveat=report.caveat,
        unavailable_note=report.unavailable_note,
    )

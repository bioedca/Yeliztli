"""Imputation reachability API (SW-C4 / roadmap #47).

Reports a sample's imputation feasibility/reachability — which of its typed
markers sit on panel-covered chromosomes, the typed-backbone density per
chromosome, and how many sites a run actually recovered — from
:func:`backend.analysis.imputation_reachability.summarize_sample_reachability`.

GET /api/imputation/reachability?sample_id=N
"""

from __future__ import annotations

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from backend.analysis.imputation_reachability import summarize_sample_reachability
from backend.api.dependencies import require_fresh_sample
from backend.db.connection import get_registry
from backend.db.tables import samples

router = APIRouter(
    prefix="/imputation",
    tags=["imputation"],
    dependencies=[Depends(require_fresh_sample)],
)


class ChromReachabilityResponse(BaseModel):
    """Typed-backbone density on one panel-covered chromosome."""

    chrom: str
    typed_markers: int
    median_gap_bp: int | None = None


class ReachabilityResponse(BaseModel):
    """A sample's imputation reachability/feasibility report."""

    panel_version: str
    panel_build: str
    panel_chromosomes: list[str]
    typed_total: int
    typed_on_panel: int
    typed_off_panel: int
    imputation_run: bool
    imputed_reachable: int
    per_chromosome: list[ChromReachabilityResponse]


def _get_sample_engine(sample_id: int) -> sa.Engine:
    """Look up a sample and return its engine (mirrors the findings route helper)."""
    registry = get_registry()
    with registry.reference_engine.connect() as conn:
        row = conn.execute(
            sa.select(samples.c.db_path).where(samples.c.id == sample_id)
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Sample not found")
    sample_db_full = registry.settings.data_dir / row.db_path
    if not sample_db_full.exists():
        raise HTTPException(status_code=404, detail="Sample database file not found")
    return registry.get_sample_engine(sample_db_full)


@router.get("/reachability", response_model=ReachabilityResponse)
async def get_reachability(
    sample_id: int = Query(..., description="Sample ID"),
) -> ReachabilityResponse:
    """Report a sample's imputation reachability from panel coverage + backbone density."""
    engine = _get_sample_engine(sample_id)
    summary = summarize_sample_reachability(engine)
    return ReachabilityResponse(
        panel_version=summary.panel_version,
        panel_build=summary.panel_build,
        panel_chromosomes=summary.panel_chromosomes,
        typed_total=summary.typed_total,
        typed_on_panel=summary.typed_on_panel,
        typed_off_panel=summary.typed_off_panel,
        imputation_run=summary.imputation_run,
        imputed_reachable=summary.imputed_reachable,
        per_chromosome=[
            ChromReachabilityResponse(
                chrom=c.chrom,
                typed_markers=c.typed_markers,
                median_gap_bp=c.median_gap_bp,
            )
            for c in summary.per_chromosome
        ],
    )

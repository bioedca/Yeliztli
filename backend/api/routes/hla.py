"""HLA / HIBAG runtime-status API (Wave D / SW-D1, roadmap #17).

Reports whether the operator-installed HIBAG HLA-imputation runtime is usable —
``Rscript`` present and at least one BYO ancestry model available — so the UI can
decide between full HIBAG calls and the single-tag HLA proxy fallback. This is a
runtime/config status (not sample-specific); it never runs the classifier.

GET /api/hla/status
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from backend.analysis.hibag_runner import hibag_runtime_status
from backend.db.connection import get_registry

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

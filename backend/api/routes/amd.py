"""Age-related macular degeneration (AMD) findings API — EXPANSION_STRATEGY.md #26.

GET  /api/analysis/amd/disclaimer
GET  /api/analysis/amd/findings?sample_id=N
POST /api/analysis/amd/run?sample_id=N
"""

from __future__ import annotations

import sqlalchemy as sa

from backend.api.routes.risk_common import make_risk_router
from backend.disclaimers import AMD_DISCLAIMER_TEXT, AMD_DISCLAIMER_TITLE


def _runner(sample_engine: sa.Engine) -> tuple[int, list[str], dict[str, str]]:
    from backend.analysis.amd import assess_amd, load_amd_panel, store_amd_findings

    panel = load_amd_panel()
    assessment = assess_amd(panel, sample_engine)
    count = store_amd_findings(assessment, sample_engine)
    return count, assessment.indeterminate_loci, assessment.indeterminate_reasons


router = make_risk_router(
    module="amd",
    prefix="/analysis/amd",
    tags=["amd"],
    disclaimer_title=AMD_DISCLAIMER_TITLE,
    disclaimer_text=AMD_DISCLAIMER_TEXT,
    runner=_runner,
)

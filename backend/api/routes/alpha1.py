"""Alpha-1 antitrypsin deficiency findings API — EXPANSION_STRATEGY.md #25.

GET  /api/analysis/alpha1/disclaimer
GET  /api/analysis/alpha1/findings?sample_id=N
POST /api/analysis/alpha1/run?sample_id=N
"""

from __future__ import annotations

import sqlalchemy as sa

from backend.api.routes.risk_common import make_risk_router
from backend.disclaimers import ALPHA1_DISCLAIMER_TEXT, ALPHA1_DISCLAIMER_TITLE


def _runner(sample_engine: sa.Engine) -> tuple[int, list[str], dict[str, str]]:
    from backend.analysis.alpha1 import (
        assess_alpha1,
        load_alpha1_panel,
        store_alpha1_findings,
    )

    panel = load_alpha1_panel()
    assessment = assess_alpha1(panel, sample_engine)
    count = store_alpha1_findings(assessment, sample_engine)
    return count, assessment.indeterminate_loci, assessment.indeterminate_reasons


router = make_risk_router(
    module="alpha1",
    prefix="/analysis/alpha1",
    tags=["alpha1"],
    disclaimer_title=ALPHA1_DISCLAIMER_TITLE,
    disclaimer_text=ALPHA1_DISCLAIMER_TEXT,
    runner=_runner,
)

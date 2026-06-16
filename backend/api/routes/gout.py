"""Gout / serum-urate findings API — EXPANSION_STRATEGY.md #43.

GET  /api/analysis/gout/disclaimer
GET  /api/analysis/gout/findings?sample_id=N
POST /api/analysis/gout/run?sample_id=N
"""

from __future__ import annotations

import sqlalchemy as sa

from backend.api.routes.risk_common import make_risk_router
from backend.disclaimers import GOUT_DISCLAIMER_TEXT, GOUT_DISCLAIMER_TITLE


def _runner(sample_engine: sa.Engine) -> tuple[int, list[str], dict[str, str]]:
    from backend.analysis.gout import assess_gout, load_gout_panel, store_gout_findings

    panel = load_gout_panel()
    assessment = assess_gout(panel, sample_engine)
    count = store_gout_findings(assessment, sample_engine)
    return count, assessment.indeterminate_loci, assessment.indeterminate_reasons


router = make_risk_router(
    module="gout",
    prefix="/analysis/gout",
    tags=["gout"],
    disclaimer_title=GOUT_DISCLAIMER_TITLE,
    disclaimer_text=GOUT_DISCLAIMER_TEXT,
    runner=_runner,
)

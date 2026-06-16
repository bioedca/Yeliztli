"""APOL1 kidney-risk findings API — EXPANSION_STRATEGY.md #27.

GET  /api/analysis/apol1/disclaimer
GET  /api/analysis/apol1/findings?sample_id=N
POST /api/analysis/apol1/run?sample_id=N
"""

from __future__ import annotations

import sqlalchemy as sa

from backend.api.routes.risk_common import make_risk_router
from backend.disclaimers import APOL1_DISCLAIMER_TEXT, APOL1_DISCLAIMER_TITLE


def _runner(sample_engine: sa.Engine) -> tuple[int, list[str], dict[str, str]]:
    from backend.analysis.apol1 import assess_apol1, load_apol1_panel, store_apol1_findings

    panel = load_apol1_panel()
    assessment = assess_apol1(panel, sample_engine)
    count = store_apol1_findings(assessment, sample_engine)
    return count, assessment.indeterminate_loci, assessment.indeterminate_reasons


router = make_risk_router(
    module="apol1",
    prefix="/analysis/apol1",
    tags=["apol1"],
    disclaimer_title=APOL1_DISCLAIMER_TITLE,
    disclaimer_text=APOL1_DISCLAIMER_TEXT,
    runner=_runner,
)

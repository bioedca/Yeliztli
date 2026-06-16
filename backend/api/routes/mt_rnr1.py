"""MT-RNR1 aminoglycoside-ototoxicity findings API — EXPANSION_STRATEGY.md #55.

GET  /api/analysis/mt-rnr1/disclaimer
GET  /api/analysis/mt-rnr1/findings?sample_id=N
POST /api/analysis/mt-rnr1/run?sample_id=N
"""

from __future__ import annotations

import sqlalchemy as sa

from backend.api.routes.risk_common import make_risk_router
from backend.disclaimers import MT_RNR1_DISCLAIMER_TEXT, MT_RNR1_DISCLAIMER_TITLE


def _runner(sample_engine: sa.Engine) -> tuple[int, list[str], dict[str, str]]:
    from backend.analysis.mt_rnr1 import (
        assess_mt_rnr1,
        load_mt_rnr1_panel,
        store_mt_rnr1_findings,
    )

    panel = load_mt_rnr1_panel()
    assessment = assess_mt_rnr1(panel, sample_engine)
    count = store_mt_rnr1_findings(assessment, sample_engine)
    return count, assessment.indeterminate_loci, assessment.indeterminate_reasons


router = make_risk_router(
    module="mt_rnr1",
    prefix="/analysis/mt-rnr1",
    tags=["mt_rnr1"],
    disclaimer_title=MT_RNR1_DISCLAIMER_TITLE,
    disclaimer_text=MT_RNR1_DISCLAIMER_TEXT,
    runner=_runner,
)

"""LHON (Leber hereditary optic neuropathy) findings API — EXPANSION_STRATEGY.md #50.

GET  /api/analysis/lhon/disclaimer
GET  /api/analysis/lhon/findings?sample_id=N
POST /api/analysis/lhon/run?sample_id=N
"""

from __future__ import annotations

import sqlalchemy as sa

from backend.api.routes.risk_common import make_risk_router
from backend.disclaimers import LHON_DISCLAIMER_TEXT, LHON_DISCLAIMER_TITLE


def _runner(sample_engine: sa.Engine) -> tuple[int, list[str], dict[str, str]]:
    from backend.analysis.lhon import assess_lhon, load_lhon_panel, store_lhon_findings

    panel = load_lhon_panel()
    assessment = assess_lhon(panel, sample_engine)
    count = store_lhon_findings(assessment, sample_engine)
    return count, assessment.indeterminate_loci, assessment.indeterminate_reasons


router = make_risk_router(
    module="lhon",
    prefix="/analysis/lhon",
    tags=["lhon"],
    disclaimer_title=LHON_DISCLAIMER_TITLE,
    disclaimer_text=LHON_DISCLAIMER_TEXT,
    runner=_runner,
)

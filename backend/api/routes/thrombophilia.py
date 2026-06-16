"""Inherited thrombophilia findings API — EXPANSION_STRATEGY.md #24.

GET  /api/analysis/thrombophilia/disclaimer
GET  /api/analysis/thrombophilia/findings?sample_id=N
POST /api/analysis/thrombophilia/run?sample_id=N
"""

from __future__ import annotations

import sqlalchemy as sa

from backend.api.routes.risk_common import make_risk_router
from backend.disclaimers import (
    THROMBOPHILIA_DISCLAIMER_TEXT,
    THROMBOPHILIA_DISCLAIMER_TITLE,
)


def _runner(sample_engine: sa.Engine) -> tuple[int, list[str], dict[str, str]]:
    from backend.analysis.thrombophilia import (
        assess_thrombophilia,
        load_thrombophilia_panel,
        store_thrombophilia_findings,
    )

    panel = load_thrombophilia_panel()
    assessment = assess_thrombophilia(panel, sample_engine)
    count = store_thrombophilia_findings(assessment, sample_engine)
    return count, assessment.indeterminate_loci, assessment.indeterminate_reasons


router = make_risk_router(
    module="thrombophilia",
    prefix="/analysis/thrombophilia",
    tags=["thrombophilia"],
    disclaimer_title=THROMBOPHILIA_DISCLAIMER_TITLE,
    disclaimer_text=THROMBOPHILIA_DISCLAIMER_TEXT,
    runner=_runner,
)

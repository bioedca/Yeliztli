"""Runs-of-Homozygosity (ROH / FROH) findings API — EXPANSION_STRATEGY.md #29.

GET  /api/analysis/roh/disclaimer
GET  /api/analysis/roh/findings?sample_id=N
POST /api/analysis/roh/run?sample_id=N
"""

from __future__ import annotations

import json
import logging
from typing import Any

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from backend.analysis.pharmacogenomics import is_patient_presentable_finding_payload
from backend.analysis.roh import (
    CATEGORY,
    DETAIL_UNAVAILABLE,
    INSUFFICIENT_AUTOSOMAL_MARKERS,
    MODULE,
    evaluability_from_detail,
    unevaluable_text,
)
from backend.api.dependencies import require_fresh_sample
from backend.api.routes.risk_common import resolve_sample_engine
from backend.db.tables import findings
from backend.disclaimers import ROH_DISCLAIMER_TEXT, ROH_DISCLAIMER_TITLE

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/analysis/roh", tags=["roh"])


class RohSegmentResponse(BaseModel):
    chrom: str
    start: int
    end: int
    length_kb: float
    n_snps: int


class RohFindingResponse(BaseModel):
    finding_text: str
    # ``None`` whenever autozygosity could not be assessed. Never 0.0 in that
    # case: an unmeasurable genome must not be served as a measured absence.
    froh: float | None = None
    # Withheld alongside FROH: "0 kb total, 0 segments" is the same measured
    # absence stated in another field.
    total_roh_kb: float | None = None
    longest_kb: float | None = None
    n_segments: int | None = None
    autosomal_snps_used: int = 0
    evaluable: bool = True
    indeterminate_reason: str | None = None
    segments: list[RohSegmentResponse] = []
    segments_truncated: bool = False


class RohDisclaimerResponse(BaseModel):
    title: str
    text: str


class RohRunResponse(BaseModel):
    findings_count: int


@router.get("/disclaimer")
def get_disclaimer() -> RohDisclaimerResponse:
    return RohDisclaimerResponse(title=ROH_DISCLAIMER_TITLE, text=ROH_DISCLAIMER_TEXT)


@router.get("/findings", dependencies=[Depends(require_fresh_sample)])
def list_findings(
    sample_id: int = Query(..., description="Sample ID"),
) -> RohFindingResponse | None:
    engine = resolve_sample_engine(sample_id)
    with engine.connect() as conn:
        row = conn.execute(
            sa.select(findings).where(findings.c.module == MODULE, findings.c.category == CATEGORY)
        ).fetchone()
    if row is None or not is_patient_presentable_finding_payload(row._mapping):
        return None
    # Parse detail_json and build the response defensively: a malformed or
    # schema-drifted detail blob (e.g. a row written by an older version, or an
    # unexpected segment shape) must not 500 — fall back to the plain
    # finding_text with zeroed metrics.
    try:
        parsed = json.loads(row.detail_json) if row.detail_json else {}
        # json.loads returns whatever JSON type was stored, so a drifted or
        # hand-edited row holding `[]`, `5` or `"text"` parses to a list, int or
        # str — and the `dict[str, Any]` annotation below would be a lie. Only a
        # JSON object can carry readable state; anything else is normalised to
        # {} so `evaluability_from_detail` classifies it as detail_unavailable.
        # This must happen at the parse site rather than at each use: the metric
        # expressions are guarded by `if evaluable` and so never call `.get`,
        # but `segments_truncated` evaluates `.get` *before* its `and evaluable`
        # short-circuits, and AttributeError is not in the except tuple below —
        # so a non-object blob would 500 instead of withholding, which is the
        # one outcome this route exists to prevent.
        detail: dict[str, Any] = parsed if isinstance(parsed, dict) else {}
        # One shared rule, also used by the unified findings API and the report
        # generator, so a pre-gate row reclassifies identically on every path.
        evaluable, snps_used, reason = evaluability_from_detail(detail, engine)
        return RohFindingResponse(
            # The resolved reason is forwarded, never defaulted: a legacy row
            # resolves to insufficient_autosomal_markers and must not be served
            # with the marker-region wording.
            finding_text=(row.finding_text or "")
            if evaluable
            else unevaluable_text(snps_used, reason or INSUFFICIENT_AUTOSOMAL_MARKERS),
            # No defaults anywhere: an evaluable row is guaranteed a stored
            # numeric FROH by evaluability_from_detail, and the companion
            # metrics are served only if they were actually recorded. A legacy
            # blob holding just `froh` must report the rest as null rather than
            # 0.0 kb / 0 segments, which would state three measurements nothing
            # took — the same substitution, one field over.
            froh=detail.get("froh") if evaluable else None,
            total_roh_kb=detail.get("total_roh_kb") if evaluable else None,
            longest_kb=detail.get("longest_kb") if evaluable else None,
            n_segments=detail.get("n_segments") if evaluable else None,
            autosomal_snps_used=snps_used,
            evaluable=evaluable,
            indeterminate_reason=reason,
            segments=[RohSegmentResponse(**s) for s in detail.get("segments", [])]
            if evaluable
            else [],
            segments_truncated=bool(detail.get("segments_truncated", False)) and evaluable,
        )
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        logger.warning("Failed to build ROH response for finding id=%s: %s", row.id, exc)
        # The metrics could not be read, so none are asserted — in particular
        # froh stays None rather than defaulting to a reassuring 0.0. The stored
        # narrative is replaced too: serving "typical result" beside
        # evaluable=false would state both at once.
        return RohFindingResponse(
            finding_text=unevaluable_text(0, DETAIL_UNAVAILABLE),
            evaluable=False,
            indeterminate_reason=DETAIL_UNAVAILABLE,
        )


@router.post("/run", dependencies=[Depends(require_fresh_sample)])
def run(sample_id: int = Query(..., description="Sample ID")) -> RohRunResponse:
    from backend.analysis.roh import detect_roh, store_roh_findings

    engine = resolve_sample_engine(sample_id)
    result = detect_roh(engine)
    count = store_roh_findings(result, engine)
    return RohRunResponse(findings_count=count)

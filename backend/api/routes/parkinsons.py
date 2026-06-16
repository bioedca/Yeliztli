"""Parkinson's (LRRK2 G2019S) findings API with an APOE-style ethical gate — #41.

Findings are blocked until the user acknowledges the disclosure gate (state
persisted per-sample in the ``parkinsons_gate`` table), mirroring the APOE gate.

GET  /api/analysis/parkinsons/disclaimer            — gate disclosure text
GET  /api/analysis/parkinsons/gate-status?sample_id=N
POST /api/analysis/parkinsons/acknowledge-gate?sample_id=N
GET  /api/analysis/parkinsons/findings?sample_id=N  — gate-protected
POST /api/analysis/parkinsons/run?sample_id=N
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from backend.api.dependencies import require_fresh_sample
from backend.api.routes.risk_common import (
    RiskFindingResponse,
    RiskFindingsListResponse,
    fetch_risk_findings,
    resolve_sample_engine,
)
from backend.db.tables import parkinsons_gate
from backend.disclaimers import (
    PARKINSONS_GATE_ACCEPT_LABEL,
    PARKINSONS_GATE_DECLINE_LABEL,
    PARKINSONS_GATE_TEXT,
    PARKINSONS_GATE_TITLE,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/analysis/parkinsons", tags=["parkinsons"])

MODULE = "parkinsons"


class GateDisclaimerResponse(BaseModel):
    title: str
    text: str
    accept_label: str
    decline_label: str


class GateStatusResponse(BaseModel):
    acknowledged: bool
    acknowledged_at: str | None = None


class GateAcknowledgeResponse(BaseModel):
    acknowledged: bool
    acknowledged_at: str


class RunResponse(BaseModel):
    findings_count: int
    indeterminate_loci: list[str]
    indeterminate_reasons: dict[str, str] = {}


def _gate_status(sample_engine: sa.Engine) -> tuple[bool, str | None]:
    with sample_engine.connect() as conn:
        row = conn.execute(
            sa.select(parkinsons_gate.c.acknowledged, parkinsons_gate.c.acknowledged_at).where(
                parkinsons_gate.c.id == 1
            )
        ).fetchone()
    if row is None or not row.acknowledged:
        return False, None
    ack_at = row.acknowledged_at
    if isinstance(ack_at, datetime):
        ack_at = ack_at.isoformat()
    elif ack_at is not None:
        ack_at = str(ack_at)
    return True, ack_at


def _ensure_gate_acknowledged(sample_engine: sa.Engine) -> None:
    acknowledged, _ = _gate_status(sample_engine)
    if not acknowledged:
        raise HTTPException(
            status_code=403,
            detail=(
                "Parkinson's disclosure gate has not been acknowledged. "
                "You must acknowledge the gate before viewing these findings."
            ),
        )


@router.get("/disclaimer")
def get_disclaimer() -> GateDisclaimerResponse:
    return GateDisclaimerResponse(
        title=PARKINSONS_GATE_TITLE,
        text=PARKINSONS_GATE_TEXT,
        accept_label=PARKINSONS_GATE_ACCEPT_LABEL,
        decline_label=PARKINSONS_GATE_DECLINE_LABEL,
    )


@router.get("/gate-status", dependencies=[Depends(require_fresh_sample)])
def get_gate_status(sample_id: int = Query(..., description="Sample ID")) -> GateStatusResponse:
    engine = resolve_sample_engine(sample_id)
    acknowledged, acknowledged_at = _gate_status(engine)
    return GateStatusResponse(acknowledged=acknowledged, acknowledged_at=acknowledged_at)


@router.post("/acknowledge-gate", dependencies=[Depends(require_fresh_sample)])
def acknowledge_gate(
    sample_id: int = Query(..., description="Sample ID"),
) -> GateAcknowledgeResponse:
    engine = resolve_sample_engine(sample_id)
    now = datetime.now(tz=UTC)
    with engine.begin() as conn:
        existing = conn.execute(
            sa.select(parkinsons_gate.c.id).where(parkinsons_gate.c.id == 1)
        ).fetchone()
        if existing is None:
            conn.execute(
                sa.insert(parkinsons_gate).values(id=1, acknowledged=True, acknowledged_at=now)
            )
        else:
            conn.execute(
                sa.update(parkinsons_gate)
                .where(parkinsons_gate.c.id == 1)
                .values(acknowledged=True, acknowledged_at=now)
            )
    logger.info("parkinsons_gate_acknowledged sample_id=%s", sample_id)
    return GateAcknowledgeResponse(acknowledged=True, acknowledged_at=now.isoformat())


@router.get("/findings", dependencies=[Depends(require_fresh_sample)])
def list_findings(
    sample_id: int = Query(..., description="Sample ID"),
) -> RiskFindingsListResponse:
    engine = resolve_sample_engine(sample_id)
    _ensure_gate_acknowledged(engine)
    raw = fetch_risk_findings(engine, MODULE)
    items = [RiskFindingResponse(**f) for f in raw]
    return RiskFindingsListResponse(items=items, total=len(items))


@router.post("/run", dependencies=[Depends(require_fresh_sample)])
def run(sample_id: int = Query(..., description="Sample ID")) -> RunResponse:
    from backend.analysis.parkinsons import (
        assess_parkinsons,
        load_parkinsons_panel,
        store_parkinsons_findings,
    )

    engine = resolve_sample_engine(sample_id)
    panel = load_parkinsons_panel()
    assessment = assess_parkinsons(panel, engine)
    count = store_parkinsons_findings(assessment, engine)
    return RunResponse(
        findings_count=count,
        indeterminate_loci=assessment.indeterminate_loci,
        indeterminate_reasons=assessment.indeterminate_reasons,
    )

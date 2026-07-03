"""Sex-chromosome aneuploidy screen API with an opt-in gate — roadmap #48.

The screen result is blocked until the user acknowledges the disclosure gate
(state persisted per-sample in ``aneuploidy_gate``), mirroring the APOE /
Parkinson's gates — this is a psychosocially sensitive, confirmation-only screen.

GET  /api/analysis/sex-aneuploidy/disclaimer
GET  /api/analysis/sex-aneuploidy/gate-status?sample_id=N
POST /api/analysis/sex-aneuploidy/acknowledge-gate?sample_id=N
GET  /api/analysis/sex-aneuploidy/findings?sample_id=N   — gate-protected
POST /api/analysis/sex-aneuploidy/run?sample_id=N        — compute/store; outcome gated
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from backend.analysis.sex_aneuploidy import (
    CATEGORY,
    MODULE,
    screen_aneuploidy,
    store_aneuploidy_findings,
)
from backend.api.dependencies import require_fresh_sample
from backend.api.routes.risk_common import resolve_sample_engine
from backend.db.tables import aneuploidy_gate, findings
from backend.disclaimers import (
    ANEUPLOIDY_GATE_ACCEPT_LABEL,
    ANEUPLOIDY_GATE_DECLINE_LABEL,
    ANEUPLOIDY_GATE_TEXT,
    ANEUPLOIDY_GATE_TITLE,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/analysis/sex-aneuploidy", tags=["sex_aneuploidy"])


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


class ScreenFindingResponse(BaseModel):
    computed: bool
    outcome: str | None = None
    finding_text: str | None = None
    x_nonpar_typed: int | None = None
    x_nonpar_het: int | None = None
    y_total: int | None = None
    y_rate: float | None = None


class RunResponse(BaseModel):
    """Result of running the sex-aneuploidy screen.

    ``outcome`` is gate-protected because it is the sensitive screen result the
    disclosure gate exists to protect. Running the screen computes and stores
    results, but the outcome is populated only after gate acknowledgment.
    """

    computed: bool
    outcome: str | None = None


def _gate_status(sample_engine: sa.Engine) -> tuple[bool, str | None]:
    with sample_engine.connect() as conn:
        row = conn.execute(
            sa.select(aneuploidy_gate.c.acknowledged, aneuploidy_gate.c.acknowledged_at).where(
                aneuploidy_gate.c.id == 1
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
                "Sex-aneuploidy disclosure gate has not been acknowledged. "
                "You must acknowledge the gate before viewing this screen."
            ),
        )


@router.get("/disclaimer")
def get_disclaimer() -> GateDisclaimerResponse:
    return GateDisclaimerResponse(
        title=ANEUPLOIDY_GATE_TITLE,
        text=ANEUPLOIDY_GATE_TEXT,
        accept_label=ANEUPLOIDY_GATE_ACCEPT_LABEL,
        decline_label=ANEUPLOIDY_GATE_DECLINE_LABEL,
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
            sa.select(aneuploidy_gate.c.id).where(aneuploidy_gate.c.id == 1)
        ).fetchone()
        if existing is None:
            conn.execute(
                sa.insert(aneuploidy_gate).values(id=1, acknowledged=True, acknowledged_at=now)
            )
        else:
            conn.execute(
                sa.update(aneuploidy_gate)
                .where(aneuploidy_gate.c.id == 1)
                .values(acknowledged=True, acknowledged_at=now)
            )
    logger.info("aneuploidy_gate_acknowledged sample_id=%s", sample_id)
    return GateAcknowledgeResponse(acknowledged=True, acknowledged_at=now.isoformat())


@router.get("/findings", dependencies=[Depends(require_fresh_sample)])
def list_findings(sample_id: int = Query(..., description="Sample ID")) -> ScreenFindingResponse:
    engine = resolve_sample_engine(sample_id)
    _ensure_gate_acknowledged(engine)
    with engine.connect() as conn:
        row = conn.execute(
            sa.select(findings).where(findings.c.module == MODULE, findings.c.category == CATEGORY)
        ).fetchone()
    if row is None:
        return ScreenFindingResponse(computed=False)
    detail: dict[str, Any] = {}
    if row.detail_json:
        try:
            detail = json.loads(row.detail_json)
        except (json.JSONDecodeError, TypeError):
            logger.warning("Failed to parse aneuploidy detail_json for id=%s", row.id)
    return ScreenFindingResponse(
        computed=True,
        outcome=detail.get("outcome"),
        finding_text=row.finding_text,
        x_nonpar_typed=detail.get("x_nonpar_typed"),
        x_nonpar_het=detail.get("x_nonpar_het"),
        y_total=detail.get("y_total"),
        y_rate=detail.get("y_rate"),
    )


@router.post("/run", dependencies=[Depends(require_fresh_sample)])
def run(sample_id: int = Query(..., description="Sample ID")) -> RunResponse:
    engine = resolve_sample_engine(sample_id)
    result = screen_aneuploidy(engine)
    store_aneuploidy_findings(result, engine)
    acknowledged, _ = _gate_status(engine)
    return RunResponse(computed=True, outcome=result.outcome if acknowledged else None)

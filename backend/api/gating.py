"""Shared disclosure-gate helpers for the API layer.

The APOE ε4 opt-in gate (see ``backend/api/routes/apoe.py``) protects the
Alzheimer-risk disclosure: APOE ε4 status is itself the sensitive finding the
gate exists to let a user choose whether to learn. Its acknowledgment state is
persisted per sample in the ``apoe_gate`` table.

Any endpoint that can surface APOE findings — including the module-agnostic
aggregators in ``routes/findings.py`` — must consult this gate, or the
disclosure is re-opened via a side route (issue #222). These helpers are the
single source of truth for that check so the gate logic is not duplicated.
"""

from __future__ import annotations

from datetime import datetime

import sqlalchemy as sa

from backend.db.tables import apoe_gate


def apoe_gate_status(sample_engine: sa.Engine) -> tuple[bool, str | None]:
    """Return the APOE gate state for a sample.

    Returns:
        Tuple of ``(acknowledged, acknowledged_at)`` where ``acknowledged_at``
        is an ISO-8601 string (or ``None`` when not acknowledged).
    """
    with sample_engine.connect() as conn:
        row = conn.execute(
            sa.select(apoe_gate.c.acknowledged, apoe_gate.c.acknowledged_at).where(
                apoe_gate.c.id == 1
            )
        ).fetchone()

    if row is None or not row.acknowledged:
        return False, None

    ack_at = row.acknowledged_at
    if ack_at is not None:
        if isinstance(ack_at, datetime):
            ack_at = ack_at.isoformat()
        else:
            ack_at = str(ack_at)
    return True, ack_at


def is_apoe_gate_acknowledged(sample_engine: sa.Engine) -> bool:
    """Return ``True`` iff the APOE disclosure gate is acknowledged for this sample."""
    acknowledged, _ = apoe_gate_status(sample_engine)
    return acknowledged

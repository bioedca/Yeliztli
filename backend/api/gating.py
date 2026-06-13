"""Shared disclosure-gate helpers for the API layer.

Several findings are opt-in *gated*: the finding is itself sensitive enough that
the user chooses whether to learn it, and its acknowledgment state is persisted
per sample in a single-row gate table. The APOE ε4 / Alzheimer-risk gate
(``apoe_gate``, see ``routes/apoe.py``), the sex-chromosome-aneuploidy screen
gate (``aneuploidy_gate``, see ``routes/sex_aneuploidy.py``), and the Parkinson's
LRRK2 G2019S gate (``parkinsons_gate``, see ``routes/parkinsons.py``) are such
gates.

Any endpoint that can surface a gated finding — including the module-agnostic
aggregators in ``routes/findings.py`` — must consult the gate, or the disclosure
is re-opened via a side route (issues #222 APOE, #299 sex-aneuploidy, #298
Parkinson's). These helpers are the single source of truth for those checks so
the gate logic is not duplicated.
"""

from __future__ import annotations

from datetime import datetime

import sqlalchemy as sa

from backend.db.tables import aneuploidy_gate, apoe_gate, parkinsons_gate


def _gate_status(sample_engine: sa.Engine, gate_table: sa.Table) -> tuple[bool, str | None]:
    """Return ``(acknowledged, acknowledged_at)`` for a single-row gate table.

    ``acknowledged_at`` is an ISO-8601 string, or ``None`` when not acknowledged.
    """
    with sample_engine.connect() as conn:
        row = conn.execute(
            sa.select(gate_table.c.acknowledged, gate_table.c.acknowledged_at).where(
                gate_table.c.id == 1
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


def apoe_gate_status(sample_engine: sa.Engine) -> tuple[bool, str | None]:
    """Return the APOE gate state ``(acknowledged, acknowledged_at)`` for a sample."""
    return _gate_status(sample_engine, apoe_gate)


def is_apoe_gate_acknowledged(sample_engine: sa.Engine) -> bool:
    """Return ``True`` iff the APOE disclosure gate is acknowledged for this sample."""
    acknowledged, _ = apoe_gate_status(sample_engine)
    return acknowledged


def aneuploidy_gate_status(sample_engine: sa.Engine) -> tuple[bool, str | None]:
    """Return the sex-aneuploidy gate state ``(acknowledged, acknowledged_at)``."""
    return _gate_status(sample_engine, aneuploidy_gate)


def is_aneuploidy_gate_acknowledged(sample_engine: sa.Engine) -> bool:
    """Return ``True`` iff the sex-aneuploidy disclosure gate is acknowledged."""
    acknowledged, _ = aneuploidy_gate_status(sample_engine)
    return acknowledged


def parkinsons_gate_status(sample_engine: sa.Engine) -> tuple[bool, str | None]:
    """Return the Parkinson's gate state ``(acknowledged, acknowledged_at)``."""
    return _gate_status(sample_engine, parkinsons_gate)


def is_parkinsons_gate_acknowledged(sample_engine: sa.Engine) -> bool:
    """Return ``True`` iff the Parkinson's disclosure gate is acknowledged."""
    acknowledged, _ = parkinsons_gate_status(sample_engine)
    return acknowledged

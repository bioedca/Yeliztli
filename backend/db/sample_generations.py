"""Generation tokens for per-sample tables that are replaced wholesale (#2118).

``annotated_variants`` and ``imputed_variants`` carry no content version, and
hashing them on every read would mean a full scan. Each writer that replaces
one of them stores a fresh token in ``annotation_state`` in the same
transaction, so a reader can tell whether the table changed since it last
looked, including after a run that failed once the table had been swapped.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import sqlalchemy as sa
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from backend.db.tables import annotation_state

ANNOTATED_VARIANTS_GENERATION_KEY = "annotated_variants_generation"
IMPUTED_VARIANTS_GENERATION_KEY = "imputed_variants_generation"


def upsert_state(conn: sa.Connection, key: str, value: str) -> None:
    """Insert or replace one ``annotation_state`` row; the caller owns the transaction."""
    stmt = sqlite_insert(annotation_state).values(key=key, value=value)
    stmt = stmt.on_conflict_do_update(
        index_elements=[annotation_state.c.key],
        set_={"value": stmt.excluded.value, "updated_at": datetime.now(UTC)},
    )
    conn.execute(stmt)


def bump_generation(conn: sa.Connection, key: str) -> None:
    """Store a new random token under ``key``; the caller owns the transaction."""
    upsert_state(conn, key, uuid.uuid4().hex)

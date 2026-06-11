"""Add ``risk_overlay_consent`` opt-in table (SW-B8).

The breast absolute-risk overlay quantifies absolute disease risk, so it is
shown only after explicit, per-sample opt-in. This forward-only additive table
on the reference DB records that consent (one row per sample + feature). It is
also declared in ``backend/db/tables.py`` and created at runtime for fresh DBs
via ``reference_metadata.create_all(checkfirst=True)``; this migration keeps the
Alembic history complete.

Downgrade drops the table.

Revision ID: 012
Revises: 011
Create Date: 2026-06-11
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "012"
down_revision: str = "011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "risk_overlay_consent",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("sample_id", sa.Integer, nullable=False),
        sa.Column(
            "feature",
            sa.Text,
            nullable=False,
            comment="Overlay feature key, e.g. 'breast_absolute_risk'",
        ),
        sa.Column("consented", sa.Integer, nullable=False, server_default="0"),
        # Set only when consent is granted (NULL otherwise) — audit timestamp.
        sa.Column("consented_at", sa.DateTime),
    )
    op.create_index(
        "idx_risk_overlay_consent_sample_feature",
        "risk_overlay_consent",
        ["sample_id", "feature"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("idx_risk_overlay_consent_sample_feature", table_name="risk_overlay_consent")
    op.drop_table("risk_overlay_consent")

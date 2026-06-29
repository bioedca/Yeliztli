"""Add version-staleness metadata to reannotation_prompts.

General reference-data staleness is distinct from ClinVar variant
reclassification. ``prompt_type`` distinguishes those cases, and
``stale_databases`` stores a JSON array of database/version deltas so one
per-sample prompt can summarize every stale source without one prompt per DB.

Revision ID: 013
Revises: 012
Create Date: 2026-06-29
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "013"
down_revision: str = "012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "reannotation_prompts",
        sa.Column(
            "prompt_type",
            sa.Text,
            nullable=False,
            server_default="reclassification",
            comment="reclassification | version_staleness",
        ),
    )
    op.add_column(
        "reannotation_prompts",
        sa.Column(
            "stale_databases",
            sa.Text,
            server_default="[]",
            comment="JSON array of reference DBs newer than the sample annotation snapshot",
        ),
    )


def downgrade() -> None:
    op.drop_column("reannotation_prompts", "stale_databases")
    op.drop_column("reannotation_prompts", "prompt_type")

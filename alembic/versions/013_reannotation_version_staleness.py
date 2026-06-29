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
    with op.batch_alter_table("reannotation_prompts") as batch_op:
        batch_op.add_column(
            sa.Column(
                "prompt_type",
                sa.Text,
                nullable=False,
                server_default="reclassification",
                comment="reclassification | version_staleness",
            ),
        )
        batch_op.add_column(
            sa.Column(
                "stale_databases",
                sa.Text,
                nullable=False,
                server_default="[]",
                comment="JSON array of reference DBs newer than the sample annotation snapshot",
            ),
        )
        batch_op.create_check_constraint(
            "ck_reannotation_prompts_prompt_type",
            "prompt_type IN ('reclassification', 'version_staleness')",
        )


def downgrade() -> None:
    with op.batch_alter_table("reannotation_prompts") as batch_op:
        batch_op.drop_constraint("ck_reannotation_prompts_prompt_type", type_="check")
        batch_op.drop_column("stale_databases")
        batch_op.drop_column("prompt_type")

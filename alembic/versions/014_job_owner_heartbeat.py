"""Give worker-owned jobs an owner, an epoch and a heartbeat.

Status and timestamps could not distinguish an API-only restart -- where the
separate Huey process may still be mutating the sample -- from a worker that
died. Recovery therefore had to choose between releasing a live lease, which
drops the annotation/export interlock mid-write, and preserving it fail-closed,
which leaves a genuinely orphaned job active forever.

``owner_id`` names the process holding the row, ``owner_epoch`` distinguishes one
run of that process from the next, and ``heartbeat_at`` is refreshed while the
owner works, so a stale lease is observable rather than inferred.

All three are nullable: rows written before this migration, and rows nobody owns,
must keep reading correctly. Recovery treats a NULL owner as unclaimed.

Revision ID: 014
Revises: 013
Create Date: 2026-08-05
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "014"
down_revision: str = "013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("jobs") as batch_op:
        batch_op.add_column(sa.Column("owner_id", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("owner_epoch", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("heartbeat_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("jobs") as batch_op:
        batch_op.drop_column("heartbeat_at")
        batch_op.drop_column("owner_epoch")
        batch_op.drop_column("owner_id")

"""Reference database forward-compat schema backfill.

The reference database is bootstrapped at startup via
``reference_metadata.create_all(checkfirst=True)`` (see ``backend/main.py``),
not via Alembic at runtime. ``create_all`` creates missing *tables* but never
adds *columns* to tables that already exist. So when a later schema revision
adds a column to an existing reference table (e.g. ``samples.individual_id``
from Alembic 009 / the AncestryDNA "individuals" grouping), installs whose
table predates that revision are left missing the column and every query that
references it fails with ``no such column``.

``ensure_reference_schema_current()`` closes that gap the same way
``ensure_sample_schema_current()`` does for per-sample databases: it inspects
the live schema and applies additive ``ALTER TABLE ADD COLUMN`` / index DDL for
any column introduced after a table was first created. It is idempotent —
safe to run on every startup — and a no-op on fresh / already-current DBs.
"""

from __future__ import annotations

import sqlalchemy as sa
import structlog

logger = structlog.get_logger(__name__)


def ensure_reference_schema_current(engine: sa.Engine) -> bool:
    """Backfill additive columns/indexes missing from an existing reference DB.

    Must run *after* ``reference_metadata.create_all`` so that any tables the
    backfilled columns reference (e.g. ``individuals``) already exist.

    Args:
        engine: SQLAlchemy engine for the reference database.

    Returns:
        True if any DDL was applied, False if the schema was already current.
    """
    inspector = sa.inspect(engine)
    table_names = set(inspector.get_table_names())
    changed = False

    # ── samples.individual_id (Alembic 009 — AncestryDNA individuals grouping)
    # Nullable FK to individuals(id). SQLite permits ADD COLUMN with a
    # REFERENCES clause when the column's default is NULL, which it is here.
    if "samples" in table_names:
        sample_cols = {c["name"] for c in inspector.get_columns("samples")}
        if "individual_id" not in sample_cols:
            with engine.begin() as conn:
                conn.execute(
                    sa.text(
                        "ALTER TABLE samples ADD COLUMN individual_id INTEGER "
                        "REFERENCES individuals(id)"
                    )
                )
                conn.execute(
                    sa.text(
                        "CREATE INDEX IF NOT EXISTS ix_samples_individual_id "
                        "ON samples (individual_id)"
                    )
                )
            changed = True
            logger.info(
                "reference_schema_backfilled",
                table="samples",
                column="individual_id",
            )

    # ── database_versions.genome_build (Alembic 011 — F30 provenance)
    # Cross-source genome-build column. Nullable TEXT; existing rows keep NULL
    # until each source's version is next recorded (the recorder auto-stamps the
    # build from EXPECTED_GENOME_BUILD).
    if "database_versions" in table_names:
        dv_cols = {c["name"] for c in inspector.get_columns("database_versions")}
        if "genome_build" not in dv_cols:
            with engine.begin() as conn:
                conn.execute(sa.text("ALTER TABLE database_versions ADD COLUMN genome_build TEXT"))
            changed = True
            logger.info(
                "reference_schema_backfilled",
                table="database_versions",
                column="genome_build",
            )

    # ── downloads.validator (PR-15 — durable If-Range across cross-process resume)
    # Nullable TEXT holding the ETag/Last-Modified captured on the first response.
    # Existing in-flight rows keep NULL until their next attempt recaptures it, so
    # the worst case for a pre-existing partial is one extra full restart — never a
    # spliced/corrupt artifact.
    if "downloads" in table_names:
        dl_cols = {c["name"] for c in inspector.get_columns("downloads")}
        if "validator" not in dl_cols:
            with engine.begin() as conn:
                conn.execute(sa.text("ALTER TABLE downloads ADD COLUMN validator TEXT"))
            changed = True
            logger.info(
                "reference_schema_backfilled",
                table="downloads",
                column="validator",
            )

    return changed

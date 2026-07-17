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
any column introduced after a table was first created. Narrow content repairs
also live here when an exact legacy fingerprint can be upgraded without
touching future or custom data. It is idempotent — safe to run on every startup
— and a no-op on fresh / already-current DBs.
"""

from __future__ import annotations

from collections import Counter

import sqlalchemy as sa
import structlog

logger = structlog.get_logger(__name__)

_CYP2C9_PHENYTOIN_GUIDELINE_URL = (
    "https://cpicpgx.org/guidelines/guideline-for-phenytoin-and-cyp2c9/"
)
_LEGACY_CYP2C9_PHENYTOIN_FINGERPRINT = Counter(
    {
        (
            "Normal Metabolizer",
            None,
            "Use label-recommended dosing.",
            "A",
            _CYP2C9_PHENYTOIN_GUIDELINE_URL,
        ): 1,
        (
            "Intermediate Metabolizer",
            None,
            "Reduce dose by 25%. Increase monitoring.",
            "A",
            _CYP2C9_PHENYTOIN_GUIDELINE_URL,
        ): 1,
        (
            "Poor Metabolizer",
            None,
            "Reduce dose by 50%. Consider alternative anticonvulsant.",
            "A",
            _CYP2C9_PHENYTOIN_GUIDELINE_URL,
        ): 1,
    }
)


def _refresh_legacy_cyp2c9_phenytoin_guidelines(engine: sa.Engine) -> bool:
    """Replace only the exact three-row pre-#1989 phenytoin fingerprint.

    Bundled CSV changes do not reload an existing reference database. The exact
    row multiset is therefore the content-migration sentinel: canonical, empty,
    mixed, duplicated, or future/custom data is left untouched.
    """
    inspector = sa.inspect(engine)
    if "cpic_guidelines" not in inspector.get_table_names():
        return False
    columns = {column["name"] for column in inspector.get_columns("cpic_guidelines")}
    required = {
        "gene",
        "drug",
        "phenotype",
        "activity_score",
        "recommendation",
        "classification",
        "guideline_url",
    }
    if not required <= columns:
        return False

    from backend.db.tables import cpic_guidelines

    target = sa.and_(
        cpic_guidelines.c.gene == "CYP2C9",
        sa.func.lower(cpic_guidelines.c.drug) == "phenytoin",
    )

    # Python 3.12/3.13's sqlite3 legacy transaction mode does not begin a DB
    # transaction for SELECT, even inside ``engine.begin()``. Acquire SQLite's
    # reserved write lock explicitly *before* fingerprinting so a concurrent
    # updater cannot commit custom/future guidance between the SELECT and DELETE.
    with engine.connect() as conn:
        conn.exec_driver_sql("BEGIN IMMEDIATE")
        try:
            rows = conn.execute(
                sa.select(
                    cpic_guidelines.c.phenotype,
                    cpic_guidelines.c.activity_score,
                    cpic_guidelines.c.recommendation,
                    cpic_guidelines.c.classification,
                    cpic_guidelines.c.guideline_url,
                ).where(target)
            ).fetchall()
            observed = Counter(
                (
                    row.phenotype,
                    row.activity_score,
                    row.recommendation,
                    row.classification,
                    row.guideline_url,
                )
                for row in rows
            )
            if observed != _LEGACY_CYP2C9_PHENYTOIN_FINGERPRINT:
                conn.rollback()
                return False

            # Only legacy installs need the bundled replacement payload.
            # Keeping this behind the locked fingerprint check lets custom,
            # future, and already-current databases start independently of an
            # irrelevant content migration while retaining strict validation
            # whenever the repair actually applies.
            from backend.annotation.cpic import CPIC_DATA_DIR, parse_cpic_guidelines_csv

            bundled_rows, _ = parse_cpic_guidelines_csv(CPIC_DATA_DIR / "cpic_guidelines.csv")
            canonical_rows = [
                row
                for row in bundled_rows
                if row["gene"] == "CYP2C9" and row["drug"].lower() == "phenytoin"
            ]
            canonical_scores = {row["activity_score"] for row in canonical_rows}
            if len(canonical_rows) != 5 or canonical_scores != {
                2.0,
                1.5,
                1.0,
                0.5,
                0.0,
            }:
                raise RuntimeError("Bundled CYP2C9/phenytoin guideline matrix is not canonical")

            conn.execute(sa.delete(cpic_guidelines).where(target))
            conn.execute(cpic_guidelines.insert(), canonical_rows)
            conn.commit()
        except BaseException:
            conn.rollback()
            raise

    logger.warning(
        "legacy_cyp2c9_phenytoin_guidelines_refreshed",
        removed_rows=len(rows),
        inserted_rows=len(canonical_rows),
    )
    return True


def _remove_orphaned_reannotation_prompts(engine: sa.Engine) -> bool:
    """Remove legacy orphan prompts and rows that provably predate a sample.

    Timestamp ordering cannot identify every prompt left by a prior occupant of
    a reused numeric sample ID: restored samples retain their original creation
    time.  Current deletion and restore paths therefore remove prompts in the
    same transaction that removes or assigns an ID; this startup repair is only
    a best-effort cleanup for legacy state.
    """
    table_names = set(sa.inspect(engine).get_table_names())
    if not {"reannotation_prompts", "samples"} <= table_names:
        return False

    from backend.db.tables import reannotation_prompts, samples

    sample_exists = sa.exists(sa.select(1).where(samples.c.id == reannotation_prompts.c.sample_id))
    current_sample_created_at = (
        sa.select(samples.c.created_at)
        .where(samples.c.id == reannotation_prompts.c.sample_id)
        .scalar_subquery()
    )
    invalid_prompt = sa.or_(
        ~sample_exists,
        reannotation_prompts.c.created_at.is_(None),
        sa.and_(
            current_sample_created_at.is_not(None),
            reannotation_prompts.c.created_at < current_sample_created_at,
        ),
    )
    with engine.begin() as conn:
        result = conn.execute(sa.delete(reannotation_prompts).where(invalid_prompt))
    removed_rows = max(result.rowcount or 0, 0)
    if removed_rows:
        logger.info(
            "orphaned_reannotation_prompts_removed",
            removed_rows=removed_rows,
        )
    return removed_rows > 0


def ensure_reference_schema_current(engine: sa.Engine) -> bool:
    """Backfill missing additive schema and exact-fingerprint content repairs.

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

    # ── cpic_guidelines.activity_score (#1993 — AS-keyed DPYD dosing)
    # Nullable REAL so CPIC recommendations that split by gene activity score
    # (DPYD fluoropyrimidines) can be keyed on the score. Existing rows and
    # phenotype-keyed genes keep NULL; the guideline lookup falls back to the
    # phenotype for NULL rows. A pre-#1993 reference.db that is not rebuilt from
    # the bundled CSV still gets the column so the AS-aware query does not error.
    if "cpic_guidelines" in table_names:
        cg_cols = {c["name"] for c in inspector.get_columns("cpic_guidelines")}
        if "activity_score" not in cg_cols:
            with engine.begin() as conn:
                conn.execute(sa.text("ALTER TABLE cpic_guidelines ADD COLUMN activity_score REAL"))
            changed = True
            logger.info(
                "reference_schema_backfilled",
                table="cpic_guidelines",
                column="activity_score",
            )

        if _refresh_legacy_cyp2c9_phenytoin_guidelines(engine):
            changed = True

    # Releases before the sample-deletion prompt cascade could leave central
    # rows whose numeric sample IDs were later reusable. Remove definite
    # orphans and rows that predate the current occupant; restore/import also
    # clears every prompt for an assigned ID transactionally because timestamps
    # cannot resolve all legacy ownership ambiguity.
    if _remove_orphaned_reannotation_prompts(engine):
        changed = True

    return changed

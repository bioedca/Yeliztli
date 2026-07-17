"""Tests for reference-DB additive-column backfill.

The reference database is bootstrapped via
``reference_metadata.create_all(checkfirst=True)`` (see ``backend/main.py``),
not Alembic at runtime. ``create_all`` creates missing *tables* but never adds
*columns* to pre-existing tables. So a ``samples`` table created before Alembic
009 (which added ``samples.individual_id``) is left without the column, and
every ``SELECT`` against ``samples`` fails with ``no such column``.

``ensure_reference_schema_current()`` backfills such additive columns. These
tests build the exact pre-009 drift (a ``samples`` table missing
``individual_id``, with the ``individuals`` table already present, mirroring a
post-``create_all`` state) and assert the column + index are added, that the
function is idempotent, and that a fresh schema is left untouched.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import sqlalchemy as sa

from backend.db.reference_schema import ensure_reference_schema_current
from backend.db.tables import (
    cpic_guidelines,
    database_versions,
    reannotation_prompts,
    reference_metadata,
    samples,
)

_PHENYTOIN_URL = "https://cpicpgx.org/guidelines/guideline-for-phenytoin-and-cyp2c9/"
_LEGACY_PHENYTOIN_ROWS = [
    {
        "gene": "CYP2C9",
        "drug": "phenytoin",
        "phenotype": "Normal Metabolizer",
        "activity_score": None,
        "recommendation": "Use label-recommended dosing.",
        "classification": "A",
        "guideline_url": _PHENYTOIN_URL,
    },
    {
        "gene": "CYP2C9",
        "drug": "phenytoin",
        "phenotype": "Intermediate Metabolizer",
        "activity_score": None,
        "recommendation": "Reduce dose by 25%. Increase monitoring.",
        "classification": "A",
        "guideline_url": _PHENYTOIN_URL,
    },
    {
        "gene": "CYP2C9",
        "drug": "phenytoin",
        "phenotype": "Poor Metabolizer",
        "activity_score": None,
        "recommendation": "Reduce dose by 50%. Consider alternative anticonvulsant.",
        "classification": "A",
        "guideline_url": _PHENYTOIN_URL,
    },
]


def _columns(engine: sa.Engine, table: str) -> set[str]:
    return {c["name"] for c in sa.inspect(engine).get_columns(table)}


def _make_pre009_samples(engine: sa.Engine) -> None:
    """Create a ``samples`` table lacking ``individual_id`` (pre-Alembic-009)."""
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "CREATE TABLE samples ("
                "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
                "  name TEXT NOT NULL,"
                "  db_path TEXT NOT NULL UNIQUE,"
                "  file_format TEXT,"
                "  file_hash TEXT,"
                "  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,"
                "  updated_at DATETIME"
                ")"
            )
        )
        # The individuals table exists already, as create_all would have made
        # it: a new table create_all *can* add, unlike a new column.
        conn.execute(
            sa.text(
                "CREATE TABLE individuals ("
                "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
                "  display_name TEXT NOT NULL,"
                "  notes TEXT DEFAULT '',"
                "  biological_sex TEXT,"
                "  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,"
                "  updated_at DATETIME"
                ")"
            )
        )


def test_backfills_missing_individual_id(tmp_path: Path) -> None:
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'ref.db'}")
    _make_pre009_samples(engine)

    assert "individual_id" not in _columns(engine, "samples")

    changed = ensure_reference_schema_current(engine)

    assert changed is True
    assert "individual_id" in _columns(engine, "samples")
    # The index that backs the two-level sample selector is created too.
    indexes = {ix["name"] for ix in sa.inspect(engine).get_indexes("samples")}
    assert "ix_samples_individual_id" in indexes


def test_select_with_individual_id_works_after_backfill(tmp_path: Path) -> None:
    """The crash repro: SELECT ... samples.individual_id must stop 500-ing."""
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'ref.db'}")
    _make_pre009_samples(engine)
    ensure_reference_schema_current(engine)

    with engine.connect() as conn:
        rows = conn.execute(sa.text("SELECT id, name, individual_id FROM samples")).fetchall()
    assert rows == []


def test_idempotent_on_second_run(tmp_path: Path) -> None:
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'ref.db'}")
    _make_pre009_samples(engine)

    assert ensure_reference_schema_current(engine) is True
    # Second run finds nothing to do.
    assert ensure_reference_schema_current(engine) is False


def test_noop_on_fresh_create_all_schema(tmp_path: Path) -> None:
    """A schema built fresh from current metadata already has the column."""
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'ref.db'}")
    reference_metadata.create_all(engine, checkfirst=True)

    assert "individual_id" in _columns(engine, "samples")
    assert "genome_build" in _columns(engine, "database_versions")
    assert "validator" in _columns(engine, "downloads")
    assert ensure_reference_schema_current(engine) is False


def test_removes_legacy_orphaned_or_misbound_prompts(tmp_path: Path) -> None:
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'ref.db'}")
    reference_metadata.create_all(engine, checkfirst=True)
    with engine.begin() as conn:
        conn.execute(
            samples.insert(),
            [
                {
                    "id": sample_id,
                    "name": f"Current sample {sample_id}",
                    "db_path": f"samples/sample_{sample_id}.db",
                    "created_at": datetime(2026, 2, 1),
                }
                for sample_id in (2, 3, 4)
            ],
        )
        conn.execute(
            reannotation_prompts.insert(),
            [
                {
                    "sample_id": sample_id,
                    "db_name": "reference_data",
                    "db_version": "multiple",
                    "prompt_type": "version_staleness",
                    "stale_databases": "[]",
                    "dismissed": True,
                    "created_at": created_at,
                }
                for sample_id, created_at in (
                    (1, datetime(2026, 1, 1)),  # no current sample row
                    (2, datetime(2026, 3, 1)),  # belongs to current sample
                    (3, datetime(2026, 1, 1)),  # predates reused sample id
                    (4, None),  # cannot be bound safely
                )
            ],
        )

    assert ensure_reference_schema_current(engine) is True
    with engine.connect() as conn:
        prompt_sample_ids = (
            conn.execute(sa.select(reannotation_prompts.c.sample_id)).scalars().all()
        )
    assert prompt_sample_ids == [2]
    assert ensure_reference_schema_current(engine) is False


def _make_pre011_database_versions(engine: sa.Engine) -> None:
    """Create a ``database_versions`` table lacking ``genome_build`` (pre-011)."""
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "CREATE TABLE database_versions ("
                "  db_name TEXT PRIMARY KEY,"
                "  version TEXT NOT NULL,"
                "  file_path TEXT,"
                "  file_size_bytes INTEGER,"
                "  downloaded_at DATETIME,"
                "  checksum_sha256 TEXT"
                ")"
            )
        )
        conn.execute(
            sa.text(
                "INSERT INTO database_versions (db_name, version) VALUES ('clinvar', '20260101')"
            )
        )


def test_backfills_missing_genome_build(tmp_path: Path) -> None:
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'ref.db'}")
    _make_pre011_database_versions(engine)

    assert "genome_build" not in _columns(engine, "database_versions")

    changed = ensure_reference_schema_current(engine)

    assert changed is True
    assert "genome_build" in _columns(engine, "database_versions")
    # The pre-existing row survives and reads NULL for the new column.
    with engine.connect() as conn:
        row = conn.execute(
            sa.text("SELECT version, genome_build FROM database_versions WHERE db_name='clinvar'")
        ).fetchone()
    assert row == ("20260101", None)


def test_genome_build_backfill_idempotent(tmp_path: Path) -> None:
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'ref.db'}")
    _make_pre011_database_versions(engine)

    assert ensure_reference_schema_current(engine) is True
    assert ensure_reference_schema_current(engine) is False


def _make_pre_validator_downloads(engine: sa.Engine) -> None:
    """Create a ``downloads`` table lacking ``validator`` (pre-PR-15)."""
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "CREATE TABLE downloads ("
                "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
                "  url TEXT NOT NULL,"
                "  dest_path TEXT NOT NULL,"
                "  total_bytes INTEGER,"
                "  downloaded_bytes INTEGER DEFAULT 0,"
                "  checksum_sha256 TEXT,"
                "  status TEXT DEFAULT 'pending',"
                "  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,"
                "  updated_at DATETIME"
                ")"
            )
        )
        conn.execute(
            sa.text(
                "INSERT INTO downloads (url, dest_path, downloaded_bytes, status) "
                "VALUES ('http://x/clinvar.db', '/d/clinvar.db', 4096, 'downloading')"
            )
        )


def test_backfills_missing_downloads_validator(tmp_path: Path) -> None:
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'ref.db'}")
    _make_pre_validator_downloads(engine)

    assert "validator" not in _columns(engine, "downloads")

    changed = ensure_reference_schema_current(engine)

    assert changed is True
    assert "validator" in _columns(engine, "downloads")
    # An in-flight partial survives and reads NULL for the new validator, so a
    # SELECT that seeds If-Range on resume cannot crash on the missing column.
    with engine.connect() as conn:
        row = conn.execute(
            sa.text("SELECT downloaded_bytes, validator FROM downloads LIMIT 1")
        ).fetchone()
    assert row == (4096, None)


def test_downloads_validator_backfill_idempotent(tmp_path: Path) -> None:
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'ref.db'}")
    _make_pre_validator_downloads(engine)

    assert ensure_reference_schema_current(engine) is True
    assert ensure_reference_schema_current(engine) is False


def _make_pre_as_cpic_guidelines(engine: sa.Engine) -> None:
    """Create a ``cpic_guidelines`` table lacking ``activity_score`` (pre-#1993)."""
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "CREATE TABLE cpic_guidelines ("
                "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
                "  gene TEXT NOT NULL,"
                "  drug TEXT NOT NULL,"
                "  phenotype TEXT NOT NULL,"
                "  recommendation TEXT,"
                "  classification TEXT,"
                "  guideline_url TEXT"
                ")"
            )
        )
        conn.execute(
            sa.text(
                "INSERT INTO cpic_guidelines (gene, drug, phenotype, recommendation) "
                "VALUES ('DPYD', 'fluorouracil', 'Poor Metabolizer', 'Avoid.')"
            )
        )


def test_backfills_missing_cpic_guidelines_activity_score(tmp_path: Path) -> None:
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'ref.db'}")
    _make_pre_as_cpic_guidelines(engine)

    assert "activity_score" not in _columns(engine, "cpic_guidelines")

    changed = ensure_reference_schema_current(engine)

    assert changed is True
    assert "activity_score" in _columns(engine, "cpic_guidelines")
    # The existing phenotype-keyed row survives and reads NULL for the new score,
    # so the AS-aware guideline lookup (#1993) does not crash on a pre-#1993 DB.
    with engine.connect() as conn:
        row = conn.execute(
            sa.text("SELECT phenotype, activity_score FROM cpic_guidelines LIMIT 1")
        ).fetchone()
    assert row == ("Poor Metabolizer", None)


def test_cpic_guidelines_activity_score_backfill_idempotent(tmp_path: Path) -> None:
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'ref.db'}")
    _make_pre_as_cpic_guidelines(engine)

    assert ensure_reference_schema_current(engine) is True
    assert ensure_reference_schema_current(engine) is False


def test_refreshes_only_exact_legacy_cyp2c9_phenytoin_fingerprint(tmp_path: Path) -> None:
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'ref.db'}")
    reference_metadata.create_all(engine, checkfirst=True)
    preserved_rows = [
        {
            "gene": "CYP2C9",
            "drug": "warfarin",
            "phenotype": "Intermediate Metabolizer",
            "activity_score": None,
            "recommendation": "Preserve warfarin guidance.",
            "classification": "A",
            "guideline_url": "https://example.test/warfarin",
        },
        {
            "gene": "CYP2D6",
            "drug": "codeine",
            "phenotype": "Poor Metabolizer",
            "activity_score": None,
            "recommendation": "Preserve unrelated guidance.",
            "classification": "A",
            "guideline_url": "https://example.test/codeine",
        },
    ]
    with engine.begin() as conn:
        conn.execute(cpic_guidelines.insert(), [*_LEGACY_PHENYTOIN_ROWS, *preserved_rows])
        conn.execute(
            database_versions.insert(),
            {
                "db_name": "cpic",
                "version": "v1.58.0",
                "checksum_sha256": "legacy-checksum",
            },
        )
    with engine.connect() as conn:
        version_before = (
            conn.execute(sa.select(database_versions).where(database_versions.c.db_name == "cpic"))
            .mappings()
            .one()
        )

    assert ensure_reference_schema_current(engine) is True

    with engine.connect() as conn:
        phenytoin = conn.execute(
            sa.select(
                cpic_guidelines.c.phenotype,
                cpic_guidelines.c.activity_score,
                cpic_guidelines.c.recommendation,
            )
            .where(
                cpic_guidelines.c.gene == "CYP2C9",
                cpic_guidelines.c.drug == "phenytoin",
            )
            .order_by(cpic_guidelines.c.activity_score.desc())
        ).fetchall()
        preserved = conn.execute(
            sa.select(
                cpic_guidelines.c.gene, cpic_guidelines.c.drug, cpic_guidelines.c.recommendation
            )
            .where(cpic_guidelines.c.drug.in_(["warfarin", "codeine"]))
            .order_by(cpic_guidelines.c.drug)
        ).fetchall()
        version_after = (
            conn.execute(sa.select(database_versions).where(database_versions.c.db_name == "cpic"))
            .mappings()
            .one()
        )

    assert [row.activity_score for row in phenytoin] == [2.0, 1.5, 1.0, 0.5, 0.0]
    assert phenytoin[0].recommendation.startswith("No adjustments needed")
    assert "approximately 25% less than typical maintenance dose" in phenytoin[2].recommendation
    assert "approximately 50% less than typical maintenance dose" in phenytoin[3].recommendation
    assert preserved == [
        ("CYP2D6", "codeine", "Preserve unrelated guidance."),
        ("CYP2C9", "warfarin", "Preserve warfarin guidance."),
    ]
    assert dict(version_after) == dict(version_before)
    assert ensure_reference_schema_current(engine) is False


def test_does_not_overwrite_mixed_or_future_phenytoin_content(tmp_path: Path) -> None:
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'ref.db'}")
    reference_metadata.create_all(engine, checkfirst=True)
    mixed_rows = [
        *_LEGACY_PHENYTOIN_ROWS,
        {
            "gene": "CYP2C9",
            "drug": "phenytoin",
            "phenotype": "Intermediate Metabolizer",
            "activity_score": 1.25,
            "recommendation": "Future custom recommendation.",
            "classification": "A",
            "guideline_url": _PHENYTOIN_URL,
        },
    ]
    with engine.begin() as conn:
        conn.execute(cpic_guidelines.insert(), mixed_rows)

    assert ensure_reference_schema_current(engine) is False
    with engine.connect() as conn:
        rows = conn.execute(
            sa.select(cpic_guidelines.c.activity_score, cpic_guidelines.c.recommendation).where(
                cpic_guidelines.c.gene == "CYP2C9",
                cpic_guidelines.c.drug == "phenytoin",
            )
        ).fetchall()
    assert len(rows) == 4
    assert (1.25, "Future custom recommendation.") in rows


def test_rechecks_legacy_fingerprint_after_loading_bundled_rows(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """A concurrent/custom write before replacement must make the repair a no-op."""
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'ref.db'}")
    reference_metadata.create_all(engine, checkfirst=True)
    with engine.begin() as conn:
        conn.execute(cpic_guidelines.insert(), _LEGACY_PHENYTOIN_ROWS)

    from backend.annotation import cpic as cpic_module

    parse_bundled_rows = cpic_module.parse_cpic_guidelines_csv

    def parse_after_custom_write(path):
        with engine.begin() as conn:
            conn.execute(
                cpic_guidelines.insert(),
                {
                    "gene": "CYP2C9",
                    "drug": "phenytoin",
                    "phenotype": "Intermediate Metabolizer",
                    "activity_score": 1.25,
                    "recommendation": "Concurrent custom recommendation.",
                    "classification": "A",
                    "guideline_url": _PHENYTOIN_URL,
                },
            )
        return parse_bundled_rows(path)

    monkeypatch.setattr(cpic_module, "parse_cpic_guidelines_csv", parse_after_custom_write)

    assert ensure_reference_schema_current(engine) is False
    with engine.connect() as conn:
        rows = conn.execute(
            sa.select(cpic_guidelines.c.activity_score, cpic_guidelines.c.recommendation).where(
                cpic_guidelines.c.gene == "CYP2C9",
                cpic_guidelines.c.drug == "phenytoin",
            )
        ).fetchall()

    assert len(rows) == 4
    assert (1.25, "Concurrent custom recommendation.") in rows


def test_legacy_refresh_locks_for_write_before_fingerprint_select(tmp_path: Path) -> None:
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'ref.db'}")
    reference_metadata.create_all(engine, checkfirst=True)
    with engine.begin() as conn:
        conn.execute(cpic_guidelines.insert(), _LEGACY_PHENYTOIN_ROWS)

    statements: list[str] = []

    def record_statement(_conn, _cursor, statement, _parameters, _context, _executemany):
        statements.append(" ".join(statement.split()).upper())

    sa.event.listen(engine, "before_cursor_execute", record_statement)
    try:
        assert ensure_reference_schema_current(engine) is True
    finally:
        sa.event.remove(engine, "before_cursor_execute", record_statement)

    begin_index = statements.index("BEGIN IMMEDIATE")
    fingerprint_index = next(
        index
        for index, statement in enumerate(statements)
        if statement.startswith("SELECT CPIC_GUIDELINES.PHENOTYPE")
    )
    delete_index = next(
        index
        for index, statement in enumerate(statements)
        if statement.startswith("DELETE FROM CPIC_GUIDELINES")
    )
    assert begin_index < fingerprint_index < delete_index


def test_activity_score_column_and_legacy_phenytoin_content_upgrade_together(
    tmp_path: Path,
) -> None:
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'ref.db'}")
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "CREATE TABLE cpic_guidelines ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, gene TEXT NOT NULL, drug TEXT NOT NULL, "
                "phenotype TEXT NOT NULL, recommendation TEXT, classification TEXT, "
                "guideline_url TEXT)"
            )
        )
        conn.execute(
            sa.text(
                "INSERT INTO cpic_guidelines "
                "(gene, drug, phenotype, recommendation, classification, guideline_url) "
                "VALUES (:gene, :drug, :phenotype, :recommendation, "
                ":classification, :guideline_url)"
            ),
            _LEGACY_PHENYTOIN_ROWS,
        )

    assert ensure_reference_schema_current(engine) is True
    assert "activity_score" in _columns(engine, "cpic_guidelines")
    with engine.connect() as conn:
        scores = (
            conn.execute(
                sa.text(
                    "SELECT activity_score FROM cpic_guidelines "
                    "WHERE gene='CYP2C9' AND drug='phenytoin' ORDER BY activity_score DESC"
                )
            )
            .scalars()
            .all()
        )
    assert scores == [2.0, 1.5, 1.0, 0.5, 0.0]

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
from multiprocessing import get_context
from pathlib import Path
from queue import Empty

import pytest
import sqlalchemy as sa

from backend.db.reference_schema import (
    bootstrap_reference_schema_tables,
    ensure_reference_schema_current,
)
from backend.db.tables import (
    LOG_ENTRY_PRESENTATION_POLICY_VERSION,
    cpic_guidelines,
    database_versions,
    log_entries,
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

_EFAVIRENZ_URL = (
    "https://cpicpgx.org/guidelines/cpic-guideline-for-efavirenz-based-on-cyp2b6-genotype/"
)
_LEGACY_EFAVIRENZ_ROWS = [
    {
        "gene": "CYP2B6",
        "drug": "efavirenz",
        "phenotype": "Intermediate Metabolizer",
        "activity_score": None,
        "recommendation": (
            "Use label-recommended dosing; consider a reduced dose if CNS side effects occur."
        ),
        "classification": "A",
        "guideline_url": _EFAVIRENZ_URL,
    },
    {
        "gene": "CYP2B6",
        "drug": "efavirenz",
        "phenotype": "Poor Metabolizer",
        "activity_score": None,
        "recommendation": (
            "Consider initiating at a decreased dose (e.g., 400 mg/day); higher plasma "
            "exposure raises CNS-toxicity risk."
        ),
        "classification": "A",
        "guideline_url": _EFAVIRENZ_URL,
    },
]
_CANONICAL_EFAVIRENZ_RECOMMENDATIONS = {
    "Intermediate Metabolizer": (
        "Consider initiating efavirenz with decreased dose of 400 mg/day."
    ),
    "Poor Metabolizer": (
        "Consider initiating efavirenz with decreased dose of 400 or 200 mg/day."
    ),
}

_TAMOXIFEN_URL = "https://cpicpgx.org/guidelines/cpic-guideline-for-tamoxifen-based-on-cyp2d6/"
_LEGACY_TAMOXIFEN_ROWS = [
    {
        "gene": "CYP2D6",
        "drug": "tamoxifen",
        "phenotype": "Normal Metabolizer",
        "activity_score": None,
        "recommendation": "Use label-recommended dosing.",
        "classification": "A",
        "guideline_url": _TAMOXIFEN_URL,
    },
    {
        "gene": "CYP2D6",
        "drug": "tamoxifen",
        "phenotype": "Intermediate Metabolizer",
        "activity_score": None,
        "recommendation": "Consider higher dose or alternative therapy.",
        "classification": "A",
        "guideline_url": _TAMOXIFEN_URL,
    },
    {
        "gene": "CYP2D6",
        "drug": "tamoxifen",
        "phenotype": "Poor Metabolizer",
        "activity_score": None,
        "recommendation": (
            "Avoid tamoxifen. Use alternative hormonal therapy such as aromatase inhibitor."
        ),
        "classification": "A",
        "guideline_url": _TAMOXIFEN_URL,
    },
]
_CANONICAL_TAMOXIFEN_RECOMMENDATIONS = {
    "Normal Metabolizer": (
        "Avoid moderate and strong CYP2D6 inhibitors. Initiate therapy with "
        "recommended standard of care dosing (tamoxifen 20 mg/day)."
    ),
    "Intermediate Metabolizer": (
        "Consider hormonal therapy such as an aromatase inhibitor for postmenopausal women "
        "or aromatase inhibitor along with ovarian function suppression in premenopausal "
        "women, given that these approaches are superior to tamoxifen regardless of CYP2D6 "
        "genotype (PMID 26211827). If aromatase inhibitor use is contraindicated, "
        "consideration should be given to use a higher but FDA approved tamoxifen dose "
        "(40 mg/day)(PMID 27226358). Avoid CYP2D6 strong to weak inhibitors."
    ),
    "Poor Metabolizer": (
        "Recommend alternative hormonal therapy such as an aromatase inhibitor for "
        "postmenopausal women or aromatase inhibitor along with ovarian function suppression "
        "in premenopausal women given that these approaches are superior to tamoxifen "
        "regardless of CYP2D6 genotype (PMID 26211827) and based on knowledge that CYP2D6 "
        "poor metabolizers switched from tamoxifen to anastrozole do not have an increased "
        "risk of recurrence (PMID 23213055). Note, higher dose tamoxifen (40 mg/day) "
        "increases but does not normalize endoxifen concentrations and can be considered if "
        "there are contraindications to aromatase inhibitor therapy (PMID 27226358, "
        "21768473)."
    ),
}

_TPMT_URL = "https://cpicpgx.org/guidelines/guideline-for-thiopurines-and-tpmt/"
_LEGACY_TPMT_POOR_METABOLIZER_BASE = [
    {
        "gene": "TPMT",
        "drug": "mercaptopurine",
        "phenotype": "Poor Metabolizer",
        "activity_score": None,
        "recommendation": "Reduce dose to 10% of standard. Consider alternative agent.",
        "classification": "A",
        "guideline_url": _TPMT_URL,
    },
    {
        "gene": "TPMT",
        "drug": "azathioprine",
        "phenotype": "Poor Metabolizer",
        "activity_score": None,
        "recommendation": "Reduce dose to 10% of standard or use alternative agent.",
        "classification": "A",
        "guideline_url": _TPMT_URL,
    },
]
_LEGACY_TPMT_THIOGUANINE_50_75 = {
    "gene": "TPMT",
    "drug": "thioguanine",
    "phenotype": "Poor Metabolizer",
    "activity_score": None,
    "recommendation": (
        "Start with drastically reduced doses (reduce by 50-75%) and titrate based on "
        "myelosuppression; for nonmalignant conditions consider an alternative agent."
    ),
    "classification": "A",
    "guideline_url": _TPMT_URL,
}
_LEGACY_TPMT_THIOGUANINE_POST_1259 = {
    "gene": "TPMT",
    "drug": "thioguanine",
    "phenotype": "Poor Metabolizer",
    "activity_score": None,
    "recommendation": (
        "Start with drastically reduced doses (reduce daily dose by 10-fold and dose "
        "thrice weekly instead of daily) and titrate based on myelosuppression; for "
        "nonmalignant conditions consider an alternative agent."
    ),
    "classification": "A",
    "guideline_url": _TPMT_URL,
}


def _columns(engine: sa.Engine, table: str) -> set[str]:
    return {c["name"] for c in sa.inspect(engine).get_columns(table)}


def _bootstrap_reference_schema_in_subprocess(db_path: str, start, errors) -> None:
    """Race two independent processes through the reference-table bootstrap."""
    engine = sa.create_engine(f"sqlite:///{db_path}", connect_args={"timeout": 5})
    try:
        start.wait(timeout=5)
        bootstrap_reference_schema_tables(engine)
    except Exception as exc:  # pragma: no cover - asserted by the parent process
        errors.put(f"{type(exc).__name__}: {exc}")
    finally:
        engine.dispose()


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
    assert "presentation_policy_version" in _columns(engine, "log_entries")
    indexes = {index["name"] for index in sa.inspect(engine).get_indexes("log_entries")}
    assert "idx_log_entries_presentation_policy_id" in indexes
    with engine.begin() as conn:
        conn.execute(
            sa.insert(log_entries).values(
                level="INFO",
                logger="backend.operations",
                message="unattested raw event",
                event_data=None,
            )
        )
        raw_version = conn.execute(
            sa.select(log_entries.c.presentation_policy_version).where(
                log_entries.c.message == "unattested raw event"
            )
        ).scalar_one()
    assert raw_version == 0
    assert ensure_reference_schema_current(engine) is False


def test_bootstrap_serializes_parallel_reference_table_creation(tmp_path: Path) -> None:
    """Concurrent web/worker starts cannot race SQLite's check/create window."""
    db_path = tmp_path / "reference.db"
    context = get_context("spawn")
    start = context.Event()
    errors = context.Queue()
    workers = [
        context.Process(
            target=_bootstrap_reference_schema_in_subprocess,
            args=(str(db_path), start, errors),
        )
        for _ in range(2)
    ]
    for worker in workers:
        worker.start()
    start.set()
    for worker in workers:
        worker.join(timeout=10)

    assert not [worker for worker in workers if worker.is_alive()]
    assert [worker.exitcode for worker in workers] == [0, 0]
    reported_errors: list[str] = []
    while True:
        try:
            reported_errors.append(errors.get_nowait())
        except Empty:
            break
    assert reported_errors == []
    engine = sa.create_engine(f"sqlite:///{db_path}")
    try:
        assert {"individuals", "jobs", "log_entries"} <= set(sa.inspect(engine).get_table_names())
    finally:
        engine.dispose()


def _make_pre_presentation_policy_log_entries(engine: sa.Engine) -> None:
    """Create the exact pre-#2019 durable-log schema with a historic row."""
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "CREATE TABLE log_entries ("
                "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
                "  timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,"
                "  level TEXT NOT NULL,"
                "  logger TEXT,"
                "  message TEXT,"
                "  event_data TEXT"
                ")"
            )
        )
        conn.execute(
            sa.text(
                "INSERT INTO log_entries (level, logger, message, event_data) VALUES "
                "('WARNING', 'backend.analysis.pharmacogenomics', "
                "'pgx_prescribing_alert', "
                '\'{"gene": "CYP2D6", "drug": "tamoxifen"}\')'
            )
        )


def test_backfills_log_presentation_policy_without_reclassifying_history(tmp_path: Path) -> None:
    """Historic log rows remain quarantined while fresh writer rows attest current policy."""
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'ref.db'}")
    _make_pre_presentation_policy_log_entries(engine)

    # Match application bootstrap: create_all must leave the old table intact,
    # after which the additive repair provides its new column and index.
    reference_metadata.create_all(engine, checkfirst=True)
    assert "presentation_policy_version" not in _columns(engine, "log_entries")

    assert ensure_reference_schema_current(engine) is True
    assert "presentation_policy_version" in _columns(engine, "log_entries")
    indexes = {index["name"] for index in sa.inspect(engine).get_indexes("log_entries")}
    assert "idx_log_entries_presentation_policy_id" in indexes

    with engine.begin() as conn:
        historic_version = conn.execute(
            sa.select(log_entries.c.presentation_policy_version).where(log_entries.c.id == 1)
        ).scalar_one()
        conn.execute(
            sa.insert(log_entries).values(
                level="INFO",
                logger="backend.operations",
                message="unattested migrated raw event",
                event_data=None,
            )
        )
        conn.execute(
            sa.insert(log_entries).values(
                level="INFO",
                logger="backend.logging_config",
                message="fresh policy-attested event",
                event_data=None,
                presentation_policy_version=LOG_ENTRY_PRESENTATION_POLICY_VERSION,
            )
        )
        raw_version = conn.execute(
            sa.select(log_entries.c.presentation_policy_version).where(
                log_entries.c.message == "unattested migrated raw event"
            )
        ).scalar_one()
        writer_version = conn.execute(
            sa.select(log_entries.c.presentation_policy_version).where(
                log_entries.c.message == "fresh policy-attested event"
            )
        ).scalar_one()

    assert historic_version == 0
    assert raw_version == 0
    assert writer_version == LOG_ENTRY_PRESENTATION_POLICY_VERSION
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


def test_does_not_overwrite_or_load_bundle_for_mixed_or_future_phenytoin_content(
    tmp_path: Path,
    monkeypatch,
) -> None:
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

    from backend.annotation import cpic as cpic_module

    def fail_if_parsed(_path):
        raise AssertionError("nonlegacy content must not load the migration payload")

    monkeypatch.setattr(cpic_module, "parse_cpic_guidelines_csv", fail_if_parsed)

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


def test_loads_bundled_rows_only_after_locked_legacy_fingerprint(
    tmp_path: Path,
    monkeypatch,
) -> None:
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'ref.db'}")
    reference_metadata.create_all(engine, checkfirst=True)
    with engine.begin() as conn:
        conn.execute(cpic_guidelines.insert(), _LEGACY_PHENYTOIN_ROWS)

    from backend.annotation import cpic as cpic_module

    parse_bundled_rows = cpic_module.parse_cpic_guidelines_csv
    events: list[str] = []

    def parse_after_fingerprint(path):
        events.append("parse")
        return parse_bundled_rows(path)

    def record_statement(_conn, _cursor, statement, _parameters, _context, _executemany):
        normalized = " ".join(statement.split()).upper()
        if normalized == "BEGIN IMMEDIATE":
            events.append("begin")
        elif normalized.startswith("SELECT CPIC_GUIDELINES.PHENOTYPE"):
            events.append("fingerprint")

    monkeypatch.setattr(cpic_module, "parse_cpic_guidelines_csv", parse_after_fingerprint)
    sa.event.listen(engine, "before_cursor_execute", record_statement)
    try:
        assert ensure_reference_schema_current(engine) is True
    finally:
        sa.event.remove(engine, "before_cursor_execute", record_statement)

    fingerprint_index = events.index("fingerprint")
    assert events[fingerprint_index - 1 : fingerprint_index + 2] == [
        "begin",
        "fingerprint",
        "parse",
    ]


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

    fingerprint_index = next(
        index
        for index, statement in enumerate(statements)
        if statement.startswith("SELECT CPIC_GUIDELINES.PHENOTYPE")
    )
    begin_index = max(
        index
        for index, statement in enumerate(statements[:fingerprint_index])
        if statement == "BEGIN IMMEDIATE"
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


def test_refreshes_only_exact_legacy_cyp2b6_efavirenz_pair(tmp_path: Path) -> None:
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'ref.db'}")
    reference_metadata.create_all(engine, checkfirst=True)
    preserved_rows = [
        {
            "gene": "CYP2B6",
            "drug": "efavirenz",
            "phenotype": "Normal Metabolizer",
            "activity_score": None,
            "recommendation": "Preserve normal-metabolizer guidance.",
            "classification": "A",
            "guideline_url": _EFAVIRENZ_URL,
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
        conn.execute(cpic_guidelines.insert(), [*_LEGACY_EFAVIRENZ_ROWS, *preserved_rows])
        conn.execute(
            database_versions.insert(),
            {
                "db_name": "cpic",
                "version": "v1.58.0",
                "checksum_sha256": "legacy-checksum",
            },
        )
    with engine.connect() as conn:
        legacy_ids = dict(
            conn.execute(
                sa.select(cpic_guidelines.c.phenotype, cpic_guidelines.c.id).where(
                    cpic_guidelines.c.gene == "CYP2B6",
                    cpic_guidelines.c.drug == "efavirenz",
                    cpic_guidelines.c.phenotype.in_(_CANONICAL_EFAVIRENZ_RECOMMENDATIONS),
                )
            ).fetchall()
        )
        version_before = (
            conn.execute(sa.select(database_versions).where(database_versions.c.db_name == "cpic"))
            .mappings()
            .one()
        )

    assert ensure_reference_schema_current(engine) is True

    with engine.connect() as conn:
        upgraded = conn.execute(
            sa.select(
                cpic_guidelines.c.id,
                cpic_guidelines.c.phenotype,
                cpic_guidelines.c.recommendation,
            ).where(
                cpic_guidelines.c.gene == "CYP2B6",
                cpic_guidelines.c.drug == "efavirenz",
                cpic_guidelines.c.phenotype.in_(_CANONICAL_EFAVIRENZ_RECOMMENDATIONS),
            )
        ).fetchall()
        preserved = conn.execute(
            sa.select(
                cpic_guidelines.c.gene,
                cpic_guidelines.c.phenotype,
                cpic_guidelines.c.recommendation,
            ).where(
                sa.or_(
                    cpic_guidelines.c.drug == "codeine",
                    cpic_guidelines.c.phenotype == "Normal Metabolizer",
                )
            )
        ).fetchall()
        version_after = (
            conn.execute(sa.select(database_versions).where(database_versions.c.db_name == "cpic"))
            .mappings()
            .one()
        )

    assert {row.phenotype: row.id for row in upgraded} == legacy_ids
    assert {
        row.phenotype: row.recommendation for row in upgraded
    } == _CANONICAL_EFAVIRENZ_RECOMMENDATIONS
    assert set(preserved) == {
        ("CYP2B6", "Normal Metabolizer", "Preserve normal-metabolizer guidance."),
        ("CYP2D6", "Poor Metabolizer", "Preserve unrelated guidance."),
    }
    assert dict(version_after) == dict(version_before)
    assert ensure_reference_schema_current(engine) is False


@pytest.mark.parametrize(
    ("rows", "updated_phenotypes"),
    [
        pytest.param(
            _LEGACY_EFAVIRENZ_ROWS[:1],
            {"Intermediate Metabolizer"},
            id="missing-companion",
        ),
        pytest.param(
            [*_LEGACY_EFAVIRENZ_ROWS, _LEGACY_EFAVIRENZ_ROWS[0]],
            {"Poor Metabolizer"},
            id="duplicate-companion",
        ),
        pytest.param(
            [
                {
                    **_LEGACY_EFAVIRENZ_ROWS[0],
                    "recommendation": "Custom intermediate-metabolizer guidance.",
                },
                _LEGACY_EFAVIRENZ_ROWS[1],
            ],
            {"Poor Metabolizer"},
            id="custom-companion",
        ),
        pytest.param(
            [
                {
                    **_LEGACY_EFAVIRENZ_ROWS[0],
                    "recommendation": _CANONICAL_EFAVIRENZ_RECOMMENDATIONS[
                        "Intermediate Metabolizer"
                    ],
                },
                _LEGACY_EFAVIRENZ_ROWS[1],
            ],
            {"Poor Metabolizer"},
            id="current-companion",
        ),
    ],
)
def test_refreshes_each_unambiguous_legacy_efavirenz_row_independently(
    tmp_path: Path,
    rows: list[dict],
    updated_phenotypes: set[str],
) -> None:
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'ref.db'}")
    reference_metadata.create_all(engine, checkfirst=True)
    with engine.begin() as conn:
        conn.execute(cpic_guidelines.insert(), rows)
    with engine.connect() as conn:
        before = [
            dict(row)
            for row in conn.execute(
                sa.select(cpic_guidelines).order_by(cpic_guidelines.c.id)
            ).mappings()
        ]

    assert ensure_reference_schema_current(engine) is True
    with engine.connect() as conn:
        after = [
            dict(row)
            for row in conn.execute(
                sa.select(cpic_guidelines).order_by(cpic_guidelines.c.id)
            ).mappings()
        ]

    for old_row, new_row in zip(before, after, strict=True):
        if (
            old_row["phenotype"] in updated_phenotypes
            and old_row["recommendation"]
            != _CANONICAL_EFAVIRENZ_RECOMMENDATIONS[old_row["phenotype"]]
        ):
            assert new_row == {
                **old_row,
                "recommendation": _CANONICAL_EFAVIRENZ_RECOMMENDATIONS[old_row["phenotype"]],
            }
        else:
            assert new_row == old_row


def test_does_not_load_bundle_for_current_efavirenz_rows(tmp_path: Path, monkeypatch) -> None:
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'ref.db'}")
    reference_metadata.create_all(engine, checkfirst=True)
    current_rows = [
        {
            **row,
            "recommendation": _CANONICAL_EFAVIRENZ_RECOMMENDATIONS[row["phenotype"]],
        }
        for row in _LEGACY_EFAVIRENZ_ROWS
    ]
    with engine.begin() as conn:
        conn.execute(cpic_guidelines.insert(), current_rows)

    from backend.annotation import cpic as cpic_module

    def fail_if_parsed(_path):
        raise AssertionError("current CYP2B6/efavirenz content must not load the bundle")

    monkeypatch.setattr(cpic_module, "parse_cpic_guidelines_csv", fail_if_parsed)

    assert ensure_reference_schema_current(engine) is False


def test_invalid_bundled_efavirenz_pair_rolls_back_legacy_rows(
    tmp_path: Path,
    monkeypatch,
) -> None:
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'ref.db'}")
    reference_metadata.create_all(engine, checkfirst=True)
    with engine.begin() as conn:
        conn.execute(cpic_guidelines.insert(), _LEGACY_EFAVIRENZ_ROWS)
    with engine.connect() as conn:
        before = [
            dict(row)
            for row in conn.execute(
                sa.select(cpic_guidelines).order_by(cpic_guidelines.c.id)
            ).mappings()
        ]

    invalid_canonical = [
        {
            **row,
            "recommendation": _CANONICAL_EFAVIRENZ_RECOMMENDATIONS[row["phenotype"]],
        }
        for row in _LEGACY_EFAVIRENZ_ROWS
    ]
    invalid_canonical[0] = {
        **invalid_canonical[0],
        "recommendation": "Unreviewed bundled recommendation.",
    }

    from backend.annotation import cpic as cpic_module

    def parse_invalid_bundle(_path):
        return invalid_canonical, None

    monkeypatch.setattr(cpic_module, "parse_cpic_guidelines_csv", parse_invalid_bundle)

    with pytest.raises(
        RuntimeError,
        match="Bundled CYP2B6/efavirenz reduced-dose guidelines are not canonical",
    ):
        ensure_reference_schema_current(engine)

    with engine.connect() as conn:
        after = [
            dict(row)
            for row in conn.execute(
                sa.select(cpic_guidelines).order_by(cpic_guidelines.c.id)
            ).mappings()
        ]
    assert after == before


def test_efavirenz_refresh_rolls_back_if_a_canonical_write_fails(tmp_path: Path) -> None:
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'ref.db'}")
    reference_metadata.create_all(engine, checkfirst=True)
    with engine.begin() as conn:
        conn.execute(cpic_guidelines.insert(), _LEGACY_EFAVIRENZ_ROWS)
    with engine.connect() as conn:
        before = [
            dict(row)
            for row in conn.execute(
                sa.select(cpic_guidelines).order_by(cpic_guidelines.c.id)
            ).mappings()
        ]

    update_count = 0

    def fail_second_update(_conn, _cursor, statement, _parameters, _context, _executemany):
        nonlocal update_count
        if " ".join(statement.split()).upper().startswith("UPDATE CPIC_GUIDELINES"):
            update_count += 1
            if update_count == 2:
                raise RuntimeError("simulated efavirenz write failure")

    sa.event.listen(engine, "before_cursor_execute", fail_second_update)
    try:
        with pytest.raises(RuntimeError, match="simulated efavirenz write failure"):
            ensure_reference_schema_current(engine)
    finally:
        sa.event.remove(engine, "before_cursor_execute", fail_second_update)

    with engine.connect() as conn:
        after = [
            dict(row)
            for row in conn.execute(
                sa.select(cpic_guidelines).order_by(cpic_guidelines.c.id)
            ).mappings()
        ]
    assert update_count == 2
    assert after == before


def test_efavirenz_refresh_locks_for_write_before_pair_fingerprint(tmp_path: Path) -> None:
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'ref.db'}")
    reference_metadata.create_all(engine, checkfirst=True)
    with engine.begin() as conn:
        conn.execute(cpic_guidelines.insert(), _LEGACY_EFAVIRENZ_ROWS)

    statements: list[str] = []

    def record_statement(_conn, _cursor, statement, _parameters, _context, _executemany):
        statements.append(" ".join(statement.split()).upper())

    sa.event.listen(engine, "before_cursor_execute", record_statement)
    try:
        assert ensure_reference_schema_current(engine) is True
    finally:
        sa.event.remove(engine, "before_cursor_execute", record_statement)

    fingerprint_index = next(
        index
        for index, statement in enumerate(statements)
        if statement.startswith("SELECT CPIC_GUIDELINES.ID, CPIC_GUIDELINES.PHENOTYPE")
    )
    begin_index = max(
        index
        for index, statement in enumerate(statements[:fingerprint_index])
        if statement == "BEGIN IMMEDIATE"
    )
    update_index = next(
        index
        for index, statement in enumerate(statements[fingerprint_index:], fingerprint_index)
        if statement.startswith("UPDATE CPIC_GUIDELINES")
    )
    assert begin_index < fingerprint_index < update_index


def _bundled_tpmt_poor_metabolizer_rows() -> list[dict]:
    from backend.annotation.cpic import CPIC_DATA_DIR, parse_cpic_guidelines_csv

    rows, _ = parse_cpic_guidelines_csv(CPIC_DATA_DIR / "cpic_guidelines.csv")
    return sorted(
        (row for row in rows if row["gene"] == "TPMT" and row["phenotype"] == "Poor Metabolizer"),
        key=lambda row: row["drug"],
    )


@pytest.mark.parametrize(
    "legacy_rows",
    [
        pytest.param(_LEGACY_TPMT_POOR_METABOLIZER_BASE, id="pre-thioguanine"),
        pytest.param(
            [*_LEGACY_TPMT_POOR_METABOLIZER_BASE, _LEGACY_TPMT_THIOGUANINE_50_75],
            id="thioguanine-50-75-percent",
        ),
        pytest.param(
            [*_LEGACY_TPMT_POOR_METABOLIZER_BASE, _LEGACY_TPMT_THIOGUANINE_POST_1259],
            id="post-1259-obsolete-nonmalignancy",
        ),
    ],
)
def test_refreshes_each_exact_legacy_tpmt_poor_metabolizer_matrix(
    tmp_path: Path,
    legacy_rows: list[dict],
) -> None:
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'ref.db'}")
    reference_metadata.create_all(engine, checkfirst=True)
    preserved_row = {
        "gene": "TPMT",
        "drug": "mercaptopurine",
        "phenotype": "Normal Metabolizer",
        "activity_score": None,
        "recommendation": "Preserve normal-metabolizer guidance.",
        "classification": "A",
        "guideline_url": _TPMT_URL,
    }
    with engine.begin() as conn:
        conn.execute(cpic_guidelines.insert(), [*legacy_rows, preserved_row])
    with engine.connect() as conn:
        existing_ids = dict(
            conn.execute(
                sa.select(cpic_guidelines.c.drug, cpic_guidelines.c.id).where(
                    cpic_guidelines.c.gene == "TPMT",
                    cpic_guidelines.c.phenotype == "Poor Metabolizer",
                )
            ).fetchall()
        )

    assert ensure_reference_schema_current(engine) is True

    with engine.connect() as conn:
        upgraded = [
            dict(row)
            for row in conn.execute(
                sa.select(
                    cpic_guidelines.c.gene,
                    cpic_guidelines.c.drug,
                    cpic_guidelines.c.phenotype,
                    cpic_guidelines.c.activity_score,
                    cpic_guidelines.c.recommendation,
                    cpic_guidelines.c.classification,
                    cpic_guidelines.c.guideline_url,
                )
                .where(
                    cpic_guidelines.c.gene == "TPMT",
                    cpic_guidelines.c.phenotype == "Poor Metabolizer",
                )
                .order_by(cpic_guidelines.c.drug)
            ).mappings()
        ]
        upgraded_ids = dict(
            conn.execute(
                sa.select(cpic_guidelines.c.drug, cpic_guidelines.c.id).where(
                    cpic_guidelines.c.gene == "TPMT",
                    cpic_guidelines.c.phenotype == "Poor Metabolizer",
                )
            ).fetchall()
        )
        preserved = conn.execute(
            sa.select(cpic_guidelines.c.recommendation).where(
                cpic_guidelines.c.gene == "TPMT",
                cpic_guidelines.c.phenotype == "Normal Metabolizer",
            )
        ).scalar_one()

    assert upgraded == _bundled_tpmt_poor_metabolizer_rows()
    assert {drug: upgraded_ids[drug] for drug in existing_ids} == existing_ids
    assert preserved == "Preserve normal-metabolizer guidance."
    assert ensure_reference_schema_current(engine) is False


@pytest.mark.parametrize(
    "near_miss",
    [
        pytest.param(_LEGACY_TPMT_POOR_METABOLIZER_BASE[:1], id="partial"),
        pytest.param(
            [
                *_LEGACY_TPMT_POOR_METABOLIZER_BASE,
                _LEGACY_TPMT_POOR_METABOLIZER_BASE[0],
            ],
            id="duplicate",
        ),
        pytest.param(
            [
                {
                    **_LEGACY_TPMT_POOR_METABOLIZER_BASE[0],
                    "recommendation": "Custom mercaptopurine guidance.",
                },
                _LEGACY_TPMT_POOR_METABOLIZER_BASE[1],
            ],
            id="custom-recommendation",
        ),
        pytest.param(
            [
                *_LEGACY_TPMT_POOR_METABOLIZER_BASE,
                {
                    **_LEGACY_TPMT_THIOGUANINE_POST_1259,
                    "drug": "custom-thiopurine",
                    "recommendation": "Future custom guidance.",
                },
            ],
            id="mixed-future-drug",
        ),
    ],
)
def test_does_not_overwrite_or_load_bundle_for_near_miss_tpmt_matrices(
    tmp_path: Path,
    monkeypatch,
    near_miss: list[dict],
) -> None:
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'ref.db'}")
    reference_metadata.create_all(engine, checkfirst=True)
    with engine.begin() as conn:
        conn.execute(cpic_guidelines.insert(), near_miss)
    with engine.connect() as conn:
        before = [
            dict(row)
            for row in conn.execute(
                sa.select(cpic_guidelines).order_by(cpic_guidelines.c.id)
            ).mappings()
        ]

    from backend.annotation import cpic as cpic_module

    def fail_if_parsed(_path):
        raise AssertionError("nonlegacy TPMT content must not load the migration payload")

    monkeypatch.setattr(cpic_module, "parse_cpic_guidelines_csv", fail_if_parsed)

    assert ensure_reference_schema_current(engine) is False
    with engine.connect() as conn:
        after = [
            dict(row)
            for row in conn.execute(
                sa.select(cpic_guidelines).order_by(cpic_guidelines.c.id)
            ).mappings()
        ]
    assert after == before


def test_current_tpmt_matrix_is_an_idempotent_noop_without_loading_bundle(
    tmp_path: Path,
    monkeypatch,
) -> None:
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'ref.db'}")
    reference_metadata.create_all(engine, checkfirst=True)
    with engine.begin() as conn:
        conn.execute(cpic_guidelines.insert(), _bundled_tpmt_poor_metabolizer_rows())

    from backend.annotation import cpic as cpic_module

    def fail_if_parsed(_path):
        raise AssertionError("current TPMT content must not reload the migration payload")

    monkeypatch.setattr(cpic_module, "parse_cpic_guidelines_csv", fail_if_parsed)

    assert ensure_reference_schema_current(engine) is False


def test_invalid_bundled_tpmt_matrix_rolls_back_the_legacy_rows(
    tmp_path: Path,
    monkeypatch,
) -> None:
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'ref.db'}")
    reference_metadata.create_all(engine, checkfirst=True)
    legacy_rows = [
        *_LEGACY_TPMT_POOR_METABOLIZER_BASE,
        _LEGACY_TPMT_THIOGUANINE_POST_1259,
    ]
    with engine.begin() as conn:
        conn.execute(cpic_guidelines.insert(), legacy_rows)
    with engine.connect() as conn:
        before = [
            dict(row)
            for row in conn.execute(
                sa.select(cpic_guidelines).order_by(cpic_guidelines.c.id)
            ).mappings()
        ]

    from backend.annotation import cpic as cpic_module

    invalid_canonical = _bundled_tpmt_poor_metabolizer_rows()
    invalid_canonical[0] = {
        **invalid_canonical[0],
        "recommendation": "Unreviewed bundled recommendation.",
    }

    def parse_invalid_bundle(_path):
        return invalid_canonical, None

    monkeypatch.setattr(cpic_module, "parse_cpic_guidelines_csv", parse_invalid_bundle)

    with pytest.raises(
        RuntimeError,
        match="Bundled TPMT poor-metabolizer guideline matrix is not canonical",
    ):
        ensure_reference_schema_current(engine)

    with engine.connect() as conn:
        after = [
            dict(row)
            for row in conn.execute(
                sa.select(cpic_guidelines).order_by(cpic_guidelines.c.id)
            ).mappings()
        ]
    assert after == before


def test_tpmt_refresh_rolls_back_if_a_canonical_write_fails(tmp_path: Path) -> None:
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'ref.db'}")
    reference_metadata.create_all(engine, checkfirst=True)
    legacy_rows = [
        *_LEGACY_TPMT_POOR_METABOLIZER_BASE,
        _LEGACY_TPMT_THIOGUANINE_POST_1259,
    ]
    with engine.begin() as conn:
        conn.execute(cpic_guidelines.insert(), legacy_rows)
    with engine.connect() as conn:
        before = [
            dict(row)
            for row in conn.execute(
                sa.select(cpic_guidelines).order_by(cpic_guidelines.c.id)
            ).mappings()
        ]

    update_count = 0

    def fail_second_update(_conn, _cursor, statement, _parameters, _context, _executemany):
        nonlocal update_count
        if " ".join(statement.split()).upper().startswith("UPDATE CPIC_GUIDELINES"):
            update_count += 1
            if update_count == 2:
                raise RuntimeError("simulated canonical write failure")

    sa.event.listen(engine, "before_cursor_execute", fail_second_update)
    try:
        with pytest.raises(RuntimeError, match="simulated canonical write failure"):
            ensure_reference_schema_current(engine)
    finally:
        sa.event.remove(engine, "before_cursor_execute", fail_second_update)

    with engine.connect() as conn:
        after = [
            dict(row)
            for row in conn.execute(
                sa.select(cpic_guidelines).order_by(cpic_guidelines.c.id)
            ).mappings()
        ]
    assert update_count == 2
    assert after == before


def test_tpmt_refresh_locks_for_write_before_whole_matrix_fingerprint(tmp_path: Path) -> None:
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'ref.db'}")
    reference_metadata.create_all(engine, checkfirst=True)
    with engine.begin() as conn:
        conn.execute(cpic_guidelines.insert(), _LEGACY_TPMT_POOR_METABOLIZER_BASE)

    statements: list[str] = []

    def record_statement(_conn, _cursor, statement, _parameters, _context, _executemany):
        statements.append(" ".join(statement.split()).upper())

    sa.event.listen(engine, "before_cursor_execute", record_statement)
    try:
        assert ensure_reference_schema_current(engine) is True
    finally:
        sa.event.remove(engine, "before_cursor_execute", record_statement)

    fingerprint_index = next(
        index
        for index, statement in enumerate(statements)
        if statement.startswith("SELECT CPIC_GUIDELINES.ID, CPIC_GUIDELINES.DRUG")
    )
    begin_index = max(
        index
        for index, statement in enumerate(statements[:fingerprint_index])
        if statement == "BEGIN IMMEDIATE"
    )
    update_index = next(
        index
        for index, statement in enumerate(statements[fingerprint_index:], fingerprint_index)
        if statement.startswith("UPDATE CPIC_GUIDELINES")
    )
    assert begin_index < fingerprint_index < update_index


def test_refreshes_only_exact_legacy_cyp2d6_tamoxifen_matrix(tmp_path: Path) -> None:
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'ref.db'}")
    reference_metadata.create_all(engine, checkfirst=True)
    preserved_rows = [
        {
            "gene": "CYP2D6",
            "drug": "codeine",
            "phenotype": "Poor Metabolizer",
            "activity_score": None,
            "recommendation": "Preserve unrelated guidance.",
            "classification": "A",
            "guideline_url": "https://example.test/codeine",
        },
        {
            "gene": "CYP2B6",
            "drug": "efavirenz",
            "phenotype": "Normal Metabolizer",
            "activity_score": None,
            "recommendation": "Preserve normal-metabolizer guidance.",
            "classification": "A",
            "guideline_url": _EFAVIRENZ_URL,
        },
    ]
    with engine.begin() as conn:
        conn.execute(cpic_guidelines.insert(), [*_LEGACY_TAMOXIFEN_ROWS, *preserved_rows])
        conn.execute(
            database_versions.insert(),
            {
                "db_name": "cpic",
                "version": "v1.58.0",
                "checksum_sha256": "legacy-checksum",
            },
        )
    with engine.connect() as conn:
        legacy_ids = dict(
            conn.execute(
                sa.select(cpic_guidelines.c.phenotype, cpic_guidelines.c.id).where(
                    cpic_guidelines.c.gene == "CYP2D6",
                    cpic_guidelines.c.drug == "tamoxifen",
                )
            ).fetchall()
        )
        version_before = (
            conn.execute(sa.select(database_versions).where(database_versions.c.db_name == "cpic"))
            .mappings()
            .one()
        )

    assert ensure_reference_schema_current(engine) is True

    with engine.connect() as conn:
        upgraded = list(
            conn.execute(
                sa.select(
                    cpic_guidelines.c.id,
                    cpic_guidelines.c.phenotype,
                    cpic_guidelines.c.activity_score,
                    cpic_guidelines.c.recommendation,
                    cpic_guidelines.c.classification,
                    cpic_guidelines.c.guideline_url,
                )
                .where(
                    cpic_guidelines.c.gene == "CYP2D6",
                    cpic_guidelines.c.drug == "tamoxifen",
                )
                .order_by(cpic_guidelines.c.phenotype)
            ).fetchall()
        )
        preserved = list(
            conn.execute(
                sa.select(
                    cpic_guidelines.c.gene,
                    cpic_guidelines.c.drug,
                    cpic_guidelines.c.phenotype,
                    cpic_guidelines.c.recommendation,
                ).where(cpic_guidelines.c.drug.in_(("codeine", "efavirenz")))
            ).fetchall()
        )
        version_after = (
            conn.execute(sa.select(database_versions).where(database_versions.c.db_name == "cpic"))
            .mappings()
            .one()
        )

    assert {row.phenotype: row.id for row in upgraded} == legacy_ids
    assert {row.phenotype: row.recommendation for row in upgraded} == (
        _CANONICAL_TAMOXIFEN_RECOMMENDATIONS
    )
    assert all(row.activity_score is None for row in upgraded)
    assert {row.classification for row in upgraded} == {"A"}
    assert {row.guideline_url for row in upgraded} == {_TAMOXIFEN_URL}
    assert set(preserved) == {
        ("CYP2D6", "codeine", "Poor Metabolizer", "Preserve unrelated guidance."),
        ("CYP2B6", "efavirenz", "Normal Metabolizer", "Preserve normal-metabolizer guidance."),
    }
    assert dict(version_after) == dict(version_before)
    assert ensure_reference_schema_current(engine) is False


@pytest.mark.parametrize(
    "rows",
    [
        pytest.param(_LEGACY_TAMOXIFEN_ROWS[:2], id="partial"),
        pytest.param(
            [*_LEGACY_TAMOXIFEN_ROWS, _LEGACY_TAMOXIFEN_ROWS[0]],
            id="duplicate",
        ),
        pytest.param(
            [
                {
                    **_LEGACY_TAMOXIFEN_ROWS[0],
                    "recommendation": _CANONICAL_TAMOXIFEN_RECOMMENDATIONS["Normal Metabolizer"],
                },
                *_LEGACY_TAMOXIFEN_ROWS[1:],
            ],
            id="mixed-current",
        ),
        pytest.param(
            [
                {
                    **_LEGACY_TAMOXIFEN_ROWS[0],
                    "recommendation": "Locally curated tamoxifen guidance.",
                },
                *_LEGACY_TAMOXIFEN_ROWS[1:],
            ],
            id="custom",
        ),
        pytest.param(
            [{**row, "drug": "TAMOXIFEN"} for row in _LEGACY_TAMOXIFEN_ROWS],
            id="uppercase-drug",
        ),
        pytest.param(
            [
                {
                    **row,
                    "recommendation": _CANONICAL_TAMOXIFEN_RECOMMENDATIONS[row["phenotype"]],
                }
                for row in _LEGACY_TAMOXIFEN_ROWS
            ],
            id="current",
        ),
    ],
)
def test_tamoxifen_refresh_leaves_nonexact_matrices_untouched_without_loading_bundle(
    tmp_path: Path,
    monkeypatch,
    rows: list[dict],
) -> None:
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'ref.db'}")
    reference_metadata.create_all(engine, checkfirst=True)
    with engine.begin() as conn:
        conn.execute(cpic_guidelines.insert(), rows)
    with engine.connect() as conn:
        before = [
            dict(row)
            for row in conn.execute(
                sa.select(cpic_guidelines).order_by(cpic_guidelines.c.id)
            ).mappings()
        ]

    from backend.annotation import cpic as cpic_module

    def fail_if_parsed(_path):
        raise AssertionError("nonlegacy CYP2D6/tamoxifen content must not load the bundle")

    monkeypatch.setattr(cpic_module, "parse_cpic_guidelines_csv", fail_if_parsed)

    assert ensure_reference_schema_current(engine) is False
    with engine.connect() as conn:
        after = [
            dict(row)
            for row in conn.execute(
                sa.select(cpic_guidelines).order_by(cpic_guidelines.c.id)
            ).mappings()
        ]
    assert after == before


def test_invalid_bundled_tamoxifen_matrix_rolls_back_legacy_rows(
    tmp_path: Path,
    monkeypatch,
) -> None:
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'ref.db'}")
    reference_metadata.create_all(engine, checkfirst=True)
    with engine.begin() as conn:
        conn.execute(cpic_guidelines.insert(), _LEGACY_TAMOXIFEN_ROWS)
    with engine.connect() as conn:
        before = [
            dict(row)
            for row in conn.execute(
                sa.select(cpic_guidelines).order_by(cpic_guidelines.c.id)
            ).mappings()
        ]

    invalid_canonical = [
        {
            **row,
            "recommendation": _CANONICAL_TAMOXIFEN_RECOMMENDATIONS[row["phenotype"]],
        }
        for row in _LEGACY_TAMOXIFEN_ROWS
    ]
    invalid_canonical[0] = {
        **invalid_canonical[0],
        "recommendation": "Unreviewed bundled recommendation.",
    }

    from backend.annotation import cpic as cpic_module

    def parse_invalid_bundle(_path):
        return invalid_canonical, None

    monkeypatch.setattr(cpic_module, "parse_cpic_guidelines_csv", parse_invalid_bundle)

    with pytest.raises(
        RuntimeError,
        match=(
            "Bundled CYP2D6/tamoxifen guideline recommendation is not canonical "
            ".*Normal Metabolizer.*Unreviewed bundled recommendation"
        ),
    ):
        ensure_reference_schema_current(engine)

    with engine.connect() as conn:
        after = [
            dict(row)
            for row in conn.execute(
                sa.select(cpic_guidelines).order_by(cpic_guidelines.c.id)
            ).mappings()
        ]
    assert after == before


def test_tamoxifen_refresh_rolls_back_if_a_canonical_write_fails(tmp_path: Path) -> None:
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'ref.db'}")
    reference_metadata.create_all(engine, checkfirst=True)
    with engine.begin() as conn:
        conn.execute(cpic_guidelines.insert(), _LEGACY_TAMOXIFEN_ROWS)
    with engine.connect() as conn:
        before = [
            dict(row)
            for row in conn.execute(
                sa.select(cpic_guidelines).order_by(cpic_guidelines.c.id)
            ).mappings()
        ]

    update_count = 0

    def fail_second_update(_conn, _cursor, statement, _parameters, _context, _executemany):
        nonlocal update_count
        if " ".join(statement.split()).upper().startswith("UPDATE CPIC_GUIDELINES"):
            update_count += 1
            if update_count == 2:
                raise RuntimeError("simulated tamoxifen write failure")

    sa.event.listen(engine, "before_cursor_execute", fail_second_update)
    try:
        with pytest.raises(RuntimeError, match="simulated tamoxifen write failure"):
            ensure_reference_schema_current(engine)
    finally:
        sa.event.remove(engine, "before_cursor_execute", fail_second_update)

    with engine.connect() as conn:
        after = [
            dict(row)
            for row in conn.execute(
                sa.select(cpic_guidelines).order_by(cpic_guidelines.c.id)
            ).mappings()
        ]
    assert update_count == 2
    assert after == before


def test_tamoxifen_refresh_locks_for_write_before_whole_matrix_fingerprint(
    tmp_path: Path,
) -> None:
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'ref.db'}")
    reference_metadata.create_all(engine, checkfirst=True)
    with engine.begin() as conn:
        conn.execute(cpic_guidelines.insert(), _LEGACY_TAMOXIFEN_ROWS)

    statements: list[tuple[str, object]] = []

    def record_statement(_conn, _cursor, statement, _parameters, _context, _executemany):
        statements.append((" ".join(statement.split()).upper(), _parameters))

    sa.event.listen(engine, "before_cursor_execute", record_statement)
    try:
        assert ensure_reference_schema_current(engine) is True
    finally:
        sa.event.remove(engine, "before_cursor_execute", record_statement)

    fingerprint_indices = [
        index
        for index, (statement, parameters) in enumerate(statements)
        if statement.startswith("SELECT CPIC_GUIDELINES.ID, CPIC_GUIDELINES.PHENOTYPE")
        and parameters == ("CYP2D6", "tamoxifen")
    ]
    assert len(fingerprint_indices) == 1
    fingerprint_index = fingerprint_indices[0]
    begin_index = max(
        index
        for index, (statement, _parameters) in enumerate(statements[:fingerprint_index])
        if statement == "BEGIN IMMEDIATE"
    )
    update_index = next(
        index
        for index, (statement, _parameters) in enumerate(
            statements[fingerprint_index:], fingerprint_index
        )
        if statement.startswith("UPDATE CPIC_GUIDELINES")
    )
    assert begin_index < fingerprint_index < update_index


_CYP2C19_CLOPIDOGREL_URL = "https://cpicpgx.org/guidelines/guideline-for-clopidogrel-and-cyp2c19/"

_LEGACY_CLOPIDOGREL_ROWS = [
    {
        "gene": "CYP2C19",
        "drug": "clopidogrel",
        "phenotype": "Intermediate Metabolizer",
        "activity_score": None,
        "recommendation": "Consider alternative antiplatelet therapy.",
        "classification": "A",
        "guideline_url": _CYP2C19_CLOPIDOGREL_URL,
    },
    {
        "gene": "CYP2C19",
        "drug": "clopidogrel",
        "phenotype": "Poor Metabolizer",
        "activity_score": None,
        "recommendation": "Use alternative antiplatelet therapy.",
        "classification": "A",
        "guideline_url": _CYP2C19_CLOPIDOGREL_URL,
    },
]


def _clopidogrel_recommendations(engine: sa.Engine) -> dict[str, str]:
    with engine.connect() as conn:
        return {
            row.phenotype: row.recommendation
            for row in conn.execute(
                sa.select(cpic_guidelines.c.phenotype, cpic_guidelines.c.recommendation).where(
                    cpic_guidelines.c.gene == "CYP2C19",
                    cpic_guidelines.c.drug == "clopidogrel",
                )
            ).fetchall()
        }


def test_upgrades_the_legacy_cyp2c19_clopidogrel_pair_on_an_existing_database(
    tmp_path: Path,
) -> None:
    """#2026: an already-installed reference.db must receive the corrected guidance.

    Editing the bundled CSV does not reload an existing database, so without this
    migration the correction reaches only fresh installs and every existing user
    keeps the under-warning indefinitely. This exercises the loaded production
    path rather than the CSV.
    """
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'ref.db'}")
    reference_metadata.create_all(engine, checkfirst=True)
    with engine.begin() as conn:
        conn.execute(cpic_guidelines.insert(), _LEGACY_CLOPIDOGREL_ROWS)

    assert ensure_reference_schema_current(engine) is True

    recommendations = _clopidogrel_recommendations(engine)
    assert set(recommendations) == {"Intermediate Metabolizer", "Poor Metabolizer"}
    for phenotype, recommendation in recommendations.items():
        lowered = recommendation.lower()
        assert "avoid" in lowered, (phenotype, recommendation)
        assert "prasugrel" in lowered and "ticagrelor" in lowered, (phenotype, recommendation)
        assert lowered != "consider alternative antiplatelet therapy."


def test_customised_row_does_not_block_repair_of_its_legacy_companion(tmp_path: Path) -> None:
    """A customised row is preserved, and must not shield its companion.

    The first version of this migration fingerprinted the pair together, so a
    customised Poor row made the whole comparison fail and left an Intermediate
    row carrying the exact released under-warning unrepaired — the harmful one
    stale because its companion had been edited. Each phenotype is now
    fingerprinted independently, as the CYP2B6 repair already does.

    Both directions are covered because the failure is asymmetric: leaving the
    Intermediate row stale is the one that keeps under-warning a patient.
    """
    for customised_phenotype in ("Poor Metabolizer", "Intermediate Metabolizer"):
        engine = sa.create_engine(f"sqlite:///{tmp_path / f'ref-{customised_phenotype[:4]}.db'}")
        reference_metadata.create_all(engine, checkfirst=True)
        rows = [dict(row) for row in _LEGACY_CLOPIDOGREL_ROWS]
        for row in rows:
            if row["phenotype"] == customised_phenotype:
                row["recommendation"] = "Site-specific antiplatelet protocol applies."
        with engine.begin() as conn:
            conn.execute(cpic_guidelines.insert(), rows)

        ensure_reference_schema_current(engine)

        recommendations = _clopidogrel_recommendations(engine)
        assert (
            recommendations[customised_phenotype] == "Site-specific antiplatelet protocol applies."
        ), f"{customised_phenotype} customisation was overwritten"

        companion = next(
            phenotype
            for phenotype in ("Intermediate Metabolizer", "Poor Metabolizer")
            if phenotype != customised_phenotype
        )
        repaired = recommendations[companion].lower()
        assert "avoid" in repaired and "prasugrel" in repaired and "ticagrelor" in repaired, (
            f"{companion} was left stale because {customised_phenotype} had been customised: "
            f"{recommendations[companion]}"
        )

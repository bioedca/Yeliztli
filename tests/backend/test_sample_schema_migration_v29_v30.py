"""Tests for the v29 -> v30 case-insensitive search indexes (#2058)."""

from __future__ import annotations

import sqlalchemy as sa

from backend.db.sample_schema import (
    _SEARCH_INDEXES,
    SAMPLE_SCHEMA_VERSION,
    create_sample_tables,
    ensure_sample_schema_current,
)

EXPECTED = {name: (table, column) for table, column, name in _SEARCH_INDEXES}


def _index_names(engine: sa.Engine, table: str) -> set[str]:
    return {index["name"] for index in sa.inspect(engine).get_indexes(table)}


def _index_sql(engine: sa.Engine, name: str) -> str:
    """The index's DDL, upper-cased with identifier quotes stripped (SQLAlchemy
    quotes the collation name; the raw DDL in sample_schema does not)."""
    with engine.connect() as conn:
        sql = conn.execute(
            sa.text("SELECT sql FROM sqlite_master WHERE type = 'index' AND name = :n"),
            {"n": name},
        ).scalar_one()
    return sql.upper().replace('"', "")


def test_fresh_sample_db_carries_the_search_indexes(sample_engine: sa.Engine) -> None:
    assert {"idx_raw_rsid_nocase"} <= _index_names(sample_engine, "raw_variants")
    assert {"idx_annot_rsid_nocase", "idx_annot_gene_nocase"} <= _index_names(
        sample_engine, "annotated_variants"
    )
    for name in EXPECTED:
        assert "COLLATE NOCASE" in _index_sql(sample_engine, name), name


def test_v30_creates_the_indexes_on_an_existing_db(sample_engine: sa.Engine) -> None:
    with sample_engine.begin() as conn:
        for name in EXPECTED:
            conn.execute(sa.text(f'DROP INDEX "{name}"'))
        conn.execute(sa.text("PRAGMA user_version = 29"))
    assert not any(
        name in _index_names(sample_engine, table) for name, (table, _) in EXPECTED.items()
    )

    assert ensure_sample_schema_current(sample_engine) is True

    with sample_engine.connect() as conn:
        version = conn.execute(sa.text("PRAGMA user_version")).scalar_one()
    assert version == SAMPLE_SCHEMA_VERSION == 30
    for name, (table, _column) in EXPECTED.items():
        assert name in _index_names(sample_engine, table), name
        assert "COLLATE NOCASE" in _index_sql(sample_engine, name)

    assert ensure_sample_schema_current(sample_engine) is False


def test_merged_sample_raw_table_from_raw_ddl_gets_the_rsid_index() -> None:
    engine = sa.create_engine("sqlite://")
    create_sample_tables(engine, is_merged_sample=True)
    assert "idx_raw_rsid_nocase" in _index_names(engine, "raw_variants")
    # The merged (chrom, pos) primary key is untouched.
    assert sa.inspect(engine).get_pk_constraint("raw_variants")["constrained_columns"] == [
        "chrom",
        "pos",
    ]


def test_v30_skips_tables_and_columns_a_partial_db_lacks() -> None:
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(sa.text("CREATE TABLE raw_variants (chrom TEXT, pos INTEGER)"))
        conn.execute(sa.text("PRAGMA user_version = 29"))
    ensure_sample_schema_current(engine)
    assert "idx_raw_rsid_nocase" not in _index_names(engine, "raw_variants")
    with engine.connect() as conn:
        assert conn.execute(sa.text("PRAGMA user_version")).scalar_one() == SAMPLE_SCHEMA_VERSION

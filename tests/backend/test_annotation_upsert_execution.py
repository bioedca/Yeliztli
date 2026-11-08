"""Bound annotation SQL construction without weakening output or transaction checks."""

from __future__ import annotations

import sqlite3

import pytest
import sqlalchemy as sa

from backend.annotation.engine import (
    CLINVAR_BIT,
    GNOMAD_BIT,
    VEP_BIT,
    _bulk_upsert,
    _reset_staging_table,
    annotated_variants_staging,
)
from backend.db.tables import annotated_variants


@pytest.fixture(params=["live", "staging"])
def upsert_target(request, sample_engine):
    if request.param == "staging":
        _reset_staging_table(sample_engine)
        return annotated_variants_staging
    return annotated_variants


@pytest.mark.parametrize("variable_limit", [999, 32766])
def test_bulk_upsert_sql_template_does_not_grow_with_row_count(
    sample_engine, upsert_target, monkeypatch, variable_limit
):
    """A wide batch must not rebuild SQL binds for every row (#2609).

    Observe the public DBAPI boundary, not elapsed time on a shared runner.
    Both batch sizes must store their complete values using the same bounded
    statement. The unchanged 10k smoke remains the wall-time acceptance check.
    """
    monkeypatch.setattr(
        "backend.annotation.sqlite_limits.SQLITE_MAX_VARIABLE_NUMBER", variable_limit
    )
    with sample_engine.connect() as conn:
        driver = conn.connection.driver_connection
        previous = driver.setlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, variable_limit)
    observed = []

    def capture(conn, cursor, statement, parameters, context, executemany):
        if statement.startswith(f"INSERT INTO {upsert_target.name} "):
            observed.append((statement, executemany, len(parameters)))

    rows = [
        {
            "rsid": f"rs_perf_{i}",
            "chrom": "1",
            "pos": 100 + i,
            "genotype": "CT",
            "gene_symbol": f"SYNTHETIC_{i}",
            "annotation_coverage": VEP_BIT,
        }
        for i in range(1001)
    ]
    sa.event.listen(sample_engine, "before_cursor_execute", capture)
    try:
        assert _bulk_upsert(sample_engine, rows[:2], upsert_target) == 2
        small = observed.copy()
        observed.clear()
        assert _bulk_upsert(sample_engine, rows, upsert_target) == len(rows)
    finally:
        sa.event.remove(sample_engine, "before_cursor_execute", capture)
        driver.setlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, previous)
    with sample_engine.connect() as conn:
        stored = conn.execute(sa.select(upsert_target).order_by(upsert_target.c.pos)).all()
    assert len(stored) == len(rows) == 1001
    for actual, expected in zip(stored, rows, strict=True):
        assert actual.rsid == expected["rsid"]
        assert actual.pos == expected["pos"]
        assert actual.gene_symbol == expected["gene_symbol"]
        assert actual.annotation_coverage == VEP_BIT
    assert len(small) == len(observed) == 1
    small_sql, small_many, small_rows = small[0]
    large_sql, large_many, large_rows = observed[0]
    assert small_many and large_many
    assert (small_rows, large_rows) == (2, 1001)
    assert large_sql == small_sql
    assert 0 < large_sql.count("?") <= len(upsert_target.columns)


@pytest.mark.parametrize("initial_coverage", [None, VEP_BIT])
def test_bulk_upsert_preserves_sparse_updates_and_duplicate_order(
    sample_engine, upsert_target, initial_coverage
):
    existing = {
        "rsid": "rs_existing",
        "chrom": "1",
        "pos": 100,
        "genotype": "CT",
        "gene_symbol": "OLD",
        "transcript_id": "OLD_TRANSCRIPT",
        "annotation_coverage": initial_coverage,
    }
    assert _bulk_upsert(sample_engine, [existing], upsert_target) == 1
    incoming = [
        {"rsid": "rs_new", "chrom": "1", "pos": 101, "gene_symbol": "NEW"},
        {**existing, "gene_symbol": "INTERMEDIATE", "annotation_coverage": CLINVAR_BIT},
        {
            "rsid": "rs_existing",
            "chrom": "2",
            "pos": 999,
            "genotype": "AA",
            "cadd_phred": 0.0,
            "annotation_coverage": GNOMAD_BIT,
        },
    ]
    assert _bulk_upsert(sample_engine, incoming, upsert_target) == 3
    with sample_engine.connect() as conn:
        stored = {r.rsid: r for r in conn.execute(sa.select(upsert_target))}
    assert set(stored) == {"rs_existing", "rs_new"}
    old = stored["rs_existing"]
    assert (old.chrom, old.pos, old.genotype) == ("1", 100, "CT")
    assert old.gene_symbol is None and old.transcript_id is None
    assert old.cadd_phred == 0.0
    assert old.annotation_coverage == (initial_coverage or 0) | CLINVAR_BIT | GNOMAD_BIT
    new = stored["rs_new"]
    assert new.gene_symbol == "NEW"
    assert new.genotype is None and new.annotation_coverage is None
    # Normalized missing annotations remain explicit NULL, including this
    # column with a server default. Executemany must not silently apply it.
    assert new.mane_select is None


def test_bulk_upsert_rolls_back_earlier_updates_and_inserts(sample_engine, upsert_target):
    existing = {
        "rsid": "rs_retained",
        "chrom": "1",
        "pos": 100,
        "gene_symbol": "RETAINED",
        "annotation_coverage": VEP_BIT,
    }
    assert _bulk_upsert(sample_engine, [existing], upsert_target) == 1
    with sample_engine.connect() as conn:
        before = conn.execute(sa.select(upsert_target)).mappings().all()
    assert len(before) == 1
    incoming = [
        {**existing, "gene_symbol": "MUST_ROLL_BACK", "annotation_coverage": CLINVAR_BIT},
        *({"rsid": f"rs_new_{i}", "chrom": "1", "pos": 200 + i} for i in range(20)),
        {"rsid": "rs_invalid", "chrom": None, "pos": 999},
        {"rsid": "rs_after_failure", "chrom": "1", "pos": 1000},
    ]
    with pytest.raises(sa.exc.IntegrityError):
        _bulk_upsert(sample_engine, incoming, upsert_target)
    with sample_engine.connect() as conn:
        after = conn.execute(sa.select(upsert_target)).mappings().all()
    assert after == before

"""Performance optimization tests (P4-22 / T4-22).

Verifies that the P4-22 performance optimizations are in place:
  - SQLite PRAGMA tuning (cache_size, mmap_size, temp_store)
  - dbNSFP lookup indexes (rsid + chrom/pos), without the dropped covering index
  - Dynamic SQLITE_MAX_VARIABLE_NUMBER detection
  - Per-source timing in AnnotationEngineResult
  - ThreadPoolExecutor reuse across batches (structural)
  - Annotation pipeline meets performance targets
"""

from __future__ import annotations

import time

import pytest
import sqlalchemy as sa
from sqlalchemy.pool import StaticPool

from backend.annotation.dbnsfp import (
    LOOKUP_BATCH_SIZE,
    POSITION_LOOKUP_BATCH_SIZE,
    _create_dbnsfp_indexes,
    _create_dbnsfp_table,
    lookup_dbnsfp_by_rsids,
)
from backend.annotation.engine import AnnotationEngineResult, run_annotation
from backend.annotation.sqlite_limits import SQLITE_MAX_VARIABLE_NUMBER
from backend.db.sample_schema import create_sample_tables
from backend.db.tables import raw_variants, reference_metadata
from scripts.benchmark import (
    BenchmarkDBRegistry,
    create_shared_memory_engine,
    generate_raw_variants,
    seed_clinvar,
    seed_dbnsfp,
    seed_gene_phenotype,
    seed_gnomad,
    seed_vep_bundle,
)

# ── SQLite limit detection ─────────────────────────────────────────────


def test_sqlite_max_variable_number_detected() -> None:
    """SQLITE_MAX_VARIABLE_NUMBER is detected and at least 999."""
    assert SQLITE_MAX_VARIABLE_NUMBER >= 999


def test_lookup_batch_sizes_respect_detected_limit() -> None:
    """Batch sizes are scaled based on the detected SQLite variable limit."""
    assert LOOKUP_BATCH_SIZE >= 500
    assert POSITION_LOOKUP_BATCH_SIZE >= 249
    # Batch sizes should not exceed the detected limit
    assert LOOKUP_BATCH_SIZE <= SQLITE_MAX_VARIABLE_NUMBER
    assert POSITION_LOOKUP_BATCH_SIZE * 4 <= SQLITE_MAX_VARIABLE_NUMBER


# ── PRAGMA tuning ──────────────────────────────────────────────────────


def test_read_optimized_pragmas(tmp_path) -> None:
    """DBRegistry._create_engine applies read-optimized PRAGMAs."""
    from backend.db.connection import DBRegistry

    db_path = tmp_path / "test.db"
    db_path.touch()

    engine = DBRegistry._create_engine(db_path, wal=True, read_optimized=True)
    with engine.connect() as conn:
        cache_size = conn.execute(sa.text("PRAGMA cache_size")).scalar()
        mmap_size = conn.execute(sa.text("PRAGMA mmap_size")).scalar()
        temp_store = conn.execute(sa.text("PRAGMA temp_store")).scalar()

    # cache_size=-65536 means 64 MB (negative = KiB)
    assert cache_size == -65536
    # mmap_size should be 256 MB
    assert mmap_size == 268435456
    # temp_store=2 means MEMORY
    assert temp_store == 2

    engine.dispose()


def test_default_engine_no_read_optimized_pragmas(tmp_path) -> None:
    """Non-read-optimized engines do not set aggressive PRAGMAs."""
    from backend.db.connection import DBRegistry

    db_path = tmp_path / "test_default.db"
    db_path.touch()

    engine = DBRegistry._create_engine(db_path, wal=True, read_optimized=False)
    with engine.connect() as conn:
        cache_size = conn.execute(sa.text("PRAGMA cache_size")).scalar()
        # Default SQLite cache_size is -2000 (2 MB)
        assert cache_size != -65536

    engine.dispose()


# ── dbNSFP lookup indexes ──────────────────────────────────────────────


def test_dbnsfp_lookup_indexes_created() -> None:
    """Index creation builds the two lookup indexes — and NOT the covering index.

    The wide all-columns ``idx_dbnsfp_rsid_covering`` was dropped: on the full
    dbNSFP release it roughly doubles the on-disk DB and its build stalled the
    setup wizard. rsid and chrom/pos lookups stay indexed.
    """
    engine = sa.create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    _create_dbnsfp_table(engine)
    _create_dbnsfp_indexes(engine)

    with engine.connect() as conn:
        indexes = conn.execute(
            sa.text(
                "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='dbnsfp_scores'"
            )
        ).fetchall()
        index_names = {row[0] for row in indexes}

    assert "idx_dbnsfp_rsid" in index_names
    assert "idx_dbnsfp_chrom_pos" in index_names
    assert "idx_dbnsfp_rsid_covering" not in index_names

    engine.dispose()


def test_dbnsfp_rsid_lookup_uses_index() -> None:
    """The rsid IN query is index-seeked (not a full scan) via idx_dbnsfp_rsid."""
    import random

    engine = sa.create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    _create_dbnsfp_table(engine)
    _create_dbnsfp_indexes(engine)

    # Seed with enough rows so the query planner prefers the index over a scan.
    rng = random.Random(42)
    rows = [
        {
            "rsid": f"rs{i}",
            "chrom": str(rng.randint(1, 22)),
            "pos": rng.randint(10_000, 250_000_000),
            "ref": rng.choice(["A", "C", "G", "T"]),
            "alt": rng.choice(["A", "C", "G", "T"]),
            "cadd_phred": rng.uniform(0, 40),
        }
        for i in range(2000)
    ]
    with engine.begin() as conn:
        for r in rows:
            conn.execute(
                sa.text(
                    "INSERT OR IGNORE INTO dbnsfp_scores "
                    "(rsid, chrom, pos, ref, alt, cadd_phred) "
                    "VALUES (:rsid, :chrom, :pos, :ref, :alt, :cadd_phred)"
                ),
                r,
            )
        # Force SQLite to update statistics
        conn.execute(sa.text("ANALYZE"))

    # Check EXPLAIN QUERY PLAN: an indexed SEARCH on idx_dbnsfp_rsid, not a SCAN.
    with engine.connect() as conn:
        plan_rows = conn.execute(
            sa.text(
                "EXPLAIN QUERY PLAN SELECT * FROM dbnsfp_scores "
                "WHERE rsid IN ('rs1', 'rs2', 'rs3')"
            )
        ).fetchall()
        plan_text = " ".join(str(row) for row in plan_rows).lower()

    # The rsid path stays index-seeked even without the (removed) covering index.
    assert "idx_dbnsfp_rsid" in plan_text
    assert "idx_dbnsfp_rsid_covering" not in plan_text
    assert "search" in plan_text  # SEARCH = indexed; a bare SCAN would be a full read

    engine.dispose()


# ── Per-source timing in AnnotationEngineResult ────────────────────────


def test_annotation_result_has_timing_fields() -> None:
    """AnnotationEngineResult includes per-source timing fields."""
    result = AnnotationEngineResult()
    assert hasattr(result, "timing_vep_s")
    assert hasattr(result, "timing_clinvar_s")
    assert hasattr(result, "timing_gnomad_s")
    assert hasattr(result, "timing_dbnsfp_s")
    assert hasattr(result, "timing_alphamissense_s")
    assert hasattr(result, "timing_gene_phenotype_s")
    assert hasattr(result, "timing_merge_s")
    assert hasattr(result, "timing_upsert_s")
    # All start at 0
    assert result.timing_vep_s == 0.0
    assert result.timing_dbnsfp_s == 0.0
    assert result.timing_alphamissense_s == 0.0


def test_annotation_populates_timing() -> None:
    """run_annotation populates timing fields in the result."""
    num = 1_000
    raw_data = generate_raw_variants(num, seed=77)
    rsids = [r["rsid"] for r in raw_data]

    sample_engine = create_shared_memory_engine()
    create_sample_tables(sample_engine)
    with sample_engine.begin() as conn:
        conn.execute(raw_variants.insert(), raw_data)

    reference_engine = create_shared_memory_engine()
    reference_metadata.create_all(reference_engine)
    vep_engine = create_shared_memory_engine()
    gnomad_engine = create_shared_memory_engine()
    dbnsfp_engine = create_shared_memory_engine()

    seed_vep_bundle(vep_engine, rsids, match_rate=0.5, seed=77)
    seed_clinvar(reference_engine, rsids, match_rate=0.05, seed=77)
    seed_gene_phenotype(reference_engine)
    seed_gnomad(gnomad_engine, rsids, match_rate=0.5, seed=77)
    seed_dbnsfp(dbnsfp_engine, rsids, match_rate=0.5, seed=77)

    registry = BenchmarkDBRegistry(
        reference_engine=reference_engine,
        vep_engine=vep_engine,
        gnomad_engine=gnomad_engine,
        dbnsfp_engine=dbnsfp_engine,
    )

    result = run_annotation(sample_engine, registry)

    # Timing fields should be populated (> 0 since we have data)
    assert result.timing_vep_s > 0
    assert result.timing_clinvar_s > 0
    assert result.timing_gnomad_s > 0
    assert result.timing_dbnsfp_s > 0
    assert result.timing_merge_s > 0
    assert result.timing_upsert_s > 0
    assert not result.errors


# ── dbNSFP lookup performance ──────────────────────────────────────────


@pytest.mark.slow  # nightly/benchmark tier: wall-clock timing, flaky on shared runners
def test_dbnsfp_rsid_lookup_performance() -> None:
    """dbNSFP rsid lookups for 10k rsids complete in under 2 seconds."""
    engine = sa.create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    _create_dbnsfp_table(engine)
    _create_dbnsfp_indexes(engine)

    # Seed with 50k variants
    import random

    rng = random.Random(42)
    rows = []
    seen: set[tuple] = set()
    for i in range(50_000):
        chrom = str(rng.randint(1, 22))
        pos = rng.randint(10_000, 250_000_000)
        ref = rng.choice(["A", "C", "G", "T"])
        alt = rng.choice([b for b in ["A", "C", "G", "T"] if b != ref])
        key = (chrom, pos, ref, alt)
        if key in seen:
            continue
        seen.add(key)
        rows.append(
            {
                "rsid": f"rs{1000000 + i}",
                "chrom": chrom,
                "pos": pos,
                "ref": ref,
                "alt": alt,
                "cadd_phred": rng.uniform(0, 40),
                "sift_score": rng.random(),
                "sift_pred": "D",
                "polyphen2_hsvar_score": rng.random(),
                "polyphen2_hsvar_pred": "D",
                "revel": rng.random(),
                "mutpred2": None,
                "vest4": None,
                "metasvm": None,
                "metalr": None,
                "gerp_rs": None,
                "phylop": None,
                "mpc": None,
                "primateai": None,
            }
        )

    with engine.begin() as conn:
        for i in range(0, len(rows), 10_000):
            conn.execute(
                sa.text(
                    "INSERT OR IGNORE INTO dbnsfp_scores "
                    "(rsid, chrom, pos, ref, alt, cadd_phred, sift_score, sift_pred, "
                    "polyphen2_hsvar_score, polyphen2_hsvar_pred, revel, mutpred2, "
                    "vest4, metasvm, metalr, gerp_rs, phylop, mpc, primateai) "
                    "VALUES (:rsid, :chrom, :pos, :ref, :alt, :cadd_phred, :sift_score, "
                    ":sift_pred, :polyphen2_hsvar_score, :polyphen2_hsvar_pred, :revel, "
                    ":mutpred2, :vest4, :metasvm, :metalr, :gerp_rs, :phylop, :mpc, :primateai)"
                ),
                rows[i : i + 10_000],
            )

    # Lookup 10k rsids (mix of matching and non-matching)
    lookup_rsids = [f"rs{1000000 + i}" for i in range(10_000)]

    t0 = time.perf_counter()
    results = lookup_dbnsfp_by_rsids(lookup_rsids, engine)
    elapsed = time.perf_counter() - t0

    assert len(results) > 0
    assert elapsed < 2.0, f"10k rsid lookup took {elapsed:.2f}s, expected < 2s"

    engine.dispose()


# ── Annotation 10k with timing ────────────────────────────────────────


@pytest.mark.slow  # nightly/benchmark tier: wall-clock timing, flaky on shared runners
def test_annotation_10k_with_timing() -> None:
    """10k annotation run populates timing and completes quickly."""
    num = 10_000
    raw_data = generate_raw_variants(num, seed=88)
    rsids = [r["rsid"] for r in raw_data]

    sample_engine = create_shared_memory_engine()
    create_sample_tables(sample_engine)
    with sample_engine.begin() as conn:
        conn.execute(raw_variants.insert(), raw_data)

    reference_engine = create_shared_memory_engine()
    reference_metadata.create_all(reference_engine)
    vep_engine = create_shared_memory_engine()
    gnomad_engine = create_shared_memory_engine()
    dbnsfp_engine = create_shared_memory_engine()

    seed_vep_bundle(vep_engine, rsids, match_rate=0.7, seed=88)
    seed_clinvar(reference_engine, rsids, match_rate=0.05, seed=88)
    seed_gene_phenotype(reference_engine)
    seed_gnomad(gnomad_engine, rsids, match_rate=0.6, seed=88)
    seed_dbnsfp(dbnsfp_engine, rsids, match_rate=0.5, seed=88)

    registry = BenchmarkDBRegistry(
        reference_engine=reference_engine,
        vep_engine=vep_engine,
        gnomad_engine=gnomad_engine,
        dbnsfp_engine=dbnsfp_engine,
    )

    t0 = time.perf_counter()
    result = run_annotation(sample_engine, registry)
    elapsed = time.perf_counter() - t0

    assert result.total_variants == num
    assert result.rows_written > 0
    assert not result.errors
    assert elapsed < 30.0, f"10k annotation took {elapsed:.1f}s"

    # Verify timing breakdown is populated
    total_source_time = (
        result.timing_vep_s
        + result.timing_clinvar_s
        + result.timing_gnomad_s
        + result.timing_dbnsfp_s
        + result.timing_alphamissense_s
    )
    assert total_source_time > 0, "Source timing not populated"

    # Log the breakdown for manual inspection
    print(f"\n  10k annotation: {elapsed:.2f}s total")
    print(f"    VEP:     {result.timing_vep_s:.3f}s")
    print(f"    ClinVar: {result.timing_clinvar_s:.3f}s")
    print(f"    gnomAD:  {result.timing_gnomad_s:.3f}s")
    print(f"    dbNSFP:  {result.timing_dbnsfp_s:.3f}s")
    print(f"    AlphaM:  {result.timing_alphamissense_s:.3f}s")
    print(f"    GenePhe: {result.timing_gene_phenotype_s:.3f}s")
    print(f"    Merge:   {result.timing_merge_s:.3f}s")
    print(f"    Upsert:  {result.timing_upsert_s:.3f}s")

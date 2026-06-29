"""Tests for the SW-C4 imputation reachability/feasibility report.

Covers the three factual signals — structural panel coverage (on/off-panel
chromosomes), descriptive backbone density (per-chromosome median gap), and
realized reachability from persisted imputed variants — plus graceful degradation
when a sample has no imputation or no typed variants.
"""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path
from unittest.mock import patch

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from backend.analysis.imputation_reachability import (
    panel_covers,
    runtime_imputable,
    summarize_sample_reachability,
)
from backend.config import Settings
from backend.db.connection import reset_registry
from backend.db.sample_schema import create_sample_tables
from backend.db.tables import annotated_variants, imputed_variants, reference_metadata, samples


def _seed_typed(engine: sa.Engine, rows: list[dict]) -> None:
    with engine.begin() as conn:
        conn.execute(sa.insert(annotated_variants), rows)


def _typed(rsid: str, chrom: str, pos: int) -> dict:
    return {"rsid": rsid, "chrom": chrom, "pos": pos}


def _seed_imputed(engine: sa.Engine, rows: list[dict]) -> None:
    with engine.begin() as conn:
        conn.execute(sa.insert(imputed_variants), rows)


def _imp(chrom: str, pos: int, *, ref: str = "A", alt: str = "G") -> dict:
    return {
        "chrom": chrom,
        "pos": pos,
        "ref": ref,
        "alt": alt,
        "dr2": 0.95,
        "af": 0.2,
        "dosage": 1.0,
    }


# ── panel_covers (structural) ─────────────────────────────────────────────


@pytest.mark.parametrize(
    "chrom,expected",
    [
        ("1", True),
        ("22", True),
        ("chr22", True),
        ("X", True),
        ("x", True),
        ("Y", False),  # panel is autosomes + X only
        ("chrY", False),
        ("MT", False),
        ("M", False),
        ("", False),
        (None, False),
    ],
)
def test_panel_covers(chrom: str | None, expected: bool) -> None:
    assert panel_covers(chrom) is expected


@pytest.mark.parametrize(
    "chrom,expected",
    [
        ("1", True),
        ("22", True),
        ("chr22", True),
        ("X", False),  # on the panel but runtime-deferred (ploidy/PAR) — not v1-imputable
        ("x", False),
        ("Y", False),
        ("MT", False),
        ("", False),
        (None, False),
    ],
)
def test_runtime_imputable(chrom: str | None, expected: bool) -> None:
    # The v1 runtime images autosomes only; X is panel-covered but deferred, so it
    # must NOT count as runtime-imputable even though panel_covers("X") is True.
    assert runtime_imputable(chrom) is expected
    if chrom in ("X", "x"):
        assert panel_covers(chrom) is True  # the exact mismatch this issue is about


# ── summarize: empty / graceful ───────────────────────────────────────────


def test_empty_sample(sample_engine: sa.Engine) -> None:
    s = summarize_sample_reachability(sample_engine)
    assert s.typed_total == 0
    assert s.typed_on_panel == 0
    assert s.typed_runtime_imputable == 0
    assert s.typed_panel_runtime_deferred == 0
    assert s.typed_off_panel == 0
    assert s.imputation_run is False
    assert s.imputed_reachable == 0
    assert s.per_chromosome == []
    assert s.per_chromosome_runtime_deferred == []
    # Panel provenance always reported (autosomes + X) ...
    assert s.panel_build == "GRCh37"
    assert "X" in s.panel_chromosomes and "Y" not in s.panel_chromosomes
    # ... while the runtime scope is autosomes-only (X deferred).
    assert "X" not in s.runtime_chromosomes
    assert s.runtime_chromosomes == [str(i) for i in range(1, 23)]


def test_missing_imputed_table_is_graceful(sample_engine: sa.Engine) -> None:
    imputed_variants.drop(sample_engine)
    _seed_typed(sample_engine, [_typed("rs1", "1", 1000)])
    s = summarize_sample_reachability(sample_engine)
    assert s.imputed_reachable == 0
    assert s.imputation_run is False
    assert s.typed_on_panel == 1


# ── on/off-panel partition + density ──────────────────────────────────────


def test_on_off_panel_partition(sample_engine: sa.Engine) -> None:
    _seed_typed(
        sample_engine,
        [
            _typed("rs1", "1", 1000),
            _typed("rs2", "1", 1100),
            _typed("rs3", "X", 5000),  # on panel but runtime-deferred (not headline-reachable)
            _typed("rsY", "Y", 200),  # off-panel (structurally unreachable)
            _typed("rsM", "MT", 50),  # off-panel
        ],
    )
    s = summarize_sample_reachability(sample_engine)
    assert s.typed_total == 5
    # Structural panel membership still includes X ...
    assert s.typed_on_panel == 3
    # ... but the runtime-imputable headline backbone is autosomes only ...
    assert s.typed_runtime_imputable == 2
    # ... and X is surfaced separately as panel-available-but-runtime-deferred.
    assert s.typed_panel_runtime_deferred == 1
    assert s.typed_off_panel == 2
    # The three runtime/deferred/off-panel buckets partition the typed total.
    assert s.typed_runtime_imputable + s.typed_panel_runtime_deferred + s.typed_off_panel == 5
    # Headline per-chromosome density covers runtime chromosomes only; X moves to
    # the parallel deferred list (off-panel chromosomes excluded from both).
    assert {c.chrom for c in s.per_chromosome} == {"1"}
    assert {c.chrom for c in s.per_chromosome_runtime_deferred} == {"X"}


def test_chrom_prefix_normalized(sample_engine: sa.Engine) -> None:
    _seed_typed(sample_engine, [_typed("rs1", "chr1", 1000), _typed("rs2", "1", 1100)])
    s = summarize_sample_reachability(sample_engine)
    assert s.typed_on_panel == 2
    by_chrom = {c.chrom: c for c in s.per_chromosome}
    assert set(by_chrom) == {"1"}
    assert by_chrom["1"].typed_markers == 2


def test_median_gap(sample_engine: sa.Engine) -> None:
    # gaps 100, 200 → median 150; single-marker chrom → None.
    _seed_typed(
        sample_engine,
        [
            _typed("rs1", "1", 1000),
            _typed("rs2", "1", 1100),
            _typed("rs3", "1", 1300),
            _typed("rs4", "2", 5000),
        ],
    )
    s = summarize_sample_reachability(sample_engine)
    by_chrom = {c.chrom: c for c in s.per_chromosome}
    assert by_chrom["1"].median_gap_bp == 150
    assert by_chrom["1"].typed_markers == 3
    assert by_chrom["2"].median_gap_bp is None  # only one marker
    assert by_chrom["2"].typed_markers == 1


def test_dedupes_loci(sample_engine: sa.Engine) -> None:
    # annotated_variants is rsid-unique: two rsIDs at one (chrom, pos) are one locus,
    # so they count once and never produce a spurious zero-gap density.
    _seed_typed(
        sample_engine,
        [
            _typed("rs1", "1", 1000),
            _typed("rs1_alt", "1", 1000),  # same locus, different rsID
            _typed("rs2", "1", 1300),
        ],
    )
    s = summarize_sample_reachability(sample_engine)
    assert s.typed_total == 2  # not 3
    by_chrom = {c.chrom: c for c in s.per_chromosome}
    assert by_chrom["1"].typed_markers == 2
    assert by_chrom["1"].median_gap_bp == 300  # one gap (1000->1300), no zero gap


def test_per_chromosome_in_panel_order(sample_engine: sa.Engine) -> None:
    # Seeded out of order; report follows the panel's chromosome order. X is
    # runtime-deferred, so it appears in the parallel deferred list, not the
    # headline runtime density.
    _seed_typed(
        sample_engine,
        [_typed("rsX", "X", 1), _typed("rs2", "2", 1), _typed("rs1", "1", 1)],
    )
    s = summarize_sample_reachability(sample_engine)
    assert [c.chrom for c in s.per_chromosome] == ["1", "2"]
    assert [c.chrom for c in s.per_chromosome_runtime_deferred] == ["X"]


def test_x_heavy_sample_does_not_inflate_runtime_headline(sample_engine: sa.Engine) -> None:
    """#1186: an X-heavy sample must not look more v1-reachable than it is.

    The reference panel covers X but the v1 input/persist pipeline is autosomes-only
    (X deferred for ploidy/PAR handling). A sample whose typed markers are mostly on
    X must report a headline runtime-imputable backbone reflecting only its few
    autosomal markers, with the X markers surfaced as runtime-deferred — never
    counted toward reachability the pipeline cannot realize.
    """
    _seed_typed(
        sample_engine,
        [
            _typed("rsx1", "X", 1000),
            _typed("rsx2", "X", 2000),
            _typed("rsx3", "X", 3000),
            _typed("rsx4", "X", 4000),
            _typed("rs_auto", "1", 500),  # the sole runtime-imputable marker
        ],
    )
    s = summarize_sample_reachability(sample_engine)
    assert s.typed_total == 5
    # Headline reachability reflects ONLY the autosomal backbone, not the 4 X loci.
    assert s.typed_runtime_imputable == 1
    assert [c.chrom for c in s.per_chromosome] == ["1"]
    # X coverage is reported, but as panel-available-but-runtime-deferred.
    assert s.typed_panel_runtime_deferred == 4
    deferred = {c.chrom: c for c in s.per_chromosome_runtime_deferred}
    assert deferred["X"].typed_markers == 4
    # Structural panel membership still accounts for X (provenance), and nothing is
    # mislabeled structurally off-panel.
    assert s.typed_on_panel == 5
    assert s.typed_off_panel == 0


def test_x_only_sample_reports_zero_runtime_reachability(sample_engine: sa.Engine) -> None:
    """An X-only sample has zero v1 runtime reachability despite full panel coverage."""
    _seed_typed(
        sample_engine,
        [_typed("rsx1", "X", 1000), _typed("rsx2", "X", 2000)],
    )
    s = summarize_sample_reachability(sample_engine)
    assert s.typed_runtime_imputable == 0
    assert s.per_chromosome == []
    assert s.typed_panel_runtime_deferred == 2
    assert s.typed_on_panel == 2  # structurally on panel
    assert s.typed_off_panel == 0


# ── realized reachability ─────────────────────────────────────────────────


def test_realized_reachability(sample_engine: sa.Engine) -> None:
    _seed_typed(sample_engine, [_typed("rs1", "1", 1000)])
    _seed_imputed(sample_engine, [_imp("1", 2000), _imp("1", 3000), _imp("2", 4000)])
    s = summarize_sample_reachability(sample_engine)
    assert s.imputed_reachable == 3
    assert s.imputation_run is True
    # Imputed sites do not inflate the typed backbone counts.
    assert s.typed_total == 1
    assert s.typed_on_panel == 1


# ── Route ─────────────────────────────────────────────────────────────────


@pytest.fixture
def reachability_client(tmp_path: Path) -> Generator[TestClient, None, None]:
    """Test client with a sample DB seeded with typed + imputed variants on disk."""
    data_dir = tmp_path / "data"
    (data_dir / "samples").mkdir(parents=True)
    settings = Settings(data_dir=data_dir, wal_mode=False)

    ref_engine = sa.create_engine(f"sqlite:///{settings.reference_db_path}")
    reference_metadata.create_all(ref_engine)

    sample_db_path = data_dir / "samples" / "sample_1.db"
    sample_engine = sa.create_engine(f"sqlite:///{sample_db_path}")
    create_sample_tables(sample_engine)
    with ref_engine.begin() as conn:
        conn.execute(
            samples.insert().values(
                id=1, name="S", db_path="samples/sample_1.db", file_format="v5", file_hash="h"
            )
        )
    _seed_typed(
        sample_engine,
        [
            _typed("rs1", "1", 1000),
            _typed("rs2", "1", 1300),
            _typed("rsX", "X", 5000),  # panel-covered but runtime-deferred
            _typed("rsY", "Y", 10),  # off-panel
        ],
    )
    _seed_imputed(sample_engine, [_imp("1", 2000)])
    ref_engine.dispose()
    sample_engine.dispose()

    with (
        patch("backend.main.get_settings", return_value=settings),
        patch("backend.db.connection.get_settings", return_value=settings),
    ):
        reset_registry()
        from backend.main import create_app

        with TestClient(create_app()) as tc:
            yield tc
        reset_registry()


def test_reachability_endpoint(reachability_client: TestClient) -> None:
    resp = reachability_client.get("/api/imputation/reachability?sample_id=1")
    assert resp.status_code == 200
    data = resp.json()
    assert data["panel_build"] == "GRCh37"
    assert data["typed_total"] == 4
    # Structural panel membership includes X; the runtime headline does not.
    assert data["typed_on_panel"] == 3
    assert data["typed_runtime_imputable"] == 2  # autosomal backbone only
    assert data["typed_panel_runtime_deferred"] == 1  # the chrX marker
    assert data["typed_off_panel"] == 1  # the chrY marker
    assert "X" not in data["runtime_chromosomes"]
    assert data["imputation_run"] is True
    assert data["imputed_reachable"] == 1
    chrom1 = next(c for c in data["per_chromosome"] if c["chrom"] == "1")
    assert chrom1["typed_markers"] == 2
    assert chrom1["median_gap_bp"] == 300
    # X is reported only in the runtime-deferred list, never the headline density.
    assert "X" not in {c["chrom"] for c in data["per_chromosome"]}
    assert {c["chrom"] for c in data["per_chromosome_runtime_deferred"]} == {"X"}


def test_reachability_endpoint_unknown_sample(reachability_client: TestClient) -> None:
    assert reachability_client.get("/api/imputation/reachability?sample_id=999").status_code == 404

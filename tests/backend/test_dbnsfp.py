"""Tests for dbNSFP SQLite loader (P2-11).

Covers:
- T2-11: dbNSFP lookup returns correct CADD, REVEL scores for rs1801133 (MTHFR C677T)
- TSV line parsing (valid, missing fields, no scores, invalid chrom)
- Multi-transcript score parsing (semicolon-separated values)
- CSV loading into dbnsfp_scores table
- Batch lookup by rsid and by (chrom, pos, ref, alt)
- Table creation and index creation
- Version recording in database_versions
- Download function structure
- Ensemble pathogenicity helpers (deleterious_count, is_ensemble_pathogenic)
- LoadStats dataclass
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy.pool import StaticPool
from structlog.testing import capture_logs

from backend.annotation.dbnsfp import (
    BATCH_SIZE,
    DBNSFP_BITMASK,
    DBNSFP_FIELDS,
    LOOKUP_BATCH_SIZE,
    DbNSFPAnnotation,
    DbNSFPRecord,
    LoadStats,
    _create_dbnsfp_indexes,
    _create_dbnsfp_table,
    _normalize_chrom,
    _parse_dbnsfp_float,
    _parse_float,
    _version_at_least,
    assess_ensemble,
    download_and_load_dbnsfp,
    download_dbnsfp,
    is_ensemble_pathogenic,
    load_dbnsfp_from_csv,
    lookup_dbnsfp_by_positions,
    lookup_dbnsfp_by_rsids,
    parse_dbnsfp_tsv_line,
    record_dbnsfp_version,
)
from backend.db.tables import database_versions, reference_metadata

# ── Fixtures ────────────────────────────────────────────────────────────

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures"
DBNSFP_SEED_CSV = FIXTURES_DIR / "seed_csvs" / "dbnsfp_seed.csv"


@pytest.fixture
def dbnsfp_engine() -> sa.Engine:
    """In-memory dbNSFP engine with tables created."""
    engine = sa.create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    _create_dbnsfp_table(engine)
    _create_dbnsfp_indexes(engine)
    return engine


@pytest.fixture
def dbnsfp_engine_with_data(dbnsfp_engine: sa.Engine) -> sa.Engine:
    """dbNSFP engine loaded from seed CSV."""
    load_dbnsfp_from_csv(DBNSFP_SEED_CSV, dbnsfp_engine, clear_existing=False)
    return dbnsfp_engine


@pytest.fixture
def reference_engine() -> sa.Engine:
    """In-memory reference engine for version tracking."""
    engine = sa.create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    reference_metadata.create_all(engine)
    return engine


# ── Helper parsing tests ─────────────────────────────────────────────────


class TestParseHelpers:
    def test_normalize_chrom_valid(self):
        assert _normalize_chrom("1") == "1"
        assert _normalize_chrom("chr1") == "1"
        assert _normalize_chrom("chrX") == "X"
        assert _normalize_chrom("22") == "22"
        assert _normalize_chrom("MT") == "MT"

    def test_normalize_chrom_invalid(self):
        assert _normalize_chrom("chr0") is None
        assert _normalize_chrom("chrUn") is None
        assert _normalize_chrom("") is None

    def test_parse_float_valid(self):
        assert _parse_float("1.5") == 1.5
        assert _parse_float("0") == 0.0
        assert _parse_float("-1.2") == -1.2

    def test_parse_float_missing(self):
        assert _parse_float(None) is None
        assert _parse_float(".") is None
        assert _parse_float("") is None
        assert _parse_float("-") is None

    def test_parse_dbnsfp_float_single(self):
        assert _parse_dbnsfp_float("0.5") == 0.5

    def test_parse_dbnsfp_float_multi_transcript(self):
        """dbNSFP stores multiple scores separated by semicolons."""
        assert _parse_dbnsfp_float("0.3;0.5;0.7") == 0.3
        assert _parse_dbnsfp_float(".;0.5;0.7") == 0.5
        assert _parse_dbnsfp_float(".;.;0.7") == 0.7

    def test_parse_dbnsfp_float_all_missing(self):
        assert _parse_dbnsfp_float(".;.;.") is None
        assert _parse_dbnsfp_float(".") is None


# ── TSV line parsing tests ───────────────────────────────────────────────


class TestParseDbnsfpTsvLine:
    """Test TSV line parsing from dbNSFP dict format."""

    def _make_fields(self, **overrides) -> dict[str, str]:
        """Build a minimal valid dbNSFP TSV row dict."""
        base = {
            "#chr": "19",
            "pos(1-based)": "44908684",
            "ref": "T",
            "alt": "C",
            "rs_dbSNP": "rs429358",
            "CADD_phred": "28.3",
            "SIFT4G_score": "0.001",
            "SIFT4G_pred": "D",
            "Polyphen2_HVAR_score": "0.998",
            "Polyphen2_HVAR_pred": "D",
            "REVEL_score": "0.812",
            "MutPred2_score": "0.780",
            "VEST4_score": "0.891",
            "MetaSVM_score": "0.920",
            "MetaLR_score": "0.885",
            "GERP++_RS": "5.48",
            "phyloP100way_vertebrate": "7.92",
            "MPC_score": "1.85",
            "PrimateAI_score": "0.91",
        }
        base.update(overrides)
        return base

    def test_valid_line(self):
        fields = self._make_fields()
        record, skip = parse_dbnsfp_tsv_line(fields)

        assert skip is None
        assert record is not None
        assert record.rsid == "rs429358"
        assert record.chrom == "19"
        assert record.pos == 44908684
        assert record.ref == "T"
        assert record.alt == "C"
        assert record.cadd_phred == pytest.approx(28.3)
        assert record.sift_score == pytest.approx(0.001)
        assert record.sift_pred == "D"
        assert record.revel == pytest.approx(0.812)

    def test_chr_prefix_stripped(self):
        fields = self._make_fields(**{"#chr": "chr19"})
        record, skip = parse_dbnsfp_tsv_line(fields)
        assert record is not None
        assert record.chrom == "19"

    def test_invalid_chrom(self):
        fields = self._make_fields(**{"#chr": "chrUn"})
        record, skip = parse_dbnsfp_tsv_line(fields)
        assert record is None
        assert skip == "invalid_chrom"

    def test_missing_pos(self):
        fields = self._make_fields(**{"pos(1-based)": ""})
        record, skip = parse_dbnsfp_tsv_line(fields)
        assert record is None
        assert skip == "malformed"

    def test_missing_ref(self):
        fields = self._make_fields(ref="")
        record, skip = parse_dbnsfp_tsv_line(fields)
        assert record is None
        assert skip == "malformed"

    def test_no_rsid_still_loads(self):
        """Variants without rsids should still be loaded (keyed on chrom/pos/ref/alt)."""
        fields = self._make_fields(**{"rs_dbSNP": "."})
        record, skip = parse_dbnsfp_tsv_line(fields)
        assert record is not None
        assert record.rsid is None

    def test_multi_rsid_takes_first(self):
        fields = self._make_fields(**{"rs_dbSNP": "rs429358;rs12345"})
        record, skip = parse_dbnsfp_tsv_line(fields)
        assert record is not None
        assert record.rsid == "rs429358"

    def test_no_scores_skipped(self):
        fields = {
            "#chr": "1",
            "pos(1-based)": "100",
            "ref": "A",
            "alt": "G",
            "rs_dbSNP": "rs1",
        }
        record, skip = parse_dbnsfp_tsv_line(fields)
        assert record is None
        assert skip == "no_scores"

    def test_multi_transcript_scores(self):
        """Multi-transcript semicolon-separated SIFT fields choose the damaging score."""
        fields = self._make_fields(
            CADD_phred="28.3",
            SIFT4G_score=".;0.002;0.005",
            SIFT4G_pred=".;D;T",
        )
        record, skip = parse_dbnsfp_tsv_line(fields)
        assert record is not None
        assert record.sift_score == pytest.approx(0.002)
        assert record.sift_pred == "D"

    def test_multi_transcript_sift_polyphen_use_most_damaging_values(self):
        """SIFT/PolyPhen summaries are order-independent over transcript values."""
        fields = self._make_fields(
            CADD_phred=".",
            SIFT4G_score="0.2;0.01",
            SIFT4G_pred="T;D",
            Polyphen2_HVAR_score="0.1;0.99",
            Polyphen2_HVAR_pred="B;D",
            REVEL_score=".",
            MetaSVM_score=".",
            MetaLR_score=".",
        )
        record, skip = parse_dbnsfp_tsv_line(fields)
        assert record is not None
        assert skip is None
        assert record.sift_score == pytest.approx(0.01)
        assert record.sift_pred == "D"
        assert record.polyphen2_hsvar_score == pytest.approx(0.99)
        assert record.polyphen2_hsvar_pred == "D"
        assert assess_ensemble(record) == (2, 2)

        reversed_fields = self._make_fields(
            CADD_phred=".",
            SIFT4G_score="0.01;0.2",
            SIFT4G_pred="D;T",
            Polyphen2_HVAR_score="0.99;0.1",
            Polyphen2_HVAR_pred="D;B",
            REVEL_score=".",
            MetaSVM_score=".",
            MetaLR_score=".",
        )
        reversed_record, reversed_skip = parse_dbnsfp_tsv_line(reversed_fields)
        assert reversed_record is not None
        assert reversed_skip is None
        assert reversed_record.sift_score == record.sift_score
        assert reversed_record.sift_pred == record.sift_pred
        assert reversed_record.polyphen2_hsvar_score == record.polyphen2_hsvar_score
        assert reversed_record.polyphen2_hsvar_pred == record.polyphen2_hsvar_pred
        assert assess_ensemble(reversed_record) == (2, 2)

    def test_comma_separated_sift4g_chooses_damaging(self):
        """#983: when a variant matches multiple dbNSFP records, SIFT4G values are
        joined with ',' (not ';'). The parser must split on either delimiter, else
        a comma-joined damaging transcript is dropped (sift_score=None,
        sift_pred='T,D') and the SIFT axis silently under-calls."""
        fields = self._make_fields(
            CADD_phred=".",
            SIFT4G_score="0.2,0.01",
            SIFT4G_pred="T,D",
            Polyphen2_HVAR_score=".",
            Polyphen2_HVAR_pred=".",
            REVEL_score=".",
            MetaSVM_score=".",
            MetaLR_score=".",
        )
        record, skip = parse_dbnsfp_tsv_line(fields)
        assert record is not None
        assert skip is None
        assert record.sift_score == pytest.approx(0.01)
        assert record.sift_pred == "D"
        # The damaging transcript must register on the SIFT axis — pre-fix this
        # was (0, 1): one axis present (SIFT), zero damaging.
        assert assess_ensemble(record) == (1, 1)

    def test_mixed_comma_and_semicolon_sift4g_delimiters(self):
        """Both delimiters can co-occur — ';' separates transcripts within a
        record, ',' joins values across multiple matched records — so the worst
        transcript must still be chosen from a mixed string."""
        fields = self._make_fields(
            CADD_phred=".",
            SIFT4G_score="0.2;0.5,0.01",
            SIFT4G_pred="T;T,D",
            Polyphen2_HVAR_score=".",
            Polyphen2_HVAR_pred=".",
            REVEL_score=".",
            MetaSVM_score=".",
            MetaLR_score=".",
        )
        record, skip = parse_dbnsfp_tsv_line(fields)
        assert record is not None
        assert record.sift_score == pytest.approx(0.01)
        assert record.sift_pred == "D"

    def test_partial_scores(self):
        """Record with only some scores should still be loaded."""
        fields = self._make_fields(
            SIFT4G_score=".",
            SIFT4G_pred=".",
            Polyphen2_HVAR_score=".",
            Polyphen2_HVAR_pred=".",
            REVEL_score=".",
            MutPred2_score=".",
            VEST4_score=".",
            MetaSVM_score=".",
            MetaLR_score=".",
            MPC_score=".",
            PrimateAI_score=".",
        )
        record, skip = parse_dbnsfp_tsv_line(fields)
        assert record is not None
        assert record.cadd_phred == pytest.approx(28.3)
        assert record.sift_score is None


# ── Table creation tests ─────────────────────────────────────────────────


class TestCreateDbnsfpTables:
    def test_creates_table(self, dbnsfp_engine: sa.Engine):
        with dbnsfp_engine.connect() as conn:
            result = conn.execute(
                sa.text(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='dbnsfp_scores'"
                )
            ).fetchone()
        assert result is not None

    def test_creates_indexes(self, dbnsfp_engine: sa.Engine):
        with dbnsfp_engine.connect() as conn:
            indexes = conn.execute(
                sa.text(
                    "SELECT name FROM sqlite_master"
                    " WHERE type='index' AND tbl_name='dbnsfp_scores'"
                )
            ).fetchall()
        index_names = {r[0] for r in indexes}
        assert "idx_dbnsfp_rsid" in index_names
        assert "idx_dbnsfp_chrom_pos" in index_names

    def test_idempotent(self, dbnsfp_engine: sa.Engine):
        """Calling table + index creation twice should not error."""
        _create_dbnsfp_table(dbnsfp_engine)
        _create_dbnsfp_indexes(dbnsfp_engine)
        # Should not raise


# ── CSV loading tests ────────────────────────────────────────────────────


class TestLoadDbnsfpFromCsv:
    def test_loads_seed_data(self, dbnsfp_engine_with_data: sa.Engine):
        with dbnsfp_engine_with_data.connect() as conn:
            count = conn.execute(sa.text("SELECT COUNT(*) FROM dbnsfp_scores")).scalar()
        assert count == 61  # 62 lines - 1 header = 61 data rows

    def test_stats_correct(self, dbnsfp_engine: sa.Engine):
        stats = load_dbnsfp_from_csv(DBNSFP_SEED_CSV, dbnsfp_engine)
        assert stats.total_lines == 61
        assert stats.variants_loaded == 61

    def test_clear_existing(self, dbnsfp_engine_with_data: sa.Engine):
        """Loading with clear_existing=True replaces data."""
        stats = load_dbnsfp_from_csv(DBNSFP_SEED_CSV, dbnsfp_engine_with_data, clear_existing=True)
        assert stats.variants_loaded == 61

    def test_known_variant_rs1801133(self, dbnsfp_engine_with_data: sa.Engine):
        """T2-11: Verify CADD and REVEL scores for rs1801133 (MTHFR C677T)."""
        with dbnsfp_engine_with_data.connect() as conn:
            row = conn.execute(
                sa.text("SELECT * FROM dbnsfp_scores WHERE rsid = 'rs1801133'")
            ).fetchone()
        assert row is not None
        assert row.cadd_phred == pytest.approx(24.8)
        assert row.revel == pytest.approx(0.689)
        assert row.chrom == "1"
        assert row.pos == 11856378

    def test_null_scores_preserved(self, dbnsfp_engine_with_data: sa.Engine):
        """Rows with some NULL scores should have NULLs in the DB."""
        with dbnsfp_engine_with_data.connect() as conn:
            row = conn.execute(
                sa.text("SELECT * FROM dbnsfp_scores WHERE rsid = 'rs80357906'")
            ).fetchone()
        assert row is not None
        assert row.cadd_phred == pytest.approx(35.0)
        # These are empty in seed CSV → NULL
        assert row.sift_score is None
        assert row.sift_pred is None


# ── Lookup tests ─────────────────────────────────────────────────────────


class TestLookupDbnsfpByRsids:
    def test_returns_correct_scores(self, dbnsfp_engine_with_data: sa.Engine):
        """T2-11: dbNSFP lookup returns correct CADD, REVEL for rs1801133."""
        results = lookup_dbnsfp_by_rsids(["rs1801133"], dbnsfp_engine_with_data)
        assert "rs1801133" in results
        annot = results["rs1801133"]
        assert annot.cadd_phred == pytest.approx(24.8)
        assert annot.revel == pytest.approx(0.689)
        assert annot.sift_pred == "D"
        assert annot.chrom == "1"
        assert annot.pos == 11856378

    def test_batch_lookup(self, dbnsfp_engine_with_data: sa.Engine):
        results = lookup_dbnsfp_by_rsids(
            ["rs429358", "rs7412", "rs_nonexistent"], dbnsfp_engine_with_data
        )
        assert "rs429358" in results
        assert "rs7412" in results
        assert "rs_nonexistent" not in results
        assert results["rs429358"].cadd_phred == pytest.approx(28.3)
        assert results["rs7412"].cadd_phred == pytest.approx(26.1)

    def test_empty_rsids(self, dbnsfp_engine_with_data: sa.Engine):
        results = lookup_dbnsfp_by_rsids([], dbnsfp_engine_with_data)
        assert len(results) == 0

    def test_large_batch_chunking(self, dbnsfp_engine_with_data: sa.Engine):
        """Ensure batching works for lists larger than LOOKUP_BATCH_SIZE."""
        # Create 600 rsids (beyond the 500 batch limit), mostly non-existent
        rsids = [f"rs{i}" for i in range(600)]
        rsids.append("rs429358")  # one real one
        results = lookup_dbnsfp_by_rsids(rsids, dbnsfp_engine_with_data)
        assert "rs429358" in results

    def test_deleterious_count_computed(self, dbnsfp_engine_with_data: sa.Engine):
        """DbNSFPAnnotation.deleterious_count should be auto-computed."""
        results = lookup_dbnsfp_by_rsids(["rs429358"], dbnsfp_engine_with_data)
        annot = results["rs429358"]
        # rs429358: SIFT=0.001(D), PP2=0.998(D), CADD=28.3(D), REVEL/MetaSVM/MetaLR(D).
        # F24: REVEL/MetaSVM/MetaLR collapse into ONE meta axis → 4 independent
        # axes (SIFT, PolyPhen, CADD, META), all deleterious.
        assert annot.deleterious_count == 4
        assert annot.deleterious_total_assessed == 4


class TestLookupDbnsfpByPositions:
    # dbnsfp.db is GRCh38-coordinate; the join is only valid for GRCh38 inputs,
    # so the tests that exercise the SQL join declare source_build="GRCh38"
    # (F35). The default GRCh37 path is covered by the cross-build-skip tests.
    def test_returns_correct_scores(self, dbnsfp_engine_with_data: sa.Engine):
        results = lookup_dbnsfp_by_positions(
            [("19", 44908684, "T", "C")], dbnsfp_engine_with_data, source_build="GRCh38"
        )
        key = ("19", 44908684, "T", "C")
        assert key in results
        annot = results[key]
        assert annot.cadd_phred == pytest.approx(28.3)
        assert annot.rsid == "rs429358"

    def test_empty_positions(self, dbnsfp_engine_with_data: sa.Engine):
        results = lookup_dbnsfp_by_positions([], dbnsfp_engine_with_data)
        assert len(results) == 0

    def test_nonexistent_position(self, dbnsfp_engine_with_data: sa.Engine):
        results = lookup_dbnsfp_by_positions(
            [("99", 1, "A", "T")], dbnsfp_engine_with_data, source_build="GRCh38"
        )
        assert len(results) == 0

    def test_large_position_batch_chunking(self, dbnsfp_engine_with_data: sa.Engine):
        """Ensure batching works for lists larger than internal batch size."""
        positions = [(str(i % 22 + 1), i, "A", "T") for i in range(300)]
        positions.append(("19", 44908684, "T", "C"))  # one real one
        results = lookup_dbnsfp_by_positions(
            positions, dbnsfp_engine_with_data, source_build="GRCh38"
        )
        assert ("19", 44908684, "T", "C") in results

    # ── F35: cross-build guard ────────────────────────────────────────
    _SKIP_EVENT = "dbnsfp_position_lookup_skipped_cross_build"

    def test_default_source_build_skips_cross_build(self, dbnsfp_engine_with_data: sa.Engine):
        """Default (GRCh37 pipeline) vs GRCh38 dbNSFP → skip + warn, no match."""
        with capture_logs() as cap_logs:
            results = lookup_dbnsfp_by_positions(
                [("19", 44908684, "T", "C")], dbnsfp_engine_with_data
            )
        assert results == {}
        events = [e for e in cap_logs if e.get("event") == self._SKIP_EVENT]
        assert events, "expected a cross-build skip warning"
        assert events[0]["source_build"] == "GRCh37"
        assert events[0]["dbnsfp_build"] == "GRCh38"

    def test_explicit_grch37_skips_cross_build(self, dbnsfp_engine_with_data: sa.Engine):
        """An explicit GRCh37 source build is skipped just like the default."""
        results = lookup_dbnsfp_by_positions(
            [("19", 44908684, "T", "C")], dbnsfp_engine_with_data, source_build="GRCh37"
        )
        assert results == {}

    def test_grch38_opt_in_runs_join(self, dbnsfp_engine_with_data: sa.Engine):
        """A caller with genuine GRCh38 coordinates can opt in and get a match."""
        with capture_logs() as cap_logs:
            results = lookup_dbnsfp_by_positions(
                [("19", 44908684, "T", "C")], dbnsfp_engine_with_data, source_build="GRCh38"
            )
        assert ("19", 44908684, "T", "C") in results
        assert not [e for e in cap_logs if e.get("event") == self._SKIP_EVENT]


# ── Ensemble pathogenicity tests ─────────────────────────────────────────


class TestEnsemblePathogenicity:
    """T2-12 / F24/F25: ensemble flag = strict majority of *present* independent axes.

    The four independent axes are SIFT, PolyPhen-2, CADD and META (the
    REVEL/MetaSVM/MetaLR meta-predictor family collapsed to one vote, F24). The
    flag needs a strict majority of the axes actually assessed, with ≥2 axes
    present (k-of-present, F25) — not a fixed ≥3-of-5.
    """

    def _make_annot(self, **kwargs) -> DbNSFPAnnotation:
        defaults = {
            "rsid": "rs1",
            "chrom": "1",
            "pos": 100,
            "ref": "A",
            "alt": "G",
        }
        defaults.update(kwargs)
        return DbNSFPAnnotation(**defaults)

    def test_all_deleterious(self):
        annot = self._make_annot(
            sift_score=0.001,
            polyphen2_hsvar_score=0.999,
            cadd_phred=30.0,
            revel=0.8,
            metasvm=0.5,
        )
        # SIFT, PolyPhen, CADD + the collapsed META axis = 4 independent axes.
        assert annot.deleterious_count == 4
        assert is_ensemble_pathogenic(annot)

    def test_exactly_three_deleterious(self):
        annot = self._make_annot(
            sift_score=0.001,  # D: < 0.05
            polyphen2_hsvar_score=0.999,  # D: > 0.909
            cadd_phred=25.0,  # D: >= 20
            revel=0.3,  # T: < 0.5
            metasvm=-0.5,  # T: <= 0
        )
        assert annot.deleterious_count == 3
        assert is_ensemble_pathogenic(annot)

    def test_two_deleterious_not_pathogenic(self):
        annot = self._make_annot(
            sift_score=0.001,  # D
            polyphen2_hsvar_score=0.999,  # D
            cadd_phred=15.0,  # T: < 20
            revel=0.3,  # T
            metasvm=-0.5,  # T
        )
        assert annot.deleterious_count == 2
        assert not is_ensemble_pathogenic(annot)

    def test_none_deleterious(self):
        annot = self._make_annot(
            sift_score=0.5,
            polyphen2_hsvar_score=0.1,
            cadd_phred=5.0,
            revel=0.1,
            metasvm=-1.0,
        )
        assert annot.deleterious_count == 0
        assert not is_ensemble_pathogenic(annot)

    def test_polyphen_strict_threshold(self):
        """F38: PolyPhen uses the strict 0.909 'probably damaging' cutoff.

        A 'possibly damaging' score (old lenient 0.453 < pp <= 0.909) no longer
        counts as a deleterious vote, matching the sibling
        ``evidence_conflict._is_polyphen_deleterious`` (> 0.909).
        """
        # Possibly-damaging range — does NOT count.
        assert self._make_annot(polyphen2_hsvar_score=0.7).deleterious_count == 0
        assert self._make_annot(polyphen2_hsvar_score=0.909).deleterious_count == 0
        # Probably-damaging (> 0.909) — counts.
        assert self._make_annot(polyphen2_hsvar_score=0.95).deleterious_count == 1

    def test_all_null_scores(self):
        annot = self._make_annot()
        assert annot.deleterious_count == 0
        assert not is_ensemble_pathogenic(annot)

    def test_boundary_values(self):
        """Test exact threshold boundaries."""
        # SIFT boundary: exactly 0.05 is NOT deleterious (< 0.05 required)
        annot = self._make_annot(sift_score=0.05)
        assert annot.deleterious_count == 0

        # PolyPhen boundary: exactly 0.453 is NOT deleterious (> 0.453 required)
        annot = self._make_annot(polyphen2_hsvar_score=0.453)
        assert annot.deleterious_count == 0

        # CADD boundary: exactly 20 IS deleterious (>= 20)
        annot = self._make_annot(cadd_phred=20.0)
        assert annot.deleterious_count == 1

        # REVEL boundary: exactly 0.5 IS deleterious (>= 0.5)
        annot = self._make_annot(revel=0.5)
        assert annot.deleterious_count == 1

        # MetaSVM boundary: exactly 0 is NOT deleterious (> 0 required)
        annot = self._make_annot(metasvm=0.0)
        assert annot.deleterious_count == 0

    def test_meta_predictors_collapse_to_one_axis(self):
        """F24: REVEL+MetaSVM+MetaLR count as ONE axis, not three votes.

        Three concordant meta-predictors (and nothing else) form a single
        deleterious axis — so a fixed-5 count would have read 2+ here, but the
        collapsed model reports exactly one assessed axis, which cannot flag on
        its own (k-of-present needs ≥2 axes).
        """
        annot = self._make_annot(revel=0.9, metasvm=0.8, metalr=0.9)
        assert annot.deleterious_count == 1
        assert annot.deleterious_total_assessed == 1
        assert not is_ensemble_pathogenic(annot)

    def test_meta_axis_needs_majority_not_a_single_outlier(self):
        """F24: a lone deleterious meta-predictor outvoted by its siblings is not a vote."""
        # MetaSVM says deleterious; REVEL and MetaLR say tolerated → axis = not del.
        annot = self._make_annot(revel=0.2, metasvm=0.8, metalr=0.1)
        assert annot.deleterious_count == 0
        assert annot.deleterious_total_assessed == 1

    def test_k_of_present_flags_two_of_two(self):
        """F25: 2 of 2 *present* axes deleterious flags, where fixed-3-of-5 never could.

        Only SIFT and CADD are present (no PolyPhen, no meta-predictors); both
        deleterious. Denominator is the 2 assessed axes, so the strict-majority
        rule fires — the old fixed threshold of 3 made this unreachable.
        """
        annot = self._make_annot(sift_score=0.001, cadd_phred=30.0)
        deleterious, assessed = annot.deleterious_count, annot.deleterious_total_assessed
        assert (deleterious, assessed) == (2, 2)
        assert is_ensemble_pathogenic(annot)

    def test_single_present_axis_never_flags(self):
        """F25: one assessed axis is not corroborating evidence, even if deleterious."""
        annot = self._make_annot(cadd_phred=35.0)
        assert annot.deleterious_total_assessed == 1
        assert not is_ensemble_pathogenic(annot)


# ── Version tracking tests ───────────────────────────────────────────────


class TestRecordDbnsfpVersion:
    def test_version_compare_orders_multi_digit_components(self):
        """A dbNSFP 5.10.x release must compare newer than 5.9.x."""
        assert _version_at_least("5.10.0", "5.9.0")
        assert not _version_at_least("5.9.0", "5.10.0")

    def test_inserts_version(self, reference_engine: sa.Engine):
        record_dbnsfp_version(
            reference_engine,
            version="4.5a",
            file_path="/data/dbnsfp.db",
            file_size_bytes=1_500_000_000,
            checksum="abc123",
        )
        with reference_engine.connect() as conn:
            row = conn.execute(
                sa.select(database_versions).where(database_versions.c.db_name == "dbnsfp")
            ).fetchone()
        assert row is not None
        assert row.version == "4.5a"
        assert row.file_size_bytes == 1_500_000_000
        assert row.checksum_sha256 == "abc123"


class TestDownloadAndLoadDbNSFP:
    def test_removes_source_archive_after_success(
        self,
        dbnsfp_engine: sa.Engine,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        archive = tmp_path / "dbnsfp_archive.zip"
        archive.write_bytes(b"tiny-dbnsfp-archive")

        def fake_download(
            dest_dir: Path,
            **_kwargs: object,
        ) -> Path:
            assert dest_dir == tmp_path
            return archive

        def fake_load(
            path: Path,
            engine: sa.Engine,
            *,
            progress_callback=None,
            **_kwargs: object,
        ) -> LoadStats:
            assert path == archive
            assert engine is dbnsfp_engine
            assert progress_callback is None
            return LoadStats(variants_loaded=1)

        monkeypatch.setattr("backend.annotation.dbnsfp.download_dbnsfp", fake_download)
        monkeypatch.setattr("backend.annotation.dbnsfp.load_dbnsfp_from_tsv", fake_load)

        stats = download_and_load_dbnsfp(dbnsfp_engine, tmp_path)

        assert stats.variants_loaded == 1
        assert stats.sha256 == hashlib.sha256(b"tiny-dbnsfp-archive").hexdigest()
        assert not archive.exists()

    def test_version_record_does_not_point_to_deleted_archive(
        self,
        dbnsfp_engine: sa.Engine,
        reference_engine: sa.Engine,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        archive = tmp_path / "dbnsfp_archive.zip"
        archive.write_bytes(b"tiny-dbnsfp-archive")

        monkeypatch.setattr(
            "backend.annotation.dbnsfp.download_dbnsfp",
            lambda _dest_dir, **_kwargs: archive,
        )
        monkeypatch.setattr(
            "backend.annotation.dbnsfp.load_dbnsfp_from_tsv",
            lambda *_args, **_kwargs: LoadStats(variants_loaded=1),
        )

        download_and_load_dbnsfp(dbnsfp_engine, tmp_path, reference_engine=reference_engine)

        with reference_engine.connect() as conn:
            row = conn.execute(
                sa.select(database_versions).where(database_versions.c.db_name == "dbnsfp")
            ).fetchone()
        assert row is not None
        assert row.file_path is None
        assert row.file_size_bytes == len(b"tiny-dbnsfp-archive")
        assert row.checksum_sha256 == hashlib.sha256(b"tiny-dbnsfp-archive").hexdigest()

    def test_updates_existing_version(self, reference_engine: sa.Engine):
        record_dbnsfp_version(reference_engine, version="4.4a")
        record_dbnsfp_version(reference_engine, version="4.5a")
        with reference_engine.connect() as conn:
            row = conn.execute(
                sa.select(database_versions).where(database_versions.c.db_name == "dbnsfp")
            ).fetchone()
        assert row.version == "4.5a"


class TestDownloadDbNSFPResume:
    """download_dbnsfp opts into cross-run resume with a validator sidecar (#756)."""

    def test_wires_resumable_and_validator_sidecar(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from types import SimpleNamespace

        tmp_file = tmp_path / "dbnsfp_archive.zip.tmp"
        sidecar = tmp_path / "dbnsfp_archive.zip.tmp.validator"
        # Simulate a partial + persisted validator left by a prior interrupted run.
        tmp_file.write_bytes(b"partial-from-prior-run")
        sidecar.write_text('"etag-v1"', encoding="utf-8")

        captured: dict[str, object] = {}

        def fake_stream_download(url: str, tmp_path_arg: Path, **kwargs: object):
            captured["url"] = url
            captured.update(kwargs)
            tmp_path_arg.write_bytes(b"complete-archive")  # finalize the partial
            return SimpleNamespace(total_bytes=16)

        monkeypatch.setattr("backend.annotation.dbnsfp.stream_download", fake_stream_download)

        dest = download_dbnsfp(tmp_path)

        assert dest == tmp_path / "dbnsfp_archive.zip"
        assert dest.read_bytes() == b"complete-archive"
        # Opted into resume, seeded with the sidecar's persisted validator.
        assert captured["resumable"] is True
        assert captured["validator"] == '"etag-v1"'
        assert callable(captured["on_validator"])
        # Success finalized the .tmp and cleared the sidecar.
        assert not sidecar.exists()
        # The on_validator hook persists a freshly captured validator for the next run.
        captured["on_validator"]('"etag-v2"')  # type: ignore[operator]
        assert sidecar.read_text(encoding="utf-8") == '"etag-v2"'


# ── Constants tests ──────────────────────────────────────────────────────


class TestConstants:
    def test_bitmask_value(self):
        assert DBNSFP_BITMASK == 8  # bit 3

    def test_batch_sizes(self):
        assert BATCH_SIZE == 10_000
        # LOOKUP_BATCH_SIZE is dynamically computed from SQLITE_MAX_VARIABLE_NUMBER
        # (P4-22 optimization) — at least 500, but may be higher on Linux.
        assert LOOKUP_BATCH_SIZE >= 500

    def test_fields_tuple(self):
        assert len(DBNSFP_FIELDS) == 14
        assert "cadd_phred" in DBNSFP_FIELDS
        assert "revel" in DBNSFP_FIELDS
        assert "primateai" in DBNSFP_FIELDS


# ── Data class tests ─────────────────────────────────────────────────────


class TestDbNSFPRecord:
    def test_default_none_scores(self):
        record = DbNSFPRecord(rsid="rs1", chrom="1", pos=100, ref="A", alt="G")
        assert record.cadd_phred is None
        assert record.sift_score is None


class TestLoadStats:
    def test_defaults(self):
        stats = LoadStats()
        assert stats.total_lines == 0
        assert stats.variants_loaded == 0
        assert stats.skipped_no_rsid == 0
        assert stats.sha256 is None


class TestIndexAfterLoad:
    """The load path builds indexes AFTER the bulk insert (speed + smaller lock window)."""

    def test_load_on_fresh_engine_creates_indexes_and_data(self) -> None:
        # Fresh engine with NO tables: load must create the table, insert, then
        # build the two lookup indexes — both must exist afterward and be
        # queryable. The wide all-columns covering index is intentionally NOT
        # built (it doubles the on-disk DB and stalled the setup-wizard build).
        engine = sa.create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        stats = load_dbnsfp_from_csv(DBNSFP_SEED_CSV, engine)
        assert stats.variants_loaded == 61

        with engine.connect() as conn:
            count = conn.execute(sa.text("SELECT COUNT(*) FROM dbnsfp_scores")).scalar()
            indexes = conn.execute(
                sa.text(
                    "SELECT name FROM sqlite_master"
                    " WHERE type='index' AND tbl_name='dbnsfp_scores'"
                )
            ).fetchall()
        index_names = {r[0] for r in indexes}
        assert count == 61
        assert "idx_dbnsfp_rsid" in index_names
        assert "idx_dbnsfp_chrom_pos" in index_names
        assert "idx_dbnsfp_rsid_covering" not in index_names

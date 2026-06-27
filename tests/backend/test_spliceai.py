"""SpliceAI BYO ingestion + splice-prediction context badge (SW-F2).

Validates the precomputed-VCF parser, the position-keyed ingestion guardrails
(min-DS floor, append vs clear, empty-parse guard, bad-row skip, chrom
normalization), the highest-score lookup, the tier classifier at the published
0.2 / 0.5 / 0.8 operating points, and the context-only badge (never ACMG
evidence). Also pins the registry wiring: SpliceAI is a BYO ``manual`` DB with no
auto-download build function, and ACMG never references it.
"""

from __future__ import annotations

import gzip
from pathlib import Path

import pytest
import sqlalchemy as sa

from backend.analysis.spliceai import (
    classify_spliceai_tier,
    spliceai_splice_context,
)
from backend.annotation.spliceai import (
    SPLICEAI_PMID,
    SpliceAILoadStats,
    create_spliceai_tables,
    ingest_spliceai_vcf,
    lookup_spliceai_by_variant,
    normalize_chrom,
    parse_spliceai_info,
    record_spliceai_version,
    spliceai_scores,
)
from backend.db.tables import database_versions, reference_metadata

# A synthetic SpliceAI-annotated VCF. Delta-score maxima drive the per-row tier:
#  A 7:117559590 G>A  ds_max 0.91 (acceptor loss)  → stored, high_confidence
#  B 1:1000      C>T  ds_max 0.10                   → below 0.2 floor
#  C 2:2000      A>G  ds_max 0.55                   → stored, likely
#  D 3:3000      A>G  ds_max 0.85                   → stored, high_confidence
#  E 5:5000      C>G  two genes: 0.30 + 0.70        → both stored (lookup → 0.70)
#  F 4:4000      G>C  no SpliceAI tag               → bad row
#  G chrX:500    A>T  ds_max 0.25 ('chr' prefix)    → stored (chrom normalized → X)
#  H malformed   (<8 columns)                       → bad row
_VCF = (
    "##fileformat=VCFv4.0\n"
    '##INFO=<ID=SpliceAI,Number=.,Type=String,Description="SpliceAIv1.3">\n'
    "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"
    "7\t117559590\t.\tG\tA\t.\t.\tSpliceAI=A|CFTR|0.02|0.91|0.00|0.05|-7|3|12|-21\n"
    "1\t1000\t.\tC\tT\t.\t.\tSpliceAI=T|GENE1|0.10|0.05|0.00|0.00|1|2|3|4\n"
    "2\t2000\t.\tA\tG\t.\t.\tSpliceAI=G|GENE2|0.55|0.10|0.30|0.00|1|2|3|4\n"
    "3\t3000\t.\tA\tG\t.\t.\tSpliceAI=G|GENE3|0.85|0.10|0.30|0.00|1|2|3|4\n"
    "5\t5000\t.\tC\tG\t.\t.\tSpliceAI=G|GENEA|0.30|0.00|0.00|0.00|1|2|3|4,"
    "G|GENEB|0.70|0.00|0.00|0.00|5|6|7|8\n"
    "4\t4000\t.\tG\tC\t.\t.\t.\n"
    "chrX\t500\t.\tA\tT\t.\t.\tSpliceAI=T|GENEX|0.25|0.00|0.00|0.00|1|2|3|4\n"
    "malformed_row\n"
)


def _engine(tmp_path: Path) -> sa.Engine:
    return sa.create_engine(f"sqlite:///{tmp_path}/spliceai.db")


def _write(tmp_path: Path, name: str, content: str, gz: bool = False) -> Path:
    p = tmp_path / name
    if gz:
        with gzip.open(p, "wt", encoding="utf-8") as fh:
            fh.write(content)
    else:
        p.write_text(content, encoding="utf-8")
    return p


def _count(engine: sa.Engine) -> int:
    with engine.connect() as conn:
        return conn.execute(sa.select(sa.func.count()).select_from(spliceai_scores)).scalar()


class TestNormalizeChrom:
    def test_strips_chr_prefix_and_uppercases(self) -> None:
        assert normalize_chrom("chr7") == "7"
        assert normalize_chrom("CHRx") == "X"
        assert normalize_chrom(" 17 ") == "17"
        assert normalize_chrom("MT") == "MT"


class TestParseSpliceaiInfo:
    def test_parses_fields_and_computes_max(self) -> None:
        [e] = parse_spliceai_info("SpliceAI=A|CFTR|0.02|0.91|0.00|0.05|-7|3|12|-21")
        assert e["symbol"] == "CFTR"
        assert e["ds_al"] == 0.91
        assert e["ds_max"] == 0.91  # max(0.02, 0.91, 0.00, 0.05)
        assert e["dp_al"] == 3

    def test_multiple_gene_entries(self) -> None:
        entries = parse_spliceai_info("SpliceAI=G|A|0.30|0|0|0|1|2|3|4,G|B|0.70|0|0|0|5|6|7|8")
        assert [e["symbol"] for e in entries] == ["A", "B"]
        assert {e["ds_max"] for e in entries} == {0.30, 0.70}

    def test_no_tag_or_short_entry_dropped(self) -> None:
        assert parse_spliceai_info("AC=1;AN=2") == []
        # Fewer than the 10 expected subfields → dropped.
        assert parse_spliceai_info("SpliceAI=A|GENE|0.5|0.1") == []

    def test_anchored_not_substring(self) -> None:
        # A key that merely ends in "SpliceAI" must not be picked up.
        assert parse_spliceai_info("XSpliceAI=A|GENE|0.9|0|0|0|1|2|3|4") == []

    def test_rejects_non_finite_and_out_of_range_scores(self) -> None:
        # nan/inf must not slip past parsing (nan would bypass the min-DS check);
        # a DS outside [0, 1] is invalid; an inf delta position must not raise.
        [e] = parse_spliceai_info("SpliceAI=A|G|nan|inf|0.50|1.50|inf|2|3|4")
        assert e["ds_ag"] is None  # nan rejected
        assert e["ds_al"] is None  # inf rejected
        assert e["ds_dg"] == 0.50
        assert e["ds_dl"] is None  # 1.50 out of [0, 1]
        assert e["ds_max"] == 0.50  # max over the only finite, in-range score
        assert e["dp_ag"] is None  # inf delta position → None, not OverflowError

    def test_all_scores_non_finite_drops_entry(self) -> None:
        # No parseable delta score → the entry is dropped entirely.
        assert parse_spliceai_info("SpliceAI=A|G|nan|inf|inf|nan|1|2|3|4") == []


class TestIngestion:
    def test_loads_with_default_floor(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        stats = ingest_spliceai_vcf(_write(tmp_path, "s.vcf", _VCF), engine)
        assert isinstance(stats, SpliceAILoadStats)
        # A, C, D, E(×2), G stored; B below 0.2; F + malformed are bad rows.
        assert stats.loaded == 6
        assert stats.skipped_below_threshold == 1
        assert stats.skipped_bad_row == 2
        assert _count(engine) == 6

    def test_min_ds_zero_keeps_subthreshold(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        stats = ingest_spliceai_vcf(_write(tmp_path, "s.vcf", _VCF), engine, min_ds=0.0)
        assert stats.loaded == 7  # B (0.10) now retained
        assert stats.skipped_below_threshold == 0

    def test_gzip_input(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        stats = ingest_spliceai_vcf(_write(tmp_path, "s.vcf.gz", _VCF, gz=True), engine)
        assert stats.loaded == 6

    def test_reingest_clears_by_default(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        ingest_spliceai_vcf(_write(tmp_path, "s.vcf", _VCF), engine)
        ingest_spliceai_vcf(_write(tmp_path, "s2.vcf", _VCF), engine)  # clear_existing=True
        assert _count(engine) == 6  # not doubled

    def test_append_preserves_existing(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        ingest_spliceai_vcf(_write(tmp_path, "s.vcf", _VCF), engine)
        extra = (
            "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"
            "9\t9000\t.\tA\tC\t.\t.\tSpliceAI=C|GENE9|0.95|0|0|0|1|2|3|4\n"
        )
        ingest_spliceai_vcf(_write(tmp_path, "indel.vcf", extra), engine, clear_existing=False)
        assert _count(engine) == 7  # 6 + 1 appended

    def test_empty_parse_raises(self, tmp_path: Path) -> None:
        # All rows below the floor → zero stored → refuse to clear/replace.
        only_low = (
            "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"
            "1\t1\t.\tA\tC\t.\t.\tSpliceAI=C|G|0.05|0|0|0|1|2|3|4\n"
        )
        engine = _engine(tmp_path)
        with pytest.raises(ValueError, match="zero SpliceAI rows"):
            ingest_spliceai_vcf(_write(tmp_path, "low.vcf", only_low), engine)

    def test_create_tables_idempotent(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        create_spliceai_tables(engine)
        create_spliceai_tables(engine)  # must not error
        assert _count(engine) == 0

    def test_multiallelic_matched_allele_stored(self, tmp_path: Path) -> None:
        # Multi-allelic row whose SpliceAI ALLELE matches one ALT → stored under it.
        vcf = (
            "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"
            "8\t8000\t.\tA\tC,T\t.\t.\tSpliceAI=T|GENE8|0.90|0|0|0|1|2|3|4\n"
        )
        engine = _engine(tmp_path)
        stats = ingest_spliceai_vcf(_write(tmp_path, "ma.vcf", vcf), engine)
        assert stats.loaded == 1
        row = lookup_spliceai_by_variant("8", 8000, "A", "T", engine)
        assert row is not None and row["ds_max"] == 0.90
        # And it is NOT findable under the other ALT (no wrong-allele storage).
        assert lookup_spliceai_by_variant("8", 8000, "A", "C", engine) is None

    def test_multiallelic_unmatched_allele_skipped(self, tmp_path: Path) -> None:
        # Multi-allelic row whose SpliceAI ALLELE matches NEITHER ALT must be
        # skipped (a bad row), never stored under an arbitrary ALT.
        vcf = (
            "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"
            "8\t8000\t.\tA\tC,T\t.\t.\tSpliceAI=G|GENE8|0.90|0|0|0|1|2|3|4\n"
        )
        engine = _engine(tmp_path)
        with pytest.raises(ValueError, match="zero SpliceAI rows"):
            ingest_spliceai_vcf(_write(tmp_path, "ma.vcf", vcf), engine)
        # Nothing was stored under either ALT.
        assert lookup_spliceai_by_variant("8", 8000, "A", "C", engine) is None
        assert lookup_spliceai_by_variant("8", 8000, "A", "T", engine) is None


class TestLookup:
    @pytest.fixture
    def loaded_engine(self, tmp_path: Path) -> sa.Engine:
        engine = _engine(tmp_path)
        ingest_spliceai_vcf(_write(tmp_path, "s.vcf", _VCF), engine)
        return engine

    def test_hit(self, loaded_engine: sa.Engine) -> None:
        row = lookup_spliceai_by_variant("7", 117559590, "G", "A", loaded_engine)
        assert row is not None
        assert row["symbol"] == "CFTR"
        assert row["ds_max"] == 0.91

    def test_normalizes_chrom_and_alleles(self, loaded_engine: sa.Engine) -> None:
        # 'chr7' query against a stored '7'; lowercase alleles.
        row = lookup_spliceai_by_variant("chr7", 117559590, "g", "a", loaded_engine)
        assert row is not None and row["symbol"] == "CFTR"

    def test_multi_gene_returns_highest_ds_max(self, loaded_engine: sa.Engine) -> None:
        row = lookup_spliceai_by_variant("5", 5000, "C", "G", loaded_engine)
        assert row is not None
        assert row["symbol"] == "GENEB"  # 0.70 > 0.30
        assert row["ds_max"] == 0.70

    def test_chr_prefixed_row_is_findable(self, loaded_engine: sa.Engine) -> None:
        # The "chrX" VCF row was normalized to "X" at ingest.
        row = lookup_spliceai_by_variant("X", 500, "A", "T", loaded_engine)
        assert row is not None and row["symbol"] == "GENEX"

    def test_miss_returns_none(self, loaded_engine: sa.Engine) -> None:
        assert lookup_spliceai_by_variant("22", 1, "A", "T", loaded_engine) is None

    def test_none_inputs_return_none(self, loaded_engine: sa.Engine) -> None:
        assert lookup_spliceai_by_variant(None, 1, "A", "T", loaded_engine) is None
        assert lookup_spliceai_by_variant("7", None, "A", "T", loaded_engine) is None
        assert lookup_spliceai_by_variant("7", 1, None, "T", loaded_engine) is None


class TestClassifyTier:
    @pytest.mark.parametrize(
        ("ds_max", "tier"),
        [
            (None, "unknown"),
            (0.0, "none"),
            (0.19, "none"),
            (0.2, "possible"),
            (0.49, "possible"),
            (0.5, "likely"),
            (0.79, "likely"),
            (0.8, "high_confidence"),
            (1.0, "high_confidence"),
        ],
    )
    def test_boundaries(self, ds_max: float | None, tier: str) -> None:
        assert classify_spliceai_tier(ds_max) == tier


class TestSpliceContext:
    def test_none_row(self) -> None:
        assert spliceai_splice_context(None) is None

    def test_summarizes_top_mode_and_is_not_acmg(self) -> None:
        row = {
            "symbol": "CFTR",
            "ds_ag": 0.02,
            "ds_al": 0.91,
            "ds_dg": 0.00,
            "ds_dl": 0.05,
            "dp_ag": -7,
            "dp_al": 3,
            "dp_dg": 12,
            "dp_dl": -21,
            "ds_max": 0.91,
        }
        ctx = spliceai_splice_context(row)
        assert ctx["ds_max"] == 0.91
        assert ctx["tier"] == "high_confidence"
        assert ctx["top_mode"] == "acceptor_loss"
        assert ctx["top_mode_label"] == "Acceptor loss"
        assert ctx["top_delta_position"] == 3
        assert ctx["ds_acceptor_loss"] == 0.91
        # In-silico prediction only — never an ACMG vote.
        assert ctx["acmg_evidence"] is False
        assert ctx["context_only"] is True
        assert ctx["note"]
        assert SPLICEAI_PMID in ctx["pmid_citations"]


class TestRecordVersion:
    def test_records_grch37_in_reference_db(self, tmp_path: Path) -> None:
        ref = sa.create_engine(f"sqlite:///{tmp_path}/reference.db")
        reference_metadata.create_all(ref)
        record_spliceai_version(ref, version="1.3", file_size_bytes=123)
        with ref.connect() as conn:
            row = conn.execute(
                sa.select(database_versions).where(database_versions.c.db_name == "spliceai")
            ).fetchone()
        assert row is not None
        assert row.version == "1.3"
        assert row.genome_build == "GRCh37"  # hg19 precomputed, joined by position


class TestRegistryWiring:
    """SpliceAI is BYO: a ``manual`` DB with no auto-download path."""

    def test_no_build_fn(self) -> None:
        from backend.db.database_registry import get_build_fn

        # No build_fn → setup wizard / update manager never auto-fetch it (BYO).
        assert get_build_fn("spliceai") is None

    def test_registered_as_manual_optional(self) -> None:
        from backend.db.database_registry import get_database

        db = get_database("spliceai")
        assert db is not None
        assert db.build_mode == "manual"
        assert db.required is False
        assert db.url == ""  # no redistributable URL (NC + login-gated)

    def test_expected_genome_build(self) -> None:
        from backend.db.database_registry import EXPECTED_GENOME_BUILD

        assert EXPECTED_GENOME_BUILD["spliceai"] == "GRCh37"


class TestAcmgIsolation:
    """SpliceAI is an in-silico prediction, not proof — it must never feed ACMG
    (no PVS1/PP3/PS3 uplift). Pins that the ACMG assessor has zero coupling to the
    SpliceAI layer (mirrors the GTEx firewall guard)."""

    def test_acmg_does_not_reference_spliceai(self) -> None:
        import backend.analysis.acmg as acmg_mod

        src = Path(acmg_mod.__file__).read_text(encoding="utf-8").lower()
        assert "spliceai" not in src, (
            "backend/analysis/acmg.py must not reference SpliceAI — an in-silico "
            "splice prediction may never become ACMG evidence."
        )

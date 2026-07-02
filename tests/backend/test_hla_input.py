"""Sample ``annotated_variants`` → HIBAG PLINK input-prep (Wave D glue).

Pins the reference-aligned biallelic-SNP filter (zygosity → 2-bit .bed code;
indels / no-calls / unresolved-REF dropped), the xMHC chr6 window, the exact PLINK
binary layout (magic bytes, low-order-bits-first packing, variant-major blocks),
the .bim (A1=REF / A2=ALT) and .fam text, and the end-to-end DB → .bed/.bim/.fam
writer (byte-for-byte).
"""

from __future__ import annotations

from pathlib import Path

import pytest
import sqlalchemy as sa

from backend.analysis.hla_input import (
    XMHC_GRCH37,
    HibagInputResult,
    MHCRegion,
    PlinkSnp,
    bed_code,
    build_bed_bytes,
    build_bim_text,
    build_fam_text,
    collect_plink_snps,
    pack_bed_snp_block,
    write_hibag_plink_input,
)
from backend.db.sample_schema import create_sample_tables
from backend.db.tables import annotated_variants

_BED_MAGIC = bytes((0x6C, 0x1B, 0x01))


@pytest.fixture
def sample_engine() -> sa.Engine:
    engine = sa.create_engine("sqlite://")
    create_sample_tables(engine)
    return engine


def _insert(engine: sa.Engine, rows: list[dict]) -> None:
    with engine.begin() as conn:
        conn.execute(annotated_variants.insert(), rows)


def _row(rsid, chrom, pos, ref, alt, zyg, gt="") -> dict:
    return {
        "rsid": rsid,
        "chrom": chrom,
        "pos": pos,
        "ref": ref,
        "alt": alt,
        "zygosity": zyg,
        "genotype": gt,
    }


class TestBedCode:
    @pytest.mark.parametrize(
        ("zyg", "expected"),
        [("hom_ref", 0b00), ("het", 0b10), ("hom_alt", 0b11)],
    )
    def test_reference_aligned_snp(self, zyg, expected) -> None:
        # A1=REF / A2=ALT: hom_ref = homozygous A1 (00), hom_alt = homozygous A2 (11).
        assert bed_code("A", "G", zyg) == expected

    def test_lowercase_alleles_accepted(self) -> None:
        assert bed_code("a", "g", "het") == 0b10

    @pytest.mark.parametrize("zyg", [None, "", "no_call", "unknown"])
    def test_unresolved_zygosity_dropped(self, zyg) -> None:
        assert bed_code("A", "G", zyg) is None

    def test_unresolved_reference_n_dropped(self) -> None:
        # The vcf_export "honest fallback" REF=N can't align to a HIBAG SNP model.
        assert bed_code("N", "G", "het") is None

    @pytest.mark.parametrize(
        ("ref", "alt"),
        [("AT", "G"), ("A", "ATG"), ("A", "G,T"), ("-", "G"), ("A", ""), ("A", None)],
    )
    def test_non_snp_dropped(self, ref, alt) -> None:
        assert bed_code(ref, alt, "het") is None

    def test_ref_equals_alt_dropped(self) -> None:
        assert bed_code("A", "A", "hom_alt") is None


class TestPackBedSnpBlock:
    @pytest.mark.parametrize(
        ("codes", "expected"),
        [
            ([0b00], b"\x00"),
            ([0b10], b"\x02"),
            ([0b11], b"\x03"),
            # Four samples pack low-order first: 0b01_11_10_00 = 0x78.
            ([0b00, 0b10, 0b11, 0b01], b"\x78"),
        ],
    )
    def test_single_byte_packing(self, codes, expected) -> None:
        assert pack_bed_snp_block(codes) == expected

    def test_fifth_sample_spills_to_a_second_byte(self) -> None:
        # 5 samples -> ceil(5/4) = 2 bytes; the 5th occupies the low bits of byte 2.
        block = pack_bed_snp_block([0b00, 0b00, 0b00, 0b00, 0b11])
        assert block == b"\x00\x03"


class TestBuildBedBytes:
    def test_magic_prefix_then_one_block_per_snp(self) -> None:
        snps = [
            PlinkSnp(pos=100, snp_id="rsA", ref="A", alt="G", code=0b10),
            PlinkSnp(pos=200, snp_id="rsB", ref="C", alt="T", code=0b11),
        ]
        assert build_bed_bytes(snps) == _BED_MAGIC + b"\x02\x03"

    def test_empty_snps_is_magic_only(self) -> None:
        assert build_bed_bytes([]) == _BED_MAGIC


class TestBuildBimText:
    def test_columns_a1_ref_a2_alt(self) -> None:
        snps = [
            PlinkSnp(pos=100, snp_id="rsA", ref="A", alt="G", code=0b00),
            PlinkSnp(pos=200, snp_id="rsB", ref="C", alt="T", code=0b10),
        ]
        text = build_bim_text(snps, chrom="6")
        assert text.splitlines() == [
            "6\trsA\t0\t100\tA\tG",
            "6\trsB\t0\t200\tC\tT",
        ]

    def test_empty_is_empty_string(self) -> None:
        assert build_bim_text([]) == ""


class TestBuildFamText:
    def test_single_sample_line(self) -> None:
        # FID=IID=name, no parents, sex unknown (0), phenotype missing (-9).
        assert build_fam_text("S1") == "S1\tS1\t0\t0\t0\t-9\n"

    def test_whitespace_and_control_chars_collapsed(self) -> None:
        assert build_fam_text("a b\tc\n") == "a_b_c_\ta_b_c_\t0\t0\t0\t-9\n"

    def test_empty_name_falls_back(self) -> None:
        assert build_fam_text("") == "SAMPLE\tSAMPLE\t0\t0\t0\t-9\n"


class TestMHCRegion:
    def test_default_window_brackets_classical_loci(self) -> None:
        # HLA-A (~29.91 Mb) and HLA-DPB1 (~33.05 Mb) sit inside the default window.
        assert XMHC_GRCH37.contains("6", 29_910_247)
        assert XMHC_GRCH37.contains("6", 33_054_976)

    @pytest.mark.parametrize("pos", [24_999_999, 34_000_001])
    def test_outside_window_excluded(self, pos) -> None:
        assert not XMHC_GRCH37.contains("6", pos)

    def test_other_chromosome_excluded(self) -> None:
        assert not XMHC_GRCH37.contains("1", 30_000_000)

    def test_chrom_token_normalized_on_both_sides(self) -> None:
        # A configured "chr6" window must still match bare "6" tokens (and vice versa),
        # otherwise a CLI-supplied region could silently drop every chr6 SNP.
        assert MHCRegion(chrom="chr6").contains("6", 31_000_000)
        assert MHCRegion(chrom="6").contains("chr6", 31_000_000)


class TestCollectPlinkSnps:
    def test_region_filter_and_sort(self) -> None:
        rows = [
            ("rs_a", "6", 31_000_000, "A", "G", "hom_ref"),  # emit, code 00
            ("rs_b", "chr6", 30_000_000, "C", "T", "het"),  # emit (chr prefix), earlier pos
            ("rs_c", "6", 32_000_000, "G", "A", "hom_alt"),  # emit, code 11
            ("rs_indel", "6", 31_500_000, "AT", "A", "het"),  # drop: indel
            ("rs_off_chrom", "1", 31_000_000, "A", "G", "het"),  # drop: not chr6
            ("rs_below", "6", 24_000_000, "A", "G", "het"),  # drop: below window
            ("rs_nocall", "6", 33_000_000, "A", "G", None),  # drop: no-call
        ]
        snps, n_total, n_emitted = collect_plink_snps(rows)
        assert n_total == 7
        assert n_emitted == 3
        # Sorted by position; rs_b (30M) precedes rs_a (31M) precedes rs_c (32M).
        assert [(s.snp_id, s.pos, s.code) for s in snps] == [
            ("rs_b", 30_000_000, 0b10),
            ("rs_a", 31_000_000, 0b00),
            ("rs_c", 32_000_000, 0b11),
        ]

    def test_missing_rsid_gets_synthetic_position_id(self) -> None:
        rows = [
            ("", "6", 31_000_000, "A", "G", "het"),
            (None, "6", 31_500_000, "C", "T", "hom_alt"),
        ]
        snps, _n_total, n_emitted = collect_plink_snps(rows)
        assert n_emitted == 2
        assert [s.snp_id for s in snps] == ["6:31000000", "6:31500000"]

    def test_custom_region_narrows_scope(self) -> None:
        rows = [
            ("rs_in", "6", 31_000_000, "A", "G", "het"),
            ("rs_out", "6", 31_900_000, "A", "G", "het"),
        ]
        region = MHCRegion(chrom="6", start=30_500_000, end=31_500_000)
        snps, n_total, n_emitted = collect_plink_snps(rows, region)
        assert n_total == 2
        assert n_emitted == 1
        assert [s.snp_id for s in snps] == ["rs_in"]


class TestWriteHibagPlinkInput:
    def test_end_to_end_byte_exact(self, sample_engine: sa.Engine, tmp_path: Path) -> None:
        _insert(
            sample_engine,
            [
                _row("rs_a", "6", 31_000_000, "A", "G", "hom_ref"),
                _row("rs_b", "6", 30_000_000, "C", "T", "het"),
                _row("rs_c", "6", 32_000_000, "G", "A", "hom_alt"),
                _row("rs_indel", "6", 31_500_000, "AT", "A", "het"),  # drop
                _row("rs_off", "1", 31_000_000, "A", "G", "het"),  # drop
                _row("rs_below", "6", 24_000_000, "A", "G", "het"),  # drop
                _row("rs_nocall", "6", 33_000_000, "A", "G", None),  # drop
            ],
        )
        result = write_hibag_plink_input(
            sample_engine, tmp_path / "hla" / "sample", sample_name="S1"
        )

        assert isinstance(result, HibagInputResult)
        assert result.n_total == 7
        assert result.n_emitted == 3
        assert result.n_dropped == 4
        assert result.plink_prefix == tmp_path / "hla" / "sample"

        bed = (tmp_path / "hla" / "sample.bed").read_bytes()
        # magic, then one byte/SNP sorted by pos:
        # rs_b(het=0x02), rs_a(hom_ref=0x00), rs_c(hom_alt=0x03).
        assert bed == _BED_MAGIC + b"\x02\x00\x03"

        bim = (tmp_path / "hla" / "sample.bim").read_text(encoding="utf-8")
        assert bim.splitlines() == [
            "6\trs_b\t0\t30000000\tC\tT",
            "6\trs_a\t0\t31000000\tA\tG",
            "6\trs_c\t0\t32000000\tG\tA",
        ]

        fam = (tmp_path / "hla" / "sample.fam").read_text(encoding="utf-8")
        assert fam == "S1\tS1\t0\t0\t0\t-9\n"

    def test_dotted_prefix_appends_extensions(
        self, sample_engine: sa.Engine, tmp_path: Path
    ) -> None:
        # A prefix with a dot (e.g. "sample.hla") must APPEND ".bed" — not have its
        # ".hla" replaced — so it matches what HibagRunner.predict / the R script
        # (paste0(prefix, ".bed")) look for.
        _insert(sample_engine, [_row("rs_a", "6", 31_000_000, "A", "G", "het")])
        prefix = tmp_path / "sample.hla"
        result = write_hibag_plink_input(sample_engine, prefix)
        assert result.plink_prefix == prefix
        assert result.bed_path == tmp_path / "sample.hla.bed"
        assert (tmp_path / "sample.hla.bed").exists()
        assert (tmp_path / "sample.hla.bim").exists()
        assert (tmp_path / "sample.hla.fam").exists()
        assert not (tmp_path / "sample.bed").exists()

    def test_empty_db_writes_nothing(self, sample_engine: sa.Engine, tmp_path: Path) -> None:
        result = write_hibag_plink_input(sample_engine, tmp_path / "hla" / "sample")
        assert result.n_total == 0
        assert result.n_emitted == 0
        assert result.plink_prefix is None
        assert result.bed_path is None
        assert not (tmp_path / "hla").exists()

    def test_no_in_region_snps_writes_nothing(
        self, sample_engine: sa.Engine, tmp_path: Path
    ) -> None:
        # Real variants, but none inside the xMHC window -> graceful no-op.
        _insert(sample_engine, [_row("rs1", "1", 100, "A", "G", "het")])
        result = write_hibag_plink_input(sample_engine, tmp_path / "hla" / "sample")
        assert result.n_total == 1
        assert result.n_emitted == 0
        assert result.plink_prefix is None
        assert not (tmp_path / "hla" / "sample.bed").exists()

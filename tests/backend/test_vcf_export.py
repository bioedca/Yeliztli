"""Tests for VCF 4.2 export (P1-09).

Test IDs covered:
  T1-09 — VCF export produces valid VCF 4.2
  T1-10 — VCF contains correct ##reference=GRCh37 and ##source=Yeliztli
"""

from __future__ import annotations

import re
from datetime import date
from io import StringIO
from pathlib import Path

from backend.ingestion.vcf_export import (
    _resolve_vcf_fields,
    export_vcf_from_rows,
)

# ═══════════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════════

FIXED_DATE = date(2026, 3, 10)

SAMPLE_ROWS: list[tuple[str, str, int, str]] = [
    ("rs1000001", "1", 100000, "AA"),
    ("rs1000002", "1", 200000, "AG"),
    ("rs1000003", "2", 50000, "CC"),
    ("rs1000004", "X", 10000, "AT"),
    ("rs1000005", "Y", 5000, "T"),
    ("rs1000006", "MT", 10740, "G"),
    ("rs1000007", "1", 150000, "--"),
]


# ═══════════════════════════════════════════════════════════════════════
# T1-10: Header validation
# ═══════════════════════════════════════════════════════════════════════


class TestVCFHeaders:
    """T1-10: VCF contains correct meta-information headers."""

    def test_fileformat_header(self) -> None:
        vcf = export_vcf_from_rows([], file_date=FIXED_DATE)
        assert vcf.startswith("##fileformat=VCFv4.2\n")

    def test_reference_header(self) -> None:
        vcf = export_vcf_from_rows([], file_date=FIXED_DATE)
        assert "##reference=GRCh37\n" in vcf

    def test_source_header(self) -> None:
        vcf = export_vcf_from_rows([], file_date=FIXED_DATE)
        assert "##source=Yeliztli\n" in vcf

    def test_filedate_header(self) -> None:
        vcf = export_vcf_from_rows([], file_date=FIXED_DATE)
        assert "##fileDate=20260310\n" in vcf

    def test_format_gt_header(self) -> None:
        vcf = export_vcf_from_rows([], file_date=FIXED_DATE)
        assert '##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">' in vcf

    def test_filter_pass_header(self) -> None:
        vcf = export_vcf_from_rows([], file_date=FIXED_DATE)
        assert '##FILTER=<ID=PASS,Description="All filters passed">' in vcf

    def test_no_invalid_info_header(self) -> None:
        """VCF should not contain an INFO header with ID='.'."""
        vcf = export_vcf_from_rows([], file_date=FIXED_DATE)
        assert "##INFO=<ID=." not in vcf

    def test_ref_alt_limitation_note(self) -> None:
        vcf = export_vcf_from_rows([], file_date=FIXED_DATE)
        assert "##Yeliztli_note=" in vcf
        # The note documents reference-alignment and the honest REF=N fallback.
        assert "reference-aligned" in vcf
        assert "REF=N" in vcf

    def test_contig_lines_present(self) -> None:
        vcf = export_vcf_from_rows([], file_date=FIXED_DATE)
        for chrom in [str(i) for i in range(1, 23)] + ["X", "Y", "MT"]:
            assert f"##contig=<ID={chrom}>" in vcf

    def test_column_header_line(self) -> None:
        vcf = export_vcf_from_rows([], sample_name="TestSample", file_date=FIXED_DATE)
        expected = "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tTestSample"
        assert expected in vcf

    def test_sample_name_in_header(self) -> None:
        vcf = export_vcf_from_rows([], sample_name="MySample", file_date=FIXED_DATE)
        lines = vcf.strip().split("\n")
        header_line = [ln for ln in lines if ln.startswith("#CHROM")][0]
        assert header_line.endswith("MySample")

    def test_sample_name_tab_sanitized(self) -> None:
        """Tabs in sample name would corrupt the VCF header."""
        vcf = export_vcf_from_rows([], sample_name="Bad\tName", file_date=FIXED_DATE)
        header_line = [ln for ln in vcf.split("\n") if ln.startswith("#CHROM")][0]
        # Tab stripped, so the name should be "BadName"
        assert header_line.endswith("BadName")

    def test_sample_name_newline_sanitized(self) -> None:
        vcf = export_vcf_from_rows([], sample_name="Bad\nName", file_date=FIXED_DATE)
        header_line = [ln for ln in vcf.split("\n") if ln.startswith("#CHROM")][0]
        assert header_line.endswith("BadName")

    def test_empty_sample_name_defaults(self) -> None:
        """Empty sample name (after sanitization) falls back to SAMPLE."""
        vcf = export_vcf_from_rows([], sample_name="\t\n", file_date=FIXED_DATE)
        header_line = [ln for ln in vcf.split("\n") if ln.startswith("#CHROM")][0]
        assert header_line.endswith("SAMPLE")


# ═══════════════════════════════════════════════════════════════════════
# T1-09: Valid VCF 4.2 output
# ═══════════════════════════════════════════════════════════════════════


class TestVCFDataLines:
    """T1-09: VCF export produces valid VCF 4.2 data lines."""

    def test_homozygous_call_without_reference_uses_honest_fallback(self) -> None:
        """With no annotation-resolved ref/alt, a homozygous observed call must
        NOT claim reference-genome 0/0; emit REF=N, observed base as ALT, GT=1/1."""
        rows = [("rs100", "1", 1000, "AA")]
        vcf = export_vcf_from_rows(rows, file_date=FIXED_DATE)
        data = _get_data_lines(vcf)
        assert len(data) == 1
        fields = data[0].split("\t")
        assert fields[0] == "1"  # CHROM
        assert fields[1] == "1000"  # POS
        assert fields[2] == "rs100"  # ID
        assert fields[3] == "N"  # REF unknown — never a fabricated reference base
        assert fields[4] == "A"  # ALT = observed base
        assert fields[5] == "."  # QUAL
        assert fields[6] == "PASS"  # FILTER
        assert fields[7] == "."  # INFO
        assert fields[8] == "GT"  # FORMAT
        assert fields[9] == "1/1"  # homozygous observed vs unknown reference

    def test_heterozygous_call_without_reference_uses_honest_fallback(self) -> None:
        rows = [("rs200", "2", 2000, "AG")]
        vcf = export_vcf_from_rows(rows, file_date=FIXED_DATE)
        data = _get_data_lines(vcf)
        fields = data[0].split("\t")
        assert fields[3] == "N"  # REF unknown
        assert fields[4] == "A,G"  # both observed bases as distinct ALTs
        assert fields[9] == "1/2"  # heterozygous observed, neither asserted as REF

    def test_haploid_call_without_reference_uses_honest_fallback(self) -> None:
        """Y/MT chromosomes may have single-character genotypes."""
        rows = [("rs300", "Y", 5000, "T")]
        vcf = export_vcf_from_rows(rows, file_date=FIXED_DATE)
        data = _get_data_lines(vcf)
        fields = data[0].split("\t")
        assert fields[3] == "N"  # REF unknown
        assert fields[4] == "T"  # ALT = observed base
        assert fields[9] == "1"  # haploid GT

    def test_nocalls_skipped_by_default(self) -> None:
        rows = [
            ("rs100", "1", 1000, "AA"),
            ("rs101", "1", 2000, "--"),
        ]
        vcf = export_vcf_from_rows(rows, file_date=FIXED_DATE)
        data = _get_data_lines(vcf)
        assert len(data) == 1
        assert "rs101" not in vcf.split("\n")[-2]  # no-call absent

    def test_nocalls_included_when_requested(self) -> None:
        rows = [
            ("rs100", "1", 1000, "AA"),
            ("rs101", "1", 2000, "--"),
        ]
        vcf = export_vcf_from_rows(rows, skip_nocalls=False, file_date=FIXED_DATE)
        data = _get_data_lines(vcf)
        assert len(data) == 2
        nocall_fields = data[1].split("\t")
        assert nocall_fields[3] == "N"  # REF = N for no-call
        assert nocall_fields[9] == "./."  # missing GT

    def test_rows_sorted_by_chrom_pos(self) -> None:
        rows = [
            ("rs300", "2", 500, "CC"),
            ("rs100", "1", 2000, "AA"),
            ("rs200", "1", 1000, "GG"),
        ]
        vcf = export_vcf_from_rows(rows, file_date=FIXED_DATE)
        data = _get_data_lines(vcf)
        assert len(data) == 3
        # chr1:1000, chr1:2000, chr2:500
        assert data[0].split("\t")[1] == "1000"
        assert data[1].split("\t")[1] == "2000"
        assert data[2].split("\t")[0] == "2"

    def test_all_data_lines_have_10_columns(self) -> None:
        vcf = export_vcf_from_rows(SAMPLE_ROWS, file_date=FIXED_DATE)
        data = _get_data_lines(vcf)
        for line in data:
            assert len(line.split("\t")) == 10

    def test_vcf_header_regex_validates(self) -> None:
        """VCF 4.2 header must match ##fileformat=VCFv4.2."""
        vcf = export_vcf_from_rows(SAMPLE_ROWS, file_date=FIXED_DATE)
        assert re.match(r"^##fileformat=VCFv4\.2\n", vcf)

    def test_complete_export_with_sample_rows(self) -> None:
        """Full export of SAMPLE_ROWS produces expected line count."""
        vcf = export_vcf_from_rows(SAMPLE_ROWS, file_date=FIXED_DATE)
        data = _get_data_lines(vcf)
        # 7 rows, 1 is a no-call (skipped by default) → 6 data lines
        assert len(data) == 6


# ═══════════════════════════════════════════════════════════════════════
# #560: reference-aligned REF/ALT/GT when annotation resolved the locus
# ═══════════════════════════════════════════════════════════════════════


class TestReferenceAlignedFields:
    """#560 (same class as #471): VCF GT is allele-indexed against REF/ALT, so a
    SNP-array call must be placed against the reference. When the export route
    supplies the annotation-resolved ref/alt + zygosity, the record is emitted
    reference-aligned — a true homozygous-alternate call must be GT=1/1, never the
    false 0/0 the old genotype-inference produced.
    """

    def test_hom_alt_is_not_written_as_hom_ref(self) -> None:
        """The core bug: a true hom-alt call must be 1/1, not 0/0."""
        assert _resolve_vcf_fields("TT", "C", "T", "hom_alt") == ("C", "T", "1/1")

    def test_hom_ref_is_zero_zero(self) -> None:
        assert _resolve_vcf_fields("CC", "C", "T", "hom_ref") == ("C", "T", "0/0")

    def test_het_uses_reference_ref_alt_not_string_order(self) -> None:
        """REF/ALT follow the annotation (C/T), not the observed allele-string
        order, and GT is 0/1."""
        assert _resolve_vcf_fields("TC", "C", "T", "het") == ("C", "T", "0/1")

    def test_haploid_hom_alt_is_one(self) -> None:
        assert _resolve_vcf_fields("T", "C", "T", "hom_alt") == ("C", "T", "1")

    def test_end_to_end_hom_alt_row_reference_aligned(self) -> None:
        """A 7-element row (rsid, chrom, pos, gt, ref, alt, zygosity) is emitted
        reference-aligned through export_vcf_from_rows."""
        rows = [("rs1", "1", 100, "TT", "C", "T", "hom_alt")]
        vcf = export_vcf_from_rows(rows, file_date=FIXED_DATE)
        fields = _get_data_lines(vcf)[0].split("\t")
        assert (fields[3], fields[4], fields[9]) == ("C", "T", "1/1")

    def test_unresolved_zygosity_falls_back_to_ref_n(self) -> None:
        """ref/alt present but zygosity unresolved → honest REF=N fallback, not 0/0."""
        assert _resolve_vcf_fields("CC", "C", "T", None) == ("N", "C", "1/1")


# ═══════════════════════════════════════════════════════════════════════
# Genotype conversion unit tests (honest REF=N fallback, no reference)
# ═══════════════════════════════════════════════════════════════════════


class TestGenotypeConversion:
    def test_nocall_returns_none(self) -> None:
        assert _resolve_vcf_fields("--") is None

    def test_empty_returns_none(self) -> None:
        assert _resolve_vcf_fields("") is None

    def test_homozygous_fallback(self) -> None:
        assert _resolve_vcf_fields("CC") == ("N", "C", "1/1")

    def test_heterozygous_fallback(self) -> None:
        assert _resolve_vcf_fields("CT") == ("N", "C,T", "1/2")

    def test_haploid_fallback(self) -> None:
        assert _resolve_vcf_fields("A") == ("N", "A", "1")

    def test_indel_di_returns_none(self) -> None:
        """23andMe v3 D/I indel codes are not valid nucleotides."""
        assert _resolve_vcf_fields("DI") is None

    def test_indel_dd_returns_none(self) -> None:
        assert _resolve_vcf_fields("DD") is None

    def test_indel_ii_returns_none(self) -> None:
        assert _resolve_vcf_fields("II") is None

    def test_single_d_returns_none(self) -> None:
        assert _resolve_vcf_fields("D") is None

    def test_indel_rows_skipped_in_export(self) -> None:
        """D/I genotype rows should be skipped like no-calls."""
        rows = [
            ("rs100", "1", 1000, "AA"),
            ("rs101", "1", 2000, "DI"),
            ("rs102", "1", 3000, "DD"),
        ]
        vcf = export_vcf_from_rows(rows, file_date=FIXED_DATE)
        data = _get_data_lines(vcf)
        assert len(data) == 1
        assert data[0].split("\t")[2] == "rs100"


# ═══════════════════════════════════════════════════════════════════════
# File / stream output tests
# ═══════════════════════════════════════════════════════════════════════


class TestOutputDestinations:
    def test_write_to_file(self, tmp_path: Path) -> None:
        dest = tmp_path / "output.vcf"
        content = export_vcf_from_rows(
            SAMPLE_ROWS,
            dest=dest,
            file_date=FIXED_DATE,
        )
        assert dest.exists()
        assert dest.read_text(encoding="utf-8") == content

    def test_write_to_stream(self) -> None:
        stream = StringIO()
        content = export_vcf_from_rows(
            SAMPLE_ROWS,
            dest=stream,
            file_date=FIXED_DATE,
        )
        assert stream.getvalue() == content

    def test_return_string_when_no_dest(self) -> None:
        content = export_vcf_from_rows(SAMPLE_ROWS, file_date=FIXED_DATE)
        assert isinstance(content, str)
        assert content.startswith("##fileformat=VCFv4.2")


# ═══════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════


def _get_data_lines(vcf: str) -> list[str]:
    """Extract non-header, non-empty lines from a VCF string."""
    return [line for line in vcf.strip().split("\n") if line and not line.startswith("#")]

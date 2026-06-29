"""Tests for vcfanno integration (P4-12).

Covers:
  - BED overlay parsing (with annotation columns)
  - VCF overlay parsing (INFO field extraction)
  - Format auto-detection
  - Overlay application to sample variants (BED range + VCF exact match)
  - Overlay config CRUD operations
  - Edge cases (empty files, invalid data, deduplication)
"""

from __future__ import annotations

import pytest
import sqlalchemy as sa

from backend.annotation.vcfanno_runner import (
    apply_overlay,
    delete_overlay,
    detect_and_parse_overlay,
    get_overlay,
    list_overlays,
    parse_bed_overlay,
    parse_vcf_overlay,
    save_overlay_config,
)

# ═══════════════════════════════════════════════════════════════════════
# BED overlay parsing
# ═══════════════════════════════════════════════════════════════════════


class TestParseBedOverlay:
    """Tests for BED overlay parser."""

    def test_basic_bed_with_annotations(self) -> None:
        """Parses BED file with extra annotation columns."""
        content = (
            "#chrom\tstart\tend\tname\tscore\n"
            "chr1\t100\t200\tGENE1\t0.95\n"
            "chr2\t300\t400\tGENE2\t0.87\n"
        )
        result = parse_bed_overlay(content)
        assert result.file_type == "bed"
        assert result.record_count == 2
        assert "name" in result.column_names
        assert "score" in result.column_names
        assert result.records[0].chrom == "1"
        assert result.records[0].start == 100
        assert result.records[0].end == 200
        assert result.records[0].annotations["name"] == "GENE1"
        assert result.records[0].annotations["score"] == 0.95

    def test_bed_without_header(self) -> None:
        """Extra columns get auto-named when no header present."""
        content = "chr1\t100\t200\tGENE1\t0.95\n"
        result = parse_bed_overlay(content)
        assert "bed_col_4" in result.column_names
        assert "bed_col_5" in result.column_names
        assert result.records[0].annotations["bed_col_4"] == "GENE1"

    def test_bed_chrom_normalisation(self) -> None:
        """Chromosomes are normalised (chr prefix stripped, M -> MT)."""
        content = "chrM\t100\t200\n1\t300\t400\n"
        result = parse_bed_overlay(content)
        assert result.records[0].chrom == "MT"
        assert result.records[1].chrom == "1"

    def test_bed_skips_comments_and_track(self) -> None:
        """Comment and track lines are skipped."""
        content = "# comment\ntrack name=test\nbrowser position\nchr1\t100\t200\n"
        result = parse_bed_overlay(content)
        assert result.record_count == 1

    def test_bed_invalid_coords_warned(self) -> None:
        """Invalid coordinates generate warnings."""
        content = "chr1\t200\t100\tGENE1\nchr2\t300\t400\tGENE2\n"
        result = parse_bed_overlay(content)
        assert result.record_count == 1
        assert len(result.warnings) == 1

    def test_bed_empty_raises(self) -> None:
        """Empty BED file raises ValueError."""
        with pytest.raises(ValueError, match="No valid BED records"):
            parse_bed_overlay("")

    def test_bed_numeric_conversion(self) -> None:
        """Numeric values are auto-converted."""
        content = "chr1\t100\t200\t42\t3.14\ttext\n"
        result = parse_bed_overlay(content)
        annot = result.records[0].annotations
        assert annot["bed_col_4"] == 42
        assert annot["bed_col_5"] == 3.14
        assert annot["bed_col_6"] == "text"


# ═══════════════════════════════════════════════════════════════════════
# VCF overlay parsing
# ═══════════════════════════════════════════════════════════════════════


class TestParseVcfOverlay:
    """Tests for VCF overlay parser."""

    def test_basic_vcf(self) -> None:
        """Parses VCF with INFO field annotations."""
        content = (
            "##fileformat=VCFv4.2\n"
            '##INFO=<ID=AF,Number=A,Type=Float,Description="Allele freq">\n'
            '##INFO=<ID=CLNSIG,Number=.,Type=String,Description="ClinVar sig">\n'
            "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"
            "1\t100000\trs12345\tA\tG\t.\tPASS\tAF=0.05;CLNSIG=pathogenic\n"
            "19\t44908684\trs429358\tT\tC\t.\tPASS\tAF=0.15;CLNSIG=risk_factor\n"
        )
        result = parse_vcf_overlay(content)
        assert result.file_type == "vcf"
        assert result.record_count == 2
        assert "AF" in result.column_names
        assert "CLNSIG" in result.column_names
        assert result.records[0].chrom == "1"
        assert result.records[0].start == 100000
        assert result.records[0].annotations["AF"] == 0.05
        assert result.records[0].annotations["CLNSIG"] == "pathogenic"

    def test_vcf_multialt_info_is_projected_per_alt(self) -> None:
        """Multi-ALT records are split with allele-counted INFO projected by index."""
        content = (
            "##fileformat=VCFv4.2\n"
            '##INFO=<ID=AF,Number=A,Type=Float,Description="Allele frequency">\n'
            '##INFO=<ID=AD,Number=R,Type=Integer,Description="Allele depths">\n'
            '##INFO=<ID=DP,Number=1,Type=Integer,Description="Read depth">\n'
            "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"
            "1\t200000\t.\tC\tG,T\t.\tPASS\tAF=0.1,0.2;AD=7,3,9;DP=19;DB\n"
        )

        result = parse_vcf_overlay(content)

        assert result.record_count == 2
        assert [record.alt for record in result.records] == ["G", "T"]
        assert result.records[0].annotations == {"AF": 0.1, "AD": 3, "DP": 19, "DB": True}
        assert result.records[1].annotations == {"AF": 0.2, "AD": 9, "DP": 19, "DB": True}

    def test_vcf_flag_fields(self) -> None:
        """VCF flag fields (no value) are parsed as True."""
        content = (
            "##fileformat=VCFv4.2\n"
            "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"
            "1\t100\trs1\tA\tG\t.\tPASS\tDB;AF=0.1\n"
        )
        result = parse_vcf_overlay(content)
        assert result.records[0].annotations["DB"] is True
        assert result.records[0].annotations["AF"] == 0.1

    def test_vcf_empty_info(self) -> None:
        """VCF records with '.' INFO field produce empty annotations."""
        content = (
            "##fileformat=VCFv4.2\n"
            "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"
            "1\t100\trs1\tA\tG\t.\tPASS\t.\n"
        )
        result = parse_vcf_overlay(content)
        assert result.records[0].annotations == {}

    def test_vcf_empty_raises(self) -> None:
        """Empty VCF raises ValueError."""
        with pytest.raises(ValueError, match="No valid VCF records"):
            parse_vcf_overlay("")

    def test_vcf_chrom_normalisation(self) -> None:
        """VCF chromosomes are normalised."""
        content = (
            "##fileformat=VCFv4.2\n"
            "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"
            "chr19\t100\trs1\tA\tG\t.\tPASS\tAF=0.1\n"
        )
        result = parse_vcf_overlay(content)
        assert result.records[0].chrom == "19"


# ═══════════════════════════════════════════════════════════════════════
# Format auto-detection
# ═══════════════════════════════════════════════════════════════════════


class TestDetectAndParseOverlay:
    """Tests for overlay format auto-detection."""

    def test_vcf_by_extension(self) -> None:
        content = (
            "##fileformat=VCFv4.2\n"
            "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"
            "1\t100\trs1\tA\tG\t.\tPASS\tAF=0.1\n"
        )
        result = detect_and_parse_overlay(content, "overlay.vcf")
        assert result.file_type == "vcf"

    def test_bed_by_extension(self) -> None:
        content = "chr1\t100\t200\tGENE1\n"
        result = detect_and_parse_overlay(content, "overlay.bed")
        assert result.file_type == "bed"

    def test_vcf_by_content(self) -> None:
        content = (
            "##fileformat=VCFv4.2\n"
            "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"
            "1\t100\trs1\tA\tG\t.\tPASS\tAF=0.1\n"
        )
        result = detect_and_parse_overlay(content, "data.txt")
        assert result.file_type == "vcf"

    def test_bed_fallback(self) -> None:
        content = "chr1\t100\t200\tGENE1\n"
        result = detect_and_parse_overlay(content, "data.txt")
        assert result.file_type == "bed"


# ═══════════════════════════════════════════════════════════════════════
# Overlay config CRUD
# ═══════════════════════════════════════════════════════════════════════


class TestOverlayConfigCRUD:
    """Tests for overlay config database operations."""

    def test_save_and_get(self, reference_engine: sa.Engine) -> None:
        content = "chr1\t100\t200\tGENE1\t0.95\n"
        parsed = parse_bed_overlay(content)
        overlay_id = save_overlay_config("Test", "A test overlay", parsed, reference_engine)

        config = get_overlay(overlay_id, reference_engine)
        assert config is not None
        assert config.name == "Test"
        assert config.description == "A test overlay"
        assert config.file_type == "bed"
        assert config.region_count == 1

    def test_list_overlays(self, reference_engine: sa.Engine) -> None:
        for i in range(3):
            content = f"chr1\t{i * 100}\t{i * 100 + 100}\tGENE{i}\n"
            parsed = parse_bed_overlay(content)
            save_overlay_config(f"Overlay {i}", "", parsed, reference_engine)

        configs = list_overlays(reference_engine)
        assert len(configs) == 3

    def test_delete_overlay(self, reference_engine: sa.Engine) -> None:
        content = "chr1\t100\t200\tGENE1\n"
        parsed = parse_bed_overlay(content)
        overlay_id = save_overlay_config("Test", "", parsed, reference_engine)

        assert delete_overlay(overlay_id, reference_engine) is True
        assert get_overlay(overlay_id, reference_engine) is None
        assert delete_overlay(overlay_id, reference_engine) is False

    def test_get_nonexistent(self, reference_engine: sa.Engine) -> None:
        assert get_overlay(999, reference_engine) is None


# ═══════════════════════════════════════════════════════════════════════
# Apply overlay to sample
# ═══════════════════════════════════════════════════════════════════════


class TestApplyOverlay:
    """Tests for applying overlays to sample variants."""

    def test_bed_range_match(self, sample_with_variants: sa.Engine) -> None:
        """BED overlay matches variants within [start, end) range."""
        # rs12345 is at chrom=1, pos=100000
        content = "chr1\t99999\t100001\ttest_region\t42\n"
        parsed = parse_bed_overlay(content)

        result = apply_overlay(parsed, 1, "Test", sample_with_variants)
        assert result.variants_matched == 1
        assert result.records_checked == 1

    def test_bed_no_match(self, sample_with_variants: sa.Engine) -> None:
        """BED overlay with no overlapping regions."""
        content = "chr3\t1\t100\tno_match\n"
        parsed = parse_bed_overlay(content)

        result = apply_overlay(parsed, 1, "Test", sample_with_variants)
        assert result.variants_matched == 0

    def test_vcf_exact_match(self, sample_with_variants: sa.Engine) -> None:
        """VCF overlay matches variants by exact position."""
        content = (
            "##fileformat=VCFv4.2\n"
            "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"
            "1\t100000\trs12345\tA\tG\t.\tPASS\tCUSTOM=hello\n"
            "19\t44908684\trs429358\tT\tC\t.\tPASS\tCUSTOM=world\n"
        )
        parsed = parse_vcf_overlay(content)

        result = apply_overlay(parsed, 2, "VCF Test", sample_with_variants)
        assert result.variants_matched == 2

    def test_overlay_results_stored(self, sample_with_variants: sa.Engine) -> None:
        """Applied overlay results are stored in variant_overlays table."""
        from backend.annotation.vcfanno_runner import get_overlay_results

        content = (
            "##fileformat=VCFv4.2\n"
            "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"
            "1\t100000\trs12345\tA\tG\t.\tPASS\tSCORE=0.99;LABEL=test\n"
        )
        parsed = parse_vcf_overlay(content)
        apply_overlay(parsed, 3, "Results Test", sample_with_variants)

        results = get_overlay_results(3, sample_with_variants)
        assert len(results) == 1
        assert results[0]["rsid"] == "rs12345"
        assert results[0]["SCORE"] == 0.99
        assert results[0]["LABEL"] == "test"

    def test_overlay_reapply_replaces(self, sample_with_variants: sa.Engine) -> None:
        """Re-applying an overlay replaces previous results."""
        from backend.annotation.vcfanno_runner import get_overlay_results

        content = (
            "##fileformat=VCFv4.2\n"
            "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"
            "1\t100000\trs12345\tA\tG\t.\tPASS\tV=1\n"
        )
        parsed = parse_vcf_overlay(content)
        apply_overlay(parsed, 4, "Test", sample_with_variants)
        assert len(get_overlay_results(4, sample_with_variants)) == 1

        # Re-apply with different data
        content2 = (
            "##fileformat=VCFv4.2\n"
            "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"
            "1\t100000\trs12345\tA\tG\t.\tPASS\tV=2\n"
            "19\t44908684\trs429358\tT\tC\t.\tPASS\tV=3\n"
        )
        parsed2 = parse_vcf_overlay(content2)
        apply_overlay(parsed2, 4, "Test", sample_with_variants)

        results = get_overlay_results(4, sample_with_variants)
        assert len(results) == 2

    def test_delete_overlay_results(self, sample_with_variants: sa.Engine) -> None:
        """Delete overlay results from sample."""
        from backend.annotation.vcfanno_runner import delete_overlay_results, get_overlay_results

        content = (
            "##fileformat=VCFv4.2\n"
            "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"
            "1\t100000\trs12345\tA\tG\t.\tPASS\tV=1\n"
        )
        parsed = parse_vcf_overlay(content)
        apply_overlay(parsed, 5, "Test", sample_with_variants)

        deleted = delete_overlay_results(5, sample_with_variants)
        assert deleted == 1
        assert len(get_overlay_results(5, sample_with_variants)) == 0

    def test_bed_multiple_variants_in_range(self, sample_with_variants: sa.Engine) -> None:
        """BED region covering multiple variants matches all of them."""
        # rs429358 at 19:44908684, rs7412 at 19:44908822
        content = "19\t44908600\t44908900\tAPOE_region\t1\n"
        parsed = parse_bed_overlay(content)

        result = apply_overlay(parsed, 6, "Multi", sample_with_variants)
        assert result.variants_matched == 2

    def test_vcf_allele_aware_attaches_to_correct_allele(self, sample_engine: sa.Engine) -> None:
        """Two annotated variants at one position with different ALTs each receive only
        their own allele's INFO annotation — not the other's (issue #1228).

        Position-only matching cross-attaches: both records hit both rsIDs and the
        dedup-by-rsid keeps the first, so both variants would get the C>G score.
        """
        from backend.annotation.vcfanno_runner import get_overlay_results
        from backend.db.tables import annotated_variants

        with sample_engine.begin() as conn:
            conn.execute(
                sa.insert(annotated_variants),
                [
                    {
                        "rsid": "rsG",
                        "chrom": "1",
                        "pos": 200000,
                        "ref": "C",
                        "alt": "G",
                        "genotype": "CG",
                        "annotation_coverage": 0,
                    },
                    {
                        "rsid": "rsT",
                        "chrom": "1",
                        "pos": 200000,
                        "ref": "C",
                        "alt": "T",
                        "genotype": "CT",
                        "annotation_coverage": 0,
                    },
                ],
            )
        content = (
            "##fileformat=VCFv4.2\n"
            "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"
            "1\t200000\t.\tC\tG\t.\tPASS\tSCORE=0.9\n"
            "1\t200000\t.\tC\tT\t.\tPASS\tSCORE=0.1\n"
        )
        parsed = parse_vcf_overlay(content)
        apply_overlay(parsed, 7, "Allele-aware", sample_engine)

        results = {r["rsid"]: r for r in get_overlay_results(7, sample_engine)}
        assert results["rsG"]["SCORE"] == 0.9
        assert results["rsT"]["SCORE"] == 0.1  # not cross-attached from the C>G record
        assert len(results) == 2

    def test_vcf_multialt_number_a_info_attaches_per_allele(
        self, sample_engine: sa.Engine
    ) -> None:
        """Number=A INFO values from multi-ALT VCF records attach by ALT index (#1268)."""
        from backend.annotation.vcfanno_runner import get_overlay_results
        from backend.db.tables import annotated_variants

        with sample_engine.begin() as conn:
            conn.execute(
                sa.insert(annotated_variants),
                [
                    {
                        "rsid": "rsG",
                        "chrom": "1",
                        "pos": 200000,
                        "ref": "C",
                        "alt": "G",
                        "genotype": "CG",
                        "annotation_coverage": 0,
                    },
                    {
                        "rsid": "rsT",
                        "chrom": "1",
                        "pos": 200000,
                        "ref": "C",
                        "alt": "T",
                        "genotype": "CT",
                        "annotation_coverage": 0,
                    },
                ],
            )
        content = (
            "##fileformat=VCFv4.2\n"
            '##INFO=<ID=AF,Number=A,Type=Float,Description="Allele frequency">\n'
            "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"
            "1\t200000\t.\tC\tG,T\t.\tPASS\tAF=0.1,0.2\n"
        )
        parsed = parse_vcf_overlay(content)
        apply_overlay(parsed, 8, "Multi-ALT", sample_engine)

        results = {r["rsid"]: r for r in get_overlay_results(8, sample_engine)}
        assert results["rsG"]["AF"] == 0.1
        assert results["rsT"]["AF"] == 0.2
        assert len(results) == 2

    def test_vcf_multialt_position_fallback_keeps_unprojected_info(
        self, sample_engine: sa.Engine
    ) -> None:
        """Raw-only fallback keeps full multi-ALT INFO because sample ALT is unknown."""
        from backend.annotation.vcfanno_runner import get_overlay_results
        from backend.db.tables import raw_variants

        with sample_engine.begin() as conn:
            conn.execute(
                sa.insert(raw_variants),
                [
                    {
                        "rsid": "rsRaw",
                        "chrom": "1",
                        "pos": 200000,
                        "genotype": "CT",
                    }
                ],
            )
        content = (
            "##fileformat=VCFv4.2\n"
            '##INFO=<ID=AF,Number=A,Type=Float,Description="Allele frequency">\n'
            '##INFO=<ID=AD,Number=R,Type=Integer,Description="Allele depths">\n'
            "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"
            "1\t200000\t.\tC\tG,T\t.\tPASS\tAF=0.1,0.2;AD=7,3,9\n"
        )
        parsed = parse_vcf_overlay(content)
        result = apply_overlay(parsed, 9, "Raw fallback", sample_engine)

        results = get_overlay_results(9, sample_engine)
        assert result.variants_matched == 1
        assert results == [
            {
                "rsid": "rsRaw",
                "overlay_id": 9,
                "AF": "0.1,0.2",
                "AD": "7,3,9",
            }
        ]

    def test_vcf_allele_aware_skips_uncarried_alt(self, sample_engine: sa.Engine) -> None:
        """A VCF overlay allele the sample doesn't carry attaches to nothing, even though
        a different ALT exists at the same coordinate (issue #1228)."""
        from backend.db.tables import annotated_variants

        with sample_engine.begin() as conn:
            conn.execute(
                sa.insert(annotated_variants),
                [
                    {
                        "rsid": "rsG",
                        "chrom": "1",
                        "pos": 200000,
                        "ref": "C",
                        "alt": "G",
                        "genotype": "CG",
                        "annotation_coverage": 0,
                    }
                ],
            )
        content = (
            "##fileformat=VCFv4.2\n"
            "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"
            "1\t200000\t.\tC\tA\t.\tPASS\tSCORE=0.5\n"  # sample carries C>G, not C>A
        )
        parsed = parse_vcf_overlay(content)
        result = apply_overlay(parsed, 10, "Uncarried", sample_engine)
        assert result.variants_matched == 0

    def test_vcf_falls_back_to_position_when_alleles_null(self, sample_engine: sa.Engine) -> None:
        """annotated_variants present but with null ref/alt → VCF overlay falls back to
        (chrom, pos)-only matching rather than silently matching nothing (#1228).

        Guards against gating allele-aware matching on ``bool(rows)`` alone: an empty
        allele index would otherwise drop every VCF match to zero.
        """
        from backend.db.tables import annotated_variants

        with sample_engine.begin() as conn:
            conn.execute(
                sa.insert(annotated_variants),
                [
                    {
                        "rsid": "rsNull",
                        "chrom": "1",
                        "pos": 300000,
                        "ref": None,
                        "alt": None,
                        "genotype": "??",
                        "annotation_coverage": 0,
                    }
                ],
            )
        content = (
            "##fileformat=VCFv4.2\n"
            "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"
            "1\t300000\t.\tC\tG\t.\tPASS\tSCORE=0.7\n"
        )
        parsed = parse_vcf_overlay(content)
        result = apply_overlay(parsed, 11, "Null-allele fallback", sample_engine)
        assert result.variants_matched == 1  # position-only fallback, not zero

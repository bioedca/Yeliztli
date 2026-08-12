from types import SimpleNamespace

import pytest

from backend.analysis.pathway_coverage import (
    coverage_interpretation,
    pathway_summary_text,
    variant_label,
)


def test_no_called_snps_does_not_read_as_clean_negative() -> None:
    missing = [SimpleNamespace(rsid="rs1801133", coverage_status="not_on_array")]

    text = coverage_interpretation(
        level="Standard",
        called_count=0,
        missing_snps=missing,
        indeterminate_count=0,
    )

    assert text == "No tracked SNPs assessed; 1 tracked SNP (1 off-chip) not assessed."
    assert "No variants of concern" not in text


def test_no_called_snps_pathway_summary_does_not_read_as_clean_negative() -> None:
    missing = [SimpleNamespace(rsid="rs1801133", coverage_status="not_on_array")]

    text = pathway_summary_text(
        pathway_name="Folate metabolism",
        level="Standard",
        called_count=0,
        missing_snps=missing,
        indeterminate_count=0,
    )

    assert text == (
        "Folate metabolism — No tracked SNPs assessed; 1 tracked SNP (1 off-chip) not assessed."
    )
    assert "No variants of concern" not in text


def test_standard_indeterminate_wording_precedes_no_called_fallback() -> None:
    missing = [SimpleNamespace(rsid="rs1049434", coverage_status="not_on_array")]

    text = coverage_interpretation(
        level="Standard",
        called_count=0,
        missing_snps=missing,
        indeterminate_count=1,
    )

    assert text == (
        "Standard result is based on interpreted SNPs only; "
        "1 tracked SNP (1 off-chip) not assessed."
    )
    assert "No tracked SNPs assessed" not in text


# ── variant_label (#2021) ─────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("gene", "variant_name", "expected"),
    [
        # The defect: roughly a third of curated entries repeat the gene.
        ("FTO", "FTO intron 1", "FTO intron 1"),
        ("DRD4", "DRD4 exon III VNTR proxy", "DRD4 exon III VNTR proxy"),
        ("HLA-B", "HLA-B*57:01 proxy", "HLA-B*57:01 proxy"),
        ("GC", "GC/DBP variant", "GC/DBP variant"),
        # ...and two thirds do not, so the prefix must still be added.
        ("VDR", "FokI (M1T)", "VDR FokI (M1T)"),
        ("MTHFR", "C677T", "MTHFR C677T"),
        ("IL13", "R130Q", "IL13 R130Q"),
        # Word boundary: a gene that merely shares a prefix is still prepended,
        # so "GC" must not be swallowed by an unrelated "GCH1 …" name.
        ("GC", "GCH1 promoter variant", "GC GCH1 promoter variant"),
        # Degenerate inputs.
        ("FTO", "", "FTO"),
        ("", "FokI (M1T)", "FokI (M1T)"),
        ("FTO", "FTO", "FTO"),
        ("fto", "FTO intron 1", "FTO intron 1"),
        ("  FTO  ", " FTO intron 1 ", "FTO intron 1"),
    ],
)
def test_variant_label(gene: str, variant_name: str, expected: str) -> None:
    assert variant_label(gene, variant_name) == expected

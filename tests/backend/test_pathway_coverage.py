from types import SimpleNamespace

from backend.analysis.pathway_coverage import coverage_interpretation, pathway_summary_text


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

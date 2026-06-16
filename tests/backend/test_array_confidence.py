"""Unit tests for the array-confidence reliability badge (SW-A11 / #14).

Covers the pure Weedon-PPV classification, the catalogue/novelty signal, and the
badge envelope. The badge is a reliability flag only — these tests lock that it
never carries or implies an evidence-level/significance change.
"""

from __future__ import annotations

import pytest
import sqlalchemy as sa

from backend.analysis.array_confidence import (
    APOE_ARRAY_RELIABILITY_PMIDS,
    RELIABILITY_HIGH,
    RELIABILITY_LOCUS_LOW,
    RELIABILITY_LOW,
    RELIABILITY_MODERATE,
    RELIABILITY_UNKNOWN,
    RELIABILITY_VERY_LOW,
    WEEDON_PMID,
    _is_catalogued,
    array_confidence_badge,
    assess_pathogenic_findings,
    classify_array_reliability,
)
from backend.db.tables import annotated_variants, findings
from backend.disclaimers import ARRAY_CONFIDENCE_CONTEXT_ONLY


class TestClassifyArrayReliability:
    @pytest.mark.parametrize(
        "popmax_af,expected",
        [
            (0.30, RELIABILITY_HIGH),
            (0.01, RELIABILITY_HIGH),
            (1e-3, RELIABILITY_HIGH),  # band edge inclusive (>=)
            (1.00001e-3, RELIABILITY_HIGH),  # just above the HIGH edge
            (9.99999e-4, RELIABILITY_MODERATE),  # just below the HIGH edge
            (9.9e-4, RELIABILITY_MODERATE),
            (5e-4, RELIABILITY_MODERATE),
            (1e-5, RELIABILITY_MODERATE),  # band edge inclusive (>=)
            (1.00001e-5, RELIABILITY_MODERATE),  # just above the MODERATE edge
            (9.99999e-6, RELIABILITY_LOW),  # just below the MODERATE edge
            (9e-6, RELIABILITY_LOW),
            (1e-7, RELIABILITY_LOW),
            (0.0, RELIABILITY_LOW),
        ],
    )
    def test_frequency_bands(self, popmax_af: float, expected: str) -> None:
        # Catalogue status is irrelevant once a usable frequency is known.
        assert classify_array_reliability(popmax_af, is_catalogued=True) == expected
        assert classify_array_reliability(popmax_af, is_catalogued=False) == expected

    def test_no_frequency_catalogued_is_unknown_not_high(self) -> None:
        # Fail-safe: missing AF is never treated as "common/reliable".
        assert classify_array_reliability(None, is_catalogued=True) == RELIABILITY_UNKNOWN

    def test_no_frequency_uncatalogued_is_very_low(self) -> None:
        assert classify_array_reliability(None, is_catalogued=False) == RELIABILITY_VERY_LOW

    @pytest.mark.parametrize("bad_af", [-1.0, -1e-6, -0.5])
    def test_negative_af_is_treated_as_unavailable_not_low(self, bad_af: float) -> None:
        # A negative AF can only be upstream corruption — fail-safe to
        # unknown/very_low, never a confident rare-variant (LOW) call.
        assert classify_array_reliability(bad_af, is_catalogued=True) == RELIABILITY_UNKNOWN
        assert classify_array_reliability(bad_af, is_catalogued=False) == RELIABILITY_VERY_LOW


class TestIsCatalogued:
    def test_dbsnp_rs_identifier(self) -> None:
        assert _is_catalogued("rs80357906", None, None) is True

    def test_clinvar_significance(self) -> None:
        assert _is_catalogued(None, "Pathogenic", None) is True

    def test_clinvar_accession(self) -> None:
        assert _is_catalogued(None, None, "VCV000017661") is True

    def test_i_prefix_chip_id_is_not_catalogued(self) -> None:
        # Vendor "i" probe IDs are not dbSNP identifiers.
        assert _is_catalogued("i5000123", None, None) is False

    @pytest.mark.parametrize("rsid", ["RS80357906", "Rs80357906", "rS80357906"])
    def test_dbsnp_rs_identifier_is_case_insensitive(self, rsid: str) -> None:
        assert _is_catalogued(rsid, None, None) is True

    def test_nothing_known_is_not_catalogued(self) -> None:
        assert _is_catalogued(None, None, None) is False


class TestArrayConfidenceBadge:
    def test_high_band_does_not_recommend_confirmation(self) -> None:
        badge = array_confidence_badge(0.05, is_catalogued=True)
        assert badge["reliability"] == RELIABILITY_HIGH
        assert badge["confirm_in_clia_recommended"] is False
        assert badge["is_novel"] is False

    def test_moderate_band_recommends_confirmation(self) -> None:
        badge = array_confidence_badge(5e-4, is_catalogued=True)
        assert badge["reliability"] == RELIABILITY_MODERATE
        assert badge["confirm_in_clia_recommended"] is True

    def test_low_band_recommends_confirmation(self) -> None:
        badge = array_confidence_badge(1e-6, is_catalogued=True)
        assert badge["reliability"] == RELIABILITY_LOW
        assert badge["confirm_in_clia_recommended"] is True

    def test_novel_flag_only_when_uncatalogued_and_no_af(self) -> None:
        assert array_confidence_badge(None, is_catalogued=False)["is_novel"] is True
        assert array_confidence_badge(None, is_catalogued=True)["is_novel"] is False
        assert array_confidence_badge(1e-6, is_catalogued=False)["is_novel"] is False

    def test_badge_envelope_is_context_only(self) -> None:
        badge = array_confidence_badge(5e-4, is_catalogued=True)
        assert badge["context_only"] is True
        assert badge["note"] == ARRAY_CONFIDENCE_CONTEXT_ONLY
        assert WEEDON_PMID in badge["pmid_citations"]
        # A reliability flag must never carry evidence-tier / significance fields.
        assert "evidence_level" not in badge
        assert "clinvar_significance" not in badge
        assert badge["gnomad_af_popmax"] == 5e-4


class TestLocusSpecificLowReliability:
    """#636: a locus that is a documented genotyping-array weak spot is rated
    ``locus_low`` regardless of allele frequency. The leading case is the APOE
    ε-defining pair rs429358/rs7412 — common (ε4 ~15%), so a frequency-only model
    rates them ``high`` and hides that they are array weak spots."""

    @pytest.mark.parametrize("rsid", ["rs429358", "rs7412"])
    def test_listed_locus_is_locus_low_despite_common_af(self, rsid: str) -> None:
        # popmax AF ~15% would map to HIGH on frequency alone.
        assert (
            classify_array_reliability(0.15, is_catalogued=True, rsid=rsid)
            == RELIABILITY_LOCUS_LOW
        )

    @pytest.mark.parametrize("rsid", ["RS429358", "Rs7412", "rs7412"])
    def test_locus_match_is_case_insensitive(self, rsid: str) -> None:
        assert (
            classify_array_reliability(0.15, is_catalogued=True, rsid=rsid)
            == RELIABILITY_LOCUS_LOW
        )

    def test_unlisted_rsid_keeps_frequency_band(self) -> None:
        assert (
            classify_array_reliability(0.15, is_catalogued=True, rsid="rs1801133")
            == RELIABILITY_HIGH
        )
        # No rsid passed → unchanged frequency behaviour (back-compatible default).
        assert classify_array_reliability(0.15, is_catalogued=True) == RELIABILITY_HIGH

    def test_badge_for_locus_low_recommends_confirmation_and_cites(self) -> None:
        badge = array_confidence_badge(0.15, is_catalogued=True, rsid="rs429358")
        assert badge["reliability"] == RELIABILITY_LOCUS_LOW
        assert badge["confirm_in_clia_recommended"] is True
        assert badge["locus_low_reliability"] is True
        # Transparency: frequency alone would have rated this common SNP HIGH.
        assert badge["frequency_band"] == RELIABILITY_HIGH
        # Locus-specific reason + APOE concordance citations, plus the Weedon anchor.
        assert "APOE" in badge["detail"]
        for pmid in APOE_ARRAY_RELIABILITY_PMIDS:
            assert pmid in badge["pmid_citations"]
        assert WEEDON_PMID in badge["pmid_citations"]
        assert badge["is_novel"] is False

    def test_unlisted_badge_is_unchanged(self) -> None:
        badge = array_confidence_badge(0.15, is_catalogued=True, rsid="rs1801133")
        assert badge["reliability"] == RELIABILITY_HIGH
        assert badge["locus_low_reliability"] is False
        assert badge["frequency_band"] is None
        assert badge["pmid_citations"] == [WEEDON_PMID]

    def test_apoe_module_shares_the_same_citations(self) -> None:
        """The APOE reliability caveat (#557/#625) sources its PMIDs from this
        shared model (#636), so the two can never drift apart."""
        from backend.analysis.apoe import APOE_RELIABILITY_PMIDS

        assert APOE_RELIABILITY_PMIDS == APOE_ARRAY_RELIABILITY_PMIDS


class TestAssessPathogenicFindings:
    def test_compound_pathogenic_finding_is_selected(self, sample_engine: sa.Engine) -> None:
        with sample_engine.begin() as conn:
            conn.execute(
                findings.insert(),
                [
                    {
                        "module": "rare_variants",
                        "category": "clinvar_pathogenic",
                        "evidence_level": 4,
                        "gene_symbol": "CFTR",
                        "rsid": "rs_compound_plp",
                        "finding_text": "CFTR rs_compound_plp — Pathogenic|drug response",
                        "clinvar_significance": "Pathogenic|drug response",
                    },
                    {
                        "module": "rare_variants",
                        "category": "rare",
                        "evidence_level": 1,
                        "gene_symbol": "GENE2",
                        "rsid": "rs_conflicting",
                        "finding_text": "GENE2 rs_conflicting — conflicting",
                        "clinvar_significance": "Conflicting classifications of pathogenicity",
                    },
                ],
            )
            conn.execute(
                annotated_variants.insert().values(
                    rsid="rs_compound_plp",
                    chrom="1",
                    pos=1000,
                    clinvar_significance="Pathogenic|drug response",
                    clinvar_accession="VCV000000001",
                    gnomad_af_popmax=1e-6,
                )
            )

        rows = assess_pathogenic_findings(sample_engine)

        assert {row["rsid"] for row in rows} == {"rs_compound_plp"}
        assert rows[0]["clinvar_significance"] == "Pathogenic|drug response"
        assert rows[0]["reliability"] == RELIABILITY_LOW

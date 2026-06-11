"""Tests for strand- and order-aware genotype lookup (backend.analysis.genotype_lookup).

Guards the strand-harmonization fix for the categorical-scoring modules: a chip
reports alleles on its design strand, which for some SNPs is the reverse strand
of the panel's curated genotype keys (the MTHFR C677T C/T-vs-G/A class). The
lookup must resolve allele order, slash-delimited indel order, and the
Watson-Crick complement strand — reference strand first.
"""

from __future__ import annotations

import pytest

from backend.analysis.genotype_lookup import (
    genotype_candidates,
    lookup_by_genotype,
    risk_allele_dosage,
)


class TestGenotypeCandidates:
    def test_two_char_acgt_order_then_complement(self) -> None:
        # Reference strand first (as-is, reversed), then complemented strand.
        assert genotype_candidates("CT") == ["CT", "TC", "GA", "AG"]

    def test_homozygous_acgt(self) -> None:
        assert genotype_candidates("CC") == ["CC", "GG"]

    def test_single_allele(self) -> None:
        assert genotype_candidates("T") == ["T", "A"]

    def test_palindrome_deduplicated(self) -> None:
        # A/T is its own complement set; no duplicate lookups.
        assert genotype_candidates("AT") == ["AT", "TA"]

    def test_slash_indel_order_only_no_complement(self) -> None:
        # Slash-delimited indels are non-ACGT → order swap only, no complement.
        assert genotype_candidates("delG/G") == ["delG/G", "G/delG"]

    def test_non_acgt_skips_complement(self) -> None:
        assert genotype_candidates("II") == ["II"]

    def test_lowercase_acgt_normalized_to_panel_frame(self) -> None:
        # A chip may report lowercase; pure-ACGT genotypes normalize to uppercase.
        assert genotype_candidates("ct") == ["CT", "TC", "GA", "AG"]

    def test_mixed_case_indel_preserves_original_case(self) -> None:
        # Indel tokens like "del" must NOT be uppercased (panel keys are mixed-case).
        assert genotype_candidates("delG/G") == ["delG/G", "G/delG"]


class TestLookupByGenotype:
    def test_exact_match(self) -> None:
        assert lookup_by_genotype({"CT": "x"}, "CT") == "x"

    def test_reversed_order_match(self) -> None:
        assert lookup_by_genotype({"TC": "x"}, "CT") == "x"

    def test_complement_strand_match(self) -> None:
        # The flagship MTHFR C677T case: chip "CT" → panel "GA".
        assert lookup_by_genotype({"GA": "moderate"}, "CT") == "moderate"

    def test_reverse_complement_match(self) -> None:
        assert lookup_by_genotype({"AG": "moderate"}, "CT") == "moderate"

    def test_homozygous_complement(self) -> None:
        assert lookup_by_genotype({"GG": "std"}, "CC") == "std"
        assert lookup_by_genotype({"AA": "elev"}, "TT") == "elev"

    def test_slash_indel_swap(self) -> None:
        assert lookup_by_genotype({"delG/G": "carrier"}, "G/delG") == "carrier"

    def test_reference_strand_preferred_over_complement(self) -> None:
        # An exact (reference-strand) key wins over a complemented one.
        assert lookup_by_genotype({"CT": "ref", "GA": "flip"}, "CT") == "ref"

    def test_lowercase_genotype_matches_uppercase_key(self) -> None:
        # The flagship MTHFR fix must survive a lowercase chip genotype.
        assert lookup_by_genotype({"GA": "moderate"}, "ct") == "moderate"
        assert lookup_by_genotype({"CT": "x"}, "ct") == "x"

    def test_explicit_none_value_returned_for_present_key(self) -> None:
        # Membership test: a present key wins even if its value is None, rather
        # than falling through to a complement-strand match.
        assert lookup_by_genotype({"CT": None, "GA": "flip"}, "CT") is None

    def test_no_match_returns_none(self) -> None:
        assert lookup_by_genotype({"AA": "x"}, "CT") is None

    def test_single_allele_complement(self) -> None:
        assert lookup_by_genotype({"A": "x"}, "T") == "x"

    def test_non_acgt_no_match_does_not_raise(self) -> None:
        assert lookup_by_genotype({"DD": "x"}, "II") is None


class TestRiskAlleleDosage:
    """Strand-aware allele counting must agree with lookup_by_genotype (issue #24)."""

    @pytest.mark.parametrize(
        "genotype,expected",
        [
            ("CC", 0),  # ref/ref
            ("CT", 1),  # het, reference strand
            ("TC", 1),  # het, reversed order
            ("TT", 2),  # hom risk, reference strand
        ],
    )
    def test_reference_strand_counts(self, genotype: str, expected: int) -> None:
        # C/T SNP, panel risk allele T.
        assert risk_allele_dosage(genotype, risk_allele="T", ref_allele="C") == expected

    @pytest.mark.parametrize(
        "genotype,expected",
        [
            ("GG", 0),  # complement of CC → ref/ref
            ("GA", 1),  # complement of CT → 1 risk allele
            ("AG", 1),  # complement of TC → 1 risk allele
            ("AA", 2),  # complement of TT → 2 risk alleles
        ],
    )
    def test_complement_strand_counts(self, genotype: str, expected: int) -> None:
        # Same C/T SNP reported on the opposite (design) strand — the issue #24 case.
        assert risk_allele_dosage(genotype, risk_allele="T", ref_allele="C") == expected

    def test_ag_snp_complement_strand(self) -> None:
        # MC1R D294H (rs1805009): panel risk A / ref G; complement strand is T/C
        # (A->T, G->C). So "TT" complements to risk "AA", "CC" to ref "GG".
        assert risk_allele_dosage("CT", risk_allele="A", ref_allele="G") == 1
        assert risk_allele_dosage("TT", risk_allele="A", ref_allele="G") == 2
        assert risk_allele_dosage("CC", risk_allele="A", ref_allele="G") == 0

    def test_reference_strand_preferred_for_palindrome(self) -> None:
        # A/T is its own complement set: count on the reference strand, never flip.
        assert risk_allele_dosage("AT", risk_allele="A", ref_allele="T") == 1
        assert risk_allele_dosage("AA", risk_allele="A", ref_allele="T") == 2

    def test_lowercase_genotype(self) -> None:
        assert risk_allele_dosage("ga", risk_allele="T", ref_allele="C") == 1

    def test_unexpected_base_falls_back_to_direct_count(self) -> None:
        # Genotype that fits neither {risk,ref} nor its complement: direct count.
        assert risk_allele_dosage("TA", risk_allele="T", ref_allele="C") == 1

"""Tests for strand- and order-aware genotype lookup (backend.analysis.genotype_lookup).

Guards the strand-harmonization fix for the categorical-scoring modules: a chip
reports alleles on its design strand, which for some SNPs is the reverse strand
of the panel's curated genotype keys (the MTHFR C677T C/T-vs-G/A class). The
lookup must resolve allele order, slash-delimited indel order, and the
Watson-Crick complement strand — reference strand first.
"""

from __future__ import annotations

from backend.analysis.genotype_lookup import (
    genotype_candidates,
    is_strand_ambiguous,
    lookup_by_genotype,
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


class TestIsStrandAmbiguous:
    """Palindromic (A/T, C/G) homozygotes whose two strands map to different
    curated values are strand-unresolvable from the genotype string (#170)."""

    # Palindromic A/T SNP whose homozygotes map to DIFFERENT categories.
    _AT = {"TT": "Standard", "AT": "Moderate", "TA": "Moderate", "AA": "Elevated"}

    def test_palindromic_homozygotes_are_ambiguous(self) -> None:
        assert is_strand_ambiguous(self._AT, "AA") is True
        assert is_strand_ambiguous(self._AT, "TT") is True

    def test_lowercase_palindromic_homozygote_is_ambiguous(self) -> None:
        assert is_strand_ambiguous(self._AT, "aa") is True

    def test_heterozygote_is_strand_invariant(self) -> None:
        # AT complement is TA (same het) → never ambiguous.
        assert is_strand_ambiguous(self._AT, "AT") is False
        assert is_strand_ambiguous(self._AT, "TA") is False

    def test_cg_palindrome_homozygote_is_ambiguous(self) -> None:
        cg = {"CC": "Standard", "CG": "Moderate", "GG": "Elevated"}
        assert is_strand_ambiguous(cg, "CC") is True
        assert is_strand_ambiguous(cg, "GG") is True

    def test_non_palindromic_snp_not_flagged(self) -> None:
        # C/T and G/A pairs are not complementary → strand resolves by complement.
        ct = {"CC": "Standard", "CT": "Moderate", "TT": "Elevated"}
        assert is_strand_ambiguous(ct, "CC") is False
        assert is_strand_ambiguous(ct, "TT") is False
        ga = {"GG": "Standard", "GA": "Moderate", "AA": "Elevated"}  # MTHFR-style
        assert is_strand_ambiguous(ga, "GG") is False
        assert is_strand_ambiguous(ga, "AA") is False

    def test_same_category_both_strands_not_ambiguous(self) -> None:
        # If both palindromic homozygotes share a category, strand is irrelevant.
        same = {"AA": "Standard", "AT": "Standard", "TT": "Standard"}
        assert is_strand_ambiguous(same, "AA") is False

    def test_only_one_strand_keyed_not_ambiguous(self) -> None:
        # Complement homozygote absent from the mapping → nothing to disagree with.
        one = {"AA": "Elevated", "AT": "Moderate"}
        assert is_strand_ambiguous(one, "AA") is False

    def test_indel_and_nocall_not_flagged(self) -> None:
        assert is_strand_ambiguous({"DD": "x", "II": "y"}, "DD") is False
        assert is_strand_ambiguous(self._AT, "--") is False

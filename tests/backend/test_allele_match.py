"""Tests for the shared strand-aware allele matching helper.

Covers EXPANSION_STRATEGY.md §10 harmonization (proposal #4):
  - the headline bug: a reverse-strand weight set silently inverts dosage under
    the old literal match, and is corrected here;
  - the bigsnpr strand-ambiguous-palindrome drop rule near MAF 0.5;
  - palindromes away from 0.5: strand-invariant heterozygote resolves, but a
    strand-ambiguous homozygote is dropped for a single sample (#247);
  - no-call / non-ACGT / unresolved handling;
  - strict back-compatibility of the legacy (no other-allele) path;
  - the risk-genotype counting primitive (risk_dosage) incl. minus-strand.
"""

from __future__ import annotations

from backend.analysis.allele_match import (
    AMBIGUOUS_DROPPED,
    MATCHED_FLIP,
    MATCHED_REF,
    MISSING_FREQ,
    NO_CALL,
    UNRESOLVED,
    match_effect_allele_dosage,
    risk_dosage,
)
from backend.analysis.prs import _count_effect_allele


class TestStrandFlipHeadlineBug:
    """A reverse-strand weight set must resolve via complement, not invert."""

    def test_reverse_strand_homozygous_resolves(self) -> None:
        # True + strand alleles C/T; the weight reports them but the chip
        # observed the reverse strand "GG" (complement of CC).
        m = match_effect_allele_dosage("GG", "C", "T", 0.20)
        assert m.status == MATCHED_FLIP
        assert m.strand == "flip"
        assert m.dosage == 2

    def test_reverse_strand_heterozygous_resolves(self) -> None:
        # "GA" is the reverse-strand representation of C/T; one copy of effect C.
        m = match_effect_allele_dosage("GA", "C", "T", 0.20)
        assert m.status == MATCHED_FLIP
        assert m.dosage == 1

    def test_old_literal_match_would_invert(self) -> None:
        # Regression proof: the legacy literal counter scores the same reverse
        # strand genotype as 0 (wrong) — this is exactly the silent inversion.
        assert _count_effect_allele("GG", "C") == 0


class TestPalindromeAmbiguity:
    """Palindromic A/T and C/G resolution is purely by zygosity, MAF-independent
    for a single sample (#247, #353): a heterozygote is strand-invariant and always
    resolves to dosage 1; a homozygote is strand-ambiguous and always dropped."""

    def test_palindrome_het_at_half_resolves(self) -> None:
        # #353: a palindromic HET reads as {A,T} on either strand → exactly one
        # effect copy regardless of MAF, including the near-0.5 band where it was
        # previously (and needlessly) dropped. Recoverable coverage.
        m = match_effect_allele_dosage("AT", "A", "T", 0.50)
        assert m.status == MATCHED_REF
        assert m.dosage == 1
        # C/G het near 0.5 likewise resolves.
        cg = match_effect_allele_dosage("CG", "C", "G", 0.50)
        assert cg.status == MATCHED_REF
        assert cg.dosage == 1

    def test_palindrome_het_resolves_at_any_maf(self) -> None:
        # MAF-independent: het resolves to dosage 1 in-band (0.41) and out (0.39, 0.05).
        for maf in (0.41, 0.39, 0.05, 0.95):
            out = match_effect_allele_dosage("AT", "A", "T", maf)
            assert out.status == MATCHED_REF, f"het dropped at maf={maf}"
            assert out.dosage == 1

    def test_palindrome_homozygote_at_half_dropped(self) -> None:
        # A palindromic HOMOZYGOTE stays strand-ambiguous for a single sample at any
        # MAF (an opposite-strand "AA" is the complement "TT"), so it is dropped —
        # the near-0.5 band's only legitimate effect for n=1.
        m = match_effect_allele_dosage("AA", "A", "T", 0.50)
        assert m.status == AMBIGUOUS_DROPPED
        assert m.dosage is None
        assert match_effect_allele_dosage("CC", "C", "G", 0.50).status == AMBIGUOUS_DROPPED

    def test_palindrome_homozygote_away_from_half_dropped(self) -> None:
        # #247: a palindromic HOMOZYGOTE is dropped away from 0.5 too — frequency
        # cannot orient one genotype's strand (a cohort technique, invalid for n=1).
        m = match_effect_allele_dosage("AA", "A", "T", 0.05)
        assert m.status == AMBIGUOUS_DROPPED
        assert m.dosage is None
        assert match_effect_allele_dosage("CC", "C", "G", 0.05).status == AMBIGUOUS_DROPPED

    def test_palindrome_without_maf_dropped(self) -> None:
        # maf=None still drops (MISSING_FREQ) so curated anchor sets (metabolic_prs,
        # which passes maf=None) keep their conservative no-frequency discipline.
        m = match_effect_allele_dosage("AT", "A", "T", None)
        assert m.status == MISSING_FREQ
        assert m.dosage is None


class TestNoCallAndUnresolved:
    def test_no_call_dashes(self) -> None:
        m = match_effect_allele_dosage("--", "C", "T", 0.2)
        assert m.status == NO_CALL
        assert m.dosage is None

    def test_none_genotype(self) -> None:
        assert match_effect_allele_dosage(None, "C", "T", 0.2).status == NO_CALL

    def test_non_acgt_unresolved(self) -> None:
        assert match_effect_allele_dosage("A-", "C", "T", 0.2).status == UNRESOLVED

    def test_different_variant_unresolved(self) -> None:
        # {C,G} fits neither {C,T} nor its complement {G,A}.
        m = match_effect_allele_dosage("CG", "C", "T", 0.2)
        assert m.status == UNRESOLVED
        assert m.dosage is None


class TestLegacyBackCompat:
    """No other allele → exact historical literal-count behaviour."""

    def test_het_counts_one(self) -> None:
        assert match_effect_allele_dosage("AG", "A", None, None).dosage == 1

    def test_hom_non_effect_counts_zero(self) -> None:
        assert match_effect_allele_dosage("GG", "A", None, None).dosage == 0

    def test_hom_effect_counts_two(self) -> None:
        m = match_effect_allele_dosage("AA", "A", None, None)
        assert m.dosage == 2
        assert m.status == MATCHED_REF

    def test_single_char_is_zero(self) -> None:
        # Historical contract: haploid single-allele call → 0 (no diploid dosage).
        assert match_effect_allele_dosage("A", "A", None, None).dosage == 0

    def test_no_strand_flip_attempted_without_other_allele(self) -> None:
        # With only the effect allele, a reverse-strand flip is undecidable, so
        # the literal count stands (guessing would re-introduce the inversion).
        assert match_effect_allele_dosage("GG", "C", None, 0.2).dosage == 0


class TestRiskDosage:
    """The risk-genotype counting primitive (used by monogenic risk modules)."""

    def test_plus_strand_counts(self) -> None:
        assert risk_dosage("AA", "A", "G") == 2
        assert risk_dosage("AG", "A", "G") == 1
        assert risk_dosage("GG", "A", "G") == 0

    def test_minus_strand_resolves(self) -> None:
        # Factor V Leiden rs6025: risk A / ref G on +; chip reports minus strand.
        # "TC" is the complement of "AG" → one copy of the risk allele.
        assert risk_dosage("TC", "A", "G") == 1
        # "TT" is the complement of homozygous-risk "AA" → dosage 2.
        assert risk_dosage("TT", "A", "G") == 2

    def test_no_call_is_indeterminate(self) -> None:
        assert risk_dosage("--", "A", "G") is None
        assert risk_dosage(None, "A", "G") is None

    def test_unresolvable_is_indeterminate(self) -> None:
        # "AC" fits neither {A,G} nor its complement {T,C} → indeterminate.
        assert risk_dosage("AC", "A", "G") is None

    def test_homozygous_reference_complement_is_zero(self) -> None:
        # "CC" is the reverse-strand representation of hom-ref "GG" → dosage 0
        # (resolvable, not indeterminate).
        assert risk_dosage("CC", "A", "G") == 0


class TestRiskDosageNoComplement:
    """``allow_complement=False`` for haploid mtDNA loci (issue #30).

    A complemented-only observation must be indeterminate (None), not a
    reverse-strand risk allele, while plus-strand reference/risk bases still
    count. Uses MT-RNR1 m.1555A>G framing: ref A, risk G, complement pair {T, C}.
    """

    def test_complement_only_is_indeterminate(self) -> None:
        # Plus-strand "C" merely complements risk "G"; without reverse-strand
        # provenance it is a different variant, not the m.1555A>G allele.
        assert risk_dosage("C", "G", "A", allow_complement=False) is None
        assert risk_dosage("CC", "G", "A", allow_complement=False) is None
        # Complement of ref "A" is "T" — also indeterminate, not hom-ref.
        assert risk_dosage("T", "G", "A", allow_complement=False) is None

    def test_plus_strand_still_counts(self) -> None:
        assert risk_dosage("G", "G", "A", allow_complement=False) == 1
        assert risk_dosage("GG", "G", "A", allow_complement=False) == 2
        assert risk_dosage("A", "G", "A", allow_complement=False) == 0

    def test_default_still_complements(self) -> None:
        # The flag defaults to True, preserving nuclear strand-flip resolution.
        assert risk_dosage("C", "G", "A") == 1
        assert risk_dosage("TC", "A", "G") == 1

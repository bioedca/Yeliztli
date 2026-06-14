"""Tests for the strand-aware build-36 → GRCh37 lift (#562).

The crux is strand handling: when hg18→hg19 liftover inverts a region (target
strand ``-``), ACGT genotype alleles must be Watson–Crick complemented, while
indel tokens, no-calls and hemizygous non-ACGT calls must be left untouched.
"""

from __future__ import annotations

import pytest

from backend.ingestion.base import ParsedVariant
from backend.ingestion.build36_liftover import (
    _complement_genotype,
    lift_build36_variants,
)
from backend.ingestion.liftover import reset_liftover

# A real plus-strand build-36 locus (APOE rs7412) → GRCh37 19:45412079.
PLUS_CHROM, PLUS_POS, PLUS_GRCH37 = "19", 50103919, 45412079
# A build-36 position in a known minus-strand chain block → GRCh37 1:2494418 (-).
MINUS_CHROM, MINUS_POS, MINUS_GRCH37 = "1", 2480000, 2494418


@pytest.fixture(autouse=True)
def _reset() -> None:
    """Isolate the LiftOver singleton between tests."""
    reset_liftover()
    yield
    reset_liftover()


def _v(chrom: str, pos: int, genotype: str, rsid: str = "rs1") -> ParsedVariant:
    return ParsedVariant(rsid=rsid, chrom=chrom, pos=pos, genotype=genotype)


# ── _complement_genotype ──────────────────────────────────────────────


class TestComplementGenotype:
    @pytest.mark.parametrize(
        ("genotype", "expected"),
        [
            ("AG", "TC"),
            ("CC", "GG"),
            ("GT", "CA"),
            ("AT", "TA"),  # palindromic — still complemented (chain says inverted)
            ("A", "T"),  # hemizygous single base (e.g. male X)
        ],
    )
    def test_pure_acgt_is_complemented(self, genotype: str, expected: str) -> None:
        assert _complement_genotype(genotype) == expected

    @pytest.mark.parametrize("token", ["--", "-", "II", "DD", "DI", "ID", "I", "D", ""])
    def test_non_acgt_tokens_unchanged(self, token: str) -> None:
        """No-calls, indels and empty tokens are never complemented."""
        assert _complement_genotype(token) == token


# ── lift_build36_variants ─────────────────────────────────────────────


class TestLiftBuild36Variants:
    def test_plus_strand_keeps_coordinates_and_genotype(self) -> None:
        result = lift_build36_variants([_v(PLUS_CHROM, PLUS_POS, "CC", rsid="rs7412")])
        assert result.lifted == 1
        assert result.dropped == 0
        assert result.strand_flipped == 0
        v = result.variants[0]
        assert (v.chrom, v.pos, v.genotype) == (PLUS_CHROM, PLUS_GRCH37, "CC")

    def test_minus_strand_complements_snp_alleles(self) -> None:
        """The load-bearing case: an inverted region complements ACGT alleles."""
        result = lift_build36_variants([_v(MINUS_CHROM, MINUS_POS, "AG")])
        assert result.lifted == 1
        assert result.strand_flipped == 1
        v = result.variants[0]
        assert (v.chrom, v.pos) == (MINUS_CHROM, MINUS_GRCH37)
        assert v.genotype == "TC"  # AG complemented on the minus strand

    def test_minus_strand_does_not_complement_indels_or_nocalls(self) -> None:
        """Indel tokens and no-calls keep their value even on a minus strand,
        and do not count as strand-flipped."""
        result = lift_build36_variants(
            [
                _v(MINUS_CHROM, MINUS_POS, "DI", rsid="iIndel"),
                _v(MINUS_CHROM, MINUS_POS, "--", rsid="iNoCall"),
            ]
        )
        assert result.lifted == 2
        assert result.strand_flipped == 0
        by_rsid = {v.rsid: v for v in result.variants}
        assert by_rsid["iIndel"].genotype == "DI"
        assert by_rsid["iNoCall"].genotype == "--"
        # both still lifted to the GRCh37 coordinate
        assert by_rsid["iIndel"].pos == MINUS_GRCH37

    def test_non_lifting_variants_are_dropped_and_counted(self) -> None:
        """A chromosome absent from the chain is dropped, not guessed."""
        result = lift_build36_variants(
            [
                _v(PLUS_CHROM, PLUS_POS, "CC", rsid="rs7412"),  # lifts
                _v("99", 100, "AA", rsid="bogus"),  # invalid chrom → dropped
            ]
        )
        assert result.lifted == 1
        assert result.dropped == 1
        assert [v.rsid for v in result.variants] == ["rs7412"]

    def test_mt_variants_are_dropped(self) -> None:
        """MT does not lift (hg18 chrM is not rCRS), so v3 MT variants are dropped."""
        result = lift_build36_variants([_v("MT", 263, "A", rsid="iMT")])
        assert result.lifted == 0
        assert result.dropped == 1
        assert result.variants == []

    def test_empty_input(self) -> None:
        result = lift_build36_variants([])
        assert result.lifted == 0
        assert result.dropped == 0
        assert result.strand_flipped == 0
        assert result.variants == []

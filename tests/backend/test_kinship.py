"""Tests for the within-account KING-robust kinship module.

The KING-robust estimator is φ = (N_hethet − 2·N_ibs0) / (Het_i + Het_j). These
tests build two rsID→genotype maps with controlled genotype-pair compositions so
the resulting φ, IBS0 proportion, and relationship band are exact and
deterministic: a duplicate scores ~0.5; parent-offspring and full-sibling both
sit at ~0.25 but split on IBS0; unrelated scores ~0; and a pair with too few
shared SNPs is reported as indeterminate.
"""

from __future__ import annotations

from backend.analysis.kinship import (
    MIN_SHARED_SNPS,
    KinshipPair,
    KinshipStats,
    _classify,
    _hom_allele,
    _is_het,
    _pair_text,
    king_kinship,
)


def _build(spec: list[tuple[int, str, str]]) -> tuple[dict[str, str], dict[str, str]]:
    """Build (genos_i, genos_j) from (count, genotype_i, genotype_j) tuples."""
    gi: dict[str, str] = {}
    gj: dict[str, str] = {}
    idx = 0
    for count, a, b in spec:
        for _ in range(count):
            rsid = f"r{idx}"
            idx += 1
            gi[rsid] = a
            gj[rsid] = b
    return gi, gj


class TestHelpers:
    def test_is_het(self) -> None:
        assert _is_het("AG") is True
        assert _is_het("AA") is False
        assert _is_het("A") is False
        assert _is_het("--") is False

    def test_hom_allele(self) -> None:
        assert _hom_allele("AA") == "A"
        assert _hom_allele("AG") is None
        assert _hom_allele("--") is None


class TestKingRobust:
    def test_duplicate_scores_half(self) -> None:
        gi, gj = _build([(1500, "AG", "AG"), (1500, "AA", "AA")])
        s = king_kinship(gi, gj)
        assert s.phi == 0.5
        assert s.relationship == "duplicate_or_mz_twin"
        assert s.n_shared == 3000

    def test_parent_offspring(self) -> None:
        # φ = 0.25 with zero opposite homozygotes → parent-offspring.
        gi, gj = _build(
            [(1000, "AG", "AG"), (1000, "AG", "AA"), (1000, "AA", "AG"), (1000, "AA", "AA")]
        )
        s = king_kinship(gi, gj)
        assert s.phi == 0.25
        assert s.ibs0 == 0
        assert s.relationship == "parent_offspring"

    def test_full_sibling(self) -> None:
        # Same 1st-degree φ band but a meaningful IBS0 fraction → full sibling.
        gi, gj = _build(
            [
                (1000, "AG", "AG"),
                (1000, "AG", "AA"),
                (1000, "AA", "AG"),
                (900, "AA", "AA"),
                (100, "AA", "GG"),  # opposite homozygotes → IBS0
            ]
        )
        s = king_kinship(gi, gj)
        assert s.ibs0 == 100
        assert 0.177 <= s.phi <= 0.354
        assert s.relationship == "full_sibling"

    def test_second_degree(self) -> None:
        # φ = 0.125, IBS0 = 0 → 2nd-degree band [0.0884, 0.177).
        gi, gj = _build([(500, "AG", "AG"), (1500, "AG", "AA"), (1500, "AA", "AG")])
        s = king_kinship(gi, gj)
        assert s.phi == 0.125
        assert s.ibs0 == 0
        assert s.relationship == "second_degree"

    def test_third_degree(self) -> None:
        # φ = 0.0625, IBS0 = 0 → 3rd-degree band [0.0442, 0.0884).
        gi, gj = _build([(250, "AG", "AG"), (1750, "AG", "AA"), (1750, "AA", "AG")])
        s = king_kinship(gi, gj)
        assert s.phi == 0.0625
        assert s.ibs0 == 0
        assert s.relationship == "third_degree"

    def test_unrelated_scores_zero(self) -> None:
        gi, gj = _build([(2000, "AG", "AA"), (2000, "AA", "AG")])
        s = king_kinship(gi, gj)
        assert s.phi == 0.0
        assert s.relationship == "unrelated"

    def test_indeterminate_when_few_shared_snps(self) -> None:
        gi, gj = _build([(MIN_SHARED_SNPS - 1, "AG", "AG")])
        s = king_kinship(gi, gj)
        assert s.n_shared < MIN_SHARED_SNPS
        assert s.relationship == "indeterminate"

    def test_only_intersecting_rsids_count(self) -> None:
        gi = {"r1": "AG", "r2": "AA", "only_i": "GG"}
        gj = {"r1": "AG", "r2": "AA", "only_j": "CC"}
        s = king_kinship(gi, gj)
        assert s.n_shared == 2  # only r1, r2 are shared

    def test_malformed_genotype_not_counted_as_ibs0(self) -> None:
        # Malformed (non-biallelic) calls must not inflate the opposite-homozygote
        # count; only the genuine AA/GG opposite homozygote (r3) is an IBS0.
        gi = {"r1": "A", "r2": "AAA", "r3": "AA"}
        gj = {"r1": "GG", "r2": "GG", "r3": "GG"}
        s = king_kinship(gi, gj)
        assert s.ibs0 == 1


class TestRelationshipBoundaries:
    def test_second_degree_boundary_is_inclusive(self) -> None:
        assert _classify(0.0884, 0.0) == "second_degree"
        assert _classify(0.0883, 0.0) == "third_degree"

    def test_third_degree_boundary_is_inclusive(self) -> None:
        assert _classify(0.0442, 0.0) == "third_degree"
        assert _classify(0.0441, 0.0) == "unrelated"


def _pair_text_for(relationship: str, phi: float) -> str:
    stats = KinshipStats(
        phi=phi,
        ibs0=0,
        ibs0_proportion=0.0,
        n_shared=3500,
        het_i=2000,
        het_j=2000,
        hethet=500,
        relationship=relationship,
    )
    pair = KinshipPair(
        other_sample_id=2,
        other_sample_name="Sample 2",
        same_vendor=True,
        stats=stats,
    )
    return _pair_text(pair)


class TestPairText:
    def test_second_degree_label_is_rendered(self) -> None:
        text = _pair_text_for("second_degree", 0.125)
        assert "2nd-degree relative" in text
        assert "grandparent" in text
        assert "half-sibling" in text
        assert "KING kinship φ=0.125" in text

    def test_third_degree_label_is_rendered(self) -> None:
        text = _pair_text_for("third_degree", 0.0625)
        assert "3rd-degree relative" in text
        assert "first cousin" in text
        assert "KING kinship φ=0.062" in text

"""Tests for the within-account KING-robust kinship module.

The KING-robust estimator is φ = (N_hethet − 2·N_ibs0) / (Het_i + Het_j). These
tests build two rsID→genotype maps with controlled genotype-pair compositions so
the resulting φ, IBS0 proportion, and relationship band are exact and
deterministic: a duplicate scores ~0.5; parent-offspring and full-sibling both
sit at ~0.25 but split on IBS0; unrelated scores ~0; and a pair with too few
shared SNPs is reported as indeterminate.
"""

from __future__ import annotations

import json

import sqlalchemy as sa

from backend.analysis.kinship import (
    CATEGORY,
    MIN_SHARED_SNPS,
    MODULE,
    KinshipPair,
    KinshipResult,
    KinshipStats,
    _classify,
    _hom_allele,
    _is_het,
    _pair_text,
    king_kinship,
    store_kinship_findings,
)
from backend.db.tables import findings


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


class TestUndefinedEstimator:
    """#2170: `n_shared` counts calls that contribute nothing to the KING denominator.

    The estimator divides by `het_i + het_j`. Identical homozygous calls satisfy
    the 2,000-SNP reportability gate without entering that denominator, so a
    comparison carrying no heterozygous information at all used to be published
    as a confident "unrelated" after an undefined ratio was forced to 0.0.
    """

    def test_zero_denominator_is_indeterminate_not_unrelated(self) -> None:
        gi, gj = _build([(MIN_SHARED_SNPS, "AA", "AA")])
        s = king_kinship(gi, gj)

        assert s.n_shared == MIN_SHARED_SNPS  # the old gate is satisfied...
        assert s.informative_denominator == 0  # ...on zero actual evidence
        assert s.relationship == "indeterminate"
        assert s.indeterminate_reason == "no_heterozygous_information"
        # phi is undefined here; 0.0 is a value, and it classified as unrelated.
        assert s.phi is None

    def test_too_few_shared_snps_reports_its_own_reason(self) -> None:
        gi, gj = _build([(10, "AG", "AG"), (10, "AA", "AA")])
        s = king_kinship(gi, gj)

        assert s.relationship == "indeterminate"
        assert s.indeterminate_reason == "insufficient_shared_snps"
        # The denominator exists here, so the two causes stay distinguishable.
        assert s.informative_denominator == 20

    def test_evaluable_unrelated_pair_is_still_unrelated(self) -> None:
        """Discriminating control: withholding must not swallow real negatives.

        Without this, returning "indeterminate" unconditionally would pass every
        other assertion in this class.
        """
        gi, gj = _build([(1000, "AG", "AA"), (1000, "AA", "AG"), (500, "AA", "GG")])
        s = king_kinship(gi, gj)

        assert s.relationship == "unrelated"
        assert s.phi is not None
        assert s.informative_denominator == 2000

    def test_shared_count_and_denominator_can_diverge(self) -> None:
        """The number a reader sees ("N shared SNPs") is not the evidence count."""
        gi, gj = _build([(MIN_SHARED_SNPS, "AA", "AA"), (1, "AG", "AG")])
        s = king_kinship(gi, gj)

        assert s.n_shared == MIN_SHARED_SNPS + 1
        assert s.informative_denominator == 2  # two orders of magnitude apart


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
    def test_duplicate_boundary_is_exclusive(self) -> None:
        assert _classify(0.3541, 0.0) == "duplicate_or_mz_twin"
        assert _classify(0.354, 0.0) == "parent_offspring"

    def test_first_degree_boundary_is_inclusive(self) -> None:
        assert _classify(0.177, 0.0) == "parent_offspring"
        assert _classify(0.177, 0.01) == "full_sibling"
        assert _classify(0.1769, 0.0) == "second_degree"

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


def _pair(name: str, relationship: str, reason: str | None = None) -> KinshipPair:
    return KinshipPair(
        other_sample_id=7,
        other_sample_name=name,
        same_vendor=True,
        stats=KinshipStats(
            phi=None if relationship == "indeterminate" else 0.01,
            ibs0=0,
            ibs0_proportion=0.0,
            n_shared=MIN_SHARED_SNPS,
            het_i=0,
            het_j=0,
            hethet=0,
            relationship=relationship,
            informative_denominator=0 if relationship == "indeterminate" else 2000,
            indeterminate_reason=reason,
        ),
    )


def _stored_text(result: KinshipResult, sample_engine: sa.Engine) -> str:
    store_kinship_findings(result, sample_engine)
    with sample_engine.connect() as conn:
        row = conn.execute(
            sa.select(findings).where(findings.c.module == MODULE, findings.c.category == CATEGORY)
        ).fetchone()
    assert row is not None
    return row.finding_text


class TestUnevaluableSummary:
    """#2170: an unevaluable comparison must not be summarised as a negative.

    `store_kinship_findings` files nothing for unrelated *or* indeterminate
    pairs, so a pair whose estimate was never defined fell into the same
    "No related samples detected" summary as a genuine negative -- false
    reassurance from a duplicate/sample-swap check.
    """

    def test_all_unevaluable_is_not_reported_as_no_related_samples(
        self, sample_engine: sa.Engine
    ) -> None:
        result = KinshipResult(
            target_sample_id=1,
            pairs=[_pair("B", "indeterminate", "no_heterozygous_information")],
            samples_compared=1,
        )

        text = _stored_text(result, sample_engine)

        assert "No related samples detected" not in text
        assert "could not be estimated" in text
        assert "not a finding of unrelatedness" in text

    def test_mixed_evaluable_and_unevaluable_counts_them_separately(
        self, sample_engine: sa.Engine
    ) -> None:
        result = KinshipResult(
            target_sample_id=1,
            pairs=[
                _pair("B", "unrelated"),
                _pair("C", "indeterminate", "insufficient_shared_snps"),
            ],
            samples_compared=2,
        )

        text = _stored_text(result, sample_engine)

        assert "1 of your 2 other local sample(s) that could be evaluated" in text
        assert "1 could not be estimated and are not counted as unrelated" in text

    def test_all_evaluable_keeps_the_plain_negative(self, sample_engine: sa.Engine) -> None:
        """Discriminating control: a real negative must still read as one."""
        result = KinshipResult(
            target_sample_id=1,
            pairs=[_pair("B", "unrelated")],
            samples_compared=1,
        )

        text = _stored_text(result, sample_engine)

        assert "No related samples detected among your 1 other local sample(s)" in text
        assert "could not be estimated" not in text

    def test_related_pair_detail_carries_the_denominator(self, sample_engine: sa.Engine) -> None:
        """The evidence count reaches the stored detail, not just the shared count."""
        pair = _pair("B", "duplicate_or_mz_twin")
        result = KinshipResult(target_sample_id=1, pairs=[pair], samples_compared=1)

        store_kinship_findings(result, sample_engine)
        with sample_engine.connect() as conn:
            row = conn.execute(
                sa.select(findings).where(
                    findings.c.module == MODULE, findings.c.category == CATEGORY
                )
            ).fetchone()
        assert row is not None
        detail = json.loads(row.detail_json)
        assert detail["informative_denominator"] == 2000
        assert detail["n_shared_snps"] == MIN_SHARED_SNPS

"""Ancestry-continuous PRS calibration (SW-B2).

Verifies the expected-PRS (ePRS) mean/variance under HWE, effect-allele alignment
to gnomAD alt-frequency, admixture-weighted AF interpolation, and the end-to-end
reference distribution built from a sample's annotated_variants + ancestry finding.
"""

from __future__ import annotations

import json
import math

import sqlalchemy as sa

from backend.analysis.prs_calibration import (
    PRS_CALIBRATION_PMIDS,
    ancestry_weighted_af,
    continuous_reference_distribution,
    effect_allele_frequency,
    expected_prs_mean_sd,
    get_ancestry_fractions,
)
from backend.db.sample_schema import create_sample_tables
from backend.db.tables import annotated_variants, findings


class TestEffectAlleleFrequency:
    def test_effect_is_alt(self) -> None:
        assert effect_allele_frequency("T", "C", "T", 0.3) == 0.3

    def test_effect_is_ref(self) -> None:
        assert effect_allele_frequency("C", "C", "T", 0.3) == 0.7

    def test_mismatch_returns_none(self) -> None:
        assert effect_allele_frequency("G", "C", "T", 0.3) is None


class TestAncestryWeightedAf:
    def test_weighted_average(self) -> None:
        per_pop = {"gnomad_af_eur": 0.2, "gnomad_af_afr": 0.6}
        af = ancestry_weighted_af(per_pop, {"EUR": 0.5, "AFR": 0.5})
        assert math.isclose(af, 0.4)

    def test_drops_pops_without_af_and_renormalizes(self) -> None:
        # MID has no gnomAD column → dropped; EUR carries full weight.
        per_pop = {"gnomad_af_eur": 0.2}
        af = ancestry_weighted_af(per_pop, {"EUR": 0.5, "MID": 0.5})
        assert math.isclose(af, 0.2)

    def test_none_when_no_af(self) -> None:
        assert ancestry_weighted_af({"gnomad_af_eur": None}, {"EUR": 1.0}) is None


class TestExpectedPrsMeanSd:
    def test_single_alt_effect_variant(self) -> None:
        variants = [
            {
                "effect_allele": "T",
                "ref": "C",
                "alt": "T",
                "weight": 1.0,
                "per_pop_alt_af": {"gnomad_af_eur": 0.3},
            }
        ]
        mean, std, n = expected_prs_mean_sd(variants, {"EUR": 1.0})
        assert math.isclose(mean, 0.6)  # 1 * 2 * 0.3
        assert math.isclose(std, math.sqrt(0.42))  # 1^2 * 2 * 0.3 * 0.7
        assert n == 1

    def test_effect_is_ref_uses_complement(self) -> None:
        variants = [
            {
                "effect_allele": "C",
                "ref": "C",
                "alt": "T",
                "weight": 1.0,
                "per_pop_alt_af": {"gnomad_af_eur": 0.3},
            }
        ]
        mean, _std, _n = expected_prs_mean_sd(variants, {"EUR": 1.0})
        assert math.isclose(mean, 1.4)  # 1 * 2 * (1 - 0.3)

    def test_skips_unusable_variants(self) -> None:
        variants = [
            {
                "effect_allele": "T",
                "ref": "C",
                "alt": "T",
                "weight": 2.0,
                "per_pop_alt_af": {"gnomad_af_eur": 0.5},
            },
            {
                "effect_allele": "G",
                "ref": "C",
                "alt": "T",
                "weight": 9.0,  # mismatch → skip
                "per_pop_alt_af": {"gnomad_af_eur": 0.5},
            },
            {
                "effect_allele": "A",
                "ref": "A",
                "alt": "G",
                "weight": 5.0,  # no AF → skip
                "per_pop_alt_af": {"gnomad_af_eur": None},
            },
        ]
        mean, _std, n = expected_prs_mean_sd(variants, {"EUR": 1.0})
        assert n == 1 and math.isclose(mean, 2.0)  # only the first

    def test_admixed_interpolation_shifts_mean(self) -> None:
        v = [
            {
                "effect_allele": "T",
                "ref": "C",
                "alt": "T",
                "weight": 1.0,
                "per_pop_alt_af": {"gnomad_af_eur": 0.1, "gnomad_af_afr": 0.9},
            }
        ]
        eur_mean = expected_prs_mean_sd(v, {"EUR": 1.0})[0]
        afr_mean = expected_prs_mean_sd(v, {"AFR": 1.0})[0]
        mix_mean = expected_prs_mean_sd(v, {"EUR": 0.5, "AFR": 0.5})[0]
        assert eur_mean < mix_mean < afr_mean  # continuous between the two


def _sample_with_ancestry(fractions: dict[str, float] | None, variants: list[dict]) -> sa.Engine:
    engine = sa.create_engine("sqlite://")
    create_sample_tables(engine)
    with engine.begin() as conn:
        if variants:
            conn.execute(annotated_variants.insert(), variants)
        if fractions is not None:
            conn.execute(
                findings.insert().values(
                    module="ancestry",
                    category="nnls_admixture",
                    finding_text="Inferred ancestry",
                    detail_json=json.dumps({"top_population": "EUR", "nnls_fractions": fractions}),
                )
            )
    return engine


class TestGetAncestryFractions:
    def test_returns_normalized_fractions(self) -> None:
        engine = _sample_with_ancestry({"EUR": 0.6, "AFR": 0.2}, [])
        fr = get_ancestry_fractions(engine)
        assert math.isclose(sum(fr.values()), 1.0)
        assert math.isclose(fr["EUR"], 0.75)  # 0.6 / 0.8

    def test_none_when_no_finding(self) -> None:
        engine = _sample_with_ancestry(None, [])
        assert get_ancestry_fractions(engine) is None


class TestContinuousReferenceDistribution:
    def _variants(self) -> list[dict]:
        return [
            {
                "rsid": "rs1",
                "chrom": "1",
                "pos": 1,
                "ref": "C",
                "alt": "T",
                "gnomad_af_eur": 0.3,
                "gnomad_af_afr": 0.5,
                "gnomad_af_amr": 0.4,
                "gnomad_af_eas": 0.2,
                "gnomad_af_sas": 0.3,
            },
            {
                "rsid": "rs2",
                "chrom": "2",
                "pos": 2,
                "ref": "A",
                "alt": "G",
                "gnomad_af_eur": 0.6,
                "gnomad_af_afr": 0.4,
                "gnomad_af_amr": 0.5,
                "gnomad_af_eas": 0.7,
                "gnomad_af_sas": 0.6,
            },
        ]

    def test_builds_calibrated_distribution(self) -> None:
        engine = _sample_with_ancestry({"EUR": 1.0}, self._variants())
        weights = [
            {"rsid": "rs1", "effect_allele": "T", "weight": 1.0},
            {"rsid": "rs2", "effect_allele": "G", "weight": -0.5},
        ]
        dist = continuous_reference_distribution(weights, engine)
        assert dist is not None
        assert dist.variants_used == 2 and dist.variants_total == 2
        # mean = 1*2*0.3 + (-0.5)*2*0.6 = 0.6 - 0.6 = 0.0 ; std > 0
        assert math.isclose(dist.mean, 0.0, abs_tol=1e-9)
        assert dist.std > 0

    def test_none_when_ancestry_unknown(self) -> None:
        engine = _sample_with_ancestry(None, self._variants())
        weights = [{"rsid": "rs1", "effect_allele": "T", "weight": 1.0}]
        assert continuous_reference_distribution(weights, engine) is None

    def test_none_when_too_few_variants_covered(self) -> None:
        # Only rs1 is annotated; weight set asks for rs1 + 3 missing → coverage 25% < 50%.
        engine = _sample_with_ancestry({"EUR": 1.0}, [self._variants()[0]])
        weights = [
            {"rsid": "rs1", "effect_allele": "T", "weight": 1.0},
            {"rsid": "rsX", "effect_allele": "A", "weight": 1.0},
            {"rsid": "rsY", "effect_allele": "A", "weight": 1.0},
            {"rsid": "rsZ", "effect_allele": "A", "weight": 1.0},
        ]
        assert continuous_reference_distribution(weights, engine) is None

    def test_admixed_distribution_differs_from_eur(self) -> None:
        weights = [{"rsid": "rs1", "effect_allele": "T", "weight": 1.0}]
        eur = continuous_reference_distribution(
            weights, _sample_with_ancestry({"EUR": 1.0}, self._variants())
        )
        afr = continuous_reference_distribution(
            weights, _sample_with_ancestry({"AFR": 1.0}, self._variants())
        )
        assert eur is not None and afr is not None
        assert eur.mean != afr.mean  # ancestry-continuous: AFR has higher rs1 alt-AF

    def test_citation_present(self) -> None:
        assert "37198491" in PRS_CALIBRATION_PMIDS

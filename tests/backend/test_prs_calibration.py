"""Ancestry-continuous PRS calibration (SW-B2).

Verifies the expected-PRS (ePRS) mean/variance under HWE, effect-allele alignment
to gnomAD alt-frequency, admixture-weighted AF interpolation, and the end-to-end
reference distribution built from a sample's annotated_variants + ancestry finding.
"""

from __future__ import annotations

import dataclasses
import json
import math
import types

import sqlalchemy as sa

from backend.analysis.prs import PRSSNPWeight, PRSWeightSet, run_prs
from backend.analysis.prs_calibration import (
    PRS_CALIBRATION_PMIDS,
    ancestry_weighted_af,
    continuous_reference_distribution,
    effect_allele_frequency,
    expected_prs_mean_sd,
    get_ancestry_fractions,
    represented_ancestry_fraction,
)
from backend.db.sample_schema import create_sample_tables
from backend.db.tables import annotated_variants, findings, imputed_variants


def _reference_with_gnomad(rows: list[dict]) -> sa.Engine:
    """In-memory reference engine with a gnomad_af table seeded with ``rows``.

    Each row needs chrom/pos/ref/alt; per-pop AF columns default to None.
    """
    from backend.annotation.gnomad import _INSERT_GNOMAD_SQL, _create_gnomad_table

    engine = sa.create_engine("sqlite://")
    _create_gnomad_table(engine)
    with engine.begin() as conn:
        for row in rows:
            params: dict = {
                "rsid": None,
                "af_global": None,
                "af_afr": None,
                "af_amr": None,
                "af_asj": None,
                "af_eas": None,
                "af_eur": None,
                "af_fin": None,
                "af_sas": None,
                "an_global": None,
                "an_afr": None,
                "an_amr": None,
                "an_asj": None,
                "an_eas": None,
                "an_eur": None,
                "an_fin": None,
                "an_sas": None,
                "homozygous_count": 0,
            }
            params.update(row)
            conn.execute(_INSERT_GNOMAD_SQL, params)
    return engine


class TestEffectAlleleFrequency:
    def test_effect_is_alt(self) -> None:
        assert effect_allele_frequency("T", "C", "T", 0.3) == 0.3

    def test_effect_is_ref(self) -> None:
        assert effect_allele_frequency("C", "C", "T", 0.3) == 0.7

    def test_mismatch_returns_none(self) -> None:
        assert effect_allele_frequency("G", "C", "T", 0.3) is None

    def test_reverse_strand_effect_uses_complemented_ref_frequency(self) -> None:
        # Weight pair C/T is the reverse-strand frame for reference pair G/A.
        assert effect_allele_frequency("C", "G", "A", 0.25, other_allele="T") == 0.75

    def test_reverse_strand_effect_uses_complemented_alt_frequency(self) -> None:
        # Weight pair C/T is the reverse-strand frame for reference pair A/G.
        assert effect_allele_frequency("C", "A", "G", 0.25, other_allele="T") == 0.25

    def test_legacy_without_other_allele_still_does_not_flip(self) -> None:
        assert effect_allele_frequency("C", "G", "A", 0.25) is None

    def test_malformed_other_allele_does_not_fall_back_to_legacy(self) -> None:
        assert effect_allele_frequency("A", "A", "G", 0.25, other_allele="I") is None

    def test_palindromic_near_half_with_other_allele_is_dropped(self) -> None:
        assert effect_allele_frequency("A", "A", "T", 0.5, other_allele="T") is None


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


class TestRepresentedAncestryFraction:
    def test_counts_only_populations_with_gnomad_columns(self) -> None:
        assert math.isclose(
            represented_ancestry_fraction({"EUR": 0.3, "MID": 0.6, "OCE": 0.1}),
            0.3,
        )

    def test_counts_represented_admixture_components(self) -> None:
        assert math.isclose(
            represented_ancestry_fraction({"AFR": 0.2, "EAS": 0.3, "OCE": 0.5}),
            0.5,
        )


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

    def test_reverse_strand_effect_allele_contributes(self) -> None:
        variants = [
            {
                "effect_allele": "C",
                "other_allele": "T",
                "ref": "G",
                "alt": "A",
                "weight": 1.0,
                "per_pop_alt_af": {"gnomad_af_eur": 0.25},
            }
        ]
        mean, std, n = expected_prs_mean_sd(variants, {"EUR": 1.0})
        assert math.isclose(mean, 1.5)  # complemented effect C maps to ref G: p = 0.75
        assert math.isclose(std, math.sqrt(0.375))
        assert n == 1

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

    def test_builds_positional_calibrated_distribution(self) -> None:
        engine = _sample_with_ancestry({"EUR": 1.0}, self._variants())
        weights = [
            {"rsid": "", "chrom": "chr1", "pos": 1, "effect_allele": "T", "weight": 1.0},
            {"chrom": "2", "pos": 2, "effect_allele": "G", "weight": -0.5},
        ]
        dist = continuous_reference_distribution(weights, engine)
        assert dist is not None
        assert dist.variants_used == 2 and dist.variants_total == 2
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

    def test_none_when_mid_dominates_ancestry_coverage(self) -> None:
        # MID has no gnomAD AF column. With only 30% represented ancestry, the
        # old renormalization produced the same distribution as a 100% EUR sample.
        engine = _sample_with_ancestry({"MID": 0.7, "EUR": 0.3}, self._variants())
        weights = [
            {"rsid": "rs1", "effect_allele": "T", "weight": 1.0},
            {"rsid": "rs2", "effect_allele": "G", "weight": -0.5},
        ]
        assert continuous_reference_distribution(weights, engine) is None

    def test_none_when_oce_dominates_ancestry_coverage(self) -> None:
        # OCE has no gnomAD AF column. With 40% represented ancestry, emitting a
        # percentile would standardize against the minority EUR fraction only.
        engine = _sample_with_ancestry({"OCE": 0.6, "EUR": 0.4}, self._variants())
        weights = [
            {"rsid": "rs1", "effect_allele": "T", "weight": 1.0},
            {"rsid": "rs2", "effect_allele": "G", "weight": -0.5},
        ]
        assert continuous_reference_distribution(weights, engine) is None

    def test_half_represented_ancestry_still_calibrates(self) -> None:
        engine = _sample_with_ancestry({"MID": 0.5, "EUR": 0.5}, self._variants())
        weights = [
            {"rsid": "rs1", "effect_allele": "T", "weight": 1.0},
            {"rsid": "rs2", "effect_allele": "G", "weight": -0.5},
        ]
        dist = continuous_reference_distribution(weights, engine)
        assert dist is not None
        assert dist.variants_used == 2

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

    def test_reverse_strand_effect_allele_contributes_to_distribution(self) -> None:
        variants = [
            {
                "rsid": "rsFLIP",
                "chrom": "1",
                "pos": 3,
                "ref": "G",
                "alt": "A",
                "gnomad_af_eur": 0.25,
                "gnomad_af_afr": 0.25,
                "gnomad_af_amr": 0.25,
                "gnomad_af_eas": 0.25,
                "gnomad_af_sas": 0.25,
            }
        ]
        weights = [
            {
                "rsid": "rsFLIP",
                "effect_allele": "C",
                "other_allele": "T",
                "weight": 1.0,
            }
        ]
        dist = continuous_reference_distribution(
            weights, _sample_with_ancestry({"EUR": 1.0}, variants)
        )

        assert dist is not None
        assert dist.variants_used == 1
        assert dist.variants_total == 1
        assert math.isclose(dist.mean, 1.5)
        assert math.isclose(dist.std, round(math.sqrt(0.375), 6))

    def test_citation_present(self) -> None:
        assert "37198491" in PRS_CALIBRATION_PMIDS


class TestRunPrsContinuousCalibrationPreservesOtherAllele:
    """run_prs() must carry ``other_allele`` into continuous calibration (#1179).

    The raw-score path (compute_prs) harmonizes strand using the weight's
    ``other_allele``; the ancestry-continuous calibration path consumes the same
    field. run_prs() rebuilt the calibration weight dicts and previously dropped
    ``other_allele``, so a reverse-strand non-palindromic variant could be scored
    in the raw sum yet excluded from the expected mean/variance — biasing the
    percentile or withholding calibration by shrinking usable coverage. This pins
    the caller end-to-end: the same oriented allele must reach both paths.
    """

    def _reverse_strand_engine(self) -> sa.Engine:
        # Sample carries the variant on the G/A strand (genotype GG = two copies
        # of complement(C)=G); the weight's effect/other is the reverse-strand
        # C/T pair. EUR alt-AF 0.25 → effect-allele freq 0.75 → mean 1*2*0.75.
        return _sample_with_ancestry(
            {"EUR": 1.0},
            [
                {
                    "rsid": "rsFLIP",
                    "chrom": "1",
                    "pos": 3,
                    "ref": "G",
                    "alt": "A",
                    "genotype": "GG",
                    "gnomad_af_global": 0.25,
                    "gnomad_af_eur": 0.25,
                }
            ],
        )

    def _weight_set(self, *, other_allele: str | None) -> PRSWeightSet:
        return PRSWeightSet(
            name="Reverse-strand regression",
            trait="regression_trait",
            module="traits",
            source_ancestry="EUR",
            source_study="test",
            source_pmid="0",
            sample_size=1,
            weights=[
                PRSSNPWeight(
                    rsid="rsFLIP",
                    effect_allele="C",
                    other_allele=other_allele,
                    weight=1.0,
                )
            ],
            reference_mean=0.0,
            reference_std=0.0,
            calibrated=False,  # force the ancestry-continuous calibration path
        )

    def test_reverse_strand_variant_reaches_continuous_calibration(self) -> None:
        engine = self._reverse_strand_engine()
        result = run_prs(self._weight_set(other_allele="T"), engine)

        # The raw-score path scores the reverse-strand variant ...
        assert result.snps_used == 1
        # ... and continuous calibration must include the SAME variant, not drop
        # it for lack of other_allele (the #1179 regression).
        assert result.calibration_method == "ancestry_continuous"
        assert result.calibration_variants_used == 1
        assert result.calibration_variants_total == 1
        assert math.isclose(result.calibration_reference_mean, 1.5)
        assert math.isclose(result.calibration_reference_std, round(math.sqrt(0.375), 6))

    def test_dropping_other_allele_would_withhold_calibration(self) -> None:
        # Control: with other_allele absent the reverse-strand variant cannot be
        # oriented against gnomAD ref/alt, so calibration is correctly withheld.
        # This is exactly the silent state the bug produced for PGS weights that
        # DO carry other_allele, demonstrating why preserving it matters.
        engine = self._reverse_strand_engine()
        result = run_prs(self._weight_set(other_allele=None), engine)

        assert result.snps_used == 1  # raw score still computed
        assert result.calibration_method is None
        assert result.calibration_variants_used is None
        assert result.percentile is None


class TestRunPrsCalibrationProjectionLocksAllFields:
    """run_prs() derives the calibration weight dicts from the dataclass (#1209).

    The #1179 defect was a hand-built dict that forgot a ``PRSSNPWeight`` field
    (``other_allele``). Deriving the projection from the dataclass means any future
    field flows through automatically. This locks that the dicts handed to
    ``continuous_reference_distribution()`` expose the FULL ``PRSSNPWeight`` field
    set — a re-introduced hand-built dict that omits a field fails here, catching
    the drift class before it can silently bias a percentile again.
    """

    def test_projection_carries_every_weight_field(self, monkeypatch) -> None:
        import backend.analysis.prs as prs_module

        captured: dict[str, list[dict]] = {}

        def _capture(weights: list[dict], sample_engine, reference_engine=None):
            captured["weights"] = weights
            return None  # withhold calibration; we only inspect the projection

        monkeypatch.setattr(prs_module, "continuous_reference_distribution", _capture)

        # rs1 must be SCORED so it survives the scored-subset filter (#1210) and
        # still reaches the projection whose field completeness we lock here. The
        # A/G genotype is non-palindromic → matched_ref; calibration itself is
        # mocked, so a minimal matched genotype is all that is needed.
        engine = _sample_with_ancestry(
            None,
            [
                {
                    "rsid": "rs1",
                    "chrom": "1",
                    "pos": 100,
                    "ref": "G",
                    "alt": "A",
                    "genotype": "AG",
                    "gnomad_af_global": 0.3,
                    "annotation_coverage": 4,
                }
            ],
        )
        weight_set = PRSWeightSet(
            name="field-set lock",
            trait="t",
            module="traits",
            source_ancestry="EUR",
            source_study="s",
            source_pmid="0",
            sample_size=1,
            weights=[
                PRSSNPWeight(
                    rsid="rs1",
                    effect_allele="A",
                    other_allele="G",
                    weight=0.5,
                    chrom="1",
                    pos=100,
                )
            ],
            reference_mean=0.0,
            reference_std=0.0,
            calibrated=False,  # take the ancestry-continuous path
        )
        run_prs(weight_set, engine)

        assert captured.get("weights"), "continuous_reference_distribution was not called"
        expected_fields = {f.name for f in dataclasses.fields(PRSSNPWeight)}
        assert "other_allele" in expected_fields  # the field whose omission was #1179
        for projected in captured["weights"]:
            missing = expected_fields - set(projected)
            assert not missing, f"projection dropped PRSSNPWeight fields: {missing}"
        # Values round-trip from the dataclass, not just the keys.
        wd = captured["weights"][0]
        assert wd["rsid"] == "rs1"
        assert wd["effect_allele"] == "A"
        assert wd["other_allele"] == "G"
        assert wd["weight"] == 0.5
        assert wd["chrom"] == "1"
        assert wd["pos"] == 100


class TestImputedCalibrationViaReference:
    """Imputed-scored variants must enter the calibration distribution, not just the
    raw score (#1236). They live in imputed_variants, not annotated_variants, so a
    reference engine is needed to source their per-pop gnomAD AF; without one (or
    without a gnomAD row) the variant stays uncalibrated rather than biasing the
    moments over a typed-only set.
    """

    def _imputed_sample(self) -> sa.Engine:
        # Ancestry finding (EUR) + an imputed-only variant at 1:999 (NOT in
        # annotated_variants), so the calibration must resolve it via the reference.
        engine = _sample_with_ancestry({"EUR": 1.0}, [])
        with engine.begin() as conn:
            conn.execute(
                imputed_variants.insert().values(
                    chrom="1", pos=999, ref="A", alt="G", dr2=0.95, af=0.3, dosage=1.0
                )
            )
        return engine

    def _weights(self) -> list[dict]:
        # effect == alt (G) so the EUR effect-allele freq is the alt AF directly.
        return [
            {
                "rsid": "",
                "chrom": "1",
                "pos": 999,
                "effect_allele": "G",
                "other_allele": "A",
                "weight": 1.0,
            }
        ]

    def test_imputed_variant_enters_distribution_via_reference(self) -> None:
        sample = self._imputed_sample()
        reference = _reference_with_gnomad(
            [{"chrom": "1", "pos": 999, "ref": "A", "alt": "G", "af_eur": 0.3}]
        )
        dist = continuous_reference_distribution(self._weights(), sample, reference)
        assert dist is not None
        assert dist.variants_used == 1 and dist.variants_total == 1
        # effect=alt=G, EUR alt-AF 0.3 → p=0.3 → mean = 1*2*0.3 = 0.6.
        assert math.isclose(dist.mean, 0.6, abs_tol=1e-6)

    def test_without_reference_imputed_variant_is_not_calibrated(self) -> None:
        # No reference engine → the imputed-only variant cannot be sourced; as the
        # only carried variant the distribution is empty and is withheld (not
        # standardized over a typed-only / empty set).
        sample = self._imputed_sample()
        assert continuous_reference_distribution(self._weights(), sample, None) is None

    def test_reference_without_matching_gnomad_row_leaves_it_uncalibrated(self) -> None:
        # Reference engine present but gnomAD has no row at 1:999 A>G → unresolved.
        sample = self._imputed_sample()
        reference = _reference_with_gnomad([])
        assert continuous_reference_distribution(self._weights(), sample, reference) is None

    def _mixed_sample(self) -> sa.Engine:
        # A typed variant (rsTYP, genotype TT, in annotated_variants WITH gnomAD AF)
        # that calibrates on its own, PLUS the imputed-only variant at 1:999. The
        # typed variant alone clears the 50% coverage gate, so without the #1236
        # guard the distribution would be built over typed-only while the raw score
        # also includes the imputed contribution — the exact bias.
        engine = _sample_with_ancestry(
            {"EUR": 1.0},
            [
                {
                    "rsid": "rsTYP",
                    "chrom": "2",
                    "pos": 500,
                    "ref": "C",
                    "alt": "T",
                    "genotype": "TT",
                    "gnomad_af_global": 0.4,
                    "gnomad_af_eur": 0.4,
                }
            ],
        )
        with engine.begin() as conn:
            conn.execute(
                imputed_variants.insert().values(
                    chrom="1", pos=999, ref="A", alt="G", dr2=0.95, af=0.3, dosage=1.0
                )
            )
        return engine

    def _mixed_weight_set(self) -> PRSWeightSet:
        return PRSWeightSet(
            name="mixed regression",
            trait="mixed_trait",
            module="traits",
            source_ancestry="EUR",
            source_study="s",
            source_pmid="0",
            sample_size=1,
            weights=[
                PRSSNPWeight(
                    rsid="rsTYP",
                    effect_allele="T",
                    other_allele="C",
                    weight=1.0,
                    chrom="2",
                    pos=500,
                ),
                PRSSNPWeight(
                    rsid="", effect_allele="G", other_allele="A", weight=1.0, chrom="1", pos=999
                ),
            ],
            reference_mean=0.0,
            reference_std=0.0,
            calibrated=False,
        )

    def test_run_prs_withholds_percentile_when_imputed_uncalibrated(self) -> None:
        # End-to-end safety net: the typed variant alone would clear the coverage
        # gate and yield a (biased) distribution, but the imputed contribution in
        # the raw score has no reference engine to calibrate it → withhold rather
        # than standardize against typed-only moments (#1236).
        result = run_prs(self._mixed_weight_set(), self._mixed_sample())  # no reference_engine
        assert result.snps_used == 2
        assert result.snps_used_imputed == 1
        assert result.calibration_method is None
        assert result.percentile is None

    def test_run_prs_calibrates_imputed_when_reference_supplied(self) -> None:
        # Preserve path: with a reference engine sourcing the imputed variant's
        # per-pop gnomAD AF, BOTH the typed and imputed contributions enter the
        # distribution and the percentile is emitted over the same set as the raw.
        reference = _reference_with_gnomad(
            [{"chrom": "1", "pos": 999, "ref": "A", "alt": "G", "af_eur": 0.3}]
        )
        result = run_prs(
            self._mixed_weight_set(), self._mixed_sample(), reference_engine=reference
        )
        assert result.snps_used_imputed == 1
        assert result.calibration_method == "ancestry_continuous"
        assert result.calibration_variants_used == 2  # typed + imputed both calibrated
        assert result.percentile is not None

    def test_withholds_when_imputed_unresolved_despite_typed_coverage(self) -> None:
        # Partial-resolution guard: the typed variant alone clears the 50% coverage
        # gate, but if the reference can't resolve the imputed variant (no gnomAD
        # row), the distribution would omit a scored contribution that IS in the raw
        # score — so the whole distribution is withheld, not calibrated over
        # typed-only moments (#1236).
        sample = self._mixed_sample()
        reference = _reference_with_gnomad([])  # no gnomAD row for the imputed 1:999
        weights = [
            {
                "rsid": "rsTYP",
                "chrom": "2",
                "pos": 500,
                "effect_allele": "T",
                "other_allele": "C",
                "weight": 1.0,
            },
            {
                "rsid": "",
                "chrom": "1",
                "pos": 999,
                "effect_allele": "G",
                "other_allele": "A",
                "weight": 1.0,
            },
        ]
        assert continuous_reference_distribution(weights, sample, reference) is None

    def test_run_cancer_prs_threads_reference_engine_to_preserve_percentile(self) -> None:
        # #1281: a PRS module entry point must forward reference_engine to run_prs so
        # an imputed-contributed score keeps a calibrated percentile (the "preserve"
        # counterpart to the run_prs-level tests above) instead of withholding it.
        # run_cancer_prs is the representative module; mixed_trait is not sex-gated,
        # so it is not skipped.
        from backend.analysis.cancer_prs import run_cancer_prs

        # Without a reference engine the imputed contribution can't be sourced →
        # percentile withheld.
        withheld = run_cancer_prs([self._mixed_weight_set()], self._mixed_sample()).results[0]
        assert withheld.snps_used_imputed == 1
        assert withheld.percentile is None

        # With a reference engine threaded through, the imputed variant is calibrated
        # and the percentile is emitted over the same variant set as the raw score.
        reference = _reference_with_gnomad(
            [{"chrom": "1", "pos": 999, "ref": "A", "alt": "G", "af_eur": 0.3}]
        )
        preserved = run_cancer_prs(
            [self._mixed_weight_set()], self._mixed_sample(), reference_engine=reference
        ).results[0]
        assert preserved.calibration_method == "ancestry_continuous"
        assert preserved.calibration_variants_used == 2  # typed + imputed both calibrated
        assert preserved.percentile is not None


class TestReferenceEngineThreading:
    """Every PRS entry point must forward ``reference_engine`` down to ``run_prs`` so
    imputed-contributed percentiles are preserved, not withheld (#1281).

    These lock the one-line wiring at each entry point with a spy; the end-to-end
    preserve behaviour itself is covered by TestImputedCalibrationViaReference.
    """

    def test_run_traits_prs_forwards_reference_engine(self, monkeypatch) -> None:
        import backend.analysis.traits as t

        captured: dict = {}

        def _spy(*_a, **kw):
            captured["reference_engine"] = kw.get("reference_engine")
            return types.SimpleNamespace(
                evidence_level=1,
                trait="t",
                percentile=None,
                is_sufficient=True,
                snps_used=0,
                snps_total=0,
            )

        monkeypatch.setattr(t, "_load_prs_weight_sets", lambda _panel: [object()])
        monkeypatch.setattr(t, "get_inferred_ancestry", lambda _e: None)
        monkeypatch.setattr(t, "get_top_ancestry_fraction", lambda _e: None)
        monkeypatch.setattr(t, "cap_evidence_level", lambda lvl, _cap: lvl)
        monkeypatch.setattr(t, "run_prs", _spy)

        sentinel = object()
        t._run_traits_prs(types.SimpleNamespace(evidence_cap=4), None, reference_engine=sentinel)
        assert captured["reference_engine"] is sentinel

    def test_score_traits_pathways_forwards_reference_engine(self, monkeypatch) -> None:
        # The outer hop the issue overlooked: score_traits_pathways already receives
        # reference_engine (for gwas_matched_rsids) but must also pass it to the PRS.
        import backend.analysis.traits as t
        from backend.analysis.traits import load_traits_panel, score_traits_pathways

        captured: dict = {}
        monkeypatch.setattr(
            t,
            "_run_traits_prs",
            lambda *_a, **kw: captured.update(reference_engine=kw.get("reference_engine")) or [],
        )
        monkeypatch.setattr(t, "_fetch_genotypes", lambda _r, _e: {})
        monkeypatch.setattr(t, "gwas_matched_rsids", lambda _r, _e: [])

        sentinel = object()
        score_traits_pathways(load_traits_panel(), None, sentinel)
        assert captured["reference_engine"] is sentinel

    def test_run_metabolic_prs_forwards_reference_engine(self, monkeypatch) -> None:
        import backend.analysis.metabolic_prs as m

        captured: dict = {}

        def _spy(*_a, **kw):
            captured["reference_engine"] = kw.get("reference_engine")
            return types.SimpleNamespace(
                pgs_id="x", coverage_fraction=0.0, snps_used=0, snps_total=0
            )

        monkeypatch.setattr(m, "score_anchor_snps", lambda _e, _t: [])
        monkeypatch.setattr(m, "load_pgs_registry", lambda: None)
        monkeypatch.setattr(m, "build_trait_weight_set", lambda *_a, **_k: object())
        monkeypatch.setattr(m, "run_prs", _spy)

        sentinel = object()
        m.run_metabolic_prs(None, object(), reference_engine=sentinel)
        assert captured["reference_engine"] is sentinel

    def test_score_ebmd_prs_forwards_reference_engine(self, monkeypatch) -> None:
        import backend.analysis.ebmd_prs as e

        captured: dict = {}
        monkeypatch.setattr(e, "load_pgs_registry", lambda: None)
        monkeypatch.setattr(e, "build_trait_weight_set", lambda *_a, **_k: object())
        monkeypatch.setattr(
            e,
            "run_prs",
            lambda *_a, **kw: captured.update(reference_engine=kw.get("reference_engine")),
        )

        sentinel = object()
        e.score_ebmd_prs(None, object(), reference_engine=sentinel)
        assert captured["reference_engine"] is sentinel

    def test_assess_fh_forwards_reference_engine_to_run_prs(self, monkeypatch) -> None:
        # Two-hop: assess_fh → score_ldl_prs → run_prs.
        import backend.analysis.fh as f

        captured: dict = {}
        monkeypatch.setattr(f, "detect_fh_monogenic", lambda _e: None)
        monkeypatch.setattr(f, "detect_apob_fdb", lambda _e: None)
        monkeypatch.setattr(f, "load_pgs_registry", lambda: None)
        monkeypatch.setattr(f, "build_trait_weight_set", lambda *_a, **_k: object())
        monkeypatch.setattr(
            f,
            "run_prs",
            lambda *_a, **kw: captured.update(reference_engine=kw.get("reference_engine")),
        )

        sentinel = object()
        f.assess_fh(None, object(), reference_engine=sentinel)
        assert captured["reference_engine"] is sentinel

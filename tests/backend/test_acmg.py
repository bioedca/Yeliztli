"""Unit tests for the DRAFT ACMG/AMP engine (SW-F1 / #13)."""

from __future__ import annotations

import pytest

from backend.analysis.acmg import (
    BA1_AF_MIN,
    BENIGN,
    BS1_AF_MIN,
    LIKELY_BENIGN,
    LIKELY_PATHOGENIC,
    PATHOGENIC,
    PM2_AF_MAX,
    UNCERTAIN,
    AcmgEvidence,
    classify_acmg,
    classify_points,
    criterion_ba1,
    criterion_bp7,
    criterion_bs1,
    criterion_pm2,
    criterion_pm4,
    criterion_pp2,
    criterion_pp3_bp4,
    criterion_pvs1,
)


class TestClassifyPoints:
    @pytest.mark.parametrize(
        "points,expected",
        [
            (10, PATHOGENIC),
            (15, PATHOGENIC),
            (9, LIKELY_PATHOGENIC),
            (6, LIKELY_PATHOGENIC),
            (5, UNCERTAIN),
            (0, UNCERTAIN),
            (-1, LIKELY_BENIGN),
            (-6, LIKELY_BENIGN),
            (-7, BENIGN),
            (-20, BENIGN),
        ],
    )
    def test_tavtigian_thresholds(self, points: int, expected: str) -> None:
        assert classify_points(points) == expected

    def test_standalone_benign_forces_benign(self) -> None:
        # BA1 is stand-alone benign regardless of positive points.
        assert classify_points(8, standalone_benign=True) == BENIGN


class TestPVS1:
    def _ev(self, consequence: str, *, lof: bool = True) -> AcmgEvidence:
        return AcmgEvidence(gene_symbol="G", consequence=consequence, gene_lof_mechanism=lof)

    def test_nonsense_very_strong(self) -> None:
        c = criterion_pvs1(self._ev("stop_gained"))
        assert c is not None and c.strength == "Very Strong" and c.points == 8

    def test_frameshift_very_strong(self) -> None:
        c = criterion_pvs1(self._ev("frameshift_variant"))
        assert c.points == 8

    def test_canonical_splice_strong(self) -> None:
        c = criterion_pvs1(self._ev("splice_donor_variant"))
        assert c.strength == "Strong" and c.points == 4

    def test_start_loss_moderate(self) -> None:
        c = criterion_pvs1(self._ev("start_lost"))
        assert c.strength == "Moderate" and c.points == 2

    def test_not_applied_when_gene_not_lof_mechanism(self) -> None:
        assert criterion_pvs1(self._ev("stop_gained", lof=False)) is None

    def test_not_applied_for_missense(self) -> None:
        assert criterion_pvs1(self._ev("missense_variant")) is None


class TestOtherCriteria:
    def test_frequency_threshold_constants_match_documented_defaults(self) -> None:
        assert PM2_AF_MAX == pytest.approx(1e-4)
        assert BS1_AF_MIN == pytest.approx(0.01)
        assert BA1_AF_MIN == pytest.approx(0.05)

    def test_pm2_missing_frequency_data_is_neutral(self) -> None:
        assert criterion_pm2(AcmgEvidence(gnomad_af_popmax=None, gnomad_af_global=None)) is None

    def test_pm2_very_rare(self) -> None:
        assert criterion_pm2(AcmgEvidence(gnomad_af_popmax=1e-5)).points == 1

    def test_pm2_not_applied_when_not_rare(self) -> None:
        assert criterion_pm2(AcmgEvidence(gnomad_af_popmax=0.005)) is None

    def test_pm2_boundary_is_strictly_below_cutoff(self) -> None:
        eps = PM2_AF_MAX / 10
        just_below = criterion_pm2(AcmgEvidence(gnomad_af_popmax=PM2_AF_MAX - eps))
        assert just_below is not None and just_below.points == 1
        assert criterion_pm2(AcmgEvidence(gnomad_af_popmax=PM2_AF_MAX)) is None
        assert criterion_pm2(AcmgEvidence(gnomad_af_popmax=PM2_AF_MAX + eps)) is None
        assert criterion_pm2(AcmgEvidence(gnomad_af_popmax=5e-4)) is None

    def test_pm4_inframe(self) -> None:
        assert criterion_pm4(AcmgEvidence(consequence="inframe_deletion")).points == 2

    def test_pm4_stop_loss(self) -> None:
        assert criterion_pm4(AcmgEvidence(consequence="stop_lost")).points == 2

    def test_pp2_missense_constrained_with_curated_missense_mechanism(self) -> None:
        c = criterion_pp2(
            AcmgEvidence(
                consequence="missense_variant",
                gene_missense_z=3.5,
                gene_missense_pathogenic_mechanism=True,
            )
        )
        assert c is not None and c.points == 1

    def test_pp2_not_applied_from_constraint_alone(self) -> None:
        assert (
            criterion_pp2(AcmgEvidence(consequence="missense_variant", gene_missense_z=3.5))
            is None
        )

    def test_pp2_not_applied_when_missense_not_disease_mechanism(self) -> None:
        assert (
            criterion_pp2(
                AcmgEvidence(
                    consequence="missense_variant",
                    gene_missense_z=3.5,
                    gene_missense_pathogenic_mechanism=False,
                )
            )
            is None
        )

    def test_pp2_not_applied_unconstrained(self) -> None:
        assert (
            criterion_pp2(
                AcmgEvidence(
                    consequence="missense_variant",
                    gene_missense_z=2.0,
                    gene_missense_pathogenic_mechanism=True,
                )
            )
            is None
        )

    def test_pp2_not_applied_non_missense(self) -> None:
        assert (
            criterion_pp2(
                AcmgEvidence(
                    consequence="stop_gained",
                    gene_missense_z=5.0,
                    gene_missense_pathogenic_mechanism=True,
                )
            )
            is None
        )

    def test_pp3_strong_revel(self) -> None:
        c = criterion_pp3_bp4(AcmgEvidence(consequence="missense_variant", revel=0.95))
        assert c.code == "PP3" and c.direction == "pathogenic" and c.points == 4

    def test_bp4_benign_revel(self) -> None:
        c = criterion_pp3_bp4(AcmgEvidence(consequence="missense_variant", revel=0.01))
        assert c.code == "BP4" and c.direction == "benign" and c.points == -4

    def test_pp3_indeterminate_revel(self) -> None:
        assert criterion_pp3_bp4(AcmgEvidence(consequence="missense_variant", revel=0.5)) is None

    def test_ba1_common(self) -> None:
        c = criterion_ba1(AcmgEvidence(gnomad_af_popmax=0.06))
        assert c is not None and c.strength == "Standalone" and c.points == -8

    def test_ba1_not_applied_below_5pct(self) -> None:
        assert criterion_ba1(AcmgEvidence(gnomad_af_popmax=0.04)) is None

    def test_ba1_boundary_is_strictly_above_cutoff(self) -> None:
        eps = BA1_AF_MIN / 100
        just_above = criterion_ba1(AcmgEvidence(gnomad_af_popmax=BA1_AF_MIN + eps))
        assert just_above is not None and just_above.strength == "Standalone"
        assert criterion_ba1(AcmgEvidence(gnomad_af_popmax=BA1_AF_MIN)) is None
        assert criterion_ba1(AcmgEvidence(gnomad_af_popmax=BA1_AF_MIN - eps)) is None

    def test_bs1_above_1pct(self) -> None:
        assert criterion_bs1(AcmgEvidence(gnomad_af_popmax=0.02)).points == -4

    def test_bs1_not_applied_in_ba1_range(self) -> None:
        assert criterion_bs1(AcmgEvidence(gnomad_af_popmax=0.06)) is None

    def test_bs1_boundary_is_above_one_pct_through_ba1_cutoff(self) -> None:
        lower_eps = BS1_AF_MIN / 10
        upper_eps = BA1_AF_MIN / 100
        just_above_lower = criterion_bs1(AcmgEvidence(gnomad_af_popmax=BS1_AF_MIN + lower_eps))
        at_upper = criterion_bs1(AcmgEvidence(gnomad_af_popmax=BA1_AF_MIN))
        assert just_above_lower is not None and just_above_lower.points == -4
        assert criterion_bs1(AcmgEvidence(gnomad_af_popmax=BS1_AF_MIN)) is None
        assert at_upper is not None and at_upper.points == -4
        assert criterion_bs1(AcmgEvidence(gnomad_af_popmax=BA1_AF_MIN + upper_eps)) is None

    def test_bp7_synonymous(self) -> None:
        assert criterion_bp7(AcmgEvidence(consequence="synonymous_variant")).points == -1

    def test_bp7_not_applied_near_splice(self) -> None:
        ev = AcmgEvidence(consequence="synonymous_variant&splice_region_variant")
        assert criterion_bp7(ev) is None


class TestClassifyAcmg:
    def test_lof_plus_rare_is_likely_pathogenic(self) -> None:
        # PVS1 (Very Strong, +8) + PM2 (+1) = 9 → Likely pathogenic.
        ev = AcmgEvidence(
            gene_symbol="G",
            consequence="stop_gained",
            gnomad_af_popmax=1e-5,
            gene_lof_mechanism=True,
        )
        result = classify_acmg(ev)
        assert result.points == 9
        assert result.classification == LIKELY_PATHOGENIC
        assert {c.code for c in result.criteria} == {"PVS1", "PM2"}
        assert result.is_draft is True

    def test_lof_missing_frequency_data_does_not_get_pm2(self) -> None:
        ev = AcmgEvidence(
            gene_symbol="G",
            consequence="stop_gained",
            gnomad_af_popmax=None,
            gene_lof_mechanism=True,
        )
        result = classify_acmg(ev)
        assert result.points == 8
        assert result.classification == LIKELY_PATHOGENIC
        assert {c.code for c in result.criteria} == {"PVS1"}

    def test_high_revel_missense_constraint_alone_remains_vus(self) -> None:
        # PP3 Strong (+4) + PM2 (+1) = 5 → VUS. PP2 needs a curated
        # pathogenic-missense disease-mechanism signal, not constraint alone.
        ev = AcmgEvidence(
            gene_symbol="G",
            consequence="missense_variant",
            revel=0.95,
            gene_missense_z=3.5,
            gnomad_af_popmax=1e-5,
        )
        result = classify_acmg(ev)
        assert result.points == 5
        assert result.classification == UNCERTAIN
        assert {c.code for c in result.criteria} == {"PP3", "PM2"}
        assert "PP2" in result.unassessable

    def test_high_revel_missense_missing_frequency_data_stays_uncertain(self) -> None:
        ev = AcmgEvidence(
            gene_symbol="G",
            consequence="missense_variant",
            revel=0.95,
            gene_missense_z=3.5,
            gnomad_af_popmax=None,
        )
        result = classify_acmg(ev)
        assert result.points == 4
        assert result.classification == UNCERTAIN
        assert {c.code for c in result.criteria} == {"PP3"}
        assert "PP2" in result.unassessable

    def test_high_revel_curated_missense_mechanism_rare_lp(self) -> None:
        # PP3 Strong (+4) + PP2 (+1) + PM2 (+1) = 6 → Likely pathogenic.
        ev = AcmgEvidence(
            gene_symbol="G",
            consequence="missense_variant",
            revel=0.95,
            gene_missense_z=3.5,
            gene_missense_pathogenic_mechanism=True,
            gnomad_af_popmax=1e-5,
        )
        result = classify_acmg(ev)
        assert result.points == 6
        assert result.classification == LIKELY_PATHOGENIC
        assert {c.code for c in result.criteria} == {"PP3", "PP2", "PM2"}

    def test_common_variant_is_standalone_benign(self) -> None:
        ev = AcmgEvidence(gene_symbol="G", consequence="missense_variant", gnomad_af_popmax=0.06)
        result = classify_acmg(ev)
        assert result.classification == BENIGN
        assert any(c.code == "BA1" for c in result.criteria)

    def test_moderately_common_is_likely_benign(self) -> None:
        # BS1 (-4) → Likely benign.
        ev = AcmgEvidence(gene_symbol="G", consequence="missense_variant", gnomad_af_popmax=0.02)
        result = classify_acmg(ev)
        assert result.classification == LIKELY_BENIGN

    def test_nothing_applies_is_uncertain(self) -> None:
        ev = AcmgEvidence(gene_symbol="G", consequence="missense_variant", gnomad_af_popmax=0.005)
        result = classify_acmg(ev)
        assert result.classification == UNCERTAIN
        assert result.criteria == []

    def test_pm3_is_listed_unassessable(self) -> None:
        result = classify_acmg(AcmgEvidence(gene_symbol="G", consequence="missense_variant"))
        assert "PM3" in result.unassessable
        assert "PP5" in result.unassessable  # withdrawn criteria flagged too

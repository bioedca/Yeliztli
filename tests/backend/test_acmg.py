"""Unit tests for the DRAFT ACMG/AMP engine (SW-F1 / #13)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
import sqlalchemy as sa

from backend.analysis.acmg import (
    BA1_AF_MIN,
    BA1_MIN_OBSERVED_ALLELES,
    BENIGN,
    BS1_AF_MIN,
    LIKELY_BENIGN,
    LIKELY_PATHOGENIC,
    PATHOGENIC,
    PM2_AF_MAX,
    UNCERTAIN,
    AcmgEvidence,
    assess_sample_acmg,
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
from backend.db.sample_schema import create_sample_tables
from backend.db.tables import annotated_variants, reference_metadata

_REPO_ROOT = Path(__file__).resolve().parents[2]
_EVIDENCE_DIR = _REPO_ROOT / "data" / "science-evidence" / "2026-08-02-acmg-bs1-founder-window"


def _walk_json(value: object):
    if isinstance(value, dict):
        for key, child in value.items():
            yield key, child
            yield from _walk_json(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_json(child)


def _assert_sanitized_payload(value: object, *, allowed_urls: tuple[str, ...] = ()) -> None:
    forbidden_keys = {
        "authors",
        "authorlist",
        "abstract",
        "citations",
        "fulltextexcerpts",
        "access",
        "url",
        "affiliation",
        "affiliationinfo",
        "coistatement",
    }
    for key, child in _walk_json(value):
        assert key.lower() not in forbidden_keys
        if isinstance(child, str):
            assert "utm_" not in child.lower()
            assert "email=" not in child.lower()
            assert "@" not in child
            if child not in allowed_urls:
                assert "http://" not in child.lower()
                assert "https://" not in child.lower()


def _resolve_json_pointer(value: object, pointer: str) -> object:
    assert pointer.startswith("/")
    resolved = value
    for segment in pointer.removeprefix("/").split("/"):
        if isinstance(resolved, list):
            resolved = resolved[int(segment)]
        else:
            assert isinstance(resolved, dict)
            resolved = resolved[segment]
    return resolved


def _ba1_evidence(
    af: float = 0.06,
    observed_alleles: int = BA1_MIN_OBSERVED_ALLELES,
    *,
    rsid: str | None = None,
) -> AcmgEvidence:
    return AcmgEvidence(
        rsid=rsid,
        gnomad_af_popmax=af,
        gnomad_an_popmax=observed_alleles,
        gnomad_af_eur=af,
        gnomad_an_eur=observed_alleles,
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
        c = criterion_ba1(_ba1_evidence())
        assert c is not None and c.strength == "Standalone" and c.points == -8

    def test_ba1_requires_observed_allele_count(self) -> None:
        assert criterion_ba1(AcmgEvidence(gnomad_af_eur=0.06)) is None
        assert (
            criterion_ba1(
                AcmgEvidence(
                    gnomad_af_eur=0.06,
                    gnomad_an_eur=BA1_MIN_OBSERVED_ALLELES - 1,
                )
            )
            is None
        )

    def test_ba1_uses_lower_supported_general_continental_frequency(self) -> None:
        criterion = criterion_ba1(
            AcmgEvidence(
                gnomad_af_afr=0.06,
                gnomad_an_afr=BA1_MIN_OBSERVED_ALLELES - 1,
                gnomad_af_eur=0.055,
                gnomad_an_eur=BA1_MIN_OBSERVED_ALLELES,
            )
        )
        assert criterion is not None and criterion.code == "BA1" and criterion.points == -8
        assert "EUR/NFE" in criterion.rationale

    def test_ba1_ignores_global_or_popmax_without_continental_population(self) -> None:
        assert (
            criterion_ba1(
                AcmgEvidence(
                    gnomad_af_global=0.06,
                    gnomad_an_global=BA1_MIN_OBSERVED_ALLELES,
                    gnomad_af_popmax=0.06,
                    gnomad_an_popmax=BA1_MIN_OBSERVED_ALLELES,
                )
            )
            is None
        )

    def test_ba1_not_applied_below_5pct(self) -> None:
        assert (
            criterion_ba1(
                AcmgEvidence(
                    gnomad_af_eur=0.04,
                    gnomad_an_eur=BA1_MIN_OBSERVED_ALLELES,
                )
            )
            is None
        )

    def test_ba1_boundary_is_strictly_above_cutoff(self) -> None:
        eps = BA1_AF_MIN / 100
        just_above = criterion_ba1(
            AcmgEvidence(
                gnomad_af_eur=BA1_AF_MIN + eps,
                gnomad_an_eur=BA1_MIN_OBSERVED_ALLELES,
            )
        )
        assert just_above is not None and just_above.strength == "Standalone"
        assert (
            criterion_ba1(
                AcmgEvidence(
                    gnomad_af_eur=BA1_AF_MIN,
                    gnomad_an_eur=BA1_MIN_OBSERVED_ALLELES,
                )
            )
            is None
        )
        assert (
            criterion_ba1(
                AcmgEvidence(
                    gnomad_af_eur=BA1_AF_MIN - eps,
                    gnomad_an_eur=BA1_MIN_OBSERVED_ALLELES,
                )
            )
            is None
        )

    @pytest.mark.parametrize(
        ("af_field", "an_field"),
        [
            ("gnomad_af_fin", "gnomad_an_fin"),
            ("gnomad_af_asj", "gnomad_an_asj"),
        ],
    )
    def test_ba1_excludes_founder_population_only_frequency(
        self, af_field: str, an_field: str
    ) -> None:
        ev = AcmgEvidence(
            gnomad_af_popmax=0.06,
            gnomad_an_popmax=BA1_MIN_OBSERVED_ALLELES,
            **{af_field: 0.06, an_field: BA1_MIN_OBSERVED_ALLELES},
        )
        assert criterion_ba1(ev) is None

    @pytest.mark.parametrize(
        ("af_field", "an_field"),
        [
            ("gnomad_af_fin", "gnomad_an_fin"),
            ("gnomad_af_asj", "gnomad_an_asj"),
        ],
    )
    def test_bs1_excludes_founder_population_only_frequency(
        self, af_field: str, an_field: str
    ) -> None:
        ev = AcmgEvidence(
            gnomad_af_popmax=0.02,
            gnomad_an_popmax=BA1_MIN_OBSERVED_ALLELES,
            **{af_field: 0.02, an_field: BA1_MIN_OBSERVED_ALLELES},
        )
        assert criterion_bs1(ev) is None

    def test_bs1_withholds_global_only_frequency_without_general_continent(self) -> None:
        ev = AcmgEvidence(
            gnomad_af_global=0.02,
            gnomad_an_global=BA1_MIN_OBSERVED_ALLELES,
        )
        assert criterion_bs1(ev) is None

    def test_bs1_uses_lower_supported_general_continental_frequency(self) -> None:
        criterion = criterion_bs1(
            AcmgEvidence(
                gnomad_af_afr=0.03,
                gnomad_an_afr=BA1_MIN_OBSERVED_ALLELES - 1,
                gnomad_af_eur=0.02,
                gnomad_an_eur=BA1_MIN_OBSERVED_ALLELES,
            )
        )
        assert criterion is not None and criterion.points == -4
        assert "EUR/NFE" in criterion.rationale

    @pytest.mark.parametrize(
        ("af_field", "an_field", "label"),
        [
            ("gnomad_af_afr", "gnomad_an_afr", "AFR"),
            ("gnomad_af_amr", "gnomad_an_amr", "AMR"),
            ("gnomad_af_eas", "gnomad_an_eas", "EAS"),
            ("gnomad_af_eur", "gnomad_an_eur", "EUR/NFE"),
            ("gnomad_af_sas", "gnomad_an_sas", "SAS"),
        ],
    )
    def test_ba1_applies_to_non_founder_continental_frequency(
        self, af_field: str, an_field: str, label: str
    ) -> None:
        ev = AcmgEvidence(
            gnomad_af_popmax=0.08,
            gnomad_an_popmax=BA1_MIN_OBSERVED_ALLELES,
            gnomad_af_fin=0.08,
            gnomad_an_fin=BA1_MIN_OBSERVED_ALLELES,
            gnomad_af_asj=0.07,
            gnomad_an_asj=BA1_MIN_OBSERVED_ALLELES,
            **{af_field: 0.06, an_field: BA1_MIN_OBSERVED_ALLELES},
        )
        c = criterion_ba1(ev)
        assert c is not None and c.code == "BA1"
        assert label in c.rationale

    @pytest.mark.parametrize(
        "rsid",
        [
            "rs1800562",  # HFE C282Y (#1243)
            "rs1799945",  # HFE H63D (#1243)
            "rs72474224",  # GJB2 V37I (#1243)
            "rs11466023",  # MEFV P369S (#1296)
            "rs13078881",  # BTD D444H (#1296)
            "rs1800556",  # ACADS R171W (#1296)
        ],
    )
    def test_ba1_skipped_for_clingen_exception_variant(self, rsid: str) -> None:
        # ClinGen SVI BA1 exception list (Ghosh 2018, PMID 30311383): common but with
        # evidence of pathogenicity → BA1 (stand-alone benign) must not fire at MAF > 5%,
        # even well above the cutoff (#1243/#1296).
        assert criterion_ba1(_ba1_evidence(0.07, rsid=rsid)) is None

    def test_ba1_exception_variant_not_force_drafted_benign(self) -> None:
        # HFE C282Y (rs1800562) at popmax 5.14% — the canonical Pathogenic hereditary-
        # hemochromatosis allele must not be force-drafted Benign by BA1 alone (#1243).
        result = classify_acmg(
            AcmgEvidence(
                rsid="rs1800562",
                gene_symbol="HFE",
                consequence="missense_variant",
                gnomad_af_popmax=0.0514,
                gnomad_an_popmax=BA1_MIN_OBSERVED_ALLELES,
                gnomad_af_eur=0.0514,
                gnomad_an_eur=BA1_MIN_OBSERVED_ALLELES,
            )
        )
        assert result.classification != BENIGN
        assert not any(c.code == "BA1" for c in result.criteria)

    def test_ba1_still_applies_to_common_non_exception_variant(self) -> None:
        # A common variant NOT on the exception list still gets BA1 → Benign (no regression).
        result = classify_acmg(
            AcmgEvidence(
                rsid="rs99999999",
                consequence="missense_variant",
                gnomad_af_popmax=0.20,
                gnomad_an_popmax=BA1_MIN_OBSERVED_ALLELES,
                gnomad_af_eur=0.20,
                gnomad_an_eur=BA1_MIN_OBSERVED_ALLELES,
            )
        )
        assert result.classification == BENIGN
        assert any(c.code == "BA1" for c in result.criteria)

    def test_bs1_above_1pct(self) -> None:
        criterion = criterion_bs1(
            AcmgEvidence(
                gnomad_af_popmax=0.04,
                gnomad_an_popmax=BA1_MIN_OBSERVED_ALLELES,
                gnomad_af_asj=0.04,
                gnomad_an_asj=BA1_MIN_OBSERVED_ALLELES,
                gnomad_af_eur=0.02,
                gnomad_an_eur=BA1_MIN_OBSERVED_ALLELES,
            )
        )
        assert criterion is not None and criterion.points == -4
        assert "EUR/NFE" in criterion.rationale

    def test_bs1_requires_observed_allele_count(self) -> None:
        assert criterion_bs1(AcmgEvidence(gnomad_af_eur=0.02)) is None
        assert (
            criterion_bs1(
                AcmgEvidence(
                    gnomad_af_eur=0.02,
                    gnomad_an_eur=BA1_MIN_OBSERVED_ALLELES - 1,
                )
            )
            is None
        )

    @pytest.mark.parametrize(
        ("rsid", "af"),
        [
            ("rs113993960", 0.0132),  # CFTR F508del (#1343)
            ("rs1799963", 0.0124),  # F2 prothrombin G20210A (#1343)
            ("rs6025", 0.0176),  # F5 Factor V Leiden (#1395)
        ],
    )
    def test_bs1_skipped_for_common_pathogenic_frequency_exception(
        self, rsid: str, af: float
    ) -> None:
        exception = AcmgEvidence(
            rsid=rsid,
            gnomad_af_popmax=af,
            gnomad_an_popmax=BA1_MIN_OBSERVED_ALLELES,
            gnomad_af_eur=af,
            gnomad_an_eur=BA1_MIN_OBSERVED_ALLELES,
        )
        control = AcmgEvidence(
            gnomad_af_popmax=af,
            gnomad_an_popmax=BA1_MIN_OBSERVED_ALLELES,
            gnomad_af_eur=af,
            gnomad_an_eur=BA1_MIN_OBSERVED_ALLELES,
        )

        assert criterion_bs1(exception) is None
        control_criterion = criterion_bs1(control)
        assert control_criterion is not None
        assert control_criterion.points == -4

    def test_bs1_not_applied_in_ba1_range(self) -> None:
        assert (
            criterion_bs1(
                AcmgEvidence(
                    gnomad_af_eur=0.06,
                    gnomad_an_eur=BA1_MIN_OBSERVED_ALLELES,
                )
            )
            is None
        )

    def test_bs1_boundary_is_above_one_pct_through_ba1_cutoff(self) -> None:
        lower_eps = BS1_AF_MIN / 10
        upper_eps = BA1_AF_MIN / 100
        just_above_lower = criterion_bs1(
            AcmgEvidence(
                gnomad_af_eur=BS1_AF_MIN + lower_eps,
                gnomad_an_eur=BA1_MIN_OBSERVED_ALLELES,
            )
        )
        at_upper = criterion_bs1(
            AcmgEvidence(
                gnomad_af_eur=BA1_AF_MIN,
                gnomad_an_eur=BA1_MIN_OBSERVED_ALLELES,
            )
        )
        assert just_above_lower is not None and just_above_lower.points == -4
        assert (
            criterion_bs1(
                AcmgEvidence(
                    gnomad_af_eur=BS1_AF_MIN,
                    gnomad_an_eur=BA1_MIN_OBSERVED_ALLELES,
                )
            )
            is None
        )
        assert at_upper is not None and at_upper.points == -4
        assert (
            criterion_bs1(
                AcmgEvidence(
                    gnomad_af_eur=BA1_AF_MIN + upper_eps,
                    gnomad_an_eur=BA1_MIN_OBSERVED_ALLELES,
                )
            )
            is None
        )

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
        ev = AcmgEvidence(
            gene_symbol="G",
            consequence="missense_variant",
            gnomad_af_popmax=0.06,
            gnomad_an_popmax=BA1_MIN_OBSERVED_ALLELES,
            gnomad_af_eur=0.06,
            gnomad_an_eur=BA1_MIN_OBSERVED_ALLELES,
        )
        result = classify_acmg(ev)
        assert result.classification == BENIGN
        assert any(c.code == "BA1" for c in result.criteria)

    def test_moderately_common_is_likely_benign(self) -> None:
        # BS1 (-4) → Likely benign.
        ev = AcmgEvidence(
            gene_symbol="G",
            consequence="missense_variant",
            gnomad_af_popmax=0.02,
            gnomad_an_popmax=BA1_MIN_OBSERVED_ALLELES,
            gnomad_af_eur=0.02,
            gnomad_an_eur=BA1_MIN_OBSERVED_ALLELES,
        )
        result = classify_acmg(ev)
        assert result.classification == LIKELY_BENIGN
        assert "31479589" in result.pmid_citations

    @pytest.mark.parametrize(
        ("evidence", "expected_codes"),
        [
            (
                AcmgEvidence(
                    rsid="rs113993960",
                    gene_symbol="CFTR",
                    consequence="inframe_deletion",
                    gnomad_af_popmax=0.0132,
                    gnomad_af_eur=0.0132,
                    gnomad_an_eur=BA1_MIN_OBSERVED_ALLELES,
                    clinvar_significance="Pathogenic",
                ),
                {"PM4"},
            ),
            (
                AcmgEvidence(
                    rsid="rs1799963",
                    gene_symbol="F2",
                    consequence="3_prime_UTR_variant",
                    gnomad_af_popmax=0.0124,
                    gnomad_af_eur=0.0124,
                    gnomad_an_eur=BA1_MIN_OBSERVED_ALLELES,
                    clinvar_significance="Pathogenic",
                ),
                set(),
            ),
            (
                AcmgEvidence(
                    rsid="rs6025",
                    gene_symbol="F5",
                    consequence="missense_variant",
                    gnomad_af_popmax=0.02,
                    gnomad_af_eur=0.02,
                    gnomad_an_eur=BA1_MIN_OBSERVED_ALLELES,
                    clinvar_significance="Pathogenic",
                ),
                set(),
            ),
        ],
    )
    def test_common_pathogenic_frequency_exception_not_likely_benign(
        self, evidence: AcmgEvidence, expected_codes: set[str]
    ) -> None:
        result = classify_acmg(evidence)
        assert result.classification != LIKELY_BENIGN
        assert {c.code for c in result.criteria} == expected_codes
        assert not any(c.code == "BS1" for c in result.criteria)

    def test_nothing_applies_is_uncertain(self) -> None:
        ev = AcmgEvidence(gene_symbol="G", consequence="missense_variant", gnomad_af_popmax=0.005)
        result = classify_acmg(ev)
        assert result.classification == UNCERTAIN
        assert result.criteria == []

    def test_pm3_is_listed_unassessable(self) -> None:
        result = classify_acmg(AcmgEvidence(gene_symbol="G", consequence="missense_variant"))
        assert "PM3" in result.unassessable
        assert "PP5" in result.unassessable  # withdrawn criteria flagged too


class TestAssessSampleAcmg:
    def test_sample_query_carries_per_population_ba1_frequency(self, tmp_path) -> None:
        reference_engine = sa.create_engine("sqlite:///:memory:")
        sample_engine = sa.create_engine(f"sqlite:///{tmp_path / 'sample.db'}")
        reference_metadata.create_all(reference_engine)
        create_sample_tables(sample_engine)

        with sample_engine.begin() as conn:
            conn.execute(
                annotated_variants.insert().values(
                    rsid="rs_common_eur",
                    chrom="1",
                    pos=100,
                    genotype="AG",
                    zygosity="het",
                    gene_symbol="G",
                    consequence="missense_variant",
                    clinvar_significance="Benign",
                    gnomad_af_popmax=0.06,
                    gnomad_an_popmax=BA1_MIN_OBSERVED_ALLELES,
                    gnomad_af_eur=0.06,
                    gnomad_an_eur=BA1_MIN_OBSERVED_ALLELES,
                )
            )

        result = assess_sample_acmg(sample_engine, reference_engine)

        assert result["total_candidates"] == 1
        variant = result["variants"][0]
        assert variant["acmg_classification"] == BENIGN
        assert any(c["code"] == "BA1" for c in variant["criteria"])

    @pytest.mark.parametrize(
        ("af_field", "an_field"),
        [
            ("gnomad_af_fin", "gnomad_an_fin"),
            ("gnomad_af_asj", "gnomad_an_asj"),
        ],
    )
    @pytest.mark.parametrize(
        ("consequence", "revel", "expected_codes"),
        [
            ("missense_variant", 0.9, {"PP3"}),
            ("stop_gained", None, set()),
            ("inframe_deletion", None, {"PM4"}),
        ],
    )
    @pytest.mark.parametrize(
        ("popmax", "global_af"),
        [
            (0.02, None),
            (None, 0.02),
        ],
    )
    @pytest.mark.parametrize(
        ("general_af", "general_an"),
        [
            (0.002, BA1_MIN_OBSERVED_ALLELES),
            (BS1_AF_MIN, BA1_MIN_OBSERVED_ALLELES),
            (0.02, BA1_MIN_OBSERVED_ALLELES - 1),
            (0.02, None),
        ],
    )
    def test_sample_query_keeps_founder_frequency_candidate_without_clinvar(
        self,
        tmp_path,
        af_field: str,
        an_field: str,
        consequence: str,
        revel: float | None,
        expected_codes: set[str],
        popmax: float | None,
        global_af: float | None,
        general_af: float,
        general_an: int | None,
    ) -> None:
        reference_engine = sa.create_engine("sqlite:///:memory:")
        sample_engine = sa.create_engine(f"sqlite:///{tmp_path / 'sample.db'}")
        reference_metadata.create_all(reference_engine)
        create_sample_tables(sample_engine)

        with sample_engine.begin() as conn:
            conn.execute(
                annotated_variants.insert().values(
                    rsid="rs_founder_only_bs1",
                    chrom="1",
                    pos=100,
                    genotype="AG",
                    zygosity="het",
                    gene_symbol="G",
                    consequence=consequence,
                    gnomad_af_global=global_af,
                    gnomad_af_popmax=popmax,
                    gnomad_an_popmax=BA1_MIN_OBSERVED_ALLELES,
                    gnomad_af_eur=general_af,
                    gnomad_an_eur=general_an,
                    revel=revel,
                    **{af_field: 0.02, an_field: BA1_MIN_OBSERVED_ALLELES},
                )
            )

        result = assess_sample_acmg(sample_engine, reference_engine)

        assert result["total_candidates"] == 1
        variant = result["variants"][0]
        assert variant["clinvar_significance"] is None
        assert variant["acmg_classification"] == UNCERTAIN
        assert {c["code"] for c in variant["criteria"]} == expected_codes

    def test_sample_query_excludes_common_nonfounder_frequency_without_clinvar(
        self, tmp_path
    ) -> None:
        reference_engine = sa.create_engine("sqlite:///:memory:")
        sample_engine = sa.create_engine(f"sqlite:///{tmp_path / 'sample.db'}")
        reference_metadata.create_all(reference_engine)
        create_sample_tables(sample_engine)

        with sample_engine.begin() as conn:
            conn.execute(
                annotated_variants.insert().values(
                    rsid="rs_common_nonfounder_without_clinvar",
                    chrom="1",
                    pos=100,
                    genotype="AG",
                    zygosity="het",
                    gene_symbol="G",
                    consequence="missense_variant",
                    gnomad_af_popmax=0.02,
                    gnomad_an_popmax=BA1_MIN_OBSERVED_ALLELES,
                    gnomad_af_fin=0.02,
                    gnomad_an_fin=BA1_MIN_OBSERVED_ALLELES,
                    gnomad_af_eur=0.02,
                    gnomad_an_eur=BA1_MIN_OBSERVED_ALLELES,
                    revel=0.9,
                )
            )

        result = assess_sample_acmg(sample_engine, reference_engine)

        assert result["total_candidates"] == 0
        assert result["variants"] == []

    def test_sample_query_excludes_hom_ref_negative_control(self, tmp_path) -> None:
        reference_engine = sa.create_engine("sqlite:///:memory:")
        sample_engine = sa.create_engine(f"sqlite:///{tmp_path / 'sample.db'}")
        reference_metadata.create_all(reference_engine)
        create_sample_tables(sample_engine)

        with sample_engine.begin() as conn:
            conn.execute(
                annotated_variants.insert().values(
                    rsid="rs_hom_ref_founder_control",
                    chrom="1",
                    pos=100,
                    genotype="AA",
                    zygosity="hom_ref",
                    gene_symbol="G",
                    consequence="missense_variant",
                    clinvar_significance="Pathogenic",
                    gnomad_af_popmax=0.02,
                    gnomad_an_popmax=BA1_MIN_OBSERVED_ALLELES,
                    gnomad_af_fin=0.02,
                    gnomad_an_fin=BA1_MIN_OBSERVED_ALLELES,
                )
            )

        result = assess_sample_acmg(sample_engine, reference_engine)

        assert result["total_candidates"] == 0
        assert result["variants"] == []


class TestBs1EvidencePacket:
    def test_generic_faf_scope_is_explicitly_bounded(self) -> None:
        inventory = json.loads(
            (_EVIDENCE_DIR / "source-inventory.json").read_text(encoding="utf-8")
        )
        criteria = inventory["benign_frequency_criteria"]
        faf_scope = criteria["generic_faf_data_eligibility"]
        ba1_source = criteria["ba1_selector_primary_source"]
        founder_source = criteria["founder_effect_corroboration"]

        assert faf_scope["criteria"] == ["BA1", "BS1"]
        assert "10.1002/cphg.93" in faf_scope["source"]
        assert "Finnish" in faf_scope["guidance"]
        assert "Ashkenazi Jewish" in faf_scope["guidance"]
        assert "does not ingest FAF" in faf_scope["implementation_limit"]
        assert "does not calculate FAF" in faf_scope["implementation_limit"]
        assert "disease-specific threshold" in faf_scope["implementation_limit"]
        assert "10.1002/humu.23642" in ba1_source["source"]
        assert "not used to calibrate BS1" in ba1_source["scope"]
        assert "10.1002/humu.24152" in founder_source["source"]
        assert "TP53" in founder_source["scope"]
        assert "not a universal BA1/BS1 threshold" in founder_source["scope"]
        assert (
            "not present either as an independent empirical frequency measurement"
            in founder_source["independence_note"]
        )
        selection_rule = criteria["selection_rule"]
        assert "allele frequency (AF)" in selection_rule
        assert "allele number (AN)" in selection_rule
        assert "Allele count (AC)" in selection_rule
        assert "neither ingested nor used" in selection_rule
        assert inventory["not_parsed_or_persisted_fields"] == ["FAF", "OTH", "AC"]

    def test_sanitized_provider_responses_are_retained_and_linked(self) -> None:
        index = json.loads(
            (_EVIDENCE_DIR / "source-response-index.json").read_text(encoding="utf-8")
        )
        entries = {entry["key"]: entry for entry in index["entries"]}
        expected_keys = {
            "consensus-ghosh-search",
            "consensus-ghosh-fetch",
            "scite-ghosh-metadata",
            "scite-cphg-faf-context",
            "scite-tp53-founder-context",
        }
        assert set(entries) == expected_keys
        _assert_sanitized_payload(index)

        for entry in entries.values():
            payload = _REPO_ROOT / entry["payload_path"]
            assert payload.is_file()
            assert hashlib.sha256(payload.read_bytes()).hexdigest() == entry["sanitized_sha256"]
            assert entry["unredacted_response_sha256"] is None
            assert entry["unredacted_response_sha256_note"].startswith("Not recorded because")
            resolved = _resolve_json_pointer(
                json.loads(payload.read_text(encoding="utf-8")), entry["json_pointer"]
            )

            if entry["service"] == "Consensus":
                response = resolved["sanitized_provider_envelope"]["content"][0]["response"]
                returned_identifier = (
                    response["selected_result"]["id"]
                    if entry["operation"] == "consensus_search"
                    else response["id"]
                )
                assert returned_identifier == entry["identifier"]
            else:
                assert resolved["key"] == entry["key"]
                response = resolved["sanitized_provider_envelope"]["content"][0]["response"]
                assert response["hits"][0]["doi"] == entry["doi"]

        consensus = json.loads(
            (_EVIDENCE_DIR / "raw" / "consensus-search-fetch-sanitized.json").read_text(
                encoding="utf-8"
            )
        )
        assert consensus["search"]["sanitized_provider_envelope"]["isError"] is False
        assert consensus["fetch"]["sanitized_provider_envelope"]["isError"] is False
        assert (
            consensus["fetch"]["sanitized_provider_envelope"]["content"][0]["response"]["id"]
            == "4439bbb6071d5097972fac0f2fea8fb0"
        )

        scite = json.loads(
            (_EVIDENCE_DIR / "raw" / "scite-targeted-doi-responses-sanitized.json").read_text(
                encoding="utf-8"
            )
        )
        responses = {response["key"]: response for response in scite["responses"]}
        assert set(responses) == {
            "scite-ghosh-metadata",
            "scite-cphg-faf-context",
            "scite-tp53-founder-context",
        }
        assert all(
            response["sanitized_provider_envelope"]["isError"] is False
            and response["response_field_presence"]["hits[0].retraction_notices"] is False
            for response in responses.values()
        )
        assert [
            hit["doi"]
            for hit in responses["scite-ghosh-metadata"]["sanitized_provider_envelope"]["content"][
                0
            ]["response"]["hits"]
        ] == ["10.1002/humu.23642", "10.1002/cphg.93", "10.1002/humu.24152"]
        assert (
            responses["scite-cphg-faf-context"]["sanitized_provider_envelope"]["content"][0][
                "response"
            ]["hits"][0]["retained_short_context"]["source_field"]
            == "fulltextExcerpts[2]"
        )
        assert (
            responses["scite-tp53-founder-context"]["sanitized_provider_envelope"]["content"][0][
                "response"
            ]["hits"][0]["retained_short_context"]["source_field"]
            == "citations[1].snippet"
        )

        _assert_sanitized_payload(consensus)
        _assert_sanitized_payload(scite)

        correction_path = _EVIDENCE_DIR / "pubmed-efetch-corrections-sanitized.json"
        correction_payload = json.loads(correction_path.read_text(encoding="utf-8"))
        assert correction_payload["claim_ids"] == ["C1", "C3"]
        public_request_url = correction_payload["source_snapshot"]["request"]
        assert public_request_url.startswith("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/")
        _assert_sanitized_payload(correction_payload, allowed_urls=(public_request_url,))
        assert (
            hashlib.sha256(correction_path.read_bytes()).hexdigest()
            == index["related_correction_snapshot"]["sanitized_sha256"]
        )
        correction_records = {record["pmid"]: record for record in correction_payload["records"]}
        assert set(correction_records) == {
            "30311383",
            "32461654",
            "28518168",
            "31479589",
            "33300245",
        }
        assert correction_records["30311383"]["comments_corrections"] == []
        assert correction_records["31479589"]["comments_corrections"] == []
        assert correction_records["33300245"]["comments_corrections"] == []
        assert {
            link["ref_type"] for link in correction_records["32461654"]["comments_corrections"]
        } == {"CommentIn", "ErratumIn"}

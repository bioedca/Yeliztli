"""Hard MAF/r² imputation firewall (SW-C3).

Pins the policy that decides whether a Beagle-imputed variant may back a
P/LP/carrier/monogenic call: imputed markers pass only when both well-imputed
(DR2 >= 0.8) and common (MAF >= 1%); genotyped markers always pass; the reason
codes and the quality-before-frequency precedence are value-locked; and the
aggregate summary counts only imputed markers.
"""

from __future__ import annotations

import pytest

from backend.analysis.imputation_firewall import (
    RARE_MAF_THRESHOLD,
    FirewallReason,
    assess_variant,
    minor_allele_frequency,
    summarize_firewall,
)
from backend.analysis.imputation_runner import WELL_IMPUTED_DR2, ImputedVariant


def _variant(
    *,
    dr2: float | None,
    af: float | None,
    imputed: bool,
    pos: int = 100,
    ref: str = "A",
    alt: str = "G",
) -> ImputedVariant:
    return ImputedVariant(chrom="22", pos=pos, ref=ref, alt=alt, dr2=dr2, af=af, imputed=imputed)


class TestThresholds:
    def test_constants_pinned(self) -> None:
        # The firewall is built on the standard cutoffs; lock them so a silent
        # edit to either constant trips a test.
        assert RARE_MAF_THRESHOLD == 0.01
        assert WELL_IMPUTED_DR2 == 0.8


class TestMinorAlleleFrequency:
    @pytest.mark.parametrize(
        ("af", "expected"),
        [(None, None), (0.2, 0.2), (0.9, pytest.approx(0.1)), (0.5, 0.5), (0.0, 0.0), (1.0, 0.0)],
    )
    def test_folds_to_minor(self, af, expected) -> None:
        assert minor_allele_frequency(af) == expected


class TestGenotyped:
    def test_genotyped_always_passes_even_if_rare(self) -> None:
        # A directly typed marker is observed, not imputed → firewall N/A. It must
        # pass even with no DR2 and a rare AF (a real array call of a rare allele).
        v = _variant(dr2=None, af=0.001, imputed=False)
        d = assess_variant(v)
        assert d.reportable is True
        assert d.reason is FirewallReason.GENOTYPED


class TestImputedPass:
    def test_common_well_imputed_passes(self) -> None:
        d = assess_variant(_variant(dr2=0.90, af=0.30, imputed=True))
        assert d.reportable is True
        assert d.reason is FirewallReason.IMPUTED_PASS

    def test_dr2_boundary_is_inclusive(self) -> None:
        # DR2 == 0.8 is well-imputed (>=); just under is not.
        assert assess_variant(_variant(dr2=0.80, af=0.30, imputed=True)).reason is (
            FirewallReason.IMPUTED_PASS
        )
        assert assess_variant(_variant(dr2=0.79, af=0.30, imputed=True)).reason is (
            FirewallReason.LOW_DR2
        )

    def test_maf_boundary_is_inclusive(self) -> None:
        # MAF == 1% is common (reportable); just under is rare (quarantined).
        assert assess_variant(_variant(dr2=0.90, af=0.01, imputed=True)).reason is (
            FirewallReason.IMPUTED_PASS
        )
        assert assess_variant(_variant(dr2=0.90, af=0.009, imputed=True)).reason is (
            FirewallReason.IMPUTED_RARE
        )

    def test_maf_uses_minor_allele_so_high_af_common_alt_passes(self) -> None:
        # AF=0.97 → MAF=0.03 ≥ 1% → common → reportable (folding matters).
        assert assess_variant(_variant(dr2=0.90, af=0.97, imputed=True)).reason is (
            FirewallReason.IMPUTED_PASS
        )


class TestQuarantine:
    def test_imputed_rare_quarantined_despite_high_dr2(self) -> None:
        # The headline case: pristine DR2 cannot rescue a rare imputed variant.
        d = assess_variant(_variant(dr2=0.99, af=0.005, imputed=True))
        assert d.reportable is False
        assert d.reason is FirewallReason.IMPUTED_RARE

    def test_imputed_rare_via_high_af_minor_allele(self) -> None:
        # AF=0.995 → MAF=0.005 < 1% → rare, even though ALT is "common".
        d = assess_variant(_variant(dr2=0.95, af=0.995, imputed=True))
        assert d.reportable is False
        assert d.reason is FirewallReason.IMPUTED_RARE

    def test_low_dr2_quarantined(self) -> None:
        d = assess_variant(_variant(dr2=0.50, af=0.30, imputed=True))
        assert d.reportable is False
        assert d.reason is FirewallReason.LOW_DR2

    def test_missing_dr2_quarantined(self) -> None:
        d = assess_variant(_variant(dr2=None, af=0.30, imputed=True))
        assert d.reportable is False
        assert d.reason is FirewallReason.MISSING_DR2

    def test_missing_af_quarantined_when_dr2_ok(self) -> None:
        d = assess_variant(_variant(dr2=0.90, af=None, imputed=True))
        assert d.reportable is False
        assert d.reason is FirewallReason.MISSING_AF


class TestPrecedence:
    def test_quality_checked_before_frequency(self) -> None:
        # Imputed, missing DR2 AND rare → quality gate fires first (MISSING_DR2),
        # not IMPUTED_RARE.
        assert assess_variant(_variant(dr2=None, af=0.001, imputed=True)).reason is (
            FirewallReason.MISSING_DR2
        )
        # Imputed, low DR2 AND rare → LOW_DR2 (still quality-first).
        assert assess_variant(_variant(dr2=0.40, af=0.001, imputed=True)).reason is (
            FirewallReason.LOW_DR2
        )


class TestSummary:
    def test_counts_only_imputed_and_tallies_reasons(self) -> None:
        variants = [
            _variant(dr2=None, af=0.001, imputed=False, pos=1),  # genotyped → excluded
            _variant(dr2=0.90, af=0.30, imputed=True, pos=2),  # pass
            _variant(dr2=0.99, af=0.005, imputed=True, pos=3),  # imputed_rare
            _variant(dr2=0.50, af=0.30, imputed=True, pos=4),  # low_dr2
            _variant(dr2=None, af=0.30, imputed=True, pos=5),  # missing_dr2
            _variant(dr2=0.90, af=0.20, imputed=True, pos=6),  # pass
        ]
        s = summarize_firewall(variants)
        assert s.n_imputed == 5  # the genotyped one is excluded
        assert s.n_reportable == 2
        assert s.n_quarantined == 3
        assert s.quarantine_reasons == {
            "imputed_rare": 1,
            "low_dr2": 1,
            "missing_dr2": 1,
        }
        assert s.frac_reportable == pytest.approx(2 / 5)

    def test_frac_none_when_no_imputed(self) -> None:
        s = summarize_firewall([_variant(dr2=None, af=0.3, imputed=False)])
        assert s.n_imputed == 0
        assert s.frac_reportable is None

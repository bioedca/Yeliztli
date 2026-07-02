"""HLA autoimmune-susceptibility assessment (Wave D / SW-D4).

Pins the four associations (B*27/AS, C*06:02/psoriasis, DRB1-SE/RA, DR3-DQ2/DR4-DQ8
T1D), each framed as susceptibility-not-diagnostic, plus the edge cases: the
disease-neutral B*27:06/*27:09 subtypes, the DR3/DR4 heterozygote (highest risk),
the DQB1*06:02 protective note, and not_typed vs typed-negative.
"""

from __future__ import annotations

from backend.analysis.hla_resolver import ResolvedHLACall
from backend.analysis.hla_susceptibility import (
    STATUS_INCREASED,
    STATUS_NEUTRAL_SUBTYPE,
    STATUS_NOT_INCREASED,
    STATUS_NOT_TYPED,
    assess_susceptibility,
)


def _c(locus, a1, a2, *, low=False) -> ResolvedHLACall:
    return ResolvedHLACall(
        locus=locus,
        allele1=a1,
        allele2=a2,
        prob=0.9,
        low_confidence=low,
        source="hibag",
        ancestry_model="European",
    )


def _find(report, hla: str):
    return next(f for f in report.findings if f.hla == hla)


class TestB27:
    def test_risk_subtype_increased(self) -> None:
        f = _find(assess_susceptibility([_c("B", "27:05", "07:02")]), "HLA-B*27")
        assert f.status == STATUS_INCREASED
        assert f.carried is True
        assert "27:05" in f.detail
        assert "susceptibility marker, not a diagnosis" in f.interpretation

    def test_neutral_subtype_not_increased(self) -> None:
        f = _find(assess_susceptibility([_c("B", "27:06", "07:02")]), "HLA-B*27")
        assert f.status == STATUS_NEUTRAL_SUBTYPE
        assert "not disease-associated" in f.interpretation

    def test_two_neutral_subtypes(self) -> None:
        f = _find(assess_susceptibility([_c("B", "27:06", "27:09")]), "HLA-B*27")
        assert f.status == STATUS_NEUTRAL_SUBTYPE

    def test_risk_plus_neutral_is_increased(self) -> None:
        # One risk subtype + one disease-neutral subtype = ONE risk copy → the
        # detail must read heterozygous, not homozygous (must not overstate).
        f = _find(assess_susceptibility([_c("B", "27:05", "27:06")]), "HLA-B*27")
        assert f.status == STATUS_INCREASED
        assert "27:05" in f.detail
        assert "heterozygous" in f.detail
        assert "27:06" not in f.detail  # the neutral copy is not shown as a risk allele

    def test_two_risk_subtypes_homozygous(self) -> None:
        f = _find(assess_susceptibility([_c("B", "27:05", "27:02")]), "HLA-B*27")
        assert f.status == STATUS_INCREASED
        assert "homozygous" in f.detail

    def test_absent(self) -> None:
        f = _find(assess_susceptibility([_c("B", "07:02", "08:01")]), "HLA-B*27")
        assert f.status == STATUS_NOT_INCREASED

    def test_not_typed(self) -> None:
        f = _find(assess_susceptibility([_c("C", "07:01", "07:02")]), "HLA-B*27")
        assert f.status == STATUS_NOT_TYPED


class TestC0602:
    def test_present(self) -> None:
        f = _find(assess_susceptibility([_c("C", "06:02", "07:01")]), "HLA-C*06:02")
        assert f.status == STATUS_INCREASED
        assert "psoriasis" in f.interpretation.lower()
        # PsA-direction caveat must travel with the finding.
        assert any(
            "psoriatic-arthritis" in n or "psoriatic arthritis" in n.lower() for n in f.notes
        )

    def test_absent(self) -> None:
        f = _find(assess_susceptibility([_c("C", "07:01", "07:02")]), "HLA-C*06:02")
        assert f.status == STATUS_NOT_INCREASED

    def test_not_typed(self) -> None:
        f = _find(assess_susceptibility([_c("B", "07:02", "08:01")]), "HLA-C*06:02")
        assert f.status == STATUS_NOT_TYPED


class TestRaSharedEpitope:
    def test_present(self) -> None:
        f = _find(assess_susceptibility([_c("DRB1", "04:01", "15:01")]), "HLA-DRB1 shared epitope")
        assert f.status == STATUS_INCREASED
        assert "04:01" in f.detail
        assert "seropositive" in f.interpretation.lower()

    def test_absent(self) -> None:
        f = _find(assess_susceptibility([_c("DRB1", "15:01", "13:01")]), "HLA-DRB1 shared epitope")
        assert f.status == STATUS_NOT_INCREASED

    def test_not_typed(self) -> None:
        f = _find(assess_susceptibility([_c("B", "07:02", "08:01")]), "HLA-DRB1 shared epitope")
        assert f.status == STATUS_NOT_TYPED


class TestT1D:
    def test_dr3_dr4_heterozygote_highest(self) -> None:
        calls = [_c("DQA1", "05:01", "03:01"), _c("DQB1", "02:01", "03:02")]
        f = _find(assess_susceptibility(calls), "HLA DR3-DQ2 / DR4-DQ8")
        assert f.status == STATUS_INCREASED
        assert "heterozygote" in f.detail.lower()
        assert "greatest" in f.interpretation.lower()

    def test_dr3_only_increased(self) -> None:
        calls = [_c("DQA1", "05:01", "01:01"), _c("DQB1", "02:01", "05:01")]
        f = _find(assess_susceptibility(calls), "HLA DR3-DQ2 / DR4-DQ8")
        assert f.status == STATUS_INCREASED
        assert "DR3-DQ2" in f.detail

    def test_protective_dqb1_0602_noted(self) -> None:
        # DR4-DQ8 present + protective DQB1*06:02 present.
        calls = [_c("DQA1", "01:02", "03:01"), _c("DQB1", "06:02", "03:02")]
        f = _find(assess_susceptibility(calls), "HLA DR3-DQ2 / DR4-DQ8")
        assert f.status == STATUS_INCREASED
        assert "protective" in f.interpretation.lower()

    def test_neither_haplotype(self) -> None:
        calls = [_c("DQA1", "01:01", "04:01"), _c("DQB1", "05:01", "06:03")]
        f = _find(assess_susceptibility(calls), "HLA DR3-DQ2 / DR4-DQ8")
        assert f.status == STATUS_NOT_INCREASED

    def test_not_typed_when_dqb1_missing(self) -> None:
        f = _find(assess_susceptibility([_c("DQA1", "05:01", "01:01")]), "HLA DR3-DQ2 / DR4-DQ8")
        assert f.status == STATUS_NOT_TYPED


class TestReport:
    def test_empty_unavailable(self) -> None:
        report = assess_susceptibility([])
        assert report.available is False
        assert report.findings == []
        assert report.unavailable_note is not None

    def test_four_findings_and_caveat(self) -> None:
        report = assess_susceptibility([_c("B", "27:05", "07:02")])
        assert report.available is True
        assert len(report.findings) == 4
        assert report.caveat
        assert "transplant" in report.caveat.lower()

    def test_low_confidence_propagates(self) -> None:
        f = _find(assess_susceptibility([_c("C", "06:02", "07:01", low=True)]), "HLA-C*06:02")
        assert f.low_confidence is True

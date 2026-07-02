"""HLA high-NPV rule-outs — celiac + narcolepsy (Wave D / SW-D3).

Pins the celiac HLA-DQ heterodimer composition (DQ2.5 / DQ8 / DQ2.2 + the DQB1*02
/ DQB1*03:02 half-heterodimer that a DQ2.5/DQ8-only rule-out would miss), the
rule_out vs permissive vs not_typed statuses, and the narcolepsy DQB1*06:02
present / absent-lowers / not_typed framing.
"""

from __future__ import annotations

from backend.analysis.hla_resolver import ResolvedHLACall
from backend.analysis.hla_rule_outs import (
    CELIAC_NOT_TYPED,
    CELIAC_PERMISSIVE,
    CELIAC_RULE_OUT,
    NARCO_ABSENT,
    NARCO_NOT_TYPED,
    NARCO_PRESENT,
    assess_rule_outs,
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


class TestCeliac:
    def test_empty_unavailable(self) -> None:
        report = assess_rule_outs([])
        assert report.available is False
        assert report.celiac is None
        assert report.unavailable_note is not None

    def test_rule_out_when_no_permissive_dq(self) -> None:
        # DQA1/DQB1 typed, but no DQ2/DQ8 heterodimer or DQB1*02 / *03:02 β-chain.
        calls = [_c("DQA1", "01:01", "04:01"), _c("DQB1", "05:01", "06:03")]
        report = assess_rule_outs(calls)
        assert report.celiac.status == CELIAC_RULE_OUT
        assert report.celiac.detected == []
        assert "very unlikely" in report.celiac.interpretation

    def test_dq25_permissive(self) -> None:
        calls = [_c("DQA1", "05:01", "01:01"), _c("DQB1", "02:01", "05:01")]
        report = assess_rule_outs(calls)
        assert report.celiac.status == CELIAC_PERMISSIVE
        assert any("DQ2.5" in d for d in report.celiac.detected)

    def test_dq8_permissive(self) -> None:
        calls = [_c("DQA1", "03:01", "01:01"), _c("DQB1", "03:02", "05:01")]
        report = assess_rule_outs(calls)
        assert report.celiac.status == CELIAC_PERMISSIVE
        assert any("DQ8" in d for d in report.celiac.detected)

    def test_dq22_permissive(self) -> None:
        calls = [_c("DQA1", "02:01", "01:01"), _c("DQB1", "02:02", "05:01")]
        report = assess_rule_outs(calls)
        assert report.celiac.status == CELIAC_PERMISSIVE
        assert any("DQ2.2" in d for d in report.celiac.detected)

    def test_half_heterodimer_is_not_ruled_out(self) -> None:
        # The key DQ2.2/half-het case: DQB1*02:01 present but no DQA1*05 → no full
        # DQ2.5, yet a DQ2.5/DQ8-only rule-out would wrongly clear this. Must be
        # flagged permissive via the DQB1*02 β-chain, NOT ruled out.
        calls = [_c("DQA1", "01:01", "04:01"), _c("DQB1", "02:01", "05:01")]
        report = assess_rule_outs(calls)
        assert report.celiac.status == CELIAC_PERMISSIVE
        assert any("half-heterodimer" in d for d in report.celiac.detected)

    def test_not_typed_when_dqb1_missing(self) -> None:
        report = assess_rule_outs([_c("DQA1", "05:01", "01:01")])  # no DQB1 call
        assert report.celiac.status == CELIAC_NOT_TYPED
        assert report.celiac.detected == []

    def test_not_typed_when_dqa1_missing(self) -> None:
        report = assess_rule_outs([_c("DQB1", "02:01", "05:01")])  # no DQA1 call
        assert report.celiac.status == CELIAC_NOT_TYPED

    def test_low_confidence_propagates(self) -> None:
        calls = [_c("DQA1", "01:01", "04:01"), _c("DQB1", "05:01", "06:03", low=True)]
        report = assess_rule_outs(calls)
        assert report.celiac.low_confidence is True


class TestNarcolepsy:
    def test_present_is_non_diagnostic(self) -> None:
        calls = [_c("DQB1", "06:02", "05:01")]
        report = assess_rule_outs(calls)
        assert report.narcolepsy.status == NARCO_PRESENT
        assert report.narcolepsy.carried is True
        assert "not" in report.narcolepsy.interpretation.lower()  # not diagnostic

    def test_absent_lowers_but_not_rule_out(self) -> None:
        calls = [_c("DQB1", "05:01", "03:01")]
        report = assess_rule_outs(calls)
        assert report.narcolepsy.status == NARCO_ABSENT
        assert report.narcolepsy.carried is False
        # Framed as "argues strongly against" but NOT a full exclusion.
        assert "strongly against" in report.narcolepsy.interpretation
        assert "not fully exclude" in report.narcolepsy.interpretation

    def test_not_typed_when_dqb1_missing(self) -> None:
        report = assess_rule_outs([_c("A", "01:01", "02:01")])
        assert report.narcolepsy.status == NARCO_NOT_TYPED

    def test_homozygous_reported(self) -> None:
        report = assess_rule_outs([_c("DQB1", "06:02", "06:02")])
        assert report.narcolepsy.zygosity == "homozygous"


def test_citations_present() -> None:
    report = assess_rule_outs([_c("DQB1", "06:02", "05:01")])
    assert "PMID:31274511" in report.citations  # Brown 2019 celiac HLA guide
    assert "PMID:30321823" in report.citations  # Capittini 2018 narcolepsy meta
    assert report.caveat

"""Raw imputed-HLA viewer/export (Wave D / SW-D5).

Pins the classical-locus ordering, the field mapping, the empty/unavailable path,
and — the load-bearing SW-D5 guard — that every response carries the imputed
caveat and the never-for-transplant statement.
"""

from __future__ import annotations

from backend.analysis.hla_resolver import ResolvedHLACall
from backend.analysis.hla_viewer import HLA_TRANSPLANT_GUARD, build_hla_viewer


def _c(locus, a1, a2, *, prob=0.9, low=False) -> ResolvedHLACall:
    return ResolvedHLACall(
        locus=locus,
        allele1=a1,
        allele2=a2,
        prob=prob,
        low_confidence=low,
        source="hibag",
        ancestry_model="European",
    )


class TestBuildHlaViewer:
    def test_empty_unavailable(self) -> None:
        report = build_hla_viewer([])
        assert report.available is False
        assert report.alleles == []
        assert report.unavailable_note is not None
        # The safety framing is present even on the empty path.
        assert report.transplant_guard
        assert report.caveat

    def test_orders_by_classical_locus(self) -> None:
        # Given out of order (DQB1, A, B) → A, B, DQB1.
        calls = [
            _c("DQB1", "06:02", "03:01"),
            _c("A", "01:01", "02:01"),
            _c("B", "57:01", "07:02"),
        ]
        report = build_hla_viewer(calls)
        assert [a.locus for a in report.alleles] == ["A", "B", "DQB1"]

    def test_unknown_locus_sorts_last(self) -> None:
        report = build_hla_viewer([_c("ZZZ", "01", "02"), _c("A", "01:01", "02:01")])
        assert [a.locus for a in report.alleles] == ["A", "ZZZ"]

    def test_field_mapping(self) -> None:
        report = build_hla_viewer([_c("B", "57:01", "07:02", prob=0.96, low=False)])
        a = report.alleles[0]
        assert (a.locus, a.allele1, a.allele2) == ("B", "57:01", "07:02")
        assert a.prob == 0.96
        assert a.low_confidence is False
        assert a.source == "hibag"
        assert a.ancestry_model == "European"

    def test_low_confidence_propagates(self) -> None:
        report = build_hla_viewer([_c("B", "57:01", "07:02", prob=0.4, low=True)])
        assert report.alleles[0].low_confidence is True

    def test_transplant_guard_content(self) -> None:
        report = build_hla_viewer([_c("A", "01:01", "02:01")])
        assert report.transplant_guard == HLA_TRANSPLANT_GUARD
        assert "never" in report.transplant_guard.lower()
        assert "transplant" in report.transplant_guard.lower()
        assert "donor" in report.transplant_guard.lower()

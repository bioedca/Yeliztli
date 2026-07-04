"""HLA drug-hypersensitivity assessment (Wave D / SW-D2).

Pins the per-drug risk assessment over resolved HLA calls: carrier → at_risk with
the CPIC recommendation, typed-but-absent → no_risk_allele, locus-not-called →
not_typed (never a false negative), empty → unavailable, plus zygosity/copies,
ancestry notes, and the standing imputed-HLA confirmation caveat.
"""

from __future__ import annotations

from backend.analysis.hla_drug_hypersensitivity import (
    HLA_IMPUTED_CONFIRMATION_CAVEAT,
    STATUS_AT_RISK,
    STATUS_NO_RISK_ALLELE,
    STATUS_NOT_TYPED,
    assess_drug_hypersensitivity,
)
from backend.analysis.hla_resolver import ResolvedHLACall


def _call(locus, a1, a2, *, prob=0.95, low=False) -> ResolvedHLACall:
    return ResolvedHLACall(
        locus=locus,
        allele1=a1,
        allele2=a2,
        prob=prob,
        low_confidence=low,
        source="hibag",
        ancestry_model="European",
    )


def _by_allele(report):
    return {a.allele: a for a in report.assessments}


class TestAssessDrugHypersensitivity:
    def test_empty_calls_unavailable(self) -> None:
        report = assess_drug_hypersensitivity([])
        assert report.available is False
        assert report.any_at_risk is False
        assert report.assessments == []
        assert report.unavailable_note is not None

    def test_b5701_carrier_is_at_risk_for_abacavir(self) -> None:
        report = assess_drug_hypersensitivity([_call("B", "57:01", "07:02")])
        assert report.available is True
        assert report.any_at_risk is True
        a = _by_allele(report)["HLA-B*57:01"]
        assert a.status == STATUS_AT_RISK
        assert a.carried is True
        assert a.copies == 1
        assert a.zygosity == "heterozygous"
        assert "abacavir" in a.drugs
        assert "abacavir" in a.recommendation.lower()
        assert "PMID:24561393" in a.citations

    def test_b_locus_typed_without_5701_is_no_risk_allele(self) -> None:
        # A B call that isn't a curated B risk allele resolves those B rows to no_risk_allele.
        report = assess_drug_hypersensitivity([_call("B", "07:02", "08:01")])
        by = _by_allele(report)
        for allele in ("HLA-B*57:01", "HLA-B*15:02", "HLA-B*15:11", "HLA-B*58:01"):
            assert by[allele].status == STATUS_NO_RISK_ALLELE
            assert by[allele].carried is False
        assert report.any_at_risk is False

    def test_locus_not_called_is_not_typed(self) -> None:
        # Only an A call present → B/DRB-independent drug loci are not typed.
        report = assess_drug_hypersensitivity([_call("A", "01:01", "02:01")])
        by = _by_allele(report)
        assert by["HLA-B*57:01"].status == STATUS_NOT_TYPED
        assert by["HLA-B*57:01"].carried is False
        # A*31:01 IS assessable (A locus typed, allele absent).
        assert by["HLA-A*31:01"].status == STATUS_NO_RISK_ALLELE

    def test_allopurinol_and_b1502_anticonvulsant_alleles(self) -> None:
        report = assess_drug_hypersensitivity([_call("B", "58:01", "15:02")])
        by = _by_allele(report)
        assert by["HLA-B*58:01"].status == STATUS_AT_RISK
        assert "allopurinol" in by["HLA-B*58:01"].drugs
        assert by["HLA-B*15:02"].status == STATUS_AT_RISK
        assert "carbamazepine" in by["HLA-B*15:02"].drugs
        assert "oxcarbazepine" in by["HLA-B*15:02"].drugs
        assert "phenytoin" in by["HLA-B*15:02"].drugs
        assert "fosphenytoin" in by["HLA-B*15:02"].drugs

    def test_homozygous_reports_two_copies(self) -> None:
        report = assess_drug_hypersensitivity([_call("B", "57:01", "57:01")])
        a = _by_allele(report)["HLA-B*57:01"]
        assert a.copies == 2
        assert a.zygosity == "homozygous"

    def test_b1502_surfaces_phenytoin_as_first_class_drug_alert(self) -> None:
        report = assess_drug_hypersensitivity([_call("B", "15:02", "07:02")])
        a = _by_allele(report)["HLA-B*15:02"]
        joined = " ".join(a.notes).lower()
        assert "han chinese" in joined  # ancestry scoping note
        assert "phenytoin" in a.drugs
        assert "fosphenytoin" in a.drugs
        assert "phenytoin-naive" in a.recommendation.lower()
        assert "phenytoin/fosphenytoin" in a.recommendation.lower()
        assert "PMID:32779747" in a.citations
        assert "PMID:34816768" in a.citations

    def test_b1511_surfaces_carbamazepine_alert(self) -> None:
        report = assess_drug_hypersensitivity([_call("B", "15:11", "07:02")])
        a = _by_allele(report)["HLA-B*15:11"]

        assert a.status == STATUS_AT_RISK
        assert a.carried is True
        assert a.drugs == ["carbamazepine"]
        assert "carbamazepine" in a.recommendation.lower()
        assert "alternative" in a.recommendation.lower()
        assert "DPWG" in a.guideline
        assert any("HLA-B*15:02 screening alone" in n for n in a.notes)
        assert any("not inferred" in n for n in a.notes)
        assert "PMID:21204807" in a.citations
        assert "PMID:34553372" in a.citations
        assert "PMID:35599240" in a.citations
        assert "PMID:38570725" in a.citations

    def test_dapsone_low_ppv_note(self) -> None:
        report = assess_drug_hypersensitivity([_call("B", "13:01", "07:02")])
        a = _by_allele(report)["HLA-B*13:01"]
        assert a.status == STATUS_AT_RISK
        assert "dapsone" in a.drugs
        assert any("predictive value" in n.lower() for n in a.notes)

    def test_low_confidence_propagates(self) -> None:
        report = assess_drug_hypersensitivity([_call("B", "57:01", "07:02", prob=0.4, low=True)])
        a = _by_allele(report)["HLA-B*57:01"]
        assert a.low_confidence is True
        assert a.prob == 0.4

    def test_caveat_always_present(self) -> None:
        report = assess_drug_hypersensitivity([_call("B", "57:01", "07:02")])
        assert report.caveat == HLA_IMPUTED_CONFIRMATION_CAVEAT
        assert "transplant" in report.caveat.lower()

    def test_covers_all_six_associations(self) -> None:
        report = assess_drug_hypersensitivity([_call("A", "01:01", "02:01")])
        # Six curated associations across A / B loci.
        assert len(report.assessments) == 6
        assert {a.allele for a in report.assessments} == {
            "HLA-B*57:01",
            "HLA-B*15:02",
            "HLA-B*15:11",
            "HLA-A*31:01",
            "HLA-B*58:01",
            "HLA-B*13:01",
        }

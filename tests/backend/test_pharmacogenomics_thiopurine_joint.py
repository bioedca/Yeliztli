"""CPIC thiopurine joint (TPMT, NUDT15) compound-IM resolution (#2007).

CPIC's thiopurine guideline is keyed on the (TPMT, NUDT15) phenotype *pair*.
A patient Intermediate at both genes is a compound intermediate metabolizer with
a distinct, lower starting-dose band (20-50% of standard) than either single-gene
Intermediate Metabolizer (30-80%), reflecting additive toxicity. The per-gene
``cpic_guidelines`` schema emits two independent single-gene alerts whose bands
both sit above CPIC's compound cap; ``generate_prescribing_alerts`` collapses the
(IM, IM) case into CPIC's one joint recommendation.

Ground truth: CPIC 2025 update, PMID:41618934 / DOI:10.1002/cpt.70209
(accessed 2026-07-17); per-drug bands sourced from api.cpicpgx.org/v1/recommendation.
"""

from __future__ import annotations

import csv
from pathlib import Path

import pytest
import sqlalchemy as sa

from backend.analysis.pharmacogenomics import (
    CallConfidence,
    StarAlleleResult,
    generate_prescribing_alerts,
)
from backend.annotation.cpic import load_cpic_from_csvs
from backend.db.tables import reference_metadata

_CPIC_DIR = Path(__file__).resolve().parents[2] / "backend" / "data" / "cpic"
_GUIDELINES_CSV = _CPIC_DIR / "cpic_guidelines.csv"
_THIOPURINES = ("mercaptopurine", "azathioprine", "thioguanine")
_JOINT_GENE = "TPMT/NUDT15"
_COMPOUND_IM = "Compound Intermediate Metabolizer"
_SINGLE_IM = "Intermediate Metabolizer"


@pytest.fixture(scope="module")
def reference_engine() -> sa.Engine:
    engine = sa.create_engine("sqlite://")
    reference_metadata.create_all(engine)
    load_cpic_from_csvs(
        _CPIC_DIR / "cpic_alleles.csv",
        _CPIC_DIR / "cpic_diplotypes.csv",
        _CPIC_DIR / "cpic_guidelines.csv",
        engine,
    )
    return engine


def _result(
    gene: str,
    phenotype: str,
    diplotype: str,
    confidence: CallConfidence = CallConfidence.COMPLETE,
) -> StarAlleleResult:
    allele1, _, allele2 = diplotype.partition("/")
    return StarAlleleResult(
        gene=gene,
        allele1=allele1,
        allele2=allele2,
        diplotype=diplotype,
        phenotype=phenotype,
        call_confidence=confidence,
        confidence_note="test fixture",
    )


def _guideline_rows() -> list[dict[str, str]]:
    with open(_GUIDELINES_CSV, encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


class TestCompoundImData:
    """The bundled CSV carries a compound-IM row for every thiopurine."""

    def test_compound_im_row_present_for_all_thiopurines(self) -> None:
        drugs = {
            row["drug"]
            for row in _guideline_rows()
            if row["gene"] == _JOINT_GENE and row["phenotype"] == _COMPOUND_IM
        }
        assert drugs == set(_THIOPURINES)

    def test_compound_im_band_is_20_50_percent(self) -> None:
        for row in _guideline_rows():
            if row["gene"] == _JOINT_GENE:
                assert "20-50%" in row["recommendation"], row["drug"]

    def test_compound_im_differs_from_both_single_gene_im(self) -> None:
        """The issue's core regression: the joint band must not equal either
        single-gene IM row (30-80% for mercaptopurine/azathioprine, 50% for
        thioguanine) — both of which exceed CPIC's compound 50% ceiling."""
        rows = _guideline_rows()

        def _rec(gene: str, drug: str, phenotype: str) -> str:
            return next(
                r["recommendation"]
                for r in rows
                if r["gene"] == gene and r["drug"] == drug and r["phenotype"] == phenotype
            )

        for drug in _THIOPURINES:
            compound = _rec(_JOINT_GENE, drug, _COMPOUND_IM)
            assert compound != _rec("TPMT", drug, _SINGLE_IM), drug
            assert compound != _rec("NUDT15", drug, _SINGLE_IM), drug


class TestJointResolution:
    """generate_prescribing_alerts applies CPIC's joint (TPMT, NUDT15) key."""

    def test_both_im_collapses_to_single_compound_alert(self, reference_engine: sa.Engine) -> None:
        results = [
            _result("TPMT", _SINGLE_IM, "*1/*3A"),
            _result("NUDT15", _SINGLE_IM, "*1/*3"),
        ]
        alerts = generate_prescribing_alerts(results, reference_engine)

        # The two conflicting single-gene thiopurine IM alerts are gone.
        leftover_single = [
            a
            for a in alerts
            if a.gene in ("TPMT", "NUDT15")
            and a.drug in _THIOPURINES
            and a.phenotype == _SINGLE_IM
        ]
        assert leftover_single == []

        # Exactly one joint compound-IM alert per thiopurine, all at 20-50%.
        compound = [a for a in alerts if a.gene == _JOINT_GENE]
        assert {a.drug for a in compound} == set(_THIOPURINES)
        assert len(compound) == len(_THIOPURINES)
        for alert in compound:
            assert alert.phenotype == _COMPOUND_IM
            assert "20-50%" in alert.recommendation
            assert "TPMT *1/*3A + NUDT15 *1/*3" == alert.diplotype

    def test_partial_component_provenance_merged_into_joint_alert(
        self, reference_engine: sa.Engine
    ) -> None:
        """A partial component's uncertainty (confidence note, indeterminate
        alleles and their loci) must survive into the merged joint alert so the
        joint recommendation does not lose the reasons behind its uncertainty."""
        tpmt = _result("TPMT", _SINGLE_IM, "*1/*3A")
        nudt15 = StarAlleleResult(
            gene="NUDT15",
            allele1="*1",
            allele2="*3",
            diplotype="*1/*3",
            phenotype=_SINGLE_IM,
            call_confidence=CallConfidence.PARTIAL,
            confidence_note="NUDT15 non-SNV alleles could not be excluded.",
            indeterminate_alleles=["*6"],
            indeterminate_allele_rsids={"*6": ["rs746071566"]},
        )
        alerts = generate_prescribing_alerts([tpmt, nudt15], reference_engine)

        compound = [a for a in alerts if a.gene == _JOINT_GENE]
        assert compound
        for alert in compound:
            assert alert.call_confidence == CallConfidence.PARTIAL
            assert "*6" in alert.indeterminate_alleles
            assert alert.indeterminate_allele_rsids.get("*6") == ["rs746071566"]
            assert "NUDT15 non-SNV alleles could not be excluded." in alert.confidence_note

    def test_single_gene_im_unchanged_when_other_gene_normal(
        self, reference_engine: sa.Engine
    ) -> None:
        results = [
            _result("TPMT", _SINGLE_IM, "*1/*3A"),
            _result("NUDT15", "Normal Metabolizer", "*1/*1"),
        ]
        alerts = generate_prescribing_alerts(results, reference_engine)

        assert [a for a in alerts if a.gene == _JOINT_GENE] == []
        tpmt_im = {a.drug for a in alerts if a.gene == "TPMT" and a.phenotype == _SINGLE_IM}
        assert tpmt_im == set(_THIOPURINES)

    def test_nudt15_insufficient_leaves_only_tpmt_alert(self, reference_engine: sa.Engine) -> None:
        """The live array case: NUDT15 is uncallable (Insufficient) on 23andMe /
        AncestryDNA, so it never produces an alert and the joint collapse must not
        fire — today's single-gene TPMT behaviour is preserved."""
        results = [
            _result("TPMT", _SINGLE_IM, "*1/*3A"),
            _result("NUDT15", _SINGLE_IM, "*1/*3", CallConfidence.INSUFFICIENT),
        ]
        alerts = generate_prescribing_alerts(results, reference_engine)

        assert [a for a in alerts if a.gene == _JOINT_GENE] == []
        assert [a for a in alerts if a.gene == "NUDT15"] == []
        tpmt_im = {a.drug for a in alerts if a.gene == "TPMT" and a.phenotype == _SINGLE_IM}
        assert tpmt_im == set(_THIOPURINES)

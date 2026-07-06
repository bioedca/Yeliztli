"""Unit tests for the SW-E4 medication-safety report building blocks.

Covers the pieces the consolidated drug-centric report
(``GET /api/analysis/pharma/report``) depends on:

  - ``classify_actionability`` — coarse routine/actionable/indeterminate labelling
    of a CPIC prescribing recommendation (presentation aid only; never clinical).
  - ``StarAlleleResult.coverage_assessed`` — SNP defining-position coverage.
  - ``store_prescribing_alerts`` persists a ``coverage`` block in ``detail_json``.

The HTTP-level report tests live in ``test_pharma_api.py``.
"""

from __future__ import annotations

import json

import sqlalchemy as sa

from backend.analysis.pharmacogenomics import (
    ACTIONABILITY_ACTIONABLE,
    ACTIONABILITY_INDETERMINATE,
    ACTIONABILITY_ROUTINE,
    CallConfidence,
    PrescribingAlert,
    StarAlleleResult,
    classify_actionability,
    store_prescribing_alerts,
)
from backend.db.sample_schema import create_sample_tables
from backend.db.tables import findings

_CYP2C9_WARFARIN_ALGORITHM_RECOMMENDATIONS = (
    "Use a validated warfarin pharmacogenetic dosing algorithm with VKORC1 and "
    "clinical factors; CYP2C9 status is one input and not a standalone percent "
    "dose rule.",
    "Use a validated warfarin pharmacogenetic dosing algorithm with VKORC1 and "
    "clinical factors; CYP2C9 poor-metabolizer status is not a standalone "
    "percent dose rule and may support alternative anticoagulant review.",
)

# ═══════════════════════════════════════════════════════════════════════
# classify_actionability
# ═══════════════════════════════════════════════════════════════════════


class TestClassifyActionability:
    def test_label_recommended_is_routine(self):
        assert classify_actionability("Use label-recommended dosing.") == ACTIONABILITY_ROUTINE

    def test_label_recommended_algorithm_is_routine(self):
        assert (
            classify_actionability("Use label-recommended dosing algorithm.")
            == ACTIONABILITY_ROUTINE
        )

    def test_age_weight_specific_label_is_routine(self):
        assert (
            classify_actionability("Use label-recommended age- or weight-specific dosing.")
            == ACTIONABILITY_ROUTINE
        )

    def test_avoid_is_actionable(self):
        assert (
            classify_actionability("Avoid codeine use. Alternative analgesics recommended.")
            == ACTIONABILITY_ACTIONABLE
        )

    def test_consider_alternative_is_actionable(self):
        assert (
            classify_actionability("Consider alternative antiplatelet therapy.")
            == ACTIONABILITY_ACTIONABLE
        )

    def test_reduce_dose_is_actionable(self):
        assert (
            classify_actionability("Reduce initial dose by 25-50%. Use pharmacogenetic algorithm.")
            == ACTIONABILITY_ACTIONABLE
        )

    def test_increase_and_monitor_is_actionable(self):
        # CYP3A5 expresser / tacrolimus: phenotype is "Normal Metabolizer" but the
        # recommendation IS actionable — recommendation-driven (not phenotype-driven)
        # classification gets this right.
        rec = "Increase starting dose by 1.5-2x. Monitor trough concentrations."
        assert classify_actionability(rec) == ACTIONABILITY_ACTIONABLE

    def test_standard_dosing_plus_monitoring_is_actionable(self):
        # "monitor" is the only action marker here. Without it, the routine
        # "standard...dosing" marker would incorrectly demote this recommendation.
        rec = "Initiate at standard label-recommended dosing; monitor for myelosuppression."
        assert classify_actionability(rec) == ACTIONABILITY_ACTIONABLE

    def test_action_verb_beats_routine_marker(self):
        # A routine marker AND an action verb in the same recommendation → actionable.
        assert (
            classify_actionability("Reduce dose; otherwise use label-recommended dosing.")
            == ACTIONABILITY_ACTIONABLE
        )

    def test_unknown_phrasing_defaults_actionable(self):
        # No routine marker and no recognized action verb: fail toward attention.
        assert (
            classify_actionability("Genotype-guided therapy per institutional protocol.")
            == ACTIONABILITY_ACTIONABLE
        )

    def test_cyp2c9_warfarin_algorithm_texts_are_actionable(self):
        for recommendation in _CYP2C9_WARFARIN_ALGORITHM_RECOMMENDATIONS:
            assert classify_actionability(recommendation) == ACTIONABILITY_ACTIONABLE
            assert "25-50%" not in recommendation
            assert "50-75%" not in recommendation

    def test_none_is_indeterminate(self):
        assert classify_actionability(None) == ACTIONABILITY_INDETERMINATE

    def test_empty_is_indeterminate(self):
        assert classify_actionability("") == ACTIONABILITY_INDETERMINATE

    def test_no_dose_adjustment_is_routine(self):
        # "no dose adjustment" embeds the action substring "adjust"; the negation
        # guard must keep it routine, not actionable.
        assert classify_actionability("No dose adjustment necessary.") == ACTIONABILITY_ROUTINE

    def test_no_recommended_dose_change_is_routine(self):
        assert (
            classify_actionability("No recommended dose change for this genotype.")
            == ACTIONABILITY_ROUTINE
        )

    def test_negation_plus_real_action_is_actionable(self):
        # A negated no-change phrase AND a genuine action verb elsewhere → actionable.
        assert (
            classify_actionability("No dose adjustment initially; reduce if intolerant.")
            == ACTIONABILITY_ACTIONABLE
        )


# ═══════════════════════════════════════════════════════════════════════
# StarAlleleResult.coverage_assessed
# ═══════════════════════════════════════════════════════════════════════


class TestCoverageAssessed:
    def _result(self, **kwargs) -> StarAlleleResult:
        base = dict(gene="CYP2C19", allele1="*1", allele2="*1", diplotype="*1/*1")
        base.update(kwargs)
        return StarAlleleResult(**base)

    def test_all_assessed(self):
        r = self._result(defining_rsid_count=3)
        assert r.coverage_assessed == 3

    def test_missing_reduces_coverage(self):
        r = self._result(defining_rsid_count=4, missing_rsids={"rs1", "rs2"})
        assert r.coverage_assessed == 2

    def test_uncalled_reduces_coverage(self):
        r = self._result(defining_rsid_count=4, uncalled_rsids={"rs9"})
        assert r.coverage_assessed == 3

    def test_missing_and_uncalled_union(self):
        # Disjoint sets: assessed = 5 - 1 - 1 = 3.
        r = self._result(
            defining_rsid_count=5,
            missing_rsids={"rs1"},
            uncalled_rsids={"rs2"},
        )
        assert r.coverage_assessed == 3

    def test_never_negative(self):
        r = self._result(defining_rsid_count=1, missing_rsids={"rs1", "rs2"})
        assert r.coverage_assessed == 0

    def test_zero_defining_positions(self):
        r = self._result(defining_rsid_count=0)
        assert r.coverage_assessed == 0


# ═══════════════════════════════════════════════════════════════════════
# store_prescribing_alerts persists coverage
# ═══════════════════════════════════════════════════════════════════════


def _make_sample_engine() -> sa.Engine:
    engine = sa.create_engine("sqlite://")
    create_sample_tables(engine)
    return engine


class TestStoreCoveragePersistence:
    def test_coverage_block_persisted(self):
        sample = _make_sample_engine()
        alerts = [
            PrescribingAlert(
                gene="CYP2C19",
                drug="clopidogrel",
                diplotype="*1/*2",
                phenotype="Intermediate Metabolizer",
                recommendation="Consider alternative antiplatelet therapy.",
                classification="A",
                guideline_url="https://cpicpgx.org/",
                call_confidence=CallConfidence.COMPLETE,
                confidence_note="All defining positions assessed.",
                evidence_level=4,
                involved_rsids=["rs4244285"],
                coverage_assessed=3,
                coverage_total=4,
            ),
        ]
        assert store_prescribing_alerts(alerts, sample) == 1

        with sample.connect() as conn:
            row = conn.execute(sa.select(findings)).first()
        detail = json.loads(row.detail_json)
        assert detail["coverage"] == {"assessed": 3, "total": 4}

    def test_coverage_defaults_zero(self):
        # PrescribingAlert without explicit coverage still emits a coverage block
        # (0/0) so the report has a consistent shape.
        sample = _make_sample_engine()
        alerts = [
            PrescribingAlert(
                gene="TPMT",
                drug="azathioprine",
                diplotype="*1/*1",
                phenotype="Normal Metabolizer",
                recommendation="Use label-recommended dosing.",
                classification="A",
                guideline_url="https://cpicpgx.org/",
                call_confidence=CallConfidence.COMPLETE,
                confidence_note="All defining positions assessed.",
                evidence_level=4,
            ),
        ]
        store_prescribing_alerts(alerts, sample)
        with sample.connect() as conn:
            row = conn.execute(sa.select(findings)).first()
        detail = json.loads(row.detail_json)
        assert detail["coverage"] == {"assessed": 0, "total": 0}

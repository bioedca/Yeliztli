"""Tests for evidence conflict detection (P2-07, T2-07, T2-08).

Tests the amber flag logic:
- Flag fires: ClinVar VUS/B/LB AND >=3 independent in-silico axes deleterious
  AND CADD >= 20
- No flag: ClinVar P/LP, ClinVar absent, or insufficient deleterious axes
"""

import pytest

from backend.annotation.dbnsfp import DbNSFPRecord, assess_ensemble
from backend.annotation.evidence_conflict import (
    EvidenceConflictResult,
    apply_evidence_conflicts,
    count_deleterious_tools,
    detect_evidence_conflict,
)

# ── Helpers ──────────────────────────────────────────────────────────────


def _make_variant(
    clinvar_significance: str | None = None,
    cadd_phred: float | None = None,
    sift_pred: str | None = None,
    sift_score: float | None = None,
    polyphen2_hsvar_pred: str | None = None,
    polyphen2_hsvar_score: float | None = None,
    revel: float | None = None,
    metasvm: float | None = None,
    metalr: float | None = None,
) -> dict:
    """Create a minimal variant dict for conflict testing."""
    return {
        "rsid": "rs12345",
        "clinvar_significance": clinvar_significance,
        "cadd_phred": cadd_phred,
        "sift_pred": sift_pred,
        "sift_score": sift_score,
        "polyphen2_hsvar_pred": polyphen2_hsvar_pred,
        "polyphen2_hsvar_score": polyphen2_hsvar_score,
        "revel": revel,
        "metasvm": metasvm,
        "metalr": metalr,
    }


def _make_conflict_variant(
    clinvar_significance: str = "Uncertain significance",
    cadd_phred: float = 28.4,
) -> dict:
    """Create a variant that should fire the conflict flag.

    All four independent in-silico axes predict deleterious, CADD >= 20.
    """
    return _make_variant(
        clinvar_significance=clinvar_significance,
        cadd_phred=cadd_phred,
        sift_pred="D",
        polyphen2_hsvar_pred="D",
        revel=0.85,
        metasvm=0.5,
    )


# ── T2-07: Flag fires for VUS + >=3 deleterious axes + CADD >= 20 ───────


class TestConflictFlagFires:
    """T2-07: Evidence conflict flag fires correctly."""

    def test_vus_all_axes_deleterious(self):
        """VUS + 4/4 axes deleterious + CADD 28.4 -> flag fires."""
        v = _make_conflict_variant()
        result = detect_evidence_conflict(v)
        assert result.flag is True
        assert result.deleterious_count == 4
        assert result.cadd_phred == 28.4

    def test_vus_exactly_3_axes_deleterious(self):
        """VUS + exactly 3/4 axes deleterious + CADD >= 20 -> flag fires."""
        v = _make_variant(
            clinvar_significance="Uncertain significance",
            cadd_phred=25.0,
            sift_pred="D",  # deleterious
            polyphen2_hsvar_pred="D",  # deleterious
            revel=0.3,  # not deleterious
            metasvm=-0.5,  # not deleterious
        )
        result = detect_evidence_conflict(v)
        # SIFT + PolyPhen + CADD (>= 20) = exactly 3 deleterious axes
        assert result.flag is True
        assert result.deleterious_count == 3

    def test_benign_with_deleterious_predictions(self):
        """Benign + >=3 axes deleterious + CADD >= 20 -> flag fires."""
        v = _make_conflict_variant(clinvar_significance="Benign")
        result = detect_evidence_conflict(v)
        assert result.flag is True

    def test_likely_benign_with_deleterious_predictions(self):
        """Likely benign + >=3 axes deleterious + CADD >= 20 -> flag fires."""
        v = _make_conflict_variant(clinvar_significance="Likely benign")
        result = detect_evidence_conflict(v)
        assert result.flag is True

    def test_vus_underscore_variant(self):
        """Uncertain_significance (underscore) also triggers."""
        v = _make_conflict_variant(clinvar_significance="Uncertain_significance")
        result = detect_evidence_conflict(v)
        assert result.flag is True

    def test_likely_benign_underscore_variant(self):
        """Likely_benign (underscore) also triggers."""
        v = _make_conflict_variant(clinvar_significance="Likely_benign")
        result = detect_evidence_conflict(v)
        assert result.flag is True


# ── T2-08: Flag does NOT fire for P/LP ──────────────────────────────────


class TestConflictFlagDoesNotFire:
    """T2-08: Evidence conflict flag does NOT fire for P/LP or absent."""

    def test_pathogenic_no_flag(self):
        """ClinVar Pathogenic + all axes deleterious -> no flag."""
        v = _make_conflict_variant(clinvar_significance="Pathogenic")
        result = detect_evidence_conflict(v)
        assert result.flag is False

    def test_likely_pathogenic_no_flag(self):
        """ClinVar Likely pathogenic + all axes deleterious -> no flag."""
        v = _make_conflict_variant(clinvar_significance="Likely pathogenic")
        result = detect_evidence_conflict(v)
        assert result.flag is False

    def test_likely_pathogenic_underscore_no_flag(self):
        """ClinVar Likely_pathogenic (underscore) → no flag."""
        v = _make_conflict_variant(clinvar_significance="Likely_pathogenic")
        result = detect_evidence_conflict(v)
        assert result.flag is False

    def test_pathogenic_likely_pathogenic_no_flag(self):
        """ClinVar combined P/LP → no flag."""
        v = _make_conflict_variant(clinvar_significance="Pathogenic/Likely pathogenic")
        result = detect_evidence_conflict(v)
        assert result.flag is False

    def test_absent_clinvar_no_flag(self):
        """No ClinVar (None) → no flag regardless of in-silico."""
        v = _make_conflict_variant(clinvar_significance=None)
        result = detect_evidence_conflict(v)
        assert result.flag is False

    def test_vus_only_2_axes_deleterious(self):
        """VUS + only 2 axes deleterious -> no flag (too weak)."""
        v = _make_variant(
            clinvar_significance="Uncertain significance",
            cadd_phred=25.0,
            sift_pred="D",  # only this one is deleterious
            polyphen2_hsvar_pred="B",  # benign
            revel=0.2,  # not deleterious
            metasvm=-1.0,  # not deleterious
        )
        result = detect_evidence_conflict(v)
        # CADD counts as deleterious = 2 axes total, still < 3
        assert result.flag is False

    def test_vus_cadd_below_threshold(self):
        """VUS + 3 non-CADD axes deleterious but CADD < 20 -> no flag."""
        v = _make_variant(
            clinvar_significance="Uncertain significance",
            cadd_phred=18.0,  # below threshold
            sift_pred="D",
            polyphen2_hsvar_pred="D",
            revel=0.85,
            metasvm=0.5,
        )
        result = detect_evidence_conflict(v)
        # 3 non-CADD axes are deleterious but CADD gate fails
        assert result.flag is False

    def test_vus_no_in_silico_data(self):
        """VUS but no in-silico data at all → no flag."""
        v = _make_variant(clinvar_significance="Uncertain significance")
        result = detect_evidence_conflict(v)
        assert result.flag is False
        assert result.deleterious_count == 0

    def test_risk_factor_no_flag(self):
        """ClinVar 'Risk factor' is not VUS/B/LB → no flag."""
        v = _make_conflict_variant(clinvar_significance="Risk factor")
        result = detect_evidence_conflict(v)
        assert result.flag is False

    def test_drug_response_no_flag(self):
        """ClinVar 'Drug response' is not VUS/B/LB → no flag."""
        v = _make_conflict_variant(clinvar_significance="Drug response")
        result = detect_evidence_conflict(v)
        assert result.flag is False

    def test_conflicting_interpretations_no_flag(self):
        """ClinVar 'Conflicting interpretations' is not VUS/B/LB → no flag."""
        v = _make_conflict_variant(
            clinvar_significance="Conflicting interpretations of pathogenicity"
        )
        result = detect_evidence_conflict(v)
        assert result.flag is False

    def test_cadd_none_no_flag(self):
        """VUS + 3 axes deleterious but CADD is None -> no flag."""
        v = _make_variant(
            clinvar_significance="Uncertain significance",
            cadd_phred=None,
            sift_pred="D",
            polyphen2_hsvar_pred="D",
            revel=0.85,
            metasvm=0.5,
        )
        result = detect_evidence_conflict(v)
        assert result.flag is False


# ── count_deleterious_tools ─────────────────────────────────────────────


class TestCountDeleteriousTools:
    """Unit tests for canonical in-silico axis counting."""

    def test_all_deleterious(self):
        v = _make_variant(
            cadd_phred=30.0,
            sift_pred="D",
            polyphen2_hsvar_pred="D",
            revel=0.8,
            metasvm=0.5,
        )
        del_count, total = count_deleterious_tools(v)
        assert del_count == 4
        assert total == 4

    def test_none_deleterious(self):
        v = _make_variant(
            cadd_phred=5.0,
            sift_pred="T",  # tolerated
            polyphen2_hsvar_pred="B",  # benign
            revel=0.1,
            metasvm=-1.0,
        )
        del_count, total = count_deleterious_tools(v)
        assert del_count == 0
        assert total == 4

    def test_no_data(self):
        v = _make_variant()
        del_count, total = count_deleterious_tools(v)
        assert del_count == 0
        assert total == 0

    def test_sift_score_fallback(self):
        """SIFT uses score < 0.05 when pred is absent."""
        v = _make_variant(sift_score=0.01)
        del_count, total = count_deleterious_tools(v)
        assert del_count == 1
        assert total == 1

    def test_sift_score_tolerated(self):
        """SIFT score >= 0.05 → tolerated."""
        v = _make_variant(sift_score=0.2)
        del_count, total = count_deleterious_tools(v)
        assert del_count == 0
        assert total == 1

    def test_polyphen_score_fallback(self):
        """PolyPhen uses score > 0.909 when pred is absent."""
        v = _make_variant(polyphen2_hsvar_score=0.95)
        del_count, total = count_deleterious_tools(v)
        assert del_count == 1
        assert total == 1

    def test_polyphen_score_benign(self):
        """PolyPhen score ≤ 0.909 → not deleterious."""
        v = _make_variant(polyphen2_hsvar_score=0.5)
        del_count, total = count_deleterious_tools(v)
        assert del_count == 0
        assert total == 1

    def test_revel_boundary(self):
        """REVEL = 0.5 exactly -> deleterious in the shared F24/F25 rule."""
        v = _make_variant(revel=0.5)
        del_count, _ = count_deleterious_tools(v)
        assert del_count == 1

    def test_metasvm_boundary(self):
        """MetaSVM = 0 exactly → not deleterious (must be > 0)."""
        v = _make_variant(metasvm=0.0)
        del_count, _ = count_deleterious_tools(v)
        assert del_count == 0

    def test_cadd_boundary(self):
        """CADD = 20 exactly -> deleterious in the shared F24/F25 rule."""
        v = _make_variant(cadd_phred=20.0)
        del_count, _ = count_deleterious_tools(v)
        assert del_count == 1

    def test_count_matches_dbnsfp_assess_ensemble(self):
        """The conflict detector cannot drift from dbNSFP F24/F25 counting."""
        v = _make_variant(
            cadd_phred=20.0,
            sift_score=0.01,
            polyphen2_hsvar_score=0.95,
            revel=0.5,
            metasvm=-1.0,
            metalr=0.9,
        )
        annot = DbNSFPRecord(
            rsid="rs12345",
            chrom="1",
            pos=12345,
            ref="G",
            alt="A",
            cadd_phred=20.0,
            sift_score=0.01,
            polyphen2_hsvar_score=0.95,
            revel=0.5,
            metasvm=-1.0,
            metalr=0.9,
        )
        assert count_deleterious_tools(v) == assess_ensemble(annot) == (4, 4)

    def test_attribute_access(self):
        """Supports attribute-style access (e.g., SQLAlchemy Row)."""

        class FakeRow:
            sift_pred = "D"
            sift_score = None
            polyphen2_hsvar_pred = "D"
            polyphen2_hsvar_score = None
            cadd_phred = 25.0
            revel = 0.8
            metasvm = 0.3
            metalr = None

        del_count, total = count_deleterious_tools(FakeRow())
        assert del_count == 4
        assert total == 4


# ── apply_evidence_conflicts (batch helper) ─────────────────────────────


class TestApplyEvidenceConflicts:
    """Tests for the batch application function."""

    def test_sets_flag_on_conflict_variant(self):
        variants = [_make_conflict_variant()]
        apply_evidence_conflicts(variants)
        assert variants[0]["evidence_conflict"] is True

    def test_clears_flag_on_pathogenic_variant(self):
        variants = [_make_conflict_variant(clinvar_significance="Pathogenic")]
        apply_evidence_conflicts(variants)
        assert variants[0]["evidence_conflict"] is False

    def test_mixed_batch(self):
        variants = [
            _make_conflict_variant(),  # should flag
            _make_conflict_variant(clinvar_significance="Pathogenic"),  # no flag
            _make_variant(clinvar_significance=None),  # no flag
            _make_conflict_variant(clinvar_significance="Benign"),  # should flag
        ]
        apply_evidence_conflicts(variants)
        assert variants[0]["evidence_conflict"] is True
        assert variants[1]["evidence_conflict"] is False
        assert variants[2]["evidence_conflict"] is False
        assert variants[3]["evidence_conflict"] is True

    def test_empty_list(self):
        variants: list[dict] = []
        result = apply_evidence_conflicts(variants)
        assert result == []


# ── EvidenceConflictResult dataclass ────────────────────────────────────


class TestEvidenceConflictResult:
    """Tests for the result dataclass."""

    def test_frozen(self):
        r = EvidenceConflictResult(
            flag=True,
            deleterious_count=4,
            total_tools_assessed=4,
            cadd_phred=28.4,
            clinvar_significance="Uncertain significance",
        )
        with pytest.raises(AttributeError):
            r.flag = False  # type: ignore[misc]

    def test_fields(self):
        r = EvidenceConflictResult(
            flag=True,
            deleterious_count=4,
            total_tools_assessed=4,
            cadd_phred=28.4,
            clinvar_significance="Uncertain significance",
        )
        assert r.flag is True
        assert r.deleterious_count == 4
        assert r.total_tools_assessed == 4
        assert r.cadd_phred == 28.4
        assert r.clinvar_significance == "Uncertain significance"

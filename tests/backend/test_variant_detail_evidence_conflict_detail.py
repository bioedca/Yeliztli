"""Regression tests for variant-detail evidence-conflict detail construction."""

from __future__ import annotations

from types import SimpleNamespace

from backend.api.routes.variant_detail import _build_evidence_conflict_detail


class TestEvidenceConflictDetailBuilder:
    def test_summary_uses_stored_f24_f25_axis_denominator(self):
        row = SimpleNamespace(
            clinvar_significance="Uncertain significance",
            clinvar_review_stars=1,
            clinvar_accession="VCV000012345",
            evidence_conflict=True,
            cadd_phred=28.4,
            sift_score=0.01,
            sift_pred="D",
            polyphen2_hsvar_score=0.95,
            polyphen2_hsvar_pred="D",
            revel=0.75,
            metasvm=0.8,
            metalr=0.7,
            deleterious_count=4,
            deleterious_total_assessed=4,
        )

        detail = _build_evidence_conflict_detail(row)

        assert detail.deleterious_count == 4
        assert detail.total_tools_assessed == 4
        assert "4 of 4 independent in-silico axes predict deleterious" in detail.summary
        assert detail.deleterious_tools == [
            "SIFT",
            "PolyPhen-2",
            "CADD",
            "REVEL",
            "MetaSVM",
            "MetaLR",
        ]

    def test_summary_falls_back_to_same_axis_counter_when_counts_missing(self):
        row = SimpleNamespace(
            clinvar_significance="Uncertain significance",
            clinvar_review_stars=1,
            clinvar_accession="VCV000012345",
            evidence_conflict=True,
            cadd_phred=28.4,
            sift_score=0.01,
            sift_pred="D",
            polyphen2_hsvar_score=0.95,
            polyphen2_hsvar_pred="D",
            revel=0.75,
            metasvm=0.8,
            metalr=0.7,
            deleterious_count=None,
            deleterious_total_assessed=None,
        )

        detail = _build_evidence_conflict_detail(row)

        assert detail.deleterious_count == 4
        assert detail.total_tools_assessed == 4
        assert "4 of 4 independent in-silico axes predict deleterious" in detail.summary

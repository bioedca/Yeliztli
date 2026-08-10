"""Tests for unified findings API (P3-39).

Covers:
- GET /api/analysis/findings — list all findings with filters
- GET /api/analysis/findings/summary — per-module counts + high confidence
- GET /api/analysis/findings/{id}/svg — SVG image retrieval
"""

from __future__ import annotations

import json
from collections.abc import Generator
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from backend.api.routes.findings import _row_to_response
from backend.config import Settings
from backend.db.connection import reset_registry
from backend.db.sample_schema import create_sample_tables
from backend.db.tables import findings, reference_metadata, samples

# ── Fixtures ────────────────────────────────────────────────────────


@pytest.fixture
def tmp_data_dir(tmp_path: Path) -> Path:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "samples").mkdir()
    return data_dir


@pytest.fixture
def findings_client(
    tmp_data_dir: Path,
) -> Generator[TestClient, None, None]:
    """FastAPI test client with a sample pre-seeded with findings."""
    settings = Settings(data_dir=tmp_data_dir, wal_mode=False)

    # Create reference.db with samples table
    ref_path = settings.reference_db_path
    ref_engine = sa.create_engine(f"sqlite:///{ref_path}")
    reference_metadata.create_all(ref_engine)

    # Create sample DB file on disk
    sample_db_path = tmp_data_dir / "samples" / "sample_1.db"
    sample_engine = sa.create_engine(f"sqlite:///{sample_db_path}")
    create_sample_tables(sample_engine)

    # Register sample in reference DB
    with ref_engine.begin() as conn:
        conn.execute(
            samples.insert().values(
                id=1,
                name="Test Sample",
                db_path="samples/sample_1.db",
                file_format="v5",
                file_hash="abc123",
            )
        )

    # Seed findings individually (different columns per row)
    seed_findings = [
        {
            "module": "cancer",
            "category": "monogenic_variant",
            "evidence_level": 4,
            "gene_symbol": "BRCA1",
            "rsid": "rs80357906",
            "finding_text": "BRCA1 Pathogenic",
            "clinvar_significance": "Pathogenic",
            "pmid_citations": json.dumps(["12345678"]),
            "detail_json": json.dumps({"syndromes": ["HBOC"]}),
            "provenance": json.dumps(
                {
                    "pipeline_version": "0.2.0",
                    "pipeline_genome_build": "GRCh37",
                    "sources": {"clinvar": {"version": "2026-05-01", "genome_build": "GRCh37"}},
                    "variation_ids": {"rsid": "rs80357906"},
                    "annotation_coverage": 0b0000110,
                    "annotation_coverage_sources": ["ClinVar", "gnomAD"],
                }
            ),
        },
        {
            "module": "pharmacogenomics",
            "category": "prescribing_alert",
            "evidence_level": 4,
            "gene_symbol": "CYP2C19",
            "diplotype": "*1/*2",
            "metabolizer_status": "Intermediate Metabolizer",
            "drug": "clopidogrel",
            "finding_text": "CYP2C19 *1/*2 IM",
        },
        {
            "module": "cardiovascular",
            "category": "monogenic_variant",
            "evidence_level": 4,
            "gene_symbol": "LDLR",
            "finding_text": "LDLR pathogenic",
        },
        {
            "module": "nutrigenomics",
            "category": "pathway_summary",
            "evidence_level": 2,
            "finding_text": "Folate Metabolism - Elevated",
            "pathway": "Folate Metabolism",
            "pathway_level": "Elevated",
        },
        {
            "module": "ancestry",
            "category": "biogeographic",
            "evidence_level": 2,
            "finding_text": "82% European ancestry",
        },
        {
            "module": "carrier_status",
            "category": "monogenic_variant",
            "evidence_level": 3,
            "gene_symbol": "CFTR",
            "finding_text": "CFTR carrier",
        },
        {
            "module": "allergy",
            "category": "drug_hypersensitivity",
            "evidence_level": 3,
            "gene_symbol": "HLA-B",
            "finding_text": "HLA-B hypersensitivity alert",
        },
        {
            "module": "gene_health",
            "category": "disease_risk",
            "evidence_level": 3,
            "gene_symbol": "APOE",
            "finding_text": "Alzheimer's disease risk (APOE ε4)",
            "related_module": "apoe",
            "related_finding_id": 1,
        },
    ]
    with sample_engine.begin() as conn:
        for f in seed_findings:
            conn.execute(findings.insert().values(**f))

    ref_engine.dispose()
    sample_engine.dispose()

    with (
        patch("backend.main.get_settings", return_value=settings),
        patch("backend.db.connection.get_settings", return_value=settings),
    ):
        reset_registry()

        from backend.main import create_app

        app = create_app()
        with TestClient(app) as tc:
            yield tc

        reset_registry()


# ── List findings tests ─────────────────────────────────────────────

# One well-formed persisted ROH segment, matching what `store_roh_findings`
# writes. Shared so the metric-consistency cases below differ only in the one
# relation under test.
_SEG = {
    "chrom": "1",
    "start": 1_000_000,
    "end": 7_200_000,
    "length_kb": 6200.0,
    "n_snps": 620,
}


class TestListFindings:
    def test_list_all_findings(self, findings_client):
        resp = findings_client.get("/api/analysis/findings?sample_id=1")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 8

    def test_sorted_by_evidence_level_desc(self, findings_client):
        resp = findings_client.get("/api/analysis/findings?sample_id=1")
        data = resp.json()
        levels = [f["evidence_level"] for f in data]
        assert levels == sorted(levels, reverse=True)
        modules_by_level = {
            level: [f["module"] for f in data if f["evidence_level"] == level]
            for level in {f["evidence_level"] for f in data}
        }
        assert modules_by_level[4] == ["cancer", "cardiovascular", "pharmacogenomics"]
        assert modules_by_level[3] == ["allergy", "carrier_status", "gene_health"]
        assert modules_by_level[2] == ["ancestry", "nutrigenomics"]

    def test_filter_by_module(self, findings_client):
        resp = findings_client.get("/api/analysis/findings?sample_id=1&module=cancer")
        data = resp.json()
        assert len(data) == 1
        assert data[0]["module"] == "cancer"

    def test_filter_by_min_stars(self, findings_client):
        resp = findings_client.get("/api/analysis/findings?sample_id=1&min_stars=3")
        data = resp.json()
        assert len(data) == 6
        for f in data:
            assert f["evidence_level"] >= 3

    def test_filter_by_category(self, findings_client):
        resp = findings_client.get("/api/analysis/findings?sample_id=1&category=prescribing_alert")
        data = resp.json()
        assert len(data) == 1
        assert data[0]["category"] == "prescribing_alert"

    def test_limit_bounds_response_highest_evidence_first(self, findings_client):
        # #1303: an unbounded fetch returns tens of thousands of rows for a real
        # sample; a bounded page must return the highest-evidence findings first.
        resp = findings_client.get("/api/analysis/findings?sample_id=1&limit=3")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 3
        assert [f["module"] for f in data] == ["cancer", "cardiovascular", "pharmacogenomics"]
        assert all(f["evidence_level"] == 4 for f in data)

    def test_offset_pages_through_results(self, findings_client):
        page2 = findings_client.get("/api/analysis/findings?sample_id=1&limit=3&offset=3").json()
        assert [f["module"] for f in page2] == ["allergy", "carrier_status", "gene_health"]
        page3 = findings_client.get("/api/analysis/findings?sample_id=1&limit=3&offset=6").json()
        assert [f["module"] for f in page3] == ["ancestry", "nutrigenomics"]
        # Short last page (< limit) signals to the caller that no more remain.
        assert len(page3) == 2

    def test_limit_composes_with_filters(self, findings_client):
        data = findings_client.get("/api/analysis/findings?sample_id=1&min_stars=3&limit=2").json()
        assert len(data) == 2
        assert all(f["evidence_level"] >= 3 for f in data)

    def test_no_limit_returns_all_legacy(self, findings_client):
        # Omitting limit preserves return-everything for non-UI callers.
        data = findings_client.get("/api/analysis/findings?sample_id=1").json()
        assert len(data) == 8

    def test_invalid_sample_returns_404(self, findings_client):
        resp = findings_client.get("/api/analysis/findings?sample_id=999")
        assert resp.status_code == 404

    def test_finding_has_parsed_pmids(self, findings_client):
        resp = findings_client.get("/api/analysis/findings?sample_id=1&module=cancer")
        data = resp.json()
        assert data[0]["pmid_citations"] == ["12345678"]

    def test_finding_has_parsed_detail(self, findings_client):
        resp = findings_client.get("/api/analysis/findings?sample_id=1&module=cancer")
        data = resp.json()
        assert data[0]["detail"]["syndromes"] == ["HBOC"]

    def test_withheld_prs_score_stays_null_in_generic_api_response(self):
        response = _row_to_response(
            SimpleNamespace(
                id=99,
                module="cancer",
                category="prs",
                evidence_level=2,
                gene_symbol=None,
                rsid=None,
                finding_text=(
                    "Breast Cancer PRS: population percentile not reported — "
                    "score lacks a validated reference distribution"
                ),
                phenotype=None,
                conditions=None,
                zygosity=None,
                clinvar_significance=None,
                diplotype=None,
                metabolizer_status=None,
                drug=None,
                haplogroup=None,
                prs_score=None,
                prs_percentile=None,
                pathway=None,
                pathway_level=None,
                svg_path=None,
                pmid_citations=None,
                detail_json=json.dumps(
                    {
                        "calibrated": False,
                        "percentile": None,
                        "z_score": None,
                    }
                ),
                provenance=None,
                related_module=None,
                related_finding_id=None,
                created_at=None,
            )
        )

        assert response.prs_score is None
        assert response.prs_percentile is None
        assert response.detail["percentile"] is None
        assert response.detail["z_score"] is None

    def _roh_row(self, *, finding_text: str, detail: dict):
        return SimpleNamespace(
            id=101,
            module="roh",
            category="autozygosity",
            evidence_level=1,
            gene_symbol=None,
            rsid=None,
            finding_text=finding_text,
            phenotype=None,
            conditions=None,
            zygosity=None,
            clinvar_significance=None,
            diplotype=None,
            metabolizer_status=None,
            drug=None,
            haplogroup=None,
            prs_score=None,
            prs_percentile=None,
            pathway=None,
            pathway_level=None,
            svg_path=None,
            pmid_citations=None,
            detail_json=json.dumps(detail),
            provenance=None,
            related_module=None,
            related_finding_id=None,
            created_at=None,
        )

    def test_legacy_roh_typical_text_is_corrected_in_generic_api(self):
        # #2177 — the unified findings explorer renders finding_text verbatim,
        # so a row persisted before the ROH evaluability gate would otherwise
        # keep presenting a "typical" negative for an unevaluable sample.
        response = _row_to_response(
            self._roh_row(
                finding_text=(
                    "No long runs of homozygosity were detected (FROH ≈ 0). "
                    "This is the typical result."
                ),
                detail={"froh": 0.0, "n_segments": 0, "autosomal_snps_used": 30},
            )
        )

        assert "typical result" not in response.finding_text.lower()
        assert "not assessed" in response.finding_text.lower()

    def test_evaluable_legacy_roh_row_scans_coverage_once(self, monkeypatch):
        # Narrative and detail were normalized independently, so each evaluated
        # the row and an evaluable legacy row triggered TWO full autosomal
        # scans per request — on the 600-700k-marker samples this module is
        # written for. Assert the scan count, not just the output.
        from backend.analysis import roh as roh_module

        calls = {"n": 0}
        real = roh_module._read_autosomal_states

        def counting(engine):
            calls["n"] += 1
            return real(engine)

        monkeypatch.setattr(roh_module, "_read_autosomal_states", counting)

        # The row must be genuinely EVALUABLE for this to bite: when it is
        # withheld, the first helper returns a corrected blob carrying an
        # `evaluable` key and the second short-circuits without scanning. The
        # double scan only occurs on the evaluable path, where the original
        # blob is returned unchanged.
        engine = sa.create_engine("sqlite://")
        create_sample_tables(engine)
        from backend.db.tables import raw_variants

        with engine.begin() as conn:
            conn.execute(
                sa.insert(raw_variants),
                [
                    {
                        "rsid": f"roh{i}",
                        "chrom": "1",
                        "pos": 1_000_000 + i * 20_000,
                        "genotype": "AG",
                    }
                    for i in range(200)
                ],
            )

        row = self._roh_row(
            finding_text="No long runs of homozygosity were detected (FROH ≈ 0).",
            # Legacy shape: a stored metric but no evaluable/count keys, which
            # is the case that has to consult the sample.
            detail={"froh": 0.0},
        )
        response = _row_to_response(row, engine)

        assert response.detail["froh"] == 0.0  # premise: the row IS evaluable
        assert calls["n"] == 1

    def test_roh_row_with_unreadable_detail_is_not_served_as_typical(self):
        # The generic builders set detail to None when detail_json will not
        # parse, so this is the path that reaches the non-dict branch — the
        # dedicated route never does, because its own try/except catches the
        # decode error first. A row whose state cannot be read must not keep
        # asserting its stored negative.
        row = self._roh_row(finding_text="placeholder", detail={})
        row.finding_text = (
            "No long runs of homozygosity were detected (FROH ≈ 0). This is the typical result."
        )
        row.detail_json = "{not valid json"

        response = _row_to_response(row)

        assert "typical result" not in response.finding_text.lower()
        assert "could not be read" in response.finding_text.lower()

    def test_legacy_roh_detail_withholds_the_measured_zero(self):
        # Correcting only the narrative would hand clients a withheld
        # conclusion next to the exact froh: 0.0 it withholds.
        response = _row_to_response(
            self._roh_row(
                finding_text="No long runs of homozygosity were detected (FROH ≈ 0).",
                detail={"froh": 0.0, "n_segments": 0, "autosomal_snps_used": 30},
            )
        )

        assert response.detail["froh"] is None
        assert response.detail["evaluable"] is False
        assert response.detail["indeterminate_reason"] == "insufficient_autosomal_markers"

    def test_withheld_roh_detail_drops_its_segments(self):
        # The generic findings path has the same contradiction to avoid: a
        # withheld detail blob must not keep concrete segment records beside
        # its nulled summary.
        response = _row_to_response(
            self._roh_row(
                finding_text="No long runs of homozygosity were detected (FROH ≈ 0).",
                detail={
                    "froh": 0.004,
                    "n_segments": 1,
                    "autosomal_snps_used": 30,
                    "segments": [
                        {
                            "chrom": "4",
                            "start": 1_000_000,
                            "end": 7_200_000,
                            "length_kb": 6200.0,
                            "n_snps": 620,
                        }
                    ],
                    "segments_truncated": True,
                },
            )
        )

        assert response.detail["froh"] is None
        assert response.detail["n_segments"] is None
        assert response.detail["segments"] == []
        assert response.detail["segments_truncated"] is False

    def test_evaluable_roh_detail_keeps_its_measured_value(self):
        response = _row_to_response(
            self._roh_row(
                finding_text="No long runs of homozygosity were detected (FROH ≈ 0).",
                detail={"froh": 0.0, "n_segments": 0, "autosomal_snps_used": 600_000},
            )
        )

        assert response.detail["froh"] == 0.0
        assert "indeterminate_reason" not in response.detail

    @pytest.mark.parametrize("blob", ["[]", "5", '"text"', "true"])
    def test_non_object_roh_detail_is_withheld_not_a_500(self, blob):
        # Valid JSON that is not an object parses to a list/int/str/bool and
        # reached `_withheld_detail`, which handed it straight back. That put a
        # non-dict into `FindingResponse.detail: dict | None`, so this route
        # raised a Pydantic error and 500'd on exactly the blobs the dedicated
        # ROH route already withholds -- the earlier fix normalised at that
        # route's parse site and so reached only one of the two representations.
        row = self._roh_row(finding_text="placeholder", detail={})
        row.finding_text = (
            "No long runs of homozygosity were detected (FROH ≈ 0). This is the typical result."
        )
        row.detail_json = blob

        response = _row_to_response(row)

        assert isinstance(response.detail, dict)
        assert response.detail["evaluable"] is False
        assert response.detail["indeterminate_reason"] == "detail_unavailable"
        assert response.detail["froh"] is None
        assert "typical result" not in response.finding_text.lower()

    @pytest.mark.parametrize(
        "companion",
        [
            {"segments": None},
            {"segments": "chr1:1-2"},
            {"segments": [{"chrom": "1", "start": 1, "end": 2, "length_kb": 1.0}]},
            {"segments": [{"chrom": 1, "start": 1, "end": 2, "length_kb": 1.0, "n_snps": 3}]},
            {"total_roh_kb": "lots"},
            {"longest_kb": []},
            {"n_segments": "many"},
            # Well-typed but impossible: every metric here is a length, a count
            # or a fraction, so none can be negative, and a NaN additionally has
            # no JSON form and can break serialization of the response serving
            # it. Type-checking alone vouched for all of these.
            {"total_roh_kb": -1},
            {"longest_kb": float("nan")},
            {"longest_kb": float("inf")},
            {"n_segments": -2},
            {"segments": [{"chrom": "1", "start": -1, "end": 2, "length_kb": 1.0, "n_snps": 3}]},
            {
                "segments": [
                    {
                        "chrom": "1",
                        "start": 1,
                        "end": 2,
                        "length_kb": float("nan"),
                        "n_snps": 3,
                    }
                ]
            },
            {"segments": [{"chrom": "1", "start": 1, "end": 2, "length_kb": -5.0, "n_snps": 3}]},
        ],
    )
    def test_unreadable_companion_metric_withholds_like_the_roh_route(self, companion):
        # Vouching on `froh` alone let the two read paths disagree about one
        # row. The dedicated ROH route materialises `segments` into response
        # models inside a `try`, so a blob holding `"segments": null` raised
        # there and was reported `detail_unavailable` -- while this path, which
        # only consults the verdict, kept serving the stored negative for that
        # very row. A row is vouched for on every path or none.
        detail = {"froh": 0.004, "autosomal_snps_used": 600_000, **companion}
        response = _row_to_response(
            self._roh_row(
                finding_text="No long runs of homozygosity were detected (FROH ≈ 0).",
                detail=detail,
            )
        )

        assert response.detail["evaluable"] is False
        assert response.detail["indeterminate_reason"] == "detail_unavailable"
        assert response.detail["froh"] is None
        assert "typical" not in response.finding_text.lower()

    def test_wellformed_companion_metrics_are_still_served(self):
        # Counterpart control for the parametrisation above: without it a guard
        # that rejected *every* companion blob would satisfy those cases while
        # withholding every real scan this module has ever recorded.
        response = _row_to_response(
            self._roh_row(
                finding_text="stored narrative",
                detail={
                    "froh": 0.004,
                    "autosomal_snps_used": 600_000,
                    "total_roh_kb": 6200.0,
                    "longest_kb": 6200.0,
                    "n_segments": 1,
                    "segments": [
                        {
                            "chrom": "1",
                            "start": 1_000_000,
                            "end": 7_200_000,
                            "length_kb": 6200.0,
                            "n_snps": 620,
                        }
                    ],
                },
            )
        )

        assert response.detail["froh"] == 0.004
        assert response.detail["n_segments"] == 1
        assert response.finding_text == "stored narrative"

    @pytest.mark.parametrize(
        ("label", "overrides"),
        [
            # the total is a sum over every segment, so it must cover the
            # listed ones -- a blob whose own segments outweigh its total
            (
                "total_below_the_listed_segments",
                {"n_segments": 2, "segments": [_SEG, _SEG], "total_roh_kb": 6200.0},
            ),
            (
                "total_above_a_complete_list",
                {"n_segments": 1, "segments": [_SEG], "total_roh_kb": 99_000.0},
            ),
            # the detector cannot emit a run with no length and no markers
            ("zero_length_segment", {"segments": [{**_SEG, "length_kb": 0.0, "n_snps": 0}]}),
            (
                "segment_below_the_emission_thresholds",
                {
                    "segments": [{**_SEG, "end": 1_010_000, "length_kb": 10.0, "n_snps": 4}],
                    "total_roh_kb": 10.0,
                    "longest_kb": 10.0,
                },
            ),
            # A row produced by a different algorithm version is not this
            # algorithm's result. The previous revision honoured such a row
            # under its own recorded thresholds, which left two sources of truth
            # -- the coverage gate kept using MIN_EVALUABLE_AUTOSOMAL_SNPS while
            # this validator honoured the row, so one blob could pass one and
            # fail the other. Withholding is the safe direction and
            # detail_unavailable already says "re-run the analysis".
            (
                "params_from_a_different_detector_version",
                {"params": {"min_roh_kb": 800, "min_roh_snps": 30}},
            ),
            # a length that disagrees with its own coordinates is not a
            # measurement of them: 1000 kb of span labelled 6200 kb
            (
                "length_disagrees_with_coordinates",
                {"segments": [{**_SEG, "end": 2_000_000}]},
            ),
            # the truncation flag is the one field that EXCUSES a short list, so
            # a drifted string here would wave the count/list check through
            ("truncation_flag_not_a_boolean", {"segments_truncated": "false"}),
            ("truncation_flag_is_a_string", {"segments_truncated": "yes"}),
            # a validator that raises is a liability: math.isfinite threw
            # OverflowError here and turned withholding into a 500
            ("oversized_integer_total", {"total_roh_kb": 10**400}),
            ("oversized_integer_segment_length", {"segments": [{**_SEG, "length_kb": 10**400}]}),
            # count vs list, with and without the truncation flag that would
            # legitimately excuse a short list
            ("count_exceeds_list", {"n_segments": 1, "segments": []}),
            ("list_exceeds_count", {"n_segments": 0, "segments": [_SEG]}),
            ("truncated_list_longer_than_count", {"n_segments": 1, "segments": [_SEG, _SEG]}),
            # zero segments cannot have summed to anything
            (
                "zero_segments_nonzero_total",
                {"n_segments": 0, "segments": [], "total_roh_kb": 900.0},
            ),
            (
                "zero_segments_nonzero_longest",
                {"n_segments": 0, "segments": [], "longest_kb": 900.0},
            ),
            # the longest segment is one of those summed
            ("longest_exceeds_total", {"total_roh_kb": 100.0, "longest_kb": 900.0}),
            # ...and cannot be shorter than a segment the blob itself lists
            ("longest_below_a_listed_segment", {"longest_kb": 10.0, "segments": [_SEG]}),
            # The persisted list is longest-first, including when truncated,
            # so a longest value that names no listed segment is fabricated.
            (
                "longest_above_every_listed_segment",
                {
                    "n_segments": 3,
                    "segments_truncated": True,
                    "segments": [_SEG, {**_SEG, "chrom": "2"}],
                    "total_roh_kb": 18600.0,
                    "longest_kb": 10000.0,
                },
            ),
            # a segment that runs backwards is not a coordinate range
            ("segment_ends_before_it_starts", {"segments": [{**_SEG, "start": 9_000_000}]}),
            # `_read_autosomal_states` can never emit these chromosomes.
            ("segment_is_not_autosomal", {"segments": [{**_SEG, "chrom": "X"}]}),
            # One detector run cannot emit the same marker interval twice, nor
            # emit overlapping runs on one chromosome.
            (
                "duplicate_segment_interval",
                {
                    "n_segments": 2,
                    "segments": [_SEG, _SEG],
                    "total_roh_kb": 12400.0,
                },
            ),
            (
                "overlapping_segment_intervals",
                {
                    "n_segments": 2,
                    "segments": [
                        _SEG,
                        {**_SEG, "start": 7_000_000, "end": 13_200_000},
                    ],
                    "total_roh_kb": 12400.0,
                },
            ),
            # A segment's homozygous calls are a subset of all callable calls,
            # and the detector's disjoint runs make that true of their sum too.
            (
                "segment_count_exceeds_callable_coverage",
                {"autosomal_snps_used": 200, "segments": [{**_SEG, "n_snps": 1000}]},
            ),
            (
                "segment_count_sum_exceeds_callable_coverage",
                {
                    "autosomal_snps_used": 200,
                    "n_segments": 2,
                    "segments": [
                        {**_SEG, "n_snps": 120},
                        {**_SEG, "chrom": "2", "n_snps": 120},
                    ],
                    "total_roh_kb": 12400.0,
                },
            ),
            # The writer records its denominator, so a rounded FROH that does
            # not equal total / that denominator is internally impossible.
            (
                "froh_disagrees_with_recorded_denominator",
                {
                    "froh": 1.0,
                    "n_segments": 0,
                    "segments": [],
                    "total_roh_kb": 0.0,
                    "longest_kb": 0.0,
                    "params": {"froh_denominator_kb": 2_770_000},
                },
            ),
            # Present-but-corrupt provenance is not the same thing as an absent
            # legacy field, and an impossible denominator stays impossible even
            # when the row omits the total needed for the arithmetic cross-check.
            ("params_is_not_an_object", {"params": "corrupt"}),
            (
                "recorded_denominator_is_zero_without_total",
                {
                    "total_roh_kb": None,
                    "params": {"froh_denominator_kb": 0},
                },
            ),
            (
                "recorded_denominator_exceeds_the_autosomal_genome",
                {
                    "total_roh_kb": None,
                    "params": {"froh_denominator_kb": 2_770_001},
                },
            ),
        ],
    )
    def test_internally_contradictory_metrics_are_withheld(self, label, overrides):
        # Per-field validity is not enough: these blobs hold nothing but
        # well-typed, in-range values and still describe an impossible scan.
        # Served verbatim they contradict themselves -- `n_segments: 1` beside
        # an empty list reports one segment and shows none. The relations are
        # read off `store_roh_findings`, the only writer, rather than
        # accumulated one review round at a time.
        detail = {
            "froh": 0.004,
            "autosomal_snps_used": 600_000,
            "total_roh_kb": 6200.0,
            "longest_kb": 6200.0,
            "n_segments": 1,
            "segments": [_SEG],
            **overrides,
        }
        response = _row_to_response(self._roh_row(finding_text="stored narrative", detail=detail))

        assert response.detail["evaluable"] is False, label
        assert response.detail["indeterminate_reason"] == "detail_unavailable", label
        assert response.detail["froh"] is None, label

    @pytest.mark.parametrize(
        ("label", "overrides"),
        [
            ("exact_agreement", {}),
            # a genuinely truncated row: the cap bit, so the list is shorter
            # than the count and says so -- and its total legitimately EXCEEDS
            # the segments it lists, because the unlisted ones counted too
            (
                "truncated_row",
                {
                    "n_segments": 40,
                    "segments": [_SEG],
                    "segments_truncated": True,
                    "total_roh_kb": 240_000.0,
                },
            ),
            # a row recording exactly this detector's thresholds is current, so
            # it is judged and served normally -- the discriminating control for
            # the version-mismatch rule below
            (
                "params_matching_this_detector",
                {"params": {"min_roh_kb": 1500, "min_roh_snps": 100}},
            ),
            # A span landing exactly on a one-decimal rounding tie. 2_048_250 bp
            # is 2048.25 kb, which the writer stores as round(...,1) == 2048.2,
            # and the raw gap between them evaluates to 0.0500000000001819 --
            # fractionally MORE than half a step. A nominal 0.05 tolerance
            # therefore withheld this freshly written, entirely genuine result,
            # so the comparison reproduces the writer's rounding instead.
            (
                "segment_on_a_rounding_tie",
                {
                    "segments": [
                        {**_SEG, "end": 3_048_250, "length_kb": 2048.2, "n_snps": 620},
                    ],
                    "total_roh_kb": 2048.2,
                    "longest_kb": 2048.2,
                },
            ),
            # ...and the total over two such segments, which the writer stores
            # as round(sum, 1). The comparison applies the same rounding, so a
            # float sum that is not bit-exact still matches.
            (
                "total_summed_over_rounded_segments",
                {
                    "n_segments": 2,
                    "segments": [
                        {**_SEG, "end": 3_048_200, "length_kb": 2048.2, "n_snps": 620},
                        {
                            **_SEG,
                            "start": 4_000_000,
                            "end": 6_048_200,
                            "length_kb": 2048.2,
                            "n_snps": 620,
                        },
                    ],
                    "total_roh_kb": 4096.4,
                    "longest_kb": 2048.2,
                },
            ),
            (
                "segment_counts_fit_callable_coverage",
                {
                    "autosomal_snps_used": 240,
                    "n_segments": 2,
                    "segments": [
                        {**_SEG, "n_snps": 120},
                        {**_SEG, "chrom": "2", "n_snps": 120},
                    ],
                    "total_roh_kb": 12400.0,
                },
            ),
            (
                "segments_touch_at_colocated_markers_without_overlapping",
                {
                    "n_segments": 2,
                    "segments": [
                        _SEG,
                        {
                            **_SEG,
                            "start": 7_200_000,
                            "end": 13_400_000,
                        },
                    ],
                    "total_roh_kb": 12400.0,
                },
            ),
            (
                "froh_matches_recorded_denominator",
                {
                    "froh": 0.00224,
                    "params": {"froh_denominator_kb": 2_770_000},
                },
            ),
            # a legacy blob recording only a subset stays evaluable
            ("no_segment_list", {"segments": None, "n_segments": None}),
            ("only_froh_and_count", {"total_roh_kb": None, "longest_kb": None}),
            (
                "genuine_empty_scan",
                {
                    "n_segments": 0,
                    "segments": [],
                    "total_roh_kb": 0.0,
                    "longest_kb": 0.0,
                },
            ),
        ],
    )
    def test_self_consistent_metrics_are_still_served(self, label, overrides):
        # Counterpart controls. Without these, a check that rejected every blob
        # with more than one metric would satisfy the parametrisation above
        # while withholding every real scan this module has recorded -- and the
        # truncated and legacy shapes are exactly the ones a too-strict relation
        # would silently eat.
        detail = {
            "froh": 0.004,
            "autosomal_snps_used": 600_000,
            "total_roh_kb": 6200.0,
            "longest_kb": 6200.0,
            "n_segments": 1,
            "segments": [_SEG],
            **overrides,
        }
        detail = {k: v for k, v in detail.items() if not (k in overrides and v is None)}
        response = _row_to_response(self._roh_row(finding_text="stored narrative", detail=detail))

        assert response.detail["froh"] == detail["froh"], label
        assert response.finding_text == "stored narrative", label

    def test_stored_reason_its_own_count_contradicts_is_downgraded(self):
        # See also test_evaluable_true_beside_a_reason_is_withheld for the
        # mirror-image contradiction on the true branch.
        #
        # A drifted row can store `evaluable: false` with
        # `insufficient_autosomal_markers` beside a well-typed count at or above
        # the floor. Honouring that pair made the narrative state, verbatim,
        # that 600000 callable SNPs are "fewer than the 100" required -- a
        # sentence its own number contradicts. The verdict is unchanged (still
        # withheld); only the explanation is replaced by one that claims nothing.
        response = _row_to_response(
            self._roh_row(
                finding_text="No long runs of homozygosity were detected (FROH ≈ 0).",
                detail={
                    "froh": 0.0,
                    "evaluable": False,
                    "indeterminate_reason": "insufficient_autosomal_markers",
                    "autosomal_snps_used": 600_000,
                },
            )
        )

        assert response.detail["evaluable"] is False
        assert response.detail["indeterminate_reason"] == "detail_unavailable"
        assert "fewer than" not in response.finding_text
        assert "600000" not in response.finding_text
        assert "could not be read" in response.finding_text.lower()

    @pytest.mark.parametrize(
        "reason", ["no_segment_eligible_region", "insufficient_autosomal_markers", "who_knows"]
    )
    def test_evaluable_true_beside_a_reason_is_withheld(self, reason):
        # A true verdict carries no reason, by construction: the writer sets
        # `indeterminate_reason` exactly when `froh` is None. A row asserting
        # both was served as `evaluable: true` beside a reason -- the dedicated
        # route happens to blank the reason on its way out, but
        # `normalize_legacy_row` hands the stored blob back untouched, so the
        # two representations disagreed about one row. Withheld on both now.
        response = _row_to_response(
            self._roh_row(
                finding_text="No long runs of homozygosity were detected (FROH ≈ 0).",
                detail={
                    "froh": 0.0,
                    "evaluable": True,
                    "indeterminate_reason": reason,
                    "autosomal_snps_used": 600_000,
                },
            )
        )

        assert response.detail["evaluable"] is False
        assert response.detail["indeterminate_reason"] == "detail_unavailable"
        assert response.detail["froh"] is None

    def test_evaluable_true_without_a_reason_is_served(self):
        # Counterpart control: the check must fire on the contradiction, not on
        # every stored true verdict -- otherwise every fresh row this module
        # writes would be withheld by its own validator.
        response = _row_to_response(
            self._roh_row(
                finding_text="stored narrative",
                detail={
                    "froh": 0.0,
                    "evaluable": True,
                    "indeterminate_reason": None,
                    "autosomal_snps_used": 600_000,
                },
            )
        )

        assert response.detail["froh"] == 0.0
        assert response.finding_text == "stored narrative"

    def test_stored_reason_its_count_does_support_is_honoured(self):
        # Counterpart control: the downgrade must fire on the contradiction
        # only, not on every stored reason -- otherwise a genuinely
        # low-coverage row would lose the cause that actually explains it.
        response = _row_to_response(
            self._roh_row(
                finding_text="No long runs of homozygosity were detected (FROH ≈ 0).",
                detail={
                    "froh": 0.0,
                    "evaluable": False,
                    "indeterminate_reason": "insufficient_autosomal_markers",
                    "autosomal_snps_used": 30,
                },
            )
        )

        assert response.detail["indeterminate_reason"] == "insufficient_autosomal_markers"
        assert "30 callable autosomal SNP(s)" in response.finding_text

    def test_evaluable_roh_text_is_untouched_in_generic_api(self):
        # Counterpart control: a well-covered ROH row keeps its stored text, so
        # the correction cannot silently rewrite every finding in the explorer.
        stored = "No long runs of homozygosity were detected (FROH ≈ 0)."
        response = _row_to_response(
            self._roh_row(
                finding_text=stored,
                detail={"froh": 0.0, "n_segments": 0, "autosomal_snps_used": 600_000},
            )
        )

        assert response.finding_text == stored

    def test_non_roh_finding_text_is_never_rewritten(self):
        # The normalizer is module-scoped: a low-marker-count detail blob on
        # another module must not trigger ROH wording.
        stored = "Sparse coverage note for an unrelated module."
        row = self._roh_row(
            finding_text=stored,
            detail={"autosomal_snps_used": 1},
        )
        row.module = "cancer"
        row.category = "prs"

        assert _row_to_response(row).finding_text == stored

    def test_finding_has_parsed_provenance(self, findings_client):
        resp = findings_client.get("/api/analysis/findings?sample_id=1&module=cancer")
        prov = resp.json()[0]["provenance"]
        # Full audit-metadata contract is preserved end-to-end (not just a subset).
        assert set(prov) == {
            "pipeline_version",
            "pipeline_genome_build",
            "sources",
            "variation_ids",
            "annotation_coverage",
            "annotation_coverage_sources",
        }
        assert prov["sources"]["clinvar"]["version"] == "2026-05-01"
        assert prov["variation_ids"]["rsid"] == "rs80357906"
        assert prov["annotation_coverage_sources"] == ["ClinVar", "gnomAD"]

    def test_finding_without_provenance_is_none(self, findings_client):
        resp = findings_client.get("/api/analysis/findings?sample_id=1&module=pharmacogenomics")
        data = resp.json()
        assert data[0]["provenance"] is None

    def test_finding_has_cross_module_link(self, findings_client):
        resp = findings_client.get("/api/analysis/findings?sample_id=1&module=gene_health")
        data = resp.json()
        assert len(data) == 1
        assert data[0]["related_module"] == "apoe"
        assert data[0]["related_finding_id"] == 1

    def test_finding_without_cross_link_has_null_fields(self, findings_client):
        resp = findings_client.get("/api/analysis/findings?sample_id=1&module=cancer")
        data = resp.json()
        assert data[0]["related_module"] is None
        assert data[0]["related_finding_id"] is None


# ── Summary tests ───────────────────────────────────────────────────


class TestFindingsSummary:
    def _seed_eligible_markers(self, tmp_data_dir: Path) -> None:
        """A region a run could occupy — the legacy rule re-derives from markers."""
        from tests.backend._roh_fixtures import seed_segment_eligible_markers

        engine = sa.create_engine(f"sqlite:///{tmp_data_dir / 'samples' / 'sample_1.db'}")
        try:
            seed_segment_eligible_markers(engine)
        finally:
            engine.dispose()

    def _seed_legacy_roh(self, tmp_data_dir: Path, snps_used: int) -> None:
        from backend.db.tables import findings as findings_table

        engine = sa.create_engine(f"sqlite:///{tmp_data_dir / 'samples' / 'sample_1.db'}")
        with engine.begin() as conn:
            conn.execute(
                sa.insert(findings_table),
                [
                    {
                        "module": "roh",
                        "category": "autozygosity",
                        "evidence_level": 1,
                        "finding_text": (
                            "No long runs of homozygosity were detected (FROH ≈ 0). "
                            "This is the typical result."
                        ),
                        "detail_json": json.dumps(
                            {"froh": 0.0, "n_segments": 0, "autosomal_snps_used": snps_used}
                        ),
                    }
                ],
            )
        engine.dispose()

    def test_summary_preview_corrects_legacy_roh_text(self, findings_client, tmp_data_dir):
        # #2177 — ReportBuilder renders top_finding_text on the module card, so
        # an uncorrected preview keeps showing "typical result" while the list
        # and dedicated endpoints report the estimate withheld.
        self._seed_legacy_roh(tmp_data_dir, snps_used=30)

        data = findings_client.get("/api/analysis/findings/summary?sample_id=1").json()
        roh = next(m for m in data["modules"] if m["module"] == "roh")

        assert "typical result" not in roh["top_finding_text"].lower()
        assert "not assessed" in roh["top_finding_text"].lower()

    def test_summary_preview_keeps_evaluable_roh_text(self, findings_client, tmp_data_dir):
        # Counterpart control: a well-covered ROH negative keeps its preview.
        # The sample carries a genuinely eligible region, because the legacy
        # rule is re-derived from its markers, not from the stored count.
        self._seed_eligible_markers(tmp_data_dir)
        self._seed_legacy_roh(tmp_data_dir, snps_used=600_000)

        data = findings_client.get("/api/analysis/findings/summary?sample_id=1").json()
        roh = next(m for m in data["modules"] if m["module"] == "roh")

        assert "typical result" in roh["top_finding_text"].lower()

    def test_summary_returns_all_modules(self, findings_client):
        resp = findings_client.get("/api/analysis/findings/summary?sample_id=1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_findings"] == 8
        modules = {m["module"] for m in data["modules"]}
        assert "cancer" in modules
        assert "pharmacogenomics" in modules
        assert "nutrigenomics" in modules

    def test_summary_high_confidence(self, findings_client):
        resp = findings_client.get("/api/analysis/findings/summary?sample_id=1")
        data = resp.json()
        high_conf = data["high_confidence_findings"]
        assert len(high_conf) == 5
        assert [f["module"] for f in high_conf] == [
            "cancer",
            "cardiovascular",
            "pharmacogenomics",
            "allergy",
            "carrier_status",
        ]
        assert [f["evidence_level"] for f in high_conf] == [4, 4, 4, 3, 3]
        assert all(f["evidence_level"] >= 3 for f in high_conf)
        assert all(f["module"] != "gene_health" for f in high_conf)

    def test_summary_high_confidence_selects_strongest_top_five(self, monkeypatch, tmp_path):
        import backend.api.routes.findings as findings_route

        sample_engine = sa.create_engine(f"sqlite:///{tmp_path / 'summary_sample.db'}")
        try:
            create_sample_tables(sample_engine)
            rows = [
                ("cancer", 4, "BRCA1 Pathogenic"),
                ("cardiovascular", 4, "LDLR pathogenic"),
                ("pharmacogenomics", 4, "CYP2C19 *1/*2 IM"),
                ("allergy", 3, "HLA-B hypersensitivity alert"),
                ("carrier_status", 3, "CFTR carrier"),
                ("gene_health", 3, "Alzheimer's disease risk (APOE ε4)"),
                ("ancestry", 2, "82% European ancestry"),
            ]
            with sample_engine.begin() as conn:
                for module, evidence_level, finding_text in rows:
                    conn.execute(
                        findings.insert().values(
                            module=module,
                            category="summary_regression",
                            evidence_level=evidence_level,
                            finding_text=finding_text,
                        )
                    )

            monkeypatch.setattr(
                findings_route, "_get_sample_engine", lambda sample_id: sample_engine
            )
            monkeypatch.setattr(findings_route, "gated_modules_to_hide", lambda engine: set())

            summary = findings_route.findings_summary(sample_id=1)
            high_conf = summary.high_confidence_findings

            assert len(high_conf) == 5
            assert [f.module for f in high_conf] == [
                "cancer",
                "cardiovascular",
                "pharmacogenomics",
                "allergy",
                "carrier_status",
            ]
            assert [f.evidence_level for f in high_conf] == [4, 4, 4, 3, 3]
            assert all(f.module != "gene_health" for f in high_conf)
        finally:
            sample_engine.dispose()

    def test_summary_module_counts(self, findings_client):
        resp = findings_client.get("/api/analysis/findings/summary?sample_id=1")
        data = resp.json()
        cancer_mod = next(m for m in data["modules"] if m["module"] == "cancer")
        assert cancer_mod["count"] == 1
        assert cancer_mod["max_evidence_level"] == 4

    def test_summary_returns_true_global_and_module_evidence_counts(self, findings_client):
        """Tier totals come from the full dataset, never a paginated window."""
        resp = findings_client.get("/api/analysis/findings/summary?sample_id=1")
        assert resp.status_code == 200
        data = resp.json()

        assert data["evidence_level_counts"] == [
            {"evidence_level": 4, "count": 3},
            {"evidence_level": 3, "count": 3},
            {"evidence_level": 2, "count": 2},
        ]
        assert (
            sum(item["count"] for item in data["evidence_level_counts"]) == data["total_findings"]
        )

        cancer_mod = next(m for m in data["modules"] if m["module"] == "cancer")
        assert cancer_mod["evidence_level_counts"] == [{"evidence_level": 4, "count": 1}]


class TestLAIPolicyQuarantine:
    """Pre-policy local ancestry must not leak through generic finding surfaces."""

    def test_list_summary_and_svg_withhold_unqualified_local_ancestry(
        self,
        monkeypatch,
        tmp_path,
    ):
        from fastapi import HTTPException

        import backend.api.routes.findings as findings_route

        sample_engine = sa.create_engine(f"sqlite:///{tmp_path / 'lai_quarantine.db'}")
        try:
            create_sample_tables(sample_engine)
            with sample_engine.begin() as conn:
                conn.execute(
                    findings.insert().values(
                        id=1,
                        module="ancestry",
                        category="nnls_admixture",
                        evidence_level=2,
                        finding_text="Qualified Tier 1 ancestry",
                    )
                )
                conn.execute(
                    findings.insert().values(
                        id=2,
                        module="ancestry",
                        category="local_ancestry",
                        evidence_level=4,
                        finding_text="Unqualified legacy chromosome painting",
                        svg_path="svgs/legacy.svg",
                    )
                )

            monkeypatch.setattr(
                findings_route,
                "_get_sample_engine",
                lambda sample_id: sample_engine,
            )
            monkeypatch.setattr(
                findings_route,
                "_get_sample_engine_and_dir",
                lambda sample_id: (sample_engine, tmp_path),
            )
            monkeypatch.setattr(findings_route, "gated_modules_to_hide", lambda engine: set())

            listed = findings_route.list_findings(
                sample_id=1,
                module=None,
                category=None,
                min_stars=None,
                limit=None,
                offset=0,
            )
            assert [finding.finding_text for finding in listed] == ["Qualified Tier 1 ancestry"]

            summary = findings_route.findings_summary(sample_id=1)
            assert summary.total_findings == 1
            assert summary.modules[0].top_finding_text == "Qualified Tier 1 ancestry"
            assert [item.model_dump() for item in summary.evidence_level_counts] == [
                {"evidence_level": 2, "count": 1}
            ]
            assert not summary.high_confidence_findings

            with pytest.raises(HTTPException) as caught:
                findings_route.get_finding_svg(finding_id=2, sample_id=1)
            assert caught.value.status_code == 404
        finally:
            sample_engine.dispose()


class TestWithheldPrescribingAlertPresentation:
    """#2019: a retained custom alert cannot bypass generic finding surfaces."""

    def test_list_summary_and_svg_hide_whitespace_wrapped_target(
        self,
        monkeypatch,
        tmp_path,
    ):
        from fastapi import HTTPException

        import backend.api.routes.findings as findings_route

        sample_engine = sa.create_engine(f"sqlite:///{tmp_path / 'withheld_alert.db'}")
        try:
            create_sample_tables(sample_engine)
            svg_dir = tmp_path / "svgs"
            svg_dir.mkdir()
            (svg_dir / "held.svg").write_text(
                '<svg xmlns="http://www.w3.org/2000/svg"><text>stale advice</text></svg>',
                encoding="utf-8",
            )
            (svg_dir / "nested.svg").write_text(
                '<svg xmlns="http://www.w3.org/2000/svg"><text>nested advice</text></svg>',
                encoding="utf-8",
            )
            (svg_dir / "duplicate.svg").write_text(
                '<svg xmlns="http://www.w3.org/2000/svg"><text>duplicate advice</text></svg>',
                encoding="utf-8",
            )
            (svg_dir / "split.svg").write_text(
                '<svg xmlns="http://www.w3.org/2000/svg"><text>split advice</text></svg>',
                encoding="utf-8",
            )
            with sample_engine.begin() as conn:
                conn.execute(
                    findings.insert().values(
                        id=1,
                        module="pharmacogenomics",
                        category="prescribing_alert",
                        evidence_level=4,
                        gene_symbol="CYP2D6",
                        drug="codeine",
                        finding_text="CYP2D6/codeine control alert",
                    )
                )
                conn.execute(
                    findings.insert().values(
                        id=2019,
                        # Deliberately not the canonical module and with
                        # whitespace/case variation: v25 preserves this row.
                        module="medication_review",
                        category="prescribing_alert",
                        evidence_level=4,
                        gene_symbol="\u00a0CYP2D6\u2003",
                        drug="\vTamOxIfEn\f",
                        finding_text="Custom retained tamoxifen clinical advice.",
                        svg_path="svgs/held.svg",
                    )
                )
                conn.execute(
                    findings.insert().values(
                        id=2020,
                        # A scalar-safe shell cannot reintroduce a held pair
                        # through recursively nested legacy detail payloads.
                        module="pharmacogenomics",
                        category="prescribing_alert",
                        evidence_level=6,
                        gene_symbol="CYP2C19",
                        drug="clopidogrel",
                        finding_text="CYP2C19/clopidogrel nested legacy shell",
                        detail_json=json.dumps(
                            {
                                "legacy": {
                                    " Gene ": "CYP2D6",
                                    "DRUG": "tamoxifen",
                                    "recommendation": "Nested tamoxifen guidance must not render.",
                                }
                            }
                        ),
                        svg_path="svgs/nested.svg",
                    )
                )
                conn.execute(
                    findings.insert().values(
                        id=2021,
                        # A permissive JSON parser would overwrite the first
                        # held gene before the payload reaches the response.
                        module="pharmacogenomics",
                        category="prescribing_alert",
                        evidence_level=6,
                        gene_symbol="CYP2C19",
                        drug="clopidogrel",
                        finding_text="CYP2C19/clopidogrel duplicate legacy shell",
                        detail_json=(
                            '{"gene":"CYP2D6","gene":"CYP2C19","drug":"tamoxifen",'
                            '"recommendation":"Duplicate tamoxifen guidance must not render."}'
                        ),
                        svg_path="svgs/duplicate.svg",
                    )
                )
                conn.execute(
                    findings.insert().values(
                        id=2022,
                        # Canonical keys can also be split across nested legacy
                        # objects, where no one dictionary contains both fields.
                        module="pharmacogenomics",
                        category="prescribing_alert",
                        evidence_level=6,
                        gene_symbol="CYP2C19",
                        drug="clopidogrel",
                        finding_text="CYP2C19/clopidogrel split legacy shell",
                        detail_json=json.dumps(
                            {
                                "gene": "CYP2D6",
                                "legacy": {
                                    "drug": "tamoxifen",
                                    "recommendation": "Split tamoxifen guidance must not render.",
                                },
                            }
                        ),
                        svg_path="svgs/split.svg",
                    )
                )
                conn.execute(
                    findings.insert().values(
                        id=2023,
                        # Sibling legacy fragments can also conceal the pair;
                        # no single object is allowed to make this appear safe.
                        module="pharmacogenomics",
                        category="prescribing_alert",
                        evidence_level=6,
                        gene_symbol="CYP2C19",
                        drug="clopidogrel",
                        finding_text="CYP2C19/clopidogrel fragmented legacy shell",
                        detail_json=json.dumps(
                            {
                                "legacy": [
                                    {"gene": "CYP2D6"},
                                    {
                                        "drug": "tamoxifen",
                                        "recommendation": (
                                            "Fragmented tamoxifen guidance must not render."
                                        ),
                                    },
                                ]
                            }
                        ),
                    )
                )
                conn.execute(
                    findings.insert().values(
                        id=2024,
                        # A scalar-safe row may not move held guidance into
                        # free text while leaving its structured payload empty.
                        module="pharmacogenomics",
                        category="prescribing_alert",
                        evidence_level=6,
                        gene_symbol="CYP2C19",
                        drug="clopidogrel",
                        finding_text="CYP2D6/tamoxifen: escalate dose",
                        detail_json=json.dumps({}),
                    )
                )
                conn.execute(
                    findings.insert().values(
                        id=2025,
                        # Nested sibling maps are one ambiguous legacy shell,
                        # even though no child object contains both identifiers.
                        module="pharmacogenomics",
                        category="prescribing_alert",
                        evidence_level=6,
                        gene_symbol="CYP2C19",
                        drug="clopidogrel",
                        finding_text="CYP2C19/clopidogrel sibling legacy shell",
                        detail_json=json.dumps(
                            {
                                "advice": {"gene": "CYP2D6"},
                                "medication": {
                                    "drug": "tamoxifen",
                                    "recommendation": "Sibling map guidance must not render.",
                                },
                            }
                        ),
                    )
                )
                conn.execute(
                    findings.insert().values(
                        id=2026,
                        # Generic findings serialize phenotype directly, so it
                        # must receive the same fail-closed presentation gate.
                        module="pharmacogenomics",
                        category="prescribing_alert",
                        evidence_level=6,
                        gene_symbol="CYP2C19",
                        drug="clopidogrel",
                        finding_text="CYP2C19/clopidogrel scalar legacy shell",
                        phenotype="CYP2D6 tamoxifen dose guidance",
                        detail_json=json.dumps({}),
                    )
                )

            monkeypatch.setattr(
                findings_route,
                "_get_sample_engine",
                lambda sample_id: sample_engine,
            )
            monkeypatch.setattr(
                findings_route,
                "_get_sample_engine_and_dir",
                lambda sample_id: (sample_engine, tmp_path),
            )
            monkeypatch.setattr(findings_route, "gated_modules_to_hide", lambda engine: set())

            listed = findings_route.list_findings(
                sample_id=1,
                module=None,
                category=None,
                min_stars=None,
                limit=None,
                offset=0,
            )
            assert [finding.finding_text for finding in listed] == ["CYP2D6/codeine control alert"]

            first_page = findings_route.list_findings(
                sample_id=1,
                module=None,
                category=None,
                min_stars=None,
                limit=1,
                offset=0,
            )
            assert [finding.finding_text for finding in first_page] == [
                "CYP2D6/codeine control alert"
            ]
            # Visible pagination has no second page. Before the payload gate
            # moved ahead of pagination, the leading evidence-level-6 legacy
            # row made this first page empty instead.
            assert (
                findings_route.list_findings(
                    sample_id=1,
                    module=None,
                    category=None,
                    min_stars=None,
                    limit=1,
                    offset=1,
                )
            ) == []

            summary = findings_route.findings_summary(sample_id=1)
            assert summary.total_findings == 1
            assert summary.modules[0].top_finding_text == "CYP2D6/codeine control alert"
            assert "tamoxifen" not in summary.model_dump_json().lower()

            # The backing SVG exists, so a 404 proves the row predicate rather
            # than a missing-file fallback.
            with pytest.raises(HTTPException) as caught:
                findings_route.get_finding_svg(finding_id=2019, sample_id=1)
            assert caught.value.status_code == 404
            with pytest.raises(HTTPException) as caught:
                findings_route.get_finding_svg(finding_id=2020, sample_id=1)
            assert caught.value.status_code == 404
            with pytest.raises(HTTPException) as caught:
                findings_route.get_finding_svg(finding_id=2021, sample_id=1)
            assert caught.value.status_code == 404
            with pytest.raises(HTTPException) as caught:
                findings_route.get_finding_svg(finding_id=2022, sample_id=1)
            assert caught.value.status_code == 404
        finally:
            sample_engine.dispose()

    def test_svg_artifact_cannot_reintroduce_held_pair(self, monkeypatch, tmp_path):
        """A source-safe row cannot serve stale held guidance from its SVG."""
        import backend.api.routes.findings as findings_route

        sample_engine = sa.create_engine(f"sqlite:///{tmp_path / 'stale_svg.db'}")
        try:
            create_sample_tables(sample_engine)
            svg_dir = tmp_path / "svgs"
            svg_dir.mkdir()
            (svg_dir / "stale.svg").write_text(
                '<svg xmlns="http://www.w3.org/2000/svg">'
                "<text><tspan>CYP2</tspan><tspan>D6</tspan>"
                "<tspan> tamoxifen dose guidance</tspan></text></svg>",
                encoding="utf-8",
            )
            with sample_engine.begin() as conn:
                conn.execute(
                    findings.insert().values(
                        id=2019,
                        module="pharmacogenomics",
                        category="prescribing_alert",
                        evidence_level=4,
                        gene_symbol="CYP2D6",
                        drug="codeine",
                        finding_text="CYP2D6/codeine control alert",
                        svg_path="svgs/stale.svg",
                    )
                )

            monkeypatch.setattr(
                findings_route,
                "_get_sample_engine_and_dir",
                lambda sample_id: (sample_engine, tmp_path),
            )
            monkeypatch.setattr(findings_route, "gated_modules_to_hide", lambda engine: set())

            response = findings_route.get_finding_svg(finding_id=2019, sample_id=1)
            assert response.status_code == 200
            body = response.body.decode()
            assert "<svg" in body
            assert "Drug: codeine" in body
            assert "tamoxifen" not in body.lower()
        finally:
            sample_engine.dispose()

    def test_svg_route_blocks_stored_path_traversal(self, monkeypatch, tmp_path):
        """A corrupt SVG path must not turn the by-id endpoint into a file reader."""
        from fastapi import HTTPException

        import backend.api.routes.findings as findings_route

        sample_engine = sa.create_engine(f"sqlite:///{tmp_path / 'svg_path.db'}")
        try:
            create_sample_tables(sample_engine)
            outside = tmp_path.parent / "outside.svg"
            sentinel = "outside SVG sentinel must never be served"
            outside.write_text(
                f'<svg xmlns="http://www.w3.org/2000/svg"><text>{sentinel}</text></svg>',
                encoding="utf-8",
            )
            with sample_engine.begin() as conn:
                conn.execute(
                    findings.insert().values(
                        id=2020,
                        module="cancer",
                        category="monogenic_variant",
                        evidence_level=4,
                        gene_symbol="BRCA1",
                        finding_text="Safe control finding",
                        svg_path="../outside.svg",
                    )
                )

            monkeypatch.setattr(
                findings_route,
                "_get_sample_engine_and_dir",
                lambda sample_id: (sample_engine, tmp_path),
            )
            monkeypatch.setattr(findings_route, "gated_modules_to_hide", lambda engine: set())

            with pytest.raises(HTTPException) as caught:
                findings_route.get_finding_svg(finding_id=2020, sample_id=1)
            assert caught.value.status_code == 404
            assert sentinel in outside.read_text(encoding="utf-8")
        finally:
            sample_engine.dispose()

    def test_list_and_summary_withhold_split_pair_across_safe_rows(
        self,
        monkeypatch,
        tmp_path,
    ):
        """A generic aggregate cannot recombine fields from safe source rows."""
        import backend.api.routes.findings as findings_route

        sample_engine = sa.create_engine(f"sqlite:///{tmp_path / 'split_pair_rows.db'}")
        try:
            create_sample_tables(sample_engine)
            with sample_engine.begin() as conn:
                conn.execute(
                    findings.insert(),
                    [
                        {
                            "module": "fitness",
                            "category": "pathway_summary",
                            "evidence_level": 4,
                            "gene_symbol": "CYP2D6",
                            "drug": None,
                            "finding_text": "Safe source row one",
                        },
                        {
                            "module": "fitness",
                            "category": "legacy_note",
                            "evidence_level": 4,
                            "gene_symbol": None,
                            "drug": "tamoxifen",
                            "finding_text": "Safe source row two",
                        },
                    ],
                )

            monkeypatch.setattr(
                findings_route, "_get_sample_engine", lambda _sample_id: sample_engine
            )
            monkeypatch.setattr(findings_route, "gated_modules_to_hide", lambda _engine: set())

            assert (
                findings_route.list_findings(
                    sample_id=1,
                    module=None,
                    category=None,
                    min_stars=None,
                    limit=None,
                    offset=0,
                )
            ) == []
            assert (
                findings_route.list_findings(
                    sample_id=1,
                    module=None,
                    category=None,
                    min_stars=None,
                    limit=10,
                    offset=0,
                )
            ) == []

            summary = findings_route.findings_summary(sample_id=1)
            assert summary.total_findings == 0
            assert summary.modules == []
            assert summary.high_confidence_findings == []
        finally:
            sample_engine.dispose()


# ── SVG endpoint tests ─────────────────────────────────────────────


class TestFindingSvg:
    def test_no_svg_returns_404(self, findings_client):
        resp = findings_client.get("/api/analysis/findings/1/svg?sample_id=1")
        # svg_path is None for seeded findings
        assert resp.status_code == 404

    def test_nonexistent_finding_returns_404(self, findings_client):
        resp = findings_client.get("/api/analysis/findings/999/svg?sample_id=1")
        assert resp.status_code == 404


# ── APOE disclosure gate on the generic aggregator (issue #222) ──────


@pytest.fixture
def apoe_findings_client(tmp_data_dir: Path) -> Generator[TestClient, None, None]:
    """Test client with both APOE and non-APOE findings stored for one sample.

    The APOE gate is NOT acknowledged by default (no apoe_gate row), so the
    generic ``/api/analysis/findings`` aggregator must withhold the APOE rows.
    """
    settings = Settings(data_dir=tmp_data_dir, wal_mode=False)

    ref_path = settings.reference_db_path
    ref_engine = sa.create_engine(f"sqlite:///{ref_path}")
    reference_metadata.create_all(ref_engine)

    sample_db_path = tmp_data_dir / "samples" / "sample_1.db"
    sample_engine = sa.create_engine(f"sqlite:///{sample_db_path}")
    create_sample_tables(sample_engine)

    with ref_engine.begin() as conn:
        conn.execute(
            samples.insert().values(
                id=1,
                name="Test Sample",
                db_path="samples/sample_1.db",
                file_format="v5",
                file_hash="abc123",
            )
        )

    seed_findings = [
        # Non-APOE finding — must always be visible.
        {
            "module": "cancer",
            "category": "monogenic_variant",
            "evidence_level": 4,
            "gene_symbol": "BRCA1",
            "finding_text": "BRCA1 Pathogenic",
        },
        # APOE findings — the ε4 diplotype + Alzheimer's narrative the gate protects.
        {
            "module": "apoe",
            "category": "genotype",
            "gene_symbol": "APOE",
            "finding_text": "APOE genotype determined",
            "diplotype": "ε3/ε4",
        },
        {
            "module": "apoe",
            "category": "cardiovascular_risk",
            "evidence_level": 4,
            "gene_symbol": "APOE",
            "finding_text": "APOE ε4 cardiovascular risk context",
            "diplotype": "ε3/ε4",
        },
        {
            "module": "apoe",
            "category": "alzheimers_risk",
            "evidence_level": 4,
            "gene_symbol": "APOE",
            "finding_text": "APOE ε3/ε4 — probabilistic Alzheimer's disease risk modifier",
            "diplotype": "ε3/ε4",
        },
        {
            "module": "apoe",
            "category": "lipid_dietary",
            "evidence_level": 3,
            "gene_symbol": "APOE",
            "finding_text": "APOE ε4 lipid/dietary context",
            "diplotype": "ε3/ε4",
        },
    ]
    with sample_engine.begin() as conn:
        for f in seed_findings:
            conn.execute(findings.insert().values(**f))

    ref_engine.dispose()
    sample_engine.dispose()

    with (
        patch("backend.main.get_settings", return_value=settings),
        patch("backend.db.connection.get_settings", return_value=settings),
    ):
        reset_registry()

        from backend.main import create_app

        app = create_app()
        with TestClient(app) as tc:
            yield tc

        reset_registry()


class TestAPOEGateOnGenericFindings:
    """The generic findings aggregator must honor the APOE disclosure gate (#222)."""

    def test_apoe_withheld_from_unfiltered_list_before_ack(self, apoe_findings_client):
        resp = apoe_findings_client.get("/api/analysis/findings?sample_id=1")
        assert resp.status_code == 200
        data = resp.json()
        modules = {f["module"] for f in data}
        assert "apoe" not in modules
        # Non-APOE findings still surface.
        assert "cancer" in modules
        # Neither the diplotype nor the Alzheimer's narrative leaks.
        assert "ε3/ε4" not in resp.text
        assert "alzheimer" not in resp.text.lower()

    def test_apoe_withheld_from_explicit_module_filter_before_ack(self, apoe_findings_client):
        resp = apoe_findings_client.get("/api/analysis/findings?sample_id=1&module=apoe")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_apoe_withheld_from_summary_before_ack(self, apoe_findings_client):
        resp = apoe_findings_client.get("/api/analysis/findings/summary?sample_id=1")
        assert resp.status_code == 200
        data = resp.json()
        modules = {m["module"] for m in data["modules"]}
        assert "apoe" not in modules
        assert data["total_findings"] == 1
        assert data["evidence_level_counts"] == [{"evidence_level": 4, "count": 1}]
        # No APOE row leaks via top_finding_text or high_confidence_findings.
        assert "ε3/ε4" not in resp.text
        assert "alzheimer" not in resp.text.lower()
        assert all(f["module"] != "apoe" for f in data["high_confidence_findings"])

    def test_apoe_visible_in_list_after_ack(self, apoe_findings_client):
        ack = apoe_findings_client.post(
            "/api/analysis/apoe/acknowledge-gate", params={"sample_id": 1}
        )
        assert ack.status_code == 200

        resp = apoe_findings_client.get("/api/analysis/findings?sample_id=1&module=apoe")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 4
        assert all(f["module"] == "apoe" for f in data)
        assert all(f["diplotype"] == "ε3/ε4" for f in data)

    def test_apoe_visible_in_summary_after_ack(self, apoe_findings_client):
        apoe_findings_client.post("/api/analysis/apoe/acknowledge-gate", params={"sample_id": 1})

        resp = apoe_findings_client.get("/api/analysis/findings/summary?sample_id=1")
        assert resp.status_code == 200
        data = resp.json()
        apoe_mod = next((m for m in data["modules"] if m["module"] == "apoe"), None)
        assert apoe_mod is not None
        assert apoe_mod["count"] == 4
        assert apoe_mod["evidence_level_counts"] == [
            {"evidence_level": 4, "count": 2},
            {"evidence_level": 3, "count": 1},
            {"evidence_level": 0, "count": 1},
        ]
        # The high-evidence APOE rows re-enter high_confidence_findings post-ack —
        # guards against an over-gating regression that permanently drops them.
        assert any(f["module"] == "apoe" for f in data["high_confidence_findings"])

    def test_apoe_withheld_with_min_stars_filter_before_ack(self, apoe_findings_client):
        # min_stars combined with the APOE gate: the high-evidence APOE rows
        # (cardiovascular/alzheimers = 4★, lipid = 3★) must stay withheld pre-ack.
        resp = apoe_findings_client.get("/api/analysis/findings?sample_id=1&min_stars=3")
        assert resp.status_code == 200
        data = resp.json()
        assert {f["module"] for f in data} == {"cancer"}
        assert "ε3/ε4" not in resp.text

        apoe_findings_client.post("/api/analysis/apoe/acknowledge-gate", params={"sample_id": 1})
        resp = apoe_findings_client.get("/api/analysis/findings?sample_id=1&min_stars=3")
        data = resp.json()
        assert "apoe" in {f["module"] for f in data}


@pytest.fixture
def apoe_svg_client(tmp_data_dir: Path) -> Generator[TestClient, None, None]:
    """Client where an APOE finding and a non-APOE finding both have on-disk SVGs.

    The APOE SVG card renders the ε4 diplotype + risk label, so the by-id SVG
    endpoint must honor the same gate as list/summary (issue #222). Findings are
    inserted with explicit ids so the test can request them directly: id=1 is the
    APOE card (gated), id=2 is a non-APOE card (always served).
    """
    settings = Settings(data_dir=tmp_data_dir, wal_mode=False)

    ref_path = settings.reference_db_path
    ref_engine = sa.create_engine(f"sqlite:///{ref_path}")
    reference_metadata.create_all(ref_engine)

    sample_dir = tmp_data_dir / "samples"
    sample_db_path = sample_dir / "sample_1.db"
    sample_engine = sa.create_engine(f"sqlite:///{sample_db_path}")
    create_sample_tables(sample_engine)

    # Write the on-disk SVGs the endpoint serves (relative to the sample dir).
    svg_dir = sample_dir / "svgs"
    svg_dir.mkdir(exist_ok=True)
    (svg_dir / "apoe_card.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg"><text>Diplotype: ε3/ε4</text></svg>',
        encoding="utf-8",
    )
    (svg_dir / "cancer_card.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg"><text>BRCA1</text></svg>',
        encoding="utf-8",
    )

    with ref_engine.begin() as conn:
        conn.execute(
            samples.insert().values(
                id=1,
                name="Test Sample",
                db_path="samples/sample_1.db",
                file_format="v5",
                file_hash="abc123",
            )
        )
    with sample_engine.begin() as conn:
        conn.execute(
            findings.insert().values(
                id=1,
                module="apoe",
                category="alzheimers_risk",
                evidence_level=4,
                gene_symbol="APOE",
                finding_text="APOE ε3/ε4 — probabilistic Alzheimer's disease risk modifier",
                diplotype="ε3/ε4",
                svg_path="svgs/apoe_card.svg",
            )
        )
        conn.execute(
            findings.insert().values(
                id=2,
                module="cancer",
                category="monogenic_variant",
                evidence_level=4,
                gene_symbol="BRCA1",
                finding_text="BRCA1 Pathogenic",
                svg_path="svgs/cancer_card.svg",
            )
        )

    ref_engine.dispose()
    sample_engine.dispose()

    with (
        patch("backend.main.get_settings", return_value=settings),
        patch("backend.db.connection.get_settings", return_value=settings),
    ):
        reset_registry()

        from backend.main import create_app

        app = create_app()
        with TestClient(app) as tc:
            yield tc

        reset_registry()


class TestAPOESvgGate:
    """GET /findings/{id}/svg must honor the APOE disclosure gate (issue #222)."""

    def test_apoe_svg_withheld_before_ack(self, apoe_svg_client):
        # 404 (not 403) pre-ack: must not even confirm the APOE finding exists.
        resp = apoe_svg_client.get("/api/analysis/findings/1/svg?sample_id=1")
        assert resp.status_code == 404

    def test_non_apoe_svg_served_regardless(self, apoe_svg_client):
        # Non-APOE SVGs are unaffected by the gate.
        resp = apoe_svg_client.get("/api/analysis/findings/2/svg?sample_id=1")
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("image/svg+xml")

    def test_apoe_svg_served_after_ack(self, apoe_svg_client):
        apoe_svg_client.post("/api/analysis/apoe/acknowledge-gate", params={"sample_id": 1})
        resp = apoe_svg_client.get("/api/analysis/findings/1/svg?sample_id=1")
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("image/svg+xml")
        assert "ε3/ε4" in resp.text


@pytest.fixture
def aneuploidy_findings_client(tmp_data_dir: Path) -> Generator[TestClient, None, None]:
    """Test client with a gated sex-aneuploidy finding + a non-gated finding.

    The aneuploidy gate is NOT acknowledged by default (no aneuploidy_gate row),
    so the generic ``/api/analysis/findings`` aggregator must withhold the
    sex_aneuploidy row (issue #299).
    """
    settings = Settings(data_dir=tmp_data_dir, wal_mode=False)

    ref_path = settings.reference_db_path
    ref_engine = sa.create_engine(f"sqlite:///{ref_path}")
    reference_metadata.create_all(ref_engine)

    sample_db_path = tmp_data_dir / "samples" / "sample_1.db"
    sample_engine = sa.create_engine(f"sqlite:///{sample_db_path}")
    create_sample_tables(sample_engine)

    with ref_engine.begin() as conn:
        conn.execute(
            samples.insert().values(
                id=1,
                name="Test Sample",
                db_path="samples/sample_1.db",
                file_format="v5",
                file_hash="abc123",
            )
        )

    seed_findings = [
        # Non-gated finding — must always be visible.
        {
            "module": "cancer",
            "category": "monogenic_variant",
            "evidence_level": 4,
            "gene_symbol": "BRCA1",
            "finding_text": "BRCA1 Pathogenic",
        },
        # The gated sex-aneuploidy screen result (possible XXY / Klinefelter).
        {
            "module": "sex_aneuploidy",
            "category": "aneuploidy_screen",
            "evidence_level": 3,
            "finding_text": (
                "Screen suggests a possible sex-chromosome aneuploidy with an XXY "
                "(Klinefelter) pattern — confirmation required."
            ),
        },
    ]
    with sample_engine.begin() as conn:
        for f in seed_findings:
            conn.execute(findings.insert().values(**f))

    ref_engine.dispose()
    sample_engine.dispose()

    with (
        patch("backend.main.get_settings", return_value=settings),
        patch("backend.db.connection.get_settings", return_value=settings),
    ):
        reset_registry()

        from backend.main import create_app

        app = create_app()
        with TestClient(app) as tc:
            yield tc

        reset_registry()


class TestAneuploidyGateOnGenericFindings:
    """The generic findings aggregator must honor the sex-aneuploidy gate (#299)."""

    def test_withheld_from_unfiltered_list_before_ack(self, aneuploidy_findings_client):
        resp = aneuploidy_findings_client.get("/api/analysis/findings?sample_id=1")
        assert resp.status_code == 200
        modules = {f["module"] for f in resp.json()}
        assert "sex_aneuploidy" not in modules
        assert "cancer" in modules  # non-gated findings still surface
        # The XXY/Klinefelter screen text must not leak.
        assert "xxy" not in resp.text.lower()
        assert "klinefelter" not in resp.text.lower()

    def test_withheld_from_explicit_module_filter_before_ack(self, aneuploidy_findings_client):
        resp = aneuploidy_findings_client.get(
            "/api/analysis/findings?sample_id=1&module=sex_aneuploidy"
        )
        assert resp.status_code == 200  # empty list, not a 403 that confirms data exists
        assert resp.json() == []

    def test_withheld_from_summary_before_ack(self, aneuploidy_findings_client):
        resp = aneuploidy_findings_client.get("/api/analysis/findings/summary?sample_id=1")
        assert resp.status_code == 200
        data = resp.json()
        assert "sex_aneuploidy" not in {m["module"] for m in data["modules"]}
        assert "xxy" not in resp.text.lower()
        assert "klinefelter" not in resp.text.lower()
        assert all(f["module"] != "sex_aneuploidy" for f in data["high_confidence_findings"])

    def test_visible_in_list_after_ack(self, aneuploidy_findings_client):
        ack = aneuploidy_findings_client.post(
            "/api/analysis/sex-aneuploidy/acknowledge-gate", params={"sample_id": 1}
        )
        assert ack.status_code == 200
        resp = aneuploidy_findings_client.get(
            "/api/analysis/findings?sample_id=1&module=sex_aneuploidy"
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["module"] == "sex_aneuploidy"
        assert "XXY" in data[0]["finding_text"]

    def test_visible_in_summary_after_ack(self, aneuploidy_findings_client):
        aneuploidy_findings_client.post(
            "/api/analysis/sex-aneuploidy/acknowledge-gate", params={"sample_id": 1}
        )
        resp = aneuploidy_findings_client.get("/api/analysis/findings/summary?sample_id=1")
        assert resp.status_code == 200
        data = resp.json()
        assert "sex_aneuploidy" in {m["module"] for m in data["modules"]}


# ── Parkinson's disclosure gate on the generic aggregator (issue #298) ──


@pytest.fixture
def parkinsons_findings_client(tmp_data_dir: Path) -> Generator[TestClient, None, None]:
    """Client with both Parkinson's and non-Parkinson's findings for one sample.

    The Parkinson's gate is NOT acknowledged by default (no parkinsons_gate row),
    so the generic ``/api/analysis/findings`` aggregator must withhold the
    Parkinson's rows (the LRRK2 G2019S reduced-penetrance risk narrative).
    """
    settings = Settings(data_dir=tmp_data_dir, wal_mode=False)

    ref_path = settings.reference_db_path
    ref_engine = sa.create_engine(f"sqlite:///{ref_path}")
    reference_metadata.create_all(ref_engine)

    sample_db_path = tmp_data_dir / "samples" / "sample_1.db"
    sample_engine = sa.create_engine(f"sqlite:///{sample_db_path}")
    create_sample_tables(sample_engine)

    with ref_engine.begin() as conn:
        conn.execute(
            samples.insert().values(
                id=1,
                name="Test Sample",
                db_path="samples/sample_1.db",
                file_format="v5",
                file_hash="abc123",
            )
        )

    seed_findings = [
        # Non-Parkinson's finding — must always be visible.
        {
            "module": "cancer",
            "category": "monogenic_variant",
            "evidence_level": 4,
            "gene_symbol": "BRCA1",
            "finding_text": "BRCA1 Pathogenic",
        },
        # A second gated module (APOE), to prove gate independence: acknowledging
        # Parkinson's must NOT reveal APOE.
        {
            "module": "apoe",
            "category": "alzheimers_risk",
            "evidence_level": 4,
            "gene_symbol": "APOE",
            "finding_text": "APOE ε3/ε4 — probabilistic Alzheimer's disease risk",
            "diplotype": "ε3/ε4",
        },
        # Parkinson's finding — the LRRK2 G2019S risk narrative the gate protects.
        # evidence_level 4 (vs production's 2) is intentional: it exercises the
        # high_confidence_findings (>=3) leak path in the summary assertions.
        {
            "module": "parkinsons",
            "category": "risk_variant",
            "evidence_level": 4,
            "gene_symbol": "LRRK2",
            "rsid": "rs34637584",
            "finding_text": ("LRRK2 G2019S — reduced-penetrance Parkinson's disease risk variant"),
            "conditions": "Parkinson's disease",
        },
    ]
    with sample_engine.begin() as conn:
        for f in seed_findings:
            conn.execute(findings.insert().values(**f))

    ref_engine.dispose()
    sample_engine.dispose()

    with (
        patch("backend.main.get_settings", return_value=settings),
        patch("backend.db.connection.get_settings", return_value=settings),
    ):
        reset_registry()

        from backend.main import create_app

        app = create_app()
        with TestClient(app) as tc:
            yield tc

        reset_registry()


class TestParkinsonsGateOnGenericFindings:
    """The generic findings aggregator must honor the Parkinson's gate (#298)."""

    _ACK = "/api/analysis/parkinsons/acknowledge-gate"

    def test_withheld_from_unfiltered_list_before_ack(self, parkinsons_findings_client):
        resp = parkinsons_findings_client.get("/api/analysis/findings?sample_id=1")
        assert resp.status_code == 200
        data = resp.json()
        modules = {f["module"] for f in data}
        assert "parkinsons" not in modules
        assert "cancer" in modules  # non-gated finding still surfaces
        assert "lrrk2" not in resp.text.lower()
        assert "g2019s" not in resp.text.lower()

    def test_withheld_from_explicit_module_filter_before_ack(self, parkinsons_findings_client):
        resp = parkinsons_findings_client.get(
            "/api/analysis/findings?sample_id=1&module=parkinsons"
        )
        assert resp.status_code == 200
        assert resp.json() == []

    def test_withheld_from_summary_before_ack(self, parkinsons_findings_client):
        resp = parkinsons_findings_client.get("/api/analysis/findings/summary?sample_id=1")
        assert resp.status_code == 200
        data = resp.json()
        assert "parkinsons" not in {m["module"] for m in data["modules"]}
        assert "g2019s" not in resp.text.lower()
        assert all(f["module"] != "parkinsons" for f in data["high_confidence_findings"])

    def test_visible_in_list_after_ack(self, parkinsons_findings_client):
        ack = parkinsons_findings_client.post(self._ACK, params={"sample_id": 1})
        assert ack.status_code == 200
        resp = parkinsons_findings_client.get(
            "/api/analysis/findings?sample_id=1&module=parkinsons"
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["module"] == "parkinsons"
        assert "G2019S" in data[0]["finding_text"]

    def test_visible_in_summary_after_ack(self, parkinsons_findings_client):
        parkinsons_findings_client.post(self._ACK, params={"sample_id": 1})
        resp = parkinsons_findings_client.get("/api/analysis/findings/summary?sample_id=1")
        assert resp.status_code == 200
        data = resp.json()
        pk = next((m for m in data["modules"] if m["module"] == "parkinsons"), None)
        assert pk is not None
        assert pk["count"] == 1
        assert any(f["module"] == "parkinsons" for f in data["high_confidence_findings"])

    def test_apoe_still_independently_gated(self, parkinsons_findings_client):
        # Acknowledging Parkinson's must NOT unlock APOE — the fixture seeds an
        # APOE row, so this is non-vacuous: the acked gate appears, the other stays
        # withheld (a blanket-unlock regression would fail here).
        parkinsons_findings_client.post(self._ACK, params={"sample_id": 1})
        resp = parkinsons_findings_client.get("/api/analysis/findings?sample_id=1")
        modules = {f["module"] for f in resp.json()}
        assert "parkinsons" in modules  # the acknowledged gate is now visible
        assert "apoe" not in modules  # the un-acknowledged APOE gate stays withheld
        assert "alzheimer" not in resp.text.lower()

    def test_both_gates_acknowledged_reveals_both(self, parkinsons_findings_client):
        # Empty-withheld branch: acknowledging every gate reveals all gated modules.
        parkinsons_findings_client.post(self._ACK, params={"sample_id": 1})
        parkinsons_findings_client.post(
            "/api/analysis/apoe/acknowledge-gate", params={"sample_id": 1}
        )
        resp = parkinsons_findings_client.get("/api/analysis/findings?sample_id=1")
        modules = {f["module"] for f in resp.json()}
        assert {"apoe", "parkinsons", "cancer"} <= modules


@pytest.fixture
def parkinsons_svg_client(tmp_data_dir: Path) -> Generator[TestClient, None, None]:
    """Client where a Parkinson's finding and a non-Parkinson's finding have SVGs."""
    settings = Settings(data_dir=tmp_data_dir, wal_mode=False)

    ref_path = settings.reference_db_path
    ref_engine = sa.create_engine(f"sqlite:///{ref_path}")
    reference_metadata.create_all(ref_engine)

    sample_dir = tmp_data_dir / "samples"
    sample_db_path = sample_dir / "sample_1.db"
    sample_engine = sa.create_engine(f"sqlite:///{sample_db_path}")
    create_sample_tables(sample_engine)

    svg_dir = sample_dir / "svgs"
    svg_dir.mkdir(exist_ok=True)
    (svg_dir / "pk_card.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg"><text>LRRK2 G2019S</text></svg>',
        encoding="utf-8",
    )
    (svg_dir / "cancer_card.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg"><text>BRCA1</text></svg>',
        encoding="utf-8",
    )

    with ref_engine.begin() as conn:
        conn.execute(
            samples.insert().values(
                id=1,
                name="Test Sample",
                db_path="samples/sample_1.db",
                file_format="v5",
                file_hash="abc123",
            )
        )
    with sample_engine.begin() as conn:
        conn.execute(
            findings.insert().values(
                id=1,
                module="parkinsons",
                category="risk_variant",
                evidence_level=4,
                gene_symbol="LRRK2",
                finding_text="LRRK2 G2019S Parkinson's risk",
                svg_path="svgs/pk_card.svg",
            )
        )
        conn.execute(
            findings.insert().values(
                id=2,
                module="cancer",
                category="monogenic_variant",
                evidence_level=4,
                gene_symbol="BRCA1",
                finding_text="BRCA1 Pathogenic",
                svg_path="svgs/cancer_card.svg",
            )
        )

    ref_engine.dispose()
    sample_engine.dispose()

    with (
        patch("backend.main.get_settings", return_value=settings),
        patch("backend.db.connection.get_settings", return_value=settings),
    ):
        reset_registry()

        from backend.main import create_app

        app = create_app()
        with TestClient(app) as tc:
            yield tc

        reset_registry()


class TestParkinsonsSvgGate:
    """GET /findings/{id}/svg must honor the Parkinson's gate (issue #298)."""

    def test_parkinsons_svg_withheld_before_ack(self, parkinsons_svg_client):
        resp = parkinsons_svg_client.get("/api/analysis/findings/1/svg?sample_id=1")
        assert resp.status_code == 404

    def test_non_parkinsons_svg_served_regardless(self, parkinsons_svg_client):
        resp = parkinsons_svg_client.get("/api/analysis/findings/2/svg?sample_id=1")
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("image/svg+xml")

    def test_parkinsons_svg_served_after_ack(self, parkinsons_svg_client):
        parkinsons_svg_client.post(
            "/api/analysis/parkinsons/acknowledge-gate", params={"sample_id": 1}
        )
        resp = parkinsons_svg_client.get("/api/analysis/findings/1/svg?sample_id=1")
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("image/svg+xml")
        assert "<svg" in resp.text
        assert "LRRK2 G2019S" not in resp.text

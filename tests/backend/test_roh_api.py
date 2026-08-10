"""Tests for the ROH / FROH findings API."""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path
from unittest.mock import patch

import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.config import Settings
from backend.db.connection import DBRegistry, reset_registry
from backend.db.sample_schema import create_sample_tables
from backend.db.tables import raw_variants, reference_metadata, samples
from backend.disclaimers import ROH_DISCLAIMER_TEXT, ROH_DISCLAIMER_TITLE


@pytest.fixture()
def _env(tmp_path: Path) -> Generator[sa.Engine, None, None]:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "samples").mkdir()

    ref_db = data_dir / "reference.db"
    ref_engine = sa.create_engine(f"sqlite:///{ref_db}")
    reference_metadata.create_all(ref_engine)
    with ref_engine.begin() as conn:
        conn.execute(
            sa.insert(samples),
            [
                {
                    "name": "test_sample",
                    "db_path": "samples/sample_1.db",
                    "file_format": "23andme_v5",
                    "file_hash": "abc123",
                }
            ],
        )

    sample_db = data_dir / "samples" / "sample_1.db"
    sample_engine = sa.create_engine(f"sqlite:///{sample_db}")
    create_sample_tables(sample_engine)
    # Two unequal clean homozygous runs plus one typed non-ROH autosomal SNP,
    # with an autosomal no-call and chrX call that must be excluded.
    # This makes total length, longest length, and typed-SNP count independently
    # discriminating at the API boundary.
    with sample_engine.begin() as conn:
        conn.execute(
            sa.insert(raw_variants),
            [
                {
                    "rsid": f"r1_{i}",
                    "chrom": "1",
                    "pos": 1_000_000 + i * 10_000,
                    "genotype": "CC",
                }
                for i in range(160)
            ]
            + [
                {
                    "rsid": f"r2_{i}",
                    "chrom": "2",
                    "pos": 1_000_000 + i * 10_000,
                    "genotype": "AA",
                }
                for i in range(200)
            ]
            + [
                {
                    "rsid": "r3_het",
                    "chrom": "3",
                    "pos": 1_000_000,
                    "genotype": "AG",
                },
                {
                    "rsid": "r4_no_call",
                    "chrom": "4",
                    "pos": 1_000_000,
                    "genotype": "--",
                },
                {
                    "rsid": "rx_hom",
                    "chrom": "X",
                    "pos": 1_000_000,
                    "genotype": "GG",
                },
            ],
        )

    settings = Settings(data_dir=data_dir)
    reset_registry()
    registry = DBRegistry(settings)
    with (
        patch("backend.api.dependencies.get_registry", return_value=registry),
        patch("backend.api.routes.risk_common.get_registry", return_value=registry),
        patch("backend.services.staleness.get_registry", return_value=registry),
    ):
        yield sample_engine
    registry.dispose_all()
    reset_registry()


@pytest.fixture()
def client(_env: sa.Engine) -> TestClient:
    from backend.api.routes.roh import router

    app = FastAPI()
    app.include_router(router, prefix="/api")
    return TestClient(app)


class TestDisclaimer:
    def test_returns_disclaimer(self, client: TestClient) -> None:
        resp = client.get("/api/analysis/roh/disclaimer")
        assert resp.status_code == 200
        data = resp.json()
        assert data["title"] == ROH_DISCLAIMER_TITLE
        assert data["text"] == ROH_DISCLAIMER_TEXT
        assert "not a diagnosis" in data["text"].lower()


class TestRunAndList:
    def test_run_then_list(self, client: TestClient) -> None:
        run = client.post("/api/analysis/roh/run?sample_id=1")
        assert run.status_code == 200
        assert run.json()["findings_count"] == 1

        listing = client.get("/api/analysis/roh/findings?sample_id=1")
        assert listing.status_code == 200
        data = listing.json()
        assert data["n_segments"] == 2
        # Independent fixture-derived literals pin the populated API fields
        # against zeroing or cross-field mapping regressions: chr1 contributes
        # 1590 kb, chr2 contributes 1990 kb, and the 360 ROH calls plus one
        # typed heterozygous call make 361 autosomal SNPs used.
        assert data["total_roh_kb"] == 3580.0
        assert data["longest_kb"] == 1990.0
        assert data["autosomal_snps_used"] == 361
        assert data["froh"] > 0
        assert [segment["chrom"] for segment in data["segments"]] == ["2", "1"]

    def test_list_before_run_is_null(self, client: TestClient) -> None:
        listing = client.get("/api/analysis/roh/findings?sample_id=1")
        assert listing.status_code == 200
        assert listing.json() is None

    def test_malformed_detail_falls_back_safely(self, _env: sa.Engine, client: TestClient) -> None:
        # A row with a schema-drifted segment must not 500 — the route falls back
        # to an indeterminate response: no metrics, and standardized text rather
        # than the stored narrative. (This comment previously described the
        # opposite — plain finding_text with zeroed metrics — which is exactly
        # the behaviour this change removes.)
        import json as _json

        from backend.db.tables import findings

        with _env.begin() as conn:
            conn.execute(
                sa.insert(findings),
                [
                    {
                        "module": "roh",
                        "category": "autozygosity",
                        "evidence_level": 1,
                        "finding_text": "summary text",
                        "detail_json": _json.dumps({"segments": [{"unexpected": "shape"}]}),
                    }
                ],
            )
        resp = client.get("/api/analysis/roh/findings?sample_id=1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["segments"] == []
        # The row's state could not be read, so nothing about it is asserted —
        # neither a metric nor its stored narrative.
        assert data["n_segments"] is None
        assert data["finding_text"] != "summary text"
        assert "could not be read" in data["finding_text"].lower()
        # Metrics could not be read, so none are asserted: the fallback must not
        # invent a reassuring FROH of 0.0 for a row it failed to parse.
        assert data["froh"] is None
        assert data["evaluable"] is False
        assert data["indeterminate_reason"] == "detail_unavailable"

    def test_quarantined_row_is_indistinguishable_from_absence(
        self, _env: sa.Engine, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The dedicated route must not reveal that a hidden row exists."""
        import json as _json

        import backend.api.routes.roh as roh_route
        from backend.db.tables import findings

        with _env.begin() as conn:
            conn.execute(
                sa.insert(findings),
                {
                    "module": "roh",
                    "category": "autozygosity",
                    "evidence_level": 1,
                    "gene_symbol": "CYP2D6",
                    "drug": "tamoxifen",
                    "finding_text": "CYP2D6 tamoxifen dose guidance must remain withheld.",
                    "detail_json": _json.dumps(
                        {
                            "froh": 0.0,
                            "autosomal_snps_used": 361,
                            "n_segments": 0,
                            "segments": [],
                        }
                    ),
                },
            )

        monkeypatch.setattr(roh_route, "resolve_sample_engine", lambda _sample_id: _env)
        response = roh_route.list_findings(sample_id=1)

        assert response is None


class TestEvaluabilityAtTheApi:
    """#2177 — the withheld state has to survive the API boundary."""

    def _reseed(self, engine: sa.Engine, rows: list[dict]) -> None:
        with engine.begin() as conn:
            conn.execute(sa.delete(raw_variants))
            if rows:
                conn.execute(sa.insert(raw_variants), rows)

    def test_unevaluable_sample_withholds_froh(self, _env: sa.Engine, client: TestClient) -> None:
        self._reseed(
            _env,
            [{"rsid": "r1", "chrom": "1", "pos": 1_000_000, "genotype": "AA"}],
        )
        assert client.post("/api/analysis/roh/run?sample_id=1").status_code == 200

        data = client.get("/api/analysis/roh/findings?sample_id=1").json()
        assert data["froh"] is None
        assert data["evaluable"] is False
        assert data["indeterminate_reason"] == "no_segment_eligible_region"
        # The narrative states this reason's own cause, not the other one's.
        assert "without a coverage gap" in data["finding_text"]
        assert data["total_roh_kb"] is None
        assert data["n_segments"] is None
        assert data["autosomal_snps_used"] == 1
        assert "not assessed" in data["finding_text"].lower()
        assert "typical result" not in data["finding_text"].lower()

    def test_legacy_row_without_the_gate_is_not_served_as_zero(
        self, _env: sa.Engine, client: TestClient
    ) -> None:
        # A finding persisted before this gate existed records the marker count
        # but no evaluability keys, and its stored text asserts the very
        # negative the gate withholds. Re-deriving from the stored count is what
        # stops the defect surviving in samples that are never re-analysed.
        import json as _json

        from backend.db.tables import findings

        with _env.begin() as conn:
            conn.execute(
                sa.insert(findings),
                [
                    {
                        "module": "roh",
                        "category": "autozygosity",
                        "evidence_level": 1,
                        "finding_text": (
                            "No long runs of homozygosity were detected (FROH ≈ 0). "
                            "This is the typical result."
                        ),
                        "detail_json": _json.dumps(
                            {
                                "froh": 0.0,
                                "total_roh_kb": 0.0,
                                "longest_kb": 0.0,
                                "n_segments": 0,
                                "autosomal_snps_used": 30,
                                "segments": [],
                            }
                        ),
                    }
                ],
            )

        data = client.get("/api/analysis/roh/findings?sample_id=1").json()
        assert data["froh"] is None
        assert data["evaluable"] is False
        assert data["indeterminate_reason"] == "insufficient_autosomal_markers"
        assert "typical result" not in data["finding_text"].lower()
        assert "not assessed" in data["finding_text"].lower()
        # The narrative must state the cause the reason field names. Asserting
        # only "not assessed" let the route serve the marker-region wording
        # beside an insufficient-markers reason — one payload, two causes.
        assert "30 callable autosomal SNP(s)" in data["finding_text"]
        assert "without a coverage gap" not in data["finding_text"]
        # No measured quantity is asserted either.
        assert data["total_roh_kb"] is None
        assert data["longest_kb"] is None
        assert data["n_segments"] is None

    def test_legacy_row_above_the_count_floor_is_rechecked_structurally(
        self, _env: sa.Engine, client: TestClient
    ) -> None:
        # 200 markers clear the count-only floor, but they sit in a 199 kb span
        # that no run could occupy. The count alone cannot see that, so the
        # stored verdict is re-derived from the sample's own marker positions.
        import json as _json

        from backend.db.tables import findings

        self._reseed(
            _env,
            [
                {"rsid": f"r{i}", "chrom": "1", "pos": 1_000_000 + i * 1_000, "genotype": "AA"}
                for i in range(200)
            ],
        )
        with _env.begin() as conn:
            conn.execute(
                sa.insert(findings),
                {
                    "module": "roh",
                    "category": "autozygosity",
                    "evidence_level": 1,
                    "finding_text": (
                        "No long runs of homozygosity were detected (FROH ≈ 0). "
                        "This is the typical result."
                    ),
                    "detail_json": _json.dumps(
                        {"froh": 0.0, "n_segments": 0, "autosomal_snps_used": 200}
                    ),
                },
            )

        data = client.get("/api/analysis/roh/findings?sample_id=1").json()
        assert data["evaluable"] is False
        assert data["indeterminate_reason"] == "no_segment_eligible_region"
        assert data["froh"] is None
        assert "typical result" not in data["finding_text"].lower()

    def test_row_with_no_recorded_count_reads_coverage_from_the_sample(
        self, _env: sa.Engine, client: TestClient
    ) -> None:
        # A blob that records no marker count at all must not be treated as a
        # count of zero — that would manufacture "0 callable autosomal SNP(s)"
        # for a sample nobody measured. The fixture has 361 markers spanning an
        # eligible region, so reading the sample yields a real negative.
        import json as _json

        from backend.db.tables import findings

        with _env.begin() as conn:
            conn.execute(
                sa.insert(findings),
                {
                    "module": "roh",
                    "category": "autozygosity",
                    "evidence_level": 1,
                    "finding_text": "No long runs of homozygosity were detected (FROH ≈ 0).",
                    "detail_json": _json.dumps({"froh": 0.0}),
                },
            )

        data = client.get("/api/analysis/roh/findings?sample_id=1").json()
        assert data["evaluable"] is True
        assert data["autosomal_snps_used"] == 361
        assert "0 callable autosomal SNP(s)" not in data["finding_text"]
        # The blob recorded only froh, so the companion metrics must come back
        # null rather than 0.0 kb / 0 segments — three measurements nothing took.
        assert data["froh"] == 0.0
        assert data["total_roh_kb"] is None
        assert data["longest_kb"] is None
        assert data["n_segments"] is None

    def test_metricless_row_stays_indeterminate_on_a_good_sample(
        self, _env: sa.Engine, client: TestClient
    ) -> None:
        # Reading the sample can show that a scan COULD have run; it cannot
        # reconstruct a result that was never stored. The fixture has ample
        # eligible coverage, yet a row carrying no FROH must still be withheld
        # rather than have a zero invented for it.
        import json as _json

        from backend.db.tables import findings

        with _env.begin() as conn:
            conn.execute(
                sa.insert(findings),
                {
                    "module": "roh",
                    "category": "autozygosity",
                    "evidence_level": 1,
                    "finding_text": (
                        "No long runs of homozygosity were detected (FROH ≈ 0). "
                        "This is the typical result."
                    ),
                    "detail_json": _json.dumps({}),
                },
            )

        data = client.get("/api/analysis/roh/findings?sample_id=1").json()
        assert data["evaluable"] is False
        assert data["indeterminate_reason"] == "detail_unavailable"
        assert data["froh"] is None
        assert "typical result" not in data["finding_text"].lower()

    def test_explicit_evaluable_true_without_a_metric_is_withheld(
        self, _env: sa.Engine, client: TestClient
    ) -> None:
        # A stored verdict is not self-certifying: `evaluable: true` beside a
        # null FROH would publish the contradictory pair evaluable=true /
        # froh=null, so the row is unavailable until re-run.
        import json as _json

        from backend.db.tables import findings

        with _env.begin() as conn:
            conn.execute(
                sa.insert(findings),
                {
                    "module": "roh",
                    "category": "autozygosity",
                    "evidence_level": 1,
                    "finding_text": (
                        "No long runs of homozygosity were detected (FROH ≈ 0). "
                        "This is the typical result."
                    ),
                    "detail_json": _json.dumps(
                        {"evaluable": True, "froh": None, "autosomal_snps_used": 600_000}
                    ),
                },
            )

        data = client.get("/api/analysis/roh/findings?sample_id=1").json()
        assert data["evaluable"] is False
        assert data["indeterminate_reason"] == "detail_unavailable"
        assert data["froh"] is None
        assert "typical result" not in data["finding_text"].lower()

    @pytest.mark.parametrize("stored", ["false", "true", 0, 1, "yes"])
    def test_non_boolean_evaluable_is_not_a_verdict(
        self, _env: sa.Engine, client: TestClient, stored: object
    ) -> None:
        # A schema-drifted verdict is not a verdict. "false" is a truthy string,
        # so reading it as an explicit true would skip the count floor and the
        # structural check and expose a stored FROH from a one-marker sample.
        import json as _json

        from backend.db.tables import findings

        self._reseed(_env, [{"rsid": "r1", "chrom": "1", "pos": 1_000_000, "genotype": "AA"}])
        with _env.begin() as conn:
            conn.execute(
                sa.insert(findings),
                {
                    "module": "roh",
                    "category": "autozygosity",
                    "evidence_level": 1,
                    "finding_text": (
                        "No long runs of homozygosity were detected (FROH ≈ 0). "
                        "This is the typical result."
                    ),
                    "detail_json": _json.dumps(
                        {"evaluable": stored, "froh": 0.0, "autosomal_snps_used": 1}
                    ),
                },
            )

        data = client.get("/api/analysis/roh/findings?sample_id=1").json()
        assert data["evaluable"] is False
        assert data["indeterminate_reason"] == "detail_unavailable"
        assert data["froh"] is None
        assert "typical result" not in data["finding_text"].lower()

    def test_explicit_true_verdict_is_revalidated_against_coverage(
        self, _env: sa.Engine, client: TestClient
    ) -> None:
        # An explicit `evaluable: true` with a numeric froh is not
        # self-certifying: a row whose sample cannot support a scan must be
        # withheld even though its verdict and metric are both well-formed.
        import json as _json

        from backend.db.tables import findings

        self._reseed(_env, [{"rsid": "r1", "chrom": "1", "pos": 1_000_000, "genotype": "AA"}])
        with _env.begin() as conn:
            conn.execute(
                sa.insert(findings),
                {
                    "module": "roh",
                    "category": "autozygosity",
                    "evidence_level": 1,
                    "finding_text": (
                        "No long runs of homozygosity were detected (FROH ≈ 0). "
                        "This is the typical result."
                    ),
                    "detail_json": _json.dumps(
                        {"evaluable": True, "froh": 0.0, "autosomal_snps_used": 1}
                    ),
                },
            )

        data = client.get("/api/analysis/roh/findings?sample_id=1").json()
        assert data["evaluable"] is False
        assert data["indeterminate_reason"] == "insufficient_autosomal_markers"
        assert data["froh"] is None
        assert "typical result" not in data["finding_text"].lower()

    def test_withheld_response_asserts_no_measurement_at_all(
        self, _env: sa.Engine, client: TestClient
    ) -> None:
        # A row carrying real segments whose sample can no longer support a
        # scan. Pins the WHOLE withheld contract rather than one field at a
        # time: every measured quantity has been withheld field-by-field over
        # several rounds, so this asserts the complete shape and will fail if a
        # future measured field is added without being withheld too.
        import json as _json

        from backend.db.tables import findings

        self._reseed(_env, [{"rsid": "r1", "chrom": "1", "pos": 1_000_000, "genotype": "AA"}])
        with _env.begin() as conn:
            conn.execute(
                sa.insert(findings),
                {
                    "module": "roh",
                    "category": "autozygosity",
                    "evidence_level": 1,
                    "finding_text": "No long runs of homozygosity were detected (FROH ≈ 0).",
                    "detail_json": _json.dumps(
                        {
                            "evaluable": True,
                            "froh": 0.004,
                            "total_roh_kb": 11080.0,
                            "longest_kb": 6200.0,
                            "n_segments": 2,
                            "autosomal_snps_used": 1,
                            "segments": [
                                {
                                    "chrom": "4",
                                    "start": 1_000_000,
                                    "end": 7_200_000,
                                    "length_kb": 6200.0,
                                    "n_snps": 620,
                                },
                                {
                                    "chrom": "9",
                                    "start": 2_000_000,
                                    "end": 6_880_000,
                                    "length_kb": 4880.0,
                                    "n_snps": 488,
                                },
                            ],
                            "segments_truncated": True,
                        }
                    ),
                },
            )

        data = client.get("/api/analysis/roh/findings?sample_id=1").json()

        assert data["evaluable"] is False
        assert data["indeterminate_reason"] == "insufficient_autosomal_markers"
        # Nothing measured survives — not the summary, and not the segment list
        # naming specific chromosomes and coordinates.
        assert data["froh"] is None
        assert data["total_roh_kb"] is None
        assert data["longest_kb"] is None
        assert data["n_segments"] is None
        assert data["segments"] == []
        assert data["segments_truncated"] is False
        # The observed marker count is retained: it is measured, not derived.
        assert data["autosomal_snps_used"] == 1
        assert "typical result" not in data["finding_text"].lower()

    def test_explicit_true_verdict_survives_on_a_covered_sample(
        self, _env: sa.Engine, client: TestClient
    ) -> None:
        # The counterpart control: revalidation must not withhold a fresh,
        # correctly-written row. The fixture's 361 markers span an eligible
        # region, so the stored verdict stands and its metric is served.
        import json as _json

        from backend.db.tables import findings

        with _env.begin() as conn:
            conn.execute(
                sa.insert(findings),
                {
                    "module": "roh",
                    "category": "autozygosity",
                    "evidence_level": 1,
                    "finding_text": "No long runs of homozygosity were detected (FROH ≈ 0).",
                    "detail_json": _json.dumps(
                        {"evaluable": True, "froh": 0.0, "autosomal_snps_used": 361}
                    ),
                },
            )

        data = client.get("/api/analysis/roh/findings?sample_id=1").json()
        assert data["evaluable"] is True
        assert data["froh"] == 0.0
        assert data["indeterminate_reason"] is None

    def test_false_verdict_without_a_count_reads_the_sample(
        self, _env: sa.Engine, client: TestClient
    ) -> None:
        # An explicit `false` with a coverage-dependent reason but no recorded
        # count must not quote zero: the reason's narrative names the marker
        # count, so the count is read from the sample instead of assumed.
        import json as _json

        from backend.db.tables import findings

        self._reseed(
            _env,
            [
                {"rsid": f"r{i}", "chrom": "1", "pos": 1_000_000 + i * 1_000, "genotype": "AA"}
                for i in range(150)
            ],
        )
        with _env.begin() as conn:
            conn.execute(
                sa.insert(findings),
                {
                    "module": "roh",
                    "category": "autozygosity",
                    "evidence_level": 1,
                    "finding_text": "stored narrative",
                    "detail_json": _json.dumps(
                        {
                            "evaluable": False,
                            "indeterminate_reason": "no_segment_eligible_region",
                            "froh": None,
                        }
                    ),
                },
            )

        data = client.get("/api/analysis/roh/findings?sample_id=1").json()
        assert data["evaluable"] is False
        assert data["indeterminate_reason"] == "no_segment_eligible_region"
        # The count came from the sample, not from a default.
        assert data["autosomal_snps_used"] == 150
        assert "(150 callable autosomal SNP(s)" in data["finding_text"]
        # Parenthesised so this cannot be satisfied by the trailing "0" of "150".
        assert "(0 callable autosomal SNP(s)" not in data["finding_text"]

    def test_unrecognised_stored_reason_does_not_borrow_another_cause(
        self, _env: sa.Engine, client: TestClient
    ) -> None:
        # unevaluable_text branches on the reason, so a drifted value would fall
        # through to the marker-region wording and state a cause the data never
        # supported — for a sample that has ample markers.
        import json as _json

        from backend.db.tables import findings

        with _env.begin() as conn:
            conn.execute(
                sa.insert(findings),
                {
                    "module": "roh",
                    "category": "autozygosity",
                    "evidence_level": 1,
                    "finding_text": "stored narrative",
                    "detail_json": _json.dumps(
                        {
                            "evaluable": False,
                            "indeterminate_reason": "some_future_reason",
                            "froh": None,
                            "autosomal_snps_used": 361,
                        }
                    ),
                },
            )

        data = client.get("/api/analysis/roh/findings?sample_id=1").json()
        assert data["evaluable"] is False
        assert data["indeterminate_reason"] == "detail_unavailable"
        assert "without a coverage gap" not in data["finding_text"]
        assert "could not be read" in data["finding_text"].lower()

    def test_no_recorded_count_on_an_ineligible_sample_is_withheld(
        self, _env: sa.Engine, client: TestClient
    ) -> None:
        # Same absent count, but the sample genuinely cannot hold a run: the
        # withheld narrative must quote the count actually read, not zero.
        import json as _json

        from backend.db.tables import findings

        self._reseed(
            _env,
            [
                {"rsid": f"r{i}", "chrom": "1", "pos": 1_000_000 + i * 1_000, "genotype": "AA"}
                for i in range(150)
            ],
        )
        with _env.begin() as conn:
            conn.execute(
                sa.insert(findings),
                {
                    "module": "roh",
                    "category": "autozygosity",
                    "evidence_level": 1,
                    "finding_text": "No long runs of homozygosity were detected (FROH ≈ 0).",
                    "detail_json": _json.dumps({"froh": 0.0}),
                },
            )

        data = client.get("/api/analysis/roh/findings?sample_id=1").json()
        assert data["evaluable"] is False
        assert data["indeterminate_reason"] == "no_segment_eligible_region"
        assert data["autosomal_snps_used"] == 150
        assert "150 callable autosomal SNP(s)" in data["finding_text"]

    def test_unreadable_detail_is_quarantined(self, _env: sa.Engine, client: TestClient) -> None:
        # A row whose stored state cannot be parsed cannot be proven free of a
        # held clinical payload.  It must therefore remain indistinguishable
        # from no row rather than confirming that hidden state exists.
        from backend.db.tables import findings

        with _env.begin() as conn:
            conn.execute(
                sa.insert(findings),
                {
                    "module": "roh",
                    "category": "autozygosity",
                    "evidence_level": 1,
                    "finding_text": (
                        "No long runs of homozygosity were detected (FROH ≈ 0). "
                        "This is the typical result."
                    ),
                    "detail_json": "{not valid json",
                },
            )

        assert client.get("/api/analysis/roh/findings?sample_id=1").json() is None

    @pytest.mark.parametrize("blob", ["[]", "5", '"text"', "null"])
    def test_valid_json_that_is_not_an_object_is_quarantined(
        self, _env: sa.Engine, client: TestClient, blob: str
    ) -> None:
        # Distinct from malformed JSON: these values parse, but none is a
        # finding object that can be inspected for held clinical content.  The
        # shared presentation gate therefore quarantines the row.
        from backend.db.tables import findings

        with _env.begin() as conn:
            conn.execute(
                sa.insert(findings),
                {
                    "module": "roh",
                    "category": "autozygosity",
                    "evidence_level": 1,
                    "finding_text": (
                        "No long runs of homozygosity were detected (FROH ≈ 0). "
                        "This is the typical result."
                    ),
                    "detail_json": blob,
                },
            )

        response = client.get("/api/analysis/roh/findings?sample_id=1")
        assert response.status_code == 200
        assert response.json() is None

    def _insert_roh_row(self, engine: sa.Engine, detail: str) -> None:
        from backend.db.tables import findings

        with engine.begin() as conn:
            conn.execute(
                sa.insert(findings),
                {
                    "module": "roh",
                    "category": "autozygosity",
                    "evidence_level": 1,
                    "finding_text": "stored narrative",
                    "detail_json": detail,
                },
            )

    def _read_direct_paths(self, engine: sa.Engine):
        """Read one stored row through both patient-visible production mappers."""
        import backend.api.routes.roh as roh_route
        from backend.api.routes.findings import _row_to_response
        from backend.db.tables import findings

        with patch.object(roh_route, "resolve_sample_engine", return_value=engine):
            dedicated = roh_route.list_findings(sample_id=1)
        with engine.connect() as conn:
            row = conn.execute(
                sa.select(findings).where(
                    findings.c.module == "roh", findings.c.category == "autozygosity"
                )
            ).one()
        return dedicated, _row_to_response(row, engine)

    @pytest.mark.parametrize("bad_froh", [-3, 12, -0.0001, 1.0001])
    def test_out_of_range_froh_is_withheld_not_served(
        self, _env: sa.Engine, client: TestClient, bad_froh: float
    ) -> None:
        # FROH is a fraction of the autosomal genome, so it is bounded by
        # [0, 1] by construction. A drifted blob holding -3 or 12 is not a
        # measurement, and serving it as one is the same substitution this
        # module exists to prevent -- one field over. Every other field here is
        # already validated against the values it may hold.
        import json as _json

        self._insert_roh_row(
            _env,
            _json.dumps({"froh": bad_froh, "autosomal_snps_used": 361, "n_segments": 1}),
        )

        response = client.get("/api/analysis/roh/findings?sample_id=1")
        assert response.status_code == 200
        data = response.json()
        assert data["evaluable"] is False
        assert data["indeterminate_reason"] == "detail_unavailable"
        assert data["froh"] is None
        assert data["finding_text"] != "stored narrative"

    @pytest.mark.parametrize("good_froh", [0.0, 0.25, 1.0])
    def test_in_range_froh_is_still_served(
        self, _env: sa.Engine, client: TestClient, good_froh: float
    ) -> None:
        # Counterpart control for the bound above: without it, a guard that
        # rejected *every* stored FROH would still satisfy the rejection test,
        # and the module would withhold every real measurement it ever made.
        import json as _json

        self._insert_roh_row(
            _env,
            _json.dumps(
                {
                    "froh": good_froh,
                    "autosomal_snps_used": 361,
                    "n_segments": 0 if good_froh == 0 else 1,
                }
            ),
        )

        data = client.get("/api/analysis/roh/findings?sample_id=1").json()
        assert data["evaluable"] is True
        assert data["froh"] == good_froh
        assert data["indeterminate_reason"] is None
        assert data["finding_text"] == "stored narrative"

    def test_drifted_marker_count_withholds_across_read_paths(self, _env: sa.Engine) -> None:
        # This sample holds 361 callable autosomal markers (160 chr1 + 200 chr2
        # + 1 chr3 het; the no-call and chrX call are excluded). A stored result
        # claiming 600000 belongs to a different marker snapshot: reporting the
        # current count beside that old FROH would combine two measurements,
        # while retaining 600000 would expose a count this sample does not have.
        # Both the dedicated response and the shared generic normalizer must
        # therefore withhold and report only the observed current count.
        import json as _json

        detail = {"froh": 0.0, "autosomal_snps_used": 600_000, "n_segments": 0}
        self._insert_roh_row(
            _env,
            _json.dumps(detail),
        )

        dedicated, generic = self._read_direct_paths(_env)
        assert dedicated is not None
        assert dedicated.evaluable is False
        assert dedicated.froh is None
        assert dedicated.indeterminate_reason == "detail_unavailable"
        assert dedicated.autosomal_snps_used == 361
        assert dedicated.segments == []

        assert generic.detail is not None
        assert generic.detail["evaluable"] is False
        assert generic.detail["froh"] is None
        assert generic.detail["segments"] == []
        assert generic.detail["indeterminate_reason"] == "detail_unavailable"
        assert generic.detail["autosomal_snps_used"] == 361
        assert generic.finding_text != "stored narrative"

    @pytest.mark.parametrize(
        "bad_count",
        [-1, "361", 361.0, True, None, 10**400],
        ids=["negative", "string", "float", "boolean", "null", "oversized"],
    )
    def test_malformed_stored_count_is_not_rescued_across_read_paths(
        self, _env: sa.Engine, bad_count
    ) -> None:
        # Missing coverage is a legitimate legacy subset, but a present value
        # this writer cannot emit proves the persisted metrics are malformed.
        # Re-reading today's sample must not bless those old metrics; both
        # patient-visible mappers withhold them and report only the observed
        # current count.
        import json as _json

        self._insert_roh_row(
            _env,
            _json.dumps({"froh": 0.0, "autosomal_snps_used": bad_count, "n_segments": 0}),
        )

        dedicated, generic = self._read_direct_paths(_env)
        assert dedicated is not None
        assert dedicated.evaluable is False
        assert dedicated.froh is None
        assert dedicated.indeterminate_reason == "detail_unavailable"
        assert dedicated.autosomal_snps_used == 361
        assert dedicated.segments == []

        assert generic.detail is not None
        assert generic.detail["evaluable"] is False
        assert generic.detail["froh"] is None
        assert generic.detail["indeterminate_reason"] == "detail_unavailable"
        assert generic.detail["autosomal_snps_used"] == 361
        assert generic.detail["segments"] == []
        assert generic.finding_text != "stored narrative"

    @pytest.mark.parametrize(
        ("orphan_reason", "include_count"),
        [
            ("no_segment_eligible_region", True),
            ("some_future_reason", True),
            ([], True),
            ("no_segment_eligible_region", False),
        ],
        ids=["known", "unknown", "non_string", "missing_count"],
    )
    def test_reason_without_false_verdict_is_withheld_across_read_paths(
        self, _env: sa.Engine, orphan_reason, include_count: bool
    ) -> None:
        # The writer records a reason only beside `evaluable: false`. A legacy
        # row that omits the verdict but carries a reason is not a supported
        # projection: the generic mapper previously preserved the orphan while
        # the dedicated mapper blanked it, yielding two accounts of one row.
        import json as _json

        detail = {
            "froh": 0.0,
            "n_segments": 0,
            "indeterminate_reason": orphan_reason,
        }
        if include_count:
            detail["autosomal_snps_used"] = 361
        self._insert_roh_row(_env, _json.dumps(detail))

        dedicated, generic = self._read_direct_paths(_env)
        assert dedicated is not None
        assert dedicated.evaluable is False
        assert dedicated.froh is None
        assert dedicated.indeterminate_reason == "detail_unavailable"
        assert dedicated.autosomal_snps_used == 361

        assert generic.detail is not None
        assert generic.detail["evaluable"] is False
        assert generic.detail["froh"] is None
        assert generic.detail["indeterminate_reason"] == "detail_unavailable"
        assert generic.detail["autosomal_snps_used"] == 361
        assert generic.finding_text != "stored narrative"

    def test_structural_reason_the_sample_refutes_is_downgraded(
        self, _env: sa.Engine, client: TestClient
    ) -> None:
        # `no_segment_eligible_region` asserts that no autosome carries a
        # qualifying block. This sample has one (160 chr1 markers spanning
        # 1.59 Mb without a gap), so honouring the stored reason would state a
        # structural absence the sample itself refutes -- which is what happens
        # whenever a sample is re-imported after the row was written. Unlike the
        # count-based reason, only a coverage scan can catch this, so the scan
        # is run for this reason specifically.
        import json as _json

        self._insert_roh_row(
            _env,
            _json.dumps(
                {
                    "froh": 0.0,
                    "evaluable": False,
                    "indeterminate_reason": "no_segment_eligible_region",
                    "autosomal_snps_used": 600_000,
                }
            ),
        )

        data = client.get("/api/analysis/roh/findings?sample_id=1").json()
        assert data["evaluable"] is False
        assert data["froh"] is None
        assert data["indeterminate_reason"] == "detail_unavailable"
        assert "without a coverage gap" not in data["finding_text"]
        assert "could not be read" in data["finding_text"].lower()

    def test_structural_reason_the_sample_confirms_is_honoured(
        self, _env: sa.Engine, client: TestClient
    ) -> None:
        # Counterpart control: the downgrade must fire only when the sample
        # actually refutes the reason. These markers clear the count floor but
        # sit 5 Mb apart, so every gap exceeds MAX_GAP_KB and no run could
        # occupy any of them -- the stored reason is true and must survive.
        import json as _json

        self._reseed(
            _env,
            [
                {
                    "rsid": f"sparse{i}",
                    "chrom": "1",
                    "pos": 1_000_000 + i * 5_000_000,
                    "genotype": "CC",
                }
                for i in range(150)
            ],
        )
        self._insert_roh_row(
            _env,
            _json.dumps(
                {
                    "froh": 0.0,
                    "evaluable": False,
                    "indeterminate_reason": "no_segment_eligible_region",
                    "autosomal_snps_used": 600_000,
                }
            ),
        )

        data = client.get("/api/analysis/roh/findings?sample_id=1").json()
        assert data["evaluable"] is False
        assert data["indeterminate_reason"] == "no_segment_eligible_region"
        assert "without a coverage gap" in data["finding_text"]
        # The count narrated is the observed one, not the drifted 600000.
        assert data["autosomal_snps_used"] == 150

    def test_stored_count_below_the_floor_is_not_rescued_by_the_sample(
        self, _env: sa.Engine, client: TestClient
    ) -> None:
        # The other half of the same change, and the reason the observed count
        # does not simply replace the stored one: this sample *could* be
        # assessed today, but the row records a marker count below the floor, so
        # its stored FROH cannot be this sample's. Letting the sample's coverage
        # rescue it would reinstate #2177 from the other side -- serving a
        # measurement computed from a marker set that is not this sample's.
        import json as _json

        self._insert_roh_row(
            _env,
            _json.dumps({"froh": 0.0, "autosomal_snps_used": 30, "n_segments": 0}),
        )

        data = client.get("/api/analysis/roh/findings?sample_id=1").json()
        assert data["evaluable"] is False
        assert data["froh"] is None
        assert data["indeterminate_reason"] == "insufficient_autosomal_markers"
        # The quoted count is the one that explains the withholding. Reporting
        # the 361 the sample holds beside "insufficient markers" would state a
        # cause its own number contradicts.
        assert "30 callable autosomal SNP(s)" in data["finding_text"]

    def test_legacy_row_with_adequate_markers_is_untouched(self, _env: sa.Engine) -> None:
        # The counterpart control: a well-covered legacy row must keep serving
        # its stored negative, so the re-derivation cannot quietly withhold
        # every finding written before the gate.
        import json as _json

        from backend.db.tables import findings

        with _env.begin() as conn:
            conn.execute(
                sa.insert(findings),
                [
                    {
                        "module": "roh",
                        "category": "autozygosity",
                        "evidence_level": 1,
                        "finding_text": "No long runs of homozygosity were detected (FROH ≈ 0).",
                        "detail_json": _json.dumps(
                            {
                                "froh": 0.0,
                                "n_segments": 0,
                                "autosomal_snps_used": 361,
                                "segments": [],
                            }
                        ),
                    }
                ],
            )

        dedicated, generic = self._read_direct_paths(_env)
        assert dedicated is not None
        assert dedicated.evaluable is True
        assert dedicated.froh == 0.0
        assert dedicated.indeterminate_reason is None
        assert dedicated.finding_text.startswith("No long runs")
        # Recorded metrics are served; unrecorded ones stay null.
        assert dedicated.n_segments == 0
        assert dedicated.total_roh_kb is None
        assert dedicated.longest_kb is None

        assert generic.detail is not None
        assert generic.detail["froh"] == 0.0
        assert generic.finding_text.startswith("No long runs")

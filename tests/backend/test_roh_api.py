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
    with patch("backend.api.routes.risk_common.get_registry", return_value=registry):
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

    def test_unreadable_detail_is_indeterminate_not_typical(
        self, _env: sa.Engine, client: TestClient
    ) -> None:
        # A row whose stored state cannot be parsed is not a row that can be
        # vouched for: it must not keep serving its stored negative.
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

        data = client.get("/api/analysis/roh/findings?sample_id=1").json()
        assert data["evaluable"] is False
        assert data["indeterminate_reason"] == "detail_unavailable"
        assert data["froh"] is None
        assert "typical result" not in data["finding_text"].lower()
        assert "could not be read" in data["finding_text"].lower()

    @pytest.mark.parametrize("blob", ["[]", "5", '"text"', "null"])
    def test_valid_json_that_is_not_an_object_withholds_instead_of_500(
        self, _env: sa.Engine, client: TestClient, blob: str
    ) -> None:
        # Distinct from the malformed-JSON case above: these all *parse*, so
        # json.JSONDecodeError never fires. They parse to a list/int/str/None,
        # and `segments_truncated` calls `.get` before its `and evaluable` can
        # short-circuit — an AttributeError that the route's except tuple does
        # not catch, so the response was a 500 rather than the withheld result.
        # A 500 is the one outcome this route exists to prevent: it tells the
        # caller nothing, where the contract is to withhold and say why.
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
        data = response.json()
        assert data["evaluable"] is False
        assert data["indeterminate_reason"] == "detail_unavailable"
        assert data["froh"] is None
        assert data["segments"] == []
        assert data["segments_truncated"] is False
        assert "typical result" not in data["finding_text"].lower()

    def test_legacy_row_with_adequate_markers_is_untouched(
        self, _env: sa.Engine, client: TestClient
    ) -> None:
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
                                "autosomal_snps_used": 600_000,
                                "segments": [],
                            }
                        ),
                    }
                ],
            )

        data = client.get("/api/analysis/roh/findings?sample_id=1").json()
        assert data["evaluable"] is True
        assert data["froh"] == 0.0
        assert data["indeterminate_reason"] is None
        assert data["finding_text"].startswith("No long runs")
        # Recorded metrics are served; unrecorded ones stay null.
        assert data["n_segments"] == 0
        assert data["total_roh_kb"] is None
        assert data["longest_kb"] is None

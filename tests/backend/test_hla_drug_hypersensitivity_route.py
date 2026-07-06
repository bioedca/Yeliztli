"""Route coverage for GET /api/hla/drug-hypersensitivity (Wave D / SW-D2).

Seeds a sample's ``hla_calls`` (a carrier case) and asserts the endpoint surfaces
the at-risk assessment; also the empty-sample (available=False) and missing-sample
(404) contracts. Fixture mirrors ``test_fh_route_coverage.py`` (patch get_settings
+ reset the DB registry against the temp data dir).
"""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path
from unittest.mock import patch

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from backend.analysis.hla_drug_hypersensitivity import assess_drug_hypersensitivity
from backend.analysis.hla_resolver import ResolvedHLACall
from backend.api.routes.hla import DrugRiskAssessmentResponse
from backend.config import Settings
from backend.db.connection import reset_registry
from backend.db.sample_schema import create_sample_tables
from backend.db.tables import hla_calls, reference_metadata, samples


def _client(tmp_data_dir: Path, calls: list[dict]) -> Generator[TestClient, None, None]:
    settings = Settings(data_dir=tmp_data_dir, wal_mode=False)
    ref_engine = sa.create_engine(f"sqlite:///{settings.reference_db_path}")
    reference_metadata.create_all(ref_engine)

    (tmp_data_dir / "samples").mkdir(parents=True, exist_ok=True)
    sample_db_path = tmp_data_dir / "samples" / "sample_1.db"
    sample_engine = sa.create_engine(f"sqlite:///{sample_db_path}")
    create_sample_tables(sample_engine)

    with ref_engine.begin() as conn:
        conn.execute(
            samples.insert().values(
                id=1,
                name="HLA Sample",
                db_path="samples/sample_1.db",
                file_format="v5",
                file_hash="hlad2",
            )
        )
    if calls:
        with sample_engine.begin() as conn:
            conn.execute(sa.insert(hla_calls), calls)

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


def _row(locus, a1, a2, *, prob=0.95, low=0) -> dict:
    return {
        "locus": locus,
        "allele1": a1,
        "allele2": a2,
        "prob": prob,
        "matching": 0.9,
        "low_confidence": low,
        "ancestry_model": "European",
        "source": "hibag",
    }


def _resolved_call(locus, a1, a2, *, prob=0.95, low=False) -> ResolvedHLACall:
    return ResolvedHLACall(
        locus=locus,
        allele1=a1,
        allele2=a2,
        prob=prob,
        low_confidence=low,
        source="hibag",
        ancestry_model="European",
    )


@pytest.fixture
def carrier_client(tmp_data_dir: Path) -> Generator[TestClient, None, None]:
    # B*57:01 carrier (abacavir at-risk) + an A call without A*31:01.
    yield from _client(tmp_data_dir, [_row("B", "57:01", "07:02"), _row("A", "01:01", "02:01")])


@pytest.fixture
def a3101_carrier_client(tmp_data_dir: Path) -> Generator[TestClient, None, None]:
    yield from _client(tmp_data_dir, [_row("A", "31:01", "02:01")])


@pytest.fixture
def empty_client(tmp_data_dir: Path) -> Generator[TestClient, None, None]:
    yield from _client(tmp_data_dir, [])


def test_b1502_response_model_preserves_phenytoin_alert_fields() -> None:
    report = assess_drug_hypersensitivity([_resolved_call("B", "15:02", "07:02")])
    b1502 = next(a for a in report.assessments if a.allele == "HLA-B*15:02")
    payload = DrugRiskAssessmentResponse(**vars(b1502)).model_dump()

    assert payload["status"] == "at_risk"
    assert "phenytoin" in payload["drugs"]
    assert "fosphenytoin" in payload["drugs"]
    assert "phenytoin-naive" in payload["recommendation"].lower()
    assert "PMID:32779747" in payload["citations"]


def test_b1511_response_model_preserves_carbamazepine_alert_fields() -> None:
    report = assess_drug_hypersensitivity([_resolved_call("B", "15:11", "07:02")])
    b1511 = next(a for a in report.assessments if a.allele == "HLA-B*15:11")
    payload = DrugRiskAssessmentResponse(**vars(b1511)).model_dump()

    assert payload["status"] == "at_risk"
    assert payload["drugs"] == ["carbamazepine"]
    assert "alternative" in payload["recommendation"].lower()
    assert "HLA-B*15:02 screening alone" in payload["notes"][0]
    assert "PMID:21204807" in payload["citations"]
    assert "PMID:38570725" in payload["citations"]


def test_low_confidence_response_model_is_indeterminate() -> None:
    report = assess_drug_hypersensitivity(
        [_resolved_call("B", "57:01", "07:02", prob=0.4, low=True)]
    )
    b5701 = next(a for a in report.assessments if a.allele == "HLA-B*57:01")
    payload = DrugRiskAssessmentResponse(**vars(b5701)).model_dump()

    assert report.any_at_risk is False
    assert payload["status"] == "low_confidence"
    assert payload["carried"] is True
    assert payload["low_confidence"] is True
    assert "do not prescribe" not in payload["recommendation"].lower()
    assert "positive or negative" in payload["recommendation"].lower()


class TestDrugHypersensitivityRoute:
    def test_carrier_surfaces_at_risk(self, carrier_client: TestClient) -> None:
        resp = carrier_client.get("/api/hla/drug-hypersensitivity", params={"sample_id": 1})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["available"] is True
        assert body["any_at_risk"] is True
        assert body["research_use_only"] is True
        assert body["caveat"]
        by = {a["allele"]: a for a in body["assessments"]}
        assert by["HLA-B*57:01"]["status"] == "at_risk"
        assert "abacavir" in by["HLA-B*57:01"]["drugs"]
        # A locus typed but no A*31:01 → assessable negative, not "not_typed".
        assert by["HLA-A*31:01"]["status"] == "no_risk_allele"

    def test_a3101_carrier_surfaces_at_risk(self, a3101_carrier_client: TestClient) -> None:
        resp = a3101_carrier_client.get("/api/hla/drug-hypersensitivity", params={"sample_id": 1})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["available"] is True
        assert body["any_at_risk"] is True
        by = {a["allele"]: a for a in body["assessments"]}
        a3101 = by["HLA-A*31:01"]
        assert a3101["status"] == "at_risk"
        assert a3101["carried"] is True
        assert a3101["drugs"] == ["carbamazepine"]
        assert "carbamazepine" in a3101["recommendation"].lower()
        assert "PMID:29392710" in a3101["citations"]

    def test_empty_sample_unavailable(self, empty_client: TestClient) -> None:
        resp = empty_client.get("/api/hla/drug-hypersensitivity", params={"sample_id": 1})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["available"] is False
        assert body["any_at_risk"] is False
        assert body["assessments"] == []
        assert body["unavailable_note"]

    def test_missing_sample_404(self, empty_client: TestClient) -> None:
        resp = empty_client.get("/api/hla/drug-hypersensitivity", params={"sample_id": 999})
        assert resp.status_code == 404

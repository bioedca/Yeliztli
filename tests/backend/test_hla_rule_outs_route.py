"""Route coverage for GET /api/hla/rule-outs (Wave D / SW-D3).

Seeds a sample's ``hla_calls`` (a celiac-permissive DQ2.5 + narcolepsy-negative
genotype) and asserts the endpoint surfaces both rule-out assessments; also the
empty-sample and missing-sample contracts. Fixture mirrors the SW-D2 route test.
"""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path
from unittest.mock import patch

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

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
                file_hash="hlad3",
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


def _row(locus, a1, a2) -> dict:
    return {
        "locus": locus,
        "allele1": a1,
        "allele2": a2,
        "prob": 0.95,
        "matching": 0.9,
        "low_confidence": 0,
        "ancestry_model": "European",
        "source": "hibag",
    }


@pytest.fixture
def celiac_client(tmp_data_dir: Path) -> Generator[TestClient, None, None]:
    # DQ2.5 (DQA1*05:01 + DQB1*02:01) present; DQB1*06:02 absent → narcolepsy negative.
    yield from _client(
        tmp_data_dir, [_row("DQA1", "05:01", "01:01"), _row("DQB1", "02:01", "05:01")]
    )


@pytest.fixture
def empty_client(tmp_data_dir: Path) -> Generator[TestClient, None, None]:
    yield from _client(tmp_data_dir, [])


class TestRuleOutsRoute:
    def test_surfaces_celiac_and_narcolepsy(self, celiac_client: TestClient) -> None:
        resp = celiac_client.get("/api/hla/rule-outs", params={"sample_id": 1})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["available"] is True
        assert body["research_use_only"] is True
        assert body["caveat"]
        assert body["citations"]
        assert body["celiac"]["status"] == "permissive_present"
        assert any("DQ2.5" in d for d in body["celiac"]["detected"])
        assert body["narcolepsy"]["status"] == "absent_lowers"
        assert body["narcolepsy"]["carried"] is False

    def test_empty_sample_unavailable(self, empty_client: TestClient) -> None:
        resp = empty_client.get("/api/hla/rule-outs", params={"sample_id": 1})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["available"] is False
        assert body["celiac"] is None
        assert body["narcolepsy"] is None
        assert body["unavailable_note"]

    def test_missing_sample_404(self, empty_client: TestClient) -> None:
        resp = empty_client.get("/api/hla/rule-outs", params={"sample_id": 999})
        assert resp.status_code == 404

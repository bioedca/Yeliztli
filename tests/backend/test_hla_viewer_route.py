"""Route coverage for GET /api/hla/alleles (Wave D / SW-D5 raw viewer/export)."""

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
                file_hash="hlad5",
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
def viewer_client(tmp_data_dir: Path) -> Generator[TestClient, None, None]:
    yield from _client(tmp_data_dir, [_row("B", "57:01", "07:02"), _row("A", "01:01", "02:01")])


@pytest.fixture
def empty_client(tmp_data_dir: Path) -> Generator[TestClient, None, None]:
    yield from _client(tmp_data_dir, [])


class TestViewerRoute:
    def test_lists_alleles_with_guard(self, viewer_client: TestClient) -> None:
        resp = viewer_client.get("/api/hla/alleles", params={"sample_id": 1})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["available"] is True
        assert body["research_use_only"] is True
        assert body["caveat"]
        # The never-for-transplant guard is the load-bearing SW-D5 statement.
        assert "transplant" in body["transplant_guard"].lower()
        # Ordered A before B.
        assert [a["locus"] for a in body["alleles"]] == ["A", "B"]

    def test_empty_sample_unavailable(self, empty_client: TestClient) -> None:
        resp = empty_client.get("/api/hla/alleles", params={"sample_id": 1})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["available"] is False
        assert body["alleles"] == []
        assert body["unavailable_note"]
        assert body["transplant_guard"]  # guard present even when unavailable

    def test_missing_sample_404(self, empty_client: TestClient) -> None:
        resp = empty_client.get("/api/hla/alleles", params={"sample_id": 999})
        assert resp.status_code == 404

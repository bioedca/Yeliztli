"""API tests for the BCHE apnea-risk context endpoint (SW-E6).

GET /api/analysis/bche?sample_id=N
"""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path
from unittest.mock import patch

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from backend.analysis.bche import BCHE_ATYPICAL_RSID, BCHE_K_RSID
from backend.config import Settings
from backend.db.connection import reset_registry
from backend.db.sample_schema import create_sample_tables
from backend.db.tables import raw_variants, reference_metadata, samples


@pytest.fixture
def tmp_data_dir(tmp_path: Path) -> Path:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "samples").mkdir()
    return data_dir


@pytest.fixture
def bche_client(tmp_data_dir: Path) -> Generator[TestClient, None, None]:
    settings = Settings(data_dir=tmp_data_dir, wal_mode=False)

    ref_engine = sa.create_engine(f"sqlite:///{settings.reference_db_path}")
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
    # Atypical heterozygous (forward TC) + K reference (CC) → intermediate risk.
    with sample_engine.begin() as conn:
        conn.execute(
            raw_variants.insert(),
            [
                {"rsid": BCHE_ATYPICAL_RSID, "chrom": "3", "pos": 165548529, "genotype": "TC"},
                {"rsid": BCHE_K_RSID, "chrom": "3", "pos": 165491280, "genotype": "CC"},
            ],
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


class TestBcheEndpoint:
    def test_reports_intermediate_risk(self, bche_client: TestClient) -> None:
        data = bche_client.get("/api/analysis/bche?sample_id=1").json()
        assert data["risk_category"] == "intermediate"
        assert data["coverage_complete"] is True
        variants = {v["rsid"]: v for v in data["variants"]}
        assert variants[BCHE_ATYPICAL_RSID]["reduced_activity_alleles"] == 1

    def test_context_only_disclosure(self, bche_client: TestClient) -> None:
        data = bche_client.get("/api/analysis/bche?sample_id=1").json()
        assert data["context_only"] is True
        assert data["any_called"] is True
        assert data["note"]
        assert data["pmid_citations"]

    def test_invalid_sample_returns_404(self, bche_client: TestClient) -> None:
        assert bche_client.get("/api/analysis/bche?sample_id=999").status_code == 404

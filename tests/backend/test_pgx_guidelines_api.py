"""API tests for the cross-source PGx evidence endpoint (SW-E2).

GET /api/analysis/pgx-guidelines?sample_id=N
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
from backend.db.tables import findings, reference_metadata, samples


@pytest.fixture
def tmp_data_dir(tmp_path: Path) -> Path:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "samples").mkdir()
    return data_dir


@pytest.fixture
def pgx_client(tmp_data_dir: Path) -> Generator[TestClient, None, None]:
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
    with sample_engine.begin() as conn:
        conn.execute(
            findings.insert(),
            [
                {
                    "module": "pharmacogenomics",
                    "category": "prescribing_alert",
                    "gene_symbol": "CYP2C19",
                    "drug": "clopidogrel",
                    "metabolizer_status": "Poor Metabolizer",
                    "finding_text": "CYP2C19/clopidogrel alert",
                }
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


class TestPgxGuidelinesEndpoint:
    def test_returns_cross_source_evidence(self, pgx_client: TestClient) -> None:
        data = pgx_client.get("/api/analysis/pgx-guidelines?sample_id=1").json()
        assert len(data["alerts"]) == 1
        a = data["alerts"][0]
        assert a["gene_symbol"] == "CYP2C19"
        assert a["pharmgkb_loe"] == "1A"
        assert a["dpwg_guideline"] is True
        assert a["fda_pgx_level"] == "Actionable PGx"

    def test_context_only_disclosure(self, pgx_client: TestClient) -> None:
        data = pgx_client.get("/api/analysis/pgx-guidelines?sample_id=1").json()
        assert data["context_only"] is True
        assert data["note"]
        assert data["pmid_citations"]

    def test_invalid_sample_returns_404(self, pgx_client: TestClient) -> None:
        assert pgx_client.get("/api/analysis/pgx-guidelines?sample_id=999").status_code == 404

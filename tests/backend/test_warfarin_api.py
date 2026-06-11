"""API tests for the warfarin-dosing context endpoint (SW-E1 warfarin layer / #13).

GET /api/analysis/warfarin?sample_id=N
"""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path
from unittest.mock import patch

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from backend.analysis.warfarin import CYP4F2_RSID, VKORC1_RSID
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
def warfarin_client(tmp_data_dir: Path) -> Generator[TestClient, None, None]:
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
    # VKORC1 G/A (forward CT → lower dose); CYP4F2 *1/*3 (forward CT → higher dose).
    with sample_engine.begin() as conn:
        conn.execute(
            raw_variants.insert(),
            [
                {"rsid": VKORC1_RSID, "chrom": "16", "pos": 31107689, "genotype": "CT"},
                {"rsid": CYP4F2_RSID, "chrom": "19", "pos": 15990431, "genotype": "CT"},
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


class TestWarfarinEndpoint:
    def test_reports_both_genes_with_directions(self, warfarin_client: TestClient) -> None:
        data = warfarin_client.get("/api/analysis/warfarin?sample_id=1").json()
        genes = {g["gene"]: g for g in data["genes"]}
        assert genes["VKORC1"]["dose_effect"] == "lower"
        assert genes["VKORC1"]["diplotype"] == "G/A"
        assert genes["CYP4F2"]["dose_effect"] == "higher"
        assert genes["CYP4F2"]["diplotype"] == "*1/*3"

    def test_context_only_disclosure(self, warfarin_client: TestClient) -> None:
        data = warfarin_client.get("/api/analysis/warfarin?sample_id=1").json()
        assert data["context_only"] is True
        assert data["any_called"] is True
        assert data["note"]
        assert data["pmid_citations"]

    def test_invalid_sample_returns_404(self, warfarin_client: TestClient) -> None:
        assert warfarin_client.get("/api/analysis/warfarin?sample_id=999").status_code == 404

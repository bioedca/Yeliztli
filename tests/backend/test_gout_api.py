"""Tests for the gout / serum-urate findings API (factory-built router)."""

from __future__ import annotations

import json
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
from backend.db.tables import findings, raw_variants, reference_metadata, samples
from backend.disclaimers import GOUT_DISCLAIMER_TEXT, GOUT_DISCLAIMER_TITLE


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
    with sample_engine.begin() as conn:
        conn.execute(
            sa.insert(raw_variants),
            [
                {"rsid": "rs2231142", "chrom": "4", "pos": 89052323, "genotype": "TT"},
                {"rsid": "rs13129697", "chrom": "4", "pos": 9926967, "genotype": "GG"},
            ],
        )
        conn.execute(
            sa.insert(findings),
            [
                {
                    "module": "ancestry",
                    "category": "nnls_admixture",
                    "evidence_level": 1,
                    "finding_text": "Ancestry: EAS",
                    "detail_json": json.dumps(
                        {"top_population": "EAS", "admixture_fractions": {"EAS": 0.9}}
                    ),
                }
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
    from backend.api.routes.gout import router

    app = FastAPI()
    app.include_router(router, prefix="/api")
    return TestClient(app)


class TestDisclaimer:
    def test_returns_disclaimer(self, client: TestClient) -> None:
        resp = client.get("/api/analysis/gout/disclaimer")
        assert resp.status_code == 200
        data = resp.json()
        assert data["title"] == GOUT_DISCLAIMER_TITLE
        assert data["text"] == GOUT_DISCLAIMER_TEXT
        assert "not a diagnosis" in data["text"].lower()
        # No dietary prescription leaks into the user-facing disclaimer.
        for banned in ("avoid purine", "low-purine", "diet", "alcohol"):
            assert banned not in data["text"].lower()


class TestRunAndList:
    def test_run_then_list_east_asian_band(self, client: TestClient) -> None:
        run = client.post("/api/analysis/gout/run?sample_id=1")
        assert run.status_code == 200
        assert run.json()["findings_count"] == 1

        listing = client.get("/api/analysis/gout/findings?sample_id=1")
        assert listing.status_code == 200
        item = listing.json()["items"][0]
        assert item["gene_symbol"] == "ABCG2"
        assert "4.53" in item["odds_ratio"]  # ancestry-selected Asian-ancestry band

    def test_run_idempotent(self, client: TestClient) -> None:
        client.post("/api/analysis/gout/run?sample_id=1")
        client.post("/api/analysis/gout/run?sample_id=1")
        listing = client.get("/api/analysis/gout/findings?sample_id=1")
        assert listing.json()["total"] == 1

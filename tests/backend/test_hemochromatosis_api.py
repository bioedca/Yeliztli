"""Tests for the hereditary haemochromatosis (HFE) findings API."""

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
from backend.disclaimers import (
    HEMOCHROMATOSIS_DISCLAIMER_TEXT,
    HEMOCHROMATOSIS_DISCLAIMER_TITLE,
)


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
    # Seed a C282Y homozygous genotype.
    with sample_engine.begin() as conn:
        conn.execute(
            sa.insert(raw_variants),
            [{"rsid": "rs1800562", "chrom": "6", "pos": 26093141, "genotype": "AA"}],
        )

    settings = Settings(data_dir=data_dir)
    reset_registry()
    registry = DBRegistry(settings)
    with (
        patch("backend.api.dependencies.get_registry", return_value=registry),
        patch("backend.api.routes.hemochromatosis.get_registry", return_value=registry),
        patch("backend.services.staleness.get_registry", return_value=registry),
    ):
        yield sample_engine
    reset_registry()


@pytest.fixture()
def client(_env: sa.Engine) -> TestClient:
    from backend.api.routes.hemochromatosis import router

    app = FastAPI()
    app.include_router(router, prefix="/api")
    return TestClient(app)


class TestDisclaimer:
    def test_returns_disclaimer(self, client: TestClient) -> None:
        resp = client.get("/api/analysis/hemochromatosis/disclaimer")
        assert resp.status_code == 200
        data = resp.json()
        assert data["title"] == HEMOCHROMATOSIS_DISCLAIMER_TITLE
        assert data["text"] == HEMOCHROMATOSIS_DISCLAIMER_TEXT
        # Negative-not-clear and treatability framing must be present.
        assert "does not rule out" in data["text"]
        assert "phlebotomy" in data["text"]


class TestRunAndList:
    def test_run_then_list(self, client: TestClient) -> None:
        run = client.post("/api/analysis/hemochromatosis/run?sample_id=1")
        assert run.status_code == 200
        assert run.json()["findings_count"] == 1

        listing = client.get("/api/analysis/hemochromatosis/findings?sample_id=1")
        assert listing.status_code == 200
        data = listing.json()
        assert data["total"] == 1
        item = data["items"][0]
        assert item["gene_symbol"] == "HFE"
        assert item["risk_classification"] == "C282Y homozygous"
        assert item["evidence_level"] == 3
        assert item["genotype_calls"]["rs1800562"] == "AA"

    def test_run_idempotent(self, client: TestClient) -> None:
        client.post("/api/analysis/hemochromatosis/run?sample_id=1")
        client.post("/api/analysis/hemochromatosis/run?sample_id=1")
        listing = client.get("/api/analysis/hemochromatosis/findings?sample_id=1")
        assert listing.json()["total"] == 1

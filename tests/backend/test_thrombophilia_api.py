"""Tests for the inherited thrombophilia findings API (factory-built router)."""

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
    THROMBOPHILIA_DISCLAIMER_TEXT,
    THROMBOPHILIA_DISCLAIMER_TITLE,
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
    with sample_engine.begin() as conn:
        conn.execute(
            sa.insert(raw_variants),
            [
                {"rsid": "rs6025", "chrom": "1", "pos": 169519049, "genotype": "GA"},
                {"rsid": "rs1799963", "chrom": "11", "pos": 46761055, "genotype": "GA"},
            ],
        )

    settings = Settings(data_dir=data_dir)
    reset_registry()
    registry = DBRegistry(settings)
    with patch("backend.api.routes.risk_common.get_registry", return_value=registry):
        yield sample_engine
    reset_registry()


@pytest.fixture()
def client(_env: sa.Engine) -> TestClient:
    from backend.api.routes.thrombophilia import router

    app = FastAPI()
    app.include_router(router, prefix="/api")
    return TestClient(app)


class TestDisclaimer:
    def test_returns_disclaimer(self, client: TestClient) -> None:
        resp = client.get("/api/analysis/thrombophilia/disclaimer")
        assert resp.status_code == 200
        data = resp.json()
        assert data["title"] == THROMBOPHILIA_DISCLAIMER_TITLE
        assert data["text"] == THROMBOPHILIA_DISCLAIMER_TEXT
        assert "absolute" in data["text"].lower()
        assert "not recommended" in data["text"]  # population screening framing


class TestRunAndList:
    def test_run_then_list_double_carrier(self, client: TestClient) -> None:
        run = client.post("/api/analysis/thrombophilia/run?sample_id=1")
        assert run.status_code == 200
        assert run.json()["findings_count"] == 1

        listing = client.get("/api/analysis/thrombophilia/findings?sample_id=1")
        assert listing.status_code == 200
        data = listing.json()
        assert data["total"] == 1
        item = data["items"][0]
        assert "double carrier" in item["risk_classification"].lower()
        assert "5.24" in item["finding_text"]
        assert item["odds_ratio"]

    def test_run_idempotent(self, client: TestClient) -> None:
        client.post("/api/analysis/thrombophilia/run?sample_id=1")
        client.post("/api/analysis/thrombophilia/run?sample_id=1")
        listing = client.get("/api/analysis/thrombophilia/findings?sample_id=1")
        assert listing.json()["total"] == 1

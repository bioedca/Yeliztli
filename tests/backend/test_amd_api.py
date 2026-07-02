"""Tests for the AMD findings API (factory-built router)."""

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
from backend.disclaimers import AMD_DISCLAIMER_TEXT, AMD_DISCLAIMER_TITLE


def _cfh(genotype: str) -> dict:
    return {"rsid": "rs1061170", "chrom": "1", "pos": 196659237, "genotype": genotype}


def _arms2(genotype: str) -> dict:
    return {"rsid": "rs10490924", "chrom": "10", "pos": 124214448, "genotype": genotype}


def _replace_raw_variants(engine: sa.Engine, rows: list[dict]) -> None:
    with engine.begin() as conn:
        conn.execute(sa.delete(raw_variants))
        if rows:
            conn.execute(sa.insert(raw_variants), rows)


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
                {"rsid": "rs1061170", "chrom": "1", "pos": 196659237, "genotype": "CC"},
                {"rsid": "rs10490924", "chrom": "10", "pos": 124214448, "genotype": "GG"},
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
    from backend.api.routes.amd import router

    app = FastAPI()
    app.include_router(router, prefix="/api")
    return TestClient(app)


class TestDisclaimer:
    def test_returns_disclaimer(self, client: TestClient) -> None:
        resp = client.get("/api/analysis/amd/disclaimer")
        assert resp.status_code == 200
        data = resp.json()
        assert data["title"] == AMD_DISCLAIMER_TITLE
        assert data["text"] == AMD_DISCLAIMER_TEXT
        assert "just under half" in data["text"].lower()
        assert "more than half" not in data["text"].lower()


class TestRunAndList:
    def test_run_then_list_cfh_hom(self, client: TestClient) -> None:
        run = client.post("/api/analysis/amd/run?sample_id=1")
        assert run.status_code == 200
        assert run.json()["findings_count"] == 1

        listing = client.get("/api/analysis/amd/findings?sample_id=1")
        assert listing.status_code == 200
        item = listing.json()["items"][0]
        assert item["gene_symbol"] == "CFH"
        assert item["risk_classification"] == "CFH Y402H homozygous"
        assert item["evidence_level"] == 3
        assert "7.2" in item["odds_ratio"]  # CFH-homozygous OR band, not just truthy
        assert "absolute" in item["absolute_risk_context"].lower()

    def test_arms2_only_caveats_are_locus_specific(
        self, client: TestClient, _env: sa.Engine
    ) -> None:
        _replace_raw_variants(_env, [_cfh("TT"), _arms2("TT")])

        run = client.post("/api/analysis/amd/run?sample_id=1")
        assert run.status_code == 200

        listing = client.get("/api/analysis/amd/findings?sample_id=1")
        assert listing.status_code == 200
        item = listing.json()["items"][0]
        assert item["gene_symbol"] == "ARMS2"
        assert item["risk_classification"] == "ARMS2 homozygous"

        caveats = " ".join(item["caveats"]).lower()
        assert "european-ancestry populations" not in caveats
        assert "may not transfer to other genetic backgrounds" not in caveats
        assert "arms2/htra1 rs10490924" in caveats
        assert "cohort-derived relative-risk estimates" in caveats
        assert "ancestry-specific absolute amd risk estimate" in caveats

    def test_run_idempotent(self, client: TestClient) -> None:
        client.post("/api/analysis/amd/run?sample_id=1")
        client.post("/api/analysis/amd/run?sample_id=1")
        listing = client.get("/api/analysis/amd/findings?sample_id=1")
        assert listing.json()["total"] == 1

"""Tests for the alpha-1 antitrypsin findings API (factory-built router)."""

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
from backend.disclaimers import ALPHA1_DISCLAIMER_TEXT, ALPHA1_DISCLAIMER_TITLE


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
                {"rsid": "rs28929474", "chrom": "14", "pos": 94847262, "genotype": "TT"},
                {"rsid": "rs17580", "chrom": "14", "pos": 94844947, "genotype": "TT"},
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
    from backend.api.routes.alpha1 import router

    app = FastAPI()
    app.include_router(router, prefix="/api")
    return TestClient(app)


class TestDisclaimer:
    def test_returns_disclaimer(self, client: TestClient) -> None:
        resp = client.get("/api/analysis/alpha1/disclaimer")
        assert resp.status_code == 200
        data = resp.json()
        assert data["title"] == ALPHA1_DISCLAIMER_TITLE
        assert data["text"] == ALPHA1_DISCLAIMER_TEXT
        assert "does not exclude rare null" in data["text"]


class TestRunAndList:
    def test_run_then_list_pizz(self, client: TestClient) -> None:
        run = client.post("/api/analysis/alpha1/run?sample_id=1")
        assert run.status_code == 200
        assert run.json()["findings_count"] == 1

        listing = client.get("/api/analysis/alpha1/findings?sample_id=1")
        assert listing.status_code == 200
        item = listing.json()["items"][0]
        assert item["gene_symbol"] == "SERPINA1"
        assert item["risk_classification"] == "PiZZ (severe deficiency)"
        assert item["evidence_level"] == 3

    def test_run_then_list_pisz_phase_fields(self, client: TestClient, _env: sa.Engine) -> None:
        with _env.begin() as conn:
            conn.execute(sa.delete(raw_variants))
            conn.execute(
                sa.insert(raw_variants),
                [
                    {
                        "rsid": "rs28929474",
                        "chrom": "14",
                        "pos": 94847262,
                        "genotype": "CT",
                    },
                    {"rsid": "rs17580", "chrom": "14", "pos": 94844947, "genotype": "AT"},
                ],
            )

        run = client.post("/api/analysis/alpha1/run?sample_id=1")
        assert run.status_code == 200
        assert run.json()["findings_count"] == 1

        listing = client.get("/api/analysis/alpha1/findings?sample_id=1")
        assert listing.status_code == 200
        item = listing.json()["items"][0]
        assert item["risk_classification"] == "PiSZ (phase-inferred intermediate deficiency)"
        assert item["evidence_level"] == 2
        assert item["phase_inferred"] is True
        assert item["call_confidence"] == "Partial"
        assert item["confidence_note"] == (
            "Unphased SNP-array genotypes observe both SERPINA1 S and Z variants "
            "but cannot determine whether they are in trans; confirm before "
            "treating as definitive PiSZ."
        )
        assert any("unphased" in caveat.lower() for caveat in item["caveats"])

    def test_run_idempotent(self, client: TestClient) -> None:
        client.post("/api/analysis/alpha1/run?sample_id=1")
        client.post("/api/analysis/alpha1/run?sample_id=1")
        listing = client.get("/api/analysis/alpha1/findings?sample_id=1")
        assert listing.json()["total"] == 1

"""Tests for the Parkinson's API and its APOE-style ethical gate."""

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
from backend.disclaimers import PARKINSONS_GATE_TITLE


@pytest.fixture()
def _env(tmp_path: Path) -> Generator[sa.Engine, None, None]:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "samples").mkdir()

    ref_engine = sa.create_engine(f"sqlite:///{data_dir / 'reference.db'}")
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
    ref_engine.dispose()

    sample_engine = sa.create_engine(f"sqlite:///{data_dir / 'samples' / 'sample_1.db'}")
    create_sample_tables(sample_engine)
    with sample_engine.begin() as conn:
        conn.execute(
            sa.insert(raw_variants),
            [{"rsid": "rs34637584", "chrom": "12", "pos": 40734202, "genotype": "GA"}],
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
    from backend.api.routes.parkinsons import router

    app = FastAPI()
    app.include_router(router, prefix="/api")
    return TestClient(app)


class TestDisclaimer:
    def test_returns_gate_disclosure(self, client: TestClient) -> None:
        resp = client.get("/api/analysis/parkinsons/disclaimer")
        assert resp.status_code == 200
        data = resp.json()
        assert data["title"] == PARKINSONS_GATE_TITLE
        assert data["accept_label"]
        assert data["decline_label"]
        assert "no proven way to prevent" in data["text"].lower()
        assert "gbap1" in data["text"].lower()  # GBA1 suppression explained
        normalized = data["text"].replace("–", "-")
        assert "24-49%" in normalized
        assert "25-42.5%" in normalized
        assert "by age 80" in normalized.lower()
        assert "most carriers never develop" not in normalized.lower()


class TestGateFlow:
    def test_findings_blocked_until_acknowledged(self, client: TestClient) -> None:
        # Compute findings (allowed without the gate — running is not viewing).
        run = client.post("/api/analysis/parkinsons/run?sample_id=1")
        assert run.status_code == 200
        assert run.json()["findings_count"] == 1

        # Gate not yet acknowledged → findings are 403.
        status = client.get("/api/analysis/parkinsons/gate-status?sample_id=1")
        assert status.json()["acknowledged"] is False
        blocked = client.get("/api/analysis/parkinsons/findings?sample_id=1")
        assert blocked.status_code == 403

        # Acknowledge the gate.
        ack = client.post("/api/analysis/parkinsons/acknowledge-gate?sample_id=1")
        assert ack.status_code == 200
        assert ack.json()["acknowledged"] is True
        assert client.get("/api/analysis/parkinsons/gate-status?sample_id=1").json()[
            "acknowledged"
        ]

        # Now findings are visible.
        listing = client.get("/api/analysis/parkinsons/findings?sample_id=1")
        assert listing.status_code == 200
        assert listing.json()["total"] == 1
        item = listing.json()["items"][0]
        assert item["gene_symbol"] == "LRRK2"
        assert item["evidence_level"] == 2

    def test_acknowledgment_persists(self, client: TestClient) -> None:
        client.post("/api/analysis/parkinsons/acknowledge-gate?sample_id=1")
        # A second acknowledge is idempotent and stays acknowledged.
        client.post("/api/analysis/parkinsons/acknowledge-gate?sample_id=1")
        assert client.get("/api/analysis/parkinsons/gate-status?sample_id=1").json()[
            "acknowledged"
        ]

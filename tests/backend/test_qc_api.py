"""Tests for the sample QC metrics API."""

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
from backend.db.tables import individuals, raw_variants, reference_metadata, samples
from backend.disclaimers import QC_DISCLAIMER_TEXT, QC_DISCLAIMER_TITLE


@pytest.fixture()
def _env(tmp_path: Path) -> Generator[sa.Engine, None, None]:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "samples").mkdir()

    ref_engine = sa.create_engine(f"sqlite:///{data_dir / 'reference.db'}")
    reference_metadata.create_all(ref_engine)
    with ref_engine.begin() as conn:
        conn.execute(
            sa.insert(individuals), [{"id": 1, "display_name": "P", "biological_sex": "XX"}]
        )
        conn.execute(
            sa.insert(samples),
            [
                {
                    "name": "test_sample",
                    "db_path": "samples/sample_1.db",
                    "file_format": "23andme_v5",
                    "file_hash": "abc123",
                    "individual_id": 1,
                }
            ],
        )
    ref_engine.dispose()

    sample_engine = sa.create_engine(f"sqlite:///{data_dir / 'samples' / 'sample_1.db'}")
    create_sample_tables(sample_engine)
    with sample_engine.begin() as conn:
        conn.execute(
            sa.insert(raw_variants),
            [
                {"rsid": "r1", "chrom": "1", "pos": 1000, "genotype": "AG"},
                {"rsid": "r2", "chrom": "2", "pos": 2000, "genotype": "AA"},
                {"rsid": "r3", "chrom": "3", "pos": 3000, "genotype": "CT"},
                # Genetic XX needs (a) an aggregate denominator on both sex
                # chromosomes (issue #363) and (b) a *diploid-X* non-PAR chrX
                # het rate, not a lone het (issue #519): sex inference decides X
                # dosage on the het rate, and a real female is heterozygous at a
                # large fraction of markers. 50 het + 50 hom = 100 typed chrX
                # (rate 0.50, above the 0.15 diploid cutoff) + an all-no-call
                # chrY denominator (≥ MIN_Y_PROBES, rate 0.0) → XX.
                {"rsid": "rx", "chrom": "X", "pos": 5_000_000, "genotype": "AG"},
                *(
                    {
                        "rsid": f"rx_fill_{i}",
                        "chrom": "X",
                        "pos": 5_000_001 + i,
                        "genotype": "AG" if i < 49 else "GG",
                    }
                    for i in range(99)
                ),
                *(
                    {"rsid": f"ry_nc_{i}", "chrom": "Y", "pos": 2_800_000 + i, "genotype": "--"}
                    for i in range(50)
                ),
            ],
        )

    settings = Settings(data_dir=data_dir)
    reset_registry()
    registry = DBRegistry(settings)
    with (
        patch("backend.api.routes.risk_common.get_registry", return_value=registry),
        patch("backend.api.routes.qc.get_registry", return_value=registry),
    ):
        yield sample_engine
    registry.dispose_all()
    reset_registry()


@pytest.fixture()
def client(_env: sa.Engine) -> TestClient:
    from backend.api.routes.qc import router

    app = FastAPI()
    app.include_router(router, prefix="/api")
    return TestClient(app)


class TestDisclaimer:
    def test_returns_disclaimer(self, client: TestClient) -> None:
        resp = client.get("/api/analysis/qc/disclaimer")
        assert resp.status_code == 200
        data = resp.json()
        assert data["title"] == QC_DISCLAIMER_TITLE
        assert data["text"] == QC_DISCLAIMER_TEXT
        assert "concordance only" in data["text"].lower()


class TestRunAndMetrics:
    def test_metrics_before_run_not_computed(self, client: TestClient) -> None:
        resp = client.get("/api/analysis/qc/metrics?sample_id=1")
        assert resp.status_code == 200
        assert resp.json()["computed"] is False

    def test_run_then_metrics_with_sex_concordance(self, client: TestClient) -> None:
        run = client.post("/api/analysis/qc/run?sample_id=1")
        assert run.status_code == 200
        assert run.json()["computed"] is True

        m = client.get("/api/analysis/qc/metrics?sample_id=1").json()
        assert m["computed"] is True
        # 4 original rows + 99 chrX hom filler + 50 chrY no-call denominator (#363).
        assert m["total_variants"] == 153
        assert m["nocall_variants"] == 50
        assert m["genetic_sex"] == "XX"
        assert m["recorded_sex"] == "XX"
        assert m["sex_check"] == "concordant"
        # Single account sample → no cohort for outlier detection.
        assert m["het_outlier_status"] == "insufficient_samples"

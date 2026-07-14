"""Tests for the ROH / FROH findings API."""

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
from backend.disclaimers import ROH_DISCLAIMER_TEXT, ROH_DISCLAIMER_TITLE


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
    # Two unequal clean homozygous runs plus one typed non-ROH autosomal SNP,
    # with an autosomal no-call and chrX call that must be excluded.
    # This makes total length, longest length, and typed-SNP count independently
    # discriminating at the API boundary.
    with sample_engine.begin() as conn:
        conn.execute(
            sa.insert(raw_variants),
            [
                {
                    "rsid": f"r1_{i}",
                    "chrom": "1",
                    "pos": 1_000_000 + i * 10_000,
                    "genotype": "CC",
                }
                for i in range(160)
            ]
            + [
                {
                    "rsid": f"r2_{i}",
                    "chrom": "2",
                    "pos": 1_000_000 + i * 10_000,
                    "genotype": "AA",
                }
                for i in range(200)
            ]
            + [
                {
                    "rsid": "r3_het",
                    "chrom": "3",
                    "pos": 1_000_000,
                    "genotype": "AG",
                },
                {
                    "rsid": "r4_no_call",
                    "chrom": "4",
                    "pos": 1_000_000,
                    "genotype": "--",
                },
                {
                    "rsid": "rx_hom",
                    "chrom": "X",
                    "pos": 1_000_000,
                    "genotype": "GG",
                },
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
    from backend.api.routes.roh import router

    app = FastAPI()
    app.include_router(router, prefix="/api")
    return TestClient(app)


class TestDisclaimer:
    def test_returns_disclaimer(self, client: TestClient) -> None:
        resp = client.get("/api/analysis/roh/disclaimer")
        assert resp.status_code == 200
        data = resp.json()
        assert data["title"] == ROH_DISCLAIMER_TITLE
        assert data["text"] == ROH_DISCLAIMER_TEXT
        assert "not a diagnosis" in data["text"].lower()


class TestRunAndList:
    def test_run_then_list(self, client: TestClient) -> None:
        run = client.post("/api/analysis/roh/run?sample_id=1")
        assert run.status_code == 200
        assert run.json()["findings_count"] == 1

        listing = client.get("/api/analysis/roh/findings?sample_id=1")
        assert listing.status_code == 200
        data = listing.json()
        assert data["n_segments"] == 2
        # Independent fixture-derived literals pin the populated API fields
        # against zeroing or cross-field mapping regressions: chr1 contributes
        # 1590 kb, chr2 contributes 1990 kb, and the 360 ROH calls plus one
        # typed heterozygous call make 361 autosomal SNPs used.
        assert data["total_roh_kb"] == 3580.0
        assert data["longest_kb"] == 1990.0
        assert data["autosomal_snps_used"] == 361
        assert data["froh"] > 0
        assert [segment["chrom"] for segment in data["segments"]] == ["2", "1"]

    def test_list_before_run_is_null(self, client: TestClient) -> None:
        listing = client.get("/api/analysis/roh/findings?sample_id=1")
        assert listing.status_code == 200
        assert listing.json() is None

    def test_malformed_detail_falls_back_safely(self, _env: sa.Engine, client: TestClient) -> None:
        # A row with a schema-drifted segment must not 500 — the route falls back
        # to the plain finding_text with zeroed metrics.
        import json as _json

        from backend.db.tables import findings

        with _env.begin() as conn:
            conn.execute(
                sa.insert(findings),
                [
                    {
                        "module": "roh",
                        "category": "autozygosity",
                        "evidence_level": 1,
                        "finding_text": "summary text",
                        "detail_json": _json.dumps({"segments": [{"unexpected": "shape"}]}),
                    }
                ],
            )
        resp = client.get("/api/analysis/roh/findings?sample_id=1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["finding_text"] == "summary text"
        assert data["segments"] == []
        assert data["n_segments"] == 0

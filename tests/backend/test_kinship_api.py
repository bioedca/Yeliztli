"""Tests for the within-account kinship API (cross-sample, route-only)."""

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
from backend.disclaimers import KINSHIP_DISCLAIMER_TEXT, KINSHIP_DISCLAIMER_TITLE


def _dup_genotypes() -> list[dict]:
    # 2600 autosomal SNPs, half het / half hom → identical copies score φ ≈ 0.5.
    return [
        {
            "rsid": f"r{i}",
            "chrom": "1",
            "pos": 1000 + i,
            "genotype": "AG" if i % 2 == 0 else "AA",
        }
        for i in range(2600)
    ]


def _make_sample_db(data_dir: Path, fname: str, rows: list[dict]) -> None:
    engine = sa.create_engine(f"sqlite:///{data_dir / 'samples' / fname}")
    create_sample_tables(engine)
    if rows:
        with engine.begin() as conn:
            conn.execute(sa.insert(raw_variants), rows)
    engine.dispose()


@pytest.fixture()
def _env(tmp_path: Path, request) -> Generator[Settings, None, None]:
    """Set up `n_samples` local samples (default 2: a target + an identical dup)."""
    n_samples = getattr(request, "param", 2)
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "samples").mkdir()

    ref_engine = sa.create_engine(f"sqlite:///{data_dir / 'reference.db'}")
    reference_metadata.create_all(ref_engine)
    rows = [
        {
            "name": f"Sample {i}",
            "db_path": f"samples/sample_{i}.db",
            "file_format": "23andme_v5",
            "file_hash": f"hash{i}",
        }
        for i in range(1, n_samples + 1)
    ]
    with ref_engine.begin() as conn:
        conn.execute(sa.insert(samples), rows)
    ref_engine.dispose()

    for i in range(1, n_samples + 1):
        _make_sample_db(data_dir, f"sample_{i}.db", _dup_genotypes())

    settings = Settings(data_dir=data_dir)
    reset_registry()
    registry = DBRegistry(settings)
    # The kinship route resolves engines two ways: directly via
    # kinship.get_registry, and via resolve_sample_engine (imported from
    # risk_common, which calls risk_common.get_registry) — both must be patched.
    with (
        patch("backend.api.routes.risk_common.get_registry", return_value=registry),
        patch("backend.api.routes.kinship.get_registry", return_value=registry),
    ):
        yield settings
    registry.dispose_all()
    reset_registry()


@pytest.fixture()
def client(_env: Settings) -> TestClient:
    from backend.api.routes.kinship import router

    app = FastAPI()
    app.include_router(router, prefix="/api")
    return TestClient(app)


class TestDisclaimer:
    def test_returns_disclaimer(self, client: TestClient) -> None:
        resp = client.get("/api/analysis/kinship/disclaimer")
        assert resp.status_code == 200
        data = resp.json()
        assert data["title"] == KINSHIP_DISCLAIMER_TITLE
        assert data["text"] == KINSHIP_DISCLAIMER_TEXT
        assert "within your own samples only" in data["text"].lower()

    def test_disclaimer_does_not_sell_shared_snps_as_confidence(self, client: TestClient) -> None:
        """#2170: the shared-SNP count is not the confidence signal.

        The disclaimer used to tell readers the shared-SNP count "is reported
        with each estimate so you can judge confidence". That is the one number
        which does not indicate confidence -- identically homozygous positions
        inflate it while contributing nothing to the KING denominator. Asserting
        on `data["text"]` rather than the constant keeps this a content lock and
        not a tautology.
        """
        text = client.get("/api/analysis/kinship/disclaimer").json()["text"]

        assert "so you can judge \nconfidence" not in text
        assert "actually informed it" in text
        assert "indeterminate" in text


class TestRunAndList:
    def test_duplicate_detected(self, client: TestClient) -> None:
        run = client.post("/api/analysis/kinship/run?sample_id=1")
        assert run.status_code == 200
        body = run.json()
        assert body["samples_compared"] == 1
        assert body["findings_count"] == 1

        listing = client.get("/api/analysis/kinship/findings?sample_id=1")
        assert listing.status_code == 200
        item = listing.json()["items"][0]
        assert item["relationship"] == "duplicate_or_mz_twin"
        assert item["phi"] == pytest.approx(0.5, abs=0.01)
        assert item["other_sample_id"] == 2

    @pytest.mark.parametrize("_env", [1], indirect=True)
    def test_single_sample_has_no_comparison(self, client: TestClient) -> None:
        run = client.post("/api/analysis/kinship/run?sample_id=1")
        assert run.status_code == 200
        assert run.json()["samples_compared"] == 0

        listing = client.get("/api/analysis/kinship/findings?sample_id=1")
        item = listing.json()["items"][0]
        assert "no other local samples" in item["finding_text"].lower()
        assert item["relationship"] is None

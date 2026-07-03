"""Tests for the sex-aneuploidy screen API and its opt-in gate."""

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
from backend.db.tables import raw_variants, reference_metadata, samples
from backend.disclaimers import ANEUPLOIDY_GATE_TITLE


def _xxy_genotypes() -> list[dict]:
    rows = []
    pos = 5_000_000
    for i in range(60):  # 60 non-PAR X hets → two X
        rows.append({"rsid": f"xh{i}", "chrom": "X", "pos": pos, "genotype": "AG"})
        pos += 137
    for i in range(60):  # 60 X homs
        rows.append({"rsid": f"xm{i}", "chrom": "X", "pos": pos, "genotype": "AA"})
        pos += 137
    pos = 6_000_000
    for i in range(60):  # 60 Y typed → Y present
        rows.append({"rsid": f"y{i}", "chrom": "Y", "pos": pos, "genotype": "GG"})
        pos += 137
    return rows


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
        conn.execute(sa.insert(raw_variants), _xxy_genotypes())

    settings = Settings(data_dir=data_dir)
    reset_registry()
    registry = DBRegistry(settings)
    with patch("backend.api.routes.risk_common.get_registry", return_value=registry):
        yield sample_engine
    registry.dispose_all()
    reset_registry()


@pytest.fixture()
def client(_env: sa.Engine) -> TestClient:
    from backend.api.routes.sex_aneuploidy import router

    app = FastAPI()
    app.include_router(router, prefix="/api")
    return TestClient(app)


class TestDisclaimer:
    def test_returns_gate_disclosure(self, client: TestClient) -> None:
        resp = client.get("/api/analysis/sex-aneuploidy/disclaimer")
        assert resp.status_code == 200
        data = resp.json()
        assert data["title"] == ANEUPLOIDY_GATE_TITLE
        assert data["accept_label"] and data["decline_label"]
        text = data["text"].lower()
        assert "screen, not a diagnosis" in text
        assert "turner" in text  # explicit out-of-scope statement


class TestGateFlow:
    def test_screen_blocked_until_acknowledged(self, client: TestClient) -> None:
        run = client.post("/api/analysis/sex-aneuploidy/run?sample_id=1")
        assert run.status_code == 200
        run_data = run.json()
        assert run_data["computed"] is True
        assert run_data["outcome"] is None
        body = json.dumps(run_data)
        for outcome in (
            "possible_xxy",
            "manual_review",
            "no_aneuploidy_signal",
            "indeterminate",
        ):
            assert outcome not in body

        assert (
            client.get("/api/analysis/sex-aneuploidy/gate-status?sample_id=1").json()[
                "acknowledged"
            ]
            is False
        )
        blocked = client.get("/api/analysis/sex-aneuploidy/findings?sample_id=1")
        assert blocked.status_code == 403

        ack = client.post("/api/analysis/sex-aneuploidy/acknowledge-gate?sample_id=1")
        assert ack.status_code == 200

        listing = client.get("/api/analysis/sex-aneuploidy/findings?sample_id=1")
        assert listing.status_code == 200
        data = listing.json()
        assert data["computed"] is True
        assert data["outcome"] == "possible_xxy"

        rerun = client.post("/api/analysis/sex-aneuploidy/run?sample_id=1")
        assert rerun.status_code == 200
        assert rerun.json()["outcome"] == "possible_xxy"

"""Tests for the metabolic anchor-SNP findings API (#138 strand resolution).

Verifies that the ``/anchors`` endpoint serializes a strand-ambiguous
palindromic homozygote as ``indeterminate`` with a suppressed (null) dosage,
rather than reporting an inverted directional copy-count.
"""

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
from backend.db.tables import annotated_variants, reference_metadata, samples


def _make_env(tmp_path: Path, fto_genotype: str) -> tuple[Settings, DBRegistry]:
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
            sa.insert(annotated_variants),
            [
                {
                    "rsid": "rs9939609",  # FTO, A/T palindrome
                    "chrom": "16",
                    "pos": 53786615,
                    "genotype": fto_genotype,
                    "gnomad_af_global": 0.4,
                    "annotation_coverage": 0,
                },
                {
                    "rsid": "rs7903146",  # TCF7L2, T/C non-palindromic
                    "chrom": "10",
                    "pos": 114758349,
                    "genotype": "TT",
                    "gnomad_af_global": 0.3,
                    "annotation_coverage": 0,
                },
            ],
        )

    settings = Settings(data_dir=data_dir)
    reset_registry()
    return settings, DBRegistry(settings)


@pytest.fixture()
def client_factory(
    tmp_path: Path,
) -> Generator[object, None, None]:
    from backend.api.routes.metabolic import router

    patchers: list[object] = []

    def _factory(fto_genotype: str) -> TestClient:
        _settings, registry = _make_env(tmp_path, fto_genotype)
        patcher = patch("backend.api.routes.metabolic.get_registry", return_value=registry)
        patcher.start()
        patchers.append(patcher)
        app = FastAPI()
        app.include_router(router, prefix="/api")
        return TestClient(app)

    yield _factory
    for p in patchers:
        p.stop()
    reset_registry()


class TestAnchorIndeterminateSerialization:
    def test_palindrome_homozygote_serialized_indeterminate(self, client_factory) -> None:
        client = client_factory("TT")  # reverse-strand homozygote at FTO A/T
        run = client.post("/api/analysis/metabolic/run?sample_id=1")
        assert run.status_code == 200

        listing = client.get("/api/analysis/metabolic/anchors?sample_id=1")
        assert listing.status_code == 200
        items = {a["gene"]: a for a in listing.json()["items"]}

        fto = items["FTO"]
        assert fto["indeterminate"] is True
        assert fto["dosage"] is None

        # The non-palindromic anchor still reports a directional dosage.
        tcf = items["TCF7L2"]
        assert tcf["indeterminate"] is False
        assert tcf["dosage"] == 2

    def test_palindrome_heterozygote_serialized_with_dosage(self, client_factory) -> None:
        client = client_factory("AT")  # strand-invariant het → 1 copy
        client.post("/api/analysis/metabolic/run?sample_id=1")
        listing = client.get("/api/analysis/metabolic/anchors?sample_id=1")
        fto = {a["gene"]: a for a in listing.json()["items"]}["FTO"]
        assert fto["indeterminate"] is False
        assert fto["dosage"] == 1

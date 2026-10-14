"""Route-level tests for the MTHFR & Methylation API (#1946).

Guards the ``POST /api/analysis/methylation/run`` response contract and the
``run`` → stored finding → ``GET /pathways`` compound-het handoff.

``test_methylation.py`` owns the science: it calls ``score_methylation_pathways``
and ``store_methylation_findings`` directly and never crosses the API boundary,
so a route that drops or mis-shapes the live handoff stays green there. These
tests exercise the boundary only — they seed genotypes whose compound-het
meaning is established by ``test_methylation.py::TestCompoundHeterozygosity``
and assert what the routes surface, rather than re-deriving the allele call.
"""

from __future__ import annotations

from collections.abc import Generator
from contextlib import ExitStack
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

# (rsid, chrom, pos) for the two MTHFR variants the compound-het call rests on,
# matching the coordinates test_methylation.py seeds.
C677T = ("rs1801133", "1", 11856378)
A1298C = ("rs1801131", "1", 11854476)

# C677T het + A1298C het = compound heterozygote; both wild type = not.
COMPOUND_HET_GENOTYPES = {C677T: "GA", A1298C: "AC"}
WILD_TYPE_GENOTYPES = {C677T: "GG", A1298C: "AA"}

RUN_URL = "/api/analysis/methylation/run?sample_id=1"
PATHWAYS_URL = "/api/analysis/methylation/pathways?sample_id=1"


@pytest.fixture()
def _env(tmp_path: Path) -> Generator[sa.Engine, None, None]:
    """Temporary data dir with one registered sample, wired into the registry."""
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

    settings = Settings(data_dir=data_dir)
    reset_registry()
    registry = DBRegistry(settings)

    with ExitStack() as stack:
        stack.enter_context(
            patch("backend.api.routes.methylation.get_registry", return_value=registry)
        )
        stack.enter_context(patch("backend.api.dependencies.get_registry", return_value=registry))
        stack.enter_context(
            patch("backend.services.staleness.get_registry", return_value=registry)
        )
        yield sample_engine

    registry.dispose_all()
    reset_registry()


@pytest.fixture()
def client(_env: sa.Engine) -> TestClient:
    from backend.api.routes.methylation import router

    app = FastAPI()
    app.include_router(router, prefix="/api")
    return TestClient(app)


def _seed_mthfr(engine: sa.Engine, genotypes: dict[tuple[str, str, int], str]) -> None:
    """Insert raw_variants rows for the given {(rsid, chrom, pos): genotype}."""
    with engine.begin() as conn:
        conn.execute(
            sa.insert(raw_variants),
            [
                {"rsid": rsid, "chrom": chrom, "pos": pos, "genotype": genotype}
                for (rsid, chrom, pos), genotype in genotypes.items()
            ],
        )


class TestRunRoute:
    def test_run_response_contract(self, client: TestClient, _env: sa.Engine) -> None:
        """POST /run returns exactly findings_count + pathways_scored."""
        _seed_mthfr(_env, COMPOUND_HET_GENOTYPES)

        resp = client.post(RUN_URL)

        assert resp.status_code == 200
        body = resp.json()
        assert set(body) == {"findings_count", "pathways_scored"}
        assert body["pathways_scored"] == 5
        assert body["findings_count"] > 0

    def test_run_is_idempotent(self, client: TestClient, _env: sa.Engine) -> None:
        """Re-running must not accumulate duplicate findings."""
        _seed_mthfr(_env, COMPOUND_HET_GENOTYPES)

        first = client.post(RUN_URL).json()
        second = client.post(RUN_URL).json()

        assert first == second

    def test_run_unknown_sample_is_404(self, client: TestClient) -> None:
        assert client.post("/api/analysis/methylation/run?sample_id=999").status_code == 404


class TestCompoundHetHandoff:
    def test_run_then_pathways_surfaces_compound_het(
        self, client: TestClient, _env: sa.Engine
    ) -> None:
        """The stored compound-het finding is recovered through GET /pathways."""
        _seed_mthfr(_env, COMPOUND_HET_GENOTYPES)
        assert client.post(RUN_URL).status_code == 200

        resp = client.get(PATHWAYS_URL)

        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == len(body["items"]) == 5

        compound_het = body["compound_het"]
        assert compound_het is not None
        assert compound_het["is_compound_het"] is True
        assert compound_het["is_double_homozygous"] is False
        assert compound_het["c677t_genotype"] == "GA"
        assert compound_het["a1298c_genotype"] == "AC"
        assert "compound" in compound_het["finding_text"].lower()

    def test_pathways_reports_no_compound_het_for_wild_type(
        self, client: TestClient, _env: sa.Engine
    ) -> None:
        """Wild-type C677T/A1298C must not surface a compound-het call."""
        _seed_mthfr(_env, WILD_TYPE_GENOTYPES)
        assert client.post(RUN_URL).status_code == 200

        resp = client.get(PATHWAYS_URL)

        assert resp.status_code == 200
        compound_het = resp.json()["compound_het"]
        assert compound_het is None or compound_het["is_compound_het"] is False

    def test_pathways_without_run_has_no_findings(
        self, client: TestClient, _env: sa.Engine
    ) -> None:
        """GET /pathways before any run returns an empty, well-formed body."""
        _seed_mthfr(_env, COMPOUND_HET_GENOTYPES)

        resp = client.get(PATHWAYS_URL)

        assert resp.status_code == 200
        body = resp.json()
        assert body["items"] == []
        assert body["total"] == 0
        assert body["compound_het"] is None

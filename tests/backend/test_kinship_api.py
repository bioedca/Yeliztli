"""Tests for the within-account kinship API (cross-sample, route-only)."""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path
from unittest.mock import patch

import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.analysis.kinship import MIN_SHARED_SNPS
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


def _all_homozygous_genotypes() -> list[dict]:
    """2,000 identical AA calls: satisfies MIN_SHARED_SNPS with ZERO het evidence.

    The KING denominator is het_i + het_j, so this pair clears the reportability
    gate while contributing nothing to it -- the #2170 witness.
    """
    return [
        {"rsid": f"h{i}", "chrom": "1", "pos": 5000 + i, "genotype": "AA"}
        for i in range(MIN_SHARED_SNPS)
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
def _env_zero_denominator(_env: Settings) -> Settings:
    """Replace both sample DBs with the all-homozygous pair."""
    data_dir = _env.data_dir
    for i in (1, 2):
        (data_dir / "samples" / f"sample_{i}.db").unlink()
        _make_sample_db(data_dir, f"sample_{i}.db", _all_homozygous_genotypes())
    return _env


@pytest.fixture()
def zero_denominator_client(_env_zero_denominator: Settings) -> TestClient:
    from backend.api.routes.kinship import router

    app = FastAPI()
    app.include_router(router, prefix="/api")
    return TestClient(app)


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
        # The divisor is a count of heterozygous CALLS (two per both-het
        # position), not a subset of the shared SNPs -- it can exceed the shared
        # count, so describing it as "those that informed it" was wrong (#2215).
        assert "heterozygous calls" in text
        assert "not a subset of it" in text
        assert "actually informed it" not in text
        assert "indeterminate" in text
        # #2215 review: the disclaimer must not make claims about the state of
        # external validation. Saying "no validated minimum has been established"
        # is a substantive statistical assertion with no source behind it, and it
        # was being used to justify publishing labels for tiny denominators.
        assert "validated minimum" not in text
        assert "no evidence" not in text
        # It must also not promise indeterminate for a merely small denominator,
        # which the implementation does not do.
        # A divisor that EXCEEDS the shared count is not the worry; a small one
        # is, so the caution is framed on smallness rather than divergence.
        assert "small next to the shared-SNP count" in text
        assert "caution" in text


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

    def test_zero_denominator_pair_reaches_the_api_with_its_evidence(
        self, zero_denominator_client: TestClient
    ) -> None:
        """#2215 review: the fields must survive the production storage path.

        2,000 identical AA/AA calls clear `MIN_SHARED_SNPS` with a KING
        denominator of zero. `informative_denominator` and
        `indeterminate_reason` exist to explain that, but folding the pair into
        the aggregate summary returned them as null from `/findings` -- for
        precisely the case this change is about.
        """
        run = zero_denominator_client.post("/api/analysis/kinship/run?sample_id=1")
        assert run.status_code == 200

        listing = zero_denominator_client.get("/api/analysis/kinship/findings?sample_id=1")
        assert listing.status_code == 200
        item = listing.json()["items"][0]
        assert item["relationship"] == "indeterminate"
        assert item["phi"] is None
        assert item["n_shared_snps"] == MIN_SHARED_SNPS
        assert item["informative_denominator"] == 0
        assert item["indeterminate_reason"] == "no_heterozygous_information"

    @pytest.mark.parametrize("_env", [1], indirect=True)
    def test_single_sample_has_no_comparison(self, client: TestClient) -> None:
        run = client.post("/api/analysis/kinship/run?sample_id=1")
        assert run.status_code == 200
        assert run.json()["samples_compared"] == 0

        listing = client.get("/api/analysis/kinship/findings?sample_id=1")
        item = listing.json()["items"][0]
        assert "no other local samples" in item["finding_text"].lower()
        assert item["relationship"] is None

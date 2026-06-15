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
from backend.db.tables import (
    individuals,
    qc_metrics,
    raw_variants,
    reference_metadata,
    samples,
)
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


# ── Het-outlier cohort must be stratified by genotyping array (#563) ──────


@pytest.fixture()
def het_cohort_client(tmp_path: Path):
    """Factory: build a QC client over an account of samples with chosen
    ``(file_format, heterozygosity_rate)`` pairs.

    The target is sample 1; ``others`` become samples 2…N. Each sample gets a
    ``qc_metrics`` row with the given het rate (inserted directly — we are
    testing the *cohort* selection, not het computation). Returns the target's
    ``/metrics`` JSON.
    """
    created: list[DBRegistry] = []

    def _build(target: tuple[str, float], others: list[tuple[str, float]]) -> dict:
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        (data_dir / "samples").mkdir()

        ref_engine = sa.create_engine(f"sqlite:///{data_dir / 'reference.db'}")
        reference_metadata.create_all(ref_engine)
        with ref_engine.begin() as conn:
            conn.execute(
                sa.insert(individuals), [{"id": 1, "display_name": "P", "biological_sex": "XX"}]
            )

        def _add(sid: int, file_format: str, het: float) -> None:
            with ref_engine.begin() as conn:
                conn.execute(
                    sa.insert(samples),
                    [
                        {
                            "id": sid,
                            "name": f"sample_{sid}",
                            "db_path": f"samples/sample_{sid}.db",
                            "file_format": file_format,
                            "file_hash": f"hash{sid}",
                            "individual_id": 1,
                        }
                    ],
                )
            se = sa.create_engine(f"sqlite:///{data_dir / 'samples' / f'sample_{sid}.db'}")
            create_sample_tables(se)
            with se.begin() as conn:
                conn.execute(sa.insert(qc_metrics), [{"heterozygosity_rate": het}])
            se.dispose()

        _add(1, target[0], target[1])
        for i, (fmt, het) in enumerate(others, start=2):
            _add(i, fmt, het)
        ref_engine.dispose()

        settings = Settings(data_dir=data_dir)
        reset_registry()
        registry = DBRegistry(settings)
        created.append(registry)
        with (
            patch("backend.api.routes.risk_common.get_registry", return_value=registry),
            patch("backend.api.routes.qc.get_registry", return_value=registry),
        ):
            from backend.api.routes.qc import router

            app = FastAPI()
            app.include_router(router, prefix="/api")
            client = TestClient(app)
            return client.get("/api/analysis/qc/metrics?sample_id=1").json()

    yield _build

    for r in created:
        r.dispose_all()
    reset_registry()


class TestHetOutlierArrayStratification:
    """The het-outlier z-score must compare only within a single genotyping
    array — heterozygosity is array-ascertainment-dependent and not comparable
    across arrays (#563)."""

    def test_minority_array_sample_not_flagged_outlier(self, het_cohort_client) -> None:
        # The #563 repro: one 23andMe sample (het 0.17) in an account otherwise
        # full of AncestryDNA samples (het ~0.30). Pre-fix the 23andMe sample
        # z-scored against the AncestryDNA cohort → spurious "outlier". Post-fix
        # the cross-array cohort is excluded, leaving 0 same-array peers. Other
        # samples DO exist, just none on this array, so the verdict is the more
        # informative ``insufficient_comparable_samples`` (#656), not the
        # nearly-empty-account ``insufficient_samples``.
        m = het_cohort_client(
            target=("23andme_v5", 0.17),
            others=[
                ("ancestrydna_v2.0", 0.30),
                ("ancestrydna_v2.0", 0.31),
                ("ancestrydna_v2.0", 0.29),
            ],
        )
        assert m["computed"] is True
        assert m["het_outlier_status"] != "outlier"
        assert m["het_outlier_status"] == "insufficient_comparable_samples"
        assert m["het_outlier_z"] is None

    def test_same_array_cohort_still_detects_outlier(self, het_cohort_client) -> None:
        # Control: the SAME numbers (target 0.17 vs cohort ~0.30) ARE a genuine
        # outlier when the cohort is the same array — proving stratification did
        # not just disable the check.
        m = het_cohort_client(
            target=("23andme_v5", 0.17),
            others=[("23andme_v5", 0.30), ("23andme_v5", 0.31), ("23andme_v5", 0.29)],
        )
        assert m["computed"] is True
        assert m["het_outlier_status"] == "outlier"
        assert m["het_outlier_z"] is not None and m["het_outlier_z"] < -3

    def test_same_array_typical_within_range(self, het_cohort_client) -> None:
        # A same-array sample close to its same-array cohort is within range.
        m = het_cohort_client(
            target=("23andme_v5", 0.175),
            others=[("23andme_v5", 0.17), ("23andme_v5", 0.18), ("23andme_v5", 0.172)],
        )
        assert m["het_outlier_status"] == "within_range"


class TestHetOutlierWithheldReason:
    """When the het-outlier z-score is withheld, the status distinguishes a
    chip-confounded gap (other samples exist but on a different array) from a
    genuinely small account — the #563 "option 2" enhancement (#656).

    The two tests below are the discriminator: they hold the *number* of other
    samples fixed (two) and vary ONLY the array, so a route that branched on raw
    sample count alone (the pre-#656 behaviour) would return the same status for
    both and fail exactly one of them.
    """

    def test_other_samples_all_different_array_is_insufficient_comparable(
        self, het_cohort_client
    ) -> None:
        # Two other samples, both on a DIFFERENT array → no within-array peers,
        # but the account is not nearly empty → insufficient_comparable_samples.
        m = het_cohort_client(
            target=("23andme_v5", 0.17),
            others=[("ancestrydna_v2.0", 0.30), ("ancestrydna_v2.0", 0.31)],
        )
        assert m["computed"] is True
        assert m["het_outlier_z"] is None
        assert m["het_outlier_status"] == "insufficient_comparable_samples"

    def test_too_few_same_array_samples_is_insufficient_samples(self, het_cohort_client) -> None:
        # The discriminator vs the test above: the SAME number of other samples
        # (two), but both share the target's array → comparable peers DO exist,
        # there are just too few (<3) for a z-score → insufficient_samples.
        m = het_cohort_client(
            target=("23andme_v5", 0.17),
            others=[("23andme_v5", 0.30), ("23andme_v5", 0.31)],
        )
        assert m["computed"] is True
        assert m["het_outlier_z"] is None
        assert m["het_outlier_status"] == "insufficient_samples"

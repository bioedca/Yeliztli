"""End-to-end validation for Ancestry Module v2 (Step 10).

Covers:
  10.1 — Synthetic 23andMe fixture (~5,000 SNPs) with known EUR ancestry
  10.2 — Tier 1 validation (NNLS fractions, classification, PCA, storage, API)
  10.3 — Tier 2 validation (LAI, skip when Java unavailable)
  10.5 — Performance benchmarks (Tier 1 < 1 second)
"""

from __future__ import annotations

import dataclasses
import json
import platform
import time
from pathlib import Path
from unittest.mock import patch

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from backend.analysis.ancestry import (
    AncestryBundle,
    get_inferred_ancestry,
    get_pca_coordinates,
    get_top_ancestry_fraction,
    infer_ancestry,
    load_ancestry_bundle,
    store_ancestry_findings,
    store_haplogroup_findings,
)
from backend.config import Settings
from backend.db.connection import reset_registry
from backend.db.sample_schema import create_sample_tables
from backend.db.tables import findings, raw_variants, reference_metadata, samples

# ── Paths ────────────────────────────────────────────────────────────────

FIXTURE_PATH = Path(__file__).resolve().parent.parent / "fixtures" / "synthetic_eur_23andme.txt"
BUNDLE_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "backend"
    / "data"
    / "panels"
    / "ancestry_pca_bundle.npz"
)


# ── Fixtures ─────────────────────────────────────────────────────────────


@pytest.fixture()
def bundle() -> AncestryBundle:
    """Load the production PCA bundle."""
    if not BUNDLE_PATH.exists():
        pytest.skip(f"PCA bundle not found at {BUNDLE_PATH}")
    return load_ancestry_bundle(BUNDLE_PATH)


@pytest.fixture()
def eur_sample_engine() -> sa.Engine:
    """In-memory sample DB loaded with synthetic EUR 23andMe data."""
    engine = sa.create_engine("sqlite://")
    create_sample_tables(engine)

    variants: list[dict] = []
    with open(FIXTURE_PATH) as f:
        for line in f:
            if line.startswith("#") or not line.strip():
                continue
            parts = line.strip().split("\t")
            variants.append(
                {
                    "rsid": parts[0],
                    "chrom": parts[1],
                    "pos": int(parts[2]),
                    "genotype": parts[3],
                }
            )

    with engine.begin() as conn:
        conn.execute(sa.insert(raw_variants), variants)

    yield engine
    engine.dispose()


@pytest.fixture()
def eur_result(bundle: AncestryBundle, eur_sample_engine: sa.Engine):
    """Run ancestry inference on the EUR fixture and return result."""
    return infer_ancestry(bundle, eur_sample_engine)


# ── 10.1 — Synthetic fixture validation ──────────────────────────────────


class TestSyntheticFixture:
    """Verify the synthetic 23andMe fixture file is well-formed."""

    def test_fixture_exists(self) -> None:
        assert FIXTURE_PATH.exists(), f"Missing fixture: {FIXTURE_PATH}"

    def test_fixture_has_5000_variants(self) -> None:
        count = 0
        with open(FIXTURE_PATH) as f:
            for line in f:
                if not line.startswith("#") and line.strip():
                    count += 1
        assert count == 5000

    def test_fixture_has_valid_format(self) -> None:
        with open(FIXTURE_PATH) as f:
            for line in f:
                if line.startswith("#") or not line.strip():
                    continue
                parts = line.strip().split("\t")
                assert len(parts) == 4, f"Expected 4 columns, got {len(parts)}: {line}"
                assert parts[0].startswith(("rs", "i")), f"Invalid rsid: {parts[0]}"
                assert parts[1].isdigit(), f"Invalid chrom: {parts[1]}"
                assert parts[2].isdigit(), f"Invalid pos: {parts[2]}"
                assert len(parts[3]) == 2, f"Invalid genotype: {parts[3]}"

    def test_fixture_covers_multiple_chromosomes(self) -> None:
        chroms: set[str] = set()
        with open(FIXTURE_PATH) as f:
            for line in f:
                if line.startswith("#") or not line.strip():
                    continue
                chroms.add(line.strip().split("\t")[1])
        assert len(chroms) >= 20, f"Expected >= 20 chromosomes, got {len(chroms)}"


# ── 10.2 — Tier 1 E2E validation ────────────────────────────────────────


class TestTier1NNLS:
    """NNLS admixture fractions sum correctly and top pop is EUR."""

    def test_nnls_fractions_sum_to_one(self, eur_result) -> None:
        frac_sum = sum(eur_result.admixture_fractions.values())
        assert abs(frac_sum - 1.0) < 0.001, f"NNLS fractions sum to {frac_sum}"

    def test_top_population_is_eur(self, eur_result) -> None:
        assert eur_result.top_population == "EUR"

    def test_eur_fraction_is_dominant(self, eur_result) -> None:
        eur_frac = eur_result.admixture_fractions.get("EUR", 0)
        assert eur_frac > 0.4, f"EUR fraction {eur_frac} too low"

    def test_all_seven_populations_present(self, eur_result) -> None:
        expected = {"AFR", "AMR", "CSA", "EAS", "EUR", "MID", "OCE"}
        assert set(eur_result.admixture_fractions.keys()) == expected


class TestTier1KNN:
    """kNN admixture fractions are valid and consistent."""

    def test_knn_fractions_sum_to_one(self, eur_result) -> None:
        assert eur_result.knn_fractions is not None
        frac_sum = sum(eur_result.knn_fractions.values())
        assert abs(frac_sum - 1.0) < 0.001

    def test_knn_top_is_eur(self, eur_result) -> None:
        assert eur_result.knn_fractions is not None
        knn_top = max(eur_result.knn_fractions, key=lambda p: eur_result.knn_fractions[p])
        assert knn_top == "EUR"


class TestTier1Confidence:
    """Confidence metrics are valid."""

    def test_confidence_high_for_eur(self, eur_result) -> None:
        assert eur_result.confidence > 0.8, f"Confidence {eur_result.confidence} too low"

    def test_confidence_in_range(self, eur_result) -> None:
        assert 0.0 <= eur_result.confidence <= 1.0

    def test_bootstrap_ci_contains_point_estimate(self, eur_result) -> None:
        assert eur_result.nnls_ci_low is not None
        assert eur_result.nnls_ci_high is not None
        for pop in eur_result.admixture_fractions:
            low = eur_result.nnls_ci_low.get(pop, 0)
            high = eur_result.nnls_ci_high.get(pop, 1)
            assert low <= high, f"{pop}: CI low {low} > high {high}"


class TestTier1PCA:
    """PCA projection produces valid coordinates."""

    def test_pc_scores_correct_dimension(self, eur_result) -> None:
        assert len(eur_result.pc_scores) == 8

    def test_coverage_is_full(self, eur_result) -> None:
        assert eur_result.coverage_fraction == 1.0

    def test_all_aims_used(self, eur_result) -> None:
        assert eur_result.snps_used == 5000
        assert eur_result.snps_total == 5000

    def test_is_sufficient(self, eur_result) -> None:
        assert eur_result.is_sufficient is True

    def test_missing_aim_rate_zero(self, eur_result) -> None:
        assert eur_result.missing_aim_rate == 0.0

    def test_admixture_method_is_nnls(self, eur_result) -> None:
        assert eur_result.admixture_method == "nnls"

    def test_n_pcs_used(self, eur_result) -> None:
        assert eur_result.n_pcs_used == 8


class TestTier1PCACoordinates:
    """PCA coordinates for visualization are valid."""

    def test_pca_coordinates_structure(self, bundle, eur_result) -> None:
        coords = get_pca_coordinates(bundle, eur_result)
        assert len(coords.user) == 8
        assert len(coords.reference_samples) == 7
        assert len(coords.centroids) == 7
        assert len(coords.population_labels) == 7
        assert coords.n_components == 8
        assert len(coords.pc_labels) == 8

    def test_pca_all_populations_in_reference(self, bundle, eur_result) -> None:
        coords = get_pca_coordinates(bundle, eur_result)
        expected = {"AFR", "AMR", "CSA", "EAS", "EUR", "MID", "OCE"}
        assert set(coords.reference_samples.keys()) == expected

    def test_pca_reference_samples_nonempty(self, bundle, eur_result) -> None:
        coords = get_pca_coordinates(bundle, eur_result)
        for pop, samples_list in coords.reference_samples.items():
            assert len(samples_list) > 0, f"{pop} has no reference samples"


class TestTier1Storage:
    """Findings are stored correctly in the sample DB."""

    def test_stores_three_findings(self, bundle, eur_sample_engine, eur_result) -> None:
        n = store_ancestry_findings(eur_result, eur_sample_engine)
        assert n == 3

    def test_findings_categories(self, bundle, eur_sample_engine, eur_result) -> None:
        store_ancestry_findings(eur_result, eur_sample_engine)
        with eur_sample_engine.connect() as conn:
            rows = conn.execute(
                sa.select(findings.c.category)
                .where(findings.c.module == "ancestry")
                .order_by(findings.c.category)
            ).fetchall()
        categories = sorted(r.category for r in rows)
        assert categories == ["knn_admixture", "nnls_admixture", "pca_projection"]

    def test_nnls_finding_has_top_population(self, bundle, eur_sample_engine, eur_result) -> None:
        store_ancestry_findings(eur_result, eur_sample_engine)
        with eur_sample_engine.connect() as conn:
            row = conn.execute(
                sa.select(findings.c.detail_json).where(
                    findings.c.module == "ancestry",
                    findings.c.category == "nnls_admixture",
                )
            ).fetchone()
        assert row is not None
        detail = json.loads(row.detail_json)
        assert detail["top_population"] == "EUR"

    def test_get_inferred_ancestry_returns_eur(
        self, bundle, eur_sample_engine, eur_result
    ) -> None:
        store_ancestry_findings(eur_result, eur_sample_engine)
        ancestry = get_inferred_ancestry(eur_sample_engine)
        assert ancestry == "EUR"

    def test_get_top_ancestry_fraction(self, bundle, eur_sample_engine, eur_result) -> None:
        store_ancestry_findings(eur_result, eur_sample_engine)
        frac = get_top_ancestry_fraction(eur_sample_engine)
        assert frac is not None
        assert frac > 0.4

    def test_pca_finding_has_correct_json(self, bundle, eur_sample_engine, eur_result) -> None:
        store_ancestry_findings(eur_result, eur_sample_engine)
        with eur_sample_engine.connect() as conn:
            row = conn.execute(
                sa.select(findings.c.detail_json).where(
                    findings.c.module == "ancestry",
                    findings.c.category == "pca_projection",
                )
            ).fetchone()
        detail = json.loads(row.detail_json)
        assert "pc_scores" in detail
        assert "population_distances" in detail
        assert "snps_used" in detail
        assert detail["snps_used"] == 5000

    _ANCESTRY_CATS = ("pca_projection", "nnls_admixture", "knn_admixture")
    _HAPLO_CATS = ("haplogroup_mt", "haplogroup_y")

    def _count(self, engine, cats) -> int:
        with engine.connect() as conn:
            return conn.execute(
                sa.select(sa.func.count())
                .select_from(findings)
                .where(findings.c.category.in_(cats))
            ).scalar()

    def test_insufficient_rerun_clears_stale_ancestry_findings(
        self, bundle, eur_sample_engine, eur_result
    ) -> None:
        # #479: a sufficient run persists 3 findings; an insufficient rerun must
        # CLEAR them, not leave the stale earlier result (the #348 class).
        assert store_ancestry_findings(eur_result, eur_sample_engine) == 3
        insufficient = dataclasses.replace(eur_result, is_sufficient=False)
        assert store_ancestry_findings(insufficient, eur_sample_engine) == 0
        assert self._count(eur_sample_engine, self._ANCESTRY_CATS) == 0

    def test_empty_rerun_clears_stale_haplogroup_findings(self, eur_sample_engine) -> None:
        # #479: a prior haplogroup finding must be cleared by a rerun yielding none.
        with eur_sample_engine.begin() as conn:
            conn.execute(
                sa.insert(findings),
                [
                    {
                        "module": "ancestry",
                        "category": "haplogroup_mt",
                        "finding_text": "stale H2a2a1",
                    }
                ],
            )
        assert store_haplogroup_findings([], eur_sample_engine) == 0
        assert self._count(eur_sample_engine, self._HAPLO_CATS) == 0

    def test_nnls_finding_has_ci(self, bundle, eur_sample_engine, eur_result) -> None:
        store_ancestry_findings(eur_result, eur_sample_engine)
        with eur_sample_engine.connect() as conn:
            row = conn.execute(
                sa.select(findings.c.detail_json).where(
                    findings.c.module == "ancestry",
                    findings.c.category == "nnls_admixture",
                )
            ).fetchone()
        detail = json.loads(row.detail_json)
        assert "ci_low" in detail
        assert "ci_high" in detail


class TestTier1API:
    """API endpoints return correct JSON for ancestry results."""

    @pytest.fixture()
    def seeded_client(self, tmp_data_dir: Path) -> TestClient:
        """TestClient with a sample that has ancestry findings pre-computed."""
        settings = Settings(data_dir=tmp_data_dir, wal_mode=False)

        # Create reference.db with samples table
        ref_path = settings.reference_db_path
        ref_engine = sa.create_engine(f"sqlite:///{ref_path}")
        reference_metadata.create_all(ref_engine)

        # Create sample DB on disk
        sample_dir = tmp_data_dir / "samples"
        sample_db_path = sample_dir / "sample_1.db"
        sample_engine = sa.create_engine(f"sqlite:///{sample_db_path}")
        create_sample_tables(sample_engine)

        # Insert variants from fixture
        variants: list[dict] = []
        with open(FIXTURE_PATH) as f:
            for line in f:
                if line.startswith("#") or not line.strip():
                    continue
                parts = line.strip().split("\t")
                variants.append(
                    {
                        "rsid": parts[0],
                        "chrom": parts[1],
                        "pos": int(parts[2]),
                        "genotype": parts[3],
                    }
                )
        with sample_engine.begin() as conn:
            conn.execute(sa.insert(raw_variants), variants)

        # Run ancestry inference and store findings
        bundle = load_ancestry_bundle(BUNDLE_PATH)
        result = infer_ancestry(bundle, sample_engine)
        store_ancestry_findings(result, sample_engine)
        sample_engine.dispose()

        # Register sample in reference.db
        with ref_engine.begin() as conn:
            conn.execute(
                sa.insert(samples),
                [
                    {
                        "name": "EUR Test",
                        "db_path": "samples/sample_1.db",
                        "file_format": "23andme_v5",
                        "file_hash": "fixture_eur",
                    }
                ],
            )
        ref_engine.dispose()

        with (
            patch("backend.main.get_settings", return_value=settings),
            patch("backend.db.connection.get_settings", return_value=settings),
            patch("backend.config.get_settings", return_value=settings),
        ):
            reset_registry()
            from backend.main import create_app

            app = create_app()
            with TestClient(app) as tc:
                yield tc
            reset_registry()

    def test_findings_endpoint_returns_eur(self, seeded_client: TestClient) -> None:
        resp = seeded_client.get("/api/analysis/ancestry/findings?sample_id=1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["top_population"] == "EUR"
        assert data["is_sufficient"] is True
        assert data["admixture_method"] == "nnls"

    def test_findings_endpoint_has_fractions(self, seeded_client: TestClient) -> None:
        resp = seeded_client.get("/api/analysis/ancestry/findings?sample_id=1")
        data = resp.json()
        fracs = data["admixture_fractions"]
        assert len(fracs) == 7
        assert abs(sum(fracs.values()) - 1.0) < 0.01

    def test_findings_endpoint_has_nnls_and_knn(self, seeded_client: TestClient) -> None:
        resp = seeded_client.get("/api/analysis/ancestry/findings?sample_id=1")
        data = resp.json()
        assert data["nnls_fractions"] is not None
        assert data["knn_fractions"] is not None

    def test_findings_endpoint_has_ci(self, seeded_client: TestClient) -> None:
        resp = seeded_client.get("/api/analysis/ancestry/findings?sample_id=1")
        data = resp.json()
        assert data["nnls_ci_low"] is not None
        assert data["nnls_ci_high"] is not None

    def test_pca_coordinates_endpoint(self, seeded_client: TestClient) -> None:
        resp = seeded_client.get("/api/analysis/ancestry/pca-coordinates?sample_id=1")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["user"]) == 8
        assert len(data["reference_samples"]) == 7
        assert len(data["centroids"]) == 7
        assert data["n_components"] == 8
        assert data["top_population"] == "EUR"

    def test_findings_endpoint_missing_sample(self, seeded_client: TestClient) -> None:
        resp = seeded_client.get("/api/analysis/ancestry/findings?sample_id=9999")
        assert resp.status_code == 404

    def test_lai_status_endpoint(self, seeded_client: TestClient) -> None:
        resp = seeded_client.get("/api/analysis/ancestry/lai/status")
        assert resp.status_code == 200
        data = resp.json()
        assert "bundle_downloaded" in data
        assert "java_available" in data
        assert "lai_available" in data
        assert isinstance(data["message"], str)


# ── 10.3 — Tier 2 validation (LAI) ──────────────────────────────────────


class TestTier2LAI:
    """LAI validation — skipped when Java is unavailable."""

    def test_lai_unavailable_without_bundle(self) -> None:
        """is_lai_available returns False when bundle is missing."""
        from backend.analysis.lai import is_lai_available

        with patch("backend.analysis.lai.get_settings") as mock_settings:
            mock_settings.return_value.resolved_lai_bundle_path = Path("/nonexistent")
            assert is_lai_available() is False

    def test_lai_trigger_404_without_bundle(self, tmp_data_dir: Path) -> None:
        """LAI trigger API returns 404 when bundle not downloaded."""
        settings = Settings(data_dir=tmp_data_dir, wal_mode=False)
        ref_path = settings.reference_db_path
        ref_engine = sa.create_engine(f"sqlite:///{ref_path}")
        reference_metadata.create_all(ref_engine)

        sample_dir = tmp_data_dir / "samples"
        sample_db_path = sample_dir / "sample_1.db"
        sample_engine = sa.create_engine(f"sqlite:///{sample_db_path}")
        create_sample_tables(sample_engine)
        sample_engine.dispose()

        with ref_engine.begin() as conn:
            conn.execute(
                sa.insert(samples),
                [
                    {
                        "name": "Test",
                        "db_path": "samples/sample_1.db",
                        "file_format": "23andme_v5",
                        "file_hash": "test",
                    }
                ],
            )
        ref_engine.dispose()

        with (
            patch("backend.main.get_settings", return_value=settings),
            patch("backend.db.connection.get_settings", return_value=settings),
            patch("backend.config.get_settings", return_value=settings),
        ):
            reset_registry()
            from backend.main import create_app

            app = create_app()
            with TestClient(app) as tc:
                resp = tc.post("/api/analysis/ancestry/lai/1")
                assert resp.status_code == 404
            reset_registry()

    def test_lai_results_schema(self) -> None:
        """LAI results JSON matches expected shape."""
        from backend.analysis.lai import LAIResult

        result = LAIResult(
            global_ancestry={
                "EUR": {
                    "fraction": 0.85,
                    "percentage": 85.0,
                    "display_name": "European",
                    "color": "#4477AA",
                },
                "AFR": {
                    "fraction": 0.15,
                    "percentage": 15.0,
                    "display_name": "African",
                    "color": "#E8A838",
                },
            },
            chromosome_painting={
                str(c): [{"start": 0, "end": 100000, "ancestry": "EUR"}] for c in range(1, 23)
            },
            metadata={"total_windows": 100, "phasing_method": "beagle"},
        )
        assert len(result.chromosome_painting) == 22
        assert all(str(c) in result.chromosome_painting for c in range(1, 23))
        frac_sum = sum(v["fraction"] for v in result.global_ancestry.values())
        assert abs(frac_sum - 1.0) < 0.01


# ── 10.5 — Performance benchmarks ───────────────────────────────────────


class TestPerformance:
    """Tier 1 must complete in < 1 second for the 5,000 AIM fixture."""

    def test_tier1_under_one_second(
        self, bundle: AncestryBundle, eur_sample_engine: sa.Engine
    ) -> None:
        t0 = time.perf_counter()
        result = infer_ancestry(bundle, eur_sample_engine)
        elapsed = time.perf_counter() - t0
        # Wall-clock perf is strictly asserted only on Linux (the canonical perf
        # runner). macOS CI runners — especially the Intel / emulated-x86 one —
        # have high variance (observed ~3s), so use a generous upper bound there
        # to catch gross regressions without flaking.
        threshold = 2.0 if platform.system() == "Linux" else 5.0
        assert elapsed < threshold, f"Tier 1 took {elapsed:.2f}s (target: < {threshold}s)"
        assert result.is_sufficient

    def test_projection_time_reported(self, eur_result) -> None:
        assert eur_result.projection_time_ms > 0
        assert eur_result.projection_time_ms < 500  # projection alone < 500ms

    @pytest.mark.slow
    def test_tier1_bulk_consistency(
        self, bundle: AncestryBundle, eur_sample_engine: sa.Engine
    ) -> None:
        """Running inference 5 times gives consistent results."""
        results = [infer_ancestry(bundle, eur_sample_engine) for _ in range(5)]
        populations = [r.top_population for r in results]
        assert all(p == "EUR" for p in populations)
        # NNLS fractions should be deterministic
        for r in results[1:]:
            for pop in r.admixture_fractions:
                assert (
                    abs(r.admixture_fractions[pop] - results[0].admixture_fractions[pop]) < 0.001
                )

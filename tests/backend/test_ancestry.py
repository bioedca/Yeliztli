"""Tests for ancestry inference module (P3-23, P3-24, P3-25).

Covers:
  - Bundle loading and validation
  - Genotype encoding (alt-allele dosage)
  - PCA projection via NumPy dot product
  - Nearest-centroid classification
  - Admixture fraction computation (P3-24, T3-24)
  - PCA coordinates for visualization (P3-25)
  - Findings storage (module='ancestry', category='pca_projection')
  - T3-25: PCA projection places known EUR-ancestry sample in EUR cluster
  - Coverage threshold enforcement
  - Integration with prs.get_inferred_ancestry()
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import numpy as np
import pytest
import sqlalchemy as sa

from backend.analysis.ancestry import (
    ADMIXED,
    UNCERTAIN,
    AncestryAIM,
    AncestryBundle,
    AncestryResult,
    PCACoordinates,
    _classify_nearest_centroid,
    _classify_with_confidence,
    _encode_dosage,
    _project_onto_pca,
    ancestry_covered,
    bootstrap_admixture_nnls,
    canonical_ancestry,
    classify_ancestry,
    compute_admixture_fractions,
    compute_confidence,
    compute_missing_aim_rate,
    estimate_admixture_knn,
    estimate_admixture_nnls,
    get_inferred_ancestry,
    get_pca_coordinates,
    infer_ancestry,
    load_ancestry_bundle,
    store_ancestry_findings,
)
from backend.db.tables import annotated_variants, findings, raw_variants

# ── Fixtures ──────────────────────────────────────────────────────────────

BUNDLE_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "backend"
    / "data"
    / "panels"
    / "ancestry_pca_bundle.npz"
)


@pytest.fixture()
def bundle() -> AncestryBundle:
    """Load the ancestry PCA bundle from the real JSON file."""
    return load_ancestry_bundle(BUNDLE_PATH)


@pytest.fixture()
def small_bundle() -> AncestryBundle:
    """A minimal synthetic bundle for unit tests."""
    snps = [
        AncestryAIM(rsid="rs1", chrom="1", pos=100, ref="A", alt="G", ref_freq=0.7),
        AncestryAIM(rsid="rs2", chrom="2", pos=200, ref="C", alt="T", ref_freq=0.5),
        AncestryAIM(rsid="rs3", chrom="3", pos=300, ref="G", alt="A", ref_freq=0.3),
        AncestryAIM(rsid="rs4", chrom="4", pos=400, ref="T", alt="C", ref_freq=0.6),
    ]

    # Loadings: 4 SNPs × 2 PCs (n_snps, n_components)
    loadings = np.array(
        [
            [0.5, 0.1],  # SNP1: PC1=0.5, PC2=0.1
            [0.3, -0.4],  # SNP2: PC1=0.3, PC2=-0.4
            [-0.2, 0.3],  # SNP3: PC1=-0.2, PC2=0.3
            [0.1, 0.5],  # SNP4: PC1=0.1, PC2=0.5
        ],
        dtype=np.float64,
    )

    # Per-AIM means and stds (derived from ref_freq: mean = 2 * alt_freq)
    means = np.array([0.6, 1.0, 1.4, 0.8], dtype=np.float64)
    stds = np.array([0.5, 0.5, 0.5, 0.5], dtype=np.float64)

    centroids = {
        "AFR": np.array([2.0, -1.0], dtype=np.float64),
        "EUR": np.array([-1.0, 1.5], dtype=np.float64),
        "EAS": np.array([-0.5, -2.0], dtype=np.float64),
    }

    reference_samples = {
        "AFR": [[2.1, -0.9], [1.8, -1.2], [2.3, -0.8]],
        "EUR": [[-1.1, 1.4], [-0.8, 1.6], [-1.2, 1.3]],
        "EAS": [[-0.6, -1.9], [-0.4, -2.1], [-0.7, -2.2]],
    }

    return AncestryBundle(
        version="test",
        build="GRCh38",
        n_components=2,
        populations=["AFR", "EUR", "EAS"],
        population_labels={"AFR": "African", "EUR": "European", "EAS": "East Asian"},
        snps=snps,
        loadings=loadings,
        means=means,
        stds=stds,
        reference_centroids=centroids,
        reference_samples=reference_samples,
        eigenvalues=np.array([10.0, 5.0], dtype=np.float64),
        n_significant_pcs=2,
        tw_pvalues=np.array([0.001] * 20, dtype=np.float64),
        n_total_snps=1000,
        n_selected_aims=4,
    )


def _insert_raw_genotypes(
    sample_engine: sa.Engine,
    genotypes: list[dict],
) -> sa.Engine:
    """Insert raw variants for testing."""
    with sample_engine.begin() as conn:
        conn.execute(sa.insert(raw_variants), genotypes)
    return sample_engine


def _insert_annotated_genotypes(
    sample_engine: sa.Engine,
    genotypes: list[dict],
) -> sa.Engine:
    """Insert annotated variants for testing."""
    with sample_engine.begin() as conn:
        conn.execute(sa.insert(annotated_variants), genotypes)
    return sample_engine


@pytest.fixture()
def eur_sample(sample_engine: sa.Engine) -> sa.Engine:
    """Sample with genotypes typical of European ancestry.

    High ref allele frequencies → low dosage for many SNPs.
    """
    # Dosages [0, 0, 2, 2] project to PC≈[-1.2, 2.24] — squarely in the EUR cluster
    # (centroid [-1, 1.5], dist 0.6; 2nd-nearest EAS is ~31x farther), so it clears
    # the PCA confidence gate as a decisive EUR call (not the old EUR/EAS borderline).
    genotypes = [
        {"rsid": "rs1", "chrom": "1", "pos": 100, "genotype": "AA"},  # 0 alt (ref A)
        {"rsid": "rs2", "chrom": "2", "pos": 200, "genotype": "CC"},  # 0 alt (ref C)
        {"rsid": "rs3", "chrom": "3", "pos": 300, "genotype": "AA"},  # 2 alt (alt A)
        {"rsid": "rs4", "chrom": "4", "pos": 400, "genotype": "CC"},  # 2 alt (alt C)
    ]
    return _insert_raw_genotypes(sample_engine, genotypes)


@pytest.fixture()
def afr_sample(sample_engine: sa.Engine) -> sa.Engine:
    """Sample with genotypes typical of African ancestry.

    Higher alt allele counts for ancestry-informative markers.
    """
    genotypes = [
        {"rsid": "rs1", "chrom": "1", "pos": 100, "genotype": "GG"},  # 2 alt
        {"rsid": "rs2", "chrom": "2", "pos": 200, "genotype": "TT"},  # 2 alt
        {"rsid": "rs3", "chrom": "3", "pos": 300, "genotype": "AA"},  # 2 alt
        {"rsid": "rs4", "chrom": "4", "pos": 400, "genotype": "CC"},  # 2 alt
    ]
    return _insert_raw_genotypes(sample_engine, genotypes)


@pytest.fixture()
def partial_sample(sample_engine: sa.Engine) -> sa.Engine:
    """Sample with only 1 of 4 SNPs — below coverage threshold."""
    genotypes = [
        {"rsid": "rs1", "chrom": "1", "pos": 100, "genotype": "AG"},
    ]
    return _insert_raw_genotypes(sample_engine, genotypes)


@pytest.fixture()
def admixed_sample(sample_engine: sa.Engine) -> sa.Engine:
    """Full-coverage sample that lands between small-bundle centroids."""
    genotypes = [
        {"rsid": "rs1", "chrom": "1", "pos": 100, "genotype": "AG"},
        {"rsid": "rs2", "chrom": "2", "pos": 200, "genotype": "CT"},
        {"rsid": "rs3", "chrom": "3", "pos": 300, "genotype": "GA"},
        {"rsid": "rs4", "chrom": "4", "pos": 400, "genotype": "TC"},
    ]
    return _insert_raw_genotypes(sample_engine, genotypes)


# ── Bundle loading tests ─────────────────────────────────────────────────


class TestLoadAncestryBundle:
    """Test ancestry PCA bundle loading from NPZ."""

    def test_loads_from_npz(self, bundle: AncestryBundle) -> None:
        assert bundle.snp_count == 5000
        assert bundle.n_components == 8
        assert len(bundle.populations) == 7

    def test_loadings_shape(self, bundle: AncestryBundle) -> None:
        assert bundle.loadings.shape == (bundle.snp_count, bundle.n_components)

    def test_means_and_stds_shape(self, bundle: AncestryBundle) -> None:
        assert bundle.means.shape == (bundle.snp_count,)
        assert bundle.stds.shape == (bundle.snp_count,)

    def test_centroids_all_populations(self, bundle: AncestryBundle) -> None:
        for pop in bundle.populations:
            assert pop in bundle.reference_centroids
            assert len(bundle.reference_centroids[pop]) == bundle.n_components

    def test_snps_have_valid_rsids(self, bundle: AncestryBundle) -> None:
        for snp in bundle.snps:
            assert snp.rsid.startswith(("rs", "i")), f"Invalid rsid: {snp.rsid}"

    def test_rsid_set(self, bundle: AncestryBundle) -> None:
        rsids = bundle.rsid_set()
        assert len(rsids) == bundle.snp_count
        assert all(r.startswith(("rs", "i")) for r in rsids)

    def test_rsid_to_index(self, bundle: AncestryBundle) -> None:
        idx_map = bundle.rsid_to_index()
        assert len(idx_map) == bundle.snp_count
        assert idx_map[bundle.snps[0].rsid] == 0
        assert idx_map[bundle.snps[-1].rsid] == bundle.snp_count - 1

    def test_file_not_found_raises(self) -> None:
        with pytest.raises(FileNotFoundError):
            load_ancestry_bundle(Path("/nonexistent/bundle.npz"))

    def test_population_labels(self, bundle: AncestryBundle) -> None:
        assert "EUR" in bundle.population_labels
        assert "AFR" in bundle.population_labels
        assert "CSA" in bundle.population_labels
        assert "MID" in bundle.population_labels

    def test_seven_populations(self, bundle: AncestryBundle) -> None:
        expected = {"AFR", "AMR", "CSA", "EAS", "EUR", "MID", "OCE"}
        assert set(bundle.populations) == expected

    def test_n_significant_pcs(self, bundle: AncestryBundle) -> None:
        assert bundle.n_significant_pcs == 8

    def test_tw_pvalues(self, bundle: AncestryBundle) -> None:
        assert len(bundle.tw_pvalues) == 20

    def test_eigenvalues(self, bundle: AncestryBundle) -> None:
        assert len(bundle.eigenvalues) == bundle.n_components
        # Eigenvalues should be in descending order
        for i in range(len(bundle.eigenvalues) - 1):
            assert bundle.eigenvalues[i] >= bundle.eigenvalues[i + 1]

    def test_reference_samples_all_populations(self, bundle: AncestryBundle) -> None:
        for pop in bundle.populations:
            assert pop in bundle.reference_samples
            assert len(bundle.reference_samples[pop]) > 0


# ── Genotype encoding tests ──────────────────────────────────────────────


class TestEncodeDosage:
    """Test alt-allele dosage encoding."""

    def test_homozygous_ref(self) -> None:
        assert _encode_dosage("AA", "G") == 0.0

    def test_heterozygous(self) -> None:
        assert _encode_dosage("AG", "G") == 1.0

    def test_homozygous_alt(self) -> None:
        assert _encode_dosage("GG", "G") == 2.0

    def test_none_returns_none(self) -> None:
        assert _encode_dosage(None, "G") is None

    def test_empty_returns_none(self) -> None:
        assert _encode_dosage("", "G") is None

    def test_nocall_returns_none(self) -> None:
        assert _encode_dosage("--", "G") is None
        assert _encode_dosage("00", "G") is None

    def test_case_insensitive(self) -> None:
        assert _encode_dosage("ag", "G") == 1.0
        assert _encode_dosage("AG", "g") == 1.0

    def test_single_char_returns_none(self) -> None:
        assert _encode_dosage("A", "G") is None

    def test_indel_nocall(self) -> None:
        assert _encode_dosage("II", "G") is None
        assert _encode_dosage("DD", "G") is None


# ── PCA projection tests ────────────────────────────────────────────────


class TestProjectOntoPCA:
    """Test PCA projection via NumPy dot product."""

    def test_all_snps_present(self, small_bundle: AncestryBundle) -> None:
        genotype_map = {"rs1": "AG", "rs2": "CT", "rs3": "GA", "rs4": "TC"}
        pc_scores, snps_used = _project_onto_pca(small_bundle, genotype_map)
        assert snps_used == 4
        assert len(pc_scores) == 2

    def test_missing_snps_imputed_as_zero(
        self,
        small_bundle: AncestryBundle,
    ) -> None:
        # Only rs1 present
        genotype_map = {"rs1": "AG"}
        _, snps_used = _project_onto_pca(small_bundle, genotype_map)
        assert snps_used == 1

    def test_empty_genotypes(self, small_bundle: AncestryBundle) -> None:
        pc_scores, snps_used = _project_onto_pca(small_bundle, {})
        assert snps_used == 0
        # All centered values are 0 → pc_scores should be 0
        np.testing.assert_array_equal(pc_scores, np.zeros(2))

    def test_projection_is_linear(self, small_bundle: AncestryBundle) -> None:
        """Verify projection = standardized @ loadings."""
        genotype_map = {"rs1": "GG", "rs2": "TT", "rs3": "AA", "rs4": "CC"}
        pc_scores, _ = _project_onto_pca(small_bundle, genotype_map)

        # Manual computation
        dosages = np.array([2.0, 2.0, 2.0, 2.0])  # all homozygous alt
        standardized = (dosages - small_bundle.means) / small_bundle.stds
        expected = standardized @ small_bundle.loadings
        np.testing.assert_array_almost_equal(pc_scores, expected)


# ── Nearest centroid classification tests ────────────────────────────────


class TestClassifyNearestCentroid:
    """Test nearest-centroid classification."""

    def test_exact_centroid_match(self) -> None:
        centroids = {
            "AFR": np.array([10.0, 0.0]),
            "EUR": np.array([-10.0, 0.0]),
        }
        pop, dists = _classify_nearest_centroid(np.array([10.0, 0.0]), centroids)
        assert pop == "AFR"
        assert dists["AFR"] == 0.0

    def test_nearest_classification(self) -> None:
        centroids = {
            "AFR": np.array([10.0, 0.0]),
            "EUR": np.array([-10.0, 0.0]),
            "EAS": np.array([0.0, 10.0]),
        }
        # Point closer to EUR
        pop, dists = _classify_nearest_centroid(np.array([-8.0, 1.0]), centroids)
        assert pop == "EUR"
        assert dists["EUR"] < dists["AFR"]
        assert dists["EUR"] < dists["EAS"]

    def test_all_distances_returned(self) -> None:
        centroids = {
            "AFR": np.array([1.0, 0.0]),
            "EUR": np.array([0.0, 1.0]),
            "EAS": np.array([0.0, 0.0]),
        }
        _, dists = _classify_nearest_centroid(np.array([0.5, 0.5]), centroids)
        assert len(dists) == 3
        assert all(d >= 0 for d in dists.values())


# ── Admixture fraction tests (P3-24, T3-24) ──────────────────────────


class TestComputeAdmixtureFractions:
    """Test admixture fraction computation via inverse-distance weighting."""

    def test_fractions_sum_to_one(self) -> None:
        """T3-24: Admixture fractions sum to ~1.0."""
        distances = {"AFR": 10.0, "EUR": 5.0, "EAS": 20.0, "SAS": 15.0}
        fractions = compute_admixture_fractions(distances)
        assert abs(sum(fractions.values()) - 1.0) < 1e-6

    def test_closer_population_has_higher_fraction(self) -> None:
        distances = {"AFR": 100.0, "EUR": 1.0, "EAS": 50.0}
        fractions = compute_admixture_fractions(distances)
        assert fractions["EUR"] > fractions["AFR"]
        assert fractions["EUR"] > fractions["EAS"]

    def test_exact_centroid_gives_1_0(self) -> None:
        """Sample at exact centroid → 100% that population."""
        distances = {"AFR": 0.0, "EUR": 10.0, "EAS": 20.0}
        fractions = compute_admixture_fractions(distances)
        assert fractions["AFR"] == 1.0
        assert fractions["EUR"] == 0.0
        assert fractions["EAS"] == 0.0

    def test_equal_distances_give_equal_fractions(self) -> None:
        distances = {"AFR": 5.0, "EUR": 5.0, "EAS": 5.0}
        fractions = compute_admixture_fractions(distances)
        for frac in fractions.values():
            assert abs(frac - 1.0 / 3) < 0.01

    def test_empty_distances(self) -> None:
        fractions = compute_admixture_fractions({})
        assert fractions == {}

    def test_all_populations_present(self) -> None:
        distances = {"AFR": 10.0, "AMR": 20.0, "EAS": 30.0, "EUR": 5.0, "SAS": 25.0, "OCE": 40.0}
        fractions = compute_admixture_fractions(distances)
        assert set(fractions.keys()) == set(distances.keys())
        assert all(0.0 <= f <= 1.0 for f in fractions.values())
        assert abs(sum(fractions.values()) - 1.0) < 1e-6

    def test_fractions_are_non_negative(self) -> None:
        distances = {"AFR": 1.0, "EUR": 100.0, "EAS": 1000.0}
        fractions = compute_admixture_fractions(distances)
        assert all(f >= 0.0 for f in fractions.values())

    def test_single_population(self) -> None:
        distances = {"EUR": 5.0}
        fractions = compute_admixture_fractions(distances)
        assert fractions["EUR"] == 1.0

    def test_two_zero_distances(self) -> None:
        """Multiple populations at distance 0 — share equally."""
        distances = {"AFR": 0.0, "EUR": 0.0, "EAS": 10.0}
        fractions = compute_admixture_fractions(distances)
        assert abs(sum(fractions.values()) - 1.0) < 1e-6
        zero_pops = [p for p, d in distances.items() if d < 1e-10]
        expected_share = 1.0 / len(zero_pops)
        for p in zero_pops:
            assert abs(fractions[p] - expected_share) < 1e-6
        assert fractions["EAS"] == 0.0


class TestAdmixtureFractionsIntegration:
    """Test admixture fractions integrated into the inference pipeline."""

    def test_infer_returns_admixture_fractions(
        self,
        small_bundle: AncestryBundle,
        eur_sample: sa.Engine,
    ) -> None:
        result = infer_ancestry(small_bundle, eur_sample)
        assert hasattr(result, "admixture_fractions")
        assert len(result.admixture_fractions) == 3  # AFR, EUR, EAS
        assert abs(sum(result.admixture_fractions.values()) - 1.0) < 1e-6

    def test_top_population_has_highest_fraction(
        self,
        small_bundle: AncestryBundle,
        eur_sample: sa.Engine,
    ) -> None:
        result = infer_ancestry(small_bundle, eur_sample)
        top_frac = result.admixture_fractions[result.top_population]
        for pop, frac in result.admixture_fractions.items():
            if pop != result.top_population:
                assert top_frac >= frac

    def test_stored_finding_has_admixture(
        self,
        small_bundle: AncestryBundle,
        eur_sample: sa.Engine,
    ) -> None:
        result = infer_ancestry(small_bundle, eur_sample)
        store_ancestry_findings(result, eur_sample)

        with eur_sample.connect() as conn:
            row = conn.execute(
                sa.select(findings)
                .where(findings.c.module == "ancestry")
                .where(findings.c.category == "nnls_admixture")
            ).fetchone()
        detail = json.loads(row.detail_json)
        assert "admixture_fractions" in detail
        fracs = detail["admixture_fractions"]
        assert abs(sum(fracs.values()) - 1.0) < 1e-6

    def test_insufficient_coverage_still_computes_fractions(
        self,
        small_bundle: AncestryBundle,
        partial_sample: sa.Engine,
    ) -> None:
        """Fractions computed even with low coverage, but finding not stored."""
        result = infer_ancestry(small_bundle, partial_sample)
        assert result.is_sufficient is False
        # Fractions still computed even though coverage is insufficient
        assert len(result.admixture_fractions) > 0


# ── Integration tests ────────────────────────────────────────────────────


class TestInferAncestry:
    """Test full ancestry inference pipeline."""

    def test_infer_with_full_coverage(
        self,
        small_bundle: AncestryBundle,
        eur_sample: sa.Engine,
    ) -> None:
        result = infer_ancestry(small_bundle, eur_sample)
        assert result.snps_used == 4
        assert result.snps_total == 4
        assert result.coverage_fraction == 1.0
        assert result.is_sufficient is True
        assert result.top_population in ("AFR", "EUR", "EAS")

    def test_infer_returns_pc_scores(
        self,
        small_bundle: AncestryBundle,
        eur_sample: sa.Engine,
    ) -> None:
        result = infer_ancestry(small_bundle, eur_sample)
        assert len(result.pc_scores) == 2

    def test_infer_returns_population_distances(
        self,
        small_bundle: AncestryBundle,
        eur_sample: sa.Engine,
    ) -> None:
        result = infer_ancestry(small_bundle, eur_sample)
        assert "AFR" in result.population_distances
        assert "EUR" in result.population_distances
        assert "EAS" in result.population_distances

    def test_insufficient_coverage(
        self,
        small_bundle: AncestryBundle,
        partial_sample: sa.Engine,
    ) -> None:
        result = infer_ancestry(small_bundle, partial_sample)
        assert result.snps_used == 1
        assert result.coverage_fraction == 0.25
        assert result.is_sufficient is False
        assert result.classification_status == "uncertain"
        assert result.top_population == UNCERTAIN
        assert "low_coverage" in result.quality_flags

    def test_full_coverage_ambiguous_sample_is_admixed(
        self,
        small_bundle: AncestryBundle,
        admixed_sample: sa.Engine,
    ) -> None:
        result = infer_ancestry(small_bundle, admixed_sample)
        assert result.snps_used == 4
        assert result.coverage_fraction == 1.0
        assert result.is_sufficient is True
        assert result.classification_status == "admixed"
        assert result.top_population == ADMIXED
        assert "low_centroid_margin" in result.quality_flags

    def test_empty_sample(
        self,
        small_bundle: AncestryBundle,
        sample_engine: sa.Engine,
    ) -> None:
        result = infer_ancestry(small_bundle, sample_engine)
        assert result.snps_used == 0
        assert result.is_sufficient is False

    def test_uses_annotated_variants_when_available(
        self,
        small_bundle: AncestryBundle,
        sample_engine: sa.Engine,
    ) -> None:
        """When annotated_variants has data, use it instead of raw_variants."""
        genotypes = [
            {
                "rsid": "rs1",
                "chrom": "1",
                "pos": 100,
                "genotype": "GG",
                "annotation_coverage": 1,
            },
            {
                "rsid": "rs2",
                "chrom": "2",
                "pos": 200,
                "genotype": "TT",
                "annotation_coverage": 1,
            },
            {
                "rsid": "rs3",
                "chrom": "3",
                "pos": 300,
                "genotype": "AA",
                "annotation_coverage": 1,
            },
            {
                "rsid": "rs4",
                "chrom": "4",
                "pos": 400,
                "genotype": "CC",
                "annotation_coverage": 1,
            },
        ]
        _insert_annotated_genotypes(sample_engine, genotypes)
        result = infer_ancestry(small_bundle, sample_engine)
        assert result.snps_used == 4

    def test_projection_under_2_seconds(
        self,
        bundle: AncestryBundle,
        sample_engine: sa.Engine,
    ) -> None:
        """Performance regression guard: full ancestry inference stays fast.

        Real inference is well under a second; this is a coarse guard against an
        accidental algorithmic blow-up (e.g. an O(n^2) regression), NOT a
        micro-benchmark. A tight 2s wall-clock bound flaked on loaded shared CI
        runners (observed 2.58s on macOS x86_64), so the bound is set generously to
        absorb runner variance while still catching a genuine regression.
        """
        # Insert some raw variants that match bundle SNPs
        bundle_snps = list(bundle.snps)[:50]
        genotypes = [
            {"rsid": s.rsid, "chrom": s.chrom, "pos": s.pos, "genotype": "AG"} for s in bundle_snps
        ]
        if genotypes:
            _insert_raw_genotypes(sample_engine, genotypes)

        t0 = time.perf_counter()
        infer_ancestry(bundle, sample_engine)
        elapsed = time.perf_counter() - t0
        assert elapsed < 8.0, f"Inference took {elapsed:.3f}s, expected < 8s (regression guard)"


# ── T3-25: EUR sample classification ────────────────────────────────────


class TestEURClassification:
    """T3-25: PCA projection places known EUR-ancestry sample in EUR cluster."""

    def test_eur_sample_classified_as_eur(
        self,
        bundle: AncestryBundle,
        sample_engine: sa.Engine,
    ) -> None:
        """A realistic EUR genome must classify *confidently* as EUR.

        Uses the synthetic clean-EUR fixture (genotypes sampled from EUR allele
        frequencies — projects squarely into the EUR cluster). A homozygous-ref
        pattern is NOT a valid EUR proxy: it is geometrically ambiguous and the
        PCA fix correctly reports it as ADMIXED, so this test uses a realistic
        EUR genotype profile instead.
        """
        fixture = Path(__file__).resolve().parent.parent / "fixtures" / "synthetic_eur_23andme.txt"
        genotypes = []
        for line in fixture.read_text(encoding="utf-8").splitlines():
            if line.startswith("#") or not line.strip():
                continue
            rsid, chrom, pos, geno = line.split("\t")
            genotypes.append({"rsid": rsid, "chrom": chrom, "pos": int(pos), "genotype": geno})
        _insert_raw_genotypes(sample_engine, genotypes)

        result = infer_ancestry(bundle, sample_engine)
        assert result.is_sufficient
        assert result.snps_used == bundle.snp_count
        # T3-25 acceptance: a realistic EUR genome must land *confidently* in EUR —
        # not vacuously "some population", and not the honest ADMIXED/UNCERTAIN
        # fallbacks (which would mean we lost the ability to make a clean call).
        assert result.classification_status == "confident"
        assert result.top_population == "EUR", (
            f"EUR genome classified as {result.top_population!r}, expected 'EUR'"
        )


# ── Confidence-gated classification (PCA fix) ────────────────────────────


class TestClassifyWithConfidence:
    """Lock the PCA honesty fix against the real-data oracle.

    The distance vectors below are the *actual* nearest-centroid squared distances
    measured by running the production bundle on real public PGP-HMS genomes
    (https://my.pgp-hms.org/public_genetic_data) with self-reported ancestry. Before
    the fix, every low-coverage and admixed sample was confidently mislabelled
    "CSA" (the centroid nearest the PCA origin). The fix must report the honest
    EUR/AFR/AMR calls, ADMIXED for the between-clusters case, and UNCERTAIN for the
    low-coverage cases — never a confident wrong continental label.
    """

    # huid: (coverage, {pop: squared_distance}, expected_top, expected_status)
    ORACLE = {
        # Confident continental calls (must be preserved).
        "huD8A898(White)": (
            0.975,
            {
                "EUR": 3.8,
                "MID": 184.0,
                "CSA": 725.4,
                "AFR": 2203.0,
                "EAS": 2998.2,
                "AMR": 11129.0,
                "OCE": 19466.2,
            },
            "EUR",
            "confident",
        ),
        "hu1BDBA5(White)": (
            0.983,
            {
                "EUR": 21.5,
                "MID": 282.2,
                "CSA": 685.3,
                "AFR": 2052.9,
                "EAS": 2790.0,
                "AMR": 10658.4,
                "OCE": 19243.2,
            },
            "EUR",
            "confident",
        ),
        "huA2ED68(AFR)": (
            0.992,
            {
                "AFR": 56.2,
                "MID": 1220.7,
                "CSA": 1342.5,
                "EUR": 1487.0,
                "EAS": 2784.3,
                "AMR": 11478.2,
                "OCE": 18794.1,
            },
            "AFR",
            "confident",
        ),
        "huA08F4D(AmInd/Hispanic)": (
            0.98,
            {
                "AMR": 1065.0,
                "EAS": 3717.4,
                "CSA": 4661.0,
                "EUR": 5439.5,
                "MID": 5867.1,
                "AFR": 6609.5,
                "OCE": 20168.8,
            },
            "AMR",
            "confident",
        ),
        # Admixed (98% coverage, Black+White): was confidently "CSA" → must be ADMIXED.
        "hu7ECB9C(Black+White)": (
            0.981,
            {
                "CSA": 103.7,
                "EUR": 289.2,
                "MID": 356.6,
                "AFR": 1511.3,
                "EAS": 1776.9,
                "AMR": 10153.1,
                "OCE": 16620.0,
            },
            ADMIXED,
            "admixed",
        ),
        # Low coverage (old 23andMe v4): was confidently "CSA" → must be UNCERTAIN.
        "hu8DD170(White,30%)": (
            0.305,
            {
                "CSA": 225.2,
                "EUR": 284.6,
                "MID": 341.2,
                "AFR": 1336.0,
                "EAS": 1535.8,
                "AMR": 9681.4,
                "OCE": 17011.5,
            },
            UNCERTAIN,
            "uncertain",
        ),
        "hu1EF1AF(Black,30%)": (
            0.305,
            {
                "CSA": 318.3,
                "MID": 473.1,
                "EUR": 511.9,
                "AFR": 887.2,
                "EAS": 1444.9,
                "AMR": 9805.1,
                "OCE": 16718.2,
            },
            UNCERTAIN,
            "uncertain",
        ),
    }

    def test_real_pgp_oracle(self) -> None:
        for name, (cov, dists, exp_pop, exp_status) in self.ORACLE.items():
            pop, status, _flags = _classify_with_confidence(cov, dists)
            assert pop == exp_pop, f"{name}: got {pop!r}, expected {exp_pop!r}"
            assert status == exp_status, f"{name}: status {status!r}, expected {exp_status!r}"

    def test_no_oracle_sample_is_confidently_csa(self) -> None:
        # The headline regression: nobody is ever confidently called CSA via the sink.
        for name, (cov, dists, _exp_pop, _exp_status) in self.ORACLE.items():
            pop, status, _ = _classify_with_confidence(cov, dists)
            assert not (pop == "CSA" and status == "confident"), f"{name} confidently CSA again"

    def test_origin_low_coverage_is_uncertain(self) -> None:
        # An origin-collapsed projection (CSA nearest) below the coverage floor → UNCERTAIN.
        origin_dists = {
            "CSA": 244.85,
            "MID": 626.6,
            "EUR": 643.2,
            "AFR": 1071.6,
            "EAS": 1119.6,
            "AMR": 9310.5,
            "OCE": 16310.4,
        }
        pop, status, flags = _classify_with_confidence(0.10, origin_dists)
        assert pop == UNCERTAIN and status == "uncertain"
        assert "low_coverage" in flags

    def test_origin_high_coverage_is_admixed_not_csa(self) -> None:
        # Even at full coverage, a near-origin projection (CSA nearest but low margin)
        # must be ADMIXED, never a confident CSA.
        origin_dists = {
            "CSA": 244.85,
            "MID": 626.6,
            "EUR": 643.2,
            "AFR": 1071.6,
            "EAS": 1119.6,
            "AMR": 9310.5,
            "OCE": 16310.4,
        }
        pop, status, _ = _classify_with_confidence(1.0, origin_dists)
        assert pop == ADMIXED and status == "admixed"

    def test_coverage_boundary(self) -> None:
        clean = {"EUR": 3.8, "MID": 184.0, "CSA": 725.4, "AFR": 2203.0}
        # Just below the floor → UNCERTAIN; at/above with a decisive margin → confident.
        assert _classify_with_confidence(0.54, clean)[0] == UNCERTAIN
        assert _classify_with_confidence(0.55, clean) == ("EUR", "confident", [])


# ── Findings storage tests ───────────────────────────────────────────────


class TestStoreAncestryFindings:
    """Test ancestry findings storage in the sample database."""

    def test_stores_three_findings(
        self,
        small_bundle: AncestryBundle,
        eur_sample: sa.Engine,
    ) -> None:
        result = infer_ancestry(small_bundle, eur_sample)
        count = store_ancestry_findings(result, eur_sample)
        assert count == 3

    def test_finding_has_module_ancestry(
        self,
        small_bundle: AncestryBundle,
        eur_sample: sa.Engine,
    ) -> None:
        result = infer_ancestry(small_bundle, eur_sample)
        store_ancestry_findings(result, eur_sample)

        with eur_sample.connect() as conn:
            rows = conn.execute(
                sa.select(findings).where(findings.c.module == "ancestry")
            ).fetchall()
        assert len(rows) == 3
        categories = {r.category for r in rows}
        assert categories == {"pca_projection", "nnls_admixture", "knn_admixture"}

    def test_detail_json_has_top_population(
        self,
        small_bundle: AncestryBundle,
        eur_sample: sa.Engine,
    ) -> None:
        result = infer_ancestry(small_bundle, eur_sample)
        store_ancestry_findings(result, eur_sample)

        with eur_sample.connect() as conn:
            row = conn.execute(
                sa.select(findings)
                .where(findings.c.module == "ancestry")
                .where(findings.c.category == "nnls_admixture")
            ).fetchone()
        detail = json.loads(row.detail_json)
        assert "top_population" in detail
        assert detail["top_population"] == result.top_population

    def test_detail_json_has_pc_scores(
        self,
        small_bundle: AncestryBundle,
        eur_sample: sa.Engine,
    ) -> None:
        result = infer_ancestry(small_bundle, eur_sample)
        store_ancestry_findings(result, eur_sample)

        with eur_sample.connect() as conn:
            row = conn.execute(
                sa.select(findings)
                .where(findings.c.module == "ancestry")
                .where(findings.c.category == "pca_projection")
            ).fetchone()
        detail = json.loads(row.detail_json)
        assert "pc_scores" in detail
        assert len(detail["pc_scores"]) == 2

    def test_detail_json_has_inferred_ancestry(
        self,
        small_bundle: AncestryBundle,
        eur_sample: sa.Engine,
    ) -> None:
        """Verify detail_json has 'inferred_ancestry' for get_inferred_ancestry()."""
        result = infer_ancestry(small_bundle, eur_sample)
        store_ancestry_findings(result, eur_sample)

        with eur_sample.connect() as conn:
            row = conn.execute(
                sa.select(findings)
                .where(findings.c.module == "ancestry")
                .where(findings.c.category == "nnls_admixture")
            ).fetchone()
        detail = json.loads(row.detail_json)
        assert "inferred_ancestry" in detail
        assert detail["inferred_ancestry"] == result.top_population

    def test_clears_previous_findings_on_rerun(
        self,
        small_bundle: AncestryBundle,
        eur_sample: sa.Engine,
    ) -> None:
        result = infer_ancestry(small_bundle, eur_sample)
        store_ancestry_findings(result, eur_sample)
        store_ancestry_findings(result, eur_sample)

        with eur_sample.connect() as conn:
            count = conn.execute(
                sa.select(sa.func.count())
                .select_from(findings)
                .where(findings.c.module == "ancestry")
            ).scalar()
        assert count == 3  # 3 categories, not 6

    def test_insufficient_coverage_stores_nothing(
        self,
        small_bundle: AncestryBundle,
        partial_sample: sa.Engine,
    ) -> None:
        result = infer_ancestry(small_bundle, partial_sample)
        count = store_ancestry_findings(result, partial_sample)
        assert count == 0

    def test_evidence_level_2(
        self,
        small_bundle: AncestryBundle,
        eur_sample: sa.Engine,
    ) -> None:
        result = infer_ancestry(small_bundle, eur_sample)
        store_ancestry_findings(result, eur_sample)

        with eur_sample.connect() as conn:
            rows = conn.execute(
                sa.select(findings).where(findings.c.module == "ancestry")
            ).fetchall()
        for row in rows:
            assert row.evidence_level == 2

    def test_finding_text_contains_population(
        self,
        small_bundle: AncestryBundle,
        eur_sample: sa.Engine,
    ) -> None:
        result = infer_ancestry(small_bundle, eur_sample)
        store_ancestry_findings(result, eur_sample)

        with eur_sample.connect() as conn:
            row = conn.execute(
                sa.select(findings)
                .where(findings.c.module == "ancestry")
                .where(findings.c.category == "nnls_admixture")
            ).fetchone()
        assert result.top_population in row.finding_text


# ── PCA coordinates for visualization (P3-25) ────────────────────────────


class TestGetPCACoordinates:
    """Test PCA coordinates for scatter plot visualization (P3-25)."""

    def test_returns_pca_coordinates(
        self,
        small_bundle: AncestryBundle,
        eur_sample: sa.Engine,
    ) -> None:
        result = infer_ancestry(small_bundle, eur_sample)
        coords = get_pca_coordinates(small_bundle, result)
        assert isinstance(coords, PCACoordinates)

    def test_user_coordinates_match_result(
        self,
        small_bundle: AncestryBundle,
        eur_sample: sa.Engine,
    ) -> None:
        result = infer_ancestry(small_bundle, eur_sample)
        coords = get_pca_coordinates(small_bundle, result)
        assert coords.user == result.pc_scores

    def test_reference_samples_present(
        self,
        small_bundle: AncestryBundle,
        eur_sample: sa.Engine,
    ) -> None:
        result = infer_ancestry(small_bundle, eur_sample)
        coords = get_pca_coordinates(small_bundle, result)
        assert len(coords.reference_samples) == 3  # AFR, EUR, EAS
        for pop in ["AFR", "EUR", "EAS"]:
            assert pop in coords.reference_samples
            assert len(coords.reference_samples[pop]) > 0

    def test_reference_samples_have_correct_dimensions(
        self,
        small_bundle: AncestryBundle,
        eur_sample: sa.Engine,
    ) -> None:
        result = infer_ancestry(small_bundle, eur_sample)
        coords = get_pca_coordinates(small_bundle, result)
        for pop, samples in coords.reference_samples.items():
            for sample in samples:
                assert len(sample) == coords.n_components, (
                    f"{pop} sample has {len(sample)} dims, expected {coords.n_components}"
                )

    def test_centroids_present(
        self,
        small_bundle: AncestryBundle,
        eur_sample: sa.Engine,
    ) -> None:
        result = infer_ancestry(small_bundle, eur_sample)
        coords = get_pca_coordinates(small_bundle, result)
        assert len(coords.centroids) == 3
        for pop in ["AFR", "EUR", "EAS"]:
            assert pop in coords.centroids
            assert len(coords.centroids[pop]) == coords.n_components

    def test_population_labels(
        self,
        small_bundle: AncestryBundle,
        eur_sample: sa.Engine,
    ) -> None:
        result = infer_ancestry(small_bundle, eur_sample)
        coords = get_pca_coordinates(small_bundle, result)
        assert coords.population_labels == {
            "AFR": "African",
            "EUR": "European",
            "EAS": "East Asian",
        }

    def test_pc_labels(
        self,
        small_bundle: AncestryBundle,
        eur_sample: sa.Engine,
    ) -> None:
        result = infer_ancestry(small_bundle, eur_sample)
        coords = get_pca_coordinates(small_bundle, result)
        assert coords.pc_labels == ["PC1", "PC2"]
        assert coords.n_components == 2

    def test_with_real_bundle(
        self,
        bundle: AncestryBundle,
        sample_engine: sa.Engine,
    ) -> None:
        """PCA coordinates work with the full bundle (8 PCs, 5000 AIMs)."""
        # Insert some genotypes
        genotypes = [
            {"rsid": s.rsid, "chrom": s.chrom, "pos": s.pos, "genotype": s.ref * 2}
            for s in bundle.snps[:50]
        ]
        _insert_raw_genotypes(sample_engine, genotypes)
        result = infer_ancestry(bundle, sample_engine)
        coords = get_pca_coordinates(bundle, result)
        assert coords.n_components == 8
        assert len(coords.pc_labels) == 8
        assert len(coords.user) == 8
        # Reference samples should have all 7 populations
        assert len(coords.reference_samples) == 7
        for pop in bundle.populations:
            assert pop in coords.reference_samples
            assert len(coords.reference_samples[pop]) > 0


class TestBundleReferenceSamples:
    """Test reference samples in the ancestry PCA bundle."""

    def test_bundle_has_reference_samples(self, bundle: AncestryBundle) -> None:
        assert len(bundle.reference_samples) > 0

    def test_all_populations_have_samples(self, bundle: AncestryBundle) -> None:
        for pop in bundle.populations:
            assert pop in bundle.reference_samples, f"Missing reference samples for {pop}"
            assert len(bundle.reference_samples[pop]) > 0

    def test_reference_sample_dimensions(self, bundle: AncestryBundle) -> None:
        for pop, samples in bundle.reference_samples.items():
            for i, sample in enumerate(samples):
                assert len(sample) == bundle.n_components, (
                    f"{pop} sample {i} has {len(sample)} dims, expected {bundle.n_components}"
                )

    def test_reference_samples_near_centroids(self, bundle: AncestryBundle) -> None:
        """Reference samples should cluster near their population centroids."""
        for pop, samples in bundle.reference_samples.items():
            centroid = bundle.reference_centroids[pop]
            mean = np.mean(samples, axis=0)
            dist = float(np.sqrt(np.sum((mean - centroid) ** 2)))
            # Mean of samples should be reasonably close to centroid
            assert dist < 10.0, f"{pop} mean distance to centroid: {dist:.2f}"


# ── NNLS admixture tests (T-ANC-01) ────────────────────────────────────────


class TestEstimateAdmixtureNNLS:
    """T-ANC-01: NNLS fractions sum to 1.0."""

    def test_fractions_sum_to_one(self, small_bundle: AncestryBundle) -> None:
        user_pcs = np.array([-0.5, 1.0])
        fracs = estimate_admixture_nnls(user_pcs, small_bundle)
        assert abs(sum(fracs.values()) - 1.0) < 0.001

    def test_all_populations_present(self, small_bundle: AncestryBundle) -> None:
        user_pcs = np.array([0.0, 0.0])
        fracs = estimate_admixture_nnls(user_pcs, small_bundle)
        for pop in small_bundle.reference_centroids:
            assert pop in fracs

    def test_near_centroid_gives_high_fraction(self, small_bundle: AncestryBundle) -> None:
        eur_centroid = small_bundle.reference_centroids["EUR"]
        fracs = estimate_admixture_nnls(eur_centroid, small_bundle)
        assert fracs["EUR"] > 0.5

    def test_fractions_non_negative(self, small_bundle: AncestryBundle) -> None:
        user_pcs = np.array([1.0, -0.5])
        fracs = estimate_admixture_nnls(user_pcs, small_bundle)
        assert all(f >= 0.0 for f in fracs.values())


# ── #486 regression: NNLS admixture must agree with nearest-centroid & kNN ──


class TestAdmixtureCoherenceRealBundle:
    """Issue #486: the reported (NNLS/FCLS) admixture breakdown must not contradict
    the nearest-centroid ``top_population`` and the kNN admixture, since all three
    derive from the same PC vector.

    The repro is a real Harvard PGP sample (23andMe v5, GRCh37, coverage 0.98).
    The centroids are near-collinear, so free NNLS reconstructs this sample with
    huge, nearly-cancelling coefficients whose normalized proportions are
    degenerate: the breakdown gave AMR only 1.8% (the *smallest* component) while
    nearest-centroid said AMR by a 3.5x margin and kNN said AMR 100% — a
    user-facing contradiction. The simplex-constrained solve restores coherence.
    """

    # Projected PC scores reported in issue #486 (8 PCs).
    PGP_4215_PCS = np.array([35.2, -6.1, 42.9, 31.8, 2.3, 0.4, 0.9, 0.7])

    def test_nearest_centroid_is_amr(self, bundle: AncestryBundle) -> None:
        nearest, distances = _classify_nearest_centroid(
            self.PGP_4215_PCS, bundle.reference_centroids
        )
        assert nearest == "AMR"
        # AMR must win by a wide margin (the issue measured ~3.5x).
        ordered = sorted(distances.values())
        assert ordered[1] / ordered[0] > 3.0, distances

    def test_knn_is_amr(self, bundle: AncestryBundle) -> None:
        knn = estimate_admixture_knn(self.PGP_4215_PCS, bundle)
        assert max(knn, key=lambda p: knn[p]) == "AMR"

    def test_nnls_admixture_argmax_matches_nearest_centroid(self, bundle: AncestryBundle) -> None:
        """The reported admixture's dominant population must equal top_population."""
        fracs = estimate_admixture_nnls(self.PGP_4215_PCS, bundle)
        nearest, _ = _classify_nearest_centroid(self.PGP_4215_PCS, bundle.reference_centroids)
        assert max(fracs, key=lambda p: fracs[p]) == nearest == "AMR", fracs

    def test_nnls_amr_is_not_the_smallest_component(self, bundle: AncestryBundle) -> None:
        """Guards the exact #486 symptom: AMR was the least-represented ancestry."""
        fracs = estimate_admixture_nnls(self.PGP_4215_PCS, bundle)
        assert fracs["AMR"] == max(fracs.values()), fracs
        assert fracs["AMR"] > min(fracs.values())

    def test_nnls_and_knn_now_agree(self, bundle: AncestryBundle) -> None:
        """Confidence (cosine similarity of NNLS vs kNN) must reflect agreement.

        Free NNLS scored 0.0388 here (issue body); a coherent convex mixture
        dominated by AMR aligns with the kNN AMR vector.
        """
        nnls = estimate_admixture_nnls(self.PGP_4215_PCS, bundle)
        knn = estimate_admixture_knn(self.PGP_4215_PCS, bundle)
        assert compute_confidence(nnls, knn) > 0.5

    def test_fractions_sum_to_one(self, bundle: AncestryBundle) -> None:
        fracs = estimate_admixture_nnls(self.PGP_4215_PCS, bundle)
        assert abs(sum(fracs.values()) - 1.0) < 1e-6
        assert all(f >= 0.0 for f in fracs.values())


# ── kNN admixture tests (T-ANC-02) ────────────────────────────────────────


class TestEstimateAdmixtureKNN:
    """T-ANC-02: kNN fractions sum to 1.0."""

    def test_fractions_sum_to_one(self, small_bundle: AncestryBundle) -> None:
        user_pcs = np.array([-1.0, 1.5])
        fracs = estimate_admixture_knn(user_pcs, small_bundle)
        assert abs(sum(fracs.values()) - 1.0) < 0.001

    def test_near_eur_cluster(self, small_bundle: AncestryBundle) -> None:
        eur_centroid = small_bundle.reference_centroids["EUR"]
        # With k=3 (small bundle has only 9 samples), EUR should dominate
        fracs = estimate_admixture_knn(eur_centroid, small_bundle, k=3)
        assert fracs["EUR"] > 0.5

    def test_near_afr_cluster(self, small_bundle: AncestryBundle) -> None:
        afr_centroid = small_bundle.reference_centroids["AFR"]
        fracs = estimate_admixture_knn(afr_centroid, small_bundle, k=3)
        assert fracs["AFR"] > 0.5


# ── EUR/EAS classification tests (T-ANC-03, T-ANC-04) ──────────────────


class TestClassifyAncestry:
    """T-ANC-03/04: Known samples project into correct clusters."""

    def test_eur_sample_classified(self, small_bundle: AncestryBundle) -> None:
        eur_pcs = small_bundle.reference_centroids["EUR"]
        pop = classify_ancestry(eur_pcs, small_bundle)
        assert pop == "EUR"

    def test_eas_sample_classified(self, small_bundle: AncestryBundle) -> None:
        eas_pcs = small_bundle.reference_centroids["EAS"]
        pop = classify_ancestry(eas_pcs, small_bundle)
        assert pop == "EAS"

    def test_returns_valid_population(self, small_bundle: AncestryBundle) -> None:
        user_pcs = np.array([0.0, 0.0])
        pop = classify_ancestry(user_pcs, small_bundle)
        assert pop in small_bundle.reference_centroids


# ── Missing AIM rate tests (T-ANC-05, T-ANC-06) ──────────────────────────


class TestComputeMissingAIMRate:
    """T-ANC-05/06: Missing AIM rate computation."""

    def test_all_present_is_zero(self, small_bundle: AncestryBundle) -> None:
        genotype_map = {snp.rsid: "AG" for snp in small_bundle.snps}
        rate = compute_missing_aim_rate(genotype_map, small_bundle)
        assert rate == 0.0

    def test_all_missing_is_one(self, small_bundle: AncestryBundle) -> None:
        rate = compute_missing_aim_rate({}, small_bundle)
        assert rate == 1.0

    def test_partial_missing_rate(self, small_bundle: AncestryBundle) -> None:
        # 4 SNPs, remove 1 → 25% missing
        genotype_map = {snp.rsid: "AG" for snp in small_bundle.snps[:-1]}
        rate = compute_missing_aim_rate(genotype_map, small_bundle)
        assert rate == 0.25

    def test_nocall_counted_as_missing(self, small_bundle: AncestryBundle) -> None:
        genotype_map = {snp.rsid: "--" for snp in small_bundle.snps}
        rate = compute_missing_aim_rate(genotype_map, small_bundle)
        assert rate == 1.0


# ── Mean imputation test (T-ANC-07) ──────────────────────────────────────


class TestMeanImputation:
    """T-ANC-07: Mean imputation doesn't crash, produces reasonable coords."""

    def test_partial_data_produces_coordinates(
        self,
        small_bundle: AncestryBundle,
        partial_sample: sa.Engine,
    ) -> None:
        result = infer_ancestry(small_bundle, partial_sample)
        assert len(result.pc_scores) == 2
        assert all(np.isfinite(s) for s in result.pc_scores)

    def test_empty_data_produces_zero_coords(
        self,
        small_bundle: AncestryBundle,
        sample_engine: sa.Engine,
    ) -> None:
        result = infer_ancestry(small_bundle, sample_engine)
        assert all(s == 0.0 for s in result.pc_scores)


# ── Confidence tests (T-ANC-08) ──────────────────────────────────────────


class TestComputeConfidence:
    """T-ANC-08: Confidence > 0.9 when NNLS/kNN agree, < 0.5 when disagree."""

    def test_identical_fractions_high_confidence(self) -> None:
        fracs = {"AFR": 0.8, "EUR": 0.1, "EAS": 0.1}
        conf = compute_confidence(fracs, fracs)
        assert conf > 0.99

    def test_similar_fractions_high_confidence(self) -> None:
        nnls = {"AFR": 0.7, "EUR": 0.2, "EAS": 0.1}
        knn = {"AFR": 0.65, "EUR": 0.25, "EAS": 0.1}
        conf = compute_confidence(nnls, knn)
        assert conf > 0.9

    def test_opposite_fractions_low_confidence(self) -> None:
        nnls = {"AFR": 1.0, "EUR": 0.0, "EAS": 0.0}
        knn = {"AFR": 0.0, "EUR": 0.0, "EAS": 1.0}
        conf = compute_confidence(nnls, knn)
        assert conf < 0.5

    def test_confidence_range(self) -> None:
        nnls = {"AFR": 0.5, "EUR": 0.3, "EAS": 0.2}
        knn = {"AFR": 0.4, "EUR": 0.4, "EAS": 0.2}
        conf = compute_confidence(nnls, knn)
        assert 0.0 <= conf <= 1.0


# ── Integration: full pipeline (T-ANC-09) ────────────────────────────────


class TestFullPipelineIntegration:
    """T-ANC-09: Full pipeline produces findings in sample DB."""

    def test_pipeline_stores_findings(
        self,
        small_bundle: AncestryBundle,
        eur_sample: sa.Engine,
    ) -> None:
        result = infer_ancestry(small_bundle, eur_sample)
        count = store_ancestry_findings(result, eur_sample)
        assert count == 3

        with eur_sample.connect() as conn:
            rows = conn.execute(
                sa.select(findings).where(findings.c.module == "ancestry")
            ).fetchall()
        assert len(rows) == 3
        categories = {r.category for r in rows}
        assert "nnls_admixture" in categories
        assert "knn_admixture" in categories
        assert "pca_projection" in categories

    def test_result_has_all_new_fields(
        self,
        small_bundle: AncestryBundle,
        eur_sample: sa.Engine,
    ) -> None:
        result = infer_ancestry(small_bundle, eur_sample)
        assert result.admixture_method == "nnls"
        assert 0.0 <= result.confidence <= 1.0
        assert 0.0 <= result.missing_aim_rate <= 1.0
        assert result.n_pcs_used == small_bundle.n_components
        assert result.nnls_fractions is not None
        assert result.knn_fractions is not None

    def test_get_inferred_ancestry_prefers_nnls(
        self,
        small_bundle: AncestryBundle,
        eur_sample: sa.Engine,
    ) -> None:
        result = infer_ancestry(small_bundle, eur_sample)
        store_ancestry_findings(result, eur_sample)
        ancestry = get_inferred_ancestry(eur_sample)
        assert ancestry == result.top_population


# ── Property: fractions always sum to ~1.0 (T-ANC-10) ────────────────────


class TestFractionsSumProperty:
    """T-ANC-10: Fractions always sum to ~1.0 regardless of input."""

    @pytest.mark.parametrize(
        "user_pcs",
        [
            np.array([0.0, 0.0]),
            np.array([100.0, -100.0]),
            np.array([-50.0, 50.0]),
            np.array([2.0, -1.0]),  # near AFR centroid
            np.array([-1.0, 1.5]),  # near EUR centroid
        ],
    )
    def test_nnls_fractions_sum_to_one(
        self,
        small_bundle: AncestryBundle,
        user_pcs: np.ndarray,
    ) -> None:
        fracs = estimate_admixture_nnls(user_pcs, small_bundle)
        assert abs(sum(fracs.values()) - 1.0) < 0.001

    @pytest.mark.parametrize(
        "user_pcs",
        [
            np.array([0.0, 0.0]),
            np.array([100.0, -100.0]),
            np.array([-1.0, 1.5]),
        ],
    )
    def test_knn_fractions_sum_to_one(
        self,
        small_bundle: AncestryBundle,
        user_pcs: np.ndarray,
    ) -> None:
        fracs = estimate_admixture_knn(user_pcs, small_bundle)
        assert abs(sum(fracs.values()) - 1.0) < 0.001


# ── classify_ancestry returns valid label (T-ANC-11) ──────────────────────


class TestClassifyAncestryValid:
    """T-ANC-11: classify_ancestry returns a valid population label."""

    @pytest.mark.parametrize(
        "user_pcs",
        [
            np.array([0.0, 0.0]),
            np.array([2.0, -1.0]),
            np.array([-1.0, 1.5]),
            np.array([-0.5, -2.0]),
            np.array([50.0, 50.0]),
        ],
    )
    def test_returns_valid_population(
        self,
        small_bundle: AncestryBundle,
        user_pcs: np.ndarray,
    ) -> None:
        pop = classify_ancestry(user_pcs, small_bundle)
        assert pop in small_bundle.reference_centroids


# ── PRS integration test ─────────────────────────────────────────────────


class TestPRSIntegration:
    """Test that ancestry findings are readable by get_inferred_ancestry()."""

    def test_get_inferred_ancestry_reads_finding(
        self,
        small_bundle: AncestryBundle,
        eur_sample: sa.Engine,
    ) -> None:
        # Before ancestry inference → None
        assert get_inferred_ancestry(eur_sample) is None

        # After ancestry inference → top population code
        result = infer_ancestry(small_bundle, eur_sample)
        store_ancestry_findings(result, eur_sample)

        ancestry = get_inferred_ancestry(eur_sample)
        assert ancestry is not None
        assert ancestry == result.top_population


# ── Data class tests ─────────────────────────────────────────────────────


class TestAncestryResult:
    """Test AncestryResult dataclass."""

    def test_n_components(self) -> None:
        result = AncestryResult(
            pc_scores=[1.0, 2.0, 3.0],
            top_population="EUR",
            population_distances={"EUR": 0.5},
            admixture_fractions={"EUR": 1.0},
            snps_used=100,
            snps_total=128,
            coverage_fraction=0.78,
            projection_time_ms=0.5,
            is_sufficient=True,
        )
        assert result.n_components == 3

    def test_sufficient_coverage(self) -> None:
        result = AncestryResult(
            pc_scores=[1.0],
            top_population="EUR",
            population_distances={},
            admixture_fractions={},
            snps_used=50,
            snps_total=100,
            coverage_fraction=0.5,
            projection_time_ms=0.1,
            is_sufficient=True,
        )
        assert result.is_sufficient is True


class TestAncestryBundle:
    """Test AncestryBundle dataclass."""

    def test_snp_count(self, small_bundle: AncestryBundle) -> None:
        assert small_bundle.snp_count == 4

    def test_rsid_set(self, small_bundle: AncestryBundle) -> None:
        assert small_bundle.rsid_set() == {"rs1", "rs2", "rs3", "rs4"}

    def test_rsid_to_index(self, small_bundle: AncestryBundle) -> None:
        idx = small_bundle.rsid_to_index()
        assert idx["rs1"] == 0
        assert idx["rs4"] == 3


# ── Bootstrap NNLS CI tests (T-MID-01) ──────────────────────────────────


class TestBootstrapAdmixtureNNLS:
    """T-MID-01: Bootstrap CI contains the point estimate."""

    def test_ci_contains_point_estimate(self, small_bundle: AncestryBundle) -> None:
        """Bootstrap 95% CI should contain the NNLS point estimate.

        With only 4 AIMs in the small bundle, bootstrap variability is
        high — use a generous tolerance of 0.15 to account for the small
        feature set producing wide CIs.
        """
        user_pcs = np.array([-1.0, 1.5])  # near EUR centroid
        genotype_map = {snp.rsid: "AG" for snp in small_bundle.snps}

        point_est = estimate_admixture_nnls(user_pcs, small_bundle)
        ci_low, ci_high = bootstrap_admixture_nnls(
            user_pcs,
            small_bundle,
            genotype_map,
            n_iterations=200,
            rng_seed=42,
        )

        for pop in small_bundle.reference_centroids:
            # Point estimate should fall within or near the CI
            # (generous tolerance for small bundle with only 4 AIMs)
            assert ci_low[pop] <= point_est[pop] + 0.15, (
                f"{pop}: ci_low={ci_low[pop]:.4f} > point={point_est[pop]:.4f}"
            )
            assert ci_high[pop] >= point_est[pop] - 0.15, (
                f"{pop}: ci_high={ci_high[pop]:.4f} < point={point_est[pop]:.4f}"
            )

    def test_ci_low_leq_ci_high(self, small_bundle: AncestryBundle) -> None:
        """CI lower bound should always be <= upper bound."""
        genotype_map = {snp.rsid: "AG" for snp in small_bundle.snps}
        ci_low, ci_high = bootstrap_admixture_nnls(
            np.array([0.0, 0.0]),
            small_bundle,
            genotype_map,
            n_iterations=50,
            rng_seed=42,
        )
        for pop in small_bundle.reference_centroids:
            assert ci_low[pop] <= ci_high[pop]

    def test_ci_values_non_negative(self, small_bundle: AncestryBundle) -> None:
        genotype_map = {snp.rsid: "AG" for snp in small_bundle.snps}
        ci_low, ci_high = bootstrap_admixture_nnls(
            np.array([0.0, 0.0]),
            small_bundle,
            genotype_map,
            n_iterations=50,
            rng_seed=42,
        )
        for pop in small_bundle.reference_centroids:
            assert ci_low[pop] >= 0.0
            assert ci_high[pop] >= 0.0

    def test_integration_infer_ancestry_has_ci(
        self,
        small_bundle: AncestryBundle,
        eur_sample: sa.Engine,
    ) -> None:
        """infer_ancestry should populate nnls_ci_low and nnls_ci_high."""
        result = infer_ancestry(small_bundle, eur_sample)
        assert result.nnls_ci_low is not None
        assert result.nnls_ci_high is not None
        for pop in small_bundle.reference_centroids:
            assert pop in result.nnls_ci_low
            assert pop in result.nnls_ci_high

    def test_stored_finding_has_ci(
        self,
        small_bundle: AncestryBundle,
        eur_sample: sa.Engine,
    ) -> None:
        """CI should be stored in the nnls_admixture finding detail_json."""
        result = infer_ancestry(small_bundle, eur_sample)
        store_ancestry_findings(result, eur_sample)

        with eur_sample.connect() as conn:
            row = conn.execute(
                sa.select(findings)
                .where(findings.c.module == "ancestry")
                .where(findings.c.category == "nnls_admixture")
            ).fetchone()
        detail = json.loads(row.detail_json)
        assert "ci_low" in detail
        assert "ci_high" in detail


# ── MID warning tests (T-MID-02, T-MID-03) ──────────────────────────────


class TestMIDWarning:
    """T-MID-02/03: MID lower-precision warning triggers correctly."""

    def test_mid_warning_triggers_below_15pct(self) -> None:
        """T-MID-02: MID warning triggers when MID proportion < 15%."""
        from backend.analysis.gnomix_inference import CANONICAL_POPULATIONS, ChromosomeResult
        from backend.analysis.lai_runner import LAIRunner

        runner = LAIRunner.__new__(LAIRunner)
        n_windows = 100

        # All windows are EUR on both haplotypes — MID gets 0%
        eur_idx = CANONICAL_POPULATIONS.index("EUR")
        hap0 = np.full(n_windows, eur_idx, dtype=np.int32)
        hap1 = np.full(n_windows, eur_idx, dtype=np.int32)

        # Build probabilities with high confidence for EUR
        probs = np.zeros((n_windows, 7), dtype=np.float64)
        probs[:, eur_idx] = 0.95

        chrom_results = {
            1: ChromosomeResult(
                chrom=1,
                n_windows=n_windows,
                hap0_ancestry=hap0,
                hap1_ancestry=hap1,
                hap0_probs=probs,
                hap1_probs=probs,
                window_positions=[(i * 1000, (i + 1) * 1000) for i in range(n_windows)],
            )
        }

        ancestry = runner._compute_global_ancestry(chrom_results)
        assert "MID" in ancestry
        assert ancestry["MID"]["fraction"] < 0.15
        assert "warning" in ancestry["MID"]
        assert "lower precision" in ancestry["MID"]["warning"]

    def test_mid_warning_absent_above_15pct(self) -> None:
        """T-MID-03: MID warning does NOT trigger when MID proportion > 15%."""
        from backend.analysis.gnomix_inference import CANONICAL_POPULATIONS, ChromosomeResult
        from backend.analysis.lai_runner import LAIRunner

        runner = LAIRunner.__new__(LAIRunner)
        n_windows = 100

        mid_idx = CANONICAL_POPULATIONS.index("MID")
        # All windows assigned to MID on both haplotypes → 100%
        hap = np.full(n_windows, mid_idx, dtype=np.int32)

        probs = np.zeros((n_windows, 7), dtype=np.float64)
        probs[:, mid_idx] = 0.90

        chrom_results = {
            1: ChromosomeResult(
                chrom=1,
                n_windows=n_windows,
                hap0_ancestry=hap,
                hap1_ancestry=hap,
                hap0_probs=probs,
                hap1_probs=probs,
                window_positions=[(i * 1000, (i + 1) * 1000) for i in range(n_windows)],
            )
        }

        ancestry = runner._compute_global_ancestry(chrom_results)
        assert "MID" in ancestry
        assert ancestry["MID"]["fraction"] > 0.15
        assert "warning" not in ancestry["MID"]

    def test_per_population_confidence_computed(self) -> None:
        """Per-population confidence should be present in LAI results."""
        from backend.analysis.gnomix_inference import CANONICAL_POPULATIONS, ChromosomeResult
        from backend.analysis.lai_runner import LAIRunner

        runner = LAIRunner.__new__(LAIRunner)
        n_windows = 10

        eur_idx = CANONICAL_POPULATIONS.index("EUR")
        hap = np.full(n_windows, eur_idx, dtype=np.int32)
        probs = np.zeros((n_windows, 7), dtype=np.float64)
        probs[:, eur_idx] = 0.85

        chrom_results = {
            1: ChromosomeResult(
                chrom=1,
                n_windows=n_windows,
                hap0_ancestry=hap,
                hap1_ancestry=hap,
                hap0_probs=probs,
                hap1_probs=probs,
                window_positions=[(i * 1000, (i + 1) * 1000) for i in range(n_windows)],
            )
        }

        ancestry = runner._compute_global_ancestry(chrom_results)
        # EUR should have high confidence (mean softmax prob ~0.85)
        assert "confidence" in ancestry["EUR"]
        assert ancestry["EUR"]["confidence"] > 0.8
        # Populations with 0 windows should have 0 confidence
        assert ancestry["AFR"]["confidence"] == 0.0


# ── Ancestry-bucket coverage helper (issue #339) ─────────────────────────


class TestAncestryCoverage:
    """Shared CSA≡SAS canonicalization + coverage helper (centralized in #339)."""

    def test_canonical_folds_csa_to_sas(self) -> None:
        assert canonical_ancestry("CSA") == "SAS"
        assert canonical_ancestry("csa") == "SAS"  # case-insensitive

    def test_canonical_passes_through_other_codes(self) -> None:
        for code in ("EUR", "AFR", "EAS", "AMR", "MID", "OCE", "SAS"):
            assert canonical_ancestry(code) == code
            assert canonical_ancestry(code.lower()) == code  # uppercased

    def test_covered_direct_membership(self) -> None:
        assert ancestry_covered("EUR", ["AFR", "EUR", "EAS"]) is True

    def test_covered_via_csa_sas_alias(self) -> None:
        # A CSA sample is covered by a SAS-labelled development set.
        assert ancestry_covered("CSA", ["AFR", "AMR", "EAS", "EUR", "SAS"]) is True

    def test_uncovered(self) -> None:
        assert ancestry_covered("MID", ["AFR", "EUR", "SAS"]) is False
        assert ancestry_covered("AFR", ["EUR", "EAS"]) is False

    def test_none_or_empty_inferred_is_uncovered(self) -> None:
        assert ancestry_covered(None, ["EUR", "SAS"]) is False
        assert ancestry_covered("", ["EUR", "SAS"]) is False

    def test_case_insensitive_on_both_sides(self) -> None:
        assert ancestry_covered("csa", ["afr", "sas"]) is True
        assert ancestry_covered("Eur", ["eur"]) is True

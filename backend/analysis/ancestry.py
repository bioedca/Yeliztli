"""Ancestry inference via PCA projection, admixture estimation, and haplogroup assignment.

Implements P3-23 (PCA projection), P3-24 (admixture fractions),
P3-25 (PCA coordinates for visualization), and P3-32 (haplogroup
assignment engine).

Projects user genotypes onto pre-computed PCA space via NumPy dot product
against loadings from a 5,000-AIM NPZ bundle. Runtime target: < 1 second.

Admixture fractions are estimated via NNLS (primary) and kNN (secondary)
against reference population centroids / samples. Fractions sum to ~1.0.

PCA coordinates for visualization (P3-25) combine the user's projected
coordinates with reference panel sample coordinates for scatter plot
rendering. Reference samples are pre-computed and stored in the bundle.

The ancestry PCA bundle (NPZ format) contains:
  - 5,000 ancestry informative markers (AIMs) with rsID lookup
  - Pre-computed PCA loadings (eigenvectors) from a 3,419-sample reference panel
  - Per-AIM means and standard deviations for standardization
  - Reference population centroids in PCA space (7 populations × 8 PCs)
  - Reference sample PCA coordinates by population (for visualization)
  - Tracy-Widom p-values and eigenvalues (pre-computed, no runtime TW)

Algorithm:
  1. Load ancestry PCA bundle (NPZ with AIMs, loadings, centroids, means/stds)
  2. Query sample genotypes for bundle SNPs (matched via rsID)
  3. Encode genotypes as alt-allele dosage (0, 1, or 2)
  4. Standardize: (dosage_i - mean_i) / std_i
  5. Project: pc_scores = standardized @ loadings
  6. Estimate admixture via NNLS against population centroids
  7. Estimate admixture via kNN against reference panel samples
  8. Classify: population with highest NNLS fraction → top population
  9. Compute confidence: cosine similarity between NNLS and kNN vectors

The ``top_population`` output is consumed by the PRS ancestry mismatch
check (P3-16) via ``prs.get_inferred_ancestry()``.

The ``get_ancestry_matched_af_column()`` utility (P3-26) maps inferred
ancestry to the corresponding gnomAD per-population AF column name,
enabling variant endpoints to display ancestry-matched allele frequencies.

Usage::

    from backend.analysis.ancestry import (
        load_ancestry_bundle,
        infer_ancestry,
        estimate_admixture_nnls,
        estimate_admixture_knn,
        compute_confidence,
        classify_ancestry,
        compute_missing_aim_rate,
        compute_admixture_fractions,
        store_ancestry_findings,
        get_pca_coordinates,
        get_ancestry_matched_af_column,
        get_inferred_ancestry,
        AncestryBundle,
        AncestryResult,
        PCACoordinates,
    )

    bundle = load_ancestry_bundle()
    result = infer_ancestry(bundle, sample_engine)
    # result.admixture_fractions contains per-population proportions
    store_ancestry_findings(result, sample_engine)
    # PCA coordinates for scatter plot visualization (P3-25)
    pca_coords = get_pca_coordinates(bundle, result)
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import sqlalchemy as sa
import structlog
from scipy.optimize import nnls as _scipy_nnls

from backend.analysis.evidence import ANCESTRY_EVIDENCE_LEVEL
from backend.analysis.zygosity import is_no_call
from backend.db.tables import (
    annotated_variants,
    findings,
    haplogroup_assignments,
    raw_variants,
)
from backend.services.sex_inference import infer_biological_sex

logger = structlog.get_logger(__name__)

# Path to the pre-computed ancestry PCA bundle (NPZ format, 5,000 AIMs)
_BUNDLE_PATH = (
    Path(__file__).resolve().parent.parent / "data" / "panels" / "ancestry_pca_bundle.npz"
)

# Path to the haplogroup defining SNP bundle (PhyloTree + ISOGG)
_HAPLOGROUP_BUNDLE_PATH = (
    Path(__file__).resolve().parent.parent / "data" / "panels" / "haplogroup_bundle.json"
)

# Super-population codes used throughout the module (canonical order, 7 populations)
POPULATIONS = ("AFR", "AMR", "CSA", "EAS", "EUR", "MID", "OCE")

# Ancestry-bucket aliases for cross-source coverage comparison. This app infers
# Central/South Asian as ``CSA`` (see POPULATIONS above), whereas external score
# catalogs label the same South Asian development ancestry ``SAS`` (PGS Catalog /
# 1000G). Fold both labels to one canonical bucket so a ``CSA`` sample is
# recognised as covered by a score's South Asian component. Shared here (a
# low-level module both pgs_bridge and prs import without a cycle) so the
# coverage rule cannot drift between them (issue #339; the split caused the #239
# review regression).
_ANCESTRY_ALIASES = {
    "CSA": "SAS",  # Central/South Asian (app) ≡ South Asian (PGS Catalog / 1000G)
}


def canonical_ancestry(code: str) -> str:
    """Fold an ancestry code to its canonical bucket for coverage comparison."""
    upper = code.upper()
    return _ANCESTRY_ALIASES.get(upper, upper)


def ancestry_covered(inferred: str | None, development_ancestries: Iterable[str]) -> bool:
    """Whether a score's development ancestries cover the inferred ancestry.

    Canonicalises both sides (applying the app↔catalog alias, e.g. CSA≡SAS) so a
    ``CSA`` sample is treated as covered by a ``SAS``-labelled development set.
    The single source of truth for "does this score cover this ancestry", shared
    by ``pgs_bridge`` (score selection / source-label resolution) and
    ``prs.check_ancestry_mismatch`` (warning decision) so the two cannot diverge.
    """
    if not inferred:
        return False
    target = canonical_ancestry(inferred)
    return target in {canonical_ancestry(a) for a in development_ancestries}


# Human-readable labels for each population
POPULATION_LABELS: dict[str, str] = {
    "AFR": "African",
    "AMR": "Admixed American",
    "CSA": "Central/South Asian",
    "EAS": "East Asian",
    "EUR": "European",
    "MID": "Middle Eastern",
    "OCE": "Oceanian",
}


# ── Data classes ──────────────────────────────────────────────────────────


@dataclass
class AncestryAIM:
    """A single ancestry informative marker from the PCA bundle."""

    rsid: str
    chrom: str
    pos: int
    ref: str
    alt: str
    ref_freq: float


@dataclass
class AncestryBundle:
    """Pre-computed PCA bundle for ancestry inference (NPZ format).

    Loaded from a NumPy NPZ archive containing 5,000 AIMs, 8 PCs,
    and 7 reference populations from a 3,419-sample panel.

    Attributes:
        version: Bundle version string.
        build: Genome build (e.g. "GRCh38").
        n_components: Number of principal components.
        populations: List of super-population codes.
        population_labels: Human-readable population names.
        snps: List of ancestry informative markers.
        loadings: PCA loadings matrix, shape (n_snps, n_components).
        means: Per-AIM mean dosage from reference panel, shape (n_snps,).
        stds: Per-AIM standard deviation from reference panel, shape (n_snps,).
        reference_centroids: Population centroids in PCA space,
            mapping population code → array of PC coordinates.
        reference_samples: Pre-computed PCA coordinates for reference
            panel samples, mapping population code → list of coordinate
            arrays. Used for PCA scatter plot visualization (P3-25).
        eigenvalues: PCA eigenvalues, shape (n_components,).
        n_significant_pcs: Number of statistically significant PCs.
        tw_pvalues: Tracy-Widom p-values, shape (20,).
        n_total_snps: Total SNPs in reference panel before AIM selection.
        n_selected_aims: Number of AIMs selected for the bundle.
    """

    version: str
    build: str
    n_components: int
    populations: list[str]
    population_labels: dict[str, str]
    snps: list[AncestryAIM]
    loadings: np.ndarray  # shape: (n_snps, n_components)
    means: np.ndarray  # shape: (n_snps,)
    stds: np.ndarray  # shape: (n_snps,)
    reference_centroids: dict[str, np.ndarray]  # pop → (n_components,)
    reference_samples: dict[str, list[list[float]]]  # pop → list of PC coords
    eigenvalues: np.ndarray  # shape: (n_components,)
    n_significant_pcs: int
    tw_pvalues: np.ndarray  # shape: (20,)
    n_total_snps: int
    n_selected_aims: int

    @property
    def snp_count(self) -> int:
        """Number of SNPs in the bundle."""
        return len(self.snps)

    def rsid_set(self) -> set[str]:
        """Return the set of rsids in the bundle."""
        return {s.rsid for s in self.snps}

    def rsid_to_index(self) -> dict[str, int]:
        """Map rsid → index in the SNP/loadings arrays."""
        return {s.rsid: i for i, s in enumerate(self.snps)}


@dataclass
class AncestryResult:
    """Result of ancestry PCA projection for a sample.

    Attributes:
        pc_scores: Projected PC coordinates, shape (n_components,).
        top_population: Nearest super-population by centroid distance.
        population_distances: Squared Euclidean distance to each centroid.
        admixture_fractions: Estimated ancestry proportions per population,
            computed via inverse-distance weighting. Values sum to ~1.0.
        snps_used: Number of SNPs with available genotype data.
        snps_total: Total SNPs in the bundle.
        coverage_fraction: snps_used / snps_total.
        projection_time_ms: Wall-clock time for the projection step.
        is_sufficient: Whether enough SNPs were genotyped.
    """

    pc_scores: list[float]
    top_population: str
    population_distances: dict[str, float]
    admixture_fractions: dict[str, float]
    snps_used: int
    snps_total: int
    coverage_fraction: float
    projection_time_ms: float
    is_sufficient: bool
    admixture_method: str = "nnls"
    confidence: float = 0.0
    missing_aim_rate: float = 0.0
    n_pcs_used: int = 0
    nnls_fractions: dict[str, float] | None = None
    knn_fractions: dict[str, float] | None = None
    nnls_ci_low: dict[str, float] | None = None
    nnls_ci_high: dict[str, float] | None = None
    # Honesty fields (PCA fix): "confident" → top_population is one of POPULATIONS;
    # "admixed" → top_population == "ADMIXED" (between clusters); "uncertain" →
    # top_population == "UNCERTAIN" (too little data to call). quality_flags carries
    # machine-readable reasons (e.g. "low_coverage", "low_centroid_margin").
    classification_status: str = "confident"
    quality_flags: list[str] = field(default_factory=list)

    @property
    def n_components(self) -> int:
        """Number of principal components."""
        return len(self.pc_scores)


@dataclass
class PCACoordinates:
    """PCA coordinates for scatter plot visualization (P3-25).

    Combines the user's projected coordinates with reference panel
    sample coordinates for rendering a PCA scatter plot.

    Attributes:
        user: User's projected PC coordinates.
        reference_samples: Reference panel samples by population,
            mapping population code → list of coordinate arrays.
        centroids: Population centroids in PCA space.
        population_labels: Human-readable population names.
        n_components: Number of principal components.
        pc_labels: Labels for each PC axis (e.g. ["PC1", "PC2", ...]).
    """

    user: list[float]
    reference_samples: dict[str, list[list[float]]]
    centroids: dict[str, list[float]]
    population_labels: dict[str, str]
    n_components: int
    pc_labels: list[str]


# ── Bundle loading ────────────────────────────────────────────────────────


def load_ancestry_bundle(bundle_path: Path | None = None) -> AncestryBundle:
    """Load the pre-computed ancestry PCA bundle from NPZ.

    Args:
        bundle_path: Optional override for the bundle NPZ path.
            Defaults to ``backend/data/panels/ancestry_pca_bundle.npz``.

    Returns:
        Parsed AncestryBundle with SNPs, loadings, centroids, and
        reference panel data for 5,000 AIMs and 7 populations.

    Raises:
        FileNotFoundError: If the bundle file does not exist.
        ValueError: If the bundle structure is invalid.
    """
    path = bundle_path or _BUNDLE_PATH
    if not path.exists():
        raise FileNotFoundError(f"Ancestry PCA bundle not found: {path}")

    logger.info("loading_ancestry_bundle", path=str(path))

    data = np.load(path, allow_pickle=False)

    # Extract scalar values
    n_significant_pcs = int(data["n_significant_pcs"])
    n_total_snps = int(data["n_total_snps"])
    n_selected_aims = int(data["n_selected_aims"])

    # Extract arrays
    loadings = data["loadings"].astype(np.float64)  # (n_snps, n_components)
    means = data["means"].astype(np.float64)  # (n_snps,)
    stds = data["stds"].astype(np.float64)  # (n_snps,)
    eigenvalues = data["eigenvalues"].astype(np.float64)  # (n_components,)
    tw_pvalues = data["tw_pvalues"].astype(np.float64)  # (20,)

    populations = list(data["populations"])
    n_components = loadings.shape[1]
    n_snps = loadings.shape[0]

    # Build AIM list from NPZ arrays, using aim_rsids_23andme for rsID matching
    aim_rsids_23andme = data["aim_rsids_23andme"]
    aim_chroms = data["aim_chroms"]
    aim_positions = data["aim_positions_grch38"]
    aim_a1 = data["aim_a1"]  # alt allele
    aim_a2 = data["aim_a2"]  # ref allele

    snps: list[AncestryAIM] = []
    for i in range(n_snps):
        snps.append(
            AncestryAIM(
                rsid=str(aim_rsids_23andme[i]),
                chrom=str(aim_chroms[i]),
                pos=int(aim_positions[i]),
                ref=str(aim_a2[i]),
                alt=str(aim_a1[i]),
                ref_freq=1.0 - float(means[i]) / 2.0,
            )
        )

    # Validate shapes
    if loadings.shape != (n_snps, n_components):
        raise ValueError(
            f"Loadings shape {loadings.shape} does not match ({n_snps}, {n_components})"
        )
    if means.shape[0] != n_snps:
        raise ValueError(f"Means has {means.shape[0]} entries, expected {n_snps}")

    # Build reference centroids dict from (n_pops, n_components) matrix
    centroid_matrix = data["population_centroids"].astype(np.float64)
    centroids: dict[str, np.ndarray] = {}
    for i, pop in enumerate(populations):
        centroids[pop] = centroid_matrix[i]

    # Build reference samples dict from ref_pca_coords + ref_labels
    ref_coords = data["ref_pca_coords"].astype(np.float64)  # (3419, n_components)
    ref_labels = data["ref_labels"]  # (3419,)
    ref_samples: dict[str, list[list[float]]] = {pop: [] for pop in populations}
    for i in range(len(ref_labels)):
        pop = str(ref_labels[i])
        if pop in ref_samples:
            ref_samples[pop].append([float(v) for v in ref_coords[i]])

    bundle = AncestryBundle(
        version="2.0.0",
        build="GRCh38",
        n_components=n_components,
        populations=populations,
        population_labels=POPULATION_LABELS,
        snps=snps,
        loadings=loadings,
        means=means,
        stds=stds,
        reference_centroids=centroids,
        reference_samples=ref_samples,
        eigenvalues=eigenvalues,
        n_significant_pcs=n_significant_pcs,
        tw_pvalues=tw_pvalues,
        n_total_snps=n_total_snps,
        n_selected_aims=n_selected_aims,
    )

    logger.info(
        "ancestry_bundle_loaded",
        snp_count=bundle.snp_count,
        n_components=bundle.n_components,
        populations=bundle.populations,
        n_significant_pcs=bundle.n_significant_pcs,
    )

    return bundle


# ── Genotype encoding ─────────────────────────────────────────────────────


def _encode_dosage(genotype: str | None, alt_allele: str) -> float | None:
    """Encode a genotype as alt-allele dosage (0, 1, or 2).

    Args:
        genotype: Two-character genotype string (e.g. "AG"), or None.
        alt_allele: The alternate allele to count.

    Returns:
        Dosage (0.0, 1.0, or 2.0), or None if genotype is missing.
    """
    if not genotype or len(genotype) < 2:
        return None

    if genotype in ("--", "00", "II", "DD", "DI", "ID"):
        return None

    count = 0
    for allele in genotype:
        if allele.upper() == alt_allele.upper():
            count += 1

    return float(min(count, 2))


# ── PCA projection ────────────────────────────────────────────────────────

# Minimum fraction of AIMs that must be genotyped before a confident call. Real
# 23andMe v5 covers ~98% of these AIMs; correct, stable calls in the validation
# set (real PGP-HMS genomes) all sit ≥0.97 coverage, while every misclassification
# clustered at 0.30-0.53. Below this floor, missing AIMs are mean-imputed to the
# panel mean, which drags the projection toward the PCA origin — and the CSA
# centroid happens to sit nearest the origin, so low-coverage samples were being
# *confidently* mislabelled "Central/South Asian" (Privé 2022; the projection
# shrinks toward the null with missing data). We refuse a single-population call
# below this and report "UNCERTAIN" instead.
_MIN_COVERAGE = 0.55

# A confident single-population call requires the sample to sit decisively closest
# to ONE centroid: the 2nd-nearest centroid must be at least this many times
# farther (squared-distance ratio) than the nearest. Below this the sample lies
# between clusters — admixed or poorly-fit — and we report "ADMIXED" rather than a
# misleading single population. Calibrated on real PGP genomes: confident
# continental calls had ratios ≥13 (EUR/AFR) and a genuine admixed African/European
# individual sat at 2.79, while a true (genuinely-distant) Admixed-American sat at
# 3.49; 3.0 cleanly separates them and errs toward the honest "admixed" verdict.
_CONFIDENT_MARGIN_RATIO = 3.0


def _project_onto_pca(
    bundle: AncestryBundle,
    genotype_map: dict[str, str | None],
) -> tuple[np.ndarray, int]:
    """Project sample genotypes onto PCA space.

    Encodes genotypes as alt-allele dosage, standardizes using
    per-AIM means and standard deviations from the reference panel,
    imputes missing values with 0 (mean), and projects via dot
    product with the loadings matrix.

    Args:
        bundle: Loaded ancestry PCA bundle.
        genotype_map: Mapping rsid → genotype string.

    Returns:
        Tuple of (pc_scores array shape (n_components,), snps_used count).
    """
    n_snps = bundle.snp_count

    # Build standardized dosage vector
    standardized = np.zeros(n_snps, dtype=np.float64)
    snps_used = 0

    for i, snp in enumerate(bundle.snps):
        genotype = genotype_map.get(snp.rsid)
        dosage = _encode_dosage(genotype, snp.alt)

        if dosage is not None:
            # Standardize: (dosage - mean) / std
            std = bundle.stds[i]
            if std > 0:
                standardized[i] = (dosage - bundle.means[i]) / std
            else:
                standardized[i] = 0.0
            snps_used += 1
        # else: leave as 0.0 (mean-imputed after standardization)

    # Project: pc_scores = standardized @ loadings
    # loadings shape: (n_snps, n_components)
    # standardized shape: (n_snps,)
    # result shape: (n_components,)
    pc_scores = standardized @ bundle.loadings

    return pc_scores, snps_used


def _classify_nearest_centroid(
    pc_scores: np.ndarray,
    centroids: dict[str, np.ndarray],
) -> tuple[str, dict[str, float]]:
    """Classify ancestry by nearest centroid in PCA space.

    Uses squared Euclidean distance to find the closest reference
    population centroid.

    Args:
        pc_scores: Sample PC coordinates, shape (n_components,).
        centroids: Population → centroid coordinates.

    Returns:
        Tuple of (top_population code, distances dict).
    """
    if not centroids:
        raise ValueError("No population centroids provided for classification")

    distances: dict[str, float] = {}
    best_pop = ""
    best_dist = float("inf")

    for pop, centroid in centroids.items():
        dist = float(np.sum((pc_scores - centroid) ** 2))
        distances[pop] = round(dist, 4)
        if dist < best_dist:
            best_dist = dist
            best_pop = pop

    return best_pop, distances


# Honest non-call outcomes (never one of the 7 super-population codes).
UNCERTAIN = "UNCERTAIN"
ADMIXED = "ADMIXED"


def _classify_with_confidence(
    coverage: float,
    distances: dict[str, float],
) -> tuple[str, str, list[str]]:
    """Assign a population only when it is both data-sufficient AND unambiguous.

    The honesty core of the PCA fix. Returns ``(top_population, status,
    quality_flags)`` where ``top_population`` is a super-population code, or
    :data:`UNCERTAIN` (too little data) / :data:`ADMIXED` (between clusters):

    1. coverage below :data:`_MIN_COVERAGE` → ``UNCERTAIN`` — a sparse projection
       collapses toward the PCA origin (≈ the CSA centroid) and must not yield a
       confident label.
    2. otherwise take the **nearest centroid** (not the degenerate NNLS argmax),
       and require the 2nd-nearest to be ≥ :data:`_CONFIDENT_MARGIN_RATIO`× farther
       (squared distance). If not, the sample sits between clusters → ``ADMIXED``.
    3. else → the nearest-centroid population, ``confident``.
    """
    flags: list[str] = []
    if coverage < _MIN_COVERAGE:
        flags.append("low_coverage")
        return UNCERTAIN, "uncertain", flags

    ranked = sorted(distances.items(), key=lambda kv: kv[1])
    nearest_pop, nearest_dist = ranked[0]
    second_dist = ranked[1][1] if len(ranked) > 1 else float("inf")
    margin_ratio = (second_dist / nearest_dist) if nearest_dist > 0 else float("inf")

    if margin_ratio < _CONFIDENT_MARGIN_RATIO:
        flags.append("low_centroid_margin")
        return ADMIXED, "admixed", flags

    return nearest_pop, "confident", flags


def estimate_admixture_nnls(
    user_pcs: np.ndarray,
    bundle: AncestryBundle,
) -> dict[str, float]:
    """Estimate admixture fractions via non-negative least squares.

    Solves min ||C @ x - user_pcs|| subject to x >= 0, where C is the
    matrix of population centroids. The solution is normalized to sum to 1.0.

    Args:
        user_pcs: User's projected PC coordinates, shape (n_components,).
        bundle: Loaded ancestry PCA bundle (contains centroids).

    Returns:
        Dict mapping population code → fraction (0.0–1.0), summing to 1.0.
    """
    pops = list(bundle.reference_centroids.keys())
    # Build centroid matrix: (n_components, n_pops)
    centroid_matrix = np.column_stack([bundle.reference_centroids[p] for p in pops])

    # NNLS: find x >= 0 such that ||centroid_matrix @ x - user_pcs|| is minimized
    x, _ = _scipy_nnls(centroid_matrix, user_pcs)

    # Normalize to sum to 1.0
    total = x.sum()
    if total > 0:
        x = x / total
    else:
        # Fallback: uniform distribution
        x = np.ones(len(pops)) / len(pops)

    fractions = {pop: round(float(x[i]), 4) for i, pop in enumerate(pops)}

    # Ensure exact sum to 1.0
    frac_sum = sum(fractions.values())
    if abs(frac_sum - 1.0) > 1e-8:
        max_pop = max(fractions, key=lambda p: fractions[p])
        fractions[max_pop] = round(fractions[max_pop] + (1.0 - frac_sum), 4)

    return fractions


def bootstrap_admixture_nnls(
    user_pcs: np.ndarray,
    bundle: AncestryBundle,
    genotype_map: dict[str, str | None],
    n_iterations: int = 100,
    ci: float = 0.95,
    rng_seed: int | None = None,
) -> tuple[dict[str, float], dict[str, float]]:
    """Bootstrap confidence intervals for NNLS admixture fractions.

    Resamples AIM subsets (with replacement), re-projects onto PCA space,
    and re-runs NNLS for each iteration.  Returns the lower and upper
    bounds of the CI per population.

    Args:
        user_pcs: User's projected PC coordinates (unused directly — kept
            for API symmetry; we re-project from genotypes each iteration).
        bundle: Loaded ancestry PCA bundle.
        genotype_map: rsid → genotype string mapping for the sample.
        n_iterations: Number of bootstrap iterations.
        ci: Confidence interval width (0–1), default 0.95.
        rng_seed: Optional seed for reproducibility.

    Returns:
        Tuple of (ci_low dict, ci_high dict) mapping population → bound.
    """
    rng = np.random.default_rng(rng_seed)
    pops = list(bundle.reference_centroids.keys())
    n_snps = bundle.snp_count
    alpha = (1.0 - ci) / 2.0

    # Pre-encode dosages for all AIMs
    dosages = np.full(n_snps, np.nan, dtype=np.float64)
    for i, snp in enumerate(bundle.snps):
        genotype = genotype_map.get(snp.rsid)
        d = _encode_dosage(genotype, snp.alt)
        if d is not None:
            dosages[i] = d

    # Centroid matrix for NNLS
    centroid_matrix = np.column_stack([bundle.reference_centroids[p] for p in pops])

    # Collect fractions from each bootstrap iteration
    all_fracs = np.zeros((n_iterations, len(pops)), dtype=np.float64)

    for it in range(n_iterations):
        # Resample AIM indices with replacement
        idx = rng.integers(0, n_snps, size=n_snps)

        # Build standardized dosage vector for resampled AIMs
        standardized = np.zeros(n_snps, dtype=np.float64)
        for j, orig_i in enumerate(idx):
            d = dosages[orig_i]
            if not np.isnan(d):
                std = bundle.stds[orig_i]
                if std > 0:
                    standardized[j] = (d - bundle.means[orig_i]) / std

        # Re-project using resampled loadings
        resampled_loadings = bundle.loadings[idx, :]
        pc_scores = standardized @ resampled_loadings

        # NNLS
        x, _ = _scipy_nnls(centroid_matrix, pc_scores)
        total = x.sum()
        if total > 0:
            x = x / total
        else:
            x = np.ones(len(pops)) / len(pops)

        all_fracs[it] = x

    # Compute percentile-based confidence intervals
    ci_low_vals = np.percentile(all_fracs, alpha * 100, axis=0)
    ci_high_vals = np.percentile(all_fracs, (1.0 - alpha) * 100, axis=0)

    ci_low = {pop: round(float(ci_low_vals[i]), 4) for i, pop in enumerate(pops)}
    ci_high = {pop: round(float(ci_high_vals[i]), 4) for i, pop in enumerate(pops)}

    return ci_low, ci_high


def estimate_admixture_knn(
    user_pcs: np.ndarray,
    bundle: AncestryBundle,
    k: int = 15,
) -> dict[str, float]:
    """Estimate admixture fractions via k-nearest neighbors.

    Finds the k nearest reference panel samples in PCA space and
    returns the proportion of each population among those neighbors.

    Args:
        user_pcs: User's projected PC coordinates, shape (n_components,).
        bundle: Loaded ancestry PCA bundle (contains reference samples).
        k: Number of nearest neighbors to use.

    Returns:
        Dict mapping population code → fraction (0.0–1.0), summing to 1.0.
    """
    # Build reference coordinate and label arrays
    all_coords: list[np.ndarray] = []
    all_labels: list[str] = []

    for pop, samples in bundle.reference_samples.items():
        for sample in samples:
            all_coords.append(np.array(sample, dtype=np.float64))
            all_labels.append(pop)

    if not all_coords:
        return {pop: round(1.0 / len(bundle.populations), 4) for pop in bundle.populations}

    ref_matrix = np.array(all_coords)  # (n_ref, n_components)
    # Euclidean distances
    diffs = ref_matrix - user_pcs[np.newaxis, :]
    distances = np.sqrt(np.sum(diffs**2, axis=1))

    # Find k nearest
    actual_k = min(k, len(distances))
    if actual_k == len(distances):
        nearest_idx = np.arange(actual_k)
    else:
        nearest_idx = np.argpartition(distances, actual_k)[:actual_k]

    # Count populations among nearest neighbors
    counts: dict[str, int] = {pop: 0 for pop in bundle.reference_centroids}
    for idx in nearest_idx:
        label = all_labels[idx]
        if label in counts:
            counts[label] += 1

    # Convert to fractions
    fractions = {pop: round(c / actual_k, 4) for pop, c in counts.items()}

    # Ensure exact sum to 1.0
    frac_sum = sum(fractions.values())
    if abs(frac_sum - 1.0) > 1e-8:
        max_pop = max(fractions, key=lambda p: fractions[p])
        fractions[max_pop] = round(fractions[max_pop] + (1.0 - frac_sum), 4)

    return fractions


def compute_confidence(
    nnls_fracs: dict[str, float],
    knn_fracs: dict[str, float],
) -> float:
    """Compute confidence as cosine similarity between NNLS and kNN vectors.

    A high similarity (close to 1.0) means the two methods agree,
    indicating high confidence in the ancestry estimate.

    Args:
        nnls_fracs: NNLS admixture fractions.
        knn_fracs: kNN admixture fractions.

    Returns:
        Cosine similarity between the two proportion vectors (0.0–1.0).
    """
    pops = sorted(set(nnls_fracs.keys()) | set(knn_fracs.keys()))
    if not pops:
        return 0.0

    a = np.array([nnls_fracs.get(p, 0.0) for p in pops])
    b = np.array([knn_fracs.get(p, 0.0) for p in pops])

    norm_a = np.linalg.norm(a)
    norm_b = np.linalg.norm(b)

    if norm_a < 1e-10 or norm_b < 1e-10:
        return 0.0

    return float(np.clip(np.dot(a, b) / (norm_a * norm_b), 0.0, 1.0))


def classify_ancestry(
    user_pcs: np.ndarray,
    bundle: AncestryBundle,
) -> str:
    """Return the population with the highest NNLS fraction.

    Args:
        user_pcs: User's projected PC coordinates.
        bundle: Loaded ancestry PCA bundle.

    Returns:
        Population code string (e.g. "EUR").
    """
    fracs = estimate_admixture_nnls(user_pcs, bundle)
    return max(fracs, key=lambda p: fracs[p])


def compute_missing_aim_rate(
    genotype_map: dict[str, str | None],
    bundle: AncestryBundle,
) -> float:
    """Compute the fraction of bundle AIMs missing from user data.

    Args:
        genotype_map: Mapping rsid → genotype string.
        bundle: Loaded ancestry PCA bundle.

    Returns:
        Fraction of missing AIMs (0.0 = all present, 1.0 = all missing).
    """
    if bundle.snp_count == 0:
        return 0.0

    missing = 0
    for snp in bundle.snps:
        gt = genotype_map.get(snp.rsid)
        if gt is None or gt in ("--", "00", "II", "DD", "DI", "ID", ""):
            missing += 1

    return missing / bundle.snp_count


def compute_admixture_fractions(
    population_distances: dict[str, float],
) -> dict[str, float]:
    """Estimate admixture fractions via inverse-distance weighting.

    .. deprecated::
        Kept for backward compatibility. Prefer ``estimate_admixture_nnls``
        for new code.

    Args:
        population_distances: Squared Euclidean distance to each
            population centroid (from _classify_nearest_centroid).

    Returns:
        Dict mapping population code → fraction (0.0–1.0).
        Empty dict if no distances provided.
    """
    if not population_distances:
        return {}

    epsilon = 1e-10

    # Check if sample is essentially on a centroid (distance ~ 0)
    min_dist = min(population_distances.values())
    if min_dist < epsilon:
        # Distribute evenly among zero-distance populations
        zero_pops = [p for p, d in population_distances.items() if d < epsilon]
        share = round(1.0 / len(zero_pops), 4)
        fractions = {}
        for pop, dist in population_distances.items():
            fractions[pop] = share if dist < epsilon else 0.0
        return fractions

    # Inverse-distance weighting: weight_i = 1 / d_i^2
    # Using squared distances directly (already squared Euclidean)
    inv_weights: dict[str, float] = {}
    total_weight = 0.0

    for pop, dist in population_distances.items():
        w = 1.0 / (dist + epsilon)
        inv_weights[pop] = w
        total_weight += w

    # Normalize to sum to 1.0
    fractions = {pop: round(w / total_weight, 4) for pop, w in inv_weights.items()}

    # Ensure exact sum to 1.0 by adjusting the largest fraction
    frac_sum = sum(fractions.values())
    if fractions and abs(frac_sum - 1.0) > 1e-8:
        max_pop = max(fractions, key=lambda p: fractions[p])
        fractions[max_pop] = round(fractions[max_pop] + (1.0 - frac_sum), 4)

    return fractions


# ── Main inference function ───────────────────────────────────────────────


def infer_ancestry(
    bundle: AncestryBundle,
    sample_engine: sa.Engine,
) -> AncestryResult:
    """Infer ancestry by PCA projection for a sample.

    Queries the sample database for genotypes at bundle SNP positions,
    projects onto PCA space, and classifies by nearest centroid.

    Tries annotated_variants first (post-annotation), falls back to
    raw_variants if annotated_variants is empty or doesn't exist.

    Args:
        bundle: Loaded ancestry PCA bundle.
        sample_engine: SQLAlchemy engine for the sample database.

    Returns:
        AncestryResult with PC scores and top population classification.
    """
    rsids = list(bundle.rsid_set())

    # Fetch genotypes — try annotated_variants first, fall back to raw_variants
    genotype_map: dict[str, str | None] = {}

    with sample_engine.connect() as conn:
        # Check if annotated_variants has data
        try:
            count = conn.execute(
                sa.select(sa.func.count()).select_from(annotated_variants)
            ).scalar()
        except sa.exc.OperationalError:
            logger.debug("annotated_variants_not_available", msg="Using raw_variants fallback")
            count = 0

        if count > 0:
            stmt = sa.select(
                annotated_variants.c.rsid,
                annotated_variants.c.genotype,
            ).where(annotated_variants.c.rsid.in_(rsids))
            rows = conn.execute(stmt).fetchall()
        else:
            # Fall back to raw_variants
            stmt = sa.select(
                raw_variants.c.rsid,
                raw_variants.c.genotype,
            ).where(raw_variants.c.rsid.in_(rsids))
            rows = conn.execute(stmt).fetchall()

    for row in rows:
        genotype_map[row.rsid] = row.genotype

    # Project onto PCA space
    t0 = time.perf_counter()
    pc_scores, snps_used = _project_onto_pca(bundle, genotype_map)
    projection_ms = (time.perf_counter() - t0) * 1000.0

    # Classify via nearest centroid (kept for distances)
    _, distances = _classify_nearest_centroid(pc_scores, bundle.reference_centroids)

    # NNLS admixture (primary method)
    nnls_fracs = estimate_admixture_nnls(pc_scores, bundle)

    # kNN admixture (secondary method)
    knn_fracs = estimate_admixture_knn(pc_scores, bundle)

    # Confidence: cosine similarity between NNLS and kNN
    confidence = compute_confidence(nnls_fracs, knn_fracs)

    # Bootstrap 95% CI for NNLS fractions
    ci_low, ci_high = bootstrap_admixture_nnls(pc_scores, bundle, genotype_map, n_iterations=100)

    # Missing AIM rate
    missing_rate = compute_missing_aim_rate(genotype_map, bundle)

    coverage = snps_used / bundle.snp_count if bundle.snp_count > 0 else 0.0

    # Honest top-population: nearest-centroid gated by coverage + margin (PCA fix).
    # NNLS argmax is retained only for the admixture *breakdown* (nnls_fractions) —
    # it is degenerate as a top-1 label (a clean sample can tie on a meaningless
    # population), and on its own it mislabelled low-coverage/admixed samples as CSA.
    top_pop, classification_status, quality_flags = _classify_with_confidence(coverage, distances)
    is_sufficient = coverage >= _MIN_COVERAGE

    result = AncestryResult(
        pc_scores=[round(float(s), 6) for s in pc_scores],
        top_population=top_pop,
        population_distances=distances,
        admixture_fractions=nnls_fracs,
        snps_used=snps_used,
        snps_total=bundle.snp_count,
        coverage_fraction=round(coverage, 4),
        projection_time_ms=round(projection_ms, 2),
        is_sufficient=is_sufficient,
        admixture_method="nnls",
        confidence=round(confidence, 4),
        missing_aim_rate=round(missing_rate, 4),
        n_pcs_used=bundle.n_components,
        nnls_fractions=nnls_fracs,
        knn_fractions=knn_fracs,
        nnls_ci_low=ci_low,
        nnls_ci_high=ci_high,
        classification_status=classification_status,
        quality_flags=quality_flags,
    )

    logger.info(
        "ancestry_inferred",
        top_population=result.top_population,
        classification_status=result.classification_status,
        quality_flags=result.quality_flags,
        snps_used=result.snps_used,
        snps_total=result.snps_total,
        coverage=result.coverage_fraction,
        projection_ms=result.projection_time_ms,
        is_sufficient=result.is_sufficient,
        admixture_method=result.admixture_method,
        confidence=result.confidence,
        missing_aim_rate=result.missing_aim_rate,
    )

    return result


# ── Findings storage ──────────────────────────────────────────────────────


def store_ancestry_findings(
    result: AncestryResult,
    sample_engine: sa.Engine,
) -> int:
    """Store ancestry inference findings in the sample database.

    Creates multiple findings with module='ancestry':
      - ``pca_projection``: PCA coordinates and centroid distances.
      - ``nnls_admixture``: NNLS admixture fractions (primary). Contains
        ``top_population`` in ``detail_json`` — read by
        ``get_inferred_ancestry()``.
      - ``knn_admixture``: kNN admixture fractions (secondary).

    Args:
        result: AncestryResult from infer_ancestry.
        sample_engine: SQLAlchemy engine for the sample database.

    Returns:
        Number of findings inserted (0 or 3).
    """
    if not result.is_sufficient:
        logger.warning(
            "ancestry_finding_skipped_insufficient",
            coverage=result.coverage_fraction,
            snps_used=result.snps_used,
        )
        return 0

    # Sort populations by distance (ascending) for display
    sorted_pops = sorted(result.population_distances.items(), key=lambda x: x[1])

    # Build admixture summary for finding text (top 3 contributions)
    sorted_admixture = sorted(result.admixture_fractions.items(), key=lambda x: x[1], reverse=True)
    admixture_parts = [f"{pop} {frac:.0%}" for pop, frac in sorted_admixture[:3] if frac >= 0.01]
    admixture_summary = ", ".join(admixture_parts) if admixture_parts else result.top_population
    # Honest headline: never assert a single population for an admixed/between-clusters
    # sample (the NNLS breakdown follows as supporting detail, not a verdict).
    if result.classification_status == "admixed":
        ancestry_label = (
            f"Admixed / low-confidence ancestry (no single population): {admixture_summary}"
        )
    else:
        ancestry_label = f"Inferred ancestry: {admixture_summary}"

    # Row 1: PCA projection
    pca_detail = {
        "top_population": result.top_population,
        "inferred_ancestry": result.top_population,
        "pc_scores": result.pc_scores,
        "population_distances": result.population_distances,
        "admixture_fractions": result.admixture_fractions,
        "population_ranking": [{"population": pop, "distance": dist} for pop, dist in sorted_pops],
        "snps_used": result.snps_used,
        "snps_total": result.snps_total,
        "coverage_fraction": result.coverage_fraction,
        "projection_time_ms": result.projection_time_ms,
        "is_sufficient": result.is_sufficient,
        "n_pcs_used": result.n_pcs_used,
        "missing_aim_rate": result.missing_aim_rate,
    }

    pca_row = {
        "module": "ancestry",
        "category": "pca_projection",
        "evidence_level": ANCESTRY_EVIDENCE_LEVEL,
        "finding_text": (
            f"PCA projection: {result.snps_used}/{result.snps_total} markers, "
            f"{result.coverage_fraction:.0%} coverage, {result.n_pcs_used} PCs"
        ),
        "detail_json": json.dumps(pca_detail),
    }

    # Row 2: NNLS admixture (primary — has top_population for get_inferred_ancestry)
    nnls_detail: dict = {
        "top_population": result.top_population,
        "inferred_ancestry": result.top_population,
        "classification_status": result.classification_status,
        "quality_flags": result.quality_flags,
        "admixture_fractions": result.nnls_fractions or result.admixture_fractions,
        "admixture_method": "nnls",
        "confidence": result.confidence,
        "missing_aim_rate": result.missing_aim_rate,
        "snps_used": result.snps_used,
        "snps_total": result.snps_total,
        "coverage_fraction": result.coverage_fraction,
    }
    if result.nnls_ci_low is not None:
        nnls_detail["ci_low"] = result.nnls_ci_low
    if result.nnls_ci_high is not None:
        nnls_detail["ci_high"] = result.nnls_ci_high

    nnls_row = {
        "module": "ancestry",
        "category": "nnls_admixture",
        "evidence_level": ANCESTRY_EVIDENCE_LEVEL,
        "finding_text": (
            f"{ancestry_label} "
            f"({result.snps_used}/{result.snps_total} markers, "
            f"{result.coverage_fraction:.0%} coverage)"
        ),
        "detail_json": json.dumps(nnls_detail),
    }

    # Row 3: kNN admixture (secondary)
    knn_detail = {
        "top_population": result.top_population,
        "admixture_fractions": result.knn_fractions or {},
        "admixture_method": "knn",
        "k": 15,
    }

    knn_row = {
        "module": "ancestry",
        "category": "knn_admixture",
        "evidence_level": ANCESTRY_EVIDENCE_LEVEL,
        "finding_text": (f"kNN admixture estimate (k=15): {result.top_population}"),
        "detail_json": json.dumps(knn_detail),
    }

    categories = ("pca_projection", "nnls_admixture", "knn_admixture")

    with sample_engine.begin() as conn:
        # Clear previous ancestry admixture findings
        conn.execute(
            sa.delete(findings).where(
                findings.c.module == "ancestry",
                findings.c.category.in_(categories),
            )
        )
        conn.execute(sa.insert(findings), [pca_row, nnls_row, knn_row])

    logger.info(
        "ancestry_findings_stored",
        top_population=result.top_population,
        admixture_method=result.admixture_method,
        confidence=result.confidence,
    )
    return 3


# ── PCA coordinates for visualization (P3-25) ────────────────────────────


def get_pca_coordinates(
    bundle: AncestryBundle,
    result: AncestryResult,
) -> PCACoordinates:
    """Get PCA coordinates for scatter plot visualization.

    Combines the user's projected PC coordinates with reference panel
    sample coordinates and centroids for rendering a PCA scatter plot.

    Args:
        bundle: Loaded ancestry PCA bundle (contains reference samples).
        result: AncestryResult from infer_ancestry (contains user PC scores).

    Returns:
        PCACoordinates with user + reference data for visualization.
    """
    centroids = {
        pop: [round(float(v), 4) for v in coords]
        for pop, coords in bundle.reference_centroids.items()
    }

    pc_labels = [f"PC{i + 1}" for i in range(bundle.n_components)]

    return PCACoordinates(
        user=result.pc_scores,
        reference_samples=bundle.reference_samples,
        centroids=centroids,
        population_labels=bundle.population_labels,
        n_components=bundle.n_components,
        pc_labels=pc_labels,
    )


# ── Ancestry-matched AF display (P3-26) ──────────────────────────────────

# Maps ancestry super-population codes to gnomAD per-population AF column
# names in the annotated_variants table. gnomAD stores Non-Finnish European
# as "nfe", which is mapped to "eur" in our schema (see gnomad.py).
# OCE (Oceanian) has no gnomAD-specific data, so falls back to global AF.
_ANCESTRY_TO_GNOMAD_COL: dict[str, str] = {
    "AFR": "gnomad_af_afr",
    "AMR": "gnomad_af_amr",
    "CSA": "gnomad_af_sas",  # gnomAD "sas" covers Central/South Asian
    "EAS": "gnomad_af_eas",
    "EUR": "gnomad_af_eur",
    "MID": "gnomad_af_global",  # gnomAD has no dedicated MID column
    "OCE": "gnomad_af_global",
}


def get_ancestry_matched_af_column(population: str | None) -> str:
    """Return the gnomAD AF column name matching the inferred ancestry.

    Maps super-population codes (AFR, AMR, EAS, EUR, SAS, OCE) to the
    corresponding ``gnomad_af_*`` column in annotated_variants.

    Args:
        population: Inferred super-population code, or None.

    Returns:
        Column name string (e.g. ``"gnomad_af_eur"``).
        Falls back to ``"gnomad_af_global"`` if population is unknown or None.
    """
    if population is None:
        return "gnomad_af_global"
    return _ANCESTRY_TO_GNOMAD_COL.get(population.upper(), "gnomad_af_global")


def _get_latest_ancestry_finding(
    sample_engine: sa.Engine,
) -> tuple[str | None, dict | None]:
    """Return ``(top_population, detail_dict)`` from the best ancestry finding.

    Preference order: ``local_ancestry`` → ``nnls_admixture`` →
    ``pca_projection`` → any ancestry finding.

    Returns ``(None, None)`` when no usable finding exists.
    """
    with sample_engine.connect() as conn:
        for category in ("local_ancestry", "nnls_admixture", "pca_projection", None):
            stmt = (
                sa.select(findings.c.detail_json)
                .where(findings.c.module == "ancestry")
                .order_by(findings.c.id.desc())
                .limit(1)
            )
            if category is not None:
                stmt = stmt.where(findings.c.category == category)
            row = conn.execute(stmt).fetchone()

            if row is not None and row.detail_json:
                try:
                    detail = json.loads(row.detail_json)
                    top_pop = detail.get("top_population") or detail.get("inferred_ancestry")
                    if top_pop:
                        return top_pop, detail
                except (ValueError, TypeError):
                    continue

    return None, None


def get_inferred_ancestry(sample_engine: sa.Engine) -> str | None:
    """Retrieve the inferred top ancestry from a sample's findings.

    Preference order: ``local_ancestry`` → ``nnls_admixture`` →
    ``pca_projection``.  Extracts ``top_population`` from ``detail_json``.

    This is the canonical way to get ancestry for P3-26 (ancestry-matched AF)
    and is also used by PRS ancestry mismatch checks (P3-16).

    Args:
        sample_engine: SQLAlchemy engine for the sample database.

    Returns:
        Inferred top ancestry code (e.g. "EUR", "EAS", "AFR") or None.
    """
    top_pop, _ = _get_latest_ancestry_finding(sample_engine)
    return top_pop


def get_top_ancestry_fraction(sample_engine: sa.Engine) -> float | None:
    """Retrieve the top ancestry fraction from a sample's findings.

    Searches the same categories as :func:`get_inferred_ancestry` in the same
    preference order (``local_ancestry`` → ``nnls_admixture`` →
    ``pca_projection``).  Returns the fraction for the top population from
    ``admixture_fractions`` in ``detail_json``.

    Args:
        sample_engine: SQLAlchemy engine for the sample database.

    Returns:
        Fraction (0.0–1.0) of the top ancestry, or None if unavailable.
    """
    top_pop, detail = _get_latest_ancestry_finding(sample_engine)
    if top_pop is None or detail is None:
        return None
    fracs = detail.get("admixture_fractions", {})
    if top_pop in fracs:
        try:
            return float(fracs[top_pop])
        except (ValueError, TypeError):
            return None
    # ADMIXED / UNCERTAIN are non-population sentinels (no confident single top
    # population), so top_pop is not a key in the population-coded
    # admixture_fractions. Fall back to the largest population fraction — the
    # dominant component — so the <70% admixed-composition warning in
    # check_ancestry_mismatch still fires instead of being silently suppressed by
    # a None fraction (#300).
    if top_pop in (ADMIXED, UNCERTAIN) and fracs:
        try:
            return max(float(v) for v in fracs.values())
        except (ValueError, TypeError):
            return None
    return None


# ── Convenience pipeline ──────────────────────────────────────────────────


def run_ancestry_inference(
    sample_engine: sa.Engine,
    bundle_path: Path | None = None,
) -> AncestryResult:
    """Run the full ancestry inference pipeline: load → infer → store.

    Args:
        sample_engine: SQLAlchemy engine for the sample database.
        bundle_path: Optional override for the bundle path.

    Returns:
        AncestryResult with PC scores and classification.
    """
    bundle = load_ancestry_bundle(bundle_path)
    result = infer_ancestry(bundle, sample_engine)
    store_ancestry_findings(result, sample_engine)
    return result


# ═══════════════════════════════════════════════════════════════════════
# Haplogroup Assignment Engine (P3-32)
# ═══════════════════════════════════════════════════════════════════════
#
# Pure Python tree-walk algorithm for mitochondrial and Y-chromosome
# haplogroup assignment using the PhyloTree + ISOGG defining SNP bundle.
#
# mtDNA assignment runs on all samples. Y-chromosome assignment runs
# only when sex is inferred as XY (presence of called Y-chromosome
# variants in the sample).
# ═══════════════════════════════════════════════════════════════════════


# ── Haplogroup data classes ──────────────────────────────────────────


@dataclass
class HaplogroupSNP:
    """A single defining SNP from the haplogroup bundle."""

    rsid: str
    pos: int
    allele: str  # derived allele


@dataclass
class HaplogroupNode:
    """A node in the haplogroup tree (recursive structure)."""

    haplogroup: str
    defining_snps: list[HaplogroupSNP]
    children: list[HaplogroupNode]


@dataclass
class HaplogroupBundle:
    """Parsed haplogroup bundle with mtDNA and Y-chromosome trees.

    Attributes:
        version: Bundle version string.
        build: Genome build (e.g. "GRCh37").
        mt_tree: Root node of the mtDNA haplogroup tree.
        y_tree: Root node of the Y-chromosome haplogroup tree.
        mt_snp_rsids: Set of all mtDNA defining SNP rsids.
        y_snp_rsids: Set of all Y-chromosome defining SNP rsids.
    """

    version: str
    build: str
    mt_tree: HaplogroupNode
    y_tree: HaplogroupNode
    mt_snp_rsids: set[str]
    y_snp_rsids: set[str]


@dataclass
class HaplogroupTraversalStep:
    """One step in the haplogroup traversal path."""

    haplogroup: str
    snps_present: int
    snps_total: int


@dataclass
class HaplogroupResult:
    """Result of haplogroup assignment for one tree (mt or Y).

    Attributes:
        tree_type: 'mt' or 'Y'.
        haplogroup: Terminal (deepest matched) haplogroup string.
        confidence: defining_snps_present / defining_snps_total.
        defining_snps_present: Count of matching defining SNPs.
        defining_snps_total: Total defining SNPs for terminal haplogroup.
        traversal_path: List of nodes from root to terminal, each with
            its own match counts.
        assignment_time_ms: Wall-clock time for this assignment.
    """

    tree_type: str
    haplogroup: str
    confidence: float
    defining_snps_present: int
    defining_snps_total: int
    traversal_path: list[HaplogroupTraversalStep]
    assignment_time_ms: float


# ── Bundle loading ───────────────────────────────────────────────────


def _parse_tree_node(data: dict) -> HaplogroupNode:
    """Recursively parse a tree node from the bundle JSON."""
    snps = [
        HaplogroupSNP(rsid=s["rsid"], pos=s["pos"], allele=s["allele"])
        for s in data.get("defining_snps", [])
    ]
    children = [_parse_tree_node(c) for c in data.get("children", [])]
    return HaplogroupNode(
        haplogroup=data["haplogroup"],
        defining_snps=snps,
        children=children,
    )


def _collect_rsids(node: HaplogroupNode) -> set[str]:
    """Collect all defining SNP rsids from a tree recursively."""
    rsids = {s.rsid for s in node.defining_snps}
    for child in node.children:
        rsids |= _collect_rsids(child)
    return rsids


def load_haplogroup_bundle(
    bundle_path: Path | None = None,
) -> HaplogroupBundle:
    """Load the haplogroup defining SNP bundle from JSON.

    Args:
        bundle_path: Optional override for the bundle JSON path.
            Defaults to ``backend/data/panels/haplogroup_bundle.json``.

    Returns:
        Parsed HaplogroupBundle with mtDNA and Y-chromosome trees.

    Raises:
        FileNotFoundError: If the bundle file does not exist.
        KeyError: If required keys are missing from the bundle.
    """
    path = bundle_path or _HAPLOGROUP_BUNDLE_PATH
    logger.info("loading_haplogroup_bundle", path=str(path))

    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    mt_tree = _parse_tree_node(data["trees"]["mt"])
    y_tree = _parse_tree_node(data["trees"]["Y"])

    bundle = HaplogroupBundle(
        version=data.get("version", "1.0.0"),
        build=data.get("build", "GRCh37"),
        mt_tree=mt_tree,
        y_tree=y_tree,
        mt_snp_rsids=_collect_rsids(mt_tree),
        y_snp_rsids=_collect_rsids(y_tree),
    )

    logger.info(
        "haplogroup_bundle_loaded",
        version=bundle.version,
        mt_snps=len(bundle.mt_snp_rsids),
        y_snps=len(bundle.y_snp_rsids),
    )

    return bundle


# ── Tree-walk algorithm ──────────────────────────────────────────────

# Minimum fraction of a node's defining SNPs that must be observed-and-derived
# for the node to count. This bounds over-resolution on sparse data; it is paired
# with a separate hard rule that *no* observed defining SNP may be ancestral
# (``snps_conflicting == 0``) — a missing/untyped marker is lack of evidence, an
# ancestral marker is positive evidence *against* the clade. See ``_tree_walk``.
_HAPLOGROUP_MIN_MATCH_FRACTION = 0.5


def _classify_node_match(
    node: HaplogroupNode,
    genotype_map: dict[str, str | None],
) -> tuple[int, int, int]:
    """Classify a node's defining SNPs into present / conflicting / total.

    Each defining SNP is one of three states, which the haplogroup tree-walk
    must treat differently (a phylogenetic clade is defined by *all* its
    mutations being derived):

    - **present** — typed and the derived allele is observed (het or hom).
    - **conflicting** — typed but the derived allele is absent (ancestral state),
      i.e. positive evidence that the sample is *not* in this clade.
    - **missing** — untyped or a no-call (``--``/``??``…): no evidence either way.
      Missing SNPs count toward ``snps_total`` but neither other bucket.

    Args:
        node: Tree node to check.
        genotype_map: Mapping rsid → genotype string.

    Returns:
        ``(snps_present, snps_conflicting, snps_total)`` where ``snps_total`` is
        the node's full defining-SNP count (present + conflicting + missing).
    """
    snps_total = len(node.defining_snps)
    if snps_total == 0:
        return 0, 0, 0

    snps_present = 0
    snps_conflicting = 0
    for snp in node.defining_snps:
        genotype = genotype_map.get(snp.rsid)
        if genotype is None or is_no_call(genotype):
            continue  # missing/untyped → no evidence either way
        if snp.allele.upper() in genotype.upper():
            snps_present += 1  # derived allele observed
        else:
            snps_conflicting += 1  # typed but ancestral → evidence against the clade

    return snps_present, snps_conflicting, snps_total


def _check_node_match(
    node: HaplogroupNode,
    genotype_map: dict[str, str | None],
) -> tuple[int, int]:
    """Check how many defining SNPs match for a node.

    A defining SNP matches if the sample's genotype at that rsid
    contains the derived allele (heterozygous or homozygous). Back-compatible
    shim over :func:`_classify_node_match` (drops the conflicting count).

    Args:
        node: Tree node to check.
        genotype_map: Mapping rsid → genotype string.

    Returns:
        Tuple of (snps_present, snps_total).
    """
    snps_present, _snps_conflicting, snps_total = _classify_node_match(node, genotype_map)
    return snps_present, snps_total


def _tree_walk(
    node: HaplogroupNode,
    genotype_map: dict[str, str | None],
    path: list[HaplogroupTraversalStep],
) -> tuple[HaplogroupNode, list[HaplogroupTraversalStep]]:
    """Recursive tree-walk to find the deepest matching haplogroup.

    Starting from a node, checks each child. A child is eligible to descend into
    only when **no** observed defining SNP contradicts it (``conflicting == 0``)
    **and** enough of its defining SNPs are observed-and-derived
    (``present / total >= _HAPLOGROUP_MIN_MATCH_FRACTION``). A single ancestral
    (conflicting) marker blocks descent so the walk stops at the deepest fully
    supported ancestor rather than over-resolving into a subclade the sample only
    half-matches. Among eligible children the best-supported (highest derived
    fraction) wins. Returns the deepest node that matches.

    The root node (mt-MRCA / Y-Adam) has no defining SNPs and always
    matches.

    Args:
        node: Current tree node.
        genotype_map: Mapping rsid → genotype string.
        path: Accumulated traversal path (mutated in-place).

    Returns:
        Tuple of (deepest matching node, full traversal path).
    """
    # Try each child node
    best_child: HaplogroupNode | None = None
    best_child_fraction = 0.0
    best_child_present = 0
    best_child_total = 0

    for child in node.children:
        present, conflicting, total = _classify_node_match(child, genotype_map)
        if total == 0:
            continue

        # A clade is defined by all its mutations being derived: any observed
        # ancestral marker is evidence *against* membership, so refuse to descend.
        # A missing (untyped) marker is not a conflict — only typed-ancestral is.
        if conflicting > 0:
            continue

        fraction = present / total
        if fraction >= _HAPLOGROUP_MIN_MATCH_FRACTION and fraction > best_child_fraction:
            best_child = child
            best_child_fraction = fraction
            best_child_present = present
            best_child_total = total

    if best_child is not None:
        # Record this step in the traversal path
        path.append(
            HaplogroupTraversalStep(
                haplogroup=best_child.haplogroup,
                snps_present=best_child_present,
                snps_total=best_child_total,
            )
        )
        # Recurse into the best matching child
        return _tree_walk(best_child, genotype_map, path)

    # No child matched — current node is the deepest match
    return node, path


# ── Main haplogroup assignment ───────────────────────────────────────


def assign_haplogroups(
    bundle: HaplogroupBundle,
    sample_engine: sa.Engine,
) -> list[HaplogroupResult]:
    """Assign mtDNA and Y-chromosome haplogroups for a sample.

    Runs mtDNA tree-walk on all samples. Runs Y-chromosome tree-walk
    only when sex is inferred as XY.

    Args:
        bundle: Loaded haplogroup bundle.
        sample_engine: SQLAlchemy engine for the sample database.

    Returns:
        List of HaplogroupResult (1 for XX samples, 2 for XY samples).
    """
    # Collect all needed rsids
    all_rsids = list(bundle.mt_snp_rsids | bundle.y_snp_rsids)

    # Fetch genotypes — try annotated_variants first, fall back to raw_variants
    genotype_map: dict[str, str | None] = {}
    with sample_engine.connect() as conn:
        try:
            count = conn.execute(
                sa.select(sa.func.count()).select_from(annotated_variants)
            ).scalar()
        except sa.exc.OperationalError:
            count = 0

        if count:
            stmt = sa.select(
                annotated_variants.c.rsid,
                annotated_variants.c.genotype,
            ).where(annotated_variants.c.rsid.in_(all_rsids))
            rows = conn.execute(stmt).fetchall()
        else:
            stmt = sa.select(
                raw_variants.c.rsid,
                raw_variants.c.genotype,
            ).where(raw_variants.c.rsid.in_(all_rsids))
            rows = conn.execute(stmt).fetchall()

    for row in rows:
        genotype_map[row.rsid] = row.genotype

    results: list[HaplogroupResult] = []

    # mtDNA assignment (always runs)
    t0 = time.perf_counter()
    mt_path: list[HaplogroupTraversalStep] = []
    terminal_mt, mt_path = _tree_walk(bundle.mt_tree, genotype_map, mt_path)

    # Accumulate total defining SNPs along the path for confidence
    mt_total_present = sum(step.snps_present for step in mt_path)
    mt_total_snps = sum(step.snps_total for step in mt_path)
    mt_confidence = mt_total_present / mt_total_snps if mt_total_snps > 0 else 0.0
    mt_time = (time.perf_counter() - t0) * 1000.0

    mt_result = HaplogroupResult(
        tree_type="mt",
        haplogroup=terminal_mt.haplogroup,
        confidence=round(mt_confidence, 4),
        defining_snps_present=mt_total_present,
        defining_snps_total=mt_total_snps,
        traversal_path=mt_path,
        assignment_time_ms=round(mt_time, 2),
    )
    results.append(mt_result)

    logger.info(
        "haplogroup_assigned",
        tree="mt",
        haplogroup=mt_result.haplogroup,
        confidence=mt_result.confidence,
        snps=f"{mt_result.defining_snps_present}/{mt_result.defining_snps_total}",
        path=" → ".join(s.haplogroup for s in mt_path),
        time_ms=mt_result.assignment_time_ms,
    )

    # Y-chromosome assignment (only for XY samples). The sex-inference
    # service is the single source of truth (Plan §9.4); "manual_review"
    # and "unknown" both skip the Y tree-walk — the new algorithm is
    # strictly more conservative than the legacy ``y_count > 0`` heuristic
    # on edge cases, and Y haplogroup assignment without a confirmed XY
    # call would be a spurious finding.
    sex = infer_biological_sex(sample_engine)

    if sex == "XY":
        t0 = time.perf_counter()
        y_path: list[HaplogroupTraversalStep] = []
        terminal_y, y_path = _tree_walk(bundle.y_tree, genotype_map, y_path)

        y_total_present = sum(step.snps_present for step in y_path)
        y_total_snps = sum(step.snps_total for step in y_path)
        y_confidence = y_total_present / y_total_snps if y_total_snps > 0 else 0.0
        y_time = (time.perf_counter() - t0) * 1000.0

        y_result = HaplogroupResult(
            tree_type="Y",
            haplogroup=terminal_y.haplogroup,
            confidence=round(y_confidence, 4),
            defining_snps_present=y_total_present,
            defining_snps_total=y_total_snps,
            traversal_path=y_path,
            assignment_time_ms=round(y_time, 2),
        )
        results.append(y_result)

        logger.info(
            "haplogroup_assigned",
            tree="Y",
            haplogroup=y_result.haplogroup,
            confidence=y_result.confidence,
            snps=f"{y_result.defining_snps_present}/{y_result.defining_snps_total}",
            path=" → ".join(s.haplogroup for s in y_path),
            time_ms=y_result.assignment_time_ms,
        )
    else:
        logger.info("y_haplogroup_skipped", reason="sex_inferred", sex=sex)

    return results


# ── Haplogroup findings storage ──────────────────────────────────────


def store_haplogroup_findings(
    results: list[HaplogroupResult],
    sample_engine: sa.Engine,
) -> int:
    """Store haplogroup results in haplogroup_assignments table and findings.

    Writes to both ``haplogroup_assignments`` (structured storage) and
    ``findings`` (unified findings table) for each haplogroup result.

    Args:
        results: List of HaplogroupResult from assign_haplogroups.
        sample_engine: SQLAlchemy engine for the sample database.

    Returns:
        Number of findings inserted.
    """
    if not results:
        return 0

    with sample_engine.begin() as conn:
        # Clear previous haplogroup data
        conn.execute(sa.delete(haplogroup_assignments))
        conn.execute(
            sa.delete(findings).where(
                findings.c.module == "ancestry",
                findings.c.category.in_(["haplogroup_mt", "haplogroup_y"]),
            )
        )

        count = 0
        for result in results:
            # Skip root-only results (no path = no assignment)
            if not result.traversal_path:
                continue

            # Insert into haplogroup_assignments table
            conn.execute(
                sa.insert(haplogroup_assignments),
                {
                    "type": result.tree_type,
                    "haplogroup": result.haplogroup,
                    "confidence": result.confidence,
                    "defining_snps_present": result.defining_snps_present,
                    "defining_snps_total": result.defining_snps_total,
                },
            )

            # Build traversal path for detail_json
            path_data = [
                {
                    "haplogroup": step.haplogroup,
                    "snps_present": step.snps_present,
                    "snps_total": step.snps_total,
                }
                for step in result.traversal_path
            ]

            tree_label = "Mitochondrial" if result.tree_type == "mt" else "Y-chromosome"
            path_str = " → ".join(s.haplogroup for s in result.traversal_path)
            category = f"haplogroup_{result.tree_type}"

            finding_text = (
                f"{tree_label} haplogroup: {result.haplogroup} "
                f"({result.defining_snps_present}/{result.defining_snps_total} "
                f"defining SNPs matched, {result.confidence:.0%} confidence)"
            )

            detail = {
                "tree_type": result.tree_type,
                "haplogroup": result.haplogroup,
                "confidence": result.confidence,
                "defining_snps_present": result.defining_snps_present,
                "defining_snps_total": result.defining_snps_total,
                "traversal_path": path_data,
                "path_string": path_str,
                "assignment_time_ms": result.assignment_time_ms,
            }

            conn.execute(
                sa.insert(findings),
                {
                    "module": "ancestry",
                    "category": category,
                    # Haplogroup via SNP matching = ★★☆☆
                    "evidence_level": ANCESTRY_EVIDENCE_LEVEL,
                    "haplogroup": result.haplogroup,
                    "finding_text": finding_text,
                    "detail_json": json.dumps(detail),
                },
            )
            count += 1

    logger.info("haplogroup_findings_stored", count=count)
    return count


# ── Convenience pipeline ─────────────────────────────────────────────


def run_haplogroup_assignment(
    sample_engine: sa.Engine,
    bundle_path: Path | None = None,
) -> list[HaplogroupResult]:
    """Run the full haplogroup assignment pipeline: load → assign → store.

    Args:
        sample_engine: SQLAlchemy engine for the sample database.
        bundle_path: Optional override for the haplogroup bundle path.

    Returns:
        List of HaplogroupResult for each tree assigned.
    """
    bundle = load_haplogroup_bundle(bundle_path)
    results = assign_haplogroups(bundle, sample_engine)
    store_haplogroup_findings(results, sample_engine)
    return results

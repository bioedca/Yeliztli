"""Generic Polygenic Risk Score (PRS) calculator engine.

Implements P3-14: a reusable PRS engine that computes scores from
published weight sets. Designed to be consumed by cancer (P3-15),
traits & personality (P3-63), and any future PRS-based module.

Key design decisions (from PRD):
  - Weight sets tagged with source GWAS ancestry and sample size.
  - Scores expressed as population percentile + z-score (never raw PRS
    value or absolute lifetime risk).
  - Bootstrap CI (1000 iterations, 95% confidence) for uncertainty.
  - Ancestry mismatch warning field on every result (for P3-16).
  - "Research Use Only" tier — PRS findings are never displayed
    alongside monogenic ClinVar findings.

Usage::

    from backend.analysis.prs import (
        PRSWeightSet,
        PRSResult,
        compute_prs,
        compute_prs_percentile,
        compute_prs_bootstrap_ci,
        store_prs_findings,
    )
    from backend.analysis.ancestry import get_inferred_ancestry

    weight_set = PRSWeightSet(
        name="Breast cancer (BCAC)",
        trait="breast_cancer",
        module="cancer",
        source_ancestry="EUR",
        source_study="Mavaddat et al. 2019",
        source_pmid="30554720",
        sample_size=228951,
        weights=[
            PRSSNPWeight(rsid="rs123", effect_allele="A", weight=0.05),
            ...
        ],
        reference_mean=0.0,
        reference_std=1.0,
    )

    result = compute_prs(weight_set, sample_engine)
    result = compute_prs_percentile(result)
    result = compute_prs_bootstrap_ci(result)
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field

import numpy as np
import sqlalchemy as sa
import structlog

from backend.analysis.allele_match import (
    AMBIGUOUS_DROPPED,
    MATCHED_FLIP,
    MATCHED_REF,
    MISSING_FREQ,
    NO_CALL,
    UNRESOLVED,
    match_effect_allele_dosage,
)
from backend.analysis.evidence import PRS_EVIDENCE_LEVEL
from backend.analysis.return_framing import prs_return_framing
from backend.db.tables import annotated_variants, findings

logger = structlog.get_logger(__name__)

# ── Data classes ──────────────────────────────────────────────────────────


@dataclass
class PRSSNPWeight:
    """A single SNP weight entry in a PRS weight set.

    ``other_allele`` is the non-effect allele of the SNP. It is **optional** for
    back-compatibility with legacy curated weight sets that only recorded the
    effect allele; when present it enables strand harmonization (reverse-strand
    flip resolution and strand-ambiguous-palindrome dropping). When absent, the
    effect allele is counted literally with no strand attempt — identical to the
    historical behaviour (see :func:`backend.analysis.allele_match`).
    """

    rsid: str
    effect_allele: str
    weight: float
    other_allele: str | None = None


@dataclass
class PRSWeightSet:
    """A published PRS weight set tagged with ancestry and study metadata.

    Each weight set defines a collection of SNP→weight mappings for a
    specific trait, along with the reference population distribution
    parameters (mean and std) needed for z-score and percentile
    computation.

    Attributes:
        name: Human-readable name (e.g. "Breast cancer (BCAC)").
        trait: Machine-readable trait identifier.
        module: Owning analysis module (e.g. "cancer", "traits").
        source_ancestry: GWAS source population (e.g. "EUR", "EAS").
        source_study: Study citation.
        source_pmid: PubMed ID of the source GWAS.
        sample_size: Total GWAS sample size.
        weights: List of SNP weight entries.
        reference_mean: Mean PRS in the reference population.
        reference_std: Standard deviation of PRS in the reference population.
        calibrated: Whether ``reference_mean``/``reference_std`` are a validated
            reference distribution for *this exact* shipped score (SNP subset,
            harmonization, and target ancestry). When ``False`` the engine
            refuses to emit a population percentile / z-score / bootstrap CI,
            because converting a raw weighted-allele sum through an
            uncalibrated mean/SD (e.g. the ``0.0``/``1.0`` placeholder) yields a
            number that looks calibrated but is not (see issue #7). Defaults to
            ``True`` so programmatically constructed weight sets keep their
            historical behaviour; data loaders should pass it explicitly.
    """

    name: str
    trait: str
    module: str
    source_ancestry: str
    source_study: str
    source_pmid: str
    sample_size: int
    weights: list[PRSSNPWeight]
    reference_mean: float
    reference_std: float
    calibrated: bool = True

    @property
    def snp_count(self) -> int:
        """Number of SNPs in the weight set."""
        return len(self.weights)

    def rsid_set(self) -> set[str]:
        """Return the set of rsids in this weight set."""
        return {w.rsid for w in self.weights}


@dataclass
class PRSSNPContribution:
    """Individual SNP contribution to a PRS score."""

    rsid: str
    effect_allele: str
    weight: float
    genotype: str | None
    dosage: int  # 0, 1, or 2 copies of effect allele
    contribution: float  # weight * dosage
    match_status: str = MATCHED_REF  # allele_match status (matched_ref/flip/no_call/…)
    strand: str = "ref"  # "ref" | "flip" | "n/a"


@dataclass
class PRSResult:
    """Complete PRS computation result for a single weight set.

    Attributes:
        weight_set_name: Name of the weight set used.
        trait: Trait identifier.
        module: Owning module name.
        source_ancestry: GWAS source population.
        source_study: Study citation.
        source_pmid: PubMed ID.
        sample_size: GWAS sample size.
        raw_score: Sum of weight * dosage.
        z_score: Standardized score ((raw - mean) / std).
        percentile: Population percentile (0–100).
        snps_used: Number of SNPs with available genotype data.
        snps_total: Total SNPs in the weight set.
        coverage_fraction: snps_used / snps_total.
        contributions: Per-SNP contribution breakdown.
        bootstrap_ci_lower: Lower bound of 95% CI (percentile).
        bootstrap_ci_upper: Upper bound of 95% CI (percentile).
        bootstrap_iterations: Number of bootstrap iterations performed.
        ancestry_mismatch: Whether user's ancestry ≠ weight set ancestry.
        ancestry_warning_text: Warning text if ancestry mismatch.
        evidence_level: Star rating (PRS components = ★☆☆☆ = 1).
    """

    weight_set_name: str
    trait: str
    module: str
    source_ancestry: str
    source_study: str
    source_pmid: str
    sample_size: int
    raw_score: float
    z_score: float | None = None
    percentile: float | None = None
    snps_used: int = 0
    snps_total: int = 0
    coverage_fraction: float = 0.0
    # Harmonization disclosure (EXPANSION_STRATEGY.md §10): weight SNPs present
    # in the sample but excluded from / adjusted in the score, surfaced rather
    # than silently dropped.
    snps_no_call: int = 0  # present but unscoreable genotype
    snps_ambiguous_dropped: int = 0  # strand-ambiguous palindrome near MAF 0.5
    snps_strand_flipped: int = 0  # resolved on the complemented strand
    snps_unresolved: int = 0  # alleles fit neither strand
    contributions: list[PRSSNPContribution] = field(default_factory=list)
    bootstrap_ci_lower: float | None = None
    bootstrap_ci_upper: float | None = None
    bootstrap_iterations: int = 0
    ancestry_mismatch: bool = False
    ancestry_warning_text: str | None = None
    evidence_level: int = PRS_EVIDENCE_LEVEL  # PRS components = ★☆☆☆
    # False → no validated reference distribution for this score, so percentile,
    # z-score and bootstrap CI are deliberately withheld (left None). See #7.
    calibrated: bool = True

    @property
    def is_sufficient(self) -> bool:
        """Whether enough SNPs were genotyped for a meaningful score.

        Requires at least 50% of weight set SNPs to have data.
        """
        return self.coverage_fraction >= 0.5

    @property
    def has_bootstrap_ci(self) -> bool:
        """Whether bootstrap CI has been computed."""
        return self.bootstrap_ci_lower is not None and self.bootstrap_ci_upper is not None


# ── Dosage computation ───────────────────────────────────────────────────


def _count_effect_allele(genotype: str | None, effect_allele: str) -> int:
    """Count copies of the effect allele in a genotype string (legacy shim).

    Retained for back-compatibility (and imported by tests). Delegates to the
    shared :func:`backend.analysis.allele_match.match_effect_allele_dosage` with
    no other allele / frequency, which reproduces the historical literal-count
    contract exactly: case-insensitive, no-call/single-char → 0, capped at 2, no
    strand handling. Strand-aware scoring lives in :func:`compute_prs`, which
    passes the weight's ``other_allele`` and the variant's gnomAD MAF.

    Args:
        genotype: Two-character genotype string, or None/empty.
        effect_allele: The effect allele to count.

    Returns:
        0, 1, or 2 — the dosage of the effect allele.
    """
    return match_effect_allele_dosage(genotype, effect_allele, None, None).dosage or 0


# ── Core PRS computation ────────────────────────────────────────────────


def compute_prs(
    weight_set: PRSWeightSet,
    sample_engine: sa.Engine,
) -> PRSResult:
    """Compute a PRS from a weight set against a sample's annotated variants.

    Queries annotated_variants for each SNP in the weight set, computes
    the dosage of the effect allele, and sums weight * dosage.

    Args:
        weight_set: The PRS weight set with SNP weights.
        sample_engine: SQLAlchemy engine for the sample database.

    Returns:
        PRSResult with raw_score and per-SNP contributions.
    """
    rsids = list(weight_set.rsid_set())

    # Fetch genotype + gnomAD MAF for all weight set SNPs in one query. The MAF
    # (already annotated on the same row) is needed only to drop strand-ambiguous
    # palindromes near 0.5 during harmonization.
    with sample_engine.connect() as conn:
        stmt = sa.select(
            annotated_variants.c.rsid,
            annotated_variants.c.genotype,
            annotated_variants.c.gnomad_af_global,
        ).where(annotated_variants.c.rsid.in_(rsids))
        rows = conn.execute(stmt).fetchall()

    genotype_map = {row.rsid: row.genotype for row in rows}
    af_map = {row.rsid: row.gnomad_af_global for row in rows}

    contributions: list[PRSSNPContribution] = []
    raw_score = 0.0
    snps_used = 0
    snps_no_call = 0
    snps_ambiguous_dropped = 0
    snps_strand_flipped = 0
    snps_unresolved = 0

    for w in weight_set.weights:
        genotype = genotype_map.get(w.rsid)
        present = w.rsid in genotype_map and genotype is not None
        match = match_effect_allele_dosage(
            genotype, w.effect_allele, w.other_allele, af_map.get(w.rsid)
        )

        # A SNP contributes to the score only when it resolved to a real dosage
        # (matched on the reference or complemented strand). No-call /
        # ambiguous-dropped / unresolved are excluded from raw_score and
        # snps_used — identical to the historical "missing" treatment — and
        # disclosed via the counters below.
        scored = match.status in (MATCHED_REF, MATCHED_FLIP) and match.dosage is not None
        dosage = match.dosage if scored else 0
        contribution = w.weight * dosage

        if scored:
            snps_used += 1
            raw_score += contribution
            if match.status == MATCHED_FLIP:
                snps_strand_flipped += 1
        elif present:
            # Present in the sample but not scored → tally why.
            if match.status == NO_CALL:
                snps_no_call += 1
            elif match.status in (AMBIGUOUS_DROPPED, MISSING_FREQ):
                snps_ambiguous_dropped += 1
            elif match.status == UNRESOLVED:
                snps_unresolved += 1

        contributions.append(
            PRSSNPContribution(
                rsid=w.rsid,
                effect_allele=w.effect_allele,
                weight=w.weight,
                genotype=genotype,
                dosage=dosage,
                contribution=contribution if scored else 0.0,
                match_status=match.status,
                strand=match.strand,
            )
        )

    snps_total = weight_set.snp_count
    coverage_fraction = snps_used / snps_total if snps_total > 0 else 0.0

    logger.info(
        "prs_computed",
        trait=weight_set.trait,
        raw_score=round(raw_score, 6),
        snps_used=snps_used,
        snps_total=snps_total,
        coverage=round(coverage_fraction, 3),
        no_call=snps_no_call,
        ambiguous_dropped=snps_ambiguous_dropped,
        strand_flipped=snps_strand_flipped,
        unresolved=snps_unresolved,
    )

    return PRSResult(
        weight_set_name=weight_set.name,
        trait=weight_set.trait,
        module=weight_set.module,
        source_ancestry=weight_set.source_ancestry,
        source_study=weight_set.source_study,
        source_pmid=weight_set.source_pmid,
        sample_size=weight_set.sample_size,
        raw_score=raw_score,
        snps_used=snps_used,
        snps_total=snps_total,
        coverage_fraction=coverage_fraction,
        snps_no_call=snps_no_call,
        snps_ambiguous_dropped=snps_ambiguous_dropped,
        snps_strand_flipped=snps_strand_flipped,
        snps_unresolved=snps_unresolved,
        contributions=contributions,
    )


# ── Percentile & z-score ────────────────────────────────────────────────


def compute_prs_percentile(
    result: PRSResult,
    reference_mean: float,
    reference_std: float,
) -> PRSResult:
    """Compute z-score and population percentile from raw PRS score.

    Uses the standard normal CDF to convert a z-score to a percentile.
    The reference_mean and reference_std come from the weight set's
    reference population.

    Args:
        result: PRSResult with raw_score computed.
        reference_mean: Mean PRS in the reference population.
        reference_std: Std dev of PRS in the reference population.

    Returns:
        Updated PRSResult with z_score and percentile populated.
    """
    if reference_std <= 0:
        logger.warning(
            "prs_invalid_reference_std",
            trait=result.trait,
            reference_std=reference_std,
        )
        result.z_score = 0.0
        result.percentile = 50.0
        return result

    z = (result.raw_score - reference_mean) / reference_std
    # Standard normal CDF via error function
    percentile = 0.5 * (1.0 + math.erf(z / math.sqrt(2.0))) * 100.0

    result.z_score = round(z, 4)
    result.percentile = round(percentile, 2)

    logger.info(
        "prs_percentile_computed",
        trait=result.trait,
        z_score=result.z_score,
        percentile=result.percentile,
    )

    return result


# ── Bootstrap confidence interval ───────────────────────────────────────


def compute_prs_bootstrap_ci(
    result: PRSResult,
    reference_mean: float,
    reference_std: float,
    n_iterations: int = 1000,
    confidence_level: float = 0.95,
    rng_seed: int | None = None,
) -> PRSResult:
    """Compute bootstrap confidence interval for PRS percentile.

    Resamples the per-SNP contributions (with replacement) to estimate
    the uncertainty in the PRS score, then converts each bootstrap
    replicate to a percentile. The CI bounds are the 2.5th and 97.5th
    percentiles of the bootstrap distribution.

    Args:
        result: PRSResult with contributions populated.
        reference_mean: Mean PRS in the reference population.
        reference_std: Std dev of PRS in the reference population.
        n_iterations: Number of bootstrap iterations (default 1000).
        confidence_level: CI confidence level (default 0.95).
        rng_seed: Optional RNG seed for reproducibility.

    Returns:
        Updated PRSResult with bootstrap_ci_lower/upper populated.
    """
    if reference_std <= 0 or not result.contributions:
        result.bootstrap_ci_lower = result.percentile
        result.bootstrap_ci_upper = result.percentile
        result.bootstrap_iterations = 0
        return result

    # Extract contributions from SNPs that were actually scored (resolved to a
    # real dosage). No-call / strand-ambiguous-dropped / unresolved SNPs carry a
    # non-None genotype but contributed 0 to the score, so they must not dilute
    # the bootstrap resample.
    used_contributions = [
        c
        for c in result.contributions
        if c.match_status in (MATCHED_REF, MATCHED_FLIP) and c.genotype is not None
    ]
    if not used_contributions:
        result.bootstrap_ci_lower = result.percentile
        result.bootstrap_ci_upper = result.percentile
        result.bootstrap_iterations = 0
        return result

    contribution_values = np.array([c.contribution for c in used_contributions], dtype=np.float64)
    n_snps = len(contribution_values)

    rng = np.random.default_rng(rng_seed)

    # Bootstrap: resample SNP contributions and compute percentile
    bootstrap_percentiles = np.empty(n_iterations, dtype=np.float64)
    sqrt2 = math.sqrt(2.0)

    for i in range(n_iterations):
        indices = rng.integers(0, n_snps, size=n_snps)
        boot_score = contribution_values[indices].sum()
        z = (boot_score - reference_mean) / reference_std
        boot_pct = 0.5 * (1.0 + math.erf(z / sqrt2)) * 100.0
        bootstrap_percentiles[i] = boot_pct

    alpha = 1.0 - confidence_level
    lower = float(np.percentile(bootstrap_percentiles, 100 * alpha / 2))
    upper = float(np.percentile(bootstrap_percentiles, 100 * (1 - alpha / 2)))

    result.bootstrap_ci_lower = round(lower, 2)
    result.bootstrap_ci_upper = round(upper, 2)
    result.bootstrap_iterations = n_iterations

    logger.info(
        "prs_bootstrap_ci_computed",
        trait=result.trait,
        ci_lower=result.bootstrap_ci_lower,
        ci_upper=result.bootstrap_ci_upper,
        iterations=n_iterations,
    )

    return result


# ── Ancestry lookup ────────────────────────────────────────────────────

# NOTE: get_inferred_ancestry was moved to backend.analysis.ancestry.
# Callers must import from there directly.

# ── Ancestry mismatch warning ───────────────────────────────────────────


def check_ancestry_mismatch(
    result: PRSResult,
    inferred_ancestry: str | None,
    top_ancestry_fraction: float | None = None,
) -> PRSResult:
    """Check and flag ancestry mismatch between PRS weights and user ancestry.

    If the user's inferred top ancestry does not match the weight set's
    source population, an amber warning is attached to the result.

    Additionally, if the top ancestry fraction is below 70%, an admixture
    warning is added regardless of whether the populations match — admixed
    individuals may see reduced PRS accuracy even when the top population
    matches the weight set source.

    Args:
        result: PRSResult to check.
        inferred_ancestry: User's inferred top ancestry (e.g. "EUR", "EAS"),
            or None if ancestry inference hasn't been run.
        top_ancestry_fraction: Fraction (0.0–1.0) of the top ancestry, or
            None if unavailable.

    Returns:
        Updated PRSResult with ancestry_mismatch and ancestry_warning_text.
    """
    if inferred_ancestry is None:
        result.ancestry_mismatch = False
        result.ancestry_warning_text = (
            "Ancestry inference has not been run. PRS accuracy depends on "
            "the match between your ancestry and the study population "
            f"({result.source_ancestry})."
        )
        return result

    source = result.source_ancestry.upper()
    inferred = inferred_ancestry.upper()

    if source != inferred:
        result.ancestry_mismatch = True
        result.ancestry_warning_text = (
            f"This PRS was derived from a {result.source_ancestry} population study. "
            f"Your inferred ancestry ({inferred_ancestry}) differs from the source "
            f"population. Percentile estimates may be less accurate for your "
            f"genetic background."
        )
    else:
        result.ancestry_mismatch = False
        result.ancestry_warning_text = None

    # Admixture-aware threshold: warn if top ancestry < 70%
    if top_ancestry_fraction is not None and top_ancestry_fraction < 0.70:
        admixture_warning = (
            "Your ancestry composition is admixed "
            f"(top ancestry {top_ancestry_fraction:.0%}). "
            "PRS accuracy may be reduced for admixed genetic backgrounds."
        )
        if result.ancestry_warning_text:
            result.ancestry_warning_text += f" {admixture_warning}"
        else:
            result.ancestry_mismatch = True
            result.ancestry_warning_text = admixture_warning

    return result


# ── Full PRS pipeline ───────────────────────────────────────────────────


def run_prs(
    weight_set: PRSWeightSet,
    sample_engine: sa.Engine,
    inferred_ancestry: str | None = None,
    top_ancestry_fraction: float | None = None,
    n_bootstrap: int = 1000,
    rng_seed: int | None = None,
) -> PRSResult:
    """Run the complete PRS pipeline: compute → percentile → bootstrap → ancestry check.

    Convenience function that chains all PRS steps.

    Args:
        weight_set: PRS weight set.
        sample_engine: Sample database engine.
        inferred_ancestry: User's inferred ancestry, or None.
        top_ancestry_fraction: Fraction (0.0–1.0) of the top ancestry, or
            None if unavailable.
        n_bootstrap: Bootstrap iterations (default 1000).
        rng_seed: Optional RNG seed for reproducibility.

    Returns:
        Complete PRSResult.
    """
    result = compute_prs(weight_set, sample_engine)
    result.calibrated = weight_set.calibrated
    if weight_set.calibrated:
        result = compute_prs_percentile(
            result, weight_set.reference_mean, weight_set.reference_std
        )
        result = compute_prs_bootstrap_ci(
            result,
            weight_set.reference_mean,
            weight_set.reference_std,
            n_iterations=n_bootstrap,
            rng_seed=rng_seed,
        )
    else:
        # No validated reference distribution: withhold percentile / z-score /
        # CI rather than emit a miscalibrated number (issue #7). raw_score and
        # coverage are still computed and surfaced as a research-use qualitative
        # state.
        logger.info(
            "prs_uncalibrated_percentile_withheld",
            trait=result.trait,
            raw_score=result.raw_score,
        )
    result = check_ancestry_mismatch(result, inferred_ancestry, top_ancestry_fraction)
    return result


# ── Findings storage ────────────────────────────────────────────────────

# §12.4 polygenic trait-architecture education block (SW-A2 / roadmap #30).
# Static context attached to every PRS finding so a percentile is never read as a
# deterministic prediction. It is purely educational and never changes the score,
# percentile, CI, or evidence level.
PRS_TRAIT_ARCHITECTURE: dict[str, str] = {
    "heritability": (
        "Twin-study heritability is larger than SNP heritability, which is larger "
        "than the variance this score explains (h²_twin > h²_SNP > h²_PRS). A "
        "polygenic score captures only a fraction of even the heritable component "
        "of a trait."
    ),
    "portability": (
        "Cross-ancestry accuracy is limited: polygenic-score accuracy falls roughly "
        "linearly with genetic distance from the score's training population "
        "(Ding et al., Nature 2023; Pearson r ≈ −0.95 across 84 traits). A score "
        "derived mainly in one population can be miscalibrated in another."
    ),
    "calibration": (
        "Calibration is not accuracy. Even a correctly calibrated percentile (the "
        "right rank within a population) does not predict your individual outcome — "
        "most trait variation is environmental and non-PRS genetic."
    ),
    "citation": "Ding et al., Nature 618:774-781 (2023); doi:10.1038/s41586-023-06079-4",
}


def store_prs_findings(
    results: list[PRSResult],
    sample_engine: sa.Engine,
    module: str,
) -> int:
    """Store PRS findings in the sample database.

    Creates one finding per PRS result with the appropriate module tag.
    Findings include the "prs" category, z-score, percentile, bootstrap CI,
    ancestry source tag, and mismatch warning.

    Args:
        results: List of PRSResult objects to store.
        sample_engine: SQLAlchemy engine for the sample database.
        module: Module name for clearing/storing (e.g. "cancer", "traits").

    Returns:
        Number of findings inserted.
    """
    rows: list[dict] = []

    for r in results:
        if not r.is_sufficient:
            logger.info(
                "prs_finding_skipped_insufficient",
                trait=r.trait,
                coverage=r.coverage_fraction,
            )
            continue

        if not r.calibrated:
            # No validated reference distribution → percentile is withheld
            # rather than computed from a placeholder mean/SD (issue #7).
            finding_text = (
                f"{r.weight_set_name}: population percentile not reported — score "
                "lacks a validated reference distribution (uncalibrated) — "
                "Research Use Only"
            )
        else:
            percentile_text = f"{r.percentile:.0f}th" if r.percentile is not None else "N/A"
            z_text = f"z = {r.z_score:.2f}" if r.z_score is not None else ""
            ci_text = ""
            if r.has_bootstrap_ci:
                ci_text = f" (95% CI: {r.bootstrap_ci_lower:.0f}th–{r.bootstrap_ci_upper:.0f}th)"

            finding_text = (
                f"{r.weight_set_name}: {percentile_text} percentile{ci_text}"
                f" [{z_text}] — Research Use Only"
            )

        detail = {
            "trait": r.trait,
            "name": r.weight_set_name,
            "is_sufficient": r.is_sufficient,
            "source_ancestry": r.source_ancestry,
            "source_study": r.source_study,
            "source_pmid": r.source_pmid,
            "sample_size": r.sample_size,
            "snps_used": r.snps_used,
            "snps_total": r.snps_total,
            "coverage_fraction": r.coverage_fraction,
            "snps_no_call": r.snps_no_call,
            "snps_ambiguous_dropped": r.snps_ambiguous_dropped,
            "snps_strand_flipped": r.snps_strand_flipped,
            "snps_unresolved": r.snps_unresolved,
            "calibrated": r.calibrated,
            "z_score": r.z_score,
            "bootstrap_ci_lower": r.bootstrap_ci_lower,
            "bootstrap_ci_upper": r.bootstrap_ci_upper,
            "bootstrap_iterations": r.bootstrap_iterations,
            "ancestry_mismatch": r.ancestry_mismatch,
            "ancestry_warning_text": r.ancestry_warning_text,
            "research_use_only": True,
            "architecture": PRS_TRAIT_ARCHITECTURE,
        }
        detail["return_framing"] = prs_return_framing(detail)

        rows.append(
            {
                "module": module,
                "category": "prs",
                "evidence_level": r.evidence_level,
                "finding_text": finding_text,
                "prs_score": r.raw_score,
                "prs_percentile": r.percentile,
                "pmid_citations": json.dumps([r.source_pmid]),
                "detail_json": json.dumps(detail),
            }
        )

    if not rows:
        logger.info("no_prs_findings_to_store", module=module)
        return 0

    with sample_engine.begin() as conn:
        # Clear previous PRS findings for this module
        conn.execute(
            sa.delete(findings).where(
                findings.c.module == module,
                findings.c.category == "prs",
            )
        )
        conn.execute(sa.insert(findings), rows)

    logger.info("prs_findings_stored", module=module, count=len(rows))
    return len(rows)

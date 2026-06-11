"""Cancer-specific PRS integration (P3-15).

Loads published weight sets for four cancer types (breast, prostate,
colorectal, melanoma) and runs them through the generic PRS engine
(P3-14). Results are stored as findings with module='cancer' and
category='prs', displayed in a separate "Research Use Only" tier.

Key decisions (from PRD P3-15):
  - Scores shown as population percentile + z-score, never raw PRS
    or absolute lifetime risk.
  - Bootstrap CI (1000 iterations, 95% confidence) rendered as shaded
    arc on gauge chart.
  - Displayed in separate "Research Use Only" visual tier.
  - Each weight set tagged with source GWAS ancestry and sample size.
  - Evidence level = 1 (★☆☆☆) for all PRS components.

Usage::

    from backend.analysis.cancer_prs import (
        load_cancer_prs_weights,
        run_cancer_prs,
        CancerPRSResult,
    )

    weight_sets = load_cancer_prs_weights()
    result = run_cancer_prs(weight_sets, sample_engine)
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import sqlalchemy as sa
import structlog

from backend.analysis.prs import (
    PRSResult,
    PRSSNPWeight,
    PRSWeightSet,
    run_prs,
    store_prs_findings,
)

logger = structlog.get_logger(__name__)

# Path to the cancer PRS weight sets JSON
_WEIGHTS_PATH = (
    Path(__file__).resolve().parent.parent / "data" / "panels" / "cancer_prs_weights.json"
)

# The four cancer traits covered by P3-15
CANCER_PRS_TRAITS = frozenset(
    {
        "breast_cancer",
        "prostate_cancer",
        "colorectal_cancer",
        "melanoma",
    }
)


# ── Data classes ──────────────────────────────────────────────────────────


@dataclass
class CancerPRSResult:
    """Aggregated cancer PRS results for all four traits.

    Attributes:
        results: Per-trait PRS results (one per weight set).
        sufficient_count: Number of traits with ≥50% SNP coverage.
        insufficient_traits: Traits that lacked coverage.
    """

    results: list[PRSResult] = field(default_factory=list)

    @property
    def sufficient_count(self) -> int:
        """Number of traits with sufficient SNP coverage."""
        return sum(1 for r in self.results if r.is_sufficient)

    @property
    def insufficient_traits(self) -> list[str]:
        """Traits that lacked sufficient SNP coverage."""
        return [r.trait for r in self.results if not r.is_sufficient]

    @property
    def trait_names(self) -> list[str]:
        """All trait identifiers."""
        return [r.trait for r in self.results]


# ── Weight set loading ────────────────────────────────────────────────────


def load_cancer_prs_weights(
    weights_path: Path | None = None,
) -> list[PRSWeightSet]:
    """Load cancer PRS weight sets from JSON.

    Each weight set defines SNP weights for a specific cancer type
    (breast, prostate, colorectal, melanoma) tagged with source GWAS
    ancestry and sample size.

    Args:
        weights_path: Optional override for the weights JSON path.
            Defaults to ``backend/data/panels/cancer_prs_weights.json``.

    Returns:
        List of PRSWeightSet objects for each cancer type.

    Raises:
        FileNotFoundError: If the weights JSON does not exist.
        json.JSONDecodeError: If the weights JSON is malformed.
    """
    path = weights_path or _WEIGHTS_PATH
    logger.info("loading_cancer_prs_weights", path=str(path))

    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    if "weight_sets" not in data:
        raise ValueError(f"Invalid cancer PRS weights file: missing 'weight_sets' key in {path}")

    weight_sets: list[PRSWeightSet] = []
    for idx, ws_data in enumerate(data["weight_sets"]):
        try:
            weights = [
                PRSSNPWeight(
                    rsid=w["rsid"],
                    effect_allele=w["effect_allele"],
                    weight=w["weight"],
                    other_allele=w.get("other_allele"),
                )
                for w in ws_data["weights"]
            ]

            weight_sets.append(
                PRSWeightSet(
                    name=ws_data["name"],
                    trait=ws_data["trait"],
                    module="cancer",
                    source_ancestry=ws_data["source_ancestry"],
                    source_study=ws_data["source_study"],
                    source_pmid=ws_data["source_pmid"],
                    sample_size=ws_data["sample_size"],
                    weights=weights,
                    reference_mean=ws_data["reference_mean"],
                    reference_std=ws_data["reference_std"],
                    # Conservative default: a bundled weight set is treated as
                    # uncalibrated unless it explicitly declares a validated
                    # reference distribution (issue #7).
                    calibrated=ws_data.get("calibrated", False),
                )
            )
        except KeyError as e:
            name = ws_data.get("name", f"index {idx}")
            raise ValueError(f"Missing required field {e} in weight set '{name}'") from e

    logger.info(
        "cancer_prs_weights_loaded",
        count=len(weight_sets),
        traits=[ws.trait for ws in weight_sets],
    )

    return weight_sets


# ── Cancer PRS pipeline ──────────────────────────────────────────────────


def run_cancer_prs(
    weight_sets: list[PRSWeightSet],
    sample_engine: sa.Engine,
    inferred_ancestry: str | None = None,
    top_ancestry_fraction: float | None = None,
    n_bootstrap: int = 1000,
    rng_seed: int | None = None,
) -> CancerPRSResult:
    """Run PRS computation for all cancer traits.

    Runs the generic PRS pipeline for each weight set (breast, prostate,
    colorectal, melanoma). Each result includes raw score, z-score,
    percentile, bootstrap CI, and ancestry mismatch check.

    Args:
        weight_sets: Cancer PRS weight sets from load_cancer_prs_weights.
        sample_engine: SQLAlchemy engine for the sample database.
        inferred_ancestry: User's inferred ancestry (e.g. "EUR"), or None.
        top_ancestry_fraction: Fraction (0.0–1.0) of the top ancestry, or
            None if unavailable.
        n_bootstrap: Bootstrap iterations (default 1000).
        rng_seed: Optional RNG seed for reproducibility.

    Returns:
        CancerPRSResult with per-trait results.
    """
    results: list[PRSResult] = []

    for ws in weight_sets:
        result = run_prs(
            ws,
            sample_engine,
            inferred_ancestry=inferred_ancestry,
            top_ancestry_fraction=top_ancestry_fraction,
            n_bootstrap=n_bootstrap,
            rng_seed=rng_seed,
        )
        results.append(result)

        logger.info(
            "cancer_prs_trait_computed",
            trait=result.trait,
            percentile=result.percentile,
            sufficient=result.is_sufficient,
            snps_used=result.snps_used,
            snps_total=result.snps_total,
        )

    cancer_result = CancerPRSResult(results=results)

    logger.info(
        "cancer_prs_complete",
        total_traits=len(results),
        sufficient=cancer_result.sufficient_count,
        insufficient_traits=cancer_result.insufficient_traits,
    )

    return cancer_result


def store_cancer_prs_findings(
    cancer_result: CancerPRSResult,
    sample_engine: sa.Engine,
) -> int:
    """Store cancer PRS findings in the sample database.

    Delegates to the generic store_prs_findings with module='cancer'.
    Only stores results with sufficient coverage (≥50%).

    Args:
        cancer_result: CancerPRSResult from run_cancer_prs.
        sample_engine: SQLAlchemy engine for the sample database.

    Returns:
        Number of findings inserted.
    """
    return store_prs_findings(cancer_result.results, sample_engine, module="cancer")

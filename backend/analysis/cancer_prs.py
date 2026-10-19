"""Cancer-specific PRS integration (P3-15).

Loads cancer weight-set records, including scoring-disabled non-reporting
models, and runs only eligible traits through the generic PRS engine (P3-14).
Results are stored as findings with module='cancer' and category='prs',
displayed in a separate "Research Use Only" tier.

Key decisions (from PRD P3-15):
  - Scores shown as population percentile + z-score, never raw PRS
    or absolute lifetime risk.
  - Individual PRS intervals are withheld unless a future implementation has
    principled effect-size/posterior uncertainty inputs.
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
    result = run_cancer_prs(weight_sets, sample_engine, inferred_sex="XY")
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
    prs_model_fingerprint,
    run_prs,
    store_prs_findings,
)

logger = structlog.get_logger(__name__)

# Path to the cancer PRS weight sets JSON
_WEIGHTS_PATH = (
    Path(__file__).resolve().parent.parent / "data" / "panels" / "cancer_prs_weights.json"
)

BREAST_CANCER_TRAIT = "breast_cancer"
PROSTATE_CANCER_TRAIT = "prostate_cancer"
_ACTIVE_MODEL_STATUS = "active"
_RUNTIME_BLOCKED_MODEL_STATUS = "source_verified_runtime_blocked"
_ALLOWED_MODEL_STATUSES = {_ACTIVE_MODEL_STATUS, _RUNTIME_BLOCKED_MODEL_STATUS}

SEX_SPECIFIC_PRS_TRAITS = {
    BREAST_CANCER_TRAIT: "XX",
    PROSTATE_CANCER_TRAIT: "XY",
}

# Recorded in every stored cancer PRS finding: the sex context the run was gated
# on. A later read compares it with the current context to tell "skipped for the
# sex recorded back then" apart from "never computed" (#2509).
SCORED_SEX_CONTEXT_KEY = "scored_sex_context"


# ── Data classes ──────────────────────────────────────────────────────────


@dataclass
class CancerPRSResult:
    """Aggregated cancer PRS results for eligible traits.

    Attributes:
        results: Per-trait PRS results (one per weight set).
        scored_sex_context: The resolved biological-sex context the sex gate was
            applied with (``"XX"``, ``"XY"``, ``"manual_review"``, ``"unknown"``
            or ``None``).
        sufficient_count: Number of traits with ≥50% SNP coverage.
        insufficient_traits: Traits that lacked coverage.
    """

    results: list[PRSResult] = field(default_factory=list)
    scored_sex_context: str | None = None

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

    The file contains active weight sets plus any scoring-disabled non-reporting
    model, each tagged with source ancestry and study metadata.

    Args:
        weights_path: Optional override for the weights JSON path.
            Defaults to ``backend/data/panels/cancer_prs_weights.json``.

    Returns:
        List of PRSWeightSet objects for each cancer type, including any
        disabled non-reporting model that :func:`run_cancer_prs` must skip.

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

    raw_weight_sets = data["weight_sets"]
    if not isinstance(raw_weight_sets, list):
        raise ValueError(
            f"Invalid cancer PRS weights file: 'weight_sets' must be a list in {path}"
        )
    traits = [weight_set.get("trait") for weight_set in raw_weight_sets]
    if len(set(traits)) != len(traits):
        raise ValueError(f"Invalid cancer PRS weights file: duplicate top-level trait in {path}")

    weight_sets: list[PRSWeightSet] = []
    for idx, ws_data in enumerate(raw_weight_sets):
        try:
            raw_weights = ws_data["weights"]
            rsids = [weight["rsid"] for weight in raw_weights]
            if len(set(rsids)) != len(rsids):
                raise ValueError(f"Duplicate rsID in weight set '{ws_data['name']}'")

            variants_number = ws_data.get("variants_number")
            if variants_number is not None and variants_number != len(raw_weights):
                raise ValueError(
                    f"Declared variants_number={variants_number} does not match "
                    f"{len(raw_weights)} rows in weight set '{ws_data['name']}'"
                )

            scoring_enabled = ws_data.get("scoring_enabled", True)
            if not isinstance(scoring_enabled, bool):
                raise ValueError(f"Weight set '{ws_data['name']}' has non-boolean scoring_enabled")
            calibrated = ws_data.get("calibrated", False)
            if not isinstance(calibrated, bool):
                raise ValueError(f"Weight set '{ws_data['name']}' has non-boolean calibrated")
            calibration_eligible = ws_data.get("calibration_eligible", True)
            if not isinstance(calibration_eligible, bool):
                raise ValueError(
                    f"Weight set '{ws_data['name']}' has non-boolean calibration_eligible"
                )

            eligibility_schema_present = any(
                "runtime_scoring_eligible" in weight for weight in raw_weights
            )
            eligibility_values = [weight.get("runtime_scoring_eligible") for weight in raw_weights]
            if eligibility_schema_present and any(
                not isinstance(value, bool) for value in eligibility_values
            ):
                raise ValueError(
                    f"Weight set '{ws_data['name']}' has missing or non-boolean "
                    "runtime_scoring_eligible row marker(s)"
                )
            model_status = ws_data.get("model_status")
            if model_status not in _ALLOWED_MODEL_STATUSES:
                raise ValueError(
                    f"Weight set '{ws_data['name']}' has missing or unsupported model_status"
                )
            if model_status == _RUNTIME_BLOCKED_MODEL_STATUS and not eligibility_schema_present:
                raise ValueError(
                    f"Weight set '{ws_data['name']}' is runtime-blocked but has no row markers"
                )
            blocked_rows = [
                weight
                for weight, eligible in zip(raw_weights, eligibility_values, strict=True)
                if eligible is False
            ]
            runtime_scoring_blocked = model_status != _ACTIVE_MODEL_STATUS or bool(blocked_rows)
            if scoring_enabled and runtime_scoring_blocked:
                raise ValueError(
                    f"Weight set '{ws_data['name']}' enables scoring with "
                    f"{len(blocked_rows)} runtime-blocked row(s)"
                )

            weights = [
                PRSSNPWeight(
                    rsid=w["rsid"],
                    effect_allele=w["effect_allele"],
                    weight=w["weight"],
                    other_allele=w.get("other_allele"),
                    chrom=w.get("chrom"),
                    pos=w.get("pos"),
                )
                for w in raw_weights
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
                    calibrated=calibrated,
                    pgs_id=ws_data.get("pgs_id"),
                    pgs_license=ws_data.get("pgs_license"),
                    development_method=ws_data.get("development_method"),
                    genome_build=ws_data.get("genome_build"),
                    variants_number=variants_number,
                    source_url=ws_data.get("source_url"),
                    # Scientific model validity and calibration eligibility are
                    # separate gates. A non-reporting model may remain loadable
                    # for audit without ever reaching the scoring engine (#1934).
                    scoring_enabled=scoring_enabled,
                    calibration_eligible=calibration_eligible,
                    runtime_scoring_blocked=runtime_scoring_blocked,
                    # Monogenic genes assessed separately from this polygenic
                    # score (SW-B3 monogenic exclusion / cross-reference).
                    monogenic_genes=ws_data.get("monogenic_genes", []),
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


def sex_gate_required_sex(trait: str, sex_context: str | None) -> str | None:
    """Return the sex a sex-specific trait requires when ``sex_context`` fails it.

    ``None`` means the gate lets the trait through: either it is not
    sex-specific, or the resolved context matches. Anything other than an exact
    match fails, so ``manual_review``, ``unknown`` and ``None`` withhold the
    score just as the opposite sex does.
    """
    required_sex = SEX_SPECIFIC_PRS_TRAITS.get(trait)
    if required_sex is not None and sex_context != required_sex:
        return required_sex
    return None


def resolve_cancer_prs_sex_context(
    sample_engine: sa.Engine,
    *,
    reference_engine: sa.Engine | None = None,
    sample_id: int | None = None,
) -> str | None:
    """Return the biological-sex context used by sex-specific cancer PRS gates."""
    from backend.services.sex_inference import (
        get_recorded_biological_sex,
        infer_biological_sex,
        resolve_biological_sex,
    )

    inferred = infer_biological_sex(sample_engine)
    recorded = (
        get_recorded_biological_sex(reference_engine, sample_id)
        if reference_engine is not None and sample_id is not None
        else None
    )
    return resolve_biological_sex(recorded_sex=recorded, inferred_sex=inferred).sex


def run_cancer_prs(
    weight_sets: list[PRSWeightSet],
    sample_engine: sa.Engine,
    inferred_ancestry: str | None = None,
    top_ancestry_fraction: float | None = None,
    inferred_sex: str | None = "unknown",
    n_bootstrap: int = 1000,
    rng_seed: int | None = None,
    reference_engine: sa.Engine | None = None,
) -> CancerPRSResult:
    """Run PRS computation for eligible cancer traits.

    Runs the generic PRS pipeline for each eligible weight set. Outputs are
    emitted only for active models and, when a trait is sex-specific, only when
    the caller provides the matching confident sex inference. Unknown or
    manual-review sex contexts suppress those numeric scores.
    Each result includes raw score, z-score, percentile, and ancestry mismatch
    check.

    Args:
        weight_sets: Cancer PRS weight sets from load_cancer_prs_weights.
        sample_engine: SQLAlchemy engine for the sample database.
        inferred_ancestry: User's inferred ancestry (e.g. "EUR"), or None.
        top_ancestry_fraction: Fraction (0.0–1.0) of the top ancestry, or
            None if unavailable.
        inferred_sex: Resolved biological-sex context (``"XX"``, ``"XY"``,
            ``"manual_review"``, ``"unknown"``). ``None`` is treated as
            unknown; pass a confident matching value explicitly to emit
            sex-specific PRS output.
        n_bootstrap: Bootstrap iterations (default 1000).
        rng_seed: Optional RNG seed for reproducibility.
        reference_engine: Optional gnomAD reference engine. When supplied,
            imputed-only scored variants are calibrated (and their continuous
            percentile preserved) instead of withheld (#1281/#1236).

    Returns:
        CancerPRSResult with per-trait results.
    """
    results: list[PRSResult] = []
    skipped_traits: list[str] = []

    for ws in weight_sets:
        if not ws.is_runtime_scored:
            skipped_traits.append(ws.trait)
            logger.warning(
                "cancer_prs_trait_skipped",
                trait=ws.trait,
                reason=(
                    "runtime_scoring_blocked" if ws.runtime_scoring_blocked else "scoring_disabled"
                ),
            )
            continue

        required_sex = sex_gate_required_sex(ws.trait, inferred_sex)
        if required_sex is not None:
            skipped_traits.append(ws.trait)
            logger.info(
                "cancer_prs_trait_skipped",
                trait=ws.trait,
                reason="sex_context_mismatch",
                required_sex=required_sex,
                inferred_sex=inferred_sex,
            )
            continue

        result = run_prs(
            ws,
            sample_engine,
            inferred_ancestry=inferred_ancestry,
            top_ancestry_fraction=top_ancestry_fraction,
            n_bootstrap=n_bootstrap,
            rng_seed=rng_seed,
            reference_engine=reference_engine,
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

    cancer_result = CancerPRSResult(results=results, scored_sex_context=inferred_sex)

    logger.info(
        "cancer_prs_complete",
        total_traits=len(results),
        sufficient=cancer_result.sufficient_count,
        insufficient_traits=cancer_result.insufficient_traits,
        skipped_traits=skipped_traits,
        inferred_sex=inferred_sex,
    )

    return cancer_result


def store_cancer_prs_findings(
    cancer_result: CancerPRSResult,
    sample_engine: sa.Engine,
) -> int:
    """Store cancer PRS findings in the sample database.

    Delegates to the generic store_prs_findings with module='cancer'. Only
    results carrying the exact fingerprint of an enabled bundled model can cross
    this final persistence boundary; this prevents a manually constructed result
    or same-trait alternate model from bypassing a non-reporting gate. An
    eligible result below the 50% coverage threshold is still stored, as a
    "coverage too low" finding with its score and percentile withheld, so the
    model is explained rather than silently missing (#2503) -- the same
    transparent storage FH, eBMD and metabolic PRS use. Each stored finding
    records the run's ``scored_sex_context`` (#2509).

    Args:
        cancer_result: CancerPRSResult from run_cancer_prs.
        sample_engine: SQLAlchemy engine for the sample database.

    Returns:
        Number of findings inserted.
    """
    enabled_model_fingerprints = {
        weight_set.trait: prs_model_fingerprint(weight_set)
        for weight_set in load_cancer_prs_weights()
        if weight_set.is_runtime_scored
    }
    eligible_results: list[PRSResult] = []
    for result in cancer_result.results:
        expected_fingerprint = enabled_model_fingerprints.get(result.trait)
        if expected_fingerprint is None or result.model_fingerprint != expected_fingerprint:
            logger.warning(
                "cancer_prs_result_not_stored",
                trait=result.trait,
                reason=(
                    "no_enabled_bundled_model"
                    if expected_fingerprint is None
                    else "model_fingerprint_mismatch"
                ),
            )
            continue
        eligible_results.append(result)

    return store_prs_findings(
        eligible_results,
        sample_engine,
        module="cancer",
        store_insufficient=True,
        extra_detail={SCORED_SEX_CONTEXT_KEY: cancer_result.scored_sex_context},
    )

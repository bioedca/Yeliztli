"""Post-annotation analysis orchestrator.

Runs all analysis modules after annotation completes, populating the
findings table so the dashboard High-Confidence Findings section has data.

Each module is run independently — a failure in one does not block others.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

    from sqlalchemy import Engine

    from backend.db.connection import DBRegistry

logger = logging.getLogger(__name__)


def run_all_analyses(
    sample_engine: Engine,
    registry: DBRegistry,
    *,
    progress_callback: Callable[[str, int, int], None] | None = None,
) -> dict[str, int | str]:
    """Run every analysis module and store findings.

    Args:
        sample_engine: Per-sample SQLite engine with annotated_variants.
        registry: DB registry (provides reference_engine and settings).
        progress_callback: Optional ``(module_name, index, total)`` callback.

    Returns:
        Dict mapping module name to findings count (int) or error string.
    """
    results: dict[str, int | str] = {}
    modules = _get_modules()

    for i, (name, runner) in enumerate(modules):
        if progress_callback:
            progress_callback(name, i, len(modules))
        try:
            count = runner(sample_engine, registry)
            results[name] = count
            logger.info(
                "analysis_module_complete",
                extra={"analysis_module": name, "findings": count},
            )
        except Exception:
            logger.exception("analysis_module_failed", extra={"analysis_module": name})
            results[name] = "error"

    return results


def _get_modules() -> list[tuple[str, Callable]]:
    """Return (name, runner_fn) pairs for all analysis modules.

    Each runner takes (sample_engine, registry) and returns findings count.
    Uses lazy imports to avoid circular dependencies and keep startup fast.
    """
    return [
        ("cancer", _run_cancer),
        ("carrier_status", _run_carrier),
        ("cardiovascular", _run_cardiovascular),
        ("hemochromatosis", _run_hemochromatosis),
        ("thrombophilia", _run_thrombophilia),
        ("alpha1", _run_alpha1),
        ("amd", _run_amd),
        ("apol1", _run_apol1),
        ("gout", _run_gout),
        ("lhon", _run_lhon),
        ("mt_rnr1", _run_mt_rnr1),
        ("parkinsons", _run_parkinsons),
        ("pharmacogenomics", _run_pharma),
        ("nutrigenomics", _run_nutrigenomics),
        ("traits", _run_traits),
        ("apoe", _run_apoe),
        ("ancestry", _run_ancestry),
        ("sleep", _run_sleep),
        ("fitness", _run_fitness),
        ("skin", _run_skin),
        ("methylation", _run_methylation),
        ("allergy", _run_allergy),
        ("gene_health", _run_gene_health),
        ("qc", _run_qc),
        ("rare_variants", _run_rare_variants),
    ]


# ── Module runners ──────────────────────────────────────────────────
# Each function encapsulates one module's analysis logic,
# mirroring the POST /run endpoint but without HTTP concerns.


def _run_cancer(sample_engine: Engine, registry: DBRegistry) -> int:
    from backend.analysis.ancestry import get_inferred_ancestry
    from backend.analysis.cancer import (
        extract_cancer_variants,
        load_cancer_panel,
        store_cancer_findings,
    )
    from backend.analysis.cancer_prs import (
        load_cancer_prs_weights,
        run_cancer_prs,
        store_cancer_prs_findings,
    )
    from backend.services.sex_inference import infer_biological_sex

    panel = load_cancer_panel()
    result = extract_cancer_variants(panel, sample_engine)
    count = store_cancer_findings(result, sample_engine, registry.reference_engine)

    from backend.analysis.ancestry import get_top_ancestry_fraction

    weight_sets = load_cancer_prs_weights()
    inferred_ancestry = get_inferred_ancestry(sample_engine)
    top_fraction = get_top_ancestry_fraction(sample_engine)
    inferred_sex = infer_biological_sex(sample_engine)
    prs_result = run_cancer_prs(
        weight_sets,
        sample_engine,
        inferred_ancestry=inferred_ancestry,
        top_ancestry_fraction=top_fraction,
        inferred_sex=inferred_sex,
    )
    count += store_cancer_prs_findings(prs_result, sample_engine)
    return count


def _run_carrier(sample_engine: Engine, registry: DBRegistry) -> int:
    from backend.analysis.carrier_status import (
        extract_carrier_variants,
        load_carrier_panel,
        store_carrier_findings,
    )

    panel = load_carrier_panel()
    result = extract_carrier_variants(panel, sample_engine)
    return store_carrier_findings(result, sample_engine)


def _run_cardiovascular(sample_engine: Engine, registry: DBRegistry) -> int:
    from backend.analysis.cardiovascular import (
        extract_cardiovascular_variants,
        load_cardiovascular_panel,
        store_cardiovascular_findings,
    )

    panel = load_cardiovascular_panel()
    result = extract_cardiovascular_variants(panel, sample_engine)
    return store_cardiovascular_findings(result, sample_engine, registry.reference_engine)


def _run_pharma(sample_engine: Engine, registry: DBRegistry) -> int:
    from backend.analysis.pharmacogenomics import (
        call_all_star_alleles,
        generate_prescribing_alerts,
        store_prescribing_alerts,
    )

    star_allele_results = call_all_star_alleles(registry.reference_engine, sample_engine)
    alerts = generate_prescribing_alerts(star_allele_results, registry.reference_engine)
    return store_prescribing_alerts(alerts, sample_engine)


def _run_nutrigenomics(sample_engine: Engine, registry: DBRegistry) -> int:
    from backend.analysis.nutrigenomics import (
        load_nutrigenomics_panel,
        score_nutrigenomics_pathways,
        store_nutrigenomics_findings,
    )

    panel = load_nutrigenomics_panel()
    result = score_nutrigenomics_pathways(panel, sample_engine, registry.reference_engine)
    return store_nutrigenomics_findings(result, sample_engine)


def _run_traits(sample_engine: Engine, registry: DBRegistry) -> int:
    from backend.analysis.traits import (
        load_traits_panel,
        score_traits_pathways,
        store_traits_findings,
    )

    panel = load_traits_panel()
    result = score_traits_pathways(panel, sample_engine, registry.reference_engine)
    return store_traits_findings(result, sample_engine)


def _run_hemochromatosis(sample_engine: Engine, registry: DBRegistry) -> int:
    from backend.analysis.hemochromatosis import (
        assess_hemochromatosis,
        load_hemochromatosis_panel,
        store_hemochromatosis_findings,
    )

    panel = load_hemochromatosis_panel()
    assessment = assess_hemochromatosis(panel, sample_engine)
    return store_hemochromatosis_findings(assessment, sample_engine)


def _run_thrombophilia(sample_engine: Engine, registry: DBRegistry) -> int:
    from backend.analysis.thrombophilia import (
        assess_thrombophilia,
        load_thrombophilia_panel,
        store_thrombophilia_findings,
    )

    panel = load_thrombophilia_panel()
    assessment = assess_thrombophilia(panel, sample_engine)
    return store_thrombophilia_findings(assessment, sample_engine)


def _run_alpha1(sample_engine: Engine, registry: DBRegistry) -> int:
    from backend.analysis.alpha1 import (
        assess_alpha1,
        load_alpha1_panel,
        store_alpha1_findings,
    )

    panel = load_alpha1_panel()
    assessment = assess_alpha1(panel, sample_engine)
    return store_alpha1_findings(assessment, sample_engine)


def _run_amd(sample_engine: Engine, registry: DBRegistry) -> int:
    from backend.analysis.amd import (
        assess_amd,
        load_amd_panel,
        store_amd_findings,
    )

    panel = load_amd_panel()
    assessment = assess_amd(panel, sample_engine)
    return store_amd_findings(assessment, sample_engine)


def _run_apol1(sample_engine: Engine, registry: DBRegistry) -> int:
    from backend.analysis.apol1 import (
        assess_apol1,
        load_apol1_panel,
        store_apol1_findings,
    )

    panel = load_apol1_panel()
    assessment = assess_apol1(panel, sample_engine)
    return store_apol1_findings(assessment, sample_engine)


def _run_gout(sample_engine: Engine, registry: DBRegistry) -> int:
    from backend.analysis.gout import (
        assess_gout,
        load_gout_panel,
        store_gout_findings,
    )

    panel = load_gout_panel()
    assessment = assess_gout(panel, sample_engine)
    return store_gout_findings(assessment, sample_engine)


def _run_lhon(sample_engine: Engine, registry: DBRegistry) -> int:
    from backend.analysis.lhon import (
        assess_lhon,
        load_lhon_panel,
        store_lhon_findings,
    )

    panel = load_lhon_panel()
    assessment = assess_lhon(panel, sample_engine)
    return store_lhon_findings(assessment, sample_engine)


def _run_mt_rnr1(sample_engine: Engine, registry: DBRegistry) -> int:
    from backend.analysis.mt_rnr1 import (
        assess_mt_rnr1,
        load_mt_rnr1_panel,
        store_mt_rnr1_findings,
    )

    panel = load_mt_rnr1_panel()
    assessment = assess_mt_rnr1(panel, sample_engine)
    return store_mt_rnr1_findings(assessment, sample_engine)


def _run_parkinsons(sample_engine: Engine, registry: DBRegistry) -> int:
    from backend.analysis.parkinsons import (
        assess_parkinsons,
        load_parkinsons_panel,
        store_parkinsons_findings,
    )

    panel = load_parkinsons_panel()
    assessment = assess_parkinsons(panel, sample_engine)
    return store_parkinsons_findings(assessment, sample_engine)


def _run_apoe(sample_engine: Engine, registry: DBRegistry) -> int:
    from backend.analysis.apoe import (
        determine_apoe_genotype,
        store_apoe_three_findings,
    )

    result = determine_apoe_genotype(sample_engine)
    return store_apoe_three_findings(result, sample_engine)


def _run_ancestry(sample_engine: Engine, registry: DBRegistry) -> int:
    from backend.analysis.ancestry import run_ancestry_inference

    result = run_ancestry_inference(sample_engine)
    # run_ancestry_inference already calls store_ancestry_findings internally;
    # only count as 1 finding when coverage was sufficient (finding stored)
    return 1 if result.is_sufficient and result.top_population else 0


def _run_sleep(sample_engine: Engine, registry: DBRegistry) -> int:
    from backend.analysis.sleep import (
        load_sleep_panel,
        score_sleep_pathways,
        store_sleep_findings,
    )

    panel = load_sleep_panel()
    result = score_sleep_pathways(panel, sample_engine, registry.reference_engine)
    return store_sleep_findings(result, sample_engine)


def _run_fitness(sample_engine: Engine, registry: DBRegistry) -> int:
    from backend.analysis.fitness import (
        load_fitness_panel,
        score_fitness_pathways,
        store_fitness_findings,
    )

    panel = load_fitness_panel()
    result = score_fitness_pathways(panel, sample_engine, registry.reference_engine)
    return store_fitness_findings(result, sample_engine)


def _run_skin(sample_engine: Engine, registry: DBRegistry) -> int:
    from backend.analysis.skin import (
        load_skin_panel,
        score_skin_pathways,
        store_skin_findings,
    )

    panel = load_skin_panel()
    result = score_skin_pathways(panel, sample_engine, registry.reference_engine)
    return store_skin_findings(result, sample_engine)


def _run_methylation(sample_engine: Engine, registry: DBRegistry) -> int:
    from backend.analysis.methylation import (
        load_methylation_panel,
        score_methylation_pathways,
        store_methylation_findings,
    )

    panel = load_methylation_panel()
    result = score_methylation_pathways(panel, sample_engine, registry.reference_engine)
    return store_methylation_findings(result, sample_engine)


def _run_allergy(sample_engine: Engine, registry: DBRegistry) -> int:
    from backend.analysis.allergy import (
        load_allergy_panel,
        score_allergy_pathways,
        store_allergy_findings,
    )

    panel = load_allergy_panel()
    result = score_allergy_pathways(panel, sample_engine, registry.reference_engine)
    return store_allergy_findings(result, sample_engine)


def _run_gene_health(sample_engine: Engine, registry: DBRegistry) -> int:
    from backend.analysis.gene_health import (
        load_gene_health_panel,
        score_gene_health_pathways,
        store_gene_health_findings,
    )

    panel = load_gene_health_panel()
    result = score_gene_health_pathways(panel, sample_engine, registry.reference_engine)
    return store_gene_health_findings(result, sample_engine)


def _run_qc(sample_engine: Engine, registry: DBRegistry) -> int:
    # QC writes to the qc_metrics table (not findings), so it does not affect the
    # high-confidence findings set; returns 0 to fit the runner contract.
    from backend.analysis.qc import compute_qc_metrics, store_qc_metrics

    metrics = compute_qc_metrics(sample_engine)
    store_qc_metrics(metrics, sample_engine)
    return 0


def _run_rare_variants(sample_engine: Engine, registry: DBRegistry) -> int:
    from backend.analysis.rare_variant_finder import (
        RareVariantFilter,
        find_rare_variants,
        store_rare_variant_findings,
    )
    from backend.services.sex_inference import infer_biological_sex

    # Carriage-gate the live finder: only surface variants the individual
    # actually carries (a chip genotypes every probe regardless of carriage).
    # Sex-gate it too (F8): infer biological sex once and drop findings that
    # contradict it (e.g. a Y-chromosome finding on an XX sample).
    inferred_sex = infer_biological_sex(sample_engine)
    result = find_rare_variants(
        RareVariantFilter(carried_only=True, inferred_sex=inferred_sex), sample_engine
    )
    return store_rare_variant_findings(result, sample_engine)

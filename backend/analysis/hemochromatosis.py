"""Hereditary haemochromatosis (HFE) risk module — EXPANSION_STRATEGY.md §6 / #23.

A thin adapter over the shared declarative risk-genotype caller
(:mod:`backend.analysis.risk_genotype`). Directly-typed C282Y (rs1800562) and
H63D (rs1799945) with genotype-combination-specific calls and sex-stratified
penetrance (biological sex from :func:`backend.services.sex_inference`).
"""

from __future__ import annotations

from pathlib import Path

import sqlalchemy as sa

from backend.analysis.risk_genotype import (
    RiskAssessment,
    RiskPanel,
    classify,
    compute_dosages,
    load_risk_panel,
    read_genotypes,
    store_risk_findings,
)
from backend.services.sex_inference import (
    get_recorded_biological_sex,
    infer_biological_sex,
    resolve_biological_sex,
)

_PANEL_PATH = (
    Path(__file__).resolve().parent.parent / "data" / "panels" / "hemochromatosis_panel.json"
)

MODULE = "hemochromatosis"


def load_hemochromatosis_panel(panel_path: Path | None = None) -> RiskPanel:
    """Load the curated HFE risk panel."""
    return load_risk_panel(panel_path or _PANEL_PATH)


def assess_hemochromatosis(
    panel: RiskPanel,
    sample_engine: sa.Engine,
    *,
    reference_engine: sa.Engine | None = None,
    sample_id: int | None = None,
) -> RiskAssessment:
    """Read HFE genotypes and classify, using recorded biological sex when available.

    C282Y-homozygote penetrance is sex-stratified (cumulative haemochromatosis
    diagnosis by age 80: ~56.4% male vs ~40.5% female; Lucas 2024, PMID 38479735), so
    the *wrong* sex shows the wrong figure. A user-recorded ``individuals.biological_sex``
    is authoritative over the noisy array inference (issue #399) — mirroring
    :func:`~backend.services.sex_inference.resolve_biological_sex` and the breast
    absolute-risk overlay — so a recorded value picks the correct single-sex penetrance
    even when inference is missing or discordant. ``reference_engine`` + ``sample_id``
    supply the recorded value; without them (legacy callers) the path falls back to
    inference, preserving prior behaviour.
    """
    readouts = read_genotypes(panel, sample_engine)
    dosages = compute_dosages(panel, readouts)
    if not panel.sex_stratified:
        return classify(panel, dosages, readouts, sex=None)

    inferred = infer_biological_sex(sample_engine)
    recorded = (
        get_recorded_biological_sex(reference_engine, sample_id)
        if reference_engine is not None and sample_id is not None
        else None
    )
    resolved = resolve_biological_sex(recorded_sex=recorded, inferred_sex=inferred)
    assessment = classify(panel, dosages, readouts, sex=resolved.sex)
    # Persist sex provenance alongside the existing detail["sex_used"] so the
    # interpretation is auditable: which source drove the penetrance, and whether a
    # recorded value overrode a discordant inference.
    for call in assessment.calls:
        call.detail["sex_source"] = resolved.source
        call.detail["sex_conflict"] = resolved.conflict
    return assessment


def store_hemochromatosis_findings(
    assessment: RiskAssessment,
    sample_engine: sa.Engine,
) -> int:
    """Persist HFE findings to the sample DB (idempotent)."""
    return store_risk_findings(assessment, sample_engine)

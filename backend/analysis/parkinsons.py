"""Parkinson's disease risk module (LRRK2 G2019S) — roadmap #41.

Thin adapter over the shared declarative risk-genotype caller for the single
directly-typed Parkinson's risk variant we can reliably call from an array:
LRRK2 G2019S (rs34637584). The finding is surfaced behind an **APOE-style ethical
opt-in gate** (see :mod:`backend.api.routes.parkinsons`) and stored at
``evidence_level 2`` so it does not auto-surface in the ungated dashboard
high-confidence section (which filters ``evidence_level >= 3``).

Honesty / ethics guardrails (§12.6, §12.10):
  - **Reduced, age-dependent penetrance** — published age-80 estimates vary by
    cohort design, ancestry, and modifier burden: recent cohorts report roughly
    24-49%, with kin-cohort estimates around 25-42.5%. Most carriers never
    develop Parkinson's. A positive call is never a diagnosis or a prediction.
  - **No preventive treatment** — there is no proven prevention, so the result is
    not actionable in a treatment sense; its value is personal (awareness, family
    planning, research). Routed to genetic counseling / CLIA confirmation.
  - **GBA1 is deliberately not reported.** The adjacent GBAP1 pseudogene makes
    array (and short-read) GBA1 genotyping unreliable, so GBA1 risk variants are
    suppressed here rather than reported as low-confidence calls. This is stated
    in the disclaimer; only LRRK2 G2019S is in the panel.

Findings carry ``clinvar_significance=NULL`` (a common reduced-penetrance risk
variant, not a deterministic ClinVar call).
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

_PANEL_PATH = Path(__file__).resolve().parent.parent / "data" / "panels" / "parkinsons_panel.json"

MODULE = "parkinsons"


def load_parkinsons_panel(panel_path: Path | None = None) -> RiskPanel:
    """Load the curated Parkinson's (LRRK2 G2019S) risk panel."""
    return load_risk_panel(panel_path or _PANEL_PATH)


def assess_parkinsons(panel: RiskPanel, sample_engine: sa.Engine) -> RiskAssessment:
    """Read LRRK2 G2019S and classify (no ancestry/sex stratification of the call)."""
    readouts = read_genotypes(panel, sample_engine)
    dosages = compute_dosages(panel, readouts)
    return classify(panel, dosages, readouts)


def store_parkinsons_findings(assessment: RiskAssessment, sample_engine: sa.Engine) -> int:
    """Persist Parkinson's findings to the sample DB (idempotent)."""
    return store_risk_findings(assessment, sample_engine)

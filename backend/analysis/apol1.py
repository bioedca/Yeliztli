"""APOL1 kidney-risk module (G1/G2 + N264K modifier) — §9 / roadmap #27.

Thin adapter over the shared declarative risk-genotype caller, injecting the
sample's inferred ancestry so the engine can add APOL1's validation context.
APOL1 risk is **recessive** (two risk alleles, any combination of G1 and G2),
**ancestry-contextualized** (the G1/G2 alleles are near-absent and best validated
in recent African ancestry), and modified by **N264K** (rs73885316), which
attenuates G2-associated risk. The G2 allele is a 6-bp deletion (rs71785313) that
is often off-chip; an untyped G2 yields a partial genotype, never a false
low-risk call.
"""

from __future__ import annotations

from pathlib import Path

import sqlalchemy as sa

from backend.analysis.ancestry import get_inferred_ancestry, get_top_ancestry_fraction
from backend.analysis.risk_genotype import (
    RiskAssessment,
    RiskPanel,
    classify,
    compute_dosages,
    load_risk_panel,
    read_genotypes,
    store_risk_findings,
)

_PANEL_PATH = Path(__file__).resolve().parent.parent / "data" / "panels" / "apol1_panel.json"

MODULE = "apol1"


def load_apol1_panel(panel_path: Path | None = None) -> RiskPanel:
    """Load the curated APOL1 risk panel."""
    return load_risk_panel(panel_path or _PANEL_PATH)


def assess_apol1(panel: RiskPanel, sample_engine: sa.Engine) -> RiskAssessment:
    """Read APOL1 G1/G2/N264K genotypes and classify with ancestry context."""
    readouts = read_genotypes(panel, sample_engine)
    dosages = compute_dosages(panel, readouts)
    inferred_ancestry = get_inferred_ancestry(sample_engine)
    ancestry_fraction = get_top_ancestry_fraction(sample_engine)
    return classify(
        panel,
        dosages,
        readouts,
        inferred_ancestry=inferred_ancestry,
        ancestry_fraction=ancestry_fraction,
    )


def store_apol1_findings(assessment: RiskAssessment, sample_engine: sa.Engine) -> int:
    """Persist APOL1 findings to the sample DB (idempotent)."""
    return store_risk_findings(assessment, sample_engine)

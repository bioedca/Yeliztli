"""Opt-in breast cancer absolute-risk overlay (SW-B8).

An **opt-in** overlay that contextualizes a user's breast-cancer genetics in
terms of *absolute* risk, layered on the existing cancer module. Because it
quantifies absolute disease risk it is gated behind explicit per-sample consent
(stored in ``risk_overlay_consent``; Alembic migration 012).

Honest scope. A personalized, polygenic-derived absolute risk would require a
calibrated breast PRS percentile, which is withheld on un-imputed array data
(coverage-limited; see SW-B5). So this overlay does **not** fabricate a personal
PRS number. Instead, once consented, it presents:
  * the US population baseline (NCI SEER: ~12.9% lifetime, ~1 in 8 women);
  * for carriers of a high-penetrance monogenic variant, the published
    genotype-class cumulative risk (BRCA1 ~72%, BRCA2 ~69% to age 80;
    Kuchenbaecker et al., JAMA 2017) with a genetics-referral prompt;
  * a handoff to the validated multifactorial model **CanRisk / BOADICEA**
    (Lee et al., Genet Med 2019; www.canrisk.org), which integrates PRS, family
    history and hormonal/lifestyle factors for an individualized estimate.

This is education + risk-stratification context, not a clinical risk assessment.
"""

from __future__ import annotations

import sqlalchemy as sa
import structlog

from backend.db.tables import findings, risk_overlay_consent

logger = structlog.get_logger(__name__)

FEATURE = "breast_absolute_risk"

# High/moderate-penetrance breast genes screened by the cancer panel.
BREAST_MONOGENIC_GENES = (
    "BRCA1",
    "BRCA2",
    "PALB2",
    "ATM",
    "CHEK2",
    "TP53",
    "PTEN",
    "CDH1",
    "STK11",
)

# US population baseline (data source, not a journal article).
SEER_BASELINE = {
    "lifetime_risk_pct": 12.9,
    "source": "NCI SEER Cancer Stat Facts: Female Breast Cancer",
    "source_url": "https://seer.cancer.gov/statfacts/html/breast.html",
    "note": "About 1 in 8 US women are diagnosed with breast cancer over their lifetime.",
}

# Published genotype-class cumulative breast-cancer risk to age 80 (carriers).
MONOGENIC_PENETRANCE = {
    "BRCA1": {"cumulative_risk_to_80_pct": 72, "ci": "65-79", "pmid": "28632866"},
    "BRCA2": {"cumulative_risk_to_80_pct": 69, "ci": "61-77", "pmid": "28632866"},
}

CANRISK = {
    "tool": "CanRisk / BOADICEA",
    "url": "https://www.canrisk.org",
    "pmid": "30643217",
    "note": (
        "CanRisk (BOADICEA) is the validated multifactorial model that integrates "
        "polygenic score, family history, hormonal/reproductive and lifestyle "
        "factors, and pathogenic variants for an individualized absolute risk."
    ),
}

PRS_NOTE = (
    "A breast-cancer polygenic score would further refine this estimate, but on "
    "un-imputed genotyping-array data its coverage is too low for a reliable "
    "percentile, so a personalized polygenic absolute risk is not shown here."
)

DISCLAIMER = (
    "Research/education only — not a clinical risk assessment or diagnosis. "
    "Absolute-risk figures are population- or genotype-class averages, not your "
    "individual risk. Discuss breast-cancer risk and screening with a clinician "
    "or genetic counsellor, especially if a pathogenic variant is reported."
)

OPT_IN_PROMPT = (
    "This optional overlay places your breast-cancer genetics in an absolute-risk "
    "context (population incidence and, for carriers, published genotype-class "
    "risk). Because it quantifies disease risk, it is shown only after you opt in."
)


# ── Consent (reference DB) ─────────────────────────────────────────────────


def get_consent(reference_engine: sa.Engine, sample_id: int) -> bool:
    """Whether the sample has opted in to the breast absolute-risk overlay."""
    with reference_engine.connect() as conn:
        row = conn.execute(
            sa.select(risk_overlay_consent.c.consented).where(
                risk_overlay_consent.c.sample_id == sample_id,
                risk_overlay_consent.c.feature == FEATURE,
            )
        ).fetchone()
    return bool(row and row.consented)


def set_consent(reference_engine: sa.Engine, sample_id: int, consented: bool) -> None:
    """Record (upsert) opt-in/opt-out consent for a sample."""
    with reference_engine.begin() as conn:
        conn.execute(
            sa.delete(risk_overlay_consent).where(
                risk_overlay_consent.c.sample_id == sample_id,
                risk_overlay_consent.c.feature == FEATURE,
            )
        )
        conn.execute(
            sa.insert(risk_overlay_consent).values(
                sample_id=sample_id,
                feature=FEATURE,
                consented=1 if consented else 0,
                # Record the grant time only when opting in; NULL on opt-out.
                consented_at=sa.func.now() if consented else None,
            )
        )
    logger.info("risk_overlay_consent_set", sample_id=sample_id, consented=consented)


# ── Overlay ────────────────────────────────────────────────────────────────


def _breast_monogenic_carriers(sample_engine: sa.Engine) -> list[str]:
    """Genes with a reportable breast-cancer monogenic finding in the sample."""
    with sample_engine.connect() as conn:
        rows = conn.execute(
            sa.select(findings.c.gene_symbol)
            .where(
                findings.c.category == "monogenic_variant",
                findings.c.gene_symbol.in_(BREAST_MONOGENIC_GENES),
                findings.c.zygosity.in_(("het", "hom_alt")),
            )
            .distinct()
        ).fetchall()
    return sorted({r.gene_symbol for r in rows if r.gene_symbol})


def build_breast_absolute_risk(
    sample_engine: sa.Engine,
    *,
    consented: bool,
) -> dict:
    """Build the breast absolute-risk overlay payload.

    Pre-consent: returns only the opt-in prompt + disclaimer (no risk figures).
    Post-consent: population baseline + carrier penetrance + CanRisk handoff.
    """
    if not consented:
        return {
            "consented": False,
            "opt_in_required": True,
            "opt_in_prompt": OPT_IN_PROMPT,
            "disclaimer": DISCLAIMER,
        }

    carriers = _breast_monogenic_carriers(sample_engine)
    monogenic = [
        {
            "gene": g,
            **MONOGENIC_PENETRANCE.get(
                g,
                {
                    "cumulative_risk_to_80_pct": None,
                    "ci": None,
                    "pmid": None,
                    "note": (
                        "Moderate-to-high-penetrance breast-cancer gene; "
                        "individualized risk via CanRisk + a genetics referral."
                    ),
                },
            ),
        }
        for g in carriers
    ]

    return {
        "consented": True,
        "opt_in_required": False,
        "population_baseline": SEER_BASELINE,
        "has_monogenic": bool(carriers),
        "monogenic": monogenic,
        "prs_note": PRS_NOTE,
        "canrisk": CANRISK,
        "disclaimer": DISCLAIMER,
        "research_use_only": True,
    }

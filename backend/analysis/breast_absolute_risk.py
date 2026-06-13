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
from backend.services.sex_inference import resolve_biological_sex

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

# Published *female* genotype-class cumulative breast-cancer risk to age 80
# (Kuchenbaecker et al., JAMA 2017, PMID 28632866 — a female-carrier cohort). These
# figures are sex-specific and must not be shown for XY/male or sex-unresolved samples.
MONOGENIC_PENETRANCE = {
    "BRCA1": {"cumulative_risk_to_80_pct": 72, "ci": "65-79", "pmid": "28632866"},
    "BRCA2": {"cumulative_risk_to_80_pct": 69, "ci": "61-77", "pmid": "28632866"},
}

# Male carriers have a very different profile: male breast-cancer lifetime risk is far
# below the female estimate, while prostate cancer becomes a major sex-specific
# component (especially BRCA2). Ranges per Lecarpentier et al. 2017, JCO (PMID 28448241);
# male breast-cancer cumulative risk to age 70 is ~1.2% (BRCA1) / ~6.8% (BRCA2) in
# Tai et al. 2007, JNCI. We carry the qualitative, citable framing rather than the
# female point estimates. ``cumulative_risk_to_80_pct`` stays None (no female number).
MALE_MONOGENIC_PENETRANCE = {
    "BRCA1": {
        "cumulative_risk_to_80_pct": None,
        "ci": None,
        "pmid": "28448241",
        "note": (
            "Male BRCA1 carrier: male breast-cancer lifetime risk is low (~1–5%, far "
            "below the female ~72% estimate) and prostate-cancer risk is elevated. "
            "Discuss male-specific screening with clinical genetics."
        ),
    },
    "BRCA2": {
        "cumulative_risk_to_80_pct": None,
        "ci": None,
        "pmid": "28448241",
        "note": (
            "Male BRCA2 carrier: male breast-cancer lifetime risk ~5–10% (far below the "
            "female ~69% estimate) and substantially elevated prostate-cancer risk; "
            "BRCA2 is the principal male breast-cancer gene. Discuss male-specific "
            "screening with clinical genetics."
        ),
    },
}

# Moderate-to-high-penetrance breast genes without a curated sex-specific number.
_FEMALE_MODERATE_FALLBACK = {
    "cumulative_risk_to_80_pct": None,
    "ci": None,
    "pmid": None,
    "note": (
        "Moderate-to-high-penetrance breast-cancer gene; "
        "individualized risk via CanRisk + a genetics referral."
    ),
}
_MALE_MODERATE_FALLBACK = {
    "cumulative_risk_to_80_pct": None,
    "ci": None,
    "pmid": None,
    "note": (
        "Moderate-to-high-penetrance breast-cancer gene; male-specific risk is not well "
        "quantified. Individualized risk via CanRisk + a genetics referral."
    ),
}
_UNRESOLVED_CARRIER_NOTE = (
    "Biological sex not resolved from array data — sex-specific penetrance is withheld. "
    "Discuss with clinical genetics / CanRisk."
)

# Per-sex framing surfaced to the user so the applicable context is explicit.
# A recorded biological sex that disagrees with a confident array inference;
# the recorded (user-set) value is authoritative and used, but the discrepancy
# is surfaced (issue #254).
_SEX_CONFLICT_NOTE = (
    "Your recorded biological sex differs from the sex inferred from your array "
    "data; the recorded value is used here. Confirm your recorded sex is correct "
    "if this is unexpected."
)

SEX_NOTE = {
    "female": (
        "Figures shown are female-specific (biological sex XX): the SEER "
        "female baseline and the BRCA1/2 female-carrier penetrance apply."
    ),
    "male": (
        "Biological sex is XY (male). The female SEER lifetime baseline and "
        "the BRCA1/2 ~69–72% female penetrance figures do not apply to males and are "
        "not shown. Male breast cancer is rare; in male BRCA carriers breast-cancer "
        "risk is far lower than in females, while prostate cancer is a major "
        "sex-specific component (especially BRCA2). Discuss male-specific BRCA risk "
        "and screening with clinical genetics."
    ),
    "unresolved": (
        "Biological sex could not be confidently resolved from your array data, so "
        "sex-specific absolute-risk figures are withheld. Sex-specific breast/prostate "
        "cancer risk differs substantially; use CanRisk / clinical genetics for an "
        "individualized estimate."
    ),
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


def _sex_context(inferred_sex: str | None) -> str:
    """Map an inferred-sex classification to an overlay context.

    ``"XX"`` → ``"female"`` (female SEER baseline + female BRCA penetrance apply).
    ``"XY"`` → ``"male"`` (female figures suppressed; male-specific framing).
    Anything else (``"manual_review"`` / ``"unknown"`` / ``None``) → ``"unresolved"``
    (no numeric sex-specific figures — we cannot safely pick a sex).
    """
    if inferred_sex == "XX":
        return "female"
    if inferred_sex == "XY":
        return "male"
    return "unresolved"


def _monogenic_entries(carriers: list[str], context: str) -> list[dict]:
    """Carrier penetrance entries appropriate to the inferred-sex context.

    The dict shape is identical across contexts (``gene`` + the
    ``cumulative_risk_to_80_pct`` / ``ci`` / ``pmid`` / ``note`` fields the overlay
    renders); only the values differ so a male/unresolved sample never receives the
    female ~69–72% figure.
    """
    entries: list[dict] = []
    for g in carriers:
        if context == "female":
            data = MONOGENIC_PENETRANCE.get(g, _FEMALE_MODERATE_FALLBACK)
        elif context == "male":
            data = MALE_MONOGENIC_PENETRANCE.get(g, _MALE_MODERATE_FALLBACK)
        else:  # unresolved — withhold any sex-specific number
            data = {
                "cumulative_risk_to_80_pct": None,
                "ci": None,
                "pmid": None,
                "note": _UNRESOLVED_CARRIER_NOTE,
            }
        entries.append({"gene": g, **data})
    return entries


def build_breast_absolute_risk(
    sample_engine: sa.Engine,
    *,
    consented: bool,
    inferred_sex: str | None = None,
    recorded_sex: str | None = None,
) -> dict:
    """Build the breast absolute-risk overlay payload.

    Pre-consent: returns only the opt-in prompt + disclaimer (no risk figures).
    Post-consent the figures are **sex-gated**. Biological sex is resolved by
    :func:`backend.services.sex_inference.resolve_biological_sex`, which prefers
    the user-recorded ``individuals.biological_sex`` (``recorded_sex``, an
    authoritative ``XX``/``XY`` value) over the array inference (``inferred_sex``,
    the output of :func:`~backend.services.sex_inference.infer_biological_sex`),
    falling back to inference when no recorded value is set (issue #254). The
    resolved value drives the context:

    * ``"XX"`` → the female SEER baseline + female BRCA1/2 penetrance (labelled
      female-specific);
    * ``"XY"`` → female figures suppressed; male-specific BRCA framing (male breast
      cancer is rare; prostate cancer is the major sex-specific component);
    * ``"manual_review"`` / ``"unknown"`` / ``None`` → no numeric sex-specific figures;
      a handoff to CanRisk / clinical genetics until sex is resolved.

    This prevents a female ~69–72% BRCA penetrance from being shown to an XY/male or
    sex-unresolved sample (gh #151), and uses a recorded sex to resolve the context
    when inference is inconclusive (gh #254).
    """
    if not consented:
        return {
            "consented": False,
            "opt_in_required": True,
            "opt_in_prompt": OPT_IN_PROMPT,
            "disclaimer": DISCLAIMER,
        }

    resolved = resolve_biological_sex(recorded_sex=recorded_sex, inferred_sex=inferred_sex)
    context = _sex_context(resolved.sex)
    carriers = _breast_monogenic_carriers(sample_engine)

    sex_note = SEX_NOTE[context]
    if resolved.conflict:
        sex_note = f"{sex_note} {_SEX_CONFLICT_NOTE}"

    payload = {
        "consented": True,
        "opt_in_required": False,
        "inferred_sex": inferred_sex,
        "recorded_sex": recorded_sex,
        "resolved_sex": resolved.sex,
        "sex_source": resolved.source,
        "sex_conflict": resolved.conflict,
        "sex_context": context,
        "sex_note": sex_note,
        "has_monogenic": bool(carriers),
        "monogenic": _monogenic_entries(carriers, context),
        "prs_note": PRS_NOTE,
        "canrisk": CANRISK,
        "disclaimer": DISCLAIMER,
        "research_use_only": True,
    }
    # The SEER baseline is a *female* lifetime figure — only show it for XX samples.
    if context == "female":
        payload["population_baseline"] = SEER_BASELINE

    return payload

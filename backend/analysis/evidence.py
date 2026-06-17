"""Centralized 4-star evidence level framework (P3-40).

Implements the unified evidence star assignment logic from PRD §3.4.
All analysis modules use these functions instead of duplicating evidence
level logic locally.

Evidence Star Criteria:

  ★★★★ (4) — Definitive / Pathogenic
    ClinVar P/LP with ≥2-star review; CPIC Tier A drug interaction;
    published case-control studies with OR > 5 and p < 5×10⁻⁸.

  ★★★☆ (3) — Strong Evidence
    ClinVar LP with 1-star review; CPIC Tier B; GWAS hit replicated
    in ≥2 independent cohorts (p < 5×10⁻⁸).

  ★★☆☆ (2) — Moderate Evidence
    ClinVar VUS with functional evidence; GWAS hit p < 5×10⁻⁸ in
    single large cohort; PharmGKB level 2A/2B.

  ★☆☆☆ (1) — Preliminary / Exploratory
    Single-study associations; candidate gene studies; PharmGKB
    level 3/4; PRS components.

ClinVar is authoritative — in-silico predictions are shown alongside
but never override ClinVar classifications.

Usage::

    from backend.analysis.evidence import (
        assign_clinvar_evidence_level,
        assign_cpic_evidence_level,
        assign_gwas_evidence_level,
        EVIDENCE_DEFINITIVE,
        EVIDENCE_STRONG,
        EVIDENCE_MODERATE,
        EVIDENCE_PRELIMINARY,
    )

    # ClinVar-based (cancer, cardiovascular, carrier, rare variants)
    level = assign_clinvar_evidence_level(
        clinvar_significance="Pathogenic",
        clinvar_review_stars=2,
    )
    assert level == 4  # ★★★★

    # With gene baseline cap (0 review stars)
    level = assign_clinvar_evidence_level(
        clinvar_significance="Likely pathogenic",
        clinvar_review_stars=0,
        gene_baseline=3,
    )
    assert level == 2  # capped at min(gene_baseline, 2)

    # CPIC-based (pharmacogenomics)
    level = assign_cpic_evidence_level("A")
    assert level == 4  # ★★★★

    # GWAS-based (nutrigenomics, fitness, sleep, etc.)
    level = assign_gwas_evidence_level(p_value=1e-10)
    assert level == 2  # single cohort GWAS = ★★☆☆
"""

from __future__ import annotations

from backend.analysis.clinvar_significance import (
    is_low_penetrance_or_risk_allele,
    primary_pathogenic_classification,
)

# ── Evidence level constants ─────────────────────────────────────────────

EVIDENCE_DEFINITIVE = 4  # ★★★★
EVIDENCE_STRONG = 3  # ★★★☆
EVIDENCE_MODERATE = 2  # ★★☆☆
EVIDENCE_PRELIMINARY = 1  # ★☆☆☆

#: Human-readable labels for each level.
EVIDENCE_LABELS: dict[int, str] = {
    EVIDENCE_DEFINITIVE: "Definitive / Pathogenic",
    EVIDENCE_STRONG: "Strong Evidence",
    EVIDENCE_MODERATE: "Moderate Evidence",
    EVIDENCE_PRELIMINARY: "Preliminary / Exploratory",
}

# ── CPIC classification → star mapping ───────────────────────────────────

_CPIC_STARS: dict[str, int] = {
    "A": EVIDENCE_DEFINITIVE,  # 4
    "B": EVIDENCE_STRONG,  # 3
    "C": EVIDENCE_MODERATE,  # 2
    "D": EVIDENCE_MODERATE,  # 2
}


# ── ClinVar-based evidence assignment ────────────────────────────────────


def assign_clinvar_evidence_level(
    clinvar_significance: str | None,
    clinvar_review_stars: int | None,
    *,
    gene_baseline: int | None = None,
    ensemble_pathogenic: bool = False,
) -> int:
    """Assign evidence level based on ClinVar data.

    Used by cancer, cardiovascular, carrier status, and rare variant
    modules for variants with ClinVar annotations.

    Args:
        clinvar_significance: ClinVar clinical significance string
            (e.g. "Pathogenic", "Likely pathogenic").
        clinvar_review_stars: ClinVar review star count (0-4).
        gene_baseline: Optional gene-level baseline evidence from curated
            panel. When provided and review stars are 0, the result is
            capped at ``min(gene_baseline, 2)``.
        ensemble_pathogenic: Whether >=3 independent in-silico axes predict
            deleterious. Tracked for downstream reporting; this function
            does not currently promote evidence level based on this flag.

    Returns:
        Evidence level integer (1-4).
    """
    primary = primary_pathogenic_classification(clinvar_significance)
    stars = clinvar_review_stars or 0

    if is_low_penetrance_or_risk_allele(clinvar_significance):
        return EVIDENCE_MODERATE

    if primary is not None:
        if stars >= 2:
            return EVIDENCE_DEFINITIVE  # 4

        if stars == 1:
            if primary == "Pathogenic":
                return EVIDENCE_DEFINITIVE  # 4
            return EVIDENCE_STRONG  # 3 — LP with 1 star

        # 0 review stars — cap at gene baseline or MODERATE
        if gene_baseline is not None:
            return min(gene_baseline, EVIDENCE_MODERATE)
        return EVIDENCE_MODERATE  # 2

    # Non-pathogenic ClinVar or absent. In-silico ensemble support is
    # computational — not functional — evidence, so it does NOT promote the
    # finding to MODERATE (★★, reserved for functional/clinical evidence per the
    # PRD rubric, F19). Ensemble-supported VUS/Benign stay PRELIMINARY (★); the
    # ensemble_pathogenic flag is surfaced as its own category, never as ★★.
    return EVIDENCE_PRELIMINARY  # 1


# ── CPIC-based evidence assignment ───────────────────────────────────────


def assign_cpic_evidence_level(classification: str | None) -> int:
    """Assign evidence level based on CPIC tier classification.

    Args:
        classification: CPIC classification letter ("A", "B", "C", "D")
            or None.

    Returns:
        Evidence level integer (2-4). Defaults to MODERATE (2) for
        unknown/None classifications.
    """
    if classification is None:
        return EVIDENCE_MODERATE
    return _CPIC_STARS.get(classification, EVIDENCE_MODERATE)


# ── GWAS-based evidence assignment ───────────────────────────────────────


def assign_gwas_evidence_level(
    *,
    replicated: bool = False,
    p_value: float | None = None,
    odds_ratio: float | None = None,
) -> int:
    """Assign evidence level for GWAS-based findings.

    Args:
        replicated: Whether the GWAS association has been replicated
            in ≥2 independent cohorts.
        p_value: The association p-value. Must be < 5e-8 for genome-wide
            significance.
        odds_ratio: The odds ratio. OR > 5 with genome-wide significance
            reaches ★★★★.

    Returns:
        Evidence level integer (1-4).
    """
    gw_significant = p_value is not None and p_value < 5e-8

    # OR > 5 with genome-wide significance → Definitive
    if gw_significant and odds_ratio is not None and odds_ratio > 5:
        return EVIDENCE_DEFINITIVE  # 4

    # Replicated GWAS hit → Strong
    if replicated and gw_significant:
        return EVIDENCE_STRONG  # 3

    # Single cohort GWAS hit → Moderate
    if gw_significant:
        return EVIDENCE_MODERATE  # 2

    # Sub-threshold or candidate gene → Preliminary
    return EVIDENCE_PRELIMINARY  # 1


# ── Fixed evidence levels for specific module types ──────────────────────

# PRS components are always ★☆☆☆ per PRD §3.4
PRS_EVIDENCE_LEVEL = EVIDENCE_PRELIMINARY  # 1

# PCA-based ancestry inference is ★★☆☆
ANCESTRY_EVIDENCE_LEVEL = EVIDENCE_MODERATE  # 2

# Traits & Personality hard cap at ★★☆☆ per PRD §3.4b
TRAITS_EVIDENCE_CAP = EVIDENCE_MODERATE  # 2


def cap_evidence_level(level: int, cap: int) -> int:
    """Cap an evidence level at a maximum value.

    Used by modules with hard evidence caps (e.g. Traits & Personality
    capped at ★★☆☆).

    Args:
        level: Computed evidence level.
        cap: Maximum allowed evidence level.

    Returns:
        ``min(level, cap)``
    """
    return min(level, cap)

"""Array-genotyping confidence (Weedon-PPV reliability badge).

EXPANSION_STRATEGY second-wave SW-A11 / roadmap #14. A genotyping array confirms
*common* variants almost perfectly but is increasingly unreliable as a variant
gets rarer — Weedon 2021 (BMJ; PMID 33589468) found array calls for common
variants concordant with sequencing >99% of the time, yet only ~16% of array
calls for variants rarer than 0.001% were confirmed, and ~4% for very rare
ClinVar Pathogenic/Likely-pathogenic variants in BRCA1/BRCA2.

This module reads the Phase-F annotation columns ``annotated_variants
.gnomad_af_popmax`` (F15) and the F12 catalogue signals and turns them into a
per-finding **reliability flag** for actionable ClinVar P/LP findings.

This is a **reliability flag only** (mirrors the gene-constraint badge in
``backend.analysis.gene_constraint``): it NEVER changes a finding's
``evidence_level`` or ``clinvar_significance``. A low-reliability flag does not
make a true call false — it means an array call at that frequency should be
confirmed in a CLIA/accredited lab before any medical action (the same
responsible-return framing as ``backend.analysis.return_framing.CLIA_CONFIRMATION``).

This module ships the Weedon-PPV half of SW-A11. The companion ClinGen 6-tier
gene-disease-validity half ships in ``backend.analysis.gene_validity`` (surfaced
at ``GET /api/analysis/gene-validity``) — both are guardrail flags on the same
actionable ClinVar P/LP findings and neither ever changes a classification.
"""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa

from backend.analysis.rare_variant_finder import PATHOGENIC_SIGNIFICANCE
from backend.db.tables import annotated_variants, findings
from backend.disclaimers import ARRAY_CONFIDENCE_CONTEXT_ONLY

# Weedon 2021 anchors (BMJ 2021;372:n214).
WEEDON_PMID = "33589468"

# Allele-frequency band edges (popmax AF). Array reliability is excellent above
# ~0.1% and collapses below ~0.001% (the frequency at which Weedon's PPV fell to
# ~16%); the band in between declines steadily.
COMMON_AF_MIN = 1e-3  # 0.1% — Weedon: >99% concordance for common variants
RARE_AF_MIN = 1e-5  # 0.001% — below this, PPV collapses to ~16%

RELIABILITY_HIGH = "high"
RELIABILITY_MODERATE = "moderate"
RELIABILITY_LOW = "low"
RELIABILITY_VERY_LOW = "very_low"
RELIABILITY_UNKNOWN = "unknown"

# (label, detail, confirm_in_clia_recommended) per band.
_BAND_COPY: dict[str, tuple[str, str, bool]] = {
    RELIABILITY_HIGH: (
        "High array reliability",
        "Common on a population scale (popmax allele frequency ≥ 0.1%). Weedon 2021 "
        "found genotyping-array calls for common variants were confirmed by sequencing "
        "more than 99% of the time.",
        False,
    ),
    RELIABILITY_MODERATE: (
        "Moderate array reliability",
        "Rare (popmax allele frequency 0.001%–0.1%). Array reliability declines steadily "
        "with rarity in this range; orthogonal confirmation is advisable before acting on "
        "the call.",
        True,
    ),
    RELIABILITY_LOW: (
        "Low array reliability",
        "Very rare (popmax allele frequency < 0.001%). Weedon 2021 confirmed only ~16% of "
        "array calls at this frequency by sequencing (≈4% for ClinVar P/LP variants in "
        "BRCA1/BRCA2) — most were false positives. Confirm in a CLIA/accredited lab before "
        "any medical decision.",
        True,
    ),
    RELIABILITY_VERY_LOW: (
        "Very low array reliability (uncatalogued call)",
        "Absent from gnomAD and not catalogued in dbSNP or ClinVar. Array genotype clusters "
        "are calibrated on observed common genotypes, so a never-before-seen call is largely "
        "unvalidated and usually a false positive. Confirm in a CLIA/accredited lab before "
        "any medical decision.",
        True,
    ),
    RELIABILITY_UNKNOWN: (
        "Array reliability not assessable from frequency",
        "No population allele frequency is available for this variant, so the Weedon "
        "frequency–reliability relationship cannot be applied. Absence of a frequency is not "
        "evidence of reliability; confirm an actionable call in a CLIA/accredited lab.",
        True,
    ),
}


def _is_catalogued(
    rsid: str | None,
    clinvar_significance: str | None,
    clinvar_accession: str | None,
) -> bool:
    """Whether a variant is recorded in a public catalogue (F12).

    Mirrors ``rare_variant_finder.RareVariantResult.is_catalogued``: a dbSNP
    ``rs`` identifier or any ClinVar record is positive evidence of prior
    description, so the variant is not novel even when gnomAD lacks a frequency.
    The ``rs`` check is case-insensitive to be robust to mixed-case rsids.
    """
    has_dbsnp_rsid = bool(rsid) and rsid.lower().startswith("rs")
    has_clinvar = clinvar_significance is not None or clinvar_accession is not None
    return has_dbsnp_rsid or has_clinvar


def _af_unavailable(popmax_af: float | None) -> bool:
    """A frequency is unusable when it is missing or invalid (negative).

    A popmax AF is a frequency in [0, 1]; a negative value can only mean upstream
    corruption. Fail-safe: treat it as "no frequency" rather than as a confident
    rare-variant call, so it never lands in a band that implies reliability.
    """
    return popmax_af is None or popmax_af < 0


def classify_array_reliability(popmax_af: float | None, is_catalogued: bool) -> str:
    """Map popmax AF + catalogue status to a Weedon reliability band.

    Fail-safe: when no usable frequency is available (missing or invalid) we never
    assume "common/reliable" — a catalogued variant with no AF is ``unknown`` (not
    assessable) and an uncatalogued one is ``very_low``.
    """
    if _af_unavailable(popmax_af):
        return RELIABILITY_UNKNOWN if is_catalogued else RELIABILITY_VERY_LOW
    if popmax_af >= COMMON_AF_MIN:
        return RELIABILITY_HIGH
    if popmax_af >= RARE_AF_MIN:
        return RELIABILITY_MODERATE
    return RELIABILITY_LOW


def array_confidence_badge(popmax_af: float | None, is_catalogued: bool) -> dict[str, Any]:
    """Build the reliability badge for one variant. Reliability flag only."""
    band = classify_array_reliability(popmax_af, is_catalogued)
    label, detail, confirm = _BAND_COPY[band]
    return {
        "reliability": band,
        "label": label,
        "detail": detail,
        "gnomad_af_popmax": popmax_af,
        "is_novel": _af_unavailable(popmax_af) and not is_catalogued,
        "confirm_in_clia_recommended": confirm,
        "context_only": True,
        "pmid_citations": [WEEDON_PMID],
        "note": ARRAY_CONFIDENCE_CONTEXT_ONLY,
    }


def assess_pathogenic_findings(sample_engine: sa.Engine) -> list[dict[str, Any]]:
    """Reliability badge for every actionable ClinVar P/LP finding in a sample.

    Left-joins ``findings`` to ``annotated_variants`` on ``rsid`` so a P/LP
    finding whose variant was not annotated still receives a badge (popmax AF
    unknown). Read-only — no finding storage is mutated.

    These findings carry a ClinVar P/LP classification and are therefore
    catalogued by definition, so every row here is ``is_novel=False`` and the
    ``very_low`` (uncatalogued) band is unreachable through this endpoint — that
    band exists for future callers (e.g. SW-F1) that classify uncatalogued
    candidate variants. The worst reachable band here is ``low`` (very rare but
    catalogued), which carries the headline Weedon warning.
    """
    av = annotated_variants
    join = findings.join(av, findings.c.rsid == av.c.rsid, isouter=True)
    stmt = (
        sa.select(
            findings.c.id,
            findings.c.module,
            findings.c.gene_symbol,
            findings.c.rsid,
            findings.c.clinvar_significance,
            findings.c.finding_text,
            av.c.gnomad_af_popmax,
            av.c.clinvar_significance.label("av_clinvar_significance"),
            av.c.clinvar_accession.label("av_clinvar_accession"),
        )
        .select_from(join)
        .where(findings.c.clinvar_significance.in_(sorted(PATHOGENIC_SIGNIFICANCE)))
        .order_by(findings.c.id)
    )
    with sample_engine.connect() as conn:
        rows = conn.execute(stmt).fetchall()

    out: list[dict[str, Any]] = []
    for row in rows:
        catalogued = _is_catalogued(
            row.rsid,
            row.clinvar_significance or row.av_clinvar_significance,
            row.av_clinvar_accession,
        )
        badge = array_confidence_badge(row.gnomad_af_popmax, catalogued)
        out.append(
            {
                "finding_id": row.id,
                "module": row.module,
                "gene_symbol": row.gene_symbol,
                "rsid": row.rsid,
                "clinvar_significance": row.clinvar_significance,
                "finding_text": row.finding_text,
                **badge,
            }
        )
    return out

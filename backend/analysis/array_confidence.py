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

Frequency is not the whole story: some loci are genotyping-array weak spots
regardless of how common they are (e.g. the APOE ε-defining SNPs rs429358/rs7412
— common, yet absent from most arrays and only ~90–93% concordant with direct
genotyping). ``_LOCUS_LOW_RELIABILITY`` is a small, cited rsID allow-list that
overrides the frequency band down to ``locus_low`` for such loci (#636).

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

from backend.analysis.clinvar_significance import pathogenic_significance_filter
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
# Locus-specific weak spot (#636): a variant poorly typed on arrays for reasons
# unrelated to its frequency, so the Weedon frequency→reliability bands do not
# apply. A listed locus is rated no better than this regardless of allele
# frequency (a *common* variant can still be mistyped — see ``_LOCUS_LOW_RELIABILITY``).
RELIABILITY_LOCUS_LOW = "locus_low"

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
    RELIABILITY_LOCUS_LOW: (
        "Low array reliability (locus-specific)",
        "This variant is a documented genotyping-array weak spot independent of its "
        "population frequency — a variant that is common can still be mistyped here, so "
        "the Weedon frequency–reliability relationship does not apply. Confirm an "
        "actionable call in a CLIA/accredited lab before any medical decision.",
        True,
    ),
}

# ── Locus-specific low-reliability overrides (#636) ─────────────────────────
#
# Rating array reliability by allele frequency alone (the Weedon bands above)
# misses loci that are genotyping-array weak spots for reasons unrelated to
# frequency: a *common* variant can still be poorly typed. The leading example is
# the APOE ε-defining pair rs429358/rs7412 — ε4 is common (~15%) so a
# frequency-only model rates them "high reliability", yet both are absent from
# most genome-wide arrays and only imperfectly captured on common platforms, with
# array/imputed-vs-direct concordance only ~90% (ε genotype) / ~93% (ε4 status).
# A listed rsID is rated ``locus_low`` regardless of its frequency band.
#
# Provenance (verified against the literature):
#   - Oldmeadow 2014 (PMID 24903779): 90% agreement for ε2/ε3/ε4 genotypes and
#     93% for ε4 status between directly measured and imputed APOE.
#   - Radmanesh 2014 (PMID 24448547), Lill 2012 (PMID 22972946): rs429358/rs7412
#     absent from / imperfectly captured on common genotyping arrays.
# (Surfaced by #557; the APOE module's own caveat — #625 — sources its citations
# from ``APOE_ARRAY_RELIABILITY_PMIDS`` below so the two never drift.)

# Canonical APOE ε-SNP array-reliability citations (single source of truth,
# also consumed by ``backend.analysis.apoe``).
APOE_ARRAY_RELIABILITY_PMIDS = ["24448547", "22972946", "24903779"]

# Single source of truth for the APOE array-vs-direct concordance figure (Oldmeadow
# 2014). Consumed by the locus reason below AND by the APOE module's reliability
# flag (``apoe._apoe_array_reliability_flag``) so the figure lives in one place.
APOE_ARRAY_CONCORDANCE = "~90% ε genotype / ~93% ε4 status"

_APOE_LOCUS_LOW_REASON = (
    "APOE ε-defining SNP — absent from most genome-wide genotyping arrays and only "
    "imperfectly captured on common platforms; array/imputed-vs-direct concordance is "
    f"only {APOE_ARRAY_CONCORDANCE}, so even a common ε4 call can be wrong."
)

# rsID (lowercase) → {"reason": locus-specific explanation, "pmids": citations}.
_LOCUS_LOW_RELIABILITY: dict[str, dict[str, Any]] = {
    "rs429358": {"reason": _APOE_LOCUS_LOW_REASON, "pmids": APOE_ARRAY_RELIABILITY_PMIDS},
    "rs7412": {"reason": _APOE_LOCUS_LOW_REASON, "pmids": APOE_ARRAY_RELIABILITY_PMIDS},
}


def _locus_low_entry(rsid: str | None) -> dict[str, Any] | None:
    """The locus-specific low-reliability registry entry for ``rsid``, if any.

    Case-insensitive on the rsID so a mixed-case identifier still matches.
    """
    if not rsid:
        return None
    return _LOCUS_LOW_RELIABILITY.get(rsid.lower())


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


def classify_array_reliability(
    popmax_af: float | None, is_catalogued: bool, rsid: str | None = None
) -> str:
    """Map popmax AF + catalogue status to a Weedon reliability band.

    Fail-safe: when no usable frequency is available (missing or invalid) we never
    assume "common/reliable" — a catalogued variant with no AF is ``unknown`` (not
    assessable) and an uncatalogued one is ``very_low``.

    Locus override (#636): a variant on the ``_LOCUS_LOW_RELIABILITY`` list is a
    documented array weak spot independent of its frequency, so it is rated
    ``locus_low`` regardless of AF — the frequency bands below never apply to it.
    """
    if _locus_low_entry(rsid) is not None:
        return RELIABILITY_LOCUS_LOW
    if _af_unavailable(popmax_af):
        return RELIABILITY_UNKNOWN if is_catalogued else RELIABILITY_VERY_LOW
    if popmax_af >= COMMON_AF_MIN:
        return RELIABILITY_HIGH
    if popmax_af >= RARE_AF_MIN:
        return RELIABILITY_MODERATE
    return RELIABILITY_LOW


def array_confidence_badge(
    popmax_af: float | None, is_catalogued: bool, rsid: str | None = None
) -> dict[str, Any]:
    """Build the reliability badge for one variant. Reliability flag only.

    When ``rsid`` is on the locus-specific low-reliability list (#636) the band is
    ``locus_low``: the badge carries the locus-specific reason + citations and a
    ``frequency_band`` showing what allele frequency alone would have rated it
    (e.g. ``high`` for common APOE ε-SNPs), so the override is transparent.
    """
    band = classify_array_reliability(popmax_af, is_catalogued, rsid)
    label, detail, confirm = _BAND_COPY[band]
    pmids = [WEEDON_PMID]
    locus_entry = _locus_low_entry(rsid)
    frequency_band: str | None = None
    if band == RELIABILITY_LOCUS_LOW and locus_entry is not None:
        detail = f"{detail} {locus_entry['reason']}"
        # Locus-specific citations lead; keep the Weedon anchor, drop duplicates.
        pmids = list(dict.fromkeys([*locus_entry["pmids"], WEEDON_PMID]))
        # What the frequency-only model would have said (for transparency).
        frequency_band = classify_array_reliability(popmax_af, is_catalogued)
    return {
        "reliability": band,
        "label": label,
        "detail": detail,
        "gnomad_af_popmax": popmax_af,
        "is_novel": _af_unavailable(popmax_af) and not is_catalogued,
        "confirm_in_clia_recommended": confirm,
        "context_only": True,
        "pmid_citations": pmids,
        "note": ARRAY_CONFIDENCE_CONTEXT_ONLY,
        "locus_low_reliability": band == RELIABILITY_LOCUS_LOW,
        "frequency_band": frequency_band,
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
    candidate variants. The worst frequency-derived band here is ``low`` (very
    rare but catalogued), which carries the headline Weedon warning; a finding at
    a locus-specific weak spot (#636) is instead rated ``locus_low`` regardless of
    its frequency.
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
        .where(pathogenic_significance_filter(findings.c.clinvar_significance))
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
        badge = array_confidence_badge(row.gnomad_af_popmax, catalogued, row.rsid)
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

"""ClinGen gene-disease validity guardrail (SW-A11 / roadmap #14).

The second half of SW-A11 (the Weedon array-reliability half ships in
:mod:`backend.analysis.array_confidence`). ClinGen gene-disease *validity*
(Strande 2017, PMID 28552198) grades how strong the evidence is that a gene
causes a disease at all — Definitive / Strong / Moderate / Limited / Disputed /
Refuted / No Known Disease Relationship. This is orthogonal to a variant's ACMG
pathogenicity: a confidently-called Pathogenic variant in a gene whose
disease relationship is only *Limited* (or actively *Disputed*/*Refuted*)
warrants caution, because such variants show markedly lower observed penetrance
(Thaxton 2022, PMID 34694049; population-cohort evidence).

This is a **guardrail flag only** (mirrors :mod:`backend.analysis.gene_constraint`
and :mod:`backend.analysis.array_confidence`): it NEVER changes a finding's
``evidence_level`` or ``clinvar_significance``. A weak-validity flag does not make
a true call false — it means an actionable call in a poorly-validated gene should
be confirmed and counselled clinically before any medical action.
"""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa

from backend.analysis.clinvar_significance import pathogenic_significance_filter
from backend.annotation.clingen import lookup_gene_validities
from backend.disclaimers import GENE_VALIDITY_CONTEXT_ONLY

# Strande 2017 framework + Thaxton 2022 (validity → variant interpretation).
CLINGEN_FRAMEWORK_PMID = "28552198"
CLINGEN_VARIANT_INTERP_PMID = "34694049"

# Ordering by strength of support for a gene→disease relationship (higher is a
# stronger relationship). "No Known Disease Relationship" sits above the
# contradicted tiers (Disputed/Refuted carry evidence *against* the relationship).
_CLASSIFICATION_RANK: dict[str, int] = {
    "Definitive": 6,
    "Strong": 5,
    "Moderate": 4,
    "Limited": 3,
    "No Known Disease Relationship": 2,
    "Disputed": 1,
    "Refuted": 0,
}

# A gene-disease relationship is "established" — strong enough to act on per ACMG
# technical standards for diagnostic panels — at Moderate or above.
ESTABLISHED_CLASSIFICATIONS = frozenset({"Definitive", "Strong", "Moderate"})

_CONTRADICTED = frozenset({"Disputed", "Refuted"})


def classification_rank(classification: str | None) -> int:
    """Rank a classification by relationship strength (unknown → -1)."""
    if classification is None:
        return -1
    return _CLASSIFICATION_RANK.get(classification, -1)


def is_established(classification: str | None) -> bool:
    """Whether a classification is Moderate-or-stronger (actionable validity)."""
    return classification in ESTABLISHED_CLASSIFICATIONS


def best_curation(curations: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The single most-supportive curation for a gene (highest rank), or None."""
    if not curations:
        return None
    return max(curations, key=lambda c: classification_rank(c.get("classification")))


def _guardrail_copy(best: dict[str, Any]) -> tuple[str, str]:
    """(label, detail) for the headline guardrail given the best curation."""
    classification = best.get("classification") or ""
    disease = best.get("disease_label") or "the associated disease"
    if classification in ESTABLISHED_CLASSIFICATIONS:
        return (
            f"Established gene-disease validity ({classification})",
            f"ClinGen classifies the gene's strongest disease relationship "
            f"({disease}) as {classification} — established evidence that this gene "
            f"causes disease. Supportive background only; it does not change the "
            f"finding's classification.",
        )
    if classification == "Limited":
        return (
            "Limited gene-disease validity",
            "ClinGen's strongest classification for this gene is Limited — the "
            "evidence that the gene causes disease is insufficient. Interpret a "
            "Pathogenic/Likely-pathogenic call with caution: variants in "
            "Limited-validity genes show markedly lower observed penetrance. "
            "Confirm and counsel clinically before any action.",
        )
    if classification in _CONTRADICTED:
        return (
            f"Contradicted gene-disease validity ({classification})",
            f"ClinGen classifies the gene's disease relationship as {classification} "
            f"— there is conflicting or contradictory evidence against this gene "
            f"causing disease. A Pathogenic/Likely-pathogenic call here warrants "
            f"strong caution and clinical confirmation.",
        )
    # No Known Disease Relationship
    return (
        "No known gene-disease relationship",
        "ClinGen found no known disease relationship for this gene. A "
        "Pathogenic/Likely-pathogenic call here should be interpreted cautiously "
        "and confirmed clinically.",
    )


def gene_validity_guardrail(
    gene_symbol: str | None, curations: list[dict[str, Any]]
) -> dict[str, Any] | None:
    """Build the gene-validity guardrail for one gene, or None if uncurated.

    Returns ``None`` (no error) when the gene has no ClinGen curation, so callers
    treat "no curation" as "not evaluated" — never as "no disease relationship".
    """
    if not gene_symbol or not curations:
        return None
    best = best_curation(curations)
    classification = best.get("classification") if best else None
    established = is_established(classification)
    label, detail = _guardrail_copy(best) if best else ("", "")
    return {
        "gene_symbol": gene_symbol,
        "has_clingen_curation": True,
        "best_classification": classification,
        "validity_established": established,
        # ``caution`` is the actionable signal: the relationship is not established.
        "caution": not established,
        "label": label,
        "detail": detail,
        "curations": curations,
        "context_only": True,
        "note": GENE_VALIDITY_CONTEXT_ONLY,
        "pmid_citations": [CLINGEN_FRAMEWORK_PMID, CLINGEN_VARIANT_INTERP_PMID],
    }


def _uncurated_guardrail(gene_symbol: str | None) -> dict[str, Any]:
    """Honest placeholder for an actionable finding whose gene ClinGen has not curated."""
    return {
        "gene_symbol": gene_symbol,
        "has_clingen_curation": False,
        "best_classification": None,
        "validity_established": False,
        "caution": False,  # absence of curation is not evidence of weak validity
        "label": "Gene-disease validity not curated by ClinGen",
        "detail": (
            "ClinGen has not published a gene-disease validity classification for "
            "this gene. Absence of a curation is not evidence either way."
        ),
        "curations": [],
        "context_only": True,
        "note": GENE_VALIDITY_CONTEXT_ONLY,
        "pmid_citations": [CLINGEN_FRAMEWORK_PMID],
    }


def assess_finding_gene_validity(
    sample_engine: sa.Engine, reference_engine: sa.Engine
) -> list[dict[str, Any]]:
    """Gene-validity guardrail for every actionable ClinVar P/LP finding.

    Read-only. Selects the same actionable Pathogenic / Likely-pathogenic findings
    as :func:`backend.analysis.array_confidence.assess_pathogenic_findings`, then
    attaches each finding's gene-level ClinGen validity guardrail (or an honest
    "not curated" placeholder). Never mutates findings.
    """
    from backend.db.tables import findings

    stmt = (
        sa.select(
            findings.c.id,
            findings.c.module,
            findings.c.gene_symbol,
            findings.c.rsid,
            findings.c.clinvar_significance,
            findings.c.finding_text,
        )
        .where(pathogenic_significance_filter(findings.c.clinvar_significance))
        .order_by(findings.c.id)
    )
    with sample_engine.connect() as conn:
        rows = conn.execute(stmt).fetchall()

    genes = [row.gene_symbol for row in rows if row.gene_symbol]
    validities = lookup_gene_validities(reference_engine, genes)

    out: list[dict[str, Any]] = []
    for row in rows:
        curations = validities.get(row.gene_symbol or "", [])
        guardrail = gene_validity_guardrail(row.gene_symbol, curations) or _uncurated_guardrail(
            row.gene_symbol
        )
        out.append(
            {
                "finding_id": row.id,
                "module": row.module,
                "rsid": row.rsid,
                "clinvar_significance": row.clinvar_significance,
                "finding_text": row.finding_text,
                **guardrail,
            }
        )
    return out

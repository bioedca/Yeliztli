"""ClinGen gene-disease validity guardrail (SW-A11 / roadmap #14).

The second half of SW-A11 (the Weedon array-reliability half ships in
:mod:`backend.analysis.array_confidence`). ClinGen gene-disease *validity*
(Strande 2017, PMID 28552198) grades how strong the evidence is that a gene
causes a particular disease — Definitive / Strong / Moderate / Limited /
Disputed / Refuted / No Known Disease Relationship. This is orthogonal to a
variant's ACMG pathogenicity: a confidently-called Pathogenic variant in a gene
whose relevant disease relationship is only *Limited* (or actively
*Disputed*/*Refuted*) warrants caution.

This is a **guardrail flag only** (mirrors :mod:`backend.analysis.gene_constraint`
and :mod:`backend.analysis.array_confidence`): it NEVER changes a finding's
``evidence_level`` or ``clinvar_significance``. A weak-validity flag does not make
a true call false — it means an actionable call for a poorly validated
gene-disease relationship should be confirmed and counselled clinically before
any medical action.
"""

from __future__ import annotations

import re
from typing import Any

import sqlalchemy as sa

from backend.analysis.clinvar_conditions import format_clinvar_conditions
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
_DISEASE_TOKEN_RE = re.compile(r"[a-z0-9]+")
_DISEASE_STOPWORDS = frozenset(
    {
        "and",
        "condition",
        "disease",
        "disorder",
        "of",
        "or",
        "phenotype",
        "predisposition",
        "related",
        "syndrome",
        "susceptibility",
        "the",
        "type",
    }
)
_DISEASE_TOKEN_ALIASES = {
    "cancers": "cancer",
    "carcinoma": "cancer",
    "carcinomas": "cancer",
    "malignancies": "cancer",
    "malignancy": "cancer",
    "neoplasm": "cancer",
    "neoplasms": "cancer",
    "tumor": "cancer",
    "tumors": "cancer",
    "tumour": "cancer",
    "tumours": "cancer",
}
_CANCER_TOKENS = frozenset({"cancer", "carcinoma", "malignancy", "neoplasm", "tumor", "tumour"})
_UNINFORMATIVE_CONDITIONS = frozenset(
    {
        "",
        "multiple conditions",
        "not provided",
        "not reported",
        "not specified",
        "not supplied",
        "unknown",
    }
)


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


def _condition_parts(conditions: str | None) -> list[str]:
    """Split a finding condition string into informative disease-context chunks."""
    parts = format_clinvar_conditions(conditions)
    out = []
    for part in parts:
        lowered = part.lower()
        if lowered and lowered not in _UNINFORMATIVE_CONDITIONS:
            out.append(part)
    return out


def _normalise_disease_token(token: str) -> str:
    return _DISEASE_TOKEN_ALIASES.get(token, token)


def _disease_tokens(value: str | None) -> frozenset[str]:
    """Normalize disease labels enough for conservative exact-ish matching."""
    if not value:
        return frozenset()
    return frozenset(
        _normalise_disease_token(token)
        for token in _DISEASE_TOKEN_RE.findall(value.lower())
        if token not in _DISEASE_STOPWORDS
    )


def _raw_disease_tokens(value: str | None) -> frozenset[str]:
    if not value:
        return frozenset()
    return frozenset(_DISEASE_TOKEN_RE.findall(value.lower()))


def _has_cancer_context(tokens: frozenset[str]) -> bool:
    return bool({_normalise_disease_token(token) for token in tokens} & {"cancer"})


def _is_gene_related_cancer_predisposition(
    curation: dict[str, Any], gene_symbol: str | None
) -> bool:
    """Broad ClinGen labels like BRCA1-related cancer predisposition."""
    tokens = _raw_disease_tokens(curation.get("disease_label"))
    gene = (gene_symbol or curation.get("gene_symbol") or "").strip().lower()
    return (
        bool(gene and gene in tokens)
        and "related" in tokens
        and bool(tokens & _CANCER_TOKENS)
        and bool(tokens & {"predisposition", "susceptibility"})
    )


def _disease_context_matches(
    curation: dict[str, Any], condition_part: str, gene_symbol: str | None
) -> bool:
    """Whether one finding condition plausibly names the curation's disease."""
    curation_tokens = _disease_tokens(curation.get("disease_label"))
    condition_tokens = _disease_tokens(condition_part)
    if not curation_tokens or not condition_tokens:
        return False
    if curation_tokens == condition_tokens:
        return True
    if _is_gene_related_cancer_predisposition(curation, gene_symbol) and _has_cancer_context(
        condition_tokens
    ):
        return True
    shared = curation_tokens & condition_tokens
    return (
        len(shared) >= 2 and len(shared) / max(len(curation_tokens), len(condition_tokens)) >= 0.8
    )


def _matching_curations(
    gene_symbol: str | None, curations: list[dict[str, Any]], conditions: str | None
) -> list[dict[str, Any]]:
    """Curations whose disease label matches the finding's disease context."""
    parts = _condition_parts(conditions)
    if not parts:
        return []
    return [
        curation
        for curation in curations
        if any(_disease_context_matches(curation, part, gene_symbol) for part in parts)
    ]


def _has_mixed_established_status(curations: list[dict[str, Any]]) -> bool:
    classifications = [c.get("classification") for c in curations]
    has_established = any(is_established(c) for c in classifications)
    has_unestablished = any(not is_established(c) for c in classifications)
    return has_established and has_unestablished


def _curation_disease_summary(curations: list[dict[str, Any]]) -> str:
    labels = [
        f"{c.get('disease_label') or 'unspecified disease'} ({c.get('classification')})"
        for c in curations
    ]
    return "; ".join(labels)


def _guardrail_copy(best: dict[str, Any]) -> tuple[str, str]:
    """(label, detail) for the headline guardrail given the best curation."""
    classification = best.get("classification") or ""
    disease = best.get("disease_label") or "the associated disease"
    if classification in ESTABLISHED_CLASSIFICATIONS:
        return (
            f"Established gene-disease validity ({classification})",
            f"ClinGen classifies the selected disease relationship "
            f"({disease}) as {classification} — established evidence that this gene "
            f"causes that disease. Supportive background only; it does not change the "
            f"finding's classification.",
        )
    if classification == "Limited":
        return (
            "Limited gene-disease validity",
            "ClinGen's selected disease-specific classification for this gene is "
            "Limited — the evidence that the gene causes that disease is "
            "insufficient. Interpret a Pathogenic/Likely-pathogenic call with "
            "caution. Confirm and counsel clinically before any action.",
        )
    if classification in _CONTRADICTED:
        return (
            f"Contradicted gene-disease validity ({classification})",
            f"ClinGen classifies the gene's disease relationship as {classification} "
            f"— there is conflicting or contradictory evidence against this "
            f"gene-disease relationship. A Pathogenic/Likely-pathogenic call here "
            f"warrants strong caution and clinical confirmation.",
        )
    # No Known Disease Relationship
    return (
        "No known gene-disease relationship",
        "ClinGen found no known disease relationship for this gene context. A "
        "Pathogenic/Likely-pathogenic call here should be interpreted cautiously "
        "and confirmed clinically.",
    )


def _unresolved_context_guardrail(
    gene_symbol: str, curations: list[dict[str, Any]], disease_context_match: str
) -> dict[str, Any]:
    """Caution when no disease context can select among mixed validity curations."""
    disease_summary = _curation_disease_summary(curations)
    return {
        "gene_symbol": gene_symbol,
        "has_clingen_curation": True,
        "best_classification": None,
        "validity_established": False,
        "caution": True,
        "label": "Disease-specific gene validity context unresolved",
        "detail": (
            "ClinGen gene-disease validity is disease-specific, and this gene has "
            f"both established and non-established disease curations: {disease_summary}. "
            "Because this finding does not identify which curated disease relationship "
            "applies, do not treat an established curation for another disease as "
            "reassurance for this finding. Confirm the disease context clinically "
            "before any action."
        ),
        "disease_context": None,
        "disease_context_match": disease_context_match,
        "matched_disease_label": None,
        "curations": curations,
        "context_only": True,
        "note": GENE_VALIDITY_CONTEXT_ONLY,
        "pmid_citations": [CLINGEN_FRAMEWORK_PMID, CLINGEN_VARIANT_INTERP_PMID],
    }


def _mixed_matched_context_guardrail(
    gene_symbol: str,
    curations: list[dict[str, Any]],
    matched_curations: list[dict[str, Any]],
    disease_context: str,
) -> dict[str, Any]:
    """Caution when multiple matched disease contexts have mixed validity."""
    disease_summary = _curation_disease_summary(matched_curations)
    return {
        "gene_symbol": gene_symbol,
        "has_clingen_curation": True,
        "best_classification": None,
        "validity_established": False,
        "caution": True,
        "label": "Multiple matched ClinGen disease-validity contexts",
        "detail": (
            f"This finding is reported for {disease_context}, which matches multiple "
            "ClinGen disease-specific curations with both established and "
            f"non-established validity: {disease_summary}. Do not treat the "
            "established matched curation as reassurance for the weaker matched "
            "disease context. Confirm the disease context clinically before any "
            "action."
        ),
        "disease_context": disease_context,
        "disease_context_match": "matched_mixed",
        "matched_disease_label": None,
        "curations": curations,
        "context_only": True,
        "note": GENE_VALIDITY_CONTEXT_ONLY,
        "pmid_citations": [CLINGEN_FRAMEWORK_PMID, CLINGEN_VARIANT_INTERP_PMID],
    }


def _unmatched_context_guardrail(
    gene_symbol: str, curations: list[dict[str, Any]], disease_context: str
) -> dict[str, Any]:
    """Caution when a finding names a disease but no curation matches it."""
    disease_summary = _curation_disease_summary(curations)
    return {
        "gene_symbol": gene_symbol,
        "has_clingen_curation": True,
        "best_classification": None,
        "validity_established": False,
        "caution": True,
        "label": "No matching ClinGen disease-specific validity curation",
        "detail": (
            f"This finding is reported for {disease_context}, but ClinGen's "
            f"curations for {gene_symbol} are disease-specific and none match that "
            f"context: {disease_summary}. Do not treat an established curation for "
            "another disease as established validity for this finding. Confirm the "
            "disease context clinically before any action."
        ),
        "disease_context": disease_context,
        "disease_context_match": "unmatched",
        "matched_disease_label": None,
        "curations": curations,
        "context_only": True,
        "note": GENE_VALIDITY_CONTEXT_ONLY,
        "pmid_citations": [CLINGEN_FRAMEWORK_PMID, CLINGEN_VARIANT_INTERP_PMID],
    }


def gene_validity_guardrail(
    gene_symbol: str | None,
    curations: list[dict[str, Any]],
    disease_context: str | None = None,
) -> dict[str, Any] | None:
    """Build the gene-validity guardrail for one gene, or None if uncurated.

    Returns ``None`` (no error) when the gene has no ClinGen curation, so callers
    treat "no curation" as "not evaluated" — never as "no disease relationship".
    """
    if not gene_symbol or not curations:
        return None
    context_parts = _condition_parts(disease_context)
    matches = _matching_curations(gene_symbol, curations, disease_context)
    if matches:
        if _has_mixed_established_status(matches):
            return _mixed_matched_context_guardrail(
                gene_symbol, curations, matches, disease_context or ""
            )
        best = best_curation(matches)
        disease_context_match = "matched"
    elif context_parts:
        return _unmatched_context_guardrail(gene_symbol, curations, disease_context or "")
    elif _has_mixed_established_status(curations):
        return _unresolved_context_guardrail(gene_symbol, curations, "unresolved")
    else:
        best = best_curation(curations)
        disease_context_match = "not_provided"

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
        "disease_context": disease_context,
        "disease_context_match": disease_context_match,
        "matched_disease_label": best.get("disease_label") if best and matches else None,
        "curations": curations,
        "context_only": True,
        "note": GENE_VALIDITY_CONTEXT_ONLY,
        "pmid_citations": [CLINGEN_FRAMEWORK_PMID, CLINGEN_VARIANT_INTERP_PMID],
    }


def _uncurated_guardrail(
    gene_symbol: str | None, disease_context: str | None = None
) -> dict[str, Any]:
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
        "disease_context": disease_context,
        "disease_context_match": "uncurated",
        "matched_disease_label": None,
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
    attaches each finding's disease-context-aware ClinGen validity guardrail (or
    an honest "not curated" placeholder). Never mutates findings.
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
            findings.c.conditions,
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
        guardrail = gene_validity_guardrail(
            row.gene_symbol, curations, row.conditions
        ) or _uncurated_guardrail(row.gene_symbol, row.conditions)
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

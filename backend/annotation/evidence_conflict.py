"""Evidence conflict detection — amber flag logic.

Fires when ClinVar classifies a variant as VUS/B/LB **and** at least three
independent in-silico axes predict deleterious **and** CADD PHRED >= 20.

No flag when ClinVar is P/LP or absent.  No flag when fewer than 3 axes
agree on deleterious.  This implements PRD §5, Sprint 2.1, P2-07.

In-silico tool thresholds (standard community cutoffs):
    - SIFT:      pred == 'D' or score < 0.05
    - PolyPhen-2: pred == 'D' (probably_damaging)
    - CADD PHRED: >= 20
    - REVEL:     >= 0.5
    - MetaSVM:   > 0
    - MetaLR:    > 0.5

Usage::

    from backend.annotation.evidence_conflict import detect_evidence_conflict

    flag = detect_evidence_conflict(variant_row)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from backend.annotation.insilico_axes import CADD_PHRED_THRESHOLD, assess_insilico_axes

# ClinVar significances that trigger conflict detection.
# P/LP and absent ClinVar → no flag.
_CONFLICT_ELIGIBLE_SIGNIFICANCES = frozenset(
    {
        "Uncertain significance",
        "Benign",
        "Likely benign",
        # Handle alternate spellings from ClinVar VCF
        "Uncertain_significance",
        "Likely_benign",
        "VUS",
    }
)

# ClinVar significances where conflict is never flagged (authoritative).
_AUTHORITATIVE_SIGNIFICANCES = frozenset(
    {
        "Pathogenic",
        "Likely pathogenic",
        "Likely_pathogenic",
        "Pathogenic/Likely pathogenic",
        "Pathogenic/Likely_pathogenic",
    }
)

# CADD PHRED threshold for the CADD-specific gate.
_CADD_THRESHOLD = CADD_PHRED_THRESHOLD

# Minimum number of independent in-silico axes predicting deleterious.
_MIN_DELETERIOUS_AXES = 3


@dataclass(frozen=True, slots=True)
class EvidenceConflictResult:
    """Result of evidence conflict detection for a single variant."""

    flag: bool
    deleterious_count: int
    total_tools_assessed: int
    cadd_phred: float | None
    clinvar_significance: str | None


def count_deleterious_tools(variant: dict[str, Any] | Any) -> tuple[int, int]:
    """Count deleterious independent axes.

    The public function name is retained for compatibility with older callers,
    but the returned values are canonical F24/F25 axis counts.
    """
    return assess_insilico_axes(variant)


def detect_evidence_conflict(variant: dict[str, Any] | Any) -> EvidenceConflictResult:
    """Detect evidence conflict for a single variant.

    The amber flag fires when ALL three conditions are met:
        1. ClinVar significance is VUS, B, or LB
        2. >=3 independent in-silico axes predict deleterious
        3. CADD PHRED >= 20

    Args:
        variant: A dict or row-like object with annotation fields.

    Returns:
        :class:`EvidenceConflictResult` with the flag and supporting data.
    """

    def _get(key: str) -> Any:
        if isinstance(variant, dict):
            return variant.get(key)
        return getattr(variant, key, None)

    clinvar_sig = _get("clinvar_significance")
    cadd_phred = _get("cadd_phred")

    # Condition 1: ClinVar must be present and VUS/B/LB
    if clinvar_sig is None:
        return EvidenceConflictResult(
            flag=False,
            deleterious_count=0,
            total_tools_assessed=0,
            cadd_phred=cadd_phred,
            clinvar_significance=clinvar_sig,
        )

    # Normalise: strip whitespace for safety
    clinvar_sig_stripped = clinvar_sig.strip()

    # P/LP → never flag
    if clinvar_sig_stripped in _AUTHORITATIVE_SIGNIFICANCES:
        return EvidenceConflictResult(
            flag=False,
            deleterious_count=0,
            total_tools_assessed=0,
            cadd_phred=cadd_phred,
            clinvar_significance=clinvar_sig,
        )

    # Must be one of the conflict-eligible significances
    if clinvar_sig_stripped not in _CONFLICT_ELIGIBLE_SIGNIFICANCES:
        return EvidenceConflictResult(
            flag=False,
            deleterious_count=0,
            total_tools_assessed=0,
            cadd_phred=cadd_phred,
            clinvar_significance=clinvar_sig,
        )

    # Condition 2 & 3: count deleterious tools and check CADD
    del_count, total_assessed = count_deleterious_tools(variant)

    flag = (
        del_count >= _MIN_DELETERIOUS_AXES
        and cadd_phred is not None
        and cadd_phred >= _CADD_THRESHOLD
    )

    return EvidenceConflictResult(
        flag=flag,
        deleterious_count=del_count,
        total_tools_assessed=total_assessed,
        cadd_phred=cadd_phred,
        clinvar_significance=clinvar_sig,
    )


def apply_evidence_conflicts(variants: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Apply evidence conflict detection to a list of merged variant dicts.

    Mutates each dict in place, setting ``evidence_conflict`` to True/False.

    Args:
        variants: List of annotation dicts (as produced by _merge_annotations).

    Returns:
        The same list, with ``evidence_conflict`` set on each dict.
    """
    for v in variants:
        result = detect_evidence_conflict(v)
        v["evidence_conflict"] = result.flag
    return variants

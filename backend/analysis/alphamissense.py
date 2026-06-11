"""AlphaMissense missense-class context badge (SW-A12).

Turns an AlphaMissense prediction (am_pathogenicity / am_class; Cheng et al.,
*Science* 2023) into a **context-only** badge for a missense variant. Mirrors the
gene-constraint / array-confidence / gene-validity badges: it NEVER changes a
finding's ``evidence_level`` or ``clinvar_significance``.

Crucially, AlphaMissense is an **additive complement to REVEL**, not a third
independent in-silico vote: REVEL and AlphaMissense are correlated meta-predictors,
so stacking them would double-count the ACMG PP3/BP4 (computational) evidence
(cf. :mod:`backend.analysis.insilico_tiers`, REVEL-only by design). This module
therefore reports AlphaMissense alongside REVEL and surfaces *concordance*, but
does not emit a separate ACMG criterion.
"""

from __future__ import annotations

from typing import Any

from backend.analysis.insilico_tiers import is_missense_consequence, revel_to_acmg_tier
from backend.annotation.alphamissense import AM_BENIGN_MAX, AM_PATHOGENIC_MIN
from backend.disclaimers import ALPHAMISSENSE_CONTEXT_ONLY

# Cheng et al., Science 2023 (AlphaMissense).
ALPHAMISSENSE_PMID = "37733863"

# Normalize the file's am_class spellings to a stable 3-way label.
_CLASS_NORMALIZE = {
    "likely_benign": "likely_benign",
    "benign": "likely_benign",
    "ambiguous": "ambiguous",
    "likely_pathogenic": "likely_pathogenic",
    "pathogenic": "likely_pathogenic",
}

# Map AlphaMissense class to the directional sense of the REVEL/ACMG PP3·BP4 axis,
# purely to compute concordance (NOT to cast a vote).
_CLASS_DIRECTION = {
    "likely_benign": "benign",
    "ambiguous": None,
    "likely_pathogenic": "pathogenic",
}


def classify_am_pathogenicity(am_pathogenicity: float | None) -> str | None:
    """Derive the 3-way class from the score using the paper thresholds.

    Used only when ``am_class`` is missing; otherwise the file's ``am_class`` wins.
    """
    if am_pathogenicity is None:
        return None
    if am_pathogenicity > AM_PATHOGENIC_MIN:
        return "likely_pathogenic"
    if am_pathogenicity < AM_BENIGN_MAX:
        return "likely_benign"
    return "ambiguous"


def _revel_direction(revel_tier: str | None) -> str | None:
    """Map an insilico_tiers criterion (PP3/BP4) to a benign/pathogenic direction."""
    if revel_tier == "PP3":
        return "pathogenic"
    if revel_tier == "BP4":
        return "benign"
    return None


def alphamissense_badge(
    am_pathogenicity: float | None,
    am_class: str | None,
    *,
    revel_criterion: str | None = None,
) -> dict[str, Any] | None:
    """Context badge for one missense variant, or ``None`` if no AlphaMissense data.

    ``revel_criterion`` is the REVEL-derived ACMG criterion code (``"PP3"`` /
    ``"BP4"`` from :func:`backend.analysis.insilico_tiers.revel_to_acmg_tier`) when
    available, used to report concordance with the (single, REVEL-based) PP3/BP4
    in-silico vote — AlphaMissense itself never casts one.
    """
    label = _CLASS_NORMALIZE.get((am_class or "").strip().lower()) if am_class else None
    if label is None:
        label = classify_am_pathogenicity(am_pathogenicity)
    if label is None:
        return None

    am_dir = _CLASS_DIRECTION.get(label)
    revel_dir = _revel_direction(revel_criterion)
    if am_dir is None or revel_dir is None:
        concordance = "not_comparable"
    elif am_dir == revel_dir:
        concordance = "concordant"
    else:
        concordance = "discordant"

    return {
        "predictor": "AlphaMissense",
        "am_pathogenicity": am_pathogenicity,
        "am_class": label,
        "revel_concordance": concordance,
        "complements_revel": True,
        "acmg_vote": False,  # never a separate PP3/BP4 vote (avoids double-counting)
        "context_only": True,
        "pmid_citations": [ALPHAMISSENSE_PMID],
        "note": ALPHAMISSENSE_CONTEXT_ONLY,
    }


def alphamissense_badge_for_variant(
    am_pathogenicity: float | None,
    am_class: str | None,
    *,
    revel: float | None,
    consequence: str | None,
) -> dict[str, Any] | None:
    """Build the context-only badge for a variant row.

    REVEL remains the only source of a PP3/BP4 computational evidence tier.
    AlphaMissense only reports whether its direction is concordant with that
    REVEL tier when one exists.
    """
    revel_tier = revel_to_acmg_tier(
        revel,
        is_missense=is_missense_consequence(consequence),
    )
    return alphamissense_badge(
        am_pathogenicity,
        am_class,
        revel_criterion=revel_tier.criterion if revel_tier is not None else None,
    )

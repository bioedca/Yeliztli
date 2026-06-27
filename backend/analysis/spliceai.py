"""SpliceAI splice-prediction context badge (SW-F2).

Turns a raw SpliceAI lookup
(:func:`backend.annotation.spliceai.lookup_spliceai_by_variant`) into a
context-only summary for a variant: its max delta score, the tier it falls in
(possible / likely / high-confidence, per the 0.2 / 0.5 / 0.8 operating points),
and which splice event (acceptor/donor gain/loss) drives the score. SpliceAI is
an **in-silico prediction, not a functional assay** — this is explicitly NOT
ACMG evidence (``acmg_evidence=False``); it never adds PVS1/PP3/PS3.
"""

from __future__ import annotations

from typing import Any

from backend.annotation.spliceai import (
    SPLICEAI_CUTOFF_HIGH,
    SPLICEAI_CUTOFF_LIKELY,
    SPLICEAI_CUTOFF_POSSIBLE,
    SPLICEAI_PMID,
)
from backend.disclaimers import SPLICEAI_CONTEXT_ONLY

# Map each delta-score column to (machine key, human label) for the dominant
# splice event, and to its paired delta-position column.
_MODE_LABELS: dict[str, tuple[str, str]] = {
    "ds_ag": ("acceptor_gain", "Acceptor gain"),
    "ds_al": ("acceptor_loss", "Acceptor loss"),
    "ds_dg": ("donor_gain", "Donor gain"),
    "ds_dl": ("donor_loss", "Donor loss"),
}
_MODE_DP: dict[str, str] = {
    "ds_ag": "dp_ag",
    "ds_al": "dp_al",
    "ds_dg": "dp_dg",
    "ds_dl": "dp_dl",
}


def classify_spliceai_tier(ds_max: float | None) -> str:
    """Bin a max delta score into a tier name (Jaganathan 2019 operating points).

    ``high_confidence`` ≥ 0.8 (high precision), ``likely`` ≥ 0.5 (recommended),
    ``possible`` ≥ 0.2 (high recall), else ``none``; ``unknown`` if absent.
    """
    if ds_max is None:
        return "unknown"
    if ds_max >= SPLICEAI_CUTOFF_HIGH:
        return "high_confidence"
    if ds_max >= SPLICEAI_CUTOFF_LIKELY:
        return "likely"
    if ds_max >= SPLICEAI_CUTOFF_POSSIBLE:
        return "possible"
    return "none"


def spliceai_splice_context(row: dict[str, Any] | None) -> dict[str, Any] | None:
    """Summarize a variant's SpliceAI prediction (context only), or None.

    ``row`` is the per-variant dict from ``lookup_spliceai_by_variant``. Returns
    the max delta score, its tier, the dominant splice event + its delta
    position, and all four per-mode scores. Never an ACMG vote.
    """
    if not row:
        return None
    ds_max = row.get("ds_max")
    present = {k: row[k] for k in _MODE_LABELS if row.get(k) is not None}
    top_key = max(present, key=lambda k: present[k]) if present else None
    top_mode, top_mode_label = _MODE_LABELS[top_key] if top_key else (None, None)
    top_dp = row.get(_MODE_DP[top_key]) if top_key else None
    return {
        "ds_max": ds_max,
        "tier": classify_spliceai_tier(ds_max),
        "symbol": row.get("symbol"),
        "top_mode": top_mode,
        "top_mode_label": top_mode_label,
        "top_delta_position": top_dp,
        "ds_acceptor_gain": row.get("ds_ag"),
        "ds_acceptor_loss": row.get("ds_al"),
        "ds_donor_gain": row.get("ds_dg"),
        "ds_donor_loss": row.get("ds_dl"),
        "acmg_evidence": False,  # in-silico prediction, never an ACMG criterion
        "context_only": True,
        "note": SPLICEAI_CONTEXT_ONLY,
        "pmid_citations": [SPLICEAI_PMID],
    }

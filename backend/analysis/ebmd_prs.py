"""Osteoporosis / heel-eBMD polygenic score (SW-B7).

Scores a sample against a heel estimated-bone-mineral-density (speed-of-sound)
polygenic score and frames it strictly as a **research-grade risk-stratification
adjunct — not a substitute for DXA or FRAX**.

Licensing: the recommended eBMD score (PGS000657 "gSOS"; Forgetta et al. 2020)
is distributed CC BY-NC-ND-4.0 (non-commercial), so under the project's
posture A it is **not bundled**. This module is therefore bring-your-own (BYO):
it scores the gSOS weights only if the user has fetched them into the standalone
``pgs_scores.db``; otherwise it reports the score as unavailable with guidance.

Clinical framing (Forgetta et al., PLoS Med 2020): a BMD PRS can *refine*
fracture-risk screening — e.g. as a prescreen to reduce DXA referrals — and
modestly improves reclassification over FRAX, but it does **not** measure bone
density, diagnose osteoporosis, or replace DXA (the diagnostic gold standard) or
FRAX (the validated fracture-risk calculator).
"""

from __future__ import annotations

import sqlalchemy as sa
import structlog

from backend.analysis.pgs_bridge import build_trait_weight_set, load_pgs_registry
from backend.analysis.prs import PRSResult, run_prs, store_prs_findings

logger = structlog.get_logger(__name__)

MODULE_NAME = "ebmd"
EBMD_TRAIT = "heel_ebmd"
EBMD_PGS_ID = "PGS000657"
EBMD_PMIDS = ["32614825"]  # Forgetta et al., PLoS Med 2020 — gSOS fracture screening

EBMD_CONTEXT: dict[str, str] = {
    "not_a_substitute": (
        "This estimated bone-mineral-density polygenic score is a research-grade "
        "risk-stratification tool. It is NOT a substitute for DXA (the diagnostic "
        "gold standard for osteoporosis) or FRAX (the validated fracture-risk "
        "calculator), and it does not measure your bone density or diagnose "
        "osteoporosis."
    ),
    "utility": (
        "A bone-density polygenic score can refine fracture-risk screening — for "
        "example as a prescreen to reduce unnecessary DXA referrals — and modestly "
        "improves risk reclassification over FRAX (Forgetta et al., PLoS Med 2020)."
    ),
    "byo": (
        "The recommended eBMD score (PGS000657, gSOS) is distributed under a "
        "non-commercial license (CC BY-NC-ND 4.0) and is therefore not bundled. "
        "Fetch it into the local score database to enable scoring."
    ),
    "disclaimer": ("Research Use Only. Discuss bone health, DXA, and FRAX with a clinician."),
}


def score_ebmd_prs(
    sample_engine: sa.Engine,
    pgs_engine: sa.Engine | None,
    inferred_ancestry: str | None = None,
    top_ancestry_fraction: float | None = None,
) -> PRSResult | None:
    """Compute the eBMD PRS via the bridge, or None when the BYO score is absent.

    ``bundle_only=False`` lets the bridge select the non-commercial gSOS score;
    it still resolves to None unless the user has loaded it into ``pgs_scores.db``.
    """
    if pgs_engine is None:
        return None
    weight_set = build_trait_weight_set(
        pgs_engine,
        EBMD_TRAIT,
        inferred_ancestry,
        registry=load_pgs_registry(),
        bundle_only=False,
    )
    if weight_set is None:
        logger.info("ebmd_score_unavailable", reason="byo_not_installed")
        return None
    return run_prs(
        weight_set,
        sample_engine,
        inferred_ancestry=inferred_ancestry,
        top_ancestry_fraction=top_ancestry_fraction,
        n_bootstrap=0,
    )


def store_ebmd_findings(prs: PRSResult | None, sample_engine: sa.Engine) -> int:
    """Store the eBMD PRS finding (replaced on re-run); 0 when unavailable.

    store_prs_findings clears prior module/prs rows unconditionally (it deletes
    before the empty-list early return, #244), so passing [] when the BYO score is
    un-installed clears any stale finding identically and reads as absent.
    """
    return store_prs_findings(
        [prs] if prs is not None else [],
        sample_engine,
        module=MODULE_NAME,
        store_insufficient=True,
    )

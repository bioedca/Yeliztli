"""GTEx eQTL regulatory-context badge (SW-F3).

Turns raw GTEx eQTL lookups (:func:`backend.annotation.gtex_eqtl.lookup_eqtls_by_rsids`)
into a context-only summary for a variant: which genes' expression it is associated
with, in how many tissues. This is **regulatory association, not mechanism** — it is
explicitly NOT ACMG evidence (``acmg_evidence=False``); an eQTL never adds PP3/PS3.
"""

from __future__ import annotations

from typing import Any

from backend.disclaimers import GTEX_EQTL_CONTEXT_ONLY

GTEX_PMID = "32913098"


def eqtl_regulatory_context(rsid: str, eqtls: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Summarize a variant's GTEx eQTL associations (context only), or None if none.

    ``eqtls`` is the per-rsid list from ``lookup_eqtls_by_rsids``. Returns the set
    of associated genes + tissue count, with the strongest (smallest p-value)
    association highlighted. Never an ACMG vote.
    """
    if not eqtls:
        return None
    genes = sorted({e["gene_id"] for e in eqtls if e.get("gene_id")})
    tissues = sorted({e["tissue"] for e in eqtls if e.get("tissue")})
    with_p = [e for e in eqtls if e.get("pval_nominal") is not None]
    top = min(with_p, key=lambda e: e["pval_nominal"]) if with_p else None
    return {
        "rsid": rsid,
        "gene_ids": genes,
        "tissues": tissues,
        "n_associations": len(eqtls),
        "top_gene_id": top.get("gene_id") if top else None,
        "top_tissue": top.get("tissue") if top else None,
        "top_pval_nominal": top.get("pval_nominal") if top else None,
        "acmg_evidence": False,  # eQTL = association, never an ACMG criterion
        "context_only": True,
        "note": GTEX_EQTL_CONTEXT_ONLY,
        "pmid_citations": [GTEX_PMID],
    }

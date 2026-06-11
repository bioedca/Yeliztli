"""Cross-source pharmacogenomic evidence layer over CPIC (SW-E2).

CPIC is one of several authorities that grade gene-drug pharmacogenomics. This
module layers three corroborating sources over the sample's existing CPIC
prescribing alerts, so a user sees not just "CPIC says X" but how strong and how
widely-endorsed the underlying gene-drug association is:

* **PharmGKB Level of Evidence** (Whirl-Carrillo 2021, PMID 34216021) — 1A is the
  strongest tier (a variant-drug combination in a CPIC/medical-society guideline);
  taken as the *best* LoE across PharmGKB's per-variant clinical annotations for
  the pair.
* **DPWG** — whether the Dutch Pharmacogenetics Working Group also publishes a
  recommendation for the pair (independent guideline corroboration).
* **FDA pharmacogenomic labeling** — the FDA testing level for the drug w.r.t. the
  gene (Actionable / Informative / Testing Required / Recommended / No clinical PGx).

The values are curated from authoritative public downloads — PharmGKB clinical
annotations + guideline annotations + drug labels (CC-BY-SA 4.0, attributed in
NOTICE; share-alike honored) and the FDA's pharmacogenomic labeling (US-government
public domain) — into ``backend/data/pgx/pgx_guideline_sources.csv``.

**Context only.** This NEVER changes a finding, a CPIC recommendation, or a
metabolizer status — it is additive evidence-strength context joined by
(gene, drug) to the sample's stored prescribing alerts. See
:data:`backend.disclaimers.PGX_SOURCES_CONTEXT_ONLY`.
"""

from __future__ import annotations

import csv
from functools import lru_cache
from pathlib import Path
from typing import Any

import sqlalchemy as sa

from backend.disclaimers import PGX_SOURCES_CONTEXT_ONLY

# PharmGKB clinical-annotation LoE framework (primary citation for the LoE column).
PGX_SOURCES_PMID = "34216021"

_CSV_PATH = Path(__file__).resolve().parent.parent / "data" / "pgx" / "pgx_guideline_sources.csv"

# PharmGKB LoE ordering (strongest first) for any best-of aggregation downstream.
_LOE_RANK = {"1A": 0, "1B": 1, "2A": 2, "2B": 3, "3": 4, "4": 5}


@lru_cache(maxsize=1)
def _load_sources() -> dict[tuple[str, str], dict[str, Any]]:
    """Load + cache the curated gene-drug → cross-source evidence map."""
    out: dict[tuple[str, str], dict[str, Any]] = {}
    with _CSV_PATH.open(encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            gene = (row.get("gene") or "").strip()
            drug = (row.get("drug") or "").strip().lower()
            if not gene or not drug:
                continue
            key = (gene, drug)
            if key in out:
                raise ValueError(f"Duplicate (gene, drug) in pgx_guideline_sources.csv: {key}")
            fda = (row.get("fda_pgx_level") or "").strip()
            out[key] = {
                "gene": gene,
                "drug": drug,
                "pharmgkb_loe": (row.get("pharmgkb_loe") or "").strip() or None,
                "dpwg_guideline": (row.get("dpwg_guideline") or "").strip().lower() == "yes",
                "fda_pgx_level": fda or None,
            }
    return out


def lookup_guideline_sources(gene: str | None, drug: str | None) -> dict[str, Any] | None:
    """Return the cross-source evidence for one (gene, drug), or None if uncurated."""
    if not gene or not drug:
        return None
    return _load_sources().get((gene.strip(), drug.strip().lower()))


def loe_rank(loe: str | None) -> int:
    """Rank a PharmGKB LoE (lower is stronger); unknown sorts last."""
    return _LOE_RANK.get((loe or "").strip(), 99)


def assess_sample_pgx_guidelines(sample_engine: sa.Engine) -> dict[str, Any]:
    """Attach cross-source evidence to each of the sample's CPIC prescribing alerts.

    Read-only. Selects the sample's stored ``prescribing_alert`` findings and joins
    each to its (gene, drug) cross-source evidence (PharmGKB LoE / DPWG / FDA).
    Additive only — never mutates a finding or the CPIC recommendation.
    """
    from backend.db.tables import findings

    stmt = (
        sa.select(
            findings.c.id,
            findings.c.gene_symbol,
            findings.c.drug,
            findings.c.metabolizer_status,
            findings.c.finding_text,
        )
        .where(findings.c.category == "prescribing_alert")
        .order_by(findings.c.id)
    )
    with sample_engine.connect() as conn:
        rows = conn.execute(stmt).fetchall()

    out: list[dict[str, Any]] = []
    for row in rows:
        sources = lookup_guideline_sources(row.gene_symbol, row.drug)
        out.append(
            {
                "finding_id": row.id,
                "gene_symbol": row.gene_symbol,
                "drug": row.drug,
                "metabolizer_status": row.metabolizer_status,
                "has_sources": sources is not None,
                "pharmgkb_loe": sources["pharmgkb_loe"] if sources else None,
                "dpwg_guideline": sources["dpwg_guideline"] if sources else None,
                "fda_pgx_level": sources["fda_pgx_level"] if sources else None,
            }
        )

    return {
        "alerts": out,
        "context_only": True,
        "note": PGX_SOURCES_CONTEXT_ONLY,
        "pmid_citations": [PGX_SOURCES_PMID],
    }

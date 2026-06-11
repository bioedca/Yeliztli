"""gnomAD gene-constraint lookup + context badge.

EXPANSION_STRATEGY.md §7 / roadmap #12. Reads the ``gnomad_gene_constraint``
table (gnomAD v2.1.1, GRCh37) from ``reference.db`` and produces a gene-level
"this gene doesn't tolerate loss-of-function" *context* badge.

This is **context only** — it can inform human reading of ACMG mechanism
criteria, but it is not itself PVS1, PP2, or BP1 mechanism evidence, **never
auto-upgrades an ACMG classification**, and never mutates a finding's
``evidence_level`` / ``clinvar_significance`` (a falsely-called LoF SNP or a
missense variant in a constrained gene must never become "Pathogenic" on the
strength of a badge).

``lof_constrained`` is derived here (not stored): a gene is LoF-constrained when
``loeuf < 0.35`` (first LOEUF decile is enriched for haploinsufficient/disease
genes) **or** ``pli > 0.9`` (Karczewski 2020, *Nature*; PMID 32461654).
"""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa
import structlog

from backend.db.tables import gnomad_gene_constraint
from backend.disclaimers import GENE_CONSTRAINT_CONTEXT_ONLY

logger = structlog.get_logger(__name__)

# LoF-constraint thresholds (Karczewski 2020).
LOEUF_CONSTRAINED_MAX = 0.35
PLI_CONSTRAINED_MIN = 0.9

_CONSTRAINT_CONTEXT_NOTE = GENE_CONSTRAINT_CONTEXT_ONLY


def is_lof_constrained(loeuf: float | None, pli: float | None) -> bool:
    """A gene is LoF-constrained when LOEUF < 0.35 or pLI > 0.9."""
    if loeuf is not None and loeuf < LOEUF_CONSTRAINED_MAX:
        return True
    return pli is not None and pli > PLI_CONSTRAINED_MIN


def _badge(loeuf: float | None, pli: float | None, constrained: bool) -> str | None:
    if not constrained:
        return None
    if loeuf is not None:
        return f"LoF-constrained gene (LOEUF {loeuf:.2f})"
    return f"LoF-constrained gene (pLI {pli:.2f})"


def lookup_gene_constraint(
    reference_engine: sa.Engine, gene_symbol: str | None
) -> dict[str, Any] | None:
    """Look up one gene's constraint context, or ``None`` if unknown/missing.

    Returns ``None`` (no error) for an unknown gene or a ``None`` input, so callers
    can treat "no curation" as "not evaluated", never as "unconstrained/benign".
    """
    if not gene_symbol:
        return None
    return lookup_gene_constraints(reference_engine, [gene_symbol]).get(gene_symbol)


def lookup_gene_constraints(
    reference_engine: sa.Engine, gene_symbols: list[str]
) -> dict[str, dict[str, Any]]:
    """Batch lookup → ``{gene_symbol: constraint_context}`` for the genes found."""
    wanted = sorted({g for g in gene_symbols if g})
    if not wanted:
        return {}
    out: dict[str, dict[str, Any]] = {}
    with reference_engine.connect() as conn:
        rows = conn.execute(
            sa.select(gnomad_gene_constraint).where(
                gnomad_gene_constraint.c.gene_symbol.in_(wanted)
            )
        ).fetchall()
    for row in rows:
        constrained = is_lof_constrained(row.loeuf, row.pli)
        out[row.gene_symbol] = {
            "gene_symbol": row.gene_symbol,
            "loeuf": row.loeuf,
            "pli": row.pli,
            "mis_z": row.mis_z,
            "lof_constrained": constrained,
            "badge": _badge(row.loeuf, row.pli, constrained),
            "context_only": True,
            "note": _CONSTRAINT_CONTEXT_NOTE,
        }
    return out

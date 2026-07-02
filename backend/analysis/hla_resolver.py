"""Resolve a sample's persisted HLA calls into normalized allele carriage (Wave D).

The shared substrate the Wave D SW-D2–D5 report layers read: it turns the raw
per-locus HIBAG calls stored in ``hla_calls`` (by
:mod:`backend.analysis.hla_persist`) into carriage queries — "does this sample
carry ``B*57:01``?" — with the source, confidence, and low-confidence flag needed
to frame the answer honestly.

**Allele-query semantics.** HIBAG stores 2-field/4-digit alleles without the locus
prefix (``allele1='57:01'``). A query is written locus-qualified (``B*57:01``,
``HLA-DQB1*06:02``, or a group-level ``B*27``). Matching is **prefix-aware** on the
colon-separated fields, so a group-level query (``B*27``) matches any 2-field
member (``27:05``) — the level several autoimmune susceptibility loci (HLA-B*27,
the DRB1 shared-epitope group) are defined at — while a full 2-field query matches
only that allele. ``copies`` counts how many of the two called alleles match, so a
query resolves to homozygous / heterozygous / absent.

**No call ≠ absent.** When the sample has no call at the queried locus (HIBAG never
ran, or that locus was not in the model), :func:`carries_allele` returns ``None``
("unknown"), never a false ``carried=False`` — a rule-out or a susceptibility
report must distinguish "typed and absent" from "not typed at all".

**Confidence travels with the call.** HIBAG's posterior ``prob`` is the confidence
(``matching`` is a QC signal, not a confidence — Zheng 2014, PMID:23712092); calls
below the gate carry ``low_confidence=True``. Every HLA call here is **imputed**, so
the clinical layers (SW-D2–D5) that consume this resolver must attach the standing
confirmatory-typing caveat to any claim they build on a call.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

import sqlalchemy as sa
import structlog

from backend.db.tables import hla_calls

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class ResolvedHLACall:
    """One per-locus HLA genotype read back from ``hla_calls``."""

    locus: str
    allele1: str
    allele2: str
    prob: float | None
    low_confidence: bool
    source: str
    ancestry_model: str | None


@dataclass(frozen=True)
class HLACarriage:
    """Carriage of a queried HLA allele (or allele group) in a sample."""

    query: str  # canonical locus-qualified query, e.g. "B*57:01"
    locus: str
    carried: bool
    copies: int  # 0, 1, or 2 of the called alleles match the query
    zygosity: str | None  # "homozygous" | "heterozygous" | None (absent)
    prob: float | None
    low_confidence: bool
    source: str


def read_hla_calls(sample_engine: sa.Engine) -> list[ResolvedHLACall]:
    """Read all persisted HLA calls for a sample (``[]`` if none / table absent)."""
    if not sa.inspect(sample_engine).has_table("hla_calls"):
        return []
    stmt = sa.select(
        hla_calls.c.locus,
        hla_calls.c.allele1,
        hla_calls.c.allele2,
        hla_calls.c.prob,
        hla_calls.c.low_confidence,
        hla_calls.c.source,
        hla_calls.c.ancestry_model,
    )
    with sample_engine.connect() as conn:
        return [
            ResolvedHLACall(
                locus=r.locus,
                allele1=r.allele1,
                allele2=r.allele2,
                prob=r.prob,
                low_confidence=bool(r.low_confidence),
                source=r.source,
                ancestry_model=r.ancestry_model,
            )
            for r in conn.execute(stmt).fetchall()
        ]


def _parse_query(allele: str) -> tuple[str, list[str]] | None:
    """Split a locus-qualified query into ``(locus, fields)``.

    ``"HLA-B*57:01"`` / ``"B*57:01"`` → ``("B", ["57", "01"])``;
    ``"B*27"`` → ``("B", ["27"])``. Returns ``None`` for a malformed query
    (missing locus/``*`` separator or empty allele).
    """
    a = allele.strip()
    if a.upper().startswith("HLA-"):
        a = a[4:]
    if "*" not in a:
        return None
    locus, _, spec = a.partition("*")
    locus = locus.strip().upper()
    fields = [f for f in spec.strip().split(":") if f]
    if not locus or not fields:
        return None
    return locus, fields


def _allele_matches(stored: str, query_fields: Sequence[str]) -> bool:
    """True iff ``stored`` (e.g. ``"27:05"``) matches the query field prefix.

    A group-level query (fewer fields, e.g. ``["27"]``) matches any allele whose
    leading fields agree (``27:05``); a full query matches only that allele. A
    query longer/more-specific than the stored allele never matches.
    """
    stored_fields = [f for f in (stored or "").strip().split(":") if f]
    if len(query_fields) > len(stored_fields):
        return False
    return stored_fields[: len(query_fields)] == list(query_fields)


def carries_allele(calls: Sequence[ResolvedHLACall], allele: str) -> HLACarriage | None:
    """Resolve carriage of ``allele`` (locus-qualified) against a sample's calls.

    Returns ``None`` when the queried locus was **not called** (unknown — never a
    false negative). Otherwise returns an :class:`HLACarriage` with ``copies`` /
    ``zygosity`` (``carried=False`` when the locus was typed but the allele is
    absent). ``allele`` must be locus-qualified (``B*57:01``); a malformed query
    also returns ``None``.
    """
    parsed = _parse_query(allele)
    if parsed is None:
        return None
    locus, fields = parsed
    call = next((c for c in calls if c.locus.upper() == locus), None)
    if call is None:
        return None
    copies = sum(1 for a in (call.allele1, call.allele2) if _allele_matches(a, fields))
    zygosity = {2: "homozygous", 1: "heterozygous"}.get(copies)
    return HLACarriage(
        query=f"{locus}*{':'.join(fields)}",
        locus=locus,
        carried=copies > 0,
        copies=copies,
        zygosity=zygosity,
        prob=call.prob,
        low_confidence=call.low_confidence,
        source=call.source,
    )


def carriage_map(
    calls: Sequence[ResolvedHLACall], alleles: Iterable[str]
) -> dict[str, HLACarriage | None]:
    """Resolve carriage for many alleles at once (query string → carriage/None)."""
    return {allele: carries_allele(calls, allele) for allele in alleles}

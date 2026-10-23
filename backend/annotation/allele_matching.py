"""Shared conservative classification of array calls and reference alleles."""

from __future__ import annotations


def normal_allele(value: str | None) -> str | None:
    if value is None:
        return None
    value = str(value).strip().upper()
    return value or None


_NUCLEOTIDES = frozenset("ACGT")
_INDEL_TOKENS = frozenset("ID")


def genotype_allele_class(genotype: str | None) -> str | None:
    """Classify a sample call as an ``"indel"`` or ``"snv"`` probe.

    Consumer arrays call indel probes with vendor ``I``/``D`` tokens and SNP
    probes with single bases. Anything else (no-calls, VCF allele strings) is
    unclassified and returns ``None``.
    """
    if not genotype:
        return None
    tokens = [
        token.strip().upper() for token in (genotype.split("/") if "/" in genotype else genotype)
    ]
    if tokens and all(token in _INDEL_TOKENS for token in tokens):
        return "indel"
    if tokens and all(token in _NUCLEOTIDES for token in tokens):
        return "snv"
    return None


def row_allele_class(row: object) -> str | None:
    """Classify a reference row as an ``"indel"`` or ``"snv"`` record.

    The bundle mixes indel representations (VCF-anchored, VEP-trimmed with
    ``-``, and malformed same-base rows), so only the unambiguous shapes are
    classified; the rest return ``None``.
    """
    ref = normal_allele(getattr(row, "ref", None))
    alt = normal_allele(getattr(row, "alt", None))
    if ref is None or alt is None:
        return None
    if ref in _NUCLEOTIDES and alt in _NUCLEOTIDES and ref != alt:
        return "snv"
    if "-" in (ref, alt) or len(ref) != len(alt):
        return "indel"
    return None

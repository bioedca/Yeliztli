"""Canonical chromosome sort order — dependency-free.

Extracted from ``vcf_export`` so lightweight data-prep scripts (the Phase A
union builders) can sort by chromosome without importing the VCF exporter,
which transitively pulls in ``backend.db`` / ``backend.config``. Both this
module and ``vcf_export`` share the same ``CHROM_ORDER`` so output ordering is
identical wherever it is used. SQL queries order through
``backend.db.chrom_order.chrom_order_case``, which is built from this mapping.
"""

from __future__ import annotations

# Canonical chromosome order: 1..22, then X, Y, MT. Unknowns sort last (99).
UNKNOWN_CHROM_ORDER = 99

CHROM_ORDER: dict[str, int] = {
    **{str(i): i for i in range(1, 23)},
    "X": 23,
    "Y": 24,
    "MT": 25,
}


def chrom_sort_key(chrom: str) -> int:
    """Return an integer sort key for a chromosome string (unknown → 99)."""
    return CHROM_ORDER.get(chrom, UNKNOWN_CHROM_ORDER)

"""SQL ordering by the canonical chromosome order.

``backend.ingestion.chrom_order`` holds the canonical order (1..22, X, Y, MT, unknown 99)
and is deliberately SQLAlchemy-free so lightweight scripts can import it. This module is
its SQL counterpart: every query that orders or pages by chromosome builds its ``CASE``
here, so the ``ORDER BY`` of one surface can never drift from another's, or from the
Python ``chrom_sort_key`` a keyset cursor compares against (#2376).
"""

from __future__ import annotations

import sqlalchemy as sa

from backend.ingestion.chrom_order import CHROM_ORDER, UNKNOWN_CHROM_ORDER


def chrom_order_case(column: sa.ColumnElement) -> sa.Case:
    """``CASE`` mapping a chromosome column to its canonical sort integer.

    ``chrom`` is TEXT, so a bare ``.asc()`` sorts ``1, 10, 11, …, 19, 2, 20, …`` with
    X/Y/MT interleaved by ASCII (#2053). Unknown contigs sort last, as in
    ``chrom_sort_key``.
    """
    return sa.case(
        *[(column == chrom, order) for chrom, order in CHROM_ORDER.items()],
        else_=UNKNOWN_CHROM_ORDER,
    )

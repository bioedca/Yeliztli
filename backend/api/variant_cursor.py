"""Complete variant ordering and API continuation/position-seek predicates."""

from __future__ import annotations

import sqlalchemy as sa
from fastapi import HTTPException

from backend.db.chrom_order import chrom_order_case
from backend.ingestion.chrom_order import chrom_sort_key


def variant_order_key(table: sa.Table) -> list[sa.ColumnElement]:
    """Unique row order, including contigs sharing the noncanonical bucket."""
    return [chrom_order_case(table.c.chrom), table.c.chrom, table.c.pos, table.c.rsid]


def variant_cursor_clause(
    table: sa.Table,
    cursor_chrom: str | None,
    cursor_pos: int | None,
    cursor_rsid: str | None,
    *,
    allow_position_seek: bool = False,
) -> sa.ColumnElement | None:
    """Resume after a unique row, or seek inclusively to a chromosome/position.

    Literal chromosome names distinguish contigs with the same ordering bucket;
    the primary-key rsID distinguishes rows at the same position. Explorer's
    chromosome jump omits the rsID and seeks to position zero.
    """
    if all(part is None for part in (cursor_chrom, cursor_pos, cursor_rsid)):
        return None
    if (
        cursor_chrom is None
        or cursor_pos is None
        or (cursor_rsid is None and not allow_position_seek)
    ):
        detail = "cursor_chrom, cursor_pos and cursor_rsid must be sent together."
        if allow_position_seek:
            detail = (
                "cursor_chrom and cursor_pos must be sent together; cursor_rsid requires both."
            )
        raise HTTPException(status_code=422, detail=detail)

    columns = variant_order_key(table)
    values: list[str | int] = [chrom_sort_key(cursor_chrom), cursor_chrom, cursor_pos]
    if cursor_rsid is not None:
        values.append(cursor_rsid)
    return sa.or_(
        *[
            sa.and_(
                *[columns[j] == values[j] for j in range(i)],
                columns[i] >= values[i]
                if cursor_rsid is None and i == len(values) - 1
                else columns[i] > values[i],
            )
            for i in range(len(values))
        ]
    )

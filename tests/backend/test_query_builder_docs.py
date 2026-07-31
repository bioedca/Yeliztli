"""Keep Query Builder documentation aligned with its runtime contracts."""

from pathlib import Path
from typing import Any

from backend.api.routes.query_builder import (
    SQL_CONSOLE_MAX_ROWS,
    SQL_CONSOLE_TIMEOUT,
    QueryRequest,
    SqlRequest,
)
from backend.db.tables import annotated_variants, raw_variants, tags, variant_tags

DOCS_PATH = Path(__file__).resolve().parents[2] / "docs" / "features" / "query-builder.md"


def _docs_text() -> str:
    """Read prose independent of Markdown source line wrapping."""
    return " ".join(DOCS_PATH.read_text(encoding="utf-8").split())


def _upper_bound(model: type[Any], field_name: str) -> int:
    """Return the inclusive upper bound on a Pydantic model field."""
    for constraint in model.model_fields[field_name].metadata:
        if (upper_bound := getattr(constraint, "le", None)) is not None:
            return int(upper_bound)
    raise AssertionError(f"{model.__name__}.{field_name} has no upper bound")


def test_query_builder_docs_distinguish_visual_and_sql_scope() -> None:
    """Document fields that require the full-schema SQL console."""
    docs = _docs_text()
    annotated_fields = {column.name for column in annotated_variants.columns}
    raw_fields = {column.name for column in raw_variants.columns}

    assert {"source", "concordance"}.isdisjoint(annotated_fields)
    assert {"source", "concordance"} <= raw_fields
    assert tags.name == "tags"
    assert variant_tags.name == "variant_tags"

    assert "filters only columns from `annotated_variants`" in docs
    assert "`tags` and `variant_tags`" in docs
    assert "`raw_variants.source`" in docs
    assert "`raw_variants.concordance`" in docs
    assert "JOIN variant_tags AS vt ON vt.rsid = av.rsid" in docs
    assert "JOIN tags AS t ON t.id = vt.tag_id" in docs


def test_query_builder_docs_pin_runtime_limits() -> None:
    """Make changes to runtime result limits require a matching docs update."""
    docs = _docs_text()
    visual_default = QueryRequest.model_fields["limit"].default
    visual_maximum = _upper_bound(QueryRequest, "limit")
    sql_default = SqlRequest.model_fields["limit"].default

    assert (
        f"defaults to **{visual_default} rows per page** and accepts at most "
        f"**{visual_maximum} rows per page**" in docs
    )
    assert (
        f"defaults to **{sql_default} rows** and accepts at most "
        f"**{SQL_CONSOLE_MAX_ROWS:,} rows**" in docs
    )
    assert f"**{SQL_CONSOLE_TIMEOUT}-second timeout**" in docs
    assert "HTTP 408" in docs
    assert "`truncated`" in docs

"""The canonical chromosome order has exactly one definition (#2376).

The order (1..22, X, Y, MT, unknown 99) lives in ``backend.ingestion.chrom_order``; its SQL
``CASE`` lives in ``backend.db.chrom_order``. Before #2376, four API routes each carried a
private copy of the dict and five carried a private ``CASE`` (and the FHIR export a sixth,
inline). They were identical, but nothing kept them so: an edit to one would have reordered
that one surface, and a keyset cursor compares a Python key against a SQL ``ORDER BY``, so
the two disagreeing skips or repeats rows at a chromosome boundary.

The guard is a self-discovering AST scan of ``backend/``; the behaviour tests evaluate the
shared ``CASE`` in SQLite against ``chrom_sort_key``.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
import sqlalchemy as sa

from backend.db.chrom_order import chrom_order_case
from backend.ingestion.chrom_order import CHROM_ORDER, UNKNOWN_CHROM_ORDER, chrom_sort_key

_BACKEND = Path(__file__).resolve().parents[2] / "backend"
_CANONICAL = _BACKEND / "ingestion" / "chrom_order.py"
_SQL_HELPER = _BACKEND / "db" / "chrom_order.py"
_ORDER_KEYS = {"X", "Y", "MT"}


def _order_literals(tree: ast.AST) -> list[int]:
    """Lines of dict literals shaped like a chromosome order: X/Y/MT keys to integers."""
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        ints = {
            key.value
            for key, value in zip(node.keys, node.values, strict=True)
            if isinstance(key, ast.Constant)
            and isinstance(value, ast.Constant)
            and type(value.value) is int
        }
        if _ORDER_KEYS <= ints:
            found.append(node.lineno)
    return found


_CANONICAL_MODULE = "backend.ingestion.chrom_order"


def _order_aliases(tree: ast.AST) -> set[str]:
    """Names a module binds to the canonical order: ``from … import CHROM_ORDER [as X]``."""
    return {
        alias.asname or alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module == _CANONICAL_MODULE
        for alias in node.names
        if alias.name == "CHROM_ORDER"
    }


def _is_order_reference(node: ast.expr, aliases: set[str]) -> bool:
    if isinstance(node, ast.Name):
        return node.id in aliases or "chrom_order" in node.id.lower()
    # Qualified access, however the module was imported: chrom_order.CHROM_ORDER,
    # m.CHROM_ORDER, backend.ingestion.chrom_order.CHROM_ORDER (#2584 review).
    return isinstance(node, ast.Attribute) and node.attr == "CHROM_ORDER"


def _private_order_uses(tree: ast.AST, methods: frozenset[str]) -> list[int]:
    """Lines calling one of ``methods`` on the canonical order, however it was imported."""
    aliases = _order_aliases(tree)
    return [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and node.attr in methods
        and _is_order_reference(node.value, aliases)
    ]


def _case_over_order(tree: ast.AST) -> list[int]:
    """Lines that iterate the canonical order's items (how a private ``CASE`` is built)."""
    return _private_order_uses(tree, frozenset({"items"}))


def _key_over_order(tree: ast.AST) -> list[int]:
    """Lines that look a chromosome up in the order directly (a private ``chrom_sort_key``)."""
    return _private_order_uses(tree, frozenset({"get"}))


def test_no_module_outside_the_canonical_one_defines_a_chromosome_order() -> None:
    sources = sorted(_BACKEND.rglob("*.py"))
    assert len(sources) > 100, "the scan found too few backend modules to be meaningful"
    offenders = {
        str(path.relative_to(_BACKEND.parent)): lines
        for path in sources
        if path != _CANONICAL
        and (lines := _order_literals(ast.parse(path.read_text(encoding="utf-8"))))
    }
    assert offenders == {}, (
        f"chromosome-order literals outside backend/ingestion/chrom_order.py: {offenders}; "
        "import CHROM_ORDER / chrom_sort_key instead"
    )


def test_no_module_outside_the_sql_helper_builds_a_chromosome_case() -> None:
    offenders = {
        str(path.relative_to(_BACKEND.parent)): lines
        for path in sorted(_BACKEND.rglob("*.py"))
        if path != _SQL_HELPER
        and (lines := _case_over_order(ast.parse(path.read_text(encoding="utf-8"))))
    }
    assert offenders == {}, (
        f"private chromosome-order CASE expressions: {offenders}; "
        "use backend.db.chrom_order.chrom_order_case"
    )


@pytest.mark.parametrize(
    ("source", "offends"),
    [
        ('ORDER = {**{str(i): i for i in range(1, 23)}, "X": 23, "Y": 24, "MT": 25}', True),
        ('ORDER = {"X": 23, "Y": 24, "MT": 25}', True),
        # RefSeq accessions keyed by chromosome are not an order.
        ('NC = {"X": "NC_000023.10", "Y": "NC_000024.9", "MT": "NC_012920.1"}', False),
        ('PLOIDY = {"X": 1}', False),
    ],
)
def test_order_literal_scan_discriminates(source: str, offends: bool) -> None:
    assert bool(_order_literals(ast.parse(source))) is offends


def test_no_module_outside_the_canonical_one_hand_rolls_a_chromosome_sort_key() -> None:
    offenders = {
        str(path.relative_to(_BACKEND.parent)): lines
        for path in sorted(_BACKEND.rglob("*.py"))
        if path != _CANONICAL
        and (lines := _key_over_order(ast.parse(path.read_text(encoding="utf-8"))))
    }
    assert offenders == {}, f"private chromosome sort keys: {offenders}; use chrom_sort_key"


@pytest.mark.parametrize(
    ("source", "offends"),
    [
        (
            "from backend.ingestion.chrom_order import CHROM_ORDER as ORDER\n"
            "sa.case(*[(c == k, v) for k, v in ORDER.items()], else_=99)",
            True,
        ),
        (
            "from backend.ingestion import chrom_order\n"
            "sa.case(*[(c == k, v) for k, v in chrom_order.CHROM_ORDER.items()], else_=99)",
            True,
        ),
        (
            "import backend.ingestion.chrom_order as order_module\n"
            "sa.case(*[(c == k, v) for k, v in order_module.CHROM_ORDER.items()], else_=99)",
            True,
        ),
        (
            "import backend.ingestion.chrom_order\n"
            "pairs = backend.ingestion.chrom_order.CHROM_ORDER.items()",
            True,
        ),
        # An unrelated name that happens to be imported as ORDER elsewhere is not the order.
        ("from somewhere import LENGTHS as ORDER\nfor k, v in ORDER.items(): pass", False),
        ("sa.case(*[(c == k, v) for k, v in CHROM_ORDER.items()], else_=99)", True),
        ("sa.case(*[(c == k, v) for k, v in _CHROM_ORDER.items()], else_=99)", True),
        ("sa.case(*[(c == k, v) for k, v in chrom_order.items()], else_=99)", True),
        ("for k, v in LENGTHS.items(): pass", False),
    ],
)
def test_case_scan_discriminates(source: str, offends: bool) -> None:
    assert bool(_case_over_order(ast.parse(source))) is offends


@pytest.mark.parametrize(
    ("source", "offends"),
    [
        ("values = [_CHROM_ORDER.get(cursor_chrom, 99), cursor_pos]", True),
        (
            "from backend.ingestion.chrom_order import CHROM_ORDER as ORDER\n"
            "key = ORDER.get(chrom, 99)",
            True,
        ),
        ("key = chrom_sort_key(chrom)", False),
        ("value = settings.get('chrom', 99)", False),
    ],
)
def test_key_scan_discriminates(source: str, offends: bool) -> None:
    assert bool(_key_over_order(ast.parse(source))) is offends


_CHROMS = ["10", "2", "MT", "Y", "1", "X", "22", "GL000192.1", "chrUn", "11"]


def _sql_order_values(chroms: list[str]) -> list[tuple[str, int]]:
    engine = sa.create_engine("sqlite://")
    metadata = sa.MetaData()
    table = sa.Table("t", metadata, sa.Column("chrom", sa.Text))
    metadata.create_all(engine)
    expr = chrom_order_case(table.c.chrom)
    with engine.begin() as conn:
        conn.execute(table.insert(), [{"chrom": chrom} for chrom in chroms])
        rows = conn.execute(
            sa.select(table.c.chrom, expr.label("k")).order_by(expr, table.c.chrom)
        ).all()
    return [(row.chrom, row.k) for row in rows]


def test_sql_case_assigns_each_chromosome_the_python_sort_key() -> None:
    values = _sql_order_values(_CHROMS)
    assert len(values) == len(_CHROMS)
    assert values == [(chrom, chrom_sort_key(chrom)) for chrom, _ in values]


def test_sql_order_matches_python_order_including_unknown_contigs() -> None:
    ordered = [chrom for chrom, _ in _sql_order_values(_CHROMS)]
    assert ordered == sorted(_CHROMS, key=lambda c: (chrom_sort_key(c), c))
    assert ordered[:3] == ["1", "2", "10"]
    assert ordered[-5:] == ["X", "Y", "MT", "GL000192.1", "chrUn"]


def test_unknown_contigs_sort_after_every_known_one() -> None:
    assert UNKNOWN_CHROM_ORDER > max(CHROM_ORDER.values())
    assert chrom_sort_key("GL000192.1") == UNKNOWN_CHROM_ORDER

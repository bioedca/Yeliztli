"""Recursive translator: react-querybuilder RuleGroupType → SQLAlchemy Core.

Converts the JSON tree produced by react-querybuilder into composable
SQLAlchemy Core ``and_()``, ``or_()``, ``not_()`` expressions.  Values are
always bound parameters — **never** string-interpolated into SQL — making
SQL injection structurally impossible.

Field names are validated against the ``annotated_variants`` schema so
arbitrary column access is rejected at translation time.

Reference: PRD §P4-01, §8.7 (adversarial test matrix).
"""

from __future__ import annotations

import logging
from typing import Any

import sqlalchemy as sa

from backend.db.tables import annotated_variants

logger = logging.getLogger(__name__)

# ── Allowed fields (annotated_variants columns only) ──────────────────

# Build from the actual table definition so it stays in sync automatically.
ALLOWED_FIELDS: frozenset[str] = frozenset(col.name for col in annotated_variants.columns)

# Text columns — gate the string operators (contains/beginsWith/endsWith) to
# text fields (input validation; see the string-operator branch below). Numeric
# classification is not precomputed: _coerce_value derives it inline from
# col.type where value coercion needs it (#1002).
_TEXT_COLUMNS: frozenset[str] = frozenset(
    col.name for col in annotated_variants.columns if isinstance(col.type, sa.Text)
)

# ── Supported operators ───────────────────────────────────────────────

SUPPORTED_OPERATORS: frozenset[str] = frozenset(
    {
        "=",
        "!=",
        "<",
        ">",
        "<=",
        ">=",
        "contains",
        "beginsWith",
        "endsWith",
        "in",
        "notIn",
        "between",
        "null",
        "notNull",
    }
)

# Maximum nesting depth to prevent abuse.
MAX_DEPTH = 20

# Maximum total rules in a single query.
MAX_RULES = 200


# ── Exceptions ────────────────────────────────────────────────────────


class TranslationError(Exception):
    """Raised when the RuleGroupType tree cannot be translated safely."""


# ── Value coercion helpers ────────────────────────────────────────────


def _coerce_numeric(value: Any, field: str) -> int | float:
    """Coerce a value to int or float for a numeric column.

    Raises TranslationError on failure.
    """
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value
    if isinstance(value, str):
        try:
            # Try int first, then float (handles scientific notation)
            if "." in value or "e" in value.lower():
                return float(value)
            return int(value)
        except ValueError:
            pass
    raise TranslationError(f"Non-numeric value {value!r} for numeric field '{field}'.")


def _coerce_boolean(value: Any, field: str) -> bool:
    """Coerce a value to boolean for a boolean column."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        if value.lower() in ("true", "1", "yes"):
            return True
        if value.lower() in ("false", "0", "no"):
            return False
    raise TranslationError(f"Non-boolean value {value!r} for boolean field '{field}'.")


def _coerce_value(value: Any, field: str) -> Any:
    """Coerce a rule value to the appropriate Python type for the field."""
    col = annotated_variants.c[field]
    if isinstance(col.type, sa.Boolean):
        return _coerce_boolean(value, field)
    if isinstance(col.type, (sa.Integer, sa.Float)):
        return _coerce_numeric(value, field)
    # Text columns: accept as-is (must be string)
    if isinstance(col.type, sa.Text):
        return str(value)
    return value


# ── Core translator ──────────────────────────────────────────────────


def _translate_rule(rule: dict[str, Any]) -> sa.ColumnElement:
    """Translate a single RuleType dict to a SQLAlchemy column expression.

    Expected shape::

        {"field": "gene_symbol", "operator": "=", "value": "BRCA1"}
    """
    field = rule.get("field")
    operator = rule.get("operator")
    value = rule.get("value")

    # ── Field validation ──────────────────────────────────────────
    if not field or not isinstance(field, str):
        raise TranslationError("Rule missing 'field' or field is not a string.")

    if field not in ALLOWED_FIELDS:
        raise TranslationError(f"Field '{field}' is not in the annotated_variants schema.")

    # ── Operator validation ───────────────────────────────────────
    if not operator or not isinstance(operator, str):
        raise TranslationError("Rule missing 'operator' or operator is not a string.")

    if operator not in SUPPORTED_OPERATORS:
        raise TranslationError(f"Unsupported operator '{operator}'.")

    col = annotated_variants.c[field]

    # ── Null operators (no value needed) ──────────────────────────
    if operator == "null":
        return col.is_(None)
    if operator == "notNull":
        return col.is_not(None)

    # ── Type validation for value-bearing operators ───────────────
    if operator == "between":
        # Value must be a two-element list/tuple
        if not isinstance(value, (list, tuple)) or len(value) != 2:
            raise TranslationError(
                f"Operator 'between' requires a two-element array, got {type(value).__name__}."
            )
        lo = _coerce_value(value[0], field)
        hi = _coerce_value(value[1], field)
        return col.between(lo, hi)

    if operator in ("in", "notIn"):
        if not isinstance(value, (list, tuple)):
            raise TranslationError(
                f"Operator '{operator}' requires an array value, got {type(value).__name__}."
            )
        if len(value) == 0:
            # IN with empty list: always false.  NOT IN empty: always true.
            if operator == "in":
                return sa.literal(False)
            return sa.literal(True)
        coerced = [_coerce_value(v, field) for v in value]
        if operator == "in":
            return col.in_(coerced)
        return col.notin_(coerced)

    # ── String operators (text columns only) ──────────────────────
    if operator in ("contains", "beginsWith", "endsWith"):
        if field not in _TEXT_COLUMNS:
            raise TranslationError(
                f"String operator '{operator}' cannot be used on non-text field '{field}'."
            )
        str_value = str(value)
        if operator == "contains":
            return col.contains(str_value)
        if operator == "beginsWith":
            return col.startswith(str_value)
        # endsWith
        return col.endswith(str_value)

    # ── Comparison operators ──────────────────────────────────────
    coerced_value = _coerce_value(value, field)

    if operator == "=":
        return col == coerced_value
    if operator == "!=":
        return col != coerced_value
    if operator == "<":
        return col < coerced_value
    if operator == ">":
        return col > coerced_value
    if operator == "<=":
        return col <= coerced_value
    if operator == ">=":
        return col >= coerced_value

    # Should be unreachable due to SUPPORTED_OPERATORS check above.
    raise TranslationError(f"Unhandled operator '{operator}'.")  # pragma: no cover


def _count_rules(node: dict[str, Any]) -> int:
    """Recursively count the total number of rules in the tree."""
    if "rules" not in node:
        return 1 if "field" in node else 0
    total = 0
    for child in node.get("rules", []):
        if isinstance(child, dict):
            total += _count_rules(child)
    return total


def translate(rule_group: dict[str, Any], *, _depth: int = 0) -> sa.ColumnElement:
    """Recursively translate a RuleGroupType JSON tree to a SQLAlchemy expression.

    Parameters
    ----------
    rule_group:
        A dict matching the react-querybuilder ``RuleGroupType`` shape::

            {
                "combinator": "and" | "or",
                "rules": [ ...rules or nested groups... ],
                "not": false  // optional
            }

    Returns
    -------
    sa.ColumnElement
        A composable SQLAlchemy Core WHERE clause.

    Raises
    ------
    TranslationError
        On invalid input: unknown fields, bad operators, type mismatches,
        excessive nesting, or injection attempts.
    """
    if _depth > MAX_DEPTH:
        raise TranslationError(f"Query exceeds maximum nesting depth of {MAX_DEPTH}.")

    if _depth == 0:
        total_rules = _count_rules(rule_group)
        if total_rules > MAX_RULES:
            raise TranslationError(
                f"Query contains {total_rules} rules, exceeding the maximum of {MAX_RULES}."
            )

    # ── Validate group structure ──────────────────────────────────
    if not isinstance(rule_group, dict):
        raise TranslationError("Rule group must be a JSON object.")

    rules = rule_group.get("rules")
    if not isinstance(rules, list):
        raise TranslationError("Rule group must have a 'rules' array.")

    combinator = rule_group.get("combinator", "and")
    if combinator not in ("and", "or"):
        raise TranslationError(f"Invalid combinator '{combinator}'. Must be 'and' or 'or'.")

    is_negated = bool(rule_group.get("not", False))

    # ── Empty rule group → match all ──────────────────────────────
    if len(rules) == 0:
        expr = sa.literal(True)
        return sa.not_(expr) if is_negated else expr

    # ── Translate each child ──────────────────────────────────────
    clauses: list[sa.ColumnElement] = []
    for child in rules:
        if not isinstance(child, dict):
            # Skip non-dict entries (e.g. string combinators in IC mode)
            continue

        if "rules" in child:
            # Nested group
            clauses.append(translate(child, _depth=_depth + 1))
        elif "field" in child:
            # Individual rule
            if child.get("disabled"):
                continue
            clauses.append(_translate_rule(child))
        # else: skip unrecognised entries

    if len(clauses) == 0:
        expr = sa.literal(True)
        return sa.not_(expr) if is_negated else expr

    # ── Combine with AND/OR ───────────────────────────────────────
    if combinator == "and":
        combined = sa.and_(*clauses) if len(clauses) > 1 else clauses[0]
    else:
        combined = sa.or_(*clauses) if len(clauses) > 1 else clauses[0]

    if is_negated:
        combined = sa.not_(combined)

    return combined

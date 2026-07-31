"""Docs↔UI guard: coverage-driven pathway-level badges stay documented (#1582).

``pathwayLevelDisplayLabel`` (``frontend/src/lib/pathwayCoverage.ts``) renders two
coverage-qualified level badges on wellness/gene-health pathway cards —
**"Tested Standard"** and **"Not Assessed"** — for a Standard pathway whose array
coverage is incomplete. They appeared nowhere in the docs, so a user seeing them
had no way to learn what they mean or how they differ from a plain Standard /
Indeterminate level. This locks both badge words into the interpretation
reference so a coverage-qualified badge can't ship undocumented again.
"""

from __future__ import annotations

import ast
from pathlib import Path

from backend.annotation.dbnsfp import ENSEMBLE_MIN_AXES, is_ensemble_pathogenic_from_counts
from backend.annotation.insilico_axes import (
    CADD_PHRED_THRESHOLD,
    METALR_THRESHOLD,
    POLYPHEN_PROBABLY_DAMAGING_THRESHOLD,
    REVEL_THRESHOLD,
    SIFT_THRESHOLD,
    assess_insilico_axes,
)

_REPO = Path(__file__).resolve().parents[2]
_DOC = _REPO / "docs" / "modules" / "interpretation-reference.md"
_RARE_VARIANTS_DOC = _REPO / "docs" / "modules" / "rare-variants.md"
_VARIANT_DETAIL_DOC = _REPO / "docs" / "features" / "variant-detail.md"
_VARIANT_EXPLORER_DOC = _REPO / "docs" / "features" / "variant-explorer.md"
_SRC = _REPO / "frontend" / "src" / "lib" / "pathwayCoverage.ts"
_RARE_VARIANT_PANEL = (
    _REPO / "frontend" / "src" / "components" / "rare-variants" / "VariantDetailPanel.tsx"
)
_LOWER_PENETRANCE_MODULES = {
    "cancer.py": (
        "Cancer",
        _REPO / "docs" / "modules" / "health-risk" / "cancer.md",
    ),
    "cardiovascular.py": (
        "Cardiovascular",
        _REPO / "docs" / "modules" / "health-risk" / "cardiovascular.md",
    ),
    "carrier_status.py": (
        "Carrier status",
        _REPO / "docs" / "modules" / "health-risk" / "carrier-status.md",
    ),
    "rare_variant_finder.py": ("Rare variants", _RARE_VARIANTS_DOC),
}

# The coverage-qualified level-badge strings pathwayLevelDisplayLabel can emit.
_COVERAGE_BADGES = ("Tested Standard", "Not Assessed")

# Direction/scale tokens the in-silico-score note must carry so CADD/REVEL aren't
# shown as uninterpretable bare numbers (#1589).
_IN_SILICO_TOKENS = ("cadd", "revel", "phred", "deleterious", "pathogenic")

# The Rare Variant panel's CADD/REVEL red-highlight thresholds, mirrored in the doc.
_UI_SCORE_THRESHOLDS = ("cadd_phred >= 20", "revel >= 0.5")

_ENSEMBLE_THRESHOLD_TOKENS = (
    f"SIFT < {SIFT_THRESHOLD:g}",
    f"PolyPhen-2 HVAR > {POLYPHEN_PROBABLY_DAMAGING_THRESHOLD:g}",
    f"CADD PHRED ≥ {CADD_PHRED_THRESHOLD:g}",
    f"REVEL ≥ {REVEL_THRESHOLD:g}",
    "MetaSVM > 0",
    f"MetaLR > {METALR_THRESHOLD:g}",
)

_ENSEMBLE_EXAMPLES = (
    (2, 2, True),
    (2, 3, True),
    (2, 4, False),
    (3, 4, True),
)


def _documents_distinct_lower_penetrance_category(text: str) -> bool:
    normalized = (
        " ".join(text.lower().split())
        .replace("**", "")
        .replace("lower-penetrance", "lower penetrance")
        .replace("risk-allele", "risk allele")
        .replace("high-penetrance", "high penetrance")
    )
    canonical_distinct_category_language = (
        "lower penetrance/risk allele findings are stored under a distinct findings category "
        "from high penetrance p/lp"
        in normalized
        or "clinvar lower penetrance/risk allele — clinvar risk assertions are stored under "
        "a distinct findings category from high penetrance p/lp"
        in normalized
    )
    return canonical_distinct_category_language


def _merge_assignment_states(
    *states: list[tuple[int, ast.expr]],
) -> list[tuple[int, ast.expr]]:
    merged: dict[tuple[int, str], tuple[int, ast.expr]] = {}
    for state in states:
        for lineno, value in state:
            marker = (lineno, ast.dump(value, include_attributes=False))
            merged[marker] = (lineno, value)
    return list(merged.values())


def _direct_assignment(
    statement: ast.stmt,
    name: str,
) -> list[tuple[int, ast.expr]] | None:
    if isinstance(statement, ast.Assign) and any(
        isinstance(target, ast.Name) and target.id == name for target in statement.targets
    ):
        return [(statement.lineno, statement.value)]
    if (
        isinstance(statement, ast.AnnAssign)
        and isinstance(statement.target, ast.Name)
        and statement.target.id == name
        and statement.value is not None
    ):
        return [(statement.lineno, statement.value)]
    if (
        isinstance(statement, ast.AugAssign)
        and isinstance(statement.target, ast.Name)
        and statement.target.id == name
    ):
        return []
    return None


def _apply_statements(
    statements: list[ast.stmt],
    name: str,
    incoming: list[tuple[int, ast.expr]],
) -> list[tuple[int, ast.expr]]:
    state = incoming
    for statement in statements:
        direct = _direct_assignment(statement, name)
        if direct is not None:
            state = direct
        elif isinstance(statement, ast.If):
            body = _apply_statements(statement.body, name, state)
            orelse = (
                _apply_statements(statement.orelse, name, state) if statement.orelse else state
            )
            state = _merge_assignment_states(body, orelse)
        elif isinstance(statement, (ast.For, ast.AsyncFor, ast.While)):
            body = _apply_statements(statement.body, name, state)
            state = _merge_assignment_states(state, body)
            if statement.orelse:
                state = _apply_statements(statement.orelse, name, state)
        elif isinstance(statement, (ast.With, ast.AsyncWith)):
            state = _apply_statements(statement.body, name, state)
        elif isinstance(statement, (ast.Try, ast.TryStar)):
            body = _apply_statements(statement.body, name, state)
            branches = [body]
            branches.extend(
                _apply_statements(handler.body, name, state) for handler in statement.handlers
            )
            state = _merge_assignment_states(*branches)
            if statement.orelse:
                state = _apply_statements(statement.orelse, name, state)
            if statement.finalbody:
                state = _apply_statements(statement.finalbody, name, state)
        elif isinstance(statement, ast.Match):
            branches = [_apply_statements(case.body, name, state) for case in statement.cases]
            state = _merge_assignment_states(state, *branches)
    return state


def _statement_blocks(statement: ast.stmt) -> list[list[ast.stmt]]:
    if isinstance(statement, ast.If):
        return [statement.body, statement.orelse]
    if isinstance(statement, (ast.For, ast.AsyncFor, ast.While)):
        return [statement.body, statement.orelse]
    if isinstance(statement, (ast.With, ast.AsyncWith)):
        return [statement.body]
    if isinstance(statement, (ast.Try, ast.TryStar)):
        return [
            statement.body,
            *(handler.body for handler in statement.handlers),
            statement.orelse,
            statement.finalbody,
        ]
    if isinstance(statement, ast.Match):
        return [case.body for case in statement.cases]
    return []


def _block_contains_line(statements: list[ast.stmt], lineno: int) -> bool:
    return any(
        statement.lineno <= lineno <= getattr(statement, "end_lineno", statement.lineno)
        for statement in statements
    )


def _assignments_reaching_line(
    statements: list[ast.stmt],
    name: str,
    before_lineno: int,
    incoming: list[tuple[int, ast.expr]],
) -> list[tuple[int, ast.expr]]:
    state = incoming
    for statement in statements:
        if statement.lineno >= before_lineno:
            break
        if before_lineno <= getattr(statement, "end_lineno", statement.lineno):
            for block in _statement_blocks(statement):
                if _block_contains_line(block, before_lineno):
                    return _assignments_reaching_line(block, name, before_lineno, state)
            return state
        state = _apply_statements([statement], name, state)
    return state


def _reaching_assignments_before(
    function: ast.FunctionDef,
    name: str,
    before_lineno: int,
) -> list[tuple[int, ast.expr]]:
    return _assignments_reaching_line(function.body, name, before_lineno, [])


def _expression_emits_lower_penetrance_category(
    expression: ast.expr,
    function: ast.FunctionDef,
    functions: dict[str, ast.FunctionDef],
    seen: set[tuple[str, str, int]],
    before_lineno: int,
) -> bool:
    marker = (
        function.name,
        ast.dump(expression, include_attributes=False),
        before_lineno,
    )
    if marker in seen:
        return False
    seen.add(marker)

    for node in ast.walk(expression):
        if isinstance(node, ast.Name) and node.id == "LOWER_PENETRANCE_RISK_ALLELE_CATEGORY":
            return True
        if isinstance(node, ast.Name):
            use_lineno = getattr(node, "lineno", before_lineno)
            if any(
                _expression_emits_lower_penetrance_category(
                    value,
                    function,
                    functions,
                    seen,
                    assignment_lineno,
                )
                for assignment_lineno, value in _reaching_assignments_before(
                    function,
                    node.id,
                    use_lineno,
                )
            ):
                return True
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            called = functions.get(node.func.id)
            if called is not None and any(
                isinstance(return_node, ast.Return)
                and return_node.value is not None
                and _expression_emits_lower_penetrance_category(
                    return_node.value,
                    called,
                    functions,
                    seen,
                    getattr(return_node, "lineno", before_lineno),
                )
                for return_node in ast.walk(called)
            ):
                return True
    return False


def _is_findings_insert_expression(
    expression: ast.expr,
    function: ast.FunctionDef,
    seen: set[tuple[str, int]],
    before_lineno: int,
) -> bool:
    marker = (ast.dump(expression, include_attributes=False), before_lineno)
    if marker in seen:
        return False
    seen.add(marker)

    if isinstance(expression, ast.Name):
        return any(
            _is_findings_insert_expression(value, function, seen, assignment_lineno)
            for assignment_lineno, value in _reaching_assignments_before(
                function,
                expression.id,
                before_lineno,
            )
        )
    if not isinstance(expression, ast.Call):
        return False

    if isinstance(expression.func, ast.Attribute) and expression.func.attr == "values":
        return _is_findings_insert_expression(
            expression.func.value,
            function,
            seen,
            getattr(expression.func.value, "lineno", before_lineno),
        )
    if isinstance(expression.func, ast.Attribute) and expression.func.attr == "insert":
        if isinstance(expression.func.value, ast.Name) and expression.func.value.id == "findings":
            return True
        return bool(
            expression.args
            and isinstance(expression.args[0], ast.Name)
            and expression.args[0].id == "findings"
        )
    return False


def _values_bound_payloads(
    expression: ast.expr,
    function: ast.FunctionDef,
    before_lineno: int,
) -> list[ast.expr]:
    if isinstance(expression, ast.Name):
        payloads: list[ast.expr] = []
        for assignment_lineno, value in _reaching_assignments_before(
            function,
            expression.id,
            before_lineno,
        ):
            payloads.extend(_values_bound_payloads(value, function, assignment_lineno))
        return payloads
    if (
        not isinstance(expression, ast.Call)
        or not isinstance(expression.func, ast.Attribute)
        or expression.func.attr != "values"
        or not _is_findings_insert_expression(
            expression.func.value,
            function,
            set(),
            getattr(expression.func.value, "lineno", before_lineno),
        )
    ):
        return []

    payloads = list(expression.args)
    if expression.keywords:
        payloads.append(
            ast.Dict(
                keys=[ast.Constant(keyword.arg) for keyword in expression.keywords],
                values=[keyword.value for keyword in expression.keywords],
            )
        )
    return payloads


def _insert_payloads(function: ast.FunctionDef) -> list[tuple[ast.expr, int]]:
    payloads: list[tuple[ast.expr, int]] = []
    for node in ast.walk(function):
        if (
            not isinstance(node, ast.Call)
            or not isinstance(node.func, ast.Attribute)
            or node.func.attr != "execute"
        ):
            continue
        statement_index = next(
            (
                index
                for index, argument in enumerate(node.args)
                if _is_findings_insert_expression(argument, function, set(), node.lineno)
            ),
            None,
        )
        statement = node.args[statement_index] if statement_index is not None else None
        if statement is None:
            statement = next(
                (
                    keyword.value
                    for keyword in node.keywords
                    if keyword.arg == "statement"
                    and _is_findings_insert_expression(
                        keyword.value,
                        function,
                        set(),
                        node.lineno,
                    )
                ),
                None,
            )
        if statement is None:
            continue

        payloads.extend(
            (payload, node.lineno)
            for payload in _values_bound_payloads(statement, function, node.lineno)
        )
        if statement_index is not None:
            payloads.extend((payload, node.lineno) for payload in node.args[statement_index + 1 :])
        payloads.extend(
            (keyword.value, node.lineno)
            for keyword in node.keywords
            if keyword.arg == "parameters"
        )
    return payloads


def _persisted_row_expressions(
    payload: ast.expr,
    function: ast.FunctionDef,
    seen_names: set[tuple[str, int]],
    before_lineno: int,
) -> list[ast.expr]:
    if isinstance(payload, (ast.List, ast.Tuple, ast.Set)):
        return list(payload.elts)
    if isinstance(payload, (ast.ListComp, ast.SetComp, ast.GeneratorExp)):
        return [payload.elt]
    if not isinstance(payload, ast.Name):
        return [payload]
    marker = (payload.id, before_lineno)
    if marker in seen_names:
        return []
    seen_names.add(marker)

    rows: list[ast.expr] = []
    assignments = _reaching_assignments_before(function, payload.id, before_lineno)
    for assignment_lineno, value in assignments:
        rows.extend(
            _persisted_row_expressions(
                value,
                function,
                set(seen_names),
                assignment_lineno,
            )
        )
    assignment_floor = min((lineno for lineno, _ in assignments), default=0)
    for node in ast.walk(function):
        if (
            not isinstance(node, ast.Call)
            or not hasattr(node, "lineno")
            or not assignment_floor < node.lineno < before_lineno
            or not isinstance(node.func, ast.Attribute)
            or not isinstance(node.func.value, ast.Name)
            or node.func.value.id != payload.id
            or not node.args
        ):
            continue
        if node.func.attr == "append":
            rows.append(node.args[0])
        elif node.func.attr == "extend":
            rows.extend(
                _persisted_row_expressions(node.args[0], function, seen_names, node.lineno)
            )
    return rows


def _row_emits_lower_penetrance_category(
    expression: ast.expr,
    function: ast.FunctionDef,
    functions: dict[str, ast.FunctionDef],
    seen: set[tuple[str, str, int]],
    before_lineno: int,
) -> bool:
    marker = (
        function.name,
        ast.dump(expression, include_attributes=False),
        before_lineno,
    )
    if marker in seen:
        return False
    seen.add(marker)

    if isinstance(expression, ast.Dict):
        return any(
            isinstance(key, ast.Constant)
            and key.value == "category"
            and _expression_emits_lower_penetrance_category(
                value,
                function,
                functions,
                set(),
                getattr(value, "lineno", before_lineno),
            )
            for key, value in zip(expression.keys, expression.values, strict=True)
        )
    if isinstance(expression, ast.Name):
        return any(
            _row_emits_lower_penetrance_category(
                value,
                function,
                functions,
                set(seen),
                assignment_lineno,
            )
            for assignment_lineno, value in _reaching_assignments_before(
                function,
                expression.id,
                before_lineno,
            )
        )
    if isinstance(expression, (ast.List, ast.Tuple, ast.Set)):
        return any(
            _row_emits_lower_penetrance_category(
                element,
                function,
                functions,
                seen,
                getattr(element, "lineno", before_lineno),
            )
            for element in expression.elts
        )
    if isinstance(expression, ast.IfExp):
        return _row_emits_lower_penetrance_category(
            expression.body,
            function,
            functions,
            seen,
            getattr(expression.body, "lineno", before_lineno),
        ) or _row_emits_lower_penetrance_category(
            expression.orelse,
            function,
            functions,
            seen,
            getattr(expression.orelse, "lineno", before_lineno),
        )
    if isinstance(expression, ast.Call) and isinstance(expression.func, ast.Name):
        if expression.func.id == "dict":
            return any(
                keyword.arg == "category"
                and _expression_emits_lower_penetrance_category(
                    keyword.value,
                    function,
                    functions,
                    set(),
                    getattr(keyword.value, "lineno", before_lineno),
                )
                for keyword in expression.keywords
            ) or any(
                _row_emits_lower_penetrance_category(
                    argument,
                    function,
                    functions,
                    seen,
                    getattr(argument, "lineno", before_lineno),
                )
                for argument in expression.args
            )
        called = functions.get(expression.func.id)
        if called is not None:
            return any(
                isinstance(return_node, ast.Return)
                and return_node.value is not None
                and _row_emits_lower_penetrance_category(
                    return_node.value,
                    called,
                    functions,
                    seen,
                    getattr(return_node, "lineno", before_lineno),
                )
                for return_node in ast.walk(called)
            )
    return False


def _stores_lower_penetrance_category(path: Path) -> bool:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    functions = {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}
    for function in functions.values():
        if not (function.name.startswith("store_") and function.name.endswith("_findings")):
            continue
        for payload, execute_lineno in _insert_payloads(function):
            if any(
                _row_emits_lower_penetrance_category(
                    row,
                    function,
                    functions,
                    set(),
                    getattr(row, "lineno", execute_lineno),
                )
                for row in _persisted_row_expressions(payload, function, set(), execute_lineno)
            ):
                return True
    return False


def test_category_emission_detector_ignores_query_only_references(tmp_path: Path) -> None:
    analysis = tmp_path / "analysis.py"
    analysis.write_text(
        """
def query_categories():
    return {"category": LOWER_PENETRANCE_RISK_ALLELE_CATEGORY}

def store_test_findings():
    cleanup_filter = {"category": LOWER_PENETRANCE_RISK_ALLELE_CATEGORY}
    rows = []
    rows.append({"category": LOWER_PENETRANCE_RISK_ALLELE_CATEGORY})
    rows = [{"category": "monogenic_variant"}]
    conn.execute(sa.insert(findings), rows)
""",
        encoding="utf-8",
    )
    assert _stores_lower_penetrance_category(analysis) is False

    analysis.write_text(
        """
def store_test_findings():
    category = LOWER_PENETRANCE_RISK_ALLELE_CATEGORY
    category = "monogenic_variant"

    def unrelated_helper():
        category = LOWER_PENETRANCE_RISK_ALLELE_CATEGORY
        return category

    rows = [{"category": category}]
    conn.execute(sa.insert(findings), rows)
""",
        encoding="utf-8",
    )
    assert _stores_lower_penetrance_category(analysis) is False

    for branch_body in (
        """
    if enabled:
        rows = [{"category": LOWER_PENETRANCE_RISK_ALLELE_CATEGORY}]
    else:
        rows = [{"category": "monogenic_variant"}]
""",
        """
    if enabled:
        rows = [{"category": "monogenic_variant"}]
    else:
        rows = [{"category": LOWER_PENETRANCE_RISK_ALLELE_CATEGORY}]
""",
    ):
        analysis.write_text(
            f"""
def store_test_findings():
{branch_body}
    conn.execute(sa.insert(findings), rows)
""",
            encoding="utf-8",
        )
        assert _stores_lower_penetrance_category(analysis) is True

    analysis.write_text(
        """
def store_test_findings():
    rows = []
    rows.append({"category": LOWER_PENETRANCE_RISK_ALLELE_CATEGORY})
    conn.execute(sa.insert(findings), rows)
""",
        encoding="utf-8",
    )
    assert _stores_lower_penetrance_category(analysis) is True

    analysis.write_text(
        """
def store_test_findings():
    rows = [dict(category=LOWER_PENETRANCE_RISK_ALLELE_CATEGORY)]
    conn.execute(sa.insert(findings), parameters=rows)
""",
        encoding="utf-8",
    )
    assert _stores_lower_penetrance_category(analysis) is True

    analysis.write_text(
        """
def store_test_findings():
    category = "monogenic_variant"
    row = {"category": category}
    category = LOWER_PENETRANCE_RISK_ALLELE_CATEGORY
    conn.execute(statement=sa.insert(findings), parameters=[row])
""",
        encoding="utf-8",
    )
    assert _stores_lower_penetrance_category(analysis) is False

    analysis.write_text(
        """
def store_test_findings():
    statement = findings.insert().values(category="monogenic_variant")
    conn.execute(statement)
    statement = findings.insert().values(
        category=LOWER_PENETRANCE_RISK_ALLELE_CATEGORY,
    )
""",
        encoding="utf-8",
    )
    assert _stores_lower_penetrance_category(analysis) is False

    for comprehension in (
        '[{"category": LOWER_PENETRANCE_RISK_ALLELE_CATEGORY} for value in values]',
        "{dict(category=LOWER_PENETRANCE_RISK_ALLELE_CATEGORY) for value in values}",
        '({"category": LOWER_PENETRANCE_RISK_ALLELE_CATEGORY} for value in values)',
    ):
        analysis.write_text(
            f"""
def store_test_findings():
    rows = {comprehension}
    conn.execute(sa.insert(findings), rows)
""",
            encoding="utf-8",
        )
        assert _stores_lower_penetrance_category(analysis) is True

    analysis.write_text(
        """
def store_test_findings():
    conn.execute(
        findings.insert().values(
            category=LOWER_PENETRANCE_RISK_ALLELE_CATEGORY,
        )
    )
""",
        encoding="utf-8",
    )
    assert _stores_lower_penetrance_category(analysis) is True


def test_lower_penetrance_output_table_matches_analysis_modules() -> None:
    """Every analysis module storing the distinct tier stays in the reference table (#2052)."""
    analysis_dir = _REPO / "backend" / "analysis"
    implementation_modules = {
        path.name for path in analysis_dir.glob("*.py") if _stores_lower_penetrance_category(path)
    }
    assert implementation_modules == set(_LOWER_PENETRANCE_MODULES), (
        "Update _LOWER_PENETRANCE_MODULES and the interpretation-reference output table "
        "when an analysis starts or stops storing the distinct ClinVar tier (#2052)."
    )

    doc = _DOC.read_text(encoding="utf-8")
    row = next(
        line
        for line in doc.splitlines()
        if line.startswith("| ClinVar lower-penetrance/risk-allele variants |")
    )
    documented_modules = {module.strip() for module in row.split("|")[2].split(",")}
    expected_modules = {display_name for display_name, _ in _LOWER_PENETRANCE_MODULES.values()}
    assert documented_modules == expected_modules, (
        "The ClinVar lower-penetrance/risk-allele output row must list every analysis "
        f"module that stores the tier; expected {sorted(expected_modules)}, found "
        f"{sorted(documented_modules)} (#2052)."
    )


def test_lower_penetrance_category_is_documented_on_each_module_page() -> None:
    """Each module names the distinct stored category in 'What you'll see' (#2052)."""
    missing: list[str] = []
    for display_name, path in _LOWER_PENETRANCE_MODULES.values():
        doc = path.read_text(encoding="utf-8")
        what_youll_see = doc.split("## What you'll see", 1)[1].split("\n## ", 1)[0]
        if not _documents_distinct_lower_penetrance_category(what_youll_see):
            missing.append(display_name)

    assert not missing, (
        "Module pages returning the distinct ClinVar lower-penetrance/risk-allele category "
        "must describe it under 'What you'll see' as a distinct stored category from "
        f"high-penetrance P/LP findings; missing {missing} (#2052)."
    )


def test_shared_module_views_disclose_that_categories_render_together() -> None:
    """Storage-category wording must not imply a user-visible tier in shared views (#2052)."""
    for display_name, path in _LOWER_PENETRANCE_MODULES.values():
        if display_name == "Rare variants":
            continue
        doc = path.read_text(encoding="utf-8")
        what_youll_see = doc.split("## What you'll see", 1)[1].split("\n## ", 1)[0]
        normalized = " ".join(what_youll_see.lower().split())
        assert "the current page displays both categories together" in normalized, (
            f"{display_name} must distinguish its stored categories "
            "from its shared UI view (#2052)."
        )


def test_lower_penetrance_category_guard_rejects_high_penetrance_equivalence() -> None:
    """Keyword-only text cannot satisfy the stored-category documentation guard (#2052)."""
    assert not _documents_distinct_lower_penetrance_category(
        "Lower-penetrance/risk-allele findings use the same tier as high-penetrance P/LP findings."
    )
    assert not _documents_distinct_lower_penetrance_category(
        "Lower-penetrance/risk-allele findings are not a separate tier from "
        "high-penetrance P/LP findings."
    )
    assert not _documents_distinct_lower_penetrance_category(
        "Lower-penetrance/risk-allele findings are not reported separately from "
        "high-penetrance P/LP findings."
    )
    assert not _documents_distinct_lower_penetrance_category(
        "Lower-penetrance/risk-allele findings are never reported in a separate tier from "
        "high-penetrance P/LP findings."
    )
    assert not _documents_distinct_lower_penetrance_category(
        "Lower-penetrance/risk-allele findings are no longer reported in a separate tier "
        "from high-penetrance P/LP findings."
    )


def test_coverage_badges_are_documented() -> None:
    doc = _DOC.read_text(encoding="utf-8")
    missing = [b for b in _COVERAGE_BADGES if b not in doc]
    assert not missing, (
        "docs/modules/interpretation-reference.md does not document pathway-level "
        f"badge(s): {missing}. pathwayLevelDisplayLabel renders these — document them "
        "in the 'Categorical pathway levels' section (#1582)."
    )


def test_badges_are_still_emitted_by_the_code() -> None:
    """Premise guard: the two labels are still the literals pathwayCoverage.ts
    emits, so a rename trips this (revisit the doc + the list above) rather than
    leaving the doc pinning a badge word the code no longer shows (#1582)."""
    src = _SRC.read_text(encoding="utf-8")
    missing = [b for b in _COVERAGE_BADGES if f'"{b}"' not in src]
    assert not missing, (
        f"frontend/src/lib/pathwayCoverage.ts no longer emits: {missing}. "
        "Update _COVERAGE_BADGES and docs/modules/interpretation-reference.md."
    )


def test_in_silico_scores_are_documented() -> None:
    """CADD and REVEL are shown as bare numbers on the variant surfaces; the
    interpretation reference must state their direction and scale so a user can
    read them (they were undocumented while SIFT/PolyPhen got labels) (#1589)."""
    doc = _DOC.read_text(encoding="utf-8").lower()
    missing = [t for t in _IN_SILICO_TOKENS if t not in doc]
    assert not missing, (
        "docs/modules/interpretation-reference.md no longer documents the in-silico "
        f"pathogenicity scores' direction/scale (missing {missing}) — keep the CADD/REVEL "
        "note (#1589)."
    )


def test_documented_score_thresholds_match_the_ui() -> None:
    """Premise guard: the CADD ≥ 20 / REVEL ≥ 0.5 cut-offs the doc states are the
    ones the Rare Variant panel actually red-highlights, so a threshold change in
    the UI forces the doc to be revisited rather than silently drifting (#1589)."""
    doc = _DOC.read_text(encoding="utf-8")
    assert "CADD ≥ 20" in doc and "REVEL ≥ 0.5" in doc, (
        "interpretation-reference.md must state the UI's CADD ≥ 20 / REVEL ≥ 0.5 "
        "display thresholds (#1589)."
    )
    src = _RARE_VARIANT_PANEL.read_text(encoding="utf-8")
    missing = [t for t in _UI_SCORE_THRESHOLDS if t not in src]
    assert not missing, (
        f"{_RARE_VARIANT_PANEL.name} no longer applies the documented thresholds "
        f"{missing}. Update the UI copy and the CADD/REVEL note in "
        "docs/modules/interpretation-reference.md (#1589)."
    )


def test_ensemble_pathogenic_rule_is_documented() -> None:
    """The badge's project-specific axes and fraction must be readable outside code (#1971)."""
    doc = _DOC.read_text(encoding="utf-8")
    missing = [token for token in _ENSEMBLE_THRESHOLD_TOKENS if token not in doc]
    assert not missing, (
        "interpretation-reference.md no longer documents the ensemble-pathogenic "
        f"axis threshold(s): {missing} (#1971)."
    )

    plain_doc = " ".join(doc.replace("**", "").split())
    rule_tokens = (
        f"at least {ENSEMBLE_MIN_AXES} axes have data and a strict majority of those "
        "assessed axes vote deleterious",
        "deleterious axes / axes with data",
        "not a percentage, probability, confidence score",
        "not a claim that the underlying scores are statistically independent",
        "other present categorical results vote non-deleterious",
        "that axis is absent",
        "their strict majority becomes one axis",
        "If none has data, META is absent",
    )
    missing = [token for token in rule_tokens if token not in plain_doc]
    assert not missing, (
        "interpretation-reference.md no longer explains the ensemble-pathogenic "
        f"denominator or limitations: {missing} (#1971)."
    )


def test_documented_ensemble_operators_match_backend_boundaries() -> None:
    """Strict/inclusive operators in the threshold table match executable boundaries (#1971)."""
    epsilon = 0.001
    cases = (
        ("SIFT at boundary", {"sift_score": SIFT_THRESHOLD}, False),
        ("SIFT below boundary", {"sift_score": SIFT_THRESHOLD - epsilon}, True),
        (
            "PolyPhen-2 at boundary",
            {"polyphen2_hsvar_score": POLYPHEN_PROBABLY_DAMAGING_THRESHOLD},
            False,
        ),
        (
            "PolyPhen-2 above boundary",
            {"polyphen2_hsvar_score": POLYPHEN_PROBABLY_DAMAGING_THRESHOLD + epsilon},
            True,
        ),
        ("CADD at boundary", {"cadd_phred": CADD_PHRED_THRESHOLD}, True),
        ("CADD below boundary", {"cadd_phred": CADD_PHRED_THRESHOLD - epsilon}, False),
        ("REVEL at boundary", {"revel": REVEL_THRESHOLD}, True),
        ("REVEL below boundary", {"revel": REVEL_THRESHOLD - epsilon}, False),
        ("MetaSVM at boundary", {"metasvm": 0.0}, False),
        ("MetaSVM above boundary", {"metasvm": epsilon}, True),
        ("MetaLR at boundary", {"metalr": METALR_THRESHOLD}, False),
        ("MetaLR above boundary", {"metalr": METALR_THRESHOLD + epsilon}, True),
    )
    for label, variant, expected_deleterious in cases:
        deleterious, assessed = assess_insilico_axes(variant)
        assert assessed == 1, label
        assert bool(deleterious) is expected_deleterious, label


def test_documented_categorical_fallbacks_match_backend_denominator() -> None:
    """Present categorical results assess an axis even when they vote non-deleterious (#1971)."""
    cases = (
        ("SIFT deleterious", {"sift_pred": "D"}, True),
        ("SIFT tolerated", {"sift_pred": "T"}, False),
        ("PolyPhen-2 probably damaging", {"polyphen2_hsvar_pred": "D"}, True),
        ("PolyPhen-2 benign", {"polyphen2_hsvar_pred": "B"}, False),
    )
    for label, variant, expected_deleterious in cases:
        deleterious, assessed = assess_insilico_axes(variant)
        assert assessed == 1, label
        assert bool(deleterious) is expected_deleterious, label

    assert assess_insilico_axes({}) == (0, 0)


def test_documented_ensemble_examples_match_backend_rule() -> None:
    """The inverse-looking 2/2 versus 3/4 examples stay tied to the shipped rule (#1971)."""
    doc = _DOC.read_text(encoding="utf-8")
    for deleterious, assessed, expected in _ENSEMBLE_EXAMPLES:
        row_prefix = f"| `{deleterious}/{assessed}` | {'Yes' if expected else 'No'} |"
        assert row_prefix in doc
        assert is_ensemble_pathogenic_from_counts(deleterious, assessed) is expected

    assert "`2/2` is the minimum firing state" in doc
    assert "not stronger than `3/4`" in doc


def test_ensemble_definition_is_linked_from_variant_docs() -> None:
    """Every user-facing docs mention points to the canonical definition (#1971)."""
    anchor = "interpretation-reference.md#ensemble-pathogenic"
    docs = (_RARE_VARIANTS_DOC, _VARIANT_DETAIL_DOC, _VARIANT_EXPLORER_DOC)
    missing = [
        path.relative_to(_REPO).as_posix()
        for path in docs
        if anchor not in path.read_text(encoding="utf-8")
    ]
    assert not missing, f"Ensemble-pathogenic definition is not linked from: {missing} (#1971)."

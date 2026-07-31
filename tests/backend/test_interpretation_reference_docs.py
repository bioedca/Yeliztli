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


def _function_assignments(function: ast.FunctionDef) -> dict[str, list[ast.expr]]:
    assignments: dict[str, list[ast.expr]] = {}
    for node in ast.walk(function):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    assignments.setdefault(target.id, []).append(node.value)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if node.value is not None:
                assignments.setdefault(node.target.id, []).append(node.value)
    return assignments


def _expression_emits_lower_penetrance_category(
    expression: ast.expr,
    function: ast.FunctionDef,
    functions: dict[str, ast.FunctionDef],
    seen: set[tuple[str, str]],
) -> bool:
    marker = (function.name, ast.dump(expression, include_attributes=False))
    if marker in seen:
        return False
    seen.add(marker)

    assignments = _function_assignments(function)
    for node in ast.walk(expression):
        if isinstance(node, ast.Name) and node.id == "LOWER_PENETRANCE_RISK_ALLELE_CATEGORY":
            return True
        if isinstance(node, ast.Name):
            if any(
                _expression_emits_lower_penetrance_category(value, function, functions, seen)
                for value in assignments.get(node.id, [])
            ):
                return True
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            called = functions.get(node.func.id)
            if called is not None and any(
                isinstance(return_node, ast.Return)
                and return_node.value is not None
                and _expression_emits_lower_penetrance_category(
                    return_node.value, called, functions, seen
                )
                for return_node in ast.walk(called)
            ):
                return True
    return False


def _stores_lower_penetrance_category(path: Path) -> bool:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    functions = {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}
    for function in functions.values():
        for node in ast.walk(function):
            if not isinstance(node, ast.Dict):
                continue
            for key, value in zip(node.keys, node.values, strict=True):
                if (
                    isinstance(key, ast.Constant)
                    and key.value == "category"
                    and _expression_emits_lower_penetrance_category(
                        value, function, functions, set()
                    )
                ):
                    return True
    return False


def test_category_emission_detector_ignores_query_only_references(tmp_path: Path) -> None:
    analysis = tmp_path / "analysis.py"
    analysis.write_text(
        """
def query_categories():
    return [LOWER_PENETRANCE_RISK_ALLELE_CATEGORY]

def store_finding():
    return {"category": "monogenic_variant"}
""",
        encoding="utf-8",
    )
    assert _stores_lower_penetrance_category(analysis) is False

    analysis.write_text(
        """
def store_finding():
    return {"category": LOWER_PENETRANCE_RISK_ALLELE_CATEGORY}
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


def test_lower_penetrance_tier_is_documented_on_each_module_page() -> None:
    """Each module returning the distinct tier names it in 'What you'll see' (#2052)."""
    missing: list[str] = []
    for display_name, path in _LOWER_PENETRANCE_MODULES.values():
        doc = path.read_text(encoding="utf-8")
        what_youll_see = doc.split("## What you'll see", 1)[1].split("\n## ", 1)[0]
        normalized = (
            " ".join(what_youll_see.lower().split())
            .replace("lower-penetrance", "lower penetrance")
            .replace("risk-allele", "risk allele")
        )
        if "lower penetrance" not in normalized or "risk allele" not in normalized:
            missing.append(display_name)

    assert not missing, (
        "Module pages returning the distinct ClinVar lower-penetrance/risk-allele tier "
        f"must describe it under 'What you'll see'; missing {missing} (#2052)."
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

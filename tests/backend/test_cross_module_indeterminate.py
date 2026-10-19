"""A withheld Indeterminate call gets no cross-module card, except a drug-safety one (#2310).

Every module's cross-module builder used to skip only ``Standard``, so a
palindromic homozygote the scorer had withheld as ``Indeterminate`` still got a
card telling the user the variant mattered for them, and FTO rs9939609 has no
genotype that skips it at all. Sleep already skipped both; every builder now
decides through :func:`emits_cross_module_card`.

The exception is a drug-safety handoff (Allergy's HLA proxies to
Pharmacogenomics). An unresolved call cannot exclude the risk allele, and
treating it as a no-risk result is the error #1455 corrected for G6PD, so that
card stays and says the call can neither confirm nor exclude the allele.
"""

from __future__ import annotations

import ast
import copy
import importlib
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from backend.analysis.cross_module_links import (
    INDETERMINATE_CATEGORY,
    STANDARD_CATEGORY,
    emits_cross_module_card,
    is_withheld_call,
)

ANALYSIS_DIR = Path(__file__).resolve().parents[2] / "backend" / "analysis"
BUILDER = "_generate_cross_module_findings"

# One cross-linked SNP per module that declares no drug. Sleep declares no
# link today, so its case injects one, as test_sleep.py does.
NON_DRUG_LINKS = {
    "allergy": "rs20541",  # IL13 -> Skin
    "gene_health": "rs9939609",  # FTO -> Metabolic, the issue's case
    "skin": "rs1805009",  # MC1R -> Cancer, a palindromic G/C locus
    "traits": "rs747302",  # DRD4 -> Gene Health
    "sleep": "rs762551",  # CYP1A2, injected link
}


def _module(name: str) -> ModuleType:
    return importlib.import_module(f"backend.analysis.{name}")


def _panel_with_link(name: str, rsid: str) -> tuple[Any, Any, Any]:
    """The module's real panel (copied), its pathway, and the cross-linked SNP."""
    module = _module(name)
    panel = copy.deepcopy(getattr(module, f"load_{name}_panel")())
    pathway = next(pw for pw in panel.pathways if any(s.rsid == rsid for s in pw.snps))
    snp = next(s for s in pathway.snps if s.rsid == rsid)
    if not getattr(snp, "cross_module", None):
        snp.cross_module = {"module": "traits", "note": "probe"}
    return panel, pathway, snp


def _cards(name: str, rsid: str, category: str, genotype: str = "TT") -> list[Any]:
    """Run the module's builder on one called SNP of ``category``."""
    module = _module(name)
    panel, pathway, panel_snp = _panel_with_link(name, rsid)
    snp = module.SNPResult(
        rsid=rsid,
        gene=panel_snp.gene,
        variant_name=getattr(panel_snp, "variant_name", None),
        genotype=genotype,
        category=category,
        effect_summary="probe",
        evidence_level=2,
        pmids=[],
        recommendation_text="",
        present_in_sample=True,
    )
    if name == "traits":
        # Traits reads the link off the scored SNP rather than the panel.
        snp.cross_module = panel_snp.cross_module
    if getattr(panel_snp, "hla_proxy", None):
        snp.hla_proxy = panel_snp.hla_proxy
    result = module.PathwayResult(
        pathway_id=pathway.id, pathway_name=pathway.name, level=category, snp_results=[snp]
    )
    assert result.called_snps, "the fixture must reach the builder to be meaningful"
    args: list[Any] = [[result], panel]
    if name == "allergy":
        args.append({})  # hla_proxy_info: no r² text needed here
    if name == "skin":
        args.append(None)  # mc1r_aggregate: the per-SNP card text
    return getattr(module, BUILDER)(*args)


def test_the_predicate_skips_standard_and_withheld_calls() -> None:
    assert emits_cross_module_card("Moderate") is True
    assert emits_cross_module_card("Elevated") is True
    assert emits_cross_module_card(STANDARD_CATEGORY) is False
    assert emits_cross_module_card(INDETERMINATE_CATEGORY) is False
    # A drug-safety handoff keeps its withheld call, never its non-carrier one.
    assert emits_cross_module_card(INDETERMINATE_CATEGORY, drug_safety_handoff=True) is True
    assert emits_cross_module_card(STANDARD_CATEGORY, drug_safety_handoff=True) is False
    assert is_withheld_call(INDETERMINATE_CATEGORY) is True
    assert is_withheld_call(STANDARD_CATEGORY) is False
    assert is_withheld_call("Moderate") is False


@pytest.mark.parametrize("name", sorted(NON_DRUG_LINKS))
def test_every_module_spells_the_categories_the_predicate_checks(name: str) -> None:
    module = _module(name)
    assert module.STANDARD == STANDARD_CATEGORY
    assert module.INDETERMINATE == INDETERMINATE_CATEGORY


@pytest.mark.parametrize("name", sorted(NON_DRUG_LINKS))
def test_only_a_determinate_carrier_call_gets_a_card(name: str) -> None:
    """Carrier, non-carrier and withheld calls on the same linked SNP."""
    module = _module(name)
    rsid = NON_DRUG_LINKS[name]

    carrier = _cards(name, rsid, module.MODERATE)
    assert [card.rsid for card in carrier] == [rsid]
    assert _cards(name, rsid, module.STANDARD) == []
    assert _cards(name, rsid, module.INDETERMINATE) == []


@pytest.mark.parametrize(
    ("rsid", "allele"),
    [("rs1061235", "HLA-A*31:01"), ("rs144012689", "HLA-B*15:02")],
)
def test_an_indeterminate_hla_proxy_keeps_its_drug_safety_card_with_the_caveat(
    rsid: str, allele: str
) -> None:
    """Both proxies are palindromic, so their homozygotes score Indeterminate.

    Silencing the handoff would treat an unresolved call as a no-risk result
    for carbamazepine; the card stays, says the call can neither confirm nor
    exclude the allele, and still carries the panel's confirmatory-typing note.
    """
    from backend.analysis import allergy

    _, _, panel_snp = _panel_with_link("allergy", rsid)
    unresolved = _cards("allergy", rsid, allergy.INDETERMINATE)
    assert len(unresolved) == 1
    text = unresolved[0].finding_text
    assert f"indeterminate call: it can neither confirm nor exclude {allele}." in text
    assert text.endswith(panel_snp.cross_module["note"])
    assert unresolved[0].detail["drug"] == panel_snp.cross_module["drug"]

    carrier = _cards("allergy", rsid, allergy.MODERATE, genotype="AT")
    assert len(carrier) == 1
    assert "indeterminate" not in carrier[0].finding_text
    assert _cards("allergy", rsid, allergy.STANDARD, genotype="AA") == []


def _builder_modules() -> list[Path]:
    return sorted(
        path
        for path in ANALYSIS_DIR.glob("*.py")
        if f"def {BUILDER}(" in path.read_text(encoding="utf-8")
    )


def test_the_guard_finds_every_known_builder() -> None:
    """Keeps the structural guard below from passing on an empty scan."""
    assert {path.stem for path in _builder_modules()} >= set(NON_DRUG_LINKS)


@pytest.mark.parametrize("path", _builder_modules(), ids=lambda path: path.stem)
def test_every_cross_module_builder_decides_through_the_shared_predicate(path: Path) -> None:
    """A builder that checks categories itself can drift back to skipping only Standard."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    builder = next(
        node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == BUILDER
    )
    calls = {
        node.func.id
        for node in ast.walk(builder)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert "emits_cross_module_card" in calls, f"{path.stem}.{BUILDER} must use the predicate"
    assert _category_label_comparisons(builder) == [], (
        f"{path.stem}.{BUILDER} compares a category with a label itself"
    )


_CATEGORY_LABELS = {"STANDARD", "INDETERMINATE"}


def _category_label_comparisons(function: ast.AST) -> list[str]:
    """Every comparison in ``function`` between a ``.category`` and a category label.

    Operand order and container do not matter: ``x.category == STANDARD``,
    ``STANDARD == x.category`` and ``x.category in (STANDARD, INDETERMINATE)``
    (the shape Sleep used before #2310) are all the same divergent check.
    """
    found = []
    for node in ast.walk(function):
        if not isinstance(node, ast.Compare):
            continue
        names = {
            sub.id
            for operand in (node.left, *node.comparators)
            for sub in ast.walk(operand)
            if isinstance(sub, ast.Name)
        }
        reads_category = any(
            isinstance(sub, ast.Attribute) and sub.attr == "category"
            for operand in (node.left, *node.comparators)
            for sub in ast.walk(operand)
        )
        if reads_category and names & _CATEGORY_LABELS:
            found.append(ast.unparse(node))
    return found


@pytest.mark.parametrize(
    "source",
    [
        "snp.category == STANDARD",
        "STANDARD == snp.category",
        "snp.category != INDETERMINATE",
        "snp.category in (STANDARD, INDETERMINATE)",
        "snp_result.category not in {STANDARD}",
    ],
)
def test_the_guard_catches_every_comparison_shape(source: str) -> None:
    """The structural guard would pass vacuously if it missed a spelling."""
    assert _category_label_comparisons(ast.parse(source)) == [source]


def test_the_guard_ignores_comparisons_that_are_not_category_checks() -> None:
    source = "snp.cross_module is None or drug is not None or level == STANDARD"
    assert _category_label_comparisons(ast.parse(source)) == []

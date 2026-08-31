"""Suite-wide guard: a finding card must not print its gene symbol twice.

Every categorical module builds SNP finding text as "<gene> <variant_name>
(<genotype>) — <effect>". Roughly half the curated ``variant_name`` values
already lead with their own gene ("FTO intron 1", "HLA-B*57:01 proxy"), so the
naive ``f"{snp.gene} {snp.variant_name}"`` rendered "FTO FTO intron 1" on the
primary finding cards of eight modules, the All Findings list, cross-module
cards and any report embedding ``finding_text`` (#2044).

``variant_label`` (``backend/analysis/pathway_coverage.py``) already existed and
already solved this — #2021 introduced it for the cross-module path — but eleven
per-SNP call sites still built the string by hand. So the fix is not to rewrite
51 curated ``variant_name`` values: stripping the gene there would corrupt the
six that are not "GENE <descriptor>", turning ``HLA-B*57:01 proxy`` into
``HLA-B *57:01 proxy`` (standard HLA nomenclature has no space) and
``GC/DBP variant`` into ``GC /DBP variant``.

Two SELF-DISCOVERING guards, in the idiom of the panel-invariant family
(``test_panel_risk_ref_invariant.py``, ``test_indel_polarity_provenance.py``):
no hand-maintained allow-list, so a new panel locus or a new module call site is
covered the moment it lands.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

import backend.analysis.gene_health as gene_health_mod
from backend.analysis.pathway_coverage import variant_label

_ANALYSIS = Path(gene_health_mod.__file__).resolve().parent
_PANELS = _ANALYSIS.parent / "data" / "panels"

# A leading token repeated immediately after itself: "FTO FTO intron 1".
_DOUBLED_LEAD = re.compile(r"^(\S+)\s+\1(\s|$)")

# The hand-rolled template this guard exists to keep out of the codebase.
_NAIVE_TEMPLATE = 'f"{snp.gene} {snp.variant_name}'


def _walk_dicts(node: object):
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _walk_dicts(value)
    elif isinstance(node, list):
        for item in node:
            yield from _walk_dicts(item)


def _panel_gene_variant_pairs() -> list[tuple[str, str, str]]:
    """``(panel, gene, variant_name)`` for every locus carrying both keys."""
    pairs: list[tuple[str, str, str]] = []
    for path in sorted(_PANELS.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):  # pragma: no cover - malformed panel
            continue
        for node in _walk_dicts(data):
            gene = node.get("gene")
            variant = node.get("variant_name")
            if isinstance(gene, str) and isinstance(variant, str) and gene and variant:
                pairs.append((path.name, gene, variant))
    return pairs


def test_panels_actually_exercise_this_guard() -> None:
    """Anti-vacuity: the sweep must reach real gene-prefixed values.

    A guard that walks an empty set, or a set with no gene-prefixed names, would
    pass while proving nothing.
    """
    pairs = _panel_gene_variant_pairs()
    assert len(pairs) > 100, f"expected a substantial panel sweep, walked {len(pairs)}"
    prefixed = [p for p in pairs if p[2].lower().startswith(p[1].lower())]
    assert len(prefixed) >= 40, (
        f"expected the sweep to reach many gene-prefixed variant_names, found {len(prefixed)}"
    )


def test_no_panel_locus_renders_a_doubled_gene() -> None:
    """The rendered label must never repeat its leading token."""
    doubled = [
        f"{panel}: gene={gene!r} variant_name={variant!r} -> {variant_label(gene, variant)!r}"
        for panel, gene, variant in _panel_gene_variant_pairs()
        if _DOUBLED_LEAD.match(variant_label(gene, variant))
    ]
    assert not doubled, "panel loci whose rendered label doubles the gene:\n" + "\n".join(doubled)


def test_no_module_builds_finding_text_by_hand() -> None:
    """Every call site must go through ``variant_label``.

    This is what actually regressed: the helper existed and eleven per-SNP sites
    across eight modules did not use it.
    """
    offenders = [
        path.name
        for path in sorted(_ANALYSIS.glob("*.py"))
        if _NAIVE_TEMPLATE in path.read_text(encoding="utf-8")
    ]
    assert not offenders, (
        "modules building finding text with the naive gene template instead of "
        f"variant_label(): {offenders}"
    )


@pytest.mark.parametrize(
    ("gene", "variant_name", "expected"),
    [
        # Deduped: the variant name already leads with the gene.
        ("FTO", "FTO intron 1", "FTO intron 1"),
        ("DNMT1", "DNMT1 synonymous", "DNMT1 synonymous"),
        # Deduped, and the separator must NOT become a space -- standard HLA
        # nomenclature is "HLA-B*57:01", and "GC/DBP" is the gene's alias pair.
        ("HLA-B", "HLA-B*57:01 proxy", "HLA-B*57:01 proxy"),
        ("HLA-DRB1", "HLA-DRB1*15:01 proxy", "HLA-DRB1*15:01 proxy"),
        ("GC", "GC/DBP variant", "GC/DBP variant"),
        # Prepended: descriptor-only names are the other half of the panels.
        ("MTHFR", "C677T", "MTHFR C677T"),
        ("PPARG", "Pro12Ala", "PPARG Pro12Ala"),
        # Prepended: a DIFFERENT gene that merely shares a prefix must not be
        # swallowed. This is why the fix cannot be "strip any leading gene-like
        # token" -- that would render "IL2RA intron 1" and lose the gene.
        ("IL2", "IL2RA intron 1", "IL2 IL2RA intron 1"),
    ],
)
def test_variant_label_shapes(gene: str, variant_name: str, expected: str) -> None:
    assert variant_label(gene, variant_name) == expected

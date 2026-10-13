"""Docs↔UI guard for the nutrigenomics pathway-level labels (#2077).

``docs/modules/wellness/nutrigenomics.md`` told users each pathway is labelled
*Elevated need*, *Moderate need*, or *Standard*, but the pathway card renders
the ``LEVEL_CONFIG`` labels in
``frontend/src/components/nutrigenomics/PathwayCard.tsx`` — "Elevated
Consideration" / "Moderate Consideration" / "Standard" (#613) — so the "What
you'll see" promise named words the product never shows. This locks the
documented level labels to the live ``LEVEL_CONFIG`` labels in both directions,
and checks the page names the coverage-qualified badges the same card can show
for a Standard pathway (``pathwayLevelDisplayLabel``, #1582).
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
_CARD_SOURCE = REPO_ROOT / "frontend" / "src" / "components" / "nutrigenomics" / "PathwayCard.tsx"
_COVERAGE_SOURCE = REPO_ROOT / "frontend" / "src" / "lib" / "pathwayCoverage.ts"
_DOC_PATH = REPO_ROOT / "docs" / "modules" / "wellness" / "nutrigenomics.md"

# Level labels the doc used before #2077; never rendered anywhere in the product.
_PHANTOM_LEVEL_LABELS = frozenset({"Elevated need", "Moderate need"})


def _live_level_labels() -> set[str]:
    """The level labels the nutrigenomics pathway card renders (its ``LEVEL_CONFIG``)."""
    text = _CARD_SOURCE.read_text(encoding="utf-8")
    match = re.search(r"const\s+LEVEL_CONFIG\b[^=]*=\s*\{(.*?)\n\}", text, re.DOTALL)
    assert match, f"Could not locate LEVEL_CONFIG in {_CARD_SOURCE}"
    labels = set(re.findall(r'label:\s*"([^"]+)"', match.group(1)))
    assert labels, "LEVEL_CONFIG parsed but no `label:` entries were found"
    return labels


def _coverage_badge_labels() -> set[str]:
    """Badges ``pathwayLevelDisplayLabel`` substitutes for a Standard pathway."""
    text = _COVERAGE_SOURCE.read_text(encoding="utf-8")
    match = re.search(r"export function pathwayLevelDisplayLabel\(.*?\n\}", text, re.DOTALL)
    assert match, f"Could not locate pathwayLevelDisplayLabel in {_COVERAGE_SOURCE}"
    badges = set(re.findall(r'"([^"]+)"', match.group(0))) - {"Standard"}
    assert badges, "pathwayLevelDisplayLabel parsed but returns no literal badge"
    return badges


def _what_youll_see() -> str:
    text = _DOC_PATH.read_text(encoding="utf-8")
    match = re.search(r"^## What you'll see\n(.*?)(?=^## |\Z)", text, re.DOTALL | re.MULTILINE)
    assert match, f"No '## What you'll see' section in {_DOC_PATH}"
    return match.group(1)


def _documented_level_labels() -> set[str]:
    """Italic labels in the ``A **level** per pathway — … —`` sentence."""
    section = " ".join(_what_youll_see().split())
    match = re.search(r"A \*\*level\*\* per pathway — (.*?) —", section)
    assert match, "Could not find the 'A **level** per pathway — … —' sentence"
    labels = set(re.findall(r"\*([^*]+)\*", match.group(1)))
    assert labels, "The level sentence names no italic labels"
    return labels


def test_doc_names_every_rendered_level_label() -> None:
    missing = sorted(_live_level_labels() - _documented_level_labels())
    assert not missing, (
        f"docs/modules/wellness/nutrigenomics.md does not name level label(s) the "
        f"pathway card renders: {missing}. Match LEVEL_CONFIG in "
        f"frontend/src/components/nutrigenomics/PathwayCard.tsx (#2077)."
    )


def test_doc_names_no_unrendered_level_label() -> None:
    phantom = sorted(_documented_level_labels() - _live_level_labels())
    assert not phantom, (
        f"docs/modules/wellness/nutrigenomics.md names level label(s) the pathway "
        f"card never renders: {phantom} (#2077)."
    )


def test_doc_names_the_coverage_qualified_badges() -> None:
    section = " ".join(_what_youll_see().split())
    missing = sorted(b for b in _coverage_badge_labels() if b not in section)
    assert not missing, (
        f"nutrigenomics.md's 'What you'll see' omits coverage badge(s) the pathway "
        f"card can show for a Standard pathway: {missing} (pathwayLevelDisplayLabel)."
    )


def test_phantom_labels_are_gone_and_really_unrendered() -> None:
    doc = " ".join(_DOC_PATH.read_text(encoding="utf-8").split())
    assert not [p for p in _PHANTOM_LEVEL_LABELS if p in doc]
    # Guards the premise: if one of these becomes a real label, revisit this test.
    assert not (_PHANTOM_LEVEL_LABELS & _live_level_labels())

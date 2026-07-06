"""Docs↔UI consistency guard for the Variant Detail page tab list (#1545).

``docs/features/variant-detail.md`` described a six-tab layout that did not match
the page's actual ``TABS`` array in
``frontend/src/pages/VariantDetailPage.tsx``: it renamed **Population** to
"Frequencies", invented standalone **Scores** and **Gene** tabs (that content
lives under Overview / Clinical), and omitted the real **Clinical** and
**Genome** tabs. This locks the documented tab names to the live ``TABS`` labels
so the page can never again document a tab that does not exist — or omit one that
does — in either direction.

(The separate concern of the two *stub* tabs — Literature/Protein — being
described as finished features is fixed in the same doc rewrite; this guard only
covers the tab-name set, which is the mechanical part that silently drifts.)
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
_TABS_SOURCE = REPO_ROOT / "frontend" / "src" / "pages" / "VariantDetailPage.tsx"
_DOC_PATH = REPO_ROOT / "docs" / "features" / "variant-detail.md"

# Tab names dropped from the docs in #1545 because they were never real tabs.
# Named so the regression stays legible; the set-equality tests below enforce it
# generally (documented tab names must equal the live TABS labels).
_PHANTOM_TAB_NAMES = frozenset({"Frequencies", "Scores", "Gene"})


def _live_tab_labels() -> list[str]:
    """The tab labels the Variant Detail page actually renders (its ``TABS``)."""
    text = _TABS_SOURCE.read_text(encoding="utf-8")
    match = re.search(r"const\s+TABS\b[^=]*=\s*\[(.*?)\]", text, re.DOTALL)
    assert match, f"Could not locate the TABS array in {_TABS_SOURCE}"
    labels = re.findall(r'label:\s*"([^"]+)"', match.group(1))
    assert labels, "TABS array parsed but no `label:` entries were found"
    return labels


def _documented_tab_names() -> set[str]:
    """Bold tab names in the doc's numbered tab list (``N. **Name** — ...``)."""
    text = _DOC_PATH.read_text(encoding="utf-8")
    return set(re.findall(r"^\d+\.\s+\*\*([^*]+)\*\*", text, re.MULTILINE))


def test_doc_documents_every_live_tab() -> None:
    documented = _documented_tab_names()
    missing = [label for label in _live_tab_labels() if label not in documented]
    assert not missing, (
        f"docs/features/variant-detail.md does not document tab(s) the Variant "
        f"Detail page renders: {missing}. Update the numbered tab list to match "
        f"the TABS array in frontend/src/pages/VariantDetailPage.tsx (#1545)."
    )


def test_doc_documents_no_phantom_tab() -> None:
    phantom = sorted(_documented_tab_names() - set(_live_tab_labels()))
    assert not phantom, (
        f"docs/features/variant-detail.md documents tab(s) absent from the live "
        f"TABS array: {phantom}. These are not real tabs (e.g. 'Scores'/'Gene' "
        f"content renders under Overview/Clinical) — see #1545."
    )


def test_phantom_names_really_are_absent_from_tabs() -> None:
    # Guards the test's own premise: if a former phantom name is ever added back
    # as a real TABS label, this trips so the assertions above are revisited.
    live = set(_live_tab_labels())
    assert not (_PHANTOM_TAB_NAMES & live), (
        f"A tab name previously documented-but-nonexistent is now live in TABS: "
        f"{sorted(_PHANTOM_TAB_NAMES & live)}. Revisit test_variant_detail_docs_"
        f"consistency and the doc."
    )

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
_REPORTS_DOC_PATH = REPO_ROOT / "docs" / "features" / "reports.md"
_VARIANT_DETAIL_COMPONENT_ROOT = REPO_ROOT / "frontend" / "src" / "components" / "variant-detail"

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


# HGVS notation (c./p.) is shown verbatim on the Overview/Protein tabs
# (hgvs_coding/hgvs_protein) but was undefined anywhere in the docs; the note added
# in #1593 must explain the notation with worked examples so a lay user can read it.
_HGVS_DOC_TOKENS = ("hgvs", "c.665c>t", "p.ala222val", "coding-dna", "amino-acid")


def test_doc_explains_hgvs_notation() -> None:
    text = _DOC_PATH.read_text(encoding="utf-8").lower()
    missing = [t for t in _HGVS_DOC_TOKENS if t not in text]
    assert not missing, (
        "docs/features/variant-detail.md no longer explains HGVS c./p. notation "
        f"(missing {missing}). The Overview/Protein tabs render hgvs_coding/hgvs_protein "
        "verbatim — keep the 'Understanding HGVS notation' key (#1593)."
    )


def _variant_detail_ui_sources() -> list[Path]:
    """Frontend sources that can honestly support a Variant Detail export claim."""
    component_sources = [
        path
        for path in _VARIANT_DETAIL_COMPONENT_ROOT.rglob("*")
        if path.suffix in {".ts", ".tsx"} and ".test." not in path.name
    ]
    return [_TABS_SOURCE, *component_sources]


def _variant_detail_ui_has_variant_card_caller() -> bool:
    """Whether Variant Detail UI sources call the finding-keyed variant-card endpoints."""
    for path in _variant_detail_ui_sources():
        text = path.read_text(encoding="utf-8")
        if "/api/reports/variant-card" in text or "reports/variant-card" in text:
            return True
    return False


def test_docs_do_not_claim_variant_detail_card_export_without_ui_caller() -> None:
    if _variant_detail_ui_has_variant_card_caller():
        return

    docs = {
        "docs/features/variant-detail.md": _DOC_PATH.read_text(encoding="utf-8").lower(),
        "docs/features/reports.md": _REPORTS_DOC_PATH.read_text(encoding="utf-8").lower(),
    }
    forbidden = {
        "docs/features/variant-detail.md": ("generate a single-variant", "from this page"),
        "docs/features/reports.md": ("evidence cards", "from any", "variant detail"),
    }
    offenders = [
        doc for doc, tokens in forbidden.items() if all(token in docs[doc] for token in tokens)
    ]
    assert not offenders, (
        "Docs claim variant-detail evidence-card export, but Variant Detail UI has no caller for "
        "the finding-keyed /api/reports/variant-card endpoints (#1622): "
        f"{offenders}."
    )

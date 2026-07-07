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

from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_DOC = _REPO / "docs" / "modules" / "interpretation-reference.md"
_SRC = _REPO / "frontend" / "src" / "lib" / "pathwayCoverage.ts"
_RARE_VARIANT_PANEL = (
    _REPO / "frontend" / "src" / "components" / "rare-variants" / "VariantDetailPanel.tsx"
)

# The coverage-qualified level-badge strings pathwayLevelDisplayLabel can emit.
_COVERAGE_BADGES = ("Tested Standard", "Not Assessed")

# Direction/scale tokens the in-silico-score note must carry so CADD/REVEL aren't
# shown as uninterpretable bare numbers (#1589).
_IN_SILICO_TOKENS = ("cadd", "revel", "phred", "deleterious", "pathogenic")

# The Rare Variant panel's CADD/REVEL red-highlight thresholds, mirrored in the doc.
_UI_SCORE_THRESHOLDS = ("cadd_phred >= 20", "revel >= 0.5")


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

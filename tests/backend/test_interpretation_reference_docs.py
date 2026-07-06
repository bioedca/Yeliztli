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

# The coverage-qualified level-badge strings pathwayLevelDisplayLabel can emit.
_COVERAGE_BADGES = ("Tested Standard", "Not Assessed")


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

"""Documentation guards for Findings Explorer filter claims."""

from __future__ import annotations

from pathlib import Path

DOCS_ROOT = Path(__file__).resolve().parent.parent.parent / "docs"
READING_RESULTS = DOCS_ROOT / "getting-started" / "reading-your-results.md"

UNSUPPORTED_FINDINGS_EXPLORER_CLAIMS = (
    "search and filter findings",
    "by evidence rating, module, gene, or phenotype",
    "by module, gene, or phenotype",
    "by gene or phenotype",
    "gene filter",
    "phenotype filter",
    "search box",
)


def _findings_explorer_section() -> str:
    text = READING_RESULTS.read_text(encoding="utf-8")
    start = text.index("## The Findings Explorer")
    try:
        end = text.index("\n## ", start + 1)
    except ValueError:
        end = len(text)
    return text[start:end]


def test_findings_explorer_docs_match_current_filter_surface() -> None:
    section = _findings_explorer_section()
    lowered = section.lower()

    assert "module" in lowered
    assert "evidence rating" in lowered

    unsupported_claims = [
        claim for claim in UNSUPPORTED_FINDINGS_EXPLORER_CLAIMS if claim in lowered
    ]
    assert unsupported_claims == []

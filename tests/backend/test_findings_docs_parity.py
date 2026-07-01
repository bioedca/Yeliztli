"""Documentation guards for Findings Explorer filter claims."""

from __future__ import annotations

import re
from pathlib import Path

DOCS_ROOT = Path(__file__).resolve().parent.parent.parent / "docs"
READING_RESULTS = DOCS_ROOT / "getting-started" / "reading-your-results.md"

SUPPORTED_FILTER_PATTERN = re.compile(
    r"\bfilter findings across every module at once by module and minimum "
    r"(?:evidence rating|evidence level|star rating)\b",
    re.IGNORECASE,
)

UNSUPPORTED_FINDINGS_EXPLORER_CLAIM_PATTERNS = {
    "search": re.compile(
        r"\b(?:search|searchable|searching|free[- ]text|search box)\b",
        re.IGNORECASE,
    ),
    "gene_filter": re.compile(
        r"\b(?:by|filter(?:s|ed|ing|able)?)\b[^.\n]*(?:gene|gene symbol)"
        r"|(?:gene|gene symbol)[^.\n]*\bfilter(?:s|ed|ing|able)?\b",
        re.IGNORECASE,
    ),
    "phenotype_filter": re.compile(
        r"\b(?:by|filter(?:s|ed|ing|able)?)\b[^.\n]*(?:phenotype|condition)"
        r"|(?:phenotype|condition)[^.\n]*\bfilter(?:s|ed|ing|able)?\b",
        re.IGNORECASE,
    ),
}


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
    normalized = re.sub(r"\s+", " ", section)

    assert SUPPORTED_FILTER_PATTERN.search(normalized)

    unsupported_claims = [
        claim
        for claim, pattern in UNSUPPORTED_FINDINGS_EXPLORER_CLAIM_PATTERNS.items()
        if pattern.search(section)
    ]
    assert unsupported_claims == []

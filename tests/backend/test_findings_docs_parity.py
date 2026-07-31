"""Documentation guards for Findings Explorer filter claims."""

from __future__ import annotations

import re
from pathlib import Path

DOCS_ROOT = Path(__file__).resolve().parent.parent.parent / "docs"
READING_RESULTS = DOCS_ROOT / "getting-started" / "reading-your-results.md"
MODULES_INDEX = DOCS_ROOT / "modules" / "index.md"
SPECIALIZED_FINDINGS = DOCS_ROOT / "modules" / "specialized.md"

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


def _markdown_section(path: Path, heading: str) -> str:
    text = path.read_text(encoding="utf-8")
    start = text.index(heading)
    try:
        end = text.index("\n## ", start + len(heading))
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


def test_specialized_docs_distinguish_automatic_findings_from_api_context() -> None:
    expected_contract = "Eight of these condition-specific modules run automatically"
    on_demand_contract = "G6PD and BChE are read-only, on-demand API context modules"
    storage_contract = (
        "Neither is part of the standard analysis or stores a Findings Explorer entry"
    )

    for path, heading in (
        (MODULES_INDEX, "## Specialized findings"),
        (SPECIALIZED_FINDINGS, "# Specialized findings"),
    ):
        section = re.sub(r"\s+", " ", _markdown_section(path, heading))
        assert expected_contract in section
        assert on_demand_contract in section
        assert storage_contract in section

    for heading, endpoint in (
        ("## G6PD deficiency", "GET /api/analysis/g6pd?sample_id=<id>"),
        ("## BChE (butyrylcholinesterase)", "GET /api/analysis/bche?sample_id=<id>"),
    ):
        section = re.sub(
            r"\s+",
            " ",
            _markdown_section(SPECIALIZED_FINDINGS, heading),
        )
        assert "not part of the standard analysis" in section
        assert "does not store a Findings Explorer entry" in section
        assert endpoint in section

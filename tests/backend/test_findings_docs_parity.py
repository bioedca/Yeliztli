"""Documentation guards for Findings Explorer filter claims."""

from __future__ import annotations

import re
from pathlib import Path

import sqlalchemy as sa

from backend.analysis.bche import (
    BCHE_ATYPICAL_ALT,
    BCHE_ATYPICAL_RSID,
    BCHE_K_REF,
    BCHE_K_RSID,
    assess_bche,
)
from backend.analysis.g6pd import G6PD_A_MINUS_DEF, G6PD_A_MINUS_RSID, assess_g6pd
from backend.analysis.run_all import _get_modules
from backend.db.sample_schema import create_sample_tables
from backend.db.tables import findings, raw_variants

DOCS_ROOT = Path(__file__).resolve().parent.parent.parent / "docs"
READING_RESULTS = DOCS_ROOT / "getting-started" / "reading-your-results.md"
MODULES_INDEX = DOCS_ROOT / "modules" / "index.md"
SPECIALIZED_FINDINGS = DOCS_ROOT / "modules" / "specialized.md"

AUTOMATIC_SPECIALIZED_MODULES = {
    "hemochromatosis",
    "thrombophilia",
    "alpha1",
    "amd",
    "apol1",
    "gout",
    "lhon",
    "mt_rnr1",
}
ON_DEMAND_CONTEXT_MODULES = {"g6pd", "bche"}

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
    evidence_section = _markdown_section(
        READING_RESULTS,
        "## Findings and evidence ratings",
    )

    assert SUPPORTED_FILTER_PATTERN.search(normalized)
    assert "Findings-producing analysis modules emit **findings**" in evidence_section
    assert "Every analysis module produces **findings**" not in evidence_section

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
    module_overview = re.sub(
        r"\s+",
        " ",
        _markdown_section(MODULES_INDEX, "# Module reference"),
    )
    runner_modules = {name for name, _runner in _get_modules()}

    assert len(AUTOMATIC_SPECIALIZED_MODULES) == 8
    assert AUTOMATIC_SPECIALIZED_MODULES <= runner_modules
    assert runner_modules.isdisjoint(ON_DEMAND_CONTEXT_MODULES)
    assert "Findings-producing modules load curated panels of variants" in module_overview
    assert (
        "Read-only, on-demand API context modules such as G6PD and BChE "
        "instead return summaries without storing findings"
    ) in module_overview
    assert "Modules come in four kinds" in module_overview

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


def test_on_demand_context_assessments_leave_findings_unchanged() -> None:
    engine = sa.create_engine("sqlite://")
    create_sample_tables(engine)

    with engine.begin() as connection:
        connection.execute(
            raw_variants.insert(),
            [
                {
                    "rsid": G6PD_A_MINUS_RSID,
                    "chrom": "X",
                    "pos": 1,
                    "genotype": G6PD_A_MINUS_DEF * 2,
                },
                {
                    "rsid": BCHE_ATYPICAL_RSID,
                    "chrom": "3",
                    "pos": 2,
                    "genotype": BCHE_ATYPICAL_ALT * 2,
                },
                {
                    "rsid": BCHE_K_RSID,
                    "chrom": "3",
                    "pos": 3,
                    "genotype": BCHE_K_REF * 2,
                },
            ],
        )
        connection.execute(
            findings.insert().values(
                module="sentinel",
                finding_text="Existing finding",
            )
        )
        before = [dict(row) for row in connection.execute(sa.select(findings)).mappings().all()]

    g6pd_result = assess_g6pd(engine)
    bche_result = assess_bche(engine)

    assert g6pd_result["context_only"] is True
    assert g6pd_result["any_called"] is True
    assert g6pd_result["at_risk"] is True
    assert bche_result["context_only"] is True
    assert bche_result["any_called"] is True
    assert bche_result["risk_category"] == "high"

    with engine.connect() as connection:
        after = [dict(row) for row in connection.execute(sa.select(findings)).mappings().all()]

    assert after == before
    engine.dispose()

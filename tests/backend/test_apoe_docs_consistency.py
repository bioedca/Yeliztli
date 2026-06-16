"""APOE docs reliability guard (#884).

The backend treats rs429358/rs7412 as locus-specific low-reliability consumer-array
calls. The public gated-module page must not drift back to the old "reliably typed"
claim, and the user-facing caveat needs to carry the same evidence handles.
"""

from __future__ import annotations

from pathlib import Path

from backend.analysis.array_confidence import (
    APOE_ARRAY_CONCORDANCE,
    APOE_ARRAY_RELIABILITY_PMIDS,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
_DOC_PATH = REPO_ROOT / "docs" / "modules" / "gated" / "apoe.md"


def _doc_text() -> str:
    return _DOC_PATH.read_text(encoding="utf-8")


def test_apoe_docs_do_not_claim_epsilon_snps_are_reliably_typed() -> None:
    text = _doc_text().lower()
    assert "both reliably typed on consumer arrays" not in text
    assert "reliably typed on consumer arrays" not in text


def test_apoe_docs_carry_backend_array_reliability_caveat() -> None:
    text = _doc_text()
    lower = text.lower()

    assert "rs429358" in text
    assert "rs7412" in text
    assert "provisional" in lower
    assert "CLIA/accredited" in text
    assert APOE_ARRAY_CONCORDANCE in text

    for pmid in APOE_ARRAY_RELIABILITY_PMIDS:
        assert f"PMID {pmid}" in text
        assert f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/" in text

"""Documentation guards for APOB's direction-specific cardiovascular scope."""

from __future__ import annotations

import json
import re
from pathlib import Path

from backend.analysis.cardiovascular import (
    CATEGORY_FH,
    CATEGORY_LIPID,
    _variant_condition_scope,
    load_cardiovascular_panel,
)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DOCS_ROOT = REPO_ROOT / "docs"
CARDIOVASCULAR_DOC = DOCS_ROOT / "modules" / "health-risk" / "cardiovascular.md"
FH_DOC = DOCS_ROOT / "modules" / "health-risk" / "familial-hypercholesterolemia.md"
EVIDENCE_DIR = REPO_ROOT / "data" / "science-evidence" / "2026-07-31-apob-low-ldl-2075"

LOW_LDL_SCOPE = (
    "APOB findings with low-LDL-only ClinVar labels—and protein-truncating APOB variants "
    "linked to familial hypobetalipoproteinemia even when ClinVar labels aggregate both "
    "directions—are reported under Other lipid metabolism and excluded from FH status."
)
FH_SCOPE = "including its ClinVar condition-label and lower-penetrance/risk-allele filters."
SOURCE_URLS = {
    "https://doi.org/10.1161/CIRCGEN.118.002376",
    "https://doi.org/10.1001/jamacardio.2022.5271",
}


def _plain_markdown(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    return re.sub(r"\s+", " ", re.sub(r"[*_`]", "", text))


def test_apob_docs_match_direction_specific_production_scope() -> None:
    panel = load_cardiovascular_panel()
    apob = panel.get_gene("APOB")

    assert apob is not None
    assert apob.conditions

    low_ldl_conditions, low_ldl_category = _variant_condition_scope(
        apob,
        "Familial hypobetalipoproteinemia 1",
        "stop_gained",
    )
    fh_conditions, fh_category = _variant_condition_scope(
        apob,
        "Familial hypercholesterolemia",
        "missense_variant",
    )
    low_ldl_missense_conditions, low_ldl_missense_category = _variant_condition_scope(
        apob,
        "Familial hypobetalipoproteinemia 1",
        "missense_variant",
    )
    unlabeled_conditions, unlabeled_category = _variant_condition_scope(
        apob,
        None,
        "missense_variant",
    )

    assert low_ldl_conditions == ["Familial hypobetalipoproteinemia 1"]
    assert low_ldl_category == CATEGORY_LIPID
    assert fh_conditions == apob.conditions
    assert fh_category == CATEGORY_FH
    assert low_ldl_missense_conditions == ["Familial hypobetalipoproteinemia 1"]
    assert low_ldl_missense_category == CATEGORY_LIPID
    assert unlabeled_conditions == apob.conditions
    assert unlabeled_category == CATEGORY_FH

    for path in (CARDIOVASCULAR_DOC, FH_DOC):
        documentation = _plain_markdown(path)
        assert LOW_LDL_SCOPE in documentation
        assert FH_SCOPE in documentation
        assert "(accessed 2026-07-31)" in documentation
        for source_url in SOURCE_URLS:
            assert source_url in documentation


def test_apob_evidence_packet_retains_reproducible_public_payloads() -> None:
    manifest = (EVIDENCE_DIR / "README.md").read_text(encoding="utf-8")

    assert "JSON schema version `0.3`" in manifest
    assert "https://www.ncbi.nlm.nih.gov/home/about/policies/" in manifest
    assert "https://www.ncbi.nlm.nih.gov/books/NBK25497/" in manifest

    correction_path = EVIDENCE_DIR / "pubmed-comments-corrections-extract.json"
    correction_payload = json.loads(correction_path.read_text(encoding="utf-8"))
    correction_records = {record["pmid"]: record for record in correction_payload["records"]}

    assert str(correction_path.relative_to(REPO_ROOT)) in manifest
    assert correction_payload["source"] == "NCBI PubMed EFetch"
    assert correction_payload["response_dtd"]["date"] == "2025-01-01"
    assert (
        correction_payload["extraction_path"]
        == "PubmedArticle/MedlineCitation/CommentsCorrectionsList"
    )

    for pmid in ("30939045", "36723951"):
        path = EVIDENCE_DIR / f"pubmed-{pmid}-esummary.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        correction_record = correction_records[pmid]

        assert str(path.relative_to(REPO_ROOT)) in manifest
        assert payload["header"] == {"type": "esummary", "version": "0.3"}
        assert payload["result"]["uids"] == [pmid]
        assert correction_record["comments_corrections_list_present"] is False
        assert correction_record["comments_corrections"] == []

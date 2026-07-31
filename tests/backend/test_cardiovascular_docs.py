"""Documentation guards for APOB's direction-specific cardiovascular scope."""

from __future__ import annotations

import hashlib
import json
import re
import xml.etree.ElementTree as ET
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
EXPECTED_EVIDENCE = {
    "30939045": {
        "doi": "10.1161/CIRCGEN.118.002376",
        "citation": ("PMID:30939045; DOI:10.1161/CIRCGEN.118.002376 (accessed 2026-07-31)"),
        "request": (
            "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
            "?db=pubmed&id=30939045&retmode=xml"
        ),
        "date_revised": "2025-05-30",
        "title": (
            "Rare Protein-Truncating Variants in APOB, Lower Low-Density Lipoprotein "
            "Cholesterol, and Protection Against Coronary Heart Disease."
        ),
        "source": "Circ Genom Precis Med",
        "pubdate": "2019 May",
    },
    "36723951": {
        "doi": "10.1001/jamacardio.2022.5271",
        "citation": ("PMID:36723951; DOI:10.1001/jamacardio.2022.5271 (accessed 2026-07-31)"),
        "request": (
            "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
            "?db=pubmed&id=36723951&retmode=xml"
        ),
        "date_revised": "2025-05-30",
        "title": (
            "Association of Rare Protein-Truncating DNA Variants in APOB or PCSK9 With "
            "Low-density Lipoprotein Cholesterol Level and Risk of Coronary Heart Disease."
        ),
        "source": "JAMA Cardiol",
        "pubdate": "2023 Mar 1",
    },
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
    assert (
        "The `Abstract`, `OtherAbstract`, `ReferenceList`, and `CoiStatement` "
        "publisher-text containers are not redistributed."
    ) in re.sub(r"\s+", " ", manifest)

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
    source_snapshot = correction_payload["source_snapshot"]
    snapshot_path = REPO_ROOT / source_snapshot["path"]
    snapshot_bytes = snapshot_path.read_bytes()
    snapshot_text = snapshot_bytes.decode("utf-8")
    snapshot_root = ET.fromstring(snapshot_text)

    assert source_snapshot == {
        "path": ("data/science-evidence/2026-07-31-apob-low-ldl-2075/pubmed-efetch-sanitized.xml"),
        "request": (
            "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
            "?db=pubmed&id=30939045%2C36723951&retmode=xml"
        ),
        "raw_sha256": "4bb34a3edc93a349fcc6b07c4f5bff1665d87cfdffe84cf01b7161cd170484df",
        "sanitized_sha256": ("a47b8b6058f116067a3d56d2bf332812508c6dced6253fc92ebb6902fba533f4"),
        "removed_elements": {
            "Abstract": 2,
            "OtherAbstract": 0,
            "ReferenceList": 2,
            "CoiStatement": 1,
        },
        "preserved_if_present": ["CommentsCorrectionsList"],
    }
    assert str(Path(source_snapshot["path"])) in manifest
    assert hashlib.sha256(snapshot_bytes).hexdigest() == source_snapshot["sanitized_sha256"]
    assert snapshot_root.tag == "PubmedArticleSet"
    assert snapshot_root.findall(".//Abstract") == []
    assert snapshot_root.findall(".//OtherAbstract") == []
    assert snapshot_root.findall(".//ReferenceList") == []
    assert snapshot_root.findall(".//CoiStatement") == []
    assert snapshot_root.findall(".//CommentsCorrectionsList") == []
    assert set(correction_records) == set(EXPECTED_EVIDENCE)
    source_articles = {
        article.findtext("MedlineCitation/PMID"): article
        for article in snapshot_root.findall("./PubmedArticle")
    }
    assert set(source_articles) == set(EXPECTED_EVIDENCE)

    for pmid, expected in EXPECTED_EVIDENCE.items():
        path = EVIDENCE_DIR / f"pubmed-{pmid}-esummary.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        correction_record = correction_records[pmid]
        summary = payload["result"][pmid]
        source_article = source_articles[pmid]
        source_citation = source_article.find("MedlineCitation")
        assert source_citation is not None
        source_doi = next(
            article_id.text
            for article_id in source_article.findall("PubmedData/ArticleIdList/ArticleId")
            if article_id.attrib["IdType"] == "doi"
        )

        assert str(path.relative_to(REPO_ROOT)) in manifest
        assert payload["header"] == {"type": "esummary", "version": "0.3"}
        assert payload["result"]["uids"] == [pmid]
        assert summary["uid"] == pmid
        assert summary["title"] == expected["title"]
        assert summary["source"] == expected["source"]
        assert summary["pubdate"] == expected["pubdate"]
        assert {
            identifier["value"]
            for identifier in summary["articleids"]
            if identifier["idtype"] == "doi"
        } == {expected["doi"]}
        assert correction_record["doi"] == expected["doi"]
        assert correction_record["citation"] == expected["citation"]
        assert correction_record["request"] == expected["request"]
        assert correction_record["date_revised"] == expected["date_revised"]
        assert correction_record["comments_corrections_list_present"] is False
        assert correction_record["comments_corrections"] == []
        assert source_citation.findtext("DateRevised/Year") == "2025"
        assert source_citation.findtext("DateRevised/Month") == "05"
        assert source_citation.findtext("DateRevised/Day") == "30"
        assert source_citation.find("CommentsCorrectionsList") is None
        assert source_doi == expected["doi"]

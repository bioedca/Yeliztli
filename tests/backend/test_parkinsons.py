"""Tests for the Parkinson's (LRRK2 G2019S) risk module.

LRRK2 G2019S is rs34637584 (ref G, risk A on the GRCh37 plus strand). Guardrails
under test: a present G2019S fires a single risk-factor finding stored at
evidence_level 2 (so it stays out of the ungated dashboard high-confidence top-5,
behind the ethical gate); the finding frames reduced penetrance and no preventive
treatment ("not a diagnosis and not a prediction"); GBA1 is absent from the panel
(deliberately suppressed); and findings write clinvar_significance=NULL.
"""

from __future__ import annotations

import hashlib
import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
import sqlalchemy as sa

from backend.analysis.parkinsons import (
    assess_parkinsons,
    load_parkinsons_panel,
    store_parkinsons_findings,
)
from backend.db.tables import findings, raw_variants
from backend.disclaimers import PARKINSONS_GATE_TEXT

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DOC_PATH = _REPO_ROOT / "docs" / "modules" / "gated" / "parkinsons.md"
_EVIDENCE_DIR = _REPO_ROOT / "data" / "science-evidence" / "2026-07-31-lrrk2-penetrance-docs-2091"
_EXPECTED_EVIDENCE = {
    "26062626": {
        "doi": "10.1212/WNL.0000000000001708",
        "age_80_estimate": "26% (95% CI 18-36%)",
        "source_tokens": ["0.26", "0.18-0.36", "to age 80 years"],
    },
    "28639421": {
        "doi": "10.1002/mds.27059",
        "age_80_estimate": "42.5% (95% CI 26.3-65.8%)",
        "source_tokens": ["42.5%", "26.3%-65.8%", "to age 80", "25%"],
    },
    "38804604": {
        "doi": "10.1093/brain/awae073",
        "age_80_estimate": "49%",
        "source_tokens": ["by age 80 was 49%"],
    },
    "40926580": {
        "doi": "10.1002/acn3.70176",
        "age_80_estimate": "24%",
        "source_tokens": ["age of 80", "24% for LRRK2 p.G2019S carriers"],
    },
}
_EXPECTED_METADATA_SHA256 = "6c924c787b5a81195a5e36ee1c1f80833819bcfa43f9e9640329f8e4b8f34e43"
_EXPECTED_SOURCE_SHA256 = "5661b8aaf49fb8ac3551a6bc0464c00a4e7db2b12949db70708c6be60e119d8b"
_EXPECTED_CLAIM_SHA256 = "f82aac09667ee55eccea3a07e34713ce916a704c5dcd7605ab9c67de93dd34ae"


def _normalized_ranges(text: str) -> str:
    return text.replace("–", "-").replace("—", "-")


@pytest.fixture()
def panel():
    return load_parkinsons_panel()


def _seed(engine: sa.Engine, rows: list[dict]) -> None:
    if rows:
        with engine.begin() as conn:
            conn.execute(sa.insert(raw_variants), rows)


def _lrrk2(genotype: str) -> dict:  # ref G / risk A
    return {"rsid": "rs34637584", "chrom": "12", "pos": 40734202, "genotype": genotype}


class TestPanelScope:
    def test_panel_only_has_lrrk2_no_gba1(self, panel) -> None:
        assert panel.rsids == ["rs34637584"]
        genes = {loc.gene_symbol for loc in panel.loci}
        assert genes == {"LRRK2"}
        assert "GBA1" not in genes and "GBA" not in genes


class TestDetection:
    def test_heterozygous_fires(self, panel, sample_engine: sa.Engine) -> None:
        _seed(sample_engine, [_lrrk2("GA")])
        a = assess_parkinsons(panel, sample_engine)
        assert len(a.calls) == 1
        assert a.calls[0].evidence_stars == 2
        assert "G2019S" in a.calls[0].finding_text

    def test_homozygous_fires(self, panel, sample_engine: sa.Engine) -> None:
        _seed(sample_engine, [_lrrk2("AA")])
        a = assess_parkinsons(panel, sample_engine)
        assert len(a.calls) == 1

    def test_minus_strand_equivalent(self, panel, sample_engine: sa.Engine) -> None:
        # "CT" is the reverse-strand complement of plus-strand "GA".
        _seed(sample_engine, [_lrrk2("CT")])
        a = assess_parkinsons(panel, sample_engine)
        assert len(a.calls) == 1

    def test_reference_no_finding(self, panel, sample_engine: sa.Engine) -> None:
        _seed(sample_engine, [_lrrk2("GG")])
        a = assess_parkinsons(panel, sample_engine)
        assert a.calls == []

    def test_no_call_indeterminate(self, panel, sample_engine: sa.Engine) -> None:
        _seed(sample_engine, [_lrrk2("--")])
        a = assess_parkinsons(panel, sample_engine)
        assert "rs34637584" in a.indeterminate_loci
        assert a.calls == []


class TestEthicalFraming:
    def test_no_prevention_and_not_a_prediction(self, panel, sample_engine: sa.Engine) -> None:
        _seed(sample_engine, [_lrrk2("GA")])
        a = assess_parkinsons(panel, sample_engine)
        corpus = a.calls[0].finding_text.lower()
        corpus += " " + " ".join(a.calls[0].detail["caveats"]).lower()
        assert "not a diagnosis and not a prediction" in corpus
        assert "no proven way to prevent" in corpus
        assert "penetrance" in corpus


class TestPenetranceCommunication:
    def test_user_facing_surfaces_match_panel_age_80_estimates(self, panel) -> None:
        model = panel.genotype_models[0]
        docs = _DOC_PATH.read_text(encoding="utf-8")
        surfaces = {
            "panel description": panel.description,
            "finding": model.finding_text,
            "consent gate": PARKINSONS_GATE_TEXT,
            "module docs": docs,
        }

        for name, text in surfaces.items():
            normalized = _normalized_ranges(text)
            assert "24-49%" in normalized, f"{name} dropped the recent-cohort range"
            assert "25-42.5%" in normalized, f"{name} dropped the kin-cohort range"
            assert "by age 80" in normalized.lower(), f"{name} dropped the age-80 qualifier"
            assert "most carriers never develop" not in normalized.lower()
            assert "many carriers never develop" not in normalized.lower()

        cited_pmids = set(re.findall(r"pubmed\.ncbi\.nlm\.nih\.gov/(\d+)", docs))
        assert cited_pmids == set(model.pmids)
        assert docs.count("(accessed 2026-07-31)") == len(model.pmids)
        for pmid, expected in _EXPECTED_EVIDENCE.items():
            assert f"PMID:{pmid}" in docs
            assert f"DOI:{expected['doi']}" in docs

    def test_evidence_snapshot_is_sanitized_and_matches_manifest(self) -> None:
        extract = json.loads(
            (_EVIDENCE_DIR / "pubmed-comments-corrections-extract.json").read_text(
                encoding="utf-8"
            )
        )
        metadata = _REPO_ROOT / extract["metadata_snapshot"]["path"]
        metadata_bytes = metadata.read_bytes()
        metadata_payload = json.loads(metadata_bytes)
        metadata_text = metadata_bytes.decode("utf-8")
        metadata_records = {record["pmid"]: record for record in metadata_payload["records"]}
        claim_snapshot = _REPO_ROOT / extract["claim_evidence_snapshot"]["path"]
        claim_bytes = claim_snapshot.read_bytes()
        claim_text = claim_bytes.decode("utf-8")
        claim_payload = json.loads(claim_bytes)
        claim_records = {record["pmid"]: record for record in claim_payload["records"]}
        snapshot = _REPO_ROOT / extract["source_snapshot"]["path"]
        snapshot_bytes = snapshot.read_bytes()
        snapshot_text = snapshot_bytes.decode("utf-8")
        root = ET.fromstring(snapshot_text)
        extracted_records = {record["pmid"]: record for record in extract["records"]}
        source_articles = {
            article.findtext("MedlineCitation/PMID"): article
            for article in root.findall("./PubmedArticle")
        }

        assert (
            hashlib.sha256(metadata_bytes).hexdigest() == (extract["metadata_snapshot"]["sha256"])
        )
        assert hashlib.sha256(metadata_bytes).hexdigest() == _EXPECTED_METADATA_SHA256
        assert (
            hashlib.sha256(claim_bytes).hexdigest() == extract["claim_evidence_snapshot"]["sha256"]
        )
        assert hashlib.sha256(claim_bytes).hexdigest() == _EXPECTED_CLAIM_SHA256
        assert (
            hashlib.sha256(snapshot_bytes).hexdigest()
            == (extract["source_snapshot"]["sanitized_sha256"])
        )
        assert hashlib.sha256(snapshot_bytes).hexdigest() == _EXPECTED_SOURCE_SHA256
        assert extract["metadata_snapshot"]["removed_metadata"] == [
            "authors",
            "history",
            "articleids",
            "all unused ESummary fields",
        ]
        assert extract["source_snapshot"]["removed_metadata"] == {"author_email_addresses": 1}
        assert (
            claim_payload["raw_response_sha256"]
            == extract["claim_evidence_snapshot"]["raw_response_sha256"]
            == extract["source_snapshot"]["raw_sha256"]
        )
        assert claim_payload["raw_response_retained"] is False
        assert claim_payload["retained_sanitized_snapshot"] == {
            "path": extract["source_snapshot"]["path"],
            "sha256": extract["source_snapshot"]["sanitized_sha256"],
        }
        assert (
            claim_payload["raw_response_sha256"]
            != claim_payload["retained_sanitized_snapshot"]["sha256"]
        )
        assert (
            re.search(
                r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}",
                snapshot_text,
            )
            is None
        )
        assert (
            re.search(
                r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}",
                claim_text,
            )
            is None
        )
        assert root.findall(".//Abstract") == []
        assert root.findall(".//OtherAbstract") == []
        assert root.findall(".//ReferenceList") == []
        assert root.findall(".//CoiStatement") == []
        assert root.findall(".//CommentsCorrectionsList") == []
        assert set(extracted_records) == set(_EXPECTED_EVIDENCE)
        assert set(source_articles) == set(_EXPECTED_EVIDENCE)
        assert set(claim_records) == set(_EXPECTED_EVIDENCE)
        assert metadata_payload.keys() == {
            "source",
            "accessed",
            "response_format",
            "records",
        }
        assert metadata_payload["source"] == "NCBI PubMed ESummary"
        assert metadata_payload["accessed"] == "2026-07-31"
        assert metadata_payload["response_format"] == "json"
        assert set(metadata_records) == set(_EXPECTED_EVIDENCE)
        assert claim_payload.keys() == {
            "source",
            "accessed",
            "response_format",
            "request",
            "raw_response_sha256",
            "raw_response_retained",
            "retained_sanitized_snapshot",
            "extraction_method",
            "records",
        }
        assert claim_payload["source"] == "NCBI PubMed EFetch"
        assert claim_payload["accessed"] == "2026-07-31"
        assert claim_payload["response_format"] == "xml"
        assert not any(
            forbidden in record
            for record in metadata_payload["records"]
            for forbidden in ("authors", "history", "articleids")
        )
        assert (
            re.search(
                r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}",
                metadata_text,
            )
            is None
        )

        for pmid, expected in _EXPECTED_EVIDENCE.items():
            extracted = extracted_records[pmid]
            summary = metadata_records[pmid]
            claim = claim_records[pmid]
            source_dois = {
                article_id.text
                for article_id in source_articles[pmid].findall(
                    "PubmedData/ArticleIdList/ArticleId"
                )
                if article_id.attrib.get("IdType") == "doi"
            }

            assert extracted["doi"] == expected["doi"]
            assert extracted["age_80_estimate"] == expected["age_80_estimate"]
            assert extracted["comments_corrections_list_present"] is False
            assert extracted["comments_corrections"] == []
            assert summary.keys() == {"pmid", "doi", "title", "journal", "pubdate"}
            assert summary["pmid"] == pmid
            assert summary["doi"] == expected["doi"]
            assert source_dois == {expected["doi"]}
            assert claim.keys() == {"pmid", "doi", "source_xpath", "source_text"}
            assert claim["pmid"] == pmid
            assert claim["doi"] == expected["doi"]
            assert claim["source_xpath"].startswith(
                "PubmedArticle/MedlineCitation/Article/Abstract/AbstractText"
            )
            assert all(token in claim["source_text"] for token in expected["source_tokens"])


class TestStorage:
    def test_clinvar_null_and_evidence_two(self, panel, sample_engine: sa.Engine) -> None:
        _seed(sample_engine, [_lrrk2("GA")])
        a = assess_parkinsons(panel, sample_engine)
        assert store_parkinsons_findings(a, sample_engine) == 1
        with sample_engine.connect() as conn:
            row = conn.execute(
                sa.select(findings).where(findings.c.module == "parkinsons")
            ).fetchone()
        assert row.clinvar_significance is None
        assert row.gene_symbol == "LRRK2"
        # evidence_level 2 keeps it out of the ungated high-confidence top-5 (>=3).
        assert row.evidence_level == 2

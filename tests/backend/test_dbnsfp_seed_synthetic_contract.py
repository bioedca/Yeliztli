"""Offline synthetic-data contract for the compact dbNSFP behavior fixture (#2035).

dbNSFP predictor values are provider-fetched, non-redistributed runtime data.
The shared mini fixture therefore exercises parsing, lookup, null handling, and
ensemble boundaries with explicit synthetic identities instead of attaching
invented scores to real variants.
"""

from __future__ import annotations

import csv
import hashlib
import json
import re
import sqlite3
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

import sqlalchemy as sa
from sqlalchemy.pool import StaticPool

from backend.annotation.dbnsfp import (
    is_ensemble_pathogenic,
    load_dbnsfp_from_csv,
    lookup_dbnsfp_by_rsids,
)

_ROOT = Path(__file__).resolve().parents[2]
_SEED = _ROOT / "tests" / "fixtures" / "seed_csvs" / "dbnsfp_seed.csv"
_CONTRACT = _ROOT / "tests" / "fixtures" / "seed_csvs" / "dbnsfp_seed.contract.json"
_MINI_DBNSFP = _ROOT / "tests" / "fixtures" / "mini_dbnsfp.db"
_EVIDENCE = _ROOT / "data" / "science-evidence" / "2026-08-12-dbnsfp-seed-2035"
_RAW_EVIDENCE = _EVIDENCE / "raw"

_DB_COLUMNS = (
    "rsid",
    "chrom",
    "pos",
    "ref",
    "alt",
    "cadd_phred",
    "sift_score",
    "sift_pred",
    "polyphen2_hsvar_score",
    "polyphen2_hsvar_pred",
    "revel",
    "mutpred2",
    "vest4",
    "metasvm",
    "metalr",
    "gerp_rs",
    "phylop",
    "mpc",
    "primateai",
)


def _contract() -> dict:
    return json.loads(_CONTRACT.read_text(encoding="utf-8"))


def _rows() -> list[dict[str, str]]:
    with _SEED.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        assert reader.fieldnames == list(_DB_COLUMNS), (
            "dbnsfp_seed.csv columns changed outside the reviewed synthetic contract"
        )
        return list(reader)


def test_contract_forbids_scientific_variant_or_upstream_score_claims() -> None:
    contract = _contract()

    assert contract["contract_version"] == 1
    assert contract["fixture_model"] == "synthetic_predictor_behavior_scenarios"
    assert contract["consumer"] == "backend.annotation.dbnsfp.load_dbnsfp_from_csv"
    assert contract["rsid_namespace"] == "rsYELIZTLI"
    assert contract["coordinate_model"] == "synthetic_grch38_lookup_keys"
    assert contract["scientific_variant_claims"] is False
    assert contract["upstream_score_values"] is False


def test_seed_contains_exactly_the_reviewed_synthetic_scenarios() -> None:
    contract = _contract()
    rows = _rows()
    rsids = [row["rsid"] for row in rows]

    assert len(rows) == contract["row_count"]
    assert len(rsids) == len(set(rsids))
    assert set(rsids) == set(contract["scenarios"])
    assert all(re.fullmatch(r"rsYELIZTLI\d{4}", rsid) for rsid in rsids)
    assert not any(re.fullmatch(r"rs\d+", rsid) for rsid in rsids)


def test_sift_and_polyphen_categories_are_consistent_with_scores() -> None:
    violations: list[str] = []

    for row in _rows():
        sift_score = row["sift_score"]
        sift_pred = row["sift_pred"]
        if bool(sift_score) != bool(sift_pred):
            violations.append(f"{row['rsid']}: incomplete SIFT score/prediction pair")
        elif sift_score:
            expected_sift = "D" if float(sift_score) < 0.05 else "T"
            if sift_pred != expected_sift:
                violations.append(
                    f"{row['rsid']}: SIFT {sift_score}/{sift_pred}, expected {expected_sift}"
                )

        polyphen_score = row["polyphen2_hsvar_score"]
        polyphen_pred = row["polyphen2_hsvar_pred"]
        if bool(polyphen_score) != bool(polyphen_pred):
            violations.append(f"{row['rsid']}: incomplete PolyPhen score/prediction pair")
        elif polyphen_score:
            score = float(polyphen_score)
            expected_polyphen = "B" if score <= 0.446 else "P" if score <= 0.908 else "D"
            if polyphen_pred != expected_polyphen:
                violations.append(
                    f"{row['rsid']}: PolyPhen {polyphen_score}/{polyphen_pred}, "
                    f"expected {expected_polyphen}"
                )

    assert not violations, "incoherent synthetic predictor categories:\n" + "\n".join(violations)


def test_scenarios_have_the_exact_reviewed_production_outcomes() -> None:
    contract = _contract()
    engine = sa.create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    try:
        load_dbnsfp_from_csv(_SEED, engine)
        annotations = lookup_dbnsfp_by_rsids(list(contract["scenarios"]), engine)
    finally:
        engine.dispose()

    assert set(annotations) == set(contract["scenarios"])
    for rsid, expected in contract["scenarios"].items():
        annotation = annotations[rsid]
        assert annotation.deleterious_count == expected["deleterious_count"], rsid
        assert annotation.deleterious_total_assessed == expected["total_assessed"], rsid
        assert is_ensemble_pathogenic(annotation) is expected["ensemble_pathogenic"], rsid


def test_checked_in_database_exactly_matches_the_synthetic_seed() -> None:
    expected = [
        tuple(None if row[column] == "" else row[column] for column in _DB_COLUMNS)
        for row in _rows()
    ]
    query = f"SELECT {', '.join(_DB_COLUMNS)} FROM dbnsfp_scores ORDER BY rowid"

    uri = f"file:{_MINI_DBNSFP.resolve()}?mode=ro&immutable=1"
    with sqlite3.connect(uri, uri=True) as connection:
        actual = connection.execute(query).fetchall()

    normalized = [
        tuple(str(value) if value is not None else None for value in row) for row in actual
    ]
    assert normalized == expected, (
        "mini_dbnsfp.db is stale relative to the synthetic seed; "
        "run python scripts/regenerate_fixtures.py"
    )


def test_scientific_evidence_manifest_binds_retained_source_payloads() -> None:
    manifest = json.loads((_EVIDENCE / "source-manifest.json").read_text(encoding="utf-8"))
    artifacts = {artifact["path"]: artifact for artifact in manifest["retained_artifacts"]}
    readme = (_EVIDENCE / "README.md").read_text(encoding="utf-8")
    defined_claims = set(re.findall(r"^\| (C\d+) \|", readme, flags=re.MULTILINE))
    expected_source_ids = {
        "dbnsfp-releases",
        "dbnsfp-license",
        "dbnsfp-query-service",
        "ensembl-vep-rs429358",
        "dbnsfp-v4-paper",
        "revel-paper",
    }
    expected_raw_paths = {
        str(path.relative_to(_ROOT)) for path in _RAW_EVIDENCE.iterdir() if path.is_file()
    }
    referenced_raw_paths: set[str] = set()

    assert manifest["packet_version"] == 3
    assert manifest["production_pin"]["manifest_path"] == "bundles/manifest.json"
    assert defined_claims == {"C1", "C2", "C3", "C4", "C5"}
    assert {source["id"] for source in manifest["sources"]} == expected_source_ids

    for source in manifest["sources"]:
        assert source["accessed"] == "2026-08-12"
        assert source["claim_ids"], f"{source['id']} has no claim mapping"
        assert set(source["claim_ids"]) <= defined_claims, (
            f"{source['id']} references an undefined claim"
        )
        assert source["version_observed"]
        assert source["license_or_terms"]
        assert source["role"]

        derived_path = source["derived_path"]
        assert derived_path in artifacts, f"{source['id']} derived summary is not in the manifest"
        assert artifacts[derived_path]["kind"] == "derived_summary"

        assert source["raw_payload_refs"], f"{source['id']} has no raw payload"
        for raw_ref in source["raw_payload_refs"]:
            relative_path = raw_ref["path"]
            referenced_raw_paths.add(relative_path)
            assert relative_path.startswith(
                "data/science-evidence/2026-08-12-dbnsfp-seed-2035/raw/"
            )
            assert relative_path in artifacts
            artifact = artifacts[relative_path]
            assert artifact["kind"] == "sanitized_raw_response"
            assert re.fullmatch(r"[0-9a-f]{64}", artifact["source_response_sha256"])
            assert artifact["request"]["method"] == "GET"
            assert artifact["request"]["url"].startswith("https://")
            assert artifact["accessed"] == source["accessed"]
            assert artifact["media_type"]
            assert artifact["sanitization"]["otherwise_preserved"] is True
            assert len(_resolve_source_ref(raw_ref)) == 1, (
                f"{source['id']} raw selector must resolve exactly one record"
            )

    assert referenced_raw_paths == expected_raw_paths
    assert len(expected_raw_paths) == 6

    for relative_path, artifact in artifacts.items():
        path = _ROOT / relative_path
        assert path.is_file(), f"retained evidence payload is missing: {relative_path}"
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        assert digest == artifact["sha256"], f"retained payload hash drift: {relative_path}"


def _resolve_source_ref(raw_ref: dict[str, str]) -> list[object]:
    """Resolve the deliberately small selector vocabulary used by this packet."""

    path = _ROOT / raw_ref["path"]
    selector_kind = raw_ref["selector_kind"]
    selector = raw_ref["selector"]

    if selector_kind == "whole_payload":
        assert selector == "$"
        return [path.read_bytes()]

    if selector_kind == "json_pointer":
        value: object = json.loads(path.read_text(encoding="utf-8"))
        for token in selector.removeprefix("/").split("/"):
            token = token.replace("~1", "/").replace("~0", "~")
            if isinstance(value, list):
                value = value[int(token)]
            else:
                assert isinstance(value, dict)
                value = value[token]
        return [value]

    if selector_kind == "xpath":
        match = re.fullmatch(
            r"/PubmedArticleSet/PubmedArticle\[MedlineCitation/PMID='(\d+)'\]",
            selector,
        )
        assert match, f"unsupported XPath selector: {selector}"
        pmid = match.group(1)
        root = ET.parse(path).getroot()
        return [
            article
            for article in root.findall("./PubmedArticle")
            if article.findtext("./MedlineCitation/PMID") == pmid
        ]

    raise AssertionError(f"unsupported selector kind: {selector_kind}")


def test_retained_source_responses_support_every_derived_claim() -> None:
    manifest = json.loads((_EVIDENCE / "source-manifest.json").read_text(encoding="utf-8"))
    raw_artifacts = {
        Path(artifact["path"]).name: artifact
        for artifact in manifest["retained_artifacts"]
        if artifact["kind"] == "sanitized_raw_response"
    }
    health_raw = json.loads(
        (_RAW_EVIDENCE / "dbnsfp-query-health.json").read_text(encoding="utf-8")
    )
    health_summary = json.loads(
        (_EVIDENCE / "dbnsfp-query-health-sanitized.json").read_text(encoding="utf-8")
    )
    assert (
        health_raw
        == health_summary["response"]
        == {
            "status": "ok",
            "dataset_version": "5.4a",
        }
    )
    assert (
        health_summary["source_payload_sha256"]
        == raw_artifacts["dbnsfp-query-health.json"]["source_response_sha256"]
    )

    releases_html = (_RAW_EVIDENCE / "dbnsfp-releases.html").read_text(encoding="utf-8")
    for expected in (
        "dbNSFP v5.4 (August 1, 2026)",
        "README v5.4a",
        "README v5.4c",
        "dbNSFP v5.3.1 (January 1, 2026)",
        "README v5.3.1a",
        "README v5.3.1c",
    ):
        assert expected in releases_html
    assert "<script" not in releases_html.casefold()
    releases_summary = json.loads(
        (_EVIDENCE / "dbnsfp-releases-sanitized.json").read_text(encoding="utf-8")
    )
    assert releases_summary["response"] == {
        "current_release": {
            "release": "5.4",
            "date": "August 1, 2026",
            "readme_branches": ["5.4a", "5.4c"],
        },
        "production_release_family": {
            "release": "5.3.1",
            "date": "January 1, 2026",
            "readme_branches": ["5.3.1a", "5.3.1c"],
        },
    }
    assert (
        releases_summary["source_payload_sha256"]
        == raw_artifacts["dbnsfp-releases.html"]["source_response_sha256"]
    )

    license_html = (_RAW_EVIDENCE / "dbnsfp-license.html").read_text(encoding="utf-8")
    assert "academic and non-commercial use" in license_html
    assert "CC BY-NC-ND 4.0" in license_html
    assert "individual licensing requirements of its components" in license_html
    assert "<script" not in license_html.casefold()
    assert "mailto:" not in license_html.casefold()
    assert "docs.google.com/forms" not in license_html.casefold()
    license_summary = json.loads(
        (_EVIDENCE / "dbnsfp-license-sanitized.json").read_text(encoding="utf-8")
    )
    assert license_summary["response"] == {
        "branch_model": ["academic", "commercial"],
        "academic_branch_use": ["academic", "non-commercial"],
        "academic_branch_license": "CC BY-NC-ND 4.0",
        "component_license_compliance_required": True,
    }
    assert (
        license_summary["source_payload_sha256"]
        == raw_artifacts["dbnsfp-license.html"]["source_response_sha256"]
    )

    ensembl_raw = json.loads(
        (_RAW_EVIDENCE / "ensembl-vep-rs429358.json").read_text(encoding="utf-8")
    )
    ensembl_summary = json.loads(
        (_EVIDENCE / "ensembl-rs429358-sanitized.json").read_text(encoding="utf-8")
    )
    assert len(ensembl_raw) == 1
    root_record = ensembl_raw[0]
    assert {
        key: root_record[key]
        for key in (
            "id",
            "assembly_name",
            "seq_region_name",
            "start",
            "end",
            "allele_string",
            "strand",
            "most_severe_consequence",
        )
    } == ensembl_summary["variant"]
    assert len(root_record["transcript_consequences"]) == 55
    apoe_missense = [
        consequence
        for consequence in root_record["transcript_consequences"]
        if consequence.get("gene_symbol") == "APOE"
        and "missense_variant" in consequence["consequence_terms"]
        and consequence.get("sift_score") is not None
    ]
    assert len(apoe_missense) == 30
    assert Counter(
        (consequence["sift_score"], consequence["sift_prediction"])
        for consequence in apoe_missense
    ) == Counter({(1.0, "tolerated"): 29, (0.46, "tolerated_low_confidence"): 1})
    low_confidence = [
        consequence
        for consequence in apoe_missense
        if consequence["sift_prediction"] == "tolerated_low_confidence"
    ]
    assert [record["transcript_id"] for record in low_confidence] == ["ENST00001121353"]
    polyphen = [
        consequence
        for consequence in apoe_missense
        if consequence.get("polyphen_score") is not None
    ]
    assert [
        (
            record["transcript_id"],
            record["polyphen_score"],
            record["polyphen_prediction"],
        )
        for record in polyphen
    ] == [("ENST00000434152", 0.0, "benign")]
    assert ensembl_summary["apoe_missense_transcript_summary"] == {
        "transcript_consequence_count": 30,
        "variant_allele": "C",
        "sift": [
            {"score": 1.0, "prediction": "tolerated", "transcript_count": 29},
            {
                "score": 0.46,
                "prediction": "tolerated_low_confidence",
                "transcript_count": 1,
                "transcript_id": "ENST00001121353",
            },
        ],
        "polyphen_non_null": [
            {
                "score": 0.0,
                "prediction": "benign",
                "transcript_id": "ENST00000434152",
            }
        ],
    }

    pubmed_summary = json.loads(
        (_EVIDENCE / "pubmed-metadata-sanitized.json").read_text(encoding="utf-8")
    )
    summaries = {record["pmid"]: record for record in pubmed_summary["records"]}
    esummary = json.loads(
        (_RAW_EVIDENCE / "pubmed-esummary-33261662-27666373.json").read_text(encoding="utf-8")
    )
    assert esummary["header"] == {"type": "esummary", "version": "0.3"}
    assert esummary["result"]["uids"] == ["33261662", "27666373"]
    denied_esummary_keys = {
        "authors",
        "lastauthor",
        "sortfirstauthor",
        "srccontriblist",
        "doccontriblist",
    }
    assert not denied_esummary_keys & set(_all_json_keys(esummary))
    assert "@" not in json.dumps(esummary)

    efetch_path = _RAW_EVIDENCE / "pubmed-efetch-33261662-27666373.xml"
    efetch_text = efetch_path.read_text(encoding="utf-8")
    efetch_root = ET.parse(efetch_path).getroot()
    articles = {
        article.findtext("./MedlineCitation/PMID"): article
        for article in efetch_root.findall("./PubmedArticle")
    }
    assert set(articles) == set(summaries) == {"33261662", "27666373"}
    for denied_tag in (
        "Abstract",
        "AuthorList",
        "AffiliationInfo",
        "GrantList",
        "ReferenceList",
        "CoiStatement",
        "MeshHeadingList",
        "KeywordList",
    ):
        assert not efetch_root.findall(f".//{denied_tag}")
    assert "@" not in efetch_text

    for pmid, expected in summaries.items():
        summary_record = esummary["result"][pmid]
        summary_dois = {
            article_id["value"]
            for article_id in summary_record["articleids"]
            if article_id["idtype"] == "doi"
        }
        article = articles[pmid]
        efetch_dois = {
            element.text for element in article.findall(".//ELocationID[@EIdType='doi']")
        }
        assert summary_record["uid"] == pmid
        assert summary_dois == efetch_dois == {expected["doi"]}
        assert summary_record["title"].rstrip(".") == expected["title"]
        assert article.findtext(".//ArticleTitle", "").rstrip(".") == expected["title"]
        assert summary_record["fulljournalname"].casefold() == expected["journal"].casefold()
        assert (
            article.findtext(".//Journal/Title", "").casefold() == expected["journal"].casefold()
        )
        assert int(summary_record["pubdate"][:4]) == expected["publication_year"]
        assert (
            int(article.findtext(".//JournalIssue/PubDate/Year", "0"))
            == expected["publication_year"]
        )
        assert article.find("./MedlineCitation/CommentsCorrectionsList") is None
        assert expected["comments_corrections_list_present_in_retrieved_record"] is False


def _all_json_keys(value: object) -> list[str]:
    if isinstance(value, dict):
        return list(value) + [key for item in value.values() for key in _all_json_keys(item)]
    if isinstance(value, list):
        return [key for item in value for key in _all_json_keys(item)]
    return []

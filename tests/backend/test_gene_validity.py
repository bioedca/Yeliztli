"""Unit tests for the ClinGen gene-validity guardrail logic (SW-A11 / #14)."""

from __future__ import annotations

import sqlalchemy as sa

from backend.analysis.gene_validity import (
    CLINGEN_FRAMEWORK_PMID,
    assess_finding_gene_validity,
    best_curation,
    classification_rank,
    gene_validity_guardrail,
    is_established,
)
from backend.db.tables import clingen_gene_validity, findings


def _cur(classification: str, disease: str = "some disease") -> dict:
    return {
        "gene_symbol": "G",
        "classification": classification,
        "disease_label": disease,
        "disease_id": "MONDO:0000000",
        "moi": "AD",
        "sop": "SOP10",
        "report_url": "https://example/r",
        "classification_date": "2024-01-01T00:00:00.000Z",
        "gcep": "GCEP",
    }


def test_classification_rank_ordering() -> None:
    order = [
        "Definitive",
        "Strong",
        "Moderate",
        "Limited",
        "No Known Disease Relationship",
        "Disputed",
        "Refuted",
    ]
    ranks = [classification_rank(c) for c in order]
    assert ranks == sorted(ranks, reverse=True)
    assert classification_rank(None) == -1
    assert classification_rank("Garbage") == -1


def test_is_established() -> None:
    assert is_established("Definitive")
    assert is_established("Strong")
    assert is_established("Moderate")
    assert not is_established("Limited")
    assert not is_established("Disputed")
    assert not is_established("Refuted")
    assert not is_established("No Known Disease Relationship")
    assert not is_established(None)


def test_best_curation_picks_strongest() -> None:
    curs = [_cur("Limited", "disease A"), _cur("Moderate", "disease B")]
    assert best_curation(curs)["classification"] == "Moderate"
    assert best_curation([]) is None


def test_guardrail_none_when_uncurated() -> None:
    assert gene_validity_guardrail("G", []) is None
    assert gene_validity_guardrail(None, [_cur("Definitive")]) is None


def test_guardrail_established_is_not_caution() -> None:
    g = gene_validity_guardrail("BRCA1", [_cur("Definitive", "HBOC")])
    assert g["has_clingen_curation"] is True
    assert g["best_classification"] == "Definitive"
    assert g["validity_established"] is True
    assert g["caution"] is False
    assert g["context_only"] is True
    assert CLINGEN_FRAMEWORK_PMID in g["pmid_citations"]
    assert "Definitive" in g["label"]


def test_guardrail_limited_triggers_caution() -> None:
    g = gene_validity_guardrail("TTN", [_cur("Limited", "DCM")])
    assert g["validity_established"] is False
    assert g["caution"] is True
    assert "Limited" in g["label"]
    assert "caution" in g["detail"].lower()


def test_guardrail_contradicted_triggers_caution() -> None:
    for tier in ("Disputed", "Refuted"):
        g = gene_validity_guardrail("FOO", [_cur(tier)])
        assert g["caution"] is True
        assert tier in g["label"]


def test_guardrail_no_known_triggers_caution() -> None:
    g = gene_validity_guardrail("BAR", [_cur("No Known Disease Relationship")])
    assert g["caution"] is True
    assert g["validity_established"] is False


def test_guardrail_best_across_diseases_wins() -> None:
    # A pleiotropic gene: Limited for one disease, Moderate for another → not caution.
    curs = [_cur("Limited", "disease A"), _cur("Moderate", "disease B")]
    g = gene_validity_guardrail("ABCB6", curs)
    assert g["best_classification"] == "Moderate"
    assert g["caution"] is False
    assert len(g["curations"]) == 2


def test_assess_finding_gene_validity_includes_compound_pathogenic_primary(
    sample_engine: sa.Engine, reference_engine: sa.Engine
) -> None:
    with reference_engine.begin() as conn:
        conn.execute(
            clingen_gene_validity.insert().values(
                gene_symbol="CFTR",
                hgnc_id="HGNC:1884",
                disease_label="cystic fibrosis",
                disease_id="MONDO:0009061",
                moi="AR",
                sop="SOP10",
                classification="Definitive",
                report_url="https://example/cftr",
                classification_date="2024-01-01T00:00:00.000Z",
                gcep="Test GCEP",
            )
        )

    with sample_engine.begin() as conn:
        conn.execute(
            findings.insert(),
            [
                {
                    "module": "rare_variants",
                    "category": "clinvar_pathogenic",
                    "evidence_level": 4,
                    "gene_symbol": "CFTR",
                    "rsid": "rs_compound_plp",
                    "finding_text": "CFTR rs_compound_plp — Pathogenic|drug response",
                    "clinvar_significance": "Pathogenic|drug response",
                },
                {
                    "module": "rare_variants",
                    "category": "rare",
                    "evidence_level": 1,
                    "gene_symbol": "GENE2",
                    "rsid": "rs_conflicting",
                    "finding_text": "GENE2 rs_conflicting — conflicting",
                    "clinvar_significance": "Conflicting classifications of pathogenicity",
                },
            ],
        )

    rows = assess_finding_gene_validity(sample_engine, reference_engine)

    assert {row["rsid"] for row in rows} == {"rs_compound_plp"}
    assert rows[0]["clinvar_significance"] == "Pathogenic|drug response"
    assert rows[0]["gene_symbol"] == "CFTR"
    assert rows[0]["validity_established"] is True

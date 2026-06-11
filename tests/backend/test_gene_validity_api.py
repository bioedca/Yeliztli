"""API tests for the gene-validity endpoint (SW-A11 / #14).

GET /api/analysis/gene-validity?sample_id=N — a ClinGen gene-disease validity
guardrail for every ClinVar P/LP finding.
"""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path
from unittest.mock import patch

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from backend.config import Settings
from backend.db.connection import reset_registry
from backend.db.sample_schema import create_sample_tables
from backend.db.tables import (
    clingen_gene_validity,
    findings,
    reference_metadata,
    samples,
)

# gene → (clinvar_significance, ClinGen classifications seeded, expected caution)
# A gene may have multiple curations; the guardrail keys on the strongest.
_FINDINGS = {
    "rs_brca1": ("BRCA1", "Pathogenic"),
    "rs_ttn": ("TTN", "Likely pathogenic"),
    "rs_foo": ("FOO1", "Pathogenic"),
    "rs_uncurated": ("ZZZGENE", "Pathogenic"),
}

_CURATIONS = [
    ("BRCA1", "hereditary breast ovarian cancer", "Definitive"),
    ("TTN", "dilated cardiomyopathy", "Limited"),
    ("ABCB6", "microphthalmia", "Limited"),  # pleiotropic — see below
    ("ABCB6", "dyschromatosis", "Moderate"),
    ("FOO1", "disputed disease", "Disputed"),
]


@pytest.fixture
def tmp_data_dir(tmp_path: Path) -> Path:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "samples").mkdir()
    return data_dir


@pytest.fixture
def gv_client(tmp_data_dir: Path) -> Generator[TestClient, None, None]:
    settings = Settings(data_dir=tmp_data_dir, wal_mode=False)

    ref_engine = sa.create_engine(f"sqlite:///{settings.reference_db_path}")
    reference_metadata.create_all(ref_engine)

    sample_db_path = tmp_data_dir / "samples" / "sample_1.db"
    sample_engine = sa.create_engine(f"sqlite:///{sample_db_path}")
    create_sample_tables(sample_engine)

    with ref_engine.begin() as conn:
        conn.execute(
            samples.insert().values(
                id=1,
                name="Test Sample",
                db_path="samples/sample_1.db",
                file_format="v5",
                file_hash="abc123",
            )
        )
        for gene, disease, classification in _CURATIONS:
            conn.execute(
                clingen_gene_validity.insert().values(
                    gene_symbol=gene,
                    hgnc_id="HGNC:1",
                    disease_label=disease,
                    disease_id="MONDO:0000000",
                    moi="AD",
                    sop="SOP10",
                    classification=classification,
                    report_url="https://example/r",
                    classification_date="2024-01-01T00:00:00.000Z",
                    gcep="Test GCEP",
                )
            )

    with sample_engine.begin() as conn:
        for rsid, (gene, sig) in _FINDINGS.items():
            conn.execute(
                findings.insert().values(
                    module="cancer",
                    category="monogenic_variant",
                    evidence_level=4,
                    gene_symbol=gene,
                    rsid=rsid,
                    finding_text=f"{gene} {rsid} — {sig}",
                    clinvar_significance=sig,
                )
            )
        # A benign finding that must never receive a guardrail.
        conn.execute(
            findings.insert().values(
                module="cancer",
                category="monogenic_variant",
                evidence_level=1,
                gene_symbol="BRCA1",
                rsid="rs_benign",
                finding_text="BRCA1 benign",
                clinvar_significance="Benign",
            )
        )

    ref_engine.dispose()
    sample_engine.dispose()

    with (
        patch("backend.main.get_settings", return_value=settings),
        patch("backend.db.connection.get_settings", return_value=settings),
    ):
        reset_registry()
        from backend.main import create_app

        app = create_app()
        with TestClient(app) as tc:
            yield tc
        reset_registry()


class TestGeneValidityEndpoint:
    def test_only_pathogenic_findings(self, gv_client: TestClient) -> None:
        data = gv_client.get("/api/analysis/gene-validity?sample_id=1").json()
        assert {d["rsid"] for d in data} == set(_FINDINGS)
        assert "rs_benign" not in {d["rsid"] for d in data}

    def test_definitive_gene_no_caution(self, gv_client: TestClient) -> None:
        by_rsid = {
            d["rsid"]: d for d in gv_client.get("/api/analysis/gene-validity?sample_id=1").json()
        }
        brca1 = by_rsid["rs_brca1"]
        assert brca1["has_clingen_curation"] is True
        assert brca1["best_classification"] == "Definitive"
        assert brca1["validity_established"] is True
        assert brca1["caution"] is False

    def test_limited_gene_triggers_caution(self, gv_client: TestClient) -> None:
        by_rsid = {
            d["rsid"]: d for d in gv_client.get("/api/analysis/gene-validity?sample_id=1").json()
        }
        ttn = by_rsid["rs_ttn"]
        assert ttn["best_classification"] == "Limited"
        assert ttn["caution"] is True

    def test_disputed_gene_triggers_caution(self, gv_client: TestClient) -> None:
        by_rsid = {
            d["rsid"]: d for d in gv_client.get("/api/analysis/gene-validity?sample_id=1").json()
        }
        assert by_rsid["rs_foo"]["best_classification"] == "Disputed"
        assert by_rsid["rs_foo"]["caution"] is True

    def test_uncurated_gene_is_not_caution(self, gv_client: TestClient) -> None:
        by_rsid = {
            d["rsid"]: d for d in gv_client.get("/api/analysis/gene-validity?sample_id=1").json()
        }
        z = by_rsid["rs_uncurated"]
        assert z["has_clingen_curation"] is False
        assert z["caution"] is False
        assert z["best_classification"] is None

    def test_guardrail_carries_context_only_disclosure(self, gv_client: TestClient) -> None:
        from backend.analysis.gene_validity import CLINGEN_FRAMEWORK_PMID

        data = gv_client.get("/api/analysis/gene-validity?sample_id=1").json()
        assert data  # guard against a vacuous loop
        for d in data:
            assert d["context_only"] is True
            assert d["note"]
            assert CLINGEN_FRAMEWORK_PMID in d["pmid_citations"]

    def test_invalid_sample_returns_404(self, gv_client: TestClient) -> None:
        resp = gv_client.get("/api/analysis/gene-validity?sample_id=999")
        assert resp.status_code == 404

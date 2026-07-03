"""Tests for drug lookup API (P3-05) and gene results endpoint.

T3-05: Drug lookup for "clopidogrel" returns CYP2C19 with correct genotype,
recommendation, and call confidence.

Covers:
  - GET /api/analysis/pharma/drugs — List all CPIC drugs
  - GET /api/analysis/pharma/drug/{drug_name} — Drug detail with user genotype
  - GET /api/analysis/pharma/genes?sample_id=N — Per-gene star-allele results
  - Case-insensitive drug name matching
  - Missing drug returns 404
  - Gene with no sample finding (Insufficient / not yet run)
  - Gene with finding includes full effect detail
"""

from __future__ import annotations

import json
from collections.abc import Generator
from pathlib import Path
from unittest.mock import patch

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from backend.config import Settings
from backend.db.connection import DBRegistry, reset_registry
from backend.db.sample_schema import create_sample_tables
from backend.db.tables import (
    cpic_guidelines,
    findings,
    reference_metadata,
    samples,
)
from backend.disclaimers import DPYD_FLUOROPYRIMIDINE_CAVEAT, MEDICATION_SAFETY_REFERENCE_BIAS

# ── Test data ────────────────────────────────────────────────────────

CPIC_GUIDELINES_DATA = [
    {
        "gene": "CYP2C19",
        "drug": "clopidogrel",
        "phenotype": "Normal Metabolizer",
        "recommendation": "Use label-recommended dosing.",
        "classification": "A",
        "guideline_url": "https://cpicpgx.org/guidelines/guideline-for-clopidogrel-and-cyp2c19/",
    },
    {
        "gene": "CYP2C19",
        "drug": "clopidogrel",
        "phenotype": "Intermediate Metabolizer",
        "recommendation": "Consider alternative antiplatelet therapy.",
        "classification": "A",
        "guideline_url": "https://cpicpgx.org/guidelines/guideline-for-clopidogrel-and-cyp2c19/",
    },
    {
        "gene": "CYP2C19",
        "drug": "clopidogrel",
        "phenotype": "Poor Metabolizer",
        "recommendation": "Use alternative antiplatelet therapy.",
        "classification": "A",
        "guideline_url": "https://cpicpgx.org/guidelines/guideline-for-clopidogrel-and-cyp2c19/",
    },
    {
        "gene": "CYP2D6",
        "drug": "codeine",
        "phenotype": "Normal Metabolizer",
        "recommendation": "Use label-recommended age- or weight-specific dosing.",
        "classification": "A",
        "guideline_url": "https://cpicpgx.org/guidelines/guideline-for-codeine-and-cyp2d6/",
    },
    {
        "gene": "CYP2D6",
        "drug": "codeine",
        "phenotype": "Poor Metabolizer",
        "recommendation": "Avoid codeine use. Alternative analgesics recommended.",
        "classification": "A",
        "guideline_url": "https://cpicpgx.org/guidelines/guideline-for-codeine-and-cyp2d6/",
    },
    {
        "gene": "CYP2C9",
        "drug": "warfarin",
        "phenotype": "Normal Metabolizer",
        "recommendation": "Use label-recommended dosing algorithm.",
        "classification": "A",
        "guideline_url": "https://cpicpgx.org/guidelines/guideline-for-warfarin-and-cyp2c9-and-vkorc1/",
    },
]

# Findings stored by the pharmacogenomics module (P3-04)
SAMPLE_FINDINGS = [
    {
        "module": "pharmacogenomics",
        "category": "prescribing_alert",
        "evidence_level": 4,
        "gene_symbol": "CYP2C19",
        "diplotype": "*1/*2",
        "metabolizer_status": "Intermediate Metabolizer",
        "drug": "clopidogrel",
        "finding_text": (
            "CYP2C19 *1/*2: Intermediate Metabolizer"
            " -- clopidogrel: Consider alternative antiplatelet therapy."
        ),
        "detail_json": json.dumps(
            {
                "recommendation": "Consider alternative antiplatelet therapy.",
                "classification": "A",
                "guideline_url": "https://cpicpgx.org/guidelines/guideline-for-clopidogrel-and-cyp2c19/",
                "call_confidence": "Complete",
                "confidence_note": "All defining rsids present and genotyped.",
                "activity_score": 0.5,
                "ehr_notation": "Intermediate Metabolizer",
                "involved_rsids": ["rs4244285"],
            }
        ),
    },
    {
        "module": "pharmacogenomics",
        "category": "prescribing_alert",
        "evidence_level": 4,
        "gene_symbol": "CYP2D6",
        "diplotype": "*1/*4",
        "metabolizer_status": "Intermediate Metabolizer",
        "drug": "codeine",
        "finding_text": (
            "CYP2D6 *1/*4: Intermediate Metabolizer -- codeine: Use label-recommended dosing."
        ),
        "detail_json": json.dumps(
            {
                "recommendation": "Use label-recommended dosing.",
                "classification": "A",
                "guideline_url": "https://cpicpgx.org/guidelines/guideline-for-codeine-and-cyp2d6/",
                "call_confidence": "Partial",
                "confidence_note": (
                    "SNP-based alleles called; structural variants cannot be excluded."
                ),
                "activity_score": 1.0,
                "ehr_notation": "Intermediate Metabolizer",
                "involved_rsids": ["rs3892097"],
            }
        ),
    },
]


_UGT1A1_IRINOTECAN_INTERMEDIATE_RECOMMENDATION = (
    "No dose reduction recommended; consider reduction for high-dose therapy."
)


_UGT1A1_GUIDELINES = [
    {
        "gene": "UGT1A1",
        "drug": "atazanavir",
        "phenotype": "Intermediate Metabolizer",
        "recommendation": "Low likelihood of bilirubin-related discontinuation.",
        "classification": "A",
        "guideline_url": "https://cpicpgx.org/guidelines/guideline-for-atazanavir-and-ugt1a1/",
    },
    {
        "gene": "UGT1A1",
        "drug": "irinotecan",
        "phenotype": "Intermediate Metabolizer",
        "recommendation": _UGT1A1_IRINOTECAN_INTERMEDIATE_RECOMMENDATION,
        "classification": "A",
        "guideline_url": "https://www.pharmgkb.org/guidelineAnnotation/PA166104951",
    },
]


def _ugt1a1_conservative_detail(recommendation: str, guideline_url: str) -> str:
    return json.dumps(
        {
            "recommendation": recommendation,
            "classification": "A",
            "guideline_url": guideline_url,
            "call_confidence": "Partial",
            "confidence_note": (
                "Cannot exclude UGT1A1*28. Conservative prescribing alert uses "
                "Intermediate Metabolizer."
            ),
            "activity_score": 1.0,
            "ehr_notation": "UGT1A1 Intermediate Metabolizer",
            "involved_rsids": ["rs4148323"],
            "coverage": {"assessed": 1, "total": 2},
            "indeterminate_alleles": ["*28"],
            "conservative_alert": True,
            "called_phenotype": "Normal Metabolizer",
            "called_activity_score": 2.0,
            "called_ehr_notation": "UGT1A1 Normal Metabolizer",
            "conservative_diplotype": "*1/*28",
            "conservative_phenotype": "Intermediate Metabolizer",
            "conservative_activity_score": 1.0,
            "conservative_allele": "*28",
        }
    )


_UGT1A1_CONSERVATIVE_FINDINGS = [
    {
        "module": "pharmacogenomics",
        "category": "prescribing_alert",
        "evidence_level": 4,
        "gene_symbol": "UGT1A1",
        "diplotype": "*1/*1",
        "metabolizer_status": "Intermediate Metabolizer",
        "drug": "atazanavir",
        "finding_text": (
            "UGT1A1 *1/*1 (possible *1/*28): Intermediate Metabolizer -- "
            "atazanavir: Low likelihood of bilirubin-related discontinuation."
        ),
        "detail_json": _ugt1a1_conservative_detail(
            "Low likelihood of bilirubin-related discontinuation.",
            "https://cpicpgx.org/guidelines/guideline-for-atazanavir-and-ugt1a1/",
        ),
    },
    {
        "module": "pharmacogenomics",
        "category": "prescribing_alert",
        "evidence_level": 4,
        "gene_symbol": "UGT1A1",
        "diplotype": "*1/*1",
        "metabolizer_status": "Intermediate Metabolizer",
        "drug": "irinotecan",
        "finding_text": (
            "UGT1A1 *1/*1 (possible *1/*28): Intermediate Metabolizer -- "
            "irinotecan: No dose reduction recommended; consider reduction for "
            "high-dose therapy."
        ),
        "detail_json": _ugt1a1_conservative_detail(
            _UGT1A1_IRINOTECAN_INTERMEDIATE_RECOMMENDATION,
            "https://www.pharmgkb.org/guidelineAnnotation/PA166104951",
        ),
    },
]


# ── Fixtures ─────────────────────────────────────────────────────────


@pytest.fixture
def tmp_data_dir(tmp_path: Path) -> Path:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "samples").mkdir()
    return data_dir


def _setup_client(
    tmp_data_dir: Path,
    guidelines: list[dict],
    sample_findings: list[dict] | None = None,
) -> Generator[tuple[TestClient, int], None, None]:
    """Create TestClient with CPIC guidelines and optional sample findings."""
    settings = Settings(data_dir=tmp_data_dir, wal_mode=False)

    ref_engine = sa.create_engine(f"sqlite:///{settings.reference_db_path}")
    reference_metadata.create_all(ref_engine)
    with ref_engine.begin() as conn:
        result = conn.execute(
            samples.insert().values(
                name="test_pharma",
                db_path="samples/sample_1.db",
                file_format="23andme_v5",
                file_hash="hash_pharma",
            )
        )
        sample_id = result.lastrowid

        if guidelines:
            conn.execute(cpic_guidelines.insert(), guidelines)
    ref_engine.dispose()

    sample_db_path = tmp_data_dir / "samples" / "sample_1.db"
    sample_engine = sa.create_engine(f"sqlite:///{sample_db_path}")
    create_sample_tables(sample_engine)
    if sample_findings:
        with sample_engine.begin() as conn:
            conn.execute(findings.insert(), sample_findings)
    sample_engine.dispose()

    with (
        patch("backend.main.get_settings", return_value=settings),
        patch("backend.db.connection.get_settings", return_value=settings),
        patch("backend.api.routes.pharma.get_registry") as mock_reg,
        patch("backend.api.routes.variant_detail.get_registry") as mock_reg2,
        patch("backend.api.routes.annotations_api.get_registry") as mock_reg3,
        patch("backend.api.routes.variants.get_registry") as mock_reg4,
        patch("backend.api.routes.ingest.get_registry") as mock_reg5,
        patch("backend.api.routes.samples.get_registry") as mock_reg6,
    ):
        reset_registry()
        registry = DBRegistry(settings)
        mock_reg.return_value = registry
        mock_reg2.return_value = registry
        mock_reg3.return_value = registry
        mock_reg4.return_value = registry
        mock_reg5.return_value = registry
        mock_reg6.return_value = registry

        from backend.main import create_app

        app = create_app()
        with TestClient(app) as tc:
            yield tc, sample_id

        registry.dispose_all()
        reset_registry()


@pytest.fixture
def client(tmp_data_dir: Path) -> Generator[tuple[TestClient, int], None, None]:
    """Client with CPIC guidelines and sample findings."""
    yield from _setup_client(tmp_data_dir, CPIC_GUIDELINES_DATA, SAMPLE_FINDINGS)


@pytest.fixture
def client_no_findings(tmp_data_dir: Path) -> Generator[tuple[TestClient, int], None, None]:
    """Client with CPIC guidelines but no sample findings."""
    yield from _setup_client(tmp_data_dir, CPIC_GUIDELINES_DATA)


@pytest.fixture
def client_no_guidelines(tmp_data_dir: Path) -> Generator[tuple[TestClient, int], None, None]:
    """Client with no CPIC guidelines loaded."""
    yield from _setup_client(tmp_data_dir, [])


@pytest.fixture
def ugt1a1_conservative_sample(tmp_data_dir: Path) -> Generator[int, None, None]:
    """Registry-backed sample with conservative UGT1A1 alert rows."""
    settings = Settings(data_dir=tmp_data_dir, wal_mode=False)

    ref_engine = sa.create_engine(f"sqlite:///{settings.reference_db_path}")
    reference_metadata.create_all(ref_engine)
    with ref_engine.begin() as conn:
        result = conn.execute(
            samples.insert().values(
                name="test_ugt1a1_conservative",
                db_path="samples/sample_1.db",
                file_format="23andme_v5",
                file_hash="hash_ugt1a1_conservative",
            )
        )
        sample_id = result.lastrowid
        conn.execute(cpic_guidelines.insert(), CPIC_GUIDELINES_DATA + _UGT1A1_GUIDELINES)
    ref_engine.dispose()

    sample_db_path = tmp_data_dir / "samples" / "sample_1.db"
    sample_engine = sa.create_engine(f"sqlite:///{sample_db_path}")
    create_sample_tables(sample_engine)
    with sample_engine.begin() as conn:
        conn.execute(findings.insert(), _UGT1A1_CONSERVATIVE_FINDINGS)
    sample_engine.dispose()

    with patch("backend.api.routes.pharma.get_registry") as mock_reg:
        reset_registry()
        registry = DBRegistry(settings)
        mock_reg.return_value = registry
        yield sample_id
        registry.dispose_all()
        reset_registry()


# ═══════════════════════════════════════════════════════════════════════
# GET /api/analysis/pharma/drugs — List all drugs
# ═══════════════════════════════════════════════════════════════════════


class TestListDrugs:
    def test_returns_all_drugs(self, client: tuple[TestClient, int]):
        tc, _ = client
        resp = tc.get("/api/analysis/pharma/drugs")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 3
        drug_names = [item["drug"] for item in data["items"]]
        assert "clopidogrel" in drug_names
        assert "codeine" in drug_names
        assert "warfarin" in drug_names

    def test_drug_has_genes(self, client: tuple[TestClient, int]):
        tc, _ = client
        resp = tc.get("/api/analysis/pharma/drugs")
        data = resp.json()
        clopidogrel = next(i for i in data["items"] if i["drug"] == "clopidogrel")
        assert clopidogrel["genes"] == ["CYP2C19"]
        assert clopidogrel["classification"] == "A"

    def test_codeine_gene(self, client: tuple[TestClient, int]):
        tc, _ = client
        resp = tc.get("/api/analysis/pharma/drugs")
        data = resp.json()
        codeine = next(i for i in data["items"] if i["drug"] == "codeine")
        assert codeine["genes"] == ["CYP2D6"]

    def test_empty_when_no_guidelines(self, client_no_guidelines: tuple[TestClient, int]):
        tc, _ = client_no_guidelines
        resp = tc.get("/api/analysis/pharma/drugs")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0
        assert data["items"] == []


# ═══════════════════════════════════════════════════════════════════════
# GET /api/analysis/pharma/drug/{drug_name} — Drug lookup
# ═══════════════════════════════════════════════════════════════════════


class TestDrugLookup:
    """T3-05: Drug lookup for clopidogrel returns CYP2C19 with correct
    genotype, recommendation, and call confidence."""

    def test_clopidogrel_returns_cyp2c19(self, client: tuple[TestClient, int]):
        tc, sample_id = client
        resp = tc.get(f"/api/analysis/pharma/drug/clopidogrel?sample_id={sample_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["drug"] == "clopidogrel"
        assert len(data["gene_effects"]) == 1
        effect = data["gene_effects"][0]
        assert effect["gene"] == "CYP2C19"
        assert effect["diplotype"] == "*1/*2"
        assert effect["metabolizer_status"] == "Intermediate Metabolizer"
        assert effect["recommendation"] == "Consider alternative antiplatelet therapy."
        assert effect["classification"] == "A"
        assert effect["call_confidence"] == "Complete"
        assert effect["evidence_level"] == 4
        assert effect["activity_score"] == 0.5
        assert effect["involved_rsids"] == ["rs4244285"]
        # An evaluated gene must NOT be flagged not_assessed (#905).
        assert effect["not_assessed"] is False

    def test_codeine_returns_cyp2d6(self, client: tuple[TestClient, int]):
        tc, sample_id = client
        resp = tc.get(f"/api/analysis/pharma/drug/codeine?sample_id={sample_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["drug"] == "codeine"
        assert len(data["gene_effects"]) == 1
        effect = data["gene_effects"][0]
        assert effect["gene"] == "CYP2D6"
        assert effect["diplotype"] == "*1/*4"
        assert effect["call_confidence"] == "Partial"

    def test_case_insensitive(self, client: tuple[TestClient, int]):
        tc, sample_id = client
        resp = tc.get(f"/api/analysis/pharma/drug/Clopidogrel?sample_id={sample_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["drug"] == "clopidogrel"

    def test_unknown_drug_404(self, client: tuple[TestClient, int]):
        tc, sample_id = client
        resp = tc.get(f"/api/analysis/pharma/drug/nonexistent_drug?sample_id={sample_id}")
        assert resp.status_code == 404

    def test_unknown_sample_404(self, client: tuple[TestClient, int]):
        tc, _ = client
        resp = tc.get("/api/analysis/pharma/drug/clopidogrel?sample_id=9999")
        assert resp.status_code == 404

    def test_missing_sample_id(self, client: tuple[TestClient, int]):
        tc, _ = client
        resp = tc.get("/api/analysis/pharma/drug/clopidogrel")
        assert resp.status_code == 422  # FastAPI validation error

    def test_drug_without_sample_finding(self, client: tuple[TestClient, int]):
        """Warfarin has guidelines but no sample findings → gene with no user data."""
        tc, sample_id = client
        resp = tc.get(f"/api/analysis/pharma/drug/warfarin?sample_id={sample_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["drug"] == "warfarin"
        assert len(data["gene_effects"]) == 1
        effect = data["gene_effects"][0]
        assert effect["gene"] == "CYP2C9"
        # No sample finding → fields should be None
        assert effect["diplotype"] is None
        assert effect["metabolizer_status"] is None
        assert effect["call_confidence"] is None
        # But should still have guideline metadata
        assert effect["classification"] == "A"
        # …flagged not_assessed so the UI shows "not assessed", not a bare card (#905).
        assert effect["not_assessed"] is True

    def test_no_findings_at_all(self, client_no_findings: tuple[TestClient, int]):
        """Sample has no PGx findings — genes returned with guideline info only."""
        tc, sample_id = client_no_findings
        resp = tc.get(f"/api/analysis/pharma/drug/clopidogrel?sample_id={sample_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["gene_effects"]) == 1
        effect = data["gene_effects"][0]
        assert effect["gene"] == "CYP2C19"
        assert effect["diplotype"] is None
        assert effect["classification"] == "A"
        assert effect["not_assessed"] is True


class TestDrugLookupGuideline:
    def test_guideline_url_present(self, client: tuple[TestClient, int]):
        tc, sample_id = client
        resp = tc.get(f"/api/analysis/pharma/drug/clopidogrel?sample_id={sample_id}")
        data = resp.json()
        effect = data["gene_effects"][0]
        assert effect["guideline_url"].startswith("https://cpicpgx.org/")

    def test_confidence_note(self, client: tuple[TestClient, int]):
        tc, sample_id = client
        resp = tc.get(f"/api/analysis/pharma/drug/clopidogrel?sample_id={sample_id}")
        data = resp.json()
        effect = data["gene_effects"][0]
        assert effect["confidence_note"] is not None
        assert len(effect["confidence_note"]) > 0

    def test_ehr_notation(self, client: tuple[TestClient, int]):
        tc, sample_id = client
        resp = tc.get(f"/api/analysis/pharma/drug/clopidogrel?sample_id={sample_id}")
        data = resp.json()
        effect = data["gene_effects"][0]
        assert effect["ehr_notation"] == "Intermediate Metabolizer"


# ═══════════════════════════════════════════════════════════════════════
# GET /api/analysis/pharma/genes — Per-gene star-allele results
# ═══════════════════════════════════════════════════════════════════════


class TestGeneResults:
    """Per-gene metabolizer card endpoint returns grouped findings."""

    def test_returns_all_genes(self, client: tuple[TestClient, int]):
        tc, sample_id = client
        resp = tc.get(f"/api/analysis/pharma/genes?sample_id={sample_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 2
        genes = [item["gene"] for item in data["items"]]
        assert "CYP2C19" in genes
        assert "CYP2D6" in genes

    def test_cyp2c19_detail(self, client: tuple[TestClient, int]):
        tc, sample_id = client
        resp = tc.get(f"/api/analysis/pharma/genes?sample_id={sample_id}")
        data = resp.json()
        cyp2c19 = next(i for i in data["items"] if i["gene"] == "CYP2C19")
        assert cyp2c19["diplotype"] == "*1/*2"
        assert cyp2c19["phenotype"] == "Intermediate Metabolizer"
        assert cyp2c19["call_confidence"] == "Complete"
        assert cyp2c19["confidence_note"] == "All defining rsids present and genotyped."
        assert cyp2c19["activity_score"] == 0.5
        assert cyp2c19["ehr_notation"] == "Intermediate Metabolizer"
        assert cyp2c19["evidence_level"] == 4
        assert cyp2c19["involved_rsids"] == ["rs4244285"]

    def test_cyp2d6_detail(self, client: tuple[TestClient, int]):
        tc, sample_id = client
        resp = tc.get(f"/api/analysis/pharma/genes?sample_id={sample_id}")
        data = resp.json()
        cyp2d6 = next(i for i in data["items"] if i["gene"] == "CYP2D6")
        assert cyp2d6["diplotype"] == "*1/*4"
        assert cyp2d6["phenotype"] == "Intermediate Metabolizer"
        assert cyp2d6["call_confidence"] == "Partial"
        assert cyp2d6["activity_score"] == 1.0
        assert cyp2d6["involved_rsids"] == ["rs3892097"]

    def test_drugs_populated_from_cpic(self, client: tuple[TestClient, int]):
        tc, sample_id = client
        resp = tc.get(f"/api/analysis/pharma/genes?sample_id={sample_id}")
        data = resp.json()
        cyp2c19 = next(i for i in data["items"] if i["gene"] == "CYP2C19")
        assert "clopidogrel" in cyp2c19["drugs"]
        cyp2d6 = next(i for i in data["items"] if i["gene"] == "CYP2D6")
        assert "codeine" in cyp2d6["drugs"]

    def test_empty_when_no_findings(self, client_no_findings: tuple[TestClient, int]):
        tc, sample_id = client_no_findings
        resp = tc.get(f"/api/analysis/pharma/genes?sample_id={sample_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0
        assert data["items"] == []

    def test_unknown_sample_404(self, client: tuple[TestClient, int]):
        tc, _ = client
        resp = tc.get("/api/analysis/pharma/genes?sample_id=9999")
        assert resp.status_code == 404

    def test_missing_sample_id(self, client: tuple[TestClient, int]):
        tc, _ = client
        resp = tc.get("/api/analysis/pharma/genes")
        assert resp.status_code == 422

    def test_items_sorted_by_gene(self, client: tuple[TestClient, int]):
        tc, sample_id = client
        resp = tc.get(f"/api/analysis/pharma/genes?sample_id={sample_id}")
        data = resp.json()
        genes = [item["gene"] for item in data["items"]]
        assert genes == sorted(genes)

    def test_caveat_absent_when_finding_has_none(self, client: tuple[TestClient, int]):
        """The route passes through gene_caveat=None when the finding has none.

        CYP2C19 has no gene-specific caveat (unlike DPYD's fatal-toxicity caveat or
        CYP2D6's copy-number caveat, which are injected at store time), so its card
        must expose gene_caveat=None.
        """
        tc, sample_id = client
        resp = tc.get(f"/api/analysis/pharma/genes?sample_id={sample_id}")
        assert resp.status_code == 200
        items = resp.json()["items"]
        cyp2c19 = next(i for i in items if i["gene"] == "CYP2C19")
        assert cyp2c19.get("gene_caveat") is None


class TestUgt1a1ConservativeSummary:
    """Conservative drug alerts must not replace the gene-level called phenotype."""

    def test_genes_endpoint_prefers_called_phenotype(
        self,
        ugt1a1_conservative_sample: int,
    ):
        from backend.api.routes.pharma import gene_results

        resp = gene_results(sample_id=ugt1a1_conservative_sample)
        ugt = next(i for i in resp.items if i.gene == "UGT1A1")

        assert ugt.diplotype == "*1/*1"
        assert ugt.phenotype == "Normal Metabolizer"
        assert ugt.activity_score == 2.0
        assert ugt.ehr_notation == "UGT1A1 Normal Metabolizer"
        assert ugt.call_confidence == "Partial"
        assert set(ugt.drugs) == {"atazanavir", "irinotecan"}

    def test_drug_endpoint_keeps_conservative_alert_phenotype(
        self,
        ugt1a1_conservative_sample: int,
    ):
        from backend.api.routes.pharma import drug_lookup

        resp = drug_lookup("atazanavir", sample_id=ugt1a1_conservative_sample)
        effect = resp.gene_effects[0]

        assert effect.gene == "UGT1A1"
        assert effect.metabolizer_status == "Intermediate Metabolizer"
        assert effect.activity_score == 1.0
        assert effect.ehr_notation == "UGT1A1 Intermediate Metabolizer"
        assert effect.recommendation == "Low likelihood of bilirubin-related discontinuation."

    def test_report_gene_coverage_uses_called_phenotype_but_drug_effects_do_not(
        self,
        ugt1a1_conservative_sample: int,
    ):
        from backend.api.routes.pharma import medication_safety_report

        resp = medication_safety_report(sample_id=ugt1a1_conservative_sample)

        ugt_coverage = next(g for g in resp.gene_coverage if g.gene == "UGT1A1")
        assert ugt_coverage.phenotype == "Normal Metabolizer"
        assert ugt_coverage.activity_score == 2.0
        assert ugt_coverage.ehr_notation == "UGT1A1 Normal Metabolizer"
        assert ugt_coverage.indeterminate_alleles == ["*28"]

        atazanavir = next(d for d in resp.drugs if d.drug == "atazanavir")
        effect = atazanavir.gene_effects[0]
        assert effect.phenotype == "Intermediate Metabolizer"
        assert effect.activity_score == 1.0
        assert effect.recommendation == "Low likelihood of bilirubin-related discontinuation."


# ── DPYD fluoropyrimidine caveat surfacing (SW-E5) ────────────────────


_DPYD_GUIDELINE = {
    "gene": "DPYD",
    "drug": "fluorouracil",
    "phenotype": "Intermediate Metabolizer",
    "recommendation": "Reduce starting dose by 50%, then titrate.",
    "classification": "A",
    "guideline_url": "https://cpicpgx.org/guidelines/guideline-for-fluoropyrimidines-and-dpyd/",
}

_DPYD_FINDING = {
    "module": "pharmacogenomics",
    "category": "prescribing_alert",
    "evidence_level": 4,
    "gene_symbol": "DPYD",
    "diplotype": "*1/*2A",
    "metabolizer_status": "Intermediate Metabolizer",
    "drug": "fluorouracil",
    "finding_text": "DPYD *1/*2A: Intermediate Metabolizer -- fluorouracil: Reduce dose.",
    "detail_json": json.dumps(
        {
            "recommendation": "Reduce starting dose by 50%, then titrate.",
            "classification": "A",
            "guideline_url": _DPYD_GUIDELINE["guideline_url"],
            "call_confidence": "Complete",
            "confidence_note": "All defining positions assessed.",
            "activity_score": 1.0,
            "ehr_notation": "DPYD Intermediate Metabolizer",
            "involved_rsids": ["rs3918290"],
            "gene_caveat": DPYD_FLUOROPYRIMIDINE_CAVEAT,
        }
    ),
}


@pytest.fixture
def dpyd_client(tmp_data_dir: Path) -> Generator[tuple[TestClient, int], None, None]:
    yield from _setup_client(
        tmp_data_dir, CPIC_GUIDELINES_DATA + [_DPYD_GUIDELINE], [_DPYD_FINDING]
    )


class TestDpydCaveatSurfacing:
    def test_genes_endpoint_surfaces_caveat(self, dpyd_client: tuple[TestClient, int]):
        tc, sample_id = dpyd_client
        resp = tc.get(f"/api/analysis/pharma/genes?sample_id={sample_id}")
        assert resp.status_code == 200
        dpyd = next(i for i in resp.json()["items"] if i["gene"] == "DPYD")
        assert dpyd["gene_caveat"] == DPYD_FLUOROPYRIMIDINE_CAVEAT

    def test_drug_endpoint_surfaces_caveat(self, dpyd_client: tuple[TestClient, int]):
        tc, sample_id = dpyd_client
        resp = tc.get(f"/api/analysis/pharma/drug/fluorouracil?sample_id={sample_id}")
        assert resp.status_code == 200
        dpyd = next(e for e in resp.json()["gene_effects"] if e["gene"] == "DPYD")
        assert dpyd["gene_caveat"] == DPYD_FLUOROPYRIMIDINE_CAVEAT


# ═══════════════════════════════════════════════════════════════════════
# GET /api/analysis/pharma/report — Consolidated medication-safety report (SW-E4)
# ═══════════════════════════════════════════════════════════════════════


# Findings exercising the report: an actionable result (CYP2C19/clopidogrel) with
# a coverage block, a routine result (CYP2D6/codeine — but with the copy-number
# caveat), and a DPYD result carrying the fatal-toxicity caveat.
_REPORT_FINDINGS = [
    {
        "module": "pharmacogenomics",
        "category": "prescribing_alert",
        "evidence_level": 4,
        "gene_symbol": "CYP2C19",
        "diplotype": "*1/*2",
        "metabolizer_status": "Intermediate Metabolizer",
        "drug": "clopidogrel",
        "finding_text": "CYP2C19 *1/*2: Intermediate Metabolizer -- clopidogrel: ...",
        "detail_json": json.dumps(
            {
                "recommendation": "Consider alternative antiplatelet therapy.",
                "classification": "A",
                "guideline_url": "https://cpicpgx.org/guidelines/clopidogrel/",
                "call_confidence": "Complete",
                "confidence_note": "All defining positions assessed.",
                "activity_score": 0.5,
                "ehr_notation": "Intermediate Metabolizer",
                "involved_rsids": ["rs4244285"],
                "coverage": {"assessed": 3, "total": 4},
            }
        ),
    },
    {
        "module": "pharmacogenomics",
        "category": "prescribing_alert",
        "evidence_level": 4,
        "gene_symbol": "CYP2D6",
        "diplotype": "*1/*1",
        "metabolizer_status": "Normal Metabolizer",
        "drug": "codeine",
        "finding_text": "CYP2D6 *1/*1: Normal Metabolizer -- codeine: ...",
        "detail_json": json.dumps(
            {
                "recommendation": "Use label-recommended age- or weight-specific dosing.",
                "classification": "A",
                "guideline_url": "https://cpicpgx.org/guidelines/codeine/",
                "call_confidence": "Partial",
                "confidence_note": "Structural variant gene.",
                "activity_score": 2.0,
                "ehr_notation": "Normal Metabolizer",
                "involved_rsids": ["rs3892097"],
                "coverage": {"assessed": 5, "total": 5},
                "gene_caveat": "CYP2D6 copy-number caveat text.",
            }
        ),
    },
    {
        "module": "pharmacogenomics",
        "category": "prescribing_alert",
        "evidence_level": 4,
        "gene_symbol": "DPYD",
        "diplotype": "*1/*2A",
        "metabolizer_status": "Intermediate Metabolizer",
        "drug": "fluorouracil",
        "finding_text": "DPYD *1/*2A: Intermediate Metabolizer -- fluorouracil: ...",
        "detail_json": json.dumps(
            {
                "recommendation": "Reduce starting dose by 50%, then titrate.",
                "classification": "A",
                "guideline_url": "https://cpicpgx.org/guidelines/fluoropyrimidines/",
                "call_confidence": "Complete",
                "confidence_note": "All defining positions assessed.",
                "activity_score": 1.0,
                "ehr_notation": "DPYD Intermediate Metabolizer",
                "involved_rsids": ["rs3918290"],
                "coverage": {"assessed": 4, "total": 4},
                "gene_caveat": DPYD_FLUOROPYRIMIDINE_CAVEAT,
            }
        ),
    },
]


@pytest.fixture
def report_client(tmp_data_dir: Path) -> Generator[tuple[TestClient, int], None, None]:
    yield from _setup_client(tmp_data_dir, CPIC_GUIDELINES_DATA, _REPORT_FINDINGS)


# Findings with corrupted coverage blocks: wrong value types and a non-dict value.
# The report must degrade to coverage=null (200), never raise a 500.
_MALFORMED_COVERAGE_FINDINGS = [
    {
        "module": "pharmacogenomics",
        "category": "prescribing_alert",
        "evidence_level": 4,
        "gene_symbol": "CYP2C19",
        "diplotype": "*1/*2",
        "metabolizer_status": "Intermediate Metabolizer",
        "drug": "clopidogrel",
        "finding_text": "CYP2C19 *1/*2 -- clopidogrel",
        "detail_json": json.dumps(
            {
                "recommendation": "Consider alternative antiplatelet therapy.",
                "classification": "A",
                "call_confidence": "Complete",
                # Wrong types: total is null, assessed is a string.
                "coverage": {"assessed": "3", "total": None},
            }
        ),
    },
    {
        "module": "pharmacogenomics",
        "category": "prescribing_alert",
        "evidence_level": 4,
        "gene_symbol": "CYP2D6",
        "diplotype": "*1/*1",
        "metabolizer_status": "Normal Metabolizer",
        "drug": "codeine",
        "finding_text": "CYP2D6 *1/*1 -- codeine",
        "detail_json": json.dumps(
            {
                "recommendation": "Use label-recommended dosing.",
                "classification": "A",
                "call_confidence": "Partial",
                # Non-dict coverage value.
                "coverage": "n/a",
            }
        ),
    },
]


@pytest.fixture
def malformed_coverage_client(tmp_data_dir: Path) -> Generator[tuple[TestClient, int], None, None]:
    yield from _setup_client(tmp_data_dir, CPIC_GUIDELINES_DATA, _MALFORMED_COVERAGE_FINDINGS)


class TestMedicationSafetyReport:
    def test_disclosure_present(self, report_client: tuple[TestClient, int]):
        tc, sample_id = report_client
        resp = tc.get(f"/api/analysis/pharma/report?sample_id={sample_id}")
        assert resp.status_code == 200
        assert resp.json()["reference_bias_disclosure"] == MEDICATION_SAFETY_REFERENCE_BIAS

    def test_counts(self, report_client: tuple[TestClient, int]):
        tc, sample_id = report_client
        data = tc.get(f"/api/analysis/pharma/report?sample_id={sample_id}").json()
        assert data["genes_assessed"] == 3  # CYP2C19, CYP2D6, DPYD
        assert data["drugs_assessed"] == 3  # clopidogrel, codeine, fluorouracil
        # clopidogrel (Consider alternative) + fluorouracil (Reduce) are actionable;
        # codeine (label-recommended) is routine.
        assert data["actionable_drug_count"] == 2

    def test_actionable_sorted_first(self, report_client: tuple[TestClient, int]):
        tc, sample_id = report_client
        data = tc.get(f"/api/analysis/pharma/report?sample_id={sample_id}").json()
        drugs = data["drugs"]
        # Actionable drugs (clopidogrel, fluorouracil) precede the routine one (codeine).
        assert [d["drug"] for d in drugs] == ["clopidogrel", "fluorouracil", "codeine"]
        assert drugs[0]["actionable"] is True
        assert drugs[-1]["actionable"] is False
        assert drugs[-1]["drug"] == "codeine"

    def test_effect_actionability_labels(self, report_client: tuple[TestClient, int]):
        tc, sample_id = report_client
        data = tc.get(f"/api/analysis/pharma/report?sample_id={sample_id}").json()
        by_drug = {d["drug"]: d for d in data["drugs"]}
        clopidogrel = by_drug["clopidogrel"]["gene_effects"][0]
        assert clopidogrel["actionability"] == "actionable"
        codeine = by_drug["codeine"]["gene_effects"][0]
        assert codeine["actionability"] == "routine"

    def test_coverage_surfaced(self, report_client: tuple[TestClient, int]):
        tc, sample_id = report_client
        data = tc.get(f"/api/analysis/pharma/report?sample_id={sample_id}").json()
        cyp2c19 = next(g for g in data["gene_coverage"] if g["gene"] == "CYP2C19")
        assert cyp2c19["coverage"] == {"assessed": 3, "total": 4}
        # And on the per-drug effect too.
        clopidogrel = next(d for d in data["drugs"] if d["drug"] == "clopidogrel")
        assert clopidogrel["gene_effects"][0]["coverage"] == {"assessed": 3, "total": 4}

    def test_phenotype_terms_and_confidence(self, report_client: tuple[TestClient, int]):
        tc, sample_id = report_client
        data = tc.get(f"/api/analysis/pharma/report?sample_id={sample_id}").json()
        cyp2d6 = next(g for g in data["gene_coverage"] if g["gene"] == "CYP2D6")
        assert cyp2d6["phenotype"] == "Normal Metabolizer"
        assert cyp2d6["call_confidence"] == "Partial"

    def test_gene_caveat_surfaced(self, report_client: tuple[TestClient, int]):
        tc, sample_id = report_client
        data = tc.get(f"/api/analysis/pharma/report?sample_id={sample_id}").json()
        dpyd = next(g for g in data["gene_coverage"] if g["gene"] == "DPYD")
        assert dpyd["gene_caveat"] == DPYD_FLUOROPYRIMIDINE_CAVEAT
        fluorouracil = next(d for d in data["drugs"] if d["drug"] == "fluorouracil")
        assert fluorouracil["gene_effects"][0]["gene_caveat"] == DPYD_FLUOROPYRIMIDINE_CAVEAT

    def test_gene_coverage_sorted(self, report_client: tuple[TestClient, int]):
        tc, sample_id = report_client
        data = tc.get(f"/api/analysis/pharma/report?sample_id={sample_id}").json()
        genes = [g["gene"] for g in data["gene_coverage"]]
        assert genes == sorted(genes)

    def test_coverage_none_when_absent(self, client: tuple[TestClient, int]):
        # The shared `client` fixture's findings predate coverage persistence —
        # the report must degrade gracefully (coverage null), not error.
        tc, sample_id = client
        resp = tc.get(f"/api/analysis/pharma/report?sample_id={sample_id}")
        assert resp.status_code == 200
        for gene in resp.json()["gene_coverage"]:
            assert gene["coverage"] is None

    def test_empty_when_no_findings(self, client_no_findings: tuple[TestClient, int]):
        tc, sample_id = client_no_findings
        resp = tc.get(f"/api/analysis/pharma/report?sample_id={sample_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["genes_assessed"] == 0
        assert data["drugs_assessed"] == 0
        assert data["actionable_drug_count"] == 0
        assert data["drugs"] == []
        assert data["gene_coverage"] == []
        # Disclosure is always present, even with no findings.
        assert data["reference_bias_disclosure"] == MEDICATION_SAFETY_REFERENCE_BIAS

    def test_unknown_sample_404(self, report_client: tuple[TestClient, int]):
        tc, _ = report_client
        resp = tc.get("/api/analysis/pharma/report?sample_id=9999")
        assert resp.status_code == 404

    def test_missing_sample_id_422(self, report_client: tuple[TestClient, int]):
        tc, _ = report_client
        resp = tc.get("/api/analysis/pharma/report")
        assert resp.status_code == 422

    def test_malformed_coverage_degrades_gracefully(
        self, malformed_coverage_client: tuple[TestClient, int]
    ):
        # A corrupted/older coverage block (wrong types, or a non-dict value) must
        # yield coverage=null with a 200, never a 500 ValidationError.
        tc, sample_id = malformed_coverage_client
        resp = tc.get(f"/api/analysis/pharma/report?sample_id={sample_id}")
        assert resp.status_code == 200
        for gene in resp.json()["gene_coverage"]:
            assert gene["coverage"] is None
        for drug in resp.json()["drugs"]:
            for effect in drug["gene_effects"]:
                assert effect["coverage"] is None

    def test_report_writes_no_findings(self, report_client: tuple[TestClient, int]):
        # Golden-snapshot guardrail: the report is read-only and must never create
        # findings. Pin the invariant with a before/after count. _get_sample_engine
        # resolves the same per-sample DB the route uses (registry is patched and
        # active inside the fixture's TestClient context).
        from backend.api.routes.pharma import _get_sample_engine

        tc, sample_id = report_client
        engine = _get_sample_engine(sample_id)
        with engine.connect() as conn:
            before = conn.execute(sa.select(sa.func.count()).select_from(findings)).scalar()
        assert tc.get(f"/api/analysis/pharma/report?sample_id={sample_id}").status_code == 200
        with engine.connect() as conn:
            after = conn.execute(sa.select(sa.func.count()).select_from(findings)).scalar()
        assert before == after

"""Tests for Gene Allergy & Immune Sensitivities findings API (P3-60).

Covers:
  - GET /api/analysis/allergy/pathways?sample_id=N — All pathway results
  - GET /api/analysis/allergy/pathway/{id}?sample_id=N — Single pathway detail
  - POST /api/analysis/allergy/run?sample_id=N — Run scoring
  - Celiac combined assessment in pathways response
  - Histamine combined assessment in pathways response
  - HLA proxy lookup data in pathway detail
  - Cross-module findings in pathways response
  - Missing sample returns 404
  - Empty findings returns empty list
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
    findings,
    raw_variants,
    reference_metadata,
    samples,
)

# ── Test data ────────────────────────────────────────────────────────

HLA_B5801_NEGATIVE_PROXY_CAVEAT = (
    "No HLA-B*58:01 tag-SNP allele was detected at rs9263726, but this proxy result "
    "does not exclude the HLA allele. Proxy LD varies by ancestry; use high-resolution "
    "HLA typing for clinical allopurinol decisions."
)

PATHWAY_SUMMARY_FINDINGS = [
    {
        "module": "allergy",
        "category": "pathway_summary",
        "evidence_level": 2,
        "gene_symbol": None,
        "rsid": None,
        "finding_text": "Atopic Conditions — Moderate consideration",
        "pathway": "Atopic Conditions",
        "pathway_level": "Moderate",
        "pmid_citations": json.dumps(["12925570", "18403759"]),
        "detail_json": json.dumps(
            {
                "pathway_id": "atopic_conditions",
                "called_snps": 2,
                "total_snps": 3,
                "missing_snps": ["rs324011"],
                "snp_details": [
                    {
                        "rsid": "rs20541",
                        "gene": "IL13",
                        "variant_name": "R130Q",
                        "genotype": "GA",
                        "category": "Moderate",
                        "effect_summary": "One copy of the R130Q variant.",
                        "evidence_level": 2,
                        "hla_proxy": None,
                        "coverage_note": None,
                    },
                    {
                        "rsid": "rs8076131",
                        "gene": "ORMDL3",
                        "variant_name": "ORMDL3 intergenic",
                        "genotype": "AA",
                        "category": "Standard",
                        "effect_summary": "No risk allele.",
                        "evidence_level": 2,
                        "hla_proxy": None,
                        "coverage_note": None,
                    },
                ],
            }
        ),
    },
    {
        "module": "allergy",
        "category": "pathway_summary",
        "evidence_level": 4,
        "gene_symbol": None,
        "rsid": None,
        "finding_text": "Drug Hypersensitivity — Elevated consideration",
        "pathway": "Drug Hypersensitivity",
        "pathway_level": "Elevated",
        "pmid_citations": json.dumps(["18196153"]),
        "detail_json": json.dumps(
            {
                "pathway_id": "drug_hypersensitivity",
                "called_snps": 2,
                "total_snps": 4,
                "missing_snps": ["rs144012689", "rs1061235"],
                "snp_details": [
                    {
                        "rsid": "rs2395029",
                        "gene": "HLA-B",
                        "variant_name": "HLA-B*57:01 proxy",
                        "genotype": "TG",
                        "category": "Elevated",
                        "effect_summary": "Carrier of HLA-B*57:01 proxy allele.",
                        "evidence_level": 4,
                        "hla_proxy": {
                            "hla_allele": "HLA-B*57:01",
                            "r_squared_eur": 0.97,
                            "clinical_grade": True,
                            "confirmatory_test_required": True,
                        },
                        "hla_proxy_lookup": {
                            "hla_allele": "HLA-B*57:01",
                            "r_squared_by_pop": {"EUR": 0.97, "AFR": 0.85},
                            "clinical_context": "Abacavir hypersensitivity",
                        },
                        "hla_proxy_caveat": "Confirmatory HLA typing required.",
                        "coverage_note": None,
                    },
                    {
                        "rsid": "rs9263726",
                        "gene": "HLA-B",
                        "variant_name": "HLA-B*58:01 proxy",
                        "genotype": "CC",
                        "category": "Standard",
                        "effect_summary": (
                            "No rs9263726 HLA-B*58:01 proxy allele detected. "
                            "This tag-SNP result does not rule out HLA-B*58:01."
                        ),
                        "evidence_level": 4,
                        "hla_proxy": {
                            "hla_allele": "HLA-B*58:01",
                            "r_squared_eur": 0.91,
                            "r_squared_eas": 0.87,
                            "r_squared_afr": 0.78,
                            "clinical_grade": False,
                            "confirmatory_test_required": True,
                        },
                        "hla_proxy_lookup": {
                            "hla_allele": "HLA-B*58:01",
                            "r_squared_by_pop": {"EUR": 0.91, "EAS": 0.87, "AFR": 0.78},
                            "clinical_context": "Allopurinol hypersensitivity (SJS/TEN)",
                        },
                        "hla_proxy_caveat": HLA_B5801_NEGATIVE_PROXY_CAVEAT,
                        "coverage_note": None,
                    },
                ],
                "hla_proxy_lookup": {
                    "rs2395029": {
                        "hla_allele": "HLA-B*57:01",
                        "r_squared_by_pop": {"EUR": 0.97, "AFR": 0.85},
                        "clinical_context": "Abacavir hypersensitivity",
                    },
                    "rs9263726": {
                        "hla_allele": "HLA-B*58:01",
                        "r_squared_by_pop": {"EUR": 0.91, "EAS": 0.87, "AFR": 0.78},
                        "clinical_context": "Allopurinol hypersensitivity (SJS/TEN)",
                    },
                },
            }
        ),
    },
    {
        "module": "allergy",
        "category": "pathway_summary",
        "evidence_level": 3,
        "gene_symbol": None,
        "rsid": None,
        "finding_text": "Food Sensitivity — Standard (no variants of concern)",
        "pathway": "Food Sensitivity",
        "pathway_level": "Standard",
        "pmid_citations": json.dumps([]),
        "detail_json": json.dumps(
            {
                "pathway_id": "food_sensitivity",
                "called_snps": 0,
                "total_snps": 2,
                "missing_snps": ["rs2187668", "rs7775228"],
                "snp_details": [],
            }
        ),
    },
    {
        "module": "allergy",
        "category": "pathway_summary",
        "evidence_level": 1,
        "gene_symbol": None,
        "rsid": None,
        "finding_text": "Histamine Metabolism — Standard (no variants of concern)",
        "pathway": "Histamine Metabolism",
        "pathway_level": "Standard",
        "pmid_citations": json.dumps([]),
        "detail_json": json.dumps(
            {
                "pathway_id": "histamine_metabolism",
                "called_snps": 0,
                "total_snps": 2,
                "missing_snps": ["rs10156191", "rs11558538"],
                "snp_details": [],
            }
        ),
    },
]

SNP_FINDING = {
    "module": "allergy",
    "category": "snp_finding",
    "evidence_level": 2,
    "gene_symbol": "IL13",
    "rsid": "rs20541",
    "finding_text": "IL13 R130Q (GA) — One copy of the R130Q variant.",
    "pathway": "Atopic Conditions",
    "pathway_level": "Moderate",
    "pmid_citations": json.dumps(["12925570"]),
    "detail_json": json.dumps(
        {
            "variant_name": "R130Q",
            "genotype": "GA",
            "recommendation": "IL13 R130Q is a well-replicated GWAS hit.",
        }
    ),
}

CELIAC_COMBINED_FINDING = {
    "module": "allergy",
    "category": "celiac_combined",
    "evidence_level": 3,
    "gene_symbol": None,
    "rsid": None,
    "finding_text": (
        "Celiac Disease Risk Assessment — Low Celiac Risk. Neither DQ2 nor DQ8 detected."
    ),
    "pathway": "Food Sensitivity",
    "pathway_level": None,
    "pmid_citations": json.dumps(["18311140"]),
    "detail_json": json.dumps(
        {
            "state": "neither",
            "label": "Low Celiac Risk",
            "dq2_genotype": "CC",
            "dq8_genotype": "CC",
        }
    ),
}

HISTAMINE_COMBINED_FINDING = {
    "module": "allergy",
    "category": "histamine_combined",
    "evidence_level": 1,
    "gene_symbol": None,
    "rsid": None,
    "finding_text": "Histamine Metabolism — No histamine metabolism variants detected.",
    "pathway": "Histamine Metabolism",
    "pathway_level": None,
    "pmid_citations": json.dumps(["15046637"]),
    "detail_json": json.dumps(
        {
            "aoc1_genotype": None,
            "hnmt_genotype": None,
            "aoc1_category": "Standard",
            "hnmt_category": "Standard",
            "de_emphasize": True,
        }
    ),
}

CROSS_MODULE_FINDING = {
    "module": "allergy",
    "category": "cross_module",
    "evidence_level": 4,
    "gene_symbol": "HLA-B",
    "rsid": "rs2395029",
    "finding_text": "HLA-B*57:01 proxy (rs2395029, TG) — See PGx for prescribing guidance.",
    "pathway": None,
    "pathway_level": None,
    "pmid_citations": json.dumps(["18196153"]),
    "detail_json": json.dumps(
        {
            "source_module": "allergy",
            "target_module": "pharmacogenomics",
            "genotype": "TG",
            "cross_module_note": "See PGx for prescribing guidance.",
        }
    ),
}


# ── Fixtures ─────────────────────────────────────────────────────────


@pytest.fixture()
def _env(tmp_path: Path) -> Generator[tuple[sa.Engine, sa.Engine], None, None]:
    """Set up a temporary DB environment and FastAPI test client."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "samples").mkdir()

    # Reference DB — must exist at the path Settings will look for
    ref_db = data_dir / "reference.db"
    ref_engine = sa.create_engine(f"sqlite:///{ref_db}")
    reference_metadata.create_all(ref_engine)

    # Register a sample
    with ref_engine.begin() as conn:
        conn.execute(
            sa.insert(samples),
            [
                {
                    "name": "test_sample",
                    "db_path": "samples/sample_1.db",
                    "file_format": "23andme_v5",
                    "file_hash": "abc123",
                }
            ],
        )

    # Sample DB
    sample_db = data_dir / "samples" / "sample_1.db"
    sample_engine = sa.create_engine(f"sqlite:///{sample_db}")
    create_sample_tables(sample_engine)

    # Create settings + registry
    settings = Settings(data_dir=data_dir)
    reset_registry()
    registry = DBRegistry(settings)

    with patch("backend.api.routes.allergy.get_registry", return_value=registry):
        yield sample_engine, ref_engine

    reset_registry()


@pytest.fixture()
def client(_env: tuple[sa.Engine, sa.Engine]) -> TestClient:
    """Create a test client for the allergy API."""
    from fastapi import FastAPI

    from backend.api.routes.allergy import router

    app = FastAPI()
    app.include_router(router, prefix="/api")
    return TestClient(app)


@pytest.fixture()
def seeded_client(
    _env: tuple[sa.Engine, sa.Engine],
) -> TestClient:
    """Create a test client with pre-seeded allergy findings."""
    sample_engine, _ = _env

    all_findings = PATHWAY_SUMMARY_FINDINGS + [
        SNP_FINDING,
        CELIAC_COMBINED_FINDING,
        HISTAMINE_COMBINED_FINDING,
        CROSS_MODULE_FINDING,
    ]
    with sample_engine.begin() as conn:
        conn.execute(sa.insert(findings), all_findings)

    from fastapi import FastAPI

    from backend.api.routes.allergy import router

    app = FastAPI()
    app.include_router(router, prefix="/api")
    return TestClient(app)


# ── Endpoint tests ───────────────────────────────────────────────────


class TestListPathways:
    def test_returns_pathways(self, seeded_client: TestClient) -> None:
        resp = seeded_client.get("/api/analysis/allergy/pathways?sample_id=1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 4
        assert len(data["items"]) == 4

    def test_pathway_fields(self, seeded_client: TestClient) -> None:
        resp = seeded_client.get("/api/analysis/allergy/pathways?sample_id=1")
        data = resp.json()
        item = next(i for i in data["items"] if i["pathway_id"] == "atopic_conditions")
        assert item["level"] == "Moderate"
        assert item["evidence_level"] == 2
        assert item["called_snps"] == 2
        assert item["total_snps"] == 3

    def test_celiac_combined_in_response(self, seeded_client: TestClient) -> None:
        resp = seeded_client.get("/api/analysis/allergy/pathways?sample_id=1")
        data = resp.json()
        assert data["celiac_combined"] is not None
        assert data["celiac_combined"]["state"] == "neither"
        assert data["celiac_combined"]["label"] == "Low Celiac Risk"

    def test_histamine_combined_in_response(self, seeded_client: TestClient) -> None:
        resp = seeded_client.get("/api/analysis/allergy/pathways?sample_id=1")
        data = resp.json()
        assert data["histamine_combined"] is not None
        assert data["histamine_combined"]["de_emphasize"] is True

    def test_cross_module_in_response(self, seeded_client: TestClient) -> None:
        resp = seeded_client.get("/api/analysis/allergy/pathways?sample_id=1")
        data = resp.json()
        assert len(data["cross_module"]) >= 1
        cross = data["cross_module"][0]
        assert cross["target_module"] == "pharmacogenomics"

    def test_empty_findings_returns_empty(self, client: TestClient) -> None:
        resp = client.get("/api/analysis/allergy/pathways?sample_id=1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0
        assert data["items"] == []

    def test_missing_sample_404(self, client: TestClient) -> None:
        resp = client.get("/api/analysis/allergy/pathways?sample_id=999")
        assert resp.status_code == 404

    def test_hla_proxy_lookup_in_pathway(self, seeded_client: TestClient) -> None:
        resp = seeded_client.get("/api/analysis/allergy/pathways?sample_id=1")
        data = resp.json()
        drug = next(i for i in data["items"] if i["pathway_id"] == "drug_hypersensitivity")
        assert drug["hla_proxy_lookup"] is not None
        assert "rs2395029" in drug["hla_proxy_lookup"]


class TestPathwayDetail:
    def test_pathway_detail(self, seeded_client: TestClient) -> None:
        resp = seeded_client.get("/api/analysis/allergy/pathway/atopic_conditions?sample_id=1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["pathway_id"] == "atopic_conditions"
        assert data["level"] == "Moderate"
        assert len(data["snp_details"]) == 2

    def test_drug_hypersensitivity_detail(self, seeded_client: TestClient) -> None:
        resp = seeded_client.get("/api/analysis/allergy/pathway/drug_hypersensitivity?sample_id=1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["level"] == "Elevated"
        assert data["hla_proxy_lookup"] is not None
        hla_b5701_detail = next(d for d in data["snp_details"] if d["rsid"] == "rs2395029")
        assert hla_b5701_detail["hla_proxy_lookup"]["r_squared_by_pop"]["AFR"] == 0.85
        assert hla_b5701_detail["hla_proxy_caveat"] == "Confirmatory HLA typing required."
        hla_b5801_detail = next(d for d in data["snp_details"] if d["rsid"] == "rs9263726")
        assert hla_b5801_detail["hla_proxy_caveat"] == HLA_B5801_NEGATIVE_PROXY_CAVEAT
        assert hla_b5801_detail["hla_proxy_lookup"]["r_squared_by_pop"]["AFR"] == 0.78

    def test_missing_pathway_404(self, seeded_client: TestClient) -> None:
        resp = seeded_client.get("/api/analysis/allergy/pathway/nonexistent?sample_id=1")
        assert resp.status_code == 404


class TestRunScoring:
    def test_run_scoring(
        self,
        _env: tuple[sa.Engine, sa.Engine],
    ) -> None:
        """POST /run triggers scoring and returns counts."""
        sample_engine, ref_engine = _env

        # Seed variants
        with sample_engine.begin() as conn:
            conn.execute(
                sa.insert(raw_variants),
                [
                    {"rsid": "rs20541", "chrom": "5", "pos": 131995964, "genotype": "GA"},
                    {"rsid": "rs2395029", "chrom": "6", "pos": 31431272, "genotype": "TG"},
                ],
            )

        from fastapi import FastAPI

        from backend.api.routes.allergy import router

        app = FastAPI()
        app.include_router(router, prefix="/api")
        client = TestClient(app)

        resp = client.post("/api/analysis/allergy/run?sample_id=1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["findings_count"] > 0
        assert data["pathways_scored"] == 4

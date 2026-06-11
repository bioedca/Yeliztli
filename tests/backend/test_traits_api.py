"""Tests for Traits & Personality findings API (P3-64).

Covers:
  - GET /api/analysis/traits/pathways?sample_id=N — All pathway results
  - GET /api/analysis/traits/pathway/{id}?sample_id=N — Single pathway detail
  - GET /api/analysis/traits/prs?sample_id=N — PRS results
  - GET /api/analysis/traits/disclaimer — Module disclaimer
  - Cross-module findings in pathways response
  - Missing sample returns 404
  - Empty findings returns empty list
  - PRS "Research Use Only" flag is always True
  - Evidence cap at 2 stars enforced
"""

from __future__ import annotations

import json
from collections.abc import Generator
from contextlib import ExitStack
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
    reference_metadata,
    samples,
)

# ── Test data ────────────────────────────────────────────────────────

PATHWAY_SUMMARY_FINDINGS = [
    {
        "module": "traits",
        "category": "pathway_summary",
        "evidence_level": 2,
        "gene_symbol": None,
        "rsid": None,
        "finding_text": "Personality Big Five — Moderate consideration",
        "pathway": "Personality Big Five",
        "pathway_level": "Moderate",
        "pmid_citations": json.dumps(["29942085"]),
        "detail_json": json.dumps(
            {
                "pathway_id": "personality_big_five",
                "prs_primary": False,
                "called_snps": 3,
                "total_snps": 5,
                "missing_snps": ["rs747302", "rs6265"],
                "snp_details": [
                    {
                        "rsid": "rs2164273",
                        "gene": "CTNNA2",
                        "variant_name": "CTNNA2 intergenic",
                        "genotype": "AG",
                        "category": "Moderate",
                        "effect_summary": "Associated with increased extraversion.",
                        "evidence_level": 2,
                        "trait_domain": "extraversion",
                        "coverage_note": None,
                    },
                    {
                        "rsid": "rs4680",
                        "gene": "COMT",
                        "variant_name": "Val158Met",
                        "genotype": "AG",
                        "category": "Moderate",
                        "effect_summary": "Heterozygous — intermediate catecholamine clearance.",
                        "evidence_level": 2,
                        "trait_domain": "neuroticism",
                        "coverage_note": None,
                    },
                    {
                        "rsid": "rs1800955",
                        "gene": "DRD4",
                        "variant_name": "DRD4 -521C/T",
                        "genotype": "CT",
                        "category": "Standard",
                        "effect_summary": "One copy; associated with novelty seeking.",
                        "evidence_level": 1,
                        "trait_domain": "openness",
                        "coverage_note": None,
                    },
                ],
            }
        ),
    },
    {
        "module": "traits",
        "category": "pathway_summary",
        "evidence_level": 2,
        "gene_symbol": None,
        "rsid": None,
        "finding_text": "Cognitive Traits — Standard consideration",
        "pathway": "Cognitive Traits",
        "pathway_level": "Standard",
        "pmid_citations": json.dumps(["35361970"]),
        "detail_json": json.dumps(
            {
                "pathway_id": "cognitive_traits",
                "prs_primary": True,
                "called_snps": 2,
                "total_snps": 4,
                "missing_snps": ["rs9320913", "rs11584700"],
                "snp_details": [],
            }
        ),
    },
    {
        "module": "traits",
        "category": "pathway_summary",
        "evidence_level": 1,
        "gene_symbol": None,
        "rsid": None,
        "finding_text": "Behavioral Traits — Standard consideration",
        "pathway": "Behavioral Traits",
        "pathway_level": "Standard",
        "pmid_citations": json.dumps([]),
        "detail_json": json.dumps(
            {
                "pathway_id": "behavioral_traits",
                "prs_primary": False,
                "called_snps": 1,
                "total_snps": 3,
                "missing_snps": ["rs7127507", "rs747302"],
                "snp_details": [],
            }
        ),
    },
]

PRS_FINDINGS = [
    {
        "module": "traits",
        "category": "prs",
        "evidence_level": 2,
        "gene_symbol": None,
        "rsid": None,
        "finding_text": "Educational Attainment PRS — 62nd percentile",
        "pathway": "Cognitive Traits",
        "pathway_level": None,
        "prs_percentile": 62.3,
        "pmid_citations": json.dumps(["35361970"]),
        "detail_json": json.dumps(
            {
                "trait": "educational_attainment",
                "name": "Educational Attainment",
                "z_score": 0.31,
                "bootstrap_ci_lower": 48.1,
                "bootstrap_ci_upper": 74.5,
                "source_ancestry": "EUR",
                "source_study": "Okbay et al. 2022",
                "snps_used": 180,
                "snps_total": 210,
                "coverage_fraction": 0.857,
                "ancestry_mismatch": False,
                "ancestry_warning_text": None,
                "is_sufficient": True,
                "calibrated": True,
                "research_use_only": True,
            }
        ),
    },
    {
        "module": "traits",
        "category": "prs",
        "evidence_level": 1,
        "gene_symbol": None,
        "rsid": None,
        "finding_text": "Cognitive Ability PRS — Insufficient coverage",
        "pathway": "Cognitive Traits",
        "pathway_level": None,
        "prs_percentile": None,
        "pmid_citations": json.dumps([]),
        "detail_json": json.dumps(
            {
                "trait": "cognitive_ability",
                "name": "Cognitive Ability",
                "percentile": None,
                "z_score": None,
                "bootstrap_ci_lower": None,
                "bootstrap_ci_upper": None,
                "source_ancestry": "EUR",
                "source_study": "Savage et al. 2018",
                "snps_used": 50,
                "snps_total": 200,
                "coverage_fraction": 0.25,
                "ancestry_mismatch": True,
                "ancestry_warning_text": "PRS derived from EUR cohort; may be less predictive.",
                "is_sufficient": False,
                "calibrated": False,
                "research_use_only": True,
            }
        ),
    },
]

SNP_FINDING = {
    "module": "traits",
    "category": "snp_finding",
    "evidence_level": 2,
    "gene_symbol": "CTNNA2",
    "rsid": "rs2164273",
    "finding_text": "CTNNA2 (rs2164273, AG) — Associated with increased extraversion.",
    "pathway": "Personality Big Five",
    "pathway_level": "Moderate",
    "pmid_citations": json.dumps(["29942085"]),
    "detail_json": json.dumps(
        {
            "variant_name": "CTNNA2 intergenic",
            "genotype": "AG",
            "trait_domain": "extraversion",
            "recommendation": None,
            "cross_module": None,
        }
    ),
}

CROSS_MODULE_FINDING = {
    "module": "traits",
    "category": "cross_module",
    "evidence_level": 2,
    "gene_symbol": "COMT",
    "rsid": "rs4680",
    "finding_text": "COMT Val158Met — see Sleep module for chronotype impact.",
    "pathway": None,
    "pathway_level": None,
    "pmid_citations": json.dumps(["29942085"]),
    "detail_json": json.dumps(
        {
            "trait_domain": "neuroticism",
            "to_module": "sleep",
            "link_type": "cross_reference",
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

    # Reference DB
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

    with ExitStack() as stack:
        stack.enter_context(patch("backend.api.routes.traits.get_registry", return_value=registry))
        stack.enter_context(patch("backend.api.dependencies.get_registry", return_value=registry))
        stack.enter_context(
            patch("backend.services.staleness.get_registry", return_value=registry)
        )
        yield sample_engine, ref_engine

    reset_registry()


@pytest.fixture()
def client(_env: tuple[sa.Engine, sa.Engine]) -> TestClient:
    """Create a test client for the traits API."""
    from fastapi import FastAPI

    from backend.api.routes.traits import router

    app = FastAPI()
    app.include_router(router, prefix="/api")
    return TestClient(app)


@pytest.fixture()
def seeded_client(
    _env: tuple[sa.Engine, sa.Engine],
) -> TestClient:
    """Create a test client with pre-seeded traits findings."""
    sample_engine, _ = _env

    all_findings = (
        PATHWAY_SUMMARY_FINDINGS
        + PRS_FINDINGS
        + [
            SNP_FINDING,
            CROSS_MODULE_FINDING,
        ]
    )
    with sample_engine.begin() as conn:
        for finding in all_findings:
            conn.execute(sa.insert(findings), finding)

    from fastapi import FastAPI

    from backend.api.routes.traits import router

    app = FastAPI()
    app.include_router(router, prefix="/api")
    return TestClient(app)


# ── Endpoint tests ───────────────────────────────────────────────────


class TestListPathways:
    def test_returns_pathways(self, seeded_client: TestClient) -> None:
        resp = seeded_client.get("/api/analysis/traits/pathways?sample_id=1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 3
        assert len(data["items"]) == 3

    def test_pathway_fields(self, seeded_client: TestClient) -> None:
        resp = seeded_client.get("/api/analysis/traits/pathways?sample_id=1")
        data = resp.json()
        item = next(i for i in data["items"] if i["pathway_id"] == "personality_big_five")
        assert item["level"] == "Moderate"
        assert item["evidence_level"] == 2
        assert item["called_snps"] == 3
        assert item["total_snps"] == 5
        assert item["prs_primary"] is False

    def test_prs_primary_flag(self, seeded_client: TestClient) -> None:
        resp = seeded_client.get("/api/analysis/traits/pathways?sample_id=1")
        data = resp.json()
        cog = next(i for i in data["items"] if i["pathway_id"] == "cognitive_traits")
        assert cog["prs_primary"] is True

    def test_cross_module_in_response(self, seeded_client: TestClient) -> None:
        resp = seeded_client.get("/api/analysis/traits/pathways?sample_id=1")
        data = resp.json()
        assert len(data["cross_module"]) >= 1
        cross = data["cross_module"][0]
        assert cross["to_module"] == "sleep"

    def test_module_disclaimer_present(self, seeded_client: TestClient) -> None:
        resp = seeded_client.get("/api/analysis/traits/pathways?sample_id=1")
        data = resp.json()
        assert data["module_disclaimer"] != ""

    def test_empty_findings_returns_empty(self, client: TestClient) -> None:
        resp = client.get("/api/analysis/traits/pathways?sample_id=1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0
        assert data["items"] == []

    def test_missing_sample_404(self, client: TestClient) -> None:
        resp = client.get("/api/analysis/traits/pathways?sample_id=999")
        assert resp.status_code == 404


class TestPathwayDetail:
    def test_pathway_detail(self, seeded_client: TestClient) -> None:
        resp = seeded_client.get("/api/analysis/traits/pathway/personality_big_five?sample_id=1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["pathway_id"] == "personality_big_five"
        assert data["level"] == "Moderate"
        assert len(data["snp_details"]) == 3

    def test_snp_detail_fields(self, seeded_client: TestClient) -> None:
        resp = seeded_client.get("/api/analysis/traits/pathway/personality_big_five?sample_id=1")
        data = resp.json()
        snps = data["snp_details"]
        extra = next(s for s in snps if s["rsid"] == "rs2164273")
        assert extra["gene"] == "CTNNA2"
        assert extra["trait_domain"] == "extraversion"
        assert extra["category"] == "Moderate"

    def test_missing_pathway_404(self, seeded_client: TestClient) -> None:
        resp = seeded_client.get("/api/analysis/traits/pathway/nonexistent?sample_id=1")
        assert resp.status_code == 404


class TestPRS:
    def test_prs_returns_items(self, seeded_client: TestClient) -> None:
        resp = seeded_client.get("/api/analysis/traits/prs?sample_id=1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 2
        assert len(data["items"]) == 2

    def test_prs_research_use_only(self, seeded_client: TestClient) -> None:
        # Surface check: the endpoint must never emit a non-RUO PRS item. The
        # invariant is *enforced* at the producer (store_prs_findings always
        # writes research_use_only=True) and tested there — see
        # test_prs.py::test_detail_json_has_ancestry_source_tag. Guard the loop
        # against an empty items list so it can't pass vacuously.
        resp = seeded_client.get("/api/analysis/traits/prs?sample_id=1")
        data = resp.json()
        assert data["items"], "no PRS items returned — assertion would be vacuous"
        for item in data["items"]:
            assert item["research_use_only"] is True

    def test_prs_sufficient_item(self, seeded_client: TestClient) -> None:
        resp = seeded_client.get("/api/analysis/traits/prs?sample_id=1")
        data = resp.json()
        ea = next(i for i in data["items"] if i["trait"] == "educational_attainment")
        assert ea["is_sufficient"] is True
        assert ea["percentile"] == pytest.approx(62.3)
        assert ea["bootstrap_ci_lower"] == pytest.approx(48.1)
        assert ea["bootstrap_ci_upper"] == pytest.approx(74.5)
        assert ea["ancestry_mismatch"] is False
        assert ea["calibrated"] is True

    def test_prs_insufficient_item(self, seeded_client: TestClient) -> None:
        resp = seeded_client.get("/api/analysis/traits/prs?sample_id=1")
        data = resp.json()
        ca = next(i for i in data["items"] if i["trait"] == "cognitive_ability")
        assert ca["is_sufficient"] is False
        assert ca["ancestry_mismatch"] is True
        assert ca["ancestry_warning_text"] is not None
        assert ca["calibrated"] is False

    def test_prs_percentile_does_not_fall_back_to_z_score(self, seeded_client: TestClient) -> None:
        resp = seeded_client.get("/api/analysis/traits/prs?sample_id=1")
        data = resp.json()
        ea = next(i for i in data["items"] if i["trait"] == "educational_attainment")
        assert ea["z_score"] == pytest.approx(0.31)
        assert ea["percentile"] == pytest.approx(62.3)

    def test_prs_evidence_cap(self, seeded_client: TestClient) -> None:
        """All PRS evidence levels must be <= 2 (★★☆☆ cap).

        Surface check over a producer-enforced invariant: PRS findings are written
        with evidence_level = PRS_EVIDENCE_LEVEL (=1), which is verified on a
        computed-then-stored finding in test_prs.py::test_findings_have_prs_category.
        Guarded against an empty items list so the loop can't pass vacuously.
        """
        resp = seeded_client.get("/api/analysis/traits/prs?sample_id=1")
        data = resp.json()
        assert data["items"], "no PRS items returned — assertion would be vacuous"
        for item in data["items"]:
            assert item["evidence_level"] <= 2

    def test_empty_prs(self, client: TestClient) -> None:
        resp = client.get("/api/analysis/traits/prs?sample_id=1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0


class TestDisclaimer:
    def test_disclaimer(self, client: TestClient) -> None:
        resp = client.get("/api/analysis/traits/disclaimer")
        assert resp.status_code == 200
        data = resp.json()
        assert data["disclaimer"] != ""
        assert data["evidence_cap"] == 2
        assert data["research_use_only"] is True

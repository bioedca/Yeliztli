"""Tests for nutrigenomics findings API (P3-09).

Covers:
  - GET /api/analysis/nutrigenomics/pathways?sample_id=N — All pathway results
  - GET /api/analysis/nutrigenomics/pathway/{id}?sample_id=N — Single pathway detail
  - POST /api/analysis/nutrigenomics/run?sample_id=N — Run scoring
  - Missing sample returns 404
  - Empty findings returns empty list
  - Pathway detail includes SNP breakdown
  - Evidence gating reflected in results
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

PATHWAY_SUMMARY_FINDINGS = [
    {
        "module": "nutrigenomics",
        "category": "pathway_summary",
        "evidence_level": 3,
        "gene_symbol": None,
        "rsid": None,
        "finding_text": "Folate Metabolism — Elevated consideration",
        "pathway": "Folate Metabolism",
        "pathway_level": "Elevated",
        "pmid_citations": json.dumps(["23824729", "22170379"]),
        "detail_json": json.dumps(
            {
                "pathway_id": "folate_metabolism",
                "called_snps": 2,
                "total_snps": 3,
                "missing_snps": ["rs1801131"],
                "snp_details": [
                    {
                        "rsid": "rs1801133",
                        "gene": "MTHFR",
                        "variant_name": "C677T",
                        "genotype": "AA",
                        "category": "Elevated",
                        "effect_summary": (
                            "Significantly reduced MTHFR enzyme activity (~30% of normal)."
                        ),
                        "evidence_level": 3,
                    },
                    {
                        "rsid": "rs1801131",
                        "gene": "MTHFR",
                        "variant_name": "A1298C",
                        "genotype": "AC",
                        "category": "Moderate",
                        "effect_summary": "Mildly reduced MTHFR enzyme activity.",
                        "evidence_level": 2,
                    },
                ],
            }
        ),
    },
    {
        "module": "nutrigenomics",
        "category": "pathway_summary",
        "evidence_level": 2,
        "gene_symbol": None,
        "rsid": None,
        "finding_text": "Vitamin D — Standard (no variants of concern)",
        "pathway": "Vitamin D",
        "pathway_level": "Standard",
        "pmid_citations": json.dumps([]),
        "detail_json": json.dumps(
            {
                "pathway_id": "vitamin_d",
                "called_snps": 0,
                "total_snps": 2,
                "missing_snps": ["rs2282679", "rs12785878"],
                "snp_details": [],
            }
        ),
    },
    {
        "module": "nutrigenomics",
        "category": "pathway_summary",
        "evidence_level": 3,
        "gene_symbol": None,
        "rsid": None,
        "finding_text": "Lactose Tolerance — Elevated consideration",
        "pathway": "Lactose Tolerance",
        "pathway_level": "Elevated",
        "pmid_citations": json.dumps(["12576863"]),
        "detail_json": json.dumps(
            {
                "pathway_id": "lactose",
                "called_snps": 1,
                "total_snps": 1,
                "missing_snps": [],
                "snp_details": [
                    {
                        "rsid": "rs4988235",
                        "gene": "LCT",
                        "variant_name": "MCM6 -13910C>T",
                        "genotype": "GG",
                        "category": "Elevated",
                        "effect_summary": (
                            "Lactase non-persistent genotype; likely lactose intolerant."
                        ),
                        "evidence_level": 3,
                    },
                ],
            }
        ),
    },
]

SNP_FINDINGS = [
    {
        "module": "nutrigenomics",
        "category": "snp_finding",
        "evidence_level": 3,
        "gene_symbol": "MTHFR",
        "rsid": "rs1801133",
        "finding_text": (
            "MTHFR C677T (AA) — Significantly reduced MTHFR enzyme activity (~30% of normal)."
        ),
        "pathway": "Folate Metabolism",
        "pathway_level": "Elevated",
        "pmid_citations": json.dumps(["23824729"]),
        "detail_json": json.dumps(
            {
                "variant_name": "C677T",
                "genotype": "AA",
                "recommendation": (
                    "Consider dietary folate sources and discuss "
                    "methylfolate supplementation with a clinician."
                ),
            }
        ),
    },
    {
        "module": "nutrigenomics",
        "category": "snp_finding",
        "evidence_level": 3,
        "gene_symbol": "LCT",
        "rsid": "rs4988235",
        "finding_text": (
            "LCT MCM6 -13910C>T (GG) — Lactase non-persistent genotype; likely lactose intolerant."
        ),
        "pathway": "Lactose Tolerance",
        "pathway_level": "Elevated",
        "pmid_citations": json.dumps(["12576863"]),
        "detail_json": json.dumps(
            {
                "variant_name": "MCM6 -13910C>T",
                "genotype": "GG",
                "recommendation": (
                    "May benefit from reduced dairy intake or lactase enzyme supplementation."
                ),
            }
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
    sample_findings: list[dict] | None = None,
    seed_variants: list[dict] | None = None,
) -> Generator[tuple[TestClient, int], None, None]:
    """Create TestClient with optional nutrigenomics findings."""
    settings = Settings(data_dir=tmp_data_dir, wal_mode=False)

    ref_engine = sa.create_engine(f"sqlite:///{settings.reference_db_path}")
    reference_metadata.create_all(ref_engine)
    with ref_engine.begin() as conn:
        result = conn.execute(
            samples.insert().values(
                name="test_nutri",
                db_path="samples/sample_1.db",
                file_format="23andme_v5",
                file_hash="hash_nutri",
            )
        )
        sample_id = result.lastrowid
    ref_engine.dispose()

    sample_db_path = tmp_data_dir / "samples" / "sample_1.db"
    sample_engine = sa.create_engine(f"sqlite:///{sample_db_path}")
    create_sample_tables(sample_engine)
    if sample_findings:
        with sample_engine.begin() as conn:
            conn.execute(findings.insert(), sample_findings)
    if seed_variants:
        with sample_engine.begin() as conn:
            conn.execute(raw_variants.insert(), seed_variants)
    sample_engine.dispose()

    with (
        patch("backend.main.get_settings", return_value=settings),
        patch("backend.db.connection.get_settings", return_value=settings),
        patch("backend.api.routes.nutrigenomics.get_registry") as mock_reg,
        patch("backend.api.routes.pharma.get_registry") as mock_reg2,
        patch("backend.api.routes.variant_detail.get_registry") as mock_reg3,
        patch("backend.api.routes.annotations_api.get_registry") as mock_reg4,
        patch("backend.api.routes.variants.get_registry") as mock_reg5,
        patch("backend.api.routes.ingest.get_registry") as mock_reg6,
        patch("backend.api.routes.samples.get_registry") as mock_reg7,
    ):
        reset_registry()
        registry = DBRegistry(settings)
        for m in [mock_reg, mock_reg2, mock_reg3, mock_reg4, mock_reg5, mock_reg6, mock_reg7]:
            m.return_value = registry

        from backend.main import create_app

        app = create_app()
        with TestClient(app) as tc:
            yield tc, sample_id

        registry.dispose_all()
        reset_registry()


@pytest.fixture
def client(tmp_data_dir: Path) -> Generator[tuple[TestClient, int], None, None]:
    """Client with nutrigenomics findings pre-loaded."""
    all_findings = PATHWAY_SUMMARY_FINDINGS + SNP_FINDINGS
    yield from _setup_client(tmp_data_dir, all_findings)


@pytest.fixture
def client_no_findings(tmp_data_dir: Path) -> Generator[tuple[TestClient, int], None, None]:
    """Client with no nutrigenomics findings."""
    yield from _setup_client(tmp_data_dir)


@pytest.fixture
def client_with_variants(tmp_data_dir: Path) -> Generator[tuple[TestClient, int], None, None]:
    """Client with raw variants for run endpoint testing."""
    variants = [
        {"rsid": "rs1801133", "chrom": "1", "pos": 11856378, "genotype": "AA"},
        {"rsid": "rs4988235", "chrom": "2", "pos": 135851076, "genotype": "GG"},
    ]
    yield from _setup_client(tmp_data_dir, seed_variants=variants)


# ═══════════════════════════════════════════════════════════════════════
# GET /api/analysis/nutrigenomics/pathways — List pathways
# ═══════════════════════════════════════════════════════════════════════


class TestListPathways:
    def test_returns_pathway_summaries(self, client: tuple[TestClient, int]) -> None:
        tc, sample_id = client
        resp = tc.get(f"/api/analysis/nutrigenomics/pathways?sample_id={sample_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 3
        pathway_ids = [item["pathway_id"] for item in data["items"]]
        assert "folate_metabolism" in pathway_ids
        assert "vitamin_d" in pathway_ids
        assert "lactose" in pathway_ids

    def test_folate_elevated(self, client: tuple[TestClient, int]) -> None:
        tc, sample_id = client
        resp = tc.get(f"/api/analysis/nutrigenomics/pathways?sample_id={sample_id}")
        data = resp.json()
        folate = next(i for i in data["items"] if i["pathway_id"] == "folate_metabolism")
        assert folate["level"] == "Elevated"
        assert folate["evidence_level"] == 3
        assert folate["called_snps"] == 2
        assert folate["total_snps"] == 3
        assert "rs1801131" in folate["missing_snps"]
        assert "23824729" in folate["pmids"]

    def test_vitamin_d_standard(self, client: tuple[TestClient, int]) -> None:
        tc, sample_id = client
        resp = tc.get(f"/api/analysis/nutrigenomics/pathways?sample_id={sample_id}")
        data = resp.json()
        vit_d = next(i for i in data["items"] if i["pathway_id"] == "vitamin_d")
        assert vit_d["level"] == "Standard"
        assert vit_d["called_snps"] == 0

    def test_lactose_elevated(self, client: tuple[TestClient, int]) -> None:
        tc, sample_id = client
        resp = tc.get(f"/api/analysis/nutrigenomics/pathways?sample_id={sample_id}")
        data = resp.json()
        lactose = next(i for i in data["items"] if i["pathway_id"] == "lactose")
        assert lactose["level"] == "Elevated"
        assert lactose["called_snps"] == 1

    def test_empty_when_no_findings(self, client_no_findings: tuple[TestClient, int]) -> None:
        tc, sample_id = client_no_findings
        resp = tc.get(f"/api/analysis/nutrigenomics/pathways?sample_id={sample_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0
        assert data["items"] == []

    def test_unknown_sample_404(self, client: tuple[TestClient, int]) -> None:
        tc, _ = client
        resp = tc.get("/api/analysis/nutrigenomics/pathways?sample_id=9999")
        assert resp.status_code == 404

    def test_missing_sample_id(self, client: tuple[TestClient, int]) -> None:
        tc, _ = client
        resp = tc.get("/api/analysis/nutrigenomics/pathways")
        assert resp.status_code == 422


# ═══════════════════════════════════════════════════════════════════════
# GET /api/analysis/nutrigenomics/pathway/{id} — Pathway detail
# ═══════════════════════════════════════════════════════════════════════


class TestPathwayDetail:
    def test_folate_detail(self, client: tuple[TestClient, int]) -> None:
        tc, sample_id = client
        resp = tc.get(
            f"/api/analysis/nutrigenomics/pathway/folate_metabolism?sample_id={sample_id}"
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["pathway_id"] == "folate_metabolism"
        assert data["pathway_name"] == "Folate Metabolism"
        assert data["level"] == "Elevated"
        assert data["evidence_level"] == 3
        assert len(data["snp_details"]) == 2

    def test_snp_detail_includes_mthfr(self, client: tuple[TestClient, int]) -> None:
        tc, sample_id = client
        resp = tc.get(
            f"/api/analysis/nutrigenomics/pathway/folate_metabolism?sample_id={sample_id}"
        )
        data = resp.json()
        mthfr = next(s for s in data["snp_details"] if s["rsid"] == "rs1801133")
        assert mthfr["gene"] == "MTHFR"
        assert mthfr["variant_name"] == "C677T"
        assert mthfr["genotype"] == "AA"
        assert mthfr["category"] == "Elevated"
        assert "reduced" in mthfr["effect_summary"].lower()
        assert mthfr["evidence_level"] == 3

    def test_snp_detail_includes_recommendation(self, client: tuple[TestClient, int]) -> None:
        """SNP finding recommendation is included in pathway detail."""
        tc, sample_id = client
        resp = tc.get(
            f"/api/analysis/nutrigenomics/pathway/folate_metabolism?sample_id={sample_id}"
        )
        data = resp.json()
        mthfr = next(s for s in data["snp_details"] if s["rsid"] == "rs1801133")
        assert mthfr["recommendation"] is not None
        assert "folate" in mthfr["recommendation"].lower()

    def test_lactose_detail(self, client: tuple[TestClient, int]) -> None:
        tc, sample_id = client
        resp = tc.get(f"/api/analysis/nutrigenomics/pathway/lactose?sample_id={sample_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["pathway_id"] == "lactose"
        assert data["level"] == "Elevated"
        assert len(data["snp_details"]) == 1
        lct = data["snp_details"][0]
        assert lct["rsid"] == "rs4988235"
        assert lct["gene"] == "LCT"

    def test_unknown_pathway_404(self, client: tuple[TestClient, int]) -> None:
        tc, sample_id = client
        resp = tc.get(f"/api/analysis/nutrigenomics/pathway/nonexistent?sample_id={sample_id}")
        assert resp.status_code == 404

    def test_unknown_sample_404(self, client: tuple[TestClient, int]) -> None:
        tc, _ = client
        resp = tc.get("/api/analysis/nutrigenomics/pathway/folate_metabolism?sample_id=9999")
        assert resp.status_code == 404

    def test_missing_sample_id(self, client: tuple[TestClient, int]) -> None:
        tc, _ = client
        resp = tc.get("/api/analysis/nutrigenomics/pathway/folate_metabolism")
        assert resp.status_code == 422

    def test_missing_snps_in_detail(self, client: tuple[TestClient, int]) -> None:
        tc, sample_id = client
        resp = tc.get(
            f"/api/analysis/nutrigenomics/pathway/folate_metabolism?sample_id={sample_id}"
        )
        data = resp.json()
        assert "rs1801131" in data["missing_snps"]

    def test_pmids_in_detail(self, client: tuple[TestClient, int]) -> None:
        tc, sample_id = client
        resp = tc.get(
            f"/api/analysis/nutrigenomics/pathway/folate_metabolism?sample_id={sample_id}"
        )
        data = resp.json()
        assert "23824729" in data["pmids"]


# ═══════════════════════════════════════════════════════════════════════
# POST /api/analysis/nutrigenomics/run — Run scoring
# ═══════════════════════════════════════════════════════════════════════


class TestRunScoring:
    def test_run_produces_findings(self, client_with_variants: tuple[TestClient, int]) -> None:
        tc, sample_id = client_with_variants
        resp = tc.post(f"/api/analysis/nutrigenomics/run?sample_id={sample_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["findings_count"] > 0
        assert data["pathways_scored"] == 7  # Always 7 pathways

    def test_run_then_list(self, client_with_variants: tuple[TestClient, int]) -> None:
        """After running, pathways endpoint returns scored results."""
        tc, sample_id = client_with_variants
        # Run scoring
        resp = tc.post(f"/api/analysis/nutrigenomics/run?sample_id={sample_id}")
        assert resp.status_code == 200

        # List pathways
        resp = tc.get(f"/api/analysis/nutrigenomics/pathways?sample_id={sample_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 7  # 7 pathways

        # Check folate pathway is Elevated (MTHFR AA)
        folate = next(
            (i for i in data["items"] if i["pathway_id"] == "folate_metabolism"),
            None,
        )
        assert folate is not None
        assert folate["level"] == "Elevated"

    def test_run_unknown_sample_404(self, client_with_variants: tuple[TestClient, int]) -> None:
        tc, _ = client_with_variants
        resp = tc.post("/api/analysis/nutrigenomics/run?sample_id=9999")
        assert resp.status_code == 404

    def test_run_idempotent(self, client_with_variants: tuple[TestClient, int]) -> None:
        """Running scoring twice produces the same result (no duplicates)."""
        tc, sample_id = client_with_variants
        resp1 = tc.post(f"/api/analysis/nutrigenomics/run?sample_id={sample_id}")
        resp2 = tc.post(f"/api/analysis/nutrigenomics/run?sample_id={sample_id}")
        assert resp1.json()["findings_count"] == resp2.json()["findings_count"]

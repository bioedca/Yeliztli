"""Tests for Gene Skin findings API (P3-56).

Covers:
  - GET /api/analysis/skin/pathways?sample_id=N — All pathway results
  - GET /api/analysis/skin/pathway/{id}?sample_id=N — Single pathway detail
  - POST /api/analysis/skin/run?sample_id=N — Run scoring
  - MC1R aggregate summary in pathways response
  - FLG insufficient data caveat in pathways response
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

PATHWAY_SUMMARY_FINDINGS = [
    {
        "module": "skin",
        "category": "pathway_summary",
        "evidence_level": 3,
        "gene_symbol": None,
        "rsid": None,
        "finding_text": "Pigmentation & UV Response — Elevated consideration",
        "pathway": "Pigmentation & UV Response",
        "pathway_level": "Elevated",
        "pmid_citations": json.dumps(["18488028", "20010810"]),
        "detail_json": json.dumps(
            {
                "pathway_id": "pigmentation_uv",
                "called_snps": 3,
                "total_snps": 4,
                "missing_snps": ["rs885479"],
                "snp_details": [
                    {
                        "rsid": "rs1805007",
                        "gene": "MC1R",
                        "variant_name": "R151C",
                        "genotype": "CT",
                        "category": "Elevated",
                        "effect_summary": (
                            "MC1R R allele — associated with fair skin and UV sensitivity."
                        ),
                        "evidence_level": 3,
                        "mc1r_allele_class": "R",
                        "coverage_note": None,
                        "insufficient_data_flag": False,
                    },
                    {
                        "rsid": "rs1805008",
                        "gene": "MC1R",
                        "variant_name": "R160W",
                        "genotype": "CC",
                        "category": "Standard",
                        "effect_summary": (
                            "Wild-type — no increased UV sensitivity from this variant."
                        ),
                        "evidence_level": 3,
                        "mc1r_allele_class": None,
                        "coverage_note": None,
                        "insufficient_data_flag": False,
                    },
                    {
                        "rsid": "rs1805009",
                        "gene": "MC1R",
                        "variant_name": "D294H",
                        "genotype": "GG",
                        "category": "Standard",
                        "effect_summary": (
                            "Wild-type — no increased UV sensitivity from this variant."
                        ),
                        "evidence_level": 3,
                        "mc1r_allele_class": None,
                        "coverage_note": None,
                        "insufficient_data_flag": False,
                    },
                ],
            }
        ),
    },
    {
        "module": "skin",
        "category": "pathway_summary",
        "evidence_level": 2,
        "gene_symbol": None,
        "rsid": None,
        "finding_text": "Skin Barrier & Inflammation — Standard (no variants of concern)",
        "pathway": "Skin Barrier & Inflammation",
        "pathway_level": "Standard",
        "pmid_citations": json.dumps([]),
        "detail_json": json.dumps(
            {
                "pathway_id": "skin_barrier_inflammation",
                "called_snps": 1,
                "total_snps": 1,
                "missing_snps": [],
                "snp_details": [
                    {
                        "rsid": "rs61816761",
                        "gene": "FLG",
                        "variant_name": "R501X",
                        "genotype": "CC",
                        "category": "Standard",
                        "effect_summary": (
                            "No R501X variant detected at rs61816761; other FLG LoF "
                            "variants are not excluded."
                        ),
                        "evidence_level": 2,
                        "mc1r_allele_class": None,
                        "coverage_note": (
                            "Single-site FLG R501X result; 2282del4 and other FLG LoF "
                            "variants are not assessed."
                        ),
                        "insufficient_data_flag": True,
                    },
                ],
            }
        ),
    },
    {
        "module": "skin",
        "category": "pathway_summary",
        "evidence_level": 2,
        "gene_symbol": None,
        "rsid": None,
        "finding_text": "Oxidative Stress & Aging — Moderate consideration",
        "pathway": "Oxidative Stress & Aging",
        "pathway_level": "Moderate",
        "pmid_citations": json.dumps(["11309423"]),
        "detail_json": json.dumps(
            {
                "pathway_id": "oxidative_stress_aging",
                "called_snps": 2,
                "total_snps": 3,
                "missing_snps": ["rs1799750"],
                "snp_details": [
                    {
                        "rsid": "rs1695",
                        "gene": "GSTP1",
                        "variant_name": "I105V",
                        "genotype": "AG",
                        "category": "Moderate",
                        "effect_summary": "Reduced glutathione S-transferase activity.",
                        "evidence_level": 2,
                        "mc1r_allele_class": None,
                        "coverage_note": None,
                        "insufficient_data_flag": False,
                    },
                    {
                        "rsid": "rs4880",
                        "gene": "SOD2",
                        "variant_name": "V16A",
                        "genotype": "TT",
                        "category": "Standard",
                        "effect_summary": "Standard SOD2 activity.",
                        "evidence_level": 2,
                        "mc1r_allele_class": None,
                        "coverage_note": None,
                        "insufficient_data_flag": False,
                    },
                ],
            }
        ),
    },
    {
        "module": "skin",
        "category": "pathway_summary",
        "evidence_level": 2,
        "gene_symbol": None,
        "rsid": None,
        "finding_text": "Skin Micronutrients — Standard (no variants of concern)",
        "pathway": "Skin Micronutrients",
        "pathway_level": "Standard",
        "pmid_citations": json.dumps([]),
        "detail_json": json.dumps(
            {
                "pathway_id": "skin_micronutrients",
                "called_snps": 0,
                "total_snps": 2,
                "missing_snps": ["rs2228570", "rs1544410"],
                "snp_details": [],
            }
        ),
    },
]

SNP_FINDINGS = [
    {
        "module": "skin",
        "category": "snp_finding",
        "evidence_level": 3,
        "gene_symbol": "MC1R",
        "rsid": "rs1805007",
        "finding_text": (
            "MC1R R151C (CT) — MC1R R allele — associated with fair skin and UV sensitivity."
        ),
        "pathway": "Pigmentation & UV Response",
        "pathway_level": "Elevated",
        "pmid_citations": json.dumps(["18488028"]),
        "detail_json": json.dumps(
            {
                "variant_name": "R151C",
                "genotype": "CT",
                "recommendation": (
                    "Consider enhanced UV protection and regular dermatological screenings."
                ),
                "mc1r_allele_class": "R",
            }
        ),
    },
    {
        "module": "skin",
        "category": "snp_finding",
        "evidence_level": 2,
        "gene_symbol": "GSTP1",
        "rsid": "rs1695",
        "finding_text": "GSTP1 I105V (AG) — Reduced glutathione S-transferase activity.",
        "pathway": "Oxidative Stress & Aging",
        "pathway_level": "Moderate",
        "pmid_citations": json.dumps(["11309423"]),
        "detail_json": json.dumps(
            {
                "variant_name": "I105V",
                "genotype": "AG",
                "recommendation": (
                    "Consider antioxidant-rich diet and topical antioxidant products."
                ),
            }
        ),
    },
]

MC1R_AGGREGATE_FINDING = {
    "module": "skin",
    "category": "mc1r_aggregate",
    "evidence_level": 3,
    "gene_symbol": "MC1R",
    "rsid": None,
    "finding_text": (
        "MC1R multi-allele summary: Moderate UV Sensitivity "
        "(1 R allele across 3 MC1R variants called). "
        "Carrier of one strong MC1R variant; moderately increased UV sensitivity."
    ),
    "pathway": "Pigmentation & UV Response",
    "pathway_level": None,
    "pmid_citations": json.dumps(["18488028", "20010810"]),
    "detail_json": json.dumps(
        {
            "r_allele_count": 1,
            "r_allele_rsids": ["rs1805007"],
            "total_mc1r_called": 3,
            "risk_label": "Moderate UV Sensitivity",
            "risk_description": (
                "Carrier of one strong MC1R variant; moderately increased UV sensitivity."
            ),
        }
    ),
}

FLG_INSUFFICIENT_DATA_FINDING = {
    "module": "skin",
    "category": "insufficient_data",
    "evidence_level": 2,
    "gene_symbol": "FLG",
    "rsid": "rs61816761",
    "finding_text": (
        "FLG R501X - Insufficient Data. "
        "Result is based on the single assayed rs61816761 nonsense variant. "
        "A negative result does not rule out other FLG loss-of-function alleles."
    ),
    "pathway": "Skin Barrier & Inflammation",
    "pathway_level": None,
    "pmid_citations": json.dumps(["16550169", "16444271"]),
    "detail_json": json.dumps(
        {
            "observed_variant": "FLG R501X (p.Arg501Ter)",
            "proxy_target": None,
            "insufficient_data_reason": (
                "Single FLG loss-of-function site; does not capture 2282del4 "
                "or other FLG null mutations."
            ),
        }
    ),
}

CROSS_MODULE_FINDINGS = [
    {
        "module": "skin",
        "category": "cross_module",
        "evidence_level": 3,
        "gene_symbol": "MC1R",
        "rsid": "rs1805007",
        "finding_text": (
            "MC1R R151C — also relevant to melanoma risk assessment. "
            "See Cancer module for full evaluation."
        ),
        "pathway": None,
        "pathway_level": None,
        "pmid_citations": json.dumps(["18488028"]),
        "detail_json": json.dumps(
            {
                "source_module": "skin",
                "target_module": "cancer",
                "cross_link_reason": (
                    "MC1R loss-of-function variants associated with melanoma risk."
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
    """Create TestClient with optional skin findings."""
    settings = Settings(data_dir=tmp_data_dir, wal_mode=False)

    ref_engine = sa.create_engine(f"sqlite:///{settings.reference_db_path}")
    reference_metadata.create_all(ref_engine)
    with ref_engine.begin() as conn:
        result = conn.execute(
            samples.insert().values(
                name="test_skin",
                db_path="samples/sample_1.db",
                file_format="23andme_v5",
                file_hash="hash_skin",
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
        patch("backend.api.routes.skin.get_registry") as mock_reg,
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
    """Client with skin findings pre-loaded (all categories)."""
    all_findings = (
        PATHWAY_SUMMARY_FINDINGS
        + SNP_FINDINGS
        + [MC1R_AGGREGATE_FINDING]
        + [FLG_INSUFFICIENT_DATA_FINDING]
        + CROSS_MODULE_FINDINGS
    )
    yield from _setup_client(tmp_data_dir, all_findings)


@pytest.fixture
def client_no_findings(tmp_data_dir: Path) -> Generator[tuple[TestClient, int], None, None]:
    """Client with no skin findings."""
    yield from _setup_client(tmp_data_dir)


@pytest.fixture
def client_with_variants(tmp_data_dir: Path) -> Generator[tuple[TestClient, int], None, None]:
    """Client with raw variants for run endpoint testing."""
    variants = [
        {"rsid": "rs1805007", "chrom": "16", "pos": 89919709, "genotype": "CT"},
        {"rsid": "rs1805008", "chrom": "16", "pos": 89919736, "genotype": "CC"},
        {"rsid": "rs1805009", "chrom": "16", "pos": 89919746, "genotype": "GG"},
        {"rsid": "rs1695", "chrom": "11", "pos": 67585218, "genotype": "AG"},
        {"rsid": "rs4880", "chrom": "6", "pos": 160113872, "genotype": "TT"},
    ]
    yield from _setup_client(tmp_data_dir, seed_variants=variants)


# ═══════════════════════════════════════════════════════════════════════
# GET /api/analysis/skin/pathways — List pathways
# ═══════════════════════════════════════════════════════════════════════


class TestListPathways:
    def test_returns_pathway_summaries(self, client: tuple[TestClient, int]) -> None:
        tc, sample_id = client
        resp = tc.get(f"/api/analysis/skin/pathways?sample_id={sample_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 4
        pathway_ids = [item["pathway_id"] for item in data["items"]]
        assert "pigmentation_uv" in pathway_ids
        assert "skin_barrier_inflammation" in pathway_ids
        assert "oxidative_stress_aging" in pathway_ids
        assert "skin_micronutrients" in pathway_ids

    def test_pigmentation_elevated(self, client: tuple[TestClient, int]) -> None:
        tc, sample_id = client
        resp = tc.get(f"/api/analysis/skin/pathways?sample_id={sample_id}")
        data = resp.json()
        pigmentation = next(i for i in data["items"] if i["pathway_id"] == "pigmentation_uv")
        assert pigmentation["level"] == "Elevated"
        assert pigmentation["evidence_level"] == 3
        assert pigmentation["called_snps"] == 3
        assert pigmentation["total_snps"] == 4
        assert "rs885479" in pigmentation["missing_snps"]

    def test_barrier_standard(self, client: tuple[TestClient, int]) -> None:
        tc, sample_id = client
        resp = tc.get(f"/api/analysis/skin/pathways?sample_id={sample_id}")
        data = resp.json()
        barrier = next(i for i in data["items"] if i["pathway_id"] == "skin_barrier_inflammation")
        assert barrier["level"] == "Standard"

    def test_mc1r_aggregate_present(self, client: tuple[TestClient, int]) -> None:
        tc, sample_id = client
        resp = tc.get(f"/api/analysis/skin/pathways?sample_id={sample_id}")
        data = resp.json()
        mc1r = data["mc1r_aggregate"]
        assert mc1r is not None
        assert mc1r["r_allele_count"] == 1
        assert "rs1805007" in mc1r["r_allele_rsids"]
        assert mc1r["total_mc1r_called"] == 3
        assert mc1r["risk_label"] == "Moderate UV Sensitivity"
        assert mc1r["evidence_level"] == 3

    def test_insufficient_data_present(self, client: tuple[TestClient, int]) -> None:
        tc, sample_id = client
        resp = tc.get(f"/api/analysis/skin/pathways?sample_id={sample_id}")
        data = resp.json()
        insuf = data["insufficient_data"]
        assert len(insuf) == 1
        assert insuf[0]["gene"] == "FLG"
        assert insuf[0]["rsid"] == "rs61816761"
        assert "R501X" in insuf[0]["finding_text"]
        assert "Insufficient Data" in insuf[0]["finding_text"]
        assert insuf[0]["proxy_target"] is None

    def test_cross_module_present(self, client: tuple[TestClient, int]) -> None:
        tc, sample_id = client
        resp = tc.get(f"/api/analysis/skin/pathways?sample_id={sample_id}")
        data = resp.json()
        cross = data["cross_module"]
        assert len(cross) == 1
        assert cross[0]["gene"] == "MC1R"
        assert cross[0]["target_module"] == "cancer"

    def test_empty_when_no_findings(self, client_no_findings: tuple[TestClient, int]) -> None:
        tc, sample_id = client_no_findings
        resp = tc.get(f"/api/analysis/skin/pathways?sample_id={sample_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0
        assert data["items"] == []
        assert data["mc1r_aggregate"] is None
        assert data["cross_module"] == []
        assert data["insufficient_data"] == []

    def test_unknown_sample_404(self, client: tuple[TestClient, int]) -> None:
        tc, _ = client
        resp = tc.get("/api/analysis/skin/pathways?sample_id=9999")
        assert resp.status_code == 404

    def test_missing_sample_id(self, client: tuple[TestClient, int]) -> None:
        tc, _ = client
        resp = tc.get("/api/analysis/skin/pathways")
        assert resp.status_code == 422


# ═══════════════════════════════════════════════════════════════════════
# GET /api/analysis/skin/pathway/{id} — Pathway detail
# ═══════════════════════════════════════════════════════════════════════


class TestPathwayDetail:
    def test_pigmentation_detail(self, client: tuple[TestClient, int]) -> None:
        tc, sample_id = client
        resp = tc.get(f"/api/analysis/skin/pathway/pigmentation_uv?sample_id={sample_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["pathway_id"] == "pigmentation_uv"
        assert data["pathway_name"] == "Pigmentation & UV Response"
        assert data["level"] == "Elevated"
        assert data["evidence_level"] == 3
        assert len(data["snp_details"]) == 3

    def test_snp_detail_mc1r_allele_class(self, client: tuple[TestClient, int]) -> None:
        tc, sample_id = client
        resp = tc.get(f"/api/analysis/skin/pathway/pigmentation_uv?sample_id={sample_id}")
        data = resp.json()
        mc1r_snp = next(s for s in data["snp_details"] if s["rsid"] == "rs1805007")
        assert mc1r_snp["gene"] == "MC1R"
        assert mc1r_snp["genotype"] == "CT"
        assert mc1r_snp["category"] == "Elevated"
        assert mc1r_snp["mc1r_allele_class"] == "R"

    def test_snp_detail_includes_recommendation(self, client: tuple[TestClient, int]) -> None:
        tc, sample_id = client
        resp = tc.get(f"/api/analysis/skin/pathway/pigmentation_uv?sample_id={sample_id}")
        data = resp.json()
        mc1r_snp = next(s for s in data["snp_details"] if s["rsid"] == "rs1805007")
        assert mc1r_snp["recommendation"] is not None
        assert "uv" in mc1r_snp["recommendation"].lower()

    def test_barrier_detail_flg_flag(self, client: tuple[TestClient, int]) -> None:
        tc, sample_id = client
        resp = tc.get(
            f"/api/analysis/skin/pathway/skin_barrier_inflammation?sample_id={sample_id}"
        )
        data = resp.json()
        assert data["pathway_id"] == "skin_barrier_inflammation"
        assert len(data["snp_details"]) == 1
        flg = data["snp_details"][0]
        assert flg["rsid"] == "rs61816761"
        assert flg["insufficient_data_flag"] is True
        assert flg["coverage_note"] is not None

    def test_oxidative_detail(self, client: tuple[TestClient, int]) -> None:
        tc, sample_id = client
        resp = tc.get(f"/api/analysis/skin/pathway/oxidative_stress_aging?sample_id={sample_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["level"] == "Moderate"
        assert len(data["snp_details"]) == 2

    def test_unknown_pathway_404(self, client: tuple[TestClient, int]) -> None:
        tc, sample_id = client
        resp = tc.get(f"/api/analysis/skin/pathway/nonexistent?sample_id={sample_id}")
        assert resp.status_code == 404

    def test_unknown_sample_404(self, client: tuple[TestClient, int]) -> None:
        tc, _ = client
        resp = tc.get("/api/analysis/skin/pathway/pigmentation_uv?sample_id=9999")
        assert resp.status_code == 404

    def test_missing_sample_id(self, client: tuple[TestClient, int]) -> None:
        tc, _ = client
        resp = tc.get("/api/analysis/skin/pathway/pigmentation_uv")
        assert resp.status_code == 422

    def test_missing_snps_in_detail(self, client: tuple[TestClient, int]) -> None:
        tc, sample_id = client
        resp = tc.get(f"/api/analysis/skin/pathway/pigmentation_uv?sample_id={sample_id}")
        data = resp.json()
        assert "rs885479" in data["missing_snps"]

    def test_pmids_in_detail(self, client: tuple[TestClient, int]) -> None:
        tc, sample_id = client
        resp = tc.get(f"/api/analysis/skin/pathway/pigmentation_uv?sample_id={sample_id}")
        data = resp.json()
        assert "18488028" in data["pmids"]


# ═══════════════════════════════════════════════════════════════════════
# POST /api/analysis/skin/run — Run scoring
# ═══════════════════════════════════════════════════════════════════════


class TestRunScoring:
    def test_run_produces_findings(self, client_with_variants: tuple[TestClient, int]) -> None:
        tc, sample_id = client_with_variants
        resp = tc.post(f"/api/analysis/skin/run?sample_id={sample_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["findings_count"] > 0
        assert data["pathways_scored"] == 4  # Always 4 pathways

    def test_run_then_list(self, client_with_variants: tuple[TestClient, int]) -> None:
        """After running, pathways endpoint returns scored results."""
        tc, sample_id = client_with_variants
        resp = tc.post(f"/api/analysis/skin/run?sample_id={sample_id}")
        assert resp.status_code == 200

        resp = tc.get(f"/api/analysis/skin/pathways?sample_id={sample_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 4

        # Check pigmentation_uv pathway present
        pigmentation = next(
            (i for i in data["items"] if i["pathway_id"] == "pigmentation_uv"),
            None,
        )
        assert pigmentation is not None

    def test_run_includes_mc1r_aggregate(
        self, client_with_variants: tuple[TestClient, int]
    ) -> None:
        """After running, MC1R aggregate should be present."""
        tc, sample_id = client_with_variants
        tc.post(f"/api/analysis/skin/run?sample_id={sample_id}")

        resp = tc.get(f"/api/analysis/skin/pathways?sample_id={sample_id}")
        data = resp.json()
        mc1r = data["mc1r_aggregate"]
        assert mc1r is not None
        assert mc1r["total_mc1r_called"] >= 1

    def test_run_unknown_sample_404(self, client_with_variants: tuple[TestClient, int]) -> None:
        tc, _ = client_with_variants
        resp = tc.post("/api/analysis/skin/run?sample_id=9999")
        assert resp.status_code == 404

    def test_run_idempotent(
        self, client_with_variants: tuple[TestClient, int], tmp_data_dir: Path
    ) -> None:
        """Running scoring twice is idempotent — equal count AND no duplicate rows."""
        tc, sample_id = client_with_variants
        resp1 = tc.post(f"/api/analysis/skin/run?sample_id={sample_id}")
        resp2 = tc.post(f"/api/analysis/skin/run?sample_id={sample_id}")
        count = resp2.json()["findings_count"]
        assert resp1.json()["findings_count"] == count

        # findings_count is the per-run stored count, so equal counts alone do not
        # prove the second run didn't APPEND duplicate rows. Assert the skin
        # findings table holds exactly `count` rows after two runs — i.e. the
        # delete-then-insert in store_skin_findings actually cleared the first run.
        # Resolve the sample DB path from the samples table rather than assuming
        # the on-disk filename, so the check doesn't couple to fixture naming.
        settings = Settings(data_dir=tmp_data_dir, wal_mode=False)
        ref_engine = sa.create_engine(f"sqlite:///{settings.reference_db_path}")
        try:
            with ref_engine.connect() as conn:
                db_path = conn.execute(
                    sa.select(samples.c.db_path).where(samples.c.id == sample_id)
                ).scalar_one()
        finally:
            ref_engine.dispose()
        sample_db = tmp_data_dir / db_path
        engine = sa.create_engine(f"sqlite:///{sample_db}")
        try:
            with engine.connect() as conn:
                rows = conn.execute(
                    sa.select(sa.func.count())
                    .select_from(findings)
                    .where(findings.c.module == "skin")
                ).scalar()
        finally:
            engine.dispose()
        assert rows == count, f"expected {count} skin findings, found {rows} (duplicates?)"

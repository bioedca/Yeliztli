"""Tests for rare variant finder API (P3-29).

Covers:
  - POST /api/analysis/rare-variants/search?sample_id=N — Search with filters
  - GET  /api/analysis/rare-variants/findings?sample_id=N — Stored findings
  - POST /api/analysis/rare-variants/run?sample_id=N — Run with defaults
  - GET  /api/analysis/rare-variants/export/tsv?sample_id=N — TSV export
  - GET  /api/analysis/rare-variants/export/vcf?sample_id=N — VCF export
  - 404 for missing sample
  - Filter validation (af_threshold range, gene_symbols, consequences)
  - Evidence level ordering in findings
"""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path
from unittest.mock import patch

import pytest
import sqlalchemy as sa
from _carriage_fixtures import hom_ref_pathogenic_row
from fastapi.testclient import TestClient

from backend.config import Settings
from backend.db.connection import reset_registry
from backend.db.sample_schema import create_sample_tables
from backend.db.tables import (
    annotated_variants,
    reference_metadata,
    samples,
)

# ── Test data ────────────────────────────────────────────────────────

ANNOTATED_VARIANTS_DATA = [
    {
        "rsid": "rs28897696",
        "chrom": "17",
        "pos": 41245466,
        "ref": "G",
        "alt": "A",
        "genotype": "GA",
        "zygosity": "het",
        "gene_symbol": "BRCA1",
        "consequence": "missense_variant",
        "hgvs_coding": "c.5123C>T",
        "hgvs_protein": "p.Ala1708Glu",
        "gnomad_af_global": 0.00002,
        "gnomad_af_afr": 0.00001,
        "gnomad_af_amr": None,
        "gnomad_af_eas": None,
        "gnomad_af_eur": 0.00003,
        "gnomad_af_fin": None,
        "gnomad_af_sas": None,
        "clinvar_significance": "Pathogenic",
        "clinvar_review_stars": 3,
        "clinvar_accession": "VCV000055399",
        "clinvar_conditions": "Breast-ovarian cancer, familial",
        "cadd_phred": 28.5,
        "revel": 0.92,
        "ensemble_pathogenic": True,
        "evidence_conflict": False,
        "disease_name": "Hereditary breast-ovarian cancer",
        "inheritance_pattern": "AD",
        "annotation_coverage": 31,
    },
    {
        "rsid": "rs121913279",
        "chrom": "7",
        "pos": 117199644,
        "ref": "C",
        "alt": "T",
        "genotype": "CT",
        "zygosity": "het",
        "gene_symbol": "CFTR",
        "consequence": "stop_gained",
        "hgvs_coding": "c.1657C>T",
        "hgvs_protein": "p.Arg553Ter",
        "gnomad_af_global": 0.0005,
        "gnomad_af_afr": None,
        "gnomad_af_amr": None,
        "gnomad_af_eas": None,
        "gnomad_af_eur": 0.001,
        "gnomad_af_fin": None,
        "gnomad_af_sas": None,
        "clinvar_significance": "Likely pathogenic",
        "clinvar_review_stars": 1,
        "clinvar_accession": "VCV000007108",
        "clinvar_conditions": "Cystic fibrosis",
        "cadd_phred": 35.0,
        "revel": None,
        "ensemble_pathogenic": False,
        "evidence_conflict": False,
        "disease_name": "Cystic fibrosis",
        "inheritance_pattern": "AR",
        "annotation_coverage": 15,
    },
    {
        "rsid": "rs999999999",
        "chrom": "3",
        "pos": 12345678,
        "ref": "A",
        "alt": "T",
        "genotype": "AT",
        "zygosity": "het",
        "gene_symbol": "TP53",
        "consequence": "missense_variant",
        "hgvs_coding": None,
        "hgvs_protein": None,
        "gnomad_af_global": None,
        "gnomad_af_afr": None,
        "gnomad_af_amr": None,
        "gnomad_af_eas": None,
        "gnomad_af_eur": None,
        "gnomad_af_fin": None,
        "gnomad_af_sas": None,
        "clinvar_significance": None,
        "clinvar_review_stars": None,
        "clinvar_accession": None,
        "clinvar_conditions": None,
        "cadd_phred": 22.0,
        "revel": 0.75,
        "ensemble_pathogenic": True,
        "evidence_conflict": False,
        "disease_name": None,
        "inheritance_pattern": None,
        "annotation_coverage": 9,
    },
    {
        "rsid": "rs12345",
        "chrom": "1",
        "pos": 1000000,
        "ref": "C",
        "alt": "G",
        "genotype": "GG",
        "zygosity": "hom_alt",
        "gene_symbol": "BRCA1",
        "consequence": "synonymous_variant",
        "hgvs_coding": None,
        "hgvs_protein": None,
        "gnomad_af_global": 0.15,
        "gnomad_af_afr": 0.12,
        "gnomad_af_amr": None,
        "gnomad_af_eas": None,
        "gnomad_af_eur": 0.18,
        "gnomad_af_fin": None,
        "gnomad_af_sas": None,
        "clinvar_significance": "Benign",
        "clinvar_review_stars": 2,
        "clinvar_accession": "VCV000000001",
        "clinvar_conditions": None,
        "cadd_phred": 1.0,
        "revel": None,
        "ensemble_pathogenic": False,
        "evidence_conflict": False,
        "disease_name": None,
        "inheritance_pattern": None,
        "annotation_coverage": 31,
    },
    {
        # Rare + Pathogenic + hom_alt carrier: passes the default rarity /
        # significance / carriage filters, so ONLY the zygosity filter can
        # exclude it. Without this row the zygosity-filter test is vacuous —
        # the sole other non-het variant (rs12345) is common+benign and is
        # dropped for AF reasons regardless of zygosity.
        "rsid": "rs_homalt_rare",
        "chrom": "13",
        "pos": 32339000,
        "ref": "C",
        "alt": "T",
        "genotype": "TT",
        "zygosity": "hom_alt",
        "gene_symbol": "BRCA2",
        "consequence": "stop_gained",
        "hgvs_coding": None,
        "hgvs_protein": None,
        "gnomad_af_global": 0.0003,
        "gnomad_af_afr": None,
        "gnomad_af_amr": None,
        "gnomad_af_eas": None,
        "gnomad_af_eur": 0.0004,
        "gnomad_af_fin": None,
        "gnomad_af_sas": None,
        "clinvar_significance": "Pathogenic",
        "clinvar_review_stars": 2,
        "clinvar_accession": "VCV000009999",
        "clinvar_conditions": "Hereditary breast and ovarian cancer",
        "cadd_phred": 33.0,
        "revel": 0.9,
        "ensemble_pathogenic": True,
        "evidence_conflict": False,
        "disease_name": "Hereditary breast-ovarian cancer",
        "inheritance_pattern": "AD",
        "annotation_coverage": 31,
    },
]


# ── Fixtures ─────────────────────────────────────────────────────────


@pytest.fixture()
def tmp_data_dir(tmp_path: Path) -> Path:
    """Create a temporary data directory with samples subdirectory."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "samples").mkdir()
    return data_dir


@pytest.fixture()
def sample_db_path(tmp_data_dir: Path) -> Path:
    """Create a sample database with annotated variants."""
    db_path = tmp_data_dir / "samples" / "sample_1.db"
    engine = sa.create_engine(f"sqlite:///{db_path}")
    create_sample_tables(engine)

    with engine.begin() as conn:
        conn.execute(sa.insert(annotated_variants), ANNOTATED_VARIANTS_DATA)

    engine.dispose()
    return db_path


@pytest.fixture()
def rare_client(tmp_data_dir: Path, sample_db_path: Path) -> Generator[TestClient, None, None]:
    """FastAPI test client with a sample DB containing annotated variants."""
    settings = Settings(data_dir=tmp_data_dir, wal_mode=False)

    # Create reference.db with samples table
    ref_path = settings.reference_db_path
    engine = sa.create_engine(f"sqlite:///{ref_path}")
    reference_metadata.create_all(engine)

    with engine.begin() as conn:
        conn.execute(
            sa.insert(samples),
            [
                {
                    "id": 1,
                    "name": "test_sample",
                    "file_format": "23andme_v5",
                    "file_hash": "abc123",
                    "db_path": "samples/sample_1.db",
                    "status": "complete",
                }
            ],
        )
    engine.dispose()

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


@pytest.fixture()
def empty_client(tmp_data_dir: Path) -> Generator[TestClient, None, None]:
    """FastAPI test client with no samples."""
    settings = Settings(data_dir=tmp_data_dir, wal_mode=False)

    ref_path = settings.reference_db_path
    engine = sa.create_engine(f"sqlite:///{ref_path}")
    reference_metadata.create_all(engine)
    engine.dispose()

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


# ═══════════════════════════════════════════════════════════════════════
# POST /api/analysis/rare-variants/search
# ═══════════════════════════════════════════════════════════════════════


class TestSearchEndpoint:
    """Tests for the search endpoint."""

    def test_search_default_filters(self, rare_client: TestClient) -> None:
        """Default search returns rare + novel variants (AF < 0.01)."""
        resp = rare_client.post(
            "/api/analysis/rare-variants/search?sample_id=1",
            json={},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] >= 2  # BRCA1 pathogenic + TP53 novel
        assert data["total_variants_scanned"] == 5
        # High AF variant (rs12345, 0.15) should be excluded
        rsids = [item["rsid"] for item in data["items"]]
        assert "rs12345" not in rsids
        assert "rs28897696" in rsids  # BRCA1 pathogenic

    def test_search_excludes_hom_ref_pathogenic(
        self, rare_client: TestClient, sample_db_path: Path
    ) -> None:
        """Default search must not surface a hom_ref (non-carrier) Pathogenic variant.

        The ``/search`` route persists findings, so it carriage-gates the finder
        (``_request_to_filter`` forces ``carried_only=True``) — a non-carried
        Pathogenic variant never leaks into results or stored findings.
        """
        engine = sa.create_engine(f"sqlite:///{sample_db_path}")
        with engine.begin() as conn:
            conn.execute(sa.insert(annotated_variants), [hom_ref_pathogenic_row()])
        engine.dispose()

        resp = rare_client.post("/api/analysis/rare-variants/search?sample_id=1", json={})
        assert resp.status_code == 200
        rsids = [item["rsid"] for item in resp.json()["items"]]
        assert "rs_hom_ref_pathogenic" not in rsids

    def test_search_gene_filter(self, rare_client: TestClient) -> None:
        """Gene panel filter restricts results to specified genes."""
        resp = rare_client.post(
            "/api/analysis/rare-variants/search?sample_id=1",
            json={"gene_symbols": ["BRCA1"]},
        )
        assert resp.status_code == 200
        data = resp.json()
        for item in data["items"]:
            assert item["gene_symbol"] == "BRCA1"

    def test_search_consequence_filter(self, rare_client: TestClient) -> None:
        """Consequence filter restricts to matching SO terms."""
        resp = rare_client.post(
            "/api/analysis/rare-variants/search?sample_id=1",
            json={"consequences": ["stop_gained"]},
        )
        assert resp.status_code == 200
        data = resp.json()
        for item in data["items"]:
            assert "stop_gained" in item["consequence"]

    def test_search_clinvar_filter(self, rare_client: TestClient) -> None:
        """ClinVar significance filter works."""
        resp = rare_client.post(
            "/api/analysis/rare-variants/search?sample_id=1",
            json={"clinvar_significance": ["Pathogenic"]},
        )
        assert resp.status_code == 200
        data = resp.json()
        for item in data["items"]:
            assert item["clinvar_significance"] == "Pathogenic"

    def test_search_af_threshold(self, rare_client: TestClient) -> None:
        """Custom AF threshold changes results."""
        resp = rare_client.post(
            "/api/analysis/rare-variants/search?sample_id=1",
            json={"af_threshold": 0.001, "include_novel": False},
        )
        assert resp.status_code == 200
        data = resp.json()
        for item in data["items"]:
            assert item["gnomad_af_global"] is not None
            assert item["gnomad_af_global"] < 0.001

    def test_search_exclude_novel(self, rare_client: TestClient) -> None:
        """Excluding novel variants removes those with no gnomAD AF."""
        resp = rare_client.post(
            "/api/analysis/rare-variants/search?sample_id=1",
            json={"include_novel": False},
        )
        assert resp.status_code == 200
        data = resp.json()
        for item in data["items"]:
            assert item["gnomad_af_global"] is not None

    def test_search_exposes_per_row_is_novel(self, rare_client: TestClient) -> None:
        """#866: each row carries is_novel (= RareVariantRecord.is_novel), so the UI
        renders "Novel" from real novelty rather than gnomAD-absence.

        rs999999999 is absent from gnomAD yet rs-prefixed (catalogued in dbSNP), so it
        is NOT novel — the exact case the old gnomad_af_global==null proxy mislabeled
        "Novel". rs28897696 (catalogued, present in gnomAD) is likewise not novel.
        """
        resp = rare_client.post("/api/analysis/rare-variants/search?sample_id=1", json={})
        assert resp.status_code == 200
        by_rsid = {item["rsid"]: item for item in resp.json()["items"]}
        assert all("is_novel" in item for item in by_rsid.values())
        # gnomAD-absent but catalogued (has an rs id) ⇒ not novel (the #866 discriminator).
        tp53 = by_rsid["rs999999999"]
        assert tp53["gnomad_af_global"] is None
        assert tp53["is_novel"] is False
        assert by_rsid["rs28897696"]["is_novel"] is False

    def test_search_zygosity_filter(self, rare_client: TestClient) -> None:
        """Zygosity filter is two-sided — het-only must EXCLUDE a carried hom_alt.

        A filter that silently ignored zygosity would still pass a one-sided
        "every returned row is het" check, because the only other rare carrier in
        the seed is hom_alt. So assert both directions: the het filter drops
        ``rs_homalt_rare`` (a rare, Pathogenic, hom_alt variant that passes every
        other default filter), the hom_alt filter surfaces it, and the two result
        sets are disjoint.
        """
        het = rare_client.post(
            "/api/analysis/rare-variants/search?sample_id=1",
            json={"zygosity": "het"},
        )
        assert het.status_code == 200
        het_items = het.json()["items"]
        het_rsids = {item["rsid"] for item in het_items}
        assert het_rsids, "expected at least one het carrier in the seed"
        for item in het_items:
            assert item["zygosity"] == "het"
        assert "rs_homalt_rare" not in het_rsids  # hom_alt excluded by het filter

        hom = rare_client.post(
            "/api/analysis/rare-variants/search?sample_id=1",
            json={"zygosity": "hom_alt"},
        )
        assert hom.status_code == 200
        hom_items = hom.json()["items"]
        hom_rsids = {item["rsid"] for item in hom_items}
        for item in hom_items:
            assert item["zygosity"] == "hom_alt"
        assert "rs_homalt_rare" in hom_rsids  # surfaced when the filter matches
        assert het_rsids.isdisjoint(hom_rsids)  # the filter genuinely partitions

    def test_search_stores_findings(self, rare_client: TestClient) -> None:
        """Search also stores findings in the sample DB."""
        rare_client.post(
            "/api/analysis/rare-variants/search?sample_id=1",
            json={},
        )
        # Verify findings are stored
        resp = rare_client.get("/api/analysis/rare-variants/findings?sample_id=1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] > 0

    def test_search_response_metadata(self, rare_client: TestClient) -> None:
        """Response includes metadata fields."""
        resp = rare_client.post(
            "/api/analysis/rare-variants/search?sample_id=1",
            json={},
        )
        data = resp.json()
        assert "novel_count" in data
        assert "pathogenic_count" in data
        assert "genes_with_findings" in data
        assert "filters_applied" in data
        assert isinstance(data["genes_with_findings"], list)

    def test_search_missing_sample_404(self, empty_client: TestClient) -> None:
        """Missing sample returns 404."""
        resp = empty_client.post(
            "/api/analysis/rare-variants/search?sample_id=999",
            json={},
        )
        assert resp.status_code == 404

    def test_search_invalid_af_threshold(self, rare_client: TestClient) -> None:
        """AF threshold outside 0–1 range is rejected."""
        resp = rare_client.post(
            "/api/analysis/rare-variants/search?sample_id=1",
            json={"af_threshold": 1.5},
        )
        assert resp.status_code == 422

    def test_search_pathogenic_sorted_first(self, rare_client: TestClient) -> None:
        """ClinVar P/LP variants appear before others in results."""
        resp = rare_client.post(
            "/api/analysis/rare-variants/search?sample_id=1",
            json={},
        )
        data = resp.json()
        items = data["items"]
        if len(items) >= 2:
            # First items should be ClinVar P/LP
            first = items[0]
            assert first["clinvar_significance"] in [
                "Pathogenic",
                "Likely pathogenic",
                "Pathogenic/Likely pathogenic",
            ]


# ═══════════════════════════════════════════════════════════════════════
# GET /api/analysis/rare-variants/findings
# ═══════════════════════════════════════════════════════════════════════


class TestFindingsEndpoint:
    """Tests for the findings endpoint."""

    def test_findings_empty_before_search(self, rare_client: TestClient) -> None:
        """Findings are empty before any search is run."""
        resp = rare_client.get("/api/analysis/rare-variants/findings?sample_id=1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0
        assert data["items"] == []

    def test_findings_after_search(self, rare_client: TestClient) -> None:
        """Findings are populated after a search."""
        rare_client.post(
            "/api/analysis/rare-variants/search?sample_id=1",
            json={},
        )
        resp = rare_client.get("/api/analysis/rare-variants/findings?sample_id=1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] > 0
        for item in data["items"]:
            assert "category" in item
            assert "evidence_level" in item
            assert "finding_text" in item

    def test_findings_sorted_by_evidence(self, rare_client: TestClient) -> None:
        """Findings are sorted by evidence level descending."""
        rare_client.post(
            "/api/analysis/rare-variants/search?sample_id=1",
            json={},
        )
        resp = rare_client.get("/api/analysis/rare-variants/findings?sample_id=1")
        items = resp.json()["items"]
        if len(items) >= 2:
            levels = [item["evidence_level"] for item in items]
            # Should be non-increasing (desc order, but may have same-level items)
            for i in range(len(levels) - 1):
                assert levels[i] >= levels[i + 1], f"Evidence levels not sorted: {levels}"

    def test_findings_contain_detail(self, rare_client: TestClient) -> None:
        """Findings include parsed detail_json."""
        rare_client.post(
            "/api/analysis/rare-variants/search?sample_id=1",
            json={},
        )
        resp = rare_client.get("/api/analysis/rare-variants/findings?sample_id=1")
        items = resp.json()["items"]
        for item in items:
            assert isinstance(item["detail"], dict)

    def test_findings_missing_sample_404(self, empty_client: TestClient) -> None:
        """Missing sample returns 404."""
        resp = empty_client.get("/api/analysis/rare-variants/findings?sample_id=999")
        assert resp.status_code == 404


# ═══════════════════════════════════════════════════════════════════════
# POST /api/analysis/rare-variants/run
# ═══════════════════════════════════════════════════════════════════════


class TestRunEndpoint:
    """Tests for the run endpoint."""

    def test_run_default_filters(self, rare_client: TestClient) -> None:
        """Run with no body uses default filters."""
        resp = rare_client.post("/api/analysis/rare-variants/run?sample_id=1")
        assert resp.status_code == 200
        data = resp.json()
        assert "variants_found" in data
        assert "findings_stored" in data
        assert data["variants_found"] >= 2
        assert data["findings_stored"] == data["variants_found"]

    def test_run_with_filters(self, rare_client: TestClient) -> None:
        """Run with custom filter body."""
        resp = rare_client.post(
            "/api/analysis/rare-variants/run?sample_id=1",
            json={"gene_symbols": ["BRCA1"], "af_threshold": 0.001},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["variants_found"] >= 1

    def test_run_missing_sample_404(self, empty_client: TestClient) -> None:
        """Missing sample returns 404."""
        resp = empty_client.post("/api/analysis/rare-variants/run?sample_id=999")
        assert resp.status_code == 404


# ═══════════════════════════════════════════════════════════════════════
# GET /api/analysis/rare-variants/export/tsv
# ═══════════════════════════════════════════════════════════════════════


class TestTSVExport:
    """Tests for TSV export."""

    def test_tsv_export_has_header(self, rare_client: TestClient) -> None:
        """TSV export includes a header row."""
        rare_client.post(
            "/api/analysis/rare-variants/search?sample_id=1",
            json={},
        )
        resp = rare_client.get("/api/analysis/rare-variants/export/tsv?sample_id=1")
        assert resp.status_code == 200
        assert "text/tab-separated-values" in resp.headers["content-type"]
        lines = resp.text.strip().split("\n")
        assert len(lines) >= 2  # header + at least one data row
        header = lines[0]
        assert "rsid" in header
        assert "gene_symbol" in header

    def test_tsv_export_content_disposition(self, rare_client: TestClient) -> None:
        """TSV has correct Content-Disposition header for download."""
        rare_client.post(
            "/api/analysis/rare-variants/search?sample_id=1",
            json={},
        )
        resp = rare_client.get("/api/analysis/rare-variants/export/tsv?sample_id=1")
        assert "attachment" in resp.headers.get("content-disposition", "")
        assert ".tsv" in resp.headers.get("content-disposition", "")

    def test_tsv_export_empty_no_findings(self, rare_client: TestClient) -> None:
        """TSV export with no findings returns header only."""
        resp = rare_client.get("/api/analysis/rare-variants/export/tsv?sample_id=1")
        assert resp.status_code == 200
        lines = resp.text.strip().split("\n")
        assert len(lines) == 1  # header only

    def test_tsv_export_missing_sample_404(self, empty_client: TestClient) -> None:
        """Missing sample returns 404."""
        resp = empty_client.get("/api/analysis/rare-variants/export/tsv?sample_id=999")
        assert resp.status_code == 404

    def test_tsv_tab_separated(self, rare_client: TestClient) -> None:
        """TSV rows are tab-separated with correct column count."""
        rare_client.post(
            "/api/analysis/rare-variants/search?sample_id=1",
            json={},
        )
        resp = rare_client.get("/api/analysis/rare-variants/export/tsv?sample_id=1")
        lines = resp.text.strip().split("\n")
        header_cols = lines[0].split("\t")
        for line in lines[1:]:
            data_cols = line.split("\t")
            assert len(data_cols) == len(header_cols)


# ═══════════════════════════════════════════════════════════════════════
# GET /api/analysis/rare-variants/export/vcf
# ═══════════════════════════════════════════════════════════════════════


class TestVCFExport:
    """Tests for VCF export."""

    def test_vcf_export_has_header(self, rare_client: TestClient) -> None:
        """VCF export starts with proper VCF 4.2 header."""
        rare_client.post(
            "/api/analysis/rare-variants/search?sample_id=1",
            json={},
        )
        resp = rare_client.get("/api/analysis/rare-variants/export/vcf?sample_id=1")
        assert resp.status_code == 200
        text = resp.text
        assert text.startswith("##fileformat=VCFv4.2")
        assert "##source=Yeliztli-RareVariantFinder" in text
        assert "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO" in text

    def test_vcf_export_info_fields(self, rare_client: TestClient) -> None:
        """VCF data lines include INFO fields."""
        rare_client.post(
            "/api/analysis/rare-variants/search?sample_id=1",
            json={},
        )
        resp = rare_client.get("/api/analysis/rare-variants/export/vcf?sample_id=1")
        lines = resp.text.strip().split("\n")
        data_lines = [line for line in lines if not line.startswith("#")]
        assert len(data_lines) > 0
        for line in data_lines:
            assert "GENE=" in line
            assert "EVLVL=" in line

    def test_vcf_export_has_real_chrom_pos(self, rare_client: TestClient) -> None:
        """VCF data lines contain real chromosome and position from annotated_variants."""
        rare_client.post(
            "/api/analysis/rare-variants/search?sample_id=1",
            json={},
        )
        resp = rare_client.get("/api/analysis/rare-variants/export/vcf?sample_id=1")
        lines = resp.text.strip().split("\n")
        data_lines = [line for line in lines if not line.startswith("#")]
        for line in data_lines:
            cols = line.split("\t")
            chrom = cols[0]
            pos = cols[1]
            # chrom should be a real chromosome, not a placeholder
            assert chrom != ".", f"CHROM should not be placeholder: {line}"
            # pos should be a real integer > 0
            assert int(pos) > 0, f"POS should be > 0: {line}"

    def test_vcf_export_content_disposition(self, rare_client: TestClient) -> None:
        """VCF has correct Content-Disposition header."""
        rare_client.post(
            "/api/analysis/rare-variants/search?sample_id=1",
            json={},
        )
        resp = rare_client.get("/api/analysis/rare-variants/export/vcf?sample_id=1")
        assert "attachment" in resp.headers.get("content-disposition", "")
        assert ".vcf" in resp.headers.get("content-disposition", "")

    def test_vcf_export_missing_sample_404(self, empty_client: TestClient) -> None:
        """Missing sample returns 404."""
        resp = empty_client.get("/api/analysis/rare-variants/export/vcf?sample_id=999")
        assert resp.status_code == 404

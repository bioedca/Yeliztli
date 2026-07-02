"""Tests for custom gene panel API (P4-11).

Covers:
  - POST /api/panels/upload — Upload and save a custom panel
  - POST /api/panels/parse — Parse file without saving (preview)
  - GET  /api/panels — List all saved panels
  - GET  /api/panels/{panel_id} — Get single panel
  - DELETE /api/panels/{panel_id} — Delete panel
  - POST /api/panels/{panel_id}/search — Run rare variant finder with panel
  - Error cases (missing panel, invalid file, etc.)
"""

from __future__ import annotations

import io
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
        "gnomad_af_afr": None,
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
]

SRY_PATHOGENIC_VARIANT = {
    "rsid": "rs_custom_panel_y_pathogenic",
    "chrom": "Y",
    "pos": 2_787_000,
    "ref": "A",
    "alt": "G",
    "genotype": "G",
    "zygosity": "hom_alt",
    "gene_symbol": "SRY",
    "consequence": "missense_variant",
    "hgvs_coding": "c.100A>G",
    "hgvs_protein": "p.Lys34Arg",
    "gnomad_af_global": 0.0005,
    "gnomad_af_afr": None,
    "gnomad_af_amr": None,
    "gnomad_af_eas": None,
    "gnomad_af_eur": None,
    "gnomad_af_fin": None,
    "gnomad_af_sas": None,
    "clinvar_significance": "Pathogenic",
    "clinvar_review_stars": 2,
    "clinvar_accession": "VCV000000001",
    "clinvar_conditions": "46,XY sex reversal",
    "cadd_phred": 24.0,
    "revel": 0.81,
    "ensemble_pathogenic": True,
    "evidence_conflict": False,
    "disease_name": "46,XY sex reversal",
    "inheritance_pattern": "Y-linked",
    "annotation_coverage": 15,
}


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


def _insert_annotated_variant(sample_db_path: Path, row: dict) -> None:
    engine = sa.create_engine(f"sqlite:///{sample_db_path}")
    with engine.begin() as conn:
        conn.execute(sa.insert(annotated_variants), [row])
    engine.dispose()


@pytest.fixture()
def panel_client(tmp_data_dir: Path, sample_db_path: Path) -> Generator[TestClient, None, None]:
    """FastAPI test client with reference.db and a sample DB."""
    settings = Settings(data_dir=tmp_data_dir, wal_mode=False)

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


# ═══════════════════════════════════════════════════════════════════════
# POST /api/panels/parse
# ═══════════════════════════════════════════════════════════════════════


class TestParseEndpoint:
    """Tests for the parse preview endpoint."""

    def test_parse_gene_list(self, panel_client: TestClient) -> None:
        """Parse a gene list file returns preview."""
        content = b"BRCA1\nBRCA2\nTP53"
        resp = panel_client.post(
            "/api/panels/parse",
            files={"file": ("genes.txt", io.BytesIO(content), "text/plain")},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["gene_count"] == 3
        assert data["source_type"] == "gene_list"
        assert "BRCA1" in data["gene_symbols"]

    def test_parse_bed_file(self, panel_client: TestClient) -> None:
        """Parse a BED file returns preview with genes and regions."""
        content = b"chr17\t41196312\t41277500\tBRCA1\nchr7\t117120017\t117308718\tCFTR"
        resp = panel_client.post(
            "/api/panels/parse",
            files={"file": ("panel.bed", io.BytesIO(content), "text/plain")},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["source_type"] == "bed"
        assert data["gene_count"] == 2
        assert data["region_count"] == 2

    def test_parse_empty_file(self, panel_client: TestClient) -> None:
        """Empty file returns 422."""
        resp = panel_client.post(
            "/api/panels/parse",
            files={"file": ("empty.txt", io.BytesIO(b""), "text/plain")},
        )
        assert resp.status_code == 422

    def test_parse_invalid_encoding(self, panel_client: TestClient) -> None:
        """Non-UTF-8 file returns 400."""
        resp = panel_client.post(
            "/api/panels/parse",
            files={"file": ("bad.txt", io.BytesIO(b"\xff\xfe"), "text/plain")},
        )
        assert resp.status_code == 400


# ═══════════════════════════════════════════════════════════════════════
# POST /api/panels/upload
# ═══════════════════════════════════════════════════════════════════════


class TestUploadEndpoint:
    """Tests for the upload and save endpoint."""

    def test_upload_gene_list(self, panel_client: TestClient) -> None:
        """Upload a gene list and save it as a panel."""
        content = b"BRCA1\nBRCA2\nTP53"
        resp = panel_client.post(
            "/api/panels/upload?name=My+Cancer+Panel",
            files={"file": ("genes.txt", io.BytesIO(content), "text/plain")},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["panel"]["name"] == "My Cancer Panel"
        assert data["panel"]["gene_count"] == 3
        assert data["panel"]["source_type"] == "gene_list"
        assert data["panel"]["id"] > 0

    def test_upload_bed_file(self, panel_client: TestClient) -> None:
        """Upload a BED file and save it."""
        content = b"chr17\t41196312\t41277500\tBRCA1\nchr7\t117120017\t117308718\tCFTR"
        resp = panel_client.post(
            "/api/panels/upload?name=BED+Panel",
            files={"file": ("regions.bed", io.BytesIO(content), "text/plain")},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["panel"]["source_type"] == "bed"
        assert data["panel"]["gene_count"] == 2
        assert data["panel"]["bed_regions"] is not None
        assert len(data["panel"]["bed_regions"]) == 2

    def test_upload_missing_name(self, panel_client: TestClient) -> None:
        """Upload without name parameter returns 422."""
        resp = panel_client.post(
            "/api/panels/upload",
            files={"file": ("genes.txt", io.BytesIO(b"BRCA1"), "text/plain")},
        )
        assert resp.status_code == 422

    def test_upload_empty_file(self, panel_client: TestClient) -> None:
        """Upload empty file returns 422."""
        resp = panel_client.post(
            "/api/panels/upload?name=Empty",
            files={"file": ("empty.txt", io.BytesIO(b""), "text/plain")},
        )
        assert resp.status_code == 422


# ═══════════════════════════════════════════════════════════════════════
# GET /api/panels
# ═══════════════════════════════════════════════════════════════════════


class TestListEndpoint:
    """Tests for the list panels endpoint."""

    def test_list_empty(self, panel_client: TestClient) -> None:
        """Empty panel list returns zero items."""
        resp = panel_client.get("/api/panels")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0
        assert data["items"] == []

    def test_list_after_upload(self, panel_client: TestClient) -> None:
        """List includes uploaded panels."""
        # Upload two panels
        panel_client.post(
            "/api/panels/upload?name=Panel+A",
            files={"file": ("a.txt", io.BytesIO(b"BRCA1\nTP53"), "text/plain")},
        )
        panel_client.post(
            "/api/panels/upload?name=Panel+B",
            files={"file": ("b.txt", io.BytesIO(b"CFTR\nHBB"), "text/plain")},
        )

        resp = panel_client.get("/api/panels")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 2
        names = [p["name"] for p in data["items"]]
        assert "Panel A" in names
        assert "Panel B" in names


# ═══════════════════════════════════════════════════════════════════════
# GET /api/panels/{panel_id}
# ═══════════════════════════════════════════════════════════════════════


class TestGetEndpoint:
    """Tests for the get single panel endpoint."""

    def test_get_existing(self, panel_client: TestClient) -> None:
        """Get an existing panel by ID."""
        upload_resp = panel_client.post(
            "/api/panels/upload?name=Test+Panel",
            files={"file": ("test.txt", io.BytesIO(b"BRCA1\nTP53"), "text/plain")},
        )
        panel_id = upload_resp.json()["panel"]["id"]

        resp = panel_client.get(f"/api/panels/{panel_id}")
        assert resp.status_code == 200
        assert resp.json()["name"] == "Test Panel"

    def test_get_not_found(self, panel_client: TestClient) -> None:
        """Non-existent panel ID returns 404."""
        resp = panel_client.get("/api/panels/999")
        assert resp.status_code == 404


# ═══════════════════════════════════════════════════════════════════════
# DELETE /api/panels/{panel_id}
# ═══════════════════════════════════════════════════════════════════════


class TestDeleteEndpoint:
    """Tests for the delete panel endpoint."""

    def test_delete_existing(self, panel_client: TestClient) -> None:
        """Delete an existing panel."""
        upload_resp = panel_client.post(
            "/api/panels/upload?name=To+Delete",
            files={"file": ("del.txt", io.BytesIO(b"BRCA1"), "text/plain")},
        )
        panel_id = upload_resp.json()["panel"]["id"]

        resp = panel_client.delete(f"/api/panels/{panel_id}")
        assert resp.status_code == 200
        assert resp.json()["status"] == "deleted"

        # Verify it's gone
        resp = panel_client.get(f"/api/panels/{panel_id}")
        assert resp.status_code == 404

    def test_delete_not_found(self, panel_client: TestClient) -> None:
        """Deleting non-existent panel returns 404."""
        resp = panel_client.delete("/api/panels/999")
        assert resp.status_code == 404


# ═══════════════════════════════════════════════════════════════════════
# POST /api/panels/{panel_id}/search
# ═══════════════════════════════════════════════════════════════════════


class TestSearchWithPanelEndpoint:
    """Tests for running rare variant search with a saved panel."""

    def test_search_with_panel(self, panel_client: TestClient) -> None:
        """Search with a saved panel returns matching variants."""
        # Upload a panel
        upload_resp = panel_client.post(
            "/api/panels/upload?name=BRCA+Panel",
            files={"file": ("brca.txt", io.BytesIO(b"BRCA1\nBRCA2"), "text/plain")},
        )
        panel_id = upload_resp.json()["panel"]["id"]

        # Search with the panel
        resp = panel_client.post(
            f"/api/panels/{panel_id}/search?sample_id=1",
            json={},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["panel_name"] == "BRCA Panel"
        assert data["variants_found"] >= 1
        assert data["findings_stored"] >= 1

    def test_search_with_panel_and_filters(self, panel_client: TestClient) -> None:
        """Search with panel respects additional filter parameters."""
        upload_resp = panel_client.post(
            "/api/panels/upload?name=All+Genes",
            files={"file": ("all.txt", io.BytesIO(b"BRCA1\nCFTR"), "text/plain")},
        )
        panel_id = upload_resp.json()["panel"]["id"]

        resp = panel_client.post(
            f"/api/panels/{panel_id}/search?sample_id=1",
            json={"clinvar_significance": ["Pathogenic"]},
        )
        assert resp.status_code == 200
        data = resp.json()
        # Only BRCA1 Pathogenic variant should match
        assert data["variants_found"] >= 1
        for gene in data["genes_with_findings"]:
            assert gene in ["BRCA1"]

    def test_search_with_panel_excludes_hom_ref_pathogenic(
        self, panel_client: TestClient, sample_db_path: Path
    ) -> None:
        """A hom_ref (non-carrier) Pathogenic variant in a panel gene is not found.

        Panel search persists findings, so it carriage-gates the finder
        (``carried_only=True``) — a non-carried Pathogenic variant in a panel
        gene is never counted as found.
        """
        _insert_annotated_variant(
            sample_db_path,
            hom_ref_pathogenic_row(gene_symbol="ZZHOMREF"),
        )

        upload_resp = panel_client.post(
            "/api/panels/upload?name=Homref+Panel",
            files={"file": ("homref.txt", io.BytesIO(b"ZZHOMREF"), "text/plain")},
        )
        panel_id = upload_resp.json()["panel"]["id"]

        resp = panel_client.post(
            f"/api/panels/{panel_id}/search?sample_id=1",
            json={},
        )
        assert resp.status_code == 200
        # The only variant in this panel gene is a non-carrier → nothing found.
        assert resp.json()["variants_found"] == 0

    def test_search_panel_suppresses_y_variant_for_xx_sample(
        self, panel_client: TestClient, sample_db_path: Path
    ) -> None:
        """Custom panel search applies the rare-variant chrY-by-sex gate."""
        _insert_annotated_variant(sample_db_path, SRY_PATHOGENIC_VARIANT)

        upload_resp = panel_client.post(
            "/api/panels/upload?name=SRY+Panel",
            files={"file": ("sry.txt", io.BytesIO(b"SRY"), "text/plain")},
        )
        panel_id = upload_resp.json()["panel"]["id"]

        with patch("backend.api.routes.custom_panels.infer_biological_sex", return_value="XX"):
            resp = panel_client.post(
                f"/api/panels/{panel_id}/search?sample_id=1",
                json={},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["variants_found"] == 0
        assert data["findings_stored"] == 0
        assert data["genes_with_findings"] == []

    @pytest.mark.parametrize("inferred_sex", ["XY", "unknown"])
    def test_search_panel_keeps_y_variant_when_not_confident_xx(
        self,
        panel_client: TestClient,
        sample_db_path: Path,
        inferred_sex: str,
    ) -> None:
        """Only confident XX suppresses chrY findings from custom panel search."""
        _insert_annotated_variant(sample_db_path, SRY_PATHOGENIC_VARIANT)

        upload_resp = panel_client.post(
            "/api/panels/upload?name=SRY+Panel",
            files={"file": ("sry.txt", io.BytesIO(b"SRY"), "text/plain")},
        )
        panel_id = upload_resp.json()["panel"]["id"]

        with patch(
            "backend.api.routes.custom_panels.infer_biological_sex",
            return_value=inferred_sex,
        ):
            resp = panel_client.post(
                f"/api/panels/{panel_id}/search?sample_id=1",
                json={},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["variants_found"] == 1
        assert data["findings_stored"] == 1
        assert data["genes_with_findings"] == ["SRY"]

    def test_search_panel_not_found(self, panel_client: TestClient) -> None:
        """Search with non-existent panel returns 404."""
        resp = panel_client.post(
            "/api/panels/999/search?sample_id=1",
            json={},
        )
        assert resp.status_code == 404

    def test_search_sample_not_found(self, panel_client: TestClient) -> None:
        """Search with non-existent sample returns 404."""
        upload_resp = panel_client.post(
            "/api/panels/upload?name=Test",
            files={"file": ("test.txt", io.BytesIO(b"BRCA1"), "text/plain")},
        )
        panel_id = upload_resp.json()["panel"]["id"]

        resp = panel_client.post(
            f"/api/panels/{panel_id}/search?sample_id=999",
            json={},
        )
        assert resp.status_code == 404

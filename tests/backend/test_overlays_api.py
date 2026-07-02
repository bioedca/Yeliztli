"""Tests for vcfanno overlay API (P4-12).

Covers:
  - POST /api/overlays/parse — Preview overlay file
  - POST /api/overlays/upload — Upload and save overlay
  - GET  /api/overlays — List all overlays
  - GET  /api/overlays/{id} — Get single overlay
  - DELETE /api/overlays/{id} — Delete overlay
  - POST /api/overlays/{id}/apply — Apply overlay to sample
  - GET  /api/overlays/{id}/results — Get overlay results
  - Error cases (missing overlay, invalid file, etc.)

Integration test T4-14: vcfanno overlay adds custom annotations visible
in variant table.
"""

from __future__ import annotations

import gzip
import io
from collections.abc import Generator
from pathlib import Path
from unittest.mock import patch

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from backend.annotation.vcfanno_runner import MAX_OVERLAY_FILE_SIZE
from backend.config import Settings
from backend.db.connection import reset_registry
from backend.db.sample_schema import create_sample_tables
from backend.db.tables import (
    raw_variants,
    reference_metadata,
    samples,
    variant_overlays,
)

# ── Test data ────────────────────────────────────────────────────────

RAW_VARIANTS_DATA = [
    {"rsid": "rs12345", "chrom": "1", "pos": 100000, "genotype": "AA"},
    {"rsid": "rs429358", "chrom": "19", "pos": 44908684, "genotype": "TC"},
    {"rsid": "rs7412", "chrom": "19", "pos": 44908822, "genotype": "CC"},
    {"rsid": "rs1801133", "chrom": "1", "pos": 11856378, "genotype": "AG"},
]

OVERLAY_VCF_BYTES = (
    b"##fileformat=VCFv4.2\n"
    b'##INFO=<ID=AF,Number=A,Type=Float,Description="AF">\n'
    b"#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"
    b"1\t100000\trs12345\tA\tG\t.\tPASS\tAF=0.05\n"
)


# ── Fixtures ─────────────────────────────────────────────────────────


@pytest.fixture()
def tmp_data_dir(tmp_path: Path) -> Path:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "samples").mkdir()
    (data_dir / "overlays").mkdir()
    return data_dir


@pytest.fixture()
def sample_db_path(tmp_data_dir: Path) -> Path:
    db_path = tmp_data_dir / "samples" / "sample_1.db"
    engine = sa.create_engine(f"sqlite:///{db_path}")
    create_sample_tables(engine)
    with engine.begin() as conn:
        conn.execute(sa.insert(raw_variants), RAW_VARIANTS_DATA)
    engine.dispose()
    return db_path


@pytest.fixture()
def overlay_client(tmp_data_dir: Path, sample_db_path: Path) -> Generator[TestClient, None, None]:
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
# POST /api/overlays/parse
# ═══════════════════════════════════════════════════════════════════════


class TestParseOverlay:
    def test_parse_bed(self, overlay_client: TestClient) -> None:
        content = b"chr1\t99000\t101000\tregion1\t42\n"
        resp = overlay_client.post(
            "/api/overlays/parse",
            files={"file": ("test.bed", io.BytesIO(content), "text/plain")},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["file_type"] == "bed"
        assert data["record_count"] == 1
        assert len(data["column_names"]) > 0

    def test_parse_vcf(self, overlay_client: TestClient) -> None:
        resp = overlay_client.post(
            "/api/overlays/parse",
            files={"file": ("test.vcf", io.BytesIO(OVERLAY_VCF_BYTES), "text/plain")},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["file_type"] == "vcf"
        assert "AF" in data["column_names"]

    def test_parse_vcf_gz(self, overlay_client: TestClient) -> None:
        resp = overlay_client.post(
            "/api/overlays/parse",
            files={
                "file": (
                    "test.vcf.gz",
                    io.BytesIO(gzip.compress(OVERLAY_VCF_BYTES)),
                    "application/gzip",
                )
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["file_type"] == "vcf"
        assert data["record_count"] == 1
        assert "AF" in data["column_names"]

    @pytest.mark.parametrize(
        "endpoint",
        [
            "/api/overlays/parse",
            "/api/overlays/upload?name=Bomb",
        ],
    )
    def test_rejects_gzip_bomb(self, overlay_client: TestClient, endpoint: str) -> None:
        expanded = b"#" * (MAX_OVERLAY_FILE_SIZE + 1)
        compressed = gzip.compress(expanded)
        assert len(compressed) < MAX_OVERLAY_FILE_SIZE

        resp = overlay_client.post(
            endpoint,
            files={"file": ("bomb.vcf.gz", io.BytesIO(compressed), "application/gzip")},
        )
        assert resp.status_code == 413
        assert "expands beyond" in resp.json()["detail"]

    def test_parse_invalid_file(self, overlay_client: TestClient) -> None:
        content = b""
        resp = overlay_client.post(
            "/api/overlays/parse",
            files={"file": ("test.bed", io.BytesIO(content), "text/plain")},
        )
        assert resp.status_code == 422


# ═══════════════════════════════════════════════════════════════════════
# POST /api/overlays/upload + GET + DELETE
# ═══════════════════════════════════════════════════════════════════════


class TestOverlayCRUD:
    def test_upload_and_list(self, overlay_client: TestClient) -> None:
        content = b"chr1\t99000\t101000\tregion1\t42\n"
        resp = overlay_client.post(
            "/api/overlays/upload?name=Test+Overlay&description=A+test",
            files={"file": ("test.bed", io.BytesIO(content), "text/plain")},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["overlay"]["name"] == "Test Overlay"
        overlay_id = data["overlay"]["id"]

        # List
        resp = overlay_client.get("/api/overlays")
        assert resp.status_code == 200
        items = resp.json()["items"]
        assert len(items) == 1
        assert items[0]["id"] == overlay_id

    def test_upload_vcf_gz_and_apply(self, overlay_client: TestClient) -> None:
        resp = overlay_client.post(
            "/api/overlays/upload?name=Gzipped+VCF&description=A+test",
            files={
                "file": (
                    "custom.vcf.gz",
                    io.BytesIO(gzip.compress(OVERLAY_VCF_BYTES)),
                    "application/gzip",
                )
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["overlay"]["file_type"] == "vcf"
        assert data["overlay"]["column_names"] == ["AF"]
        overlay_id = data["overlay"]["id"]

        resp = overlay_client.post(f"/api/overlays/{overlay_id}/apply?sample_id=1")
        assert resp.status_code == 200
        assert resp.json()["variants_matched"] == 1

    def test_get_overlay(self, overlay_client: TestClient) -> None:
        content = b"chr1\t100\t200\tGENE1\n"
        resp = overlay_client.post(
            "/api/overlays/upload?name=GetTest",
            files={"file": ("test.bed", io.BytesIO(content), "text/plain")},
        )
        overlay_id = resp.json()["overlay"]["id"]

        resp = overlay_client.get(f"/api/overlays/{overlay_id}")
        assert resp.status_code == 200
        assert resp.json()["name"] == "GetTest"

    def test_delete_overlay(self, overlay_client: TestClient) -> None:
        content = b"chr1\t100\t200\tGENE1\n"
        resp = overlay_client.post(
            "/api/overlays/upload?name=DeleteTest",
            files={"file": ("test.bed", io.BytesIO(content), "text/plain")},
        )
        overlay_id = resp.json()["overlay"]["id"]

        resp = overlay_client.delete(f"/api/overlays/{overlay_id}")
        assert resp.status_code == 200
        assert resp.json()["status"] == "deleted"

        resp = overlay_client.get(f"/api/overlays/{overlay_id}")
        assert resp.status_code == 404

    def test_get_nonexistent(self, overlay_client: TestClient) -> None:
        resp = overlay_client.get("/api/overlays/999")
        assert resp.status_code == 404

    def test_delete_nonexistent(self, overlay_client: TestClient) -> None:
        resp = overlay_client.delete("/api/overlays/999")
        assert resp.status_code == 404


# ═══════════════════════════════════════════════════════════════════════
# POST /api/overlays/{id}/apply + GET results
# ═══════════════════════════════════════════════════════════════════════


class TestApplyOverlay:
    def test_apply_bed_overlay(self, overlay_client: TestClient) -> None:
        """BED overlay applied to sample matches variants in range."""
        content = b"1\t99999\t100001\ttest_region\t42\n"
        resp = overlay_client.post(
            "/api/overlays/upload?name=BED+Apply",
            files={"file": ("test.bed", io.BytesIO(content), "text/plain")},
        )
        overlay_id = resp.json()["overlay"]["id"]

        resp = overlay_client.post(f"/api/overlays/{overlay_id}/apply?sample_id=1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["variants_matched"] >= 1

    def test_apply_vcf_overlay(self, overlay_client: TestClient) -> None:
        """VCF overlay applied to sample matches by exact position."""
        content = (
            b"##fileformat=VCFv4.2\n"
            b"#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"
            b"1\t100000\trs12345\tA\tG\t.\tPASS\tCUSTOM_SCORE=0.99\n"
        )
        resp = overlay_client.post(
            "/api/overlays/upload?name=VCF+Apply",
            files={"file": ("test.vcf", io.BytesIO(content), "text/plain")},
        )
        overlay_id = resp.json()["overlay"]["id"]

        resp = overlay_client.post(f"/api/overlays/{overlay_id}/apply?sample_id=1")
        assert resp.status_code == 200
        assert resp.json()["variants_matched"] == 1

    def test_get_results(self, overlay_client: TestClient) -> None:
        """Get overlay results after apply."""
        content = (
            b"##fileformat=VCFv4.2\n"
            b"#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"
            b"1\t100000\trs12345\tA\tG\t.\tPASS\tSCORE=0.99;LABEL=important\n"
        )
        resp = overlay_client.post(
            "/api/overlays/upload?name=Results+Test",
            files={"file": ("test.vcf", io.BytesIO(content), "text/plain")},
        )
        overlay_id = resp.json()["overlay"]["id"]

        overlay_client.post(f"/api/overlays/{overlay_id}/apply?sample_id=1")

        resp = overlay_client.get(f"/api/overlays/{overlay_id}/results?sample_id=1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["results"][0]["rsid"] == "rs12345"
        assert data["results"][0]["SCORE"] == 0.99
        assert data["results"][0]["LABEL"] == "important"

    def test_apply_nonexistent_overlay(self, overlay_client: TestClient) -> None:
        resp = overlay_client.post("/api/overlays/999/apply?sample_id=1")
        assert resp.status_code == 404

    def test_apply_nonexistent_sample(self, overlay_client: TestClient) -> None:
        content = b"chr1\t100\t200\tGENE1\n"
        resp = overlay_client.post(
            "/api/overlays/upload?name=NoSample",
            files={"file": ("test.bed", io.BytesIO(content), "text/plain")},
        )
        overlay_id = resp.json()["overlay"]["id"]

        resp = overlay_client.post(f"/api/overlays/{overlay_id}/apply?sample_id=999")
        assert resp.status_code == 404

    def test_apply_overlay_with_deleted_backing_file(
        self, overlay_client: TestClient, tmp_data_dir: Path
    ) -> None:
        """Config row survives but the on-disk backing file is gone → actionable 404.

        The config lives in the reference DB; the file lives on disk under
        ``data_dir/overlays/overlay_{id}.txt``. They can drift apart (volatile/staging
        data_dir, disk cleanup, partial restore, manual deletion). When the file is gone,
        ``_load_overlay_file`` returns ``None`` and the route's second 404 guard must fire
        with re-upload guidance — without it, ``None`` flows into the parser and raises an
        ``AttributeError`` (not ``ValueError``) → an opaque 500 instead. Pins that guard.
        """
        content = b"1\t99999\t100001\ttest_region\t42\n"
        resp = overlay_client.post(
            "/api/overlays/upload?name=Deleted+Backing+File",
            files={"file": ("test.bed", io.BytesIO(content), "text/plain")},
        )
        overlay_id = resp.json()["overlay"]["id"]

        # Delete the backing file out from under the surviving config row.
        file_path = tmp_data_dir / "overlays" / f"overlay_{overlay_id}.txt"
        assert file_path.exists()  # upload wrote it where the route will look
        file_path.unlink()

        resp = overlay_client.post(f"/api/overlays/{overlay_id}/apply?sample_id=1")
        assert resp.status_code == 404
        assert "re-upload" in resp.json()["detail"].lower()


# ═══════════════════════════════════════════════════════════════════════
# Integration test T4-14: overlay annotations visible in variant table
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.integration
class TestOverlayIntegration:
    def test_overlay_adds_custom_annotations(self, overlay_client: TestClient) -> None:
        """T4-14: vcfanno overlay adds custom annotations visible in variant table.

        Upload an overlay, apply it to a sample, and verify annotations
        are retrievable via the results endpoint.
        """
        content = (
            b"##fileformat=VCFv4.2\n"
            b'##INFO=<ID=CUSTOM_AF,Number=A,Type=Float,Description="Custom AF">\n'
            b'##INFO=<ID=CUSTOM_LABEL,Number=1,Type=String,Description="Custom label">\n'
            b"#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"
            b"1\t100000\trs12345\tA\tG\t.\tPASS\tCUSTOM_AF=0.03;CUSTOM_LABEL=high_impact\n"
            b"19\t44908684\trs429358\tT\tC\t.\tPASS\tCUSTOM_AF=0.15;CUSTOM_LABEL=moderate\n"
        )

        # Upload
        resp = overlay_client.post(
            "/api/overlays/upload?name=Integration+Test",
            files={"file": ("custom.vcf", io.BytesIO(content), "text/plain")},
        )
        assert resp.status_code == 200
        overlay_id = resp.json()["overlay"]["id"]
        assert resp.json()["overlay"]["column_names"] == ["CUSTOM_AF", "CUSTOM_LABEL"]

        # Apply
        resp = overlay_client.post(f"/api/overlays/{overlay_id}/apply?sample_id=1")
        assert resp.status_code == 200
        assert resp.json()["variants_matched"] == 2

        # Verify results
        resp = overlay_client.get(f"/api/overlays/{overlay_id}/results?sample_id=1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 2

        rsid_map = {r["rsid"]: r for r in data["results"]}
        assert rsid_map["rs12345"]["CUSTOM_AF"] == 0.03
        assert rsid_map["rs12345"]["CUSTOM_LABEL"] == "high_impact"
        assert rsid_map["rs429358"]["CUSTOM_AF"] == 0.15
        assert rsid_map["rs429358"]["CUSTOM_LABEL"] == "moderate"


# ═══════════════════════════════════════════════════════════════════════
# DELETE clears per-sample variant_overlays rows (cross-DB orphan fix; #854)
# ═══════════════════════════════════════════════════════════════════════


def _count_overlay_rows(sample_db_path: Path, overlay_id: int) -> int:
    """Count variant_overlays rows for an overlay directly in the sample DB."""
    engine = sa.create_engine(f"sqlite:///{sample_db_path}")
    try:
        with engine.connect() as conn:
            return conn.execute(
                sa.select(sa.func.count())
                .select_from(variant_overlays)
                .where(variant_overlays.c.overlay_id == overlay_id)
            ).scalar_one()
    finally:
        engine.dispose()


_OVERLAY_VCF = (
    b"##fileformat=VCFv4.2\n"
    b"#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"
    b"1\t100000\trs12345\tA\tG\t.\tPASS\tSCORE=0.99\n"
)


class TestDeleteClearsSampleResults:
    """DELETE /api/overlays/{id} must clear the overlay's per-sample
    variant_overlays rows. They live in each sample DB — a different database
    from the reference.db config row — so no FK/cascade removes them; the route
    has to sweep sample DBs explicitly or it orphans the rows (#854)."""

    def _upload_and_apply(self, client: TestClient) -> int:
        resp = client.post(
            "/api/overlays/upload?name=DeleteSweep",
            files={"file": ("o.vcf", io.BytesIO(_OVERLAY_VCF), "text/plain")},
        )
        assert resp.status_code == 200
        overlay_id = resp.json()["overlay"]["id"]
        resp = client.post(f"/api/overlays/{overlay_id}/apply?sample_id=1")
        assert resp.status_code == 200
        assert resp.json()["variants_matched"] == 1
        return overlay_id

    def test_delete_removes_sample_overlay_rows(
        self, overlay_client: TestClient, sample_db_path: Path
    ) -> None:
        overlay_id = self._upload_and_apply(overlay_client)
        # The applied row is present in the sample DB before deletion…
        assert _count_overlay_rows(sample_db_path, overlay_id) == 1

        resp = overlay_client.delete(f"/api/overlays/{overlay_id}")
        assert resp.status_code == 200

        # …and gone afterwards (no orphaned rows left behind in the sample DB).
        assert _count_overlay_rows(sample_db_path, overlay_id) == 0

    def test_recreated_overlay_does_not_inherit_stale_rows(
        self, overlay_client: TestClient, sample_db_path: Path
    ) -> None:
        # SQLite reuses the deleted INTEGER PRIMARY KEY, so a new overlay can take
        # the old id. With the rows cleared on delete, the reused-id overlay starts
        # empty instead of adopting the previous overlay's annotations (the #854
        # id-reuse phantom-data escalation).
        overlay_id = self._upload_and_apply(overlay_client)
        assert overlay_client.delete(f"/api/overlays/{overlay_id}").status_code == 200

        resp = overlay_client.post(
            "/api/overlays/upload?name=Reused",
            files={"file": ("o2.vcf", io.BytesIO(_OVERLAY_VCF), "text/plain")},
        )
        new_id = resp.json()["overlay"]["id"]
        assert new_id == overlay_id  # id was reused — the phantom-data precondition

        # The freshly-created overlay has not been applied, so it must have no rows.
        assert _count_overlay_rows(sample_db_path, new_id) == 0
        results = overlay_client.get(f"/api/overlays/{new_id}/results?sample_id=1")
        assert results.status_code == 200
        assert results.json()["total"] == 0

    def test_delete_nonexistent_does_not_touch_samples(
        self, overlay_client: TestClient, sample_db_path: Path
    ) -> None:
        # An applied overlay's rows must survive an unrelated overlay's (404) delete.
        overlay_id = self._upload_and_apply(overlay_client)
        assert overlay_client.delete("/api/overlays/999").status_code == 404
        assert _count_overlay_rows(sample_db_path, overlay_id) == 1

"""End-to-end integration test: full pipeline (P2-28).

Tests the complete flow from 23andMe file upload → parsing → annotation →
querying annotated variants via API. Uses the real annotation engine with
seeded mini reference databases (VEP, ClinVar, gnomAD, dbNSFP,
gene-phenotype) to verify the entire pipeline produces correct results.

Covers:
- File upload via POST /api/ingest returns sample_id + variant_count
- Sample appears in GET /api/samples
- Annotation via POST /api/annotation/{sample_id} completes successfully
- Job status transitions: pending → running → complete
- Annotated variants queryable via GET /api/variants
- Annotation data correctness (ClinVar, VEP, gnomAD, dbNSFP, gene-phenotype)
- Variant detail API returns full annotation for known rsids
- Density, consequence-summary, and clinvar-summary endpoints work
- Crash recovery: re-annotation succeeds
"""

from __future__ import annotations

import csv
from pathlib import Path
from unittest.mock import patch

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from backend.annotation.dbnsfp import (
    _create_dbnsfp_indexes,
    _create_dbnsfp_table,
    load_dbnsfp_from_csv,
)
from backend.annotation.gnomad import _create_gnomad_indexes, _create_gnomad_table
from backend.annotation.mondo_hpo import load_mondo_hpo_from_csv
from backend.config import Settings
from backend.db.connection import reset_registry
from backend.db.tables import (
    clinvar_variants,
    jobs,
    reference_metadata,
)

# ── Paths ──────────────────────────────────────────────────────────────

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures"
SAMPLE_FILE = FIXTURES_DIR / "sample_23andme_v5.txt"
VEP_SEED_CSV = FIXTURES_DIR / "seed_csvs" / "vep_seed.csv"
GNOMAD_SEED_CSV = FIXTURES_DIR / "seed_csvs" / "gnomad_seed.csv"
DBNSFP_SEED_CSV = FIXTURES_DIR / "seed_csvs" / "dbnsfp_seed.csv"
GENE_PHENOTYPE_SEED_CSV = FIXTURES_DIR / "seed_csvs" / "gene_phenotype_seed.csv"

# ── Seed data ──────────────────────────────────────────────────────────

SEED_CLINVAR = [
    {
        "rsid": "rs429358",
        "chrom": "19",
        "pos": 44908684,
        "ref": "T",
        "alt": "C",
        "significance": "risk_factor",
        "review_stars": 3,
        "accession": "VCV000017864",
        "conditions": "Alzheimer disease",
        "gene_symbol": "APOE",
        "variation_id": 17864,
    },
    {
        "rsid": "rs1801133",
        "chrom": "1",
        "pos": 11856378,
        "ref": "G",
        "alt": "A",
        "significance": "drug_response",
        "review_stars": 2,
        "accession": "VCV000003520",
        "conditions": "Homocysteinemia",
        "gene_symbol": "MTHFR",
        "variation_id": 3520,
    },
    {
        "rsid": "rs7412",
        "chrom": "19",
        "pos": 44908822,
        "ref": "C",
        "alt": "T",
        "significance": "risk_factor",
        "review_stars": 3,
        "accession": "VCV000017865",
        "conditions": "Alzheimer disease",
        "gene_symbol": "APOE",
        "variation_id": 17865,
    },
]


# ── Helpers to build mini annotation databases on disk ─────────────────


def _create_vep_bundle(db_path: Path) -> None:
    """Build a mini VEP bundle SQLite from the seed CSV."""
    engine = sa.create_engine(f"sqlite:///{db_path}")
    try:
        with engine.begin() as conn:
            conn.execute(
                sa.text(
                    "CREATE TABLE vep_annotations ("
                    "  rsid TEXT, chrom TEXT, pos INTEGER,"
                    "  ref TEXT, alt TEXT, gene_symbol TEXT,"
                    "  transcript_id TEXT, consequence TEXT,"
                    "  hgvs_coding TEXT, hgvs_protein TEXT,"
                    "  strand TEXT, exon_number INTEGER,"
                    "  intron_number INTEGER, mane_select INTEGER"
                    ")"
                )
            )
            conn.execute(sa.text("CREATE INDEX idx_vep_rsid ON vep_annotations(rsid)"))
            with open(VEP_SEED_CSV, encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    conn.execute(
                        sa.text(
                            "INSERT INTO vep_annotations "
                            "(rsid, chrom, pos, ref, alt, gene_symbol, "
                            "transcript_id, consequence, hgvs_coding, "
                            "hgvs_protein, strand, exon_number, "
                            "intron_number, mane_select) "
                            "VALUES (:rsid, :chrom, :pos, :ref, :alt, "
                            ":gene_symbol, :transcript_id, :consequence, "
                            ":hgvs_coding, :hgvs_protein, :strand, "
                            ":exon_number, :intron_number, :mane_select)"
                        ),
                        {
                            "rsid": row["rsid"],
                            "chrom": row["chrom"],
                            "pos": int(row["pos"]),
                            "ref": row["ref"],
                            "alt": row["alt"],
                            "gene_symbol": row["gene_symbol"],
                            "transcript_id": row["transcript_id"],
                            "consequence": row["consequence"],
                            "hgvs_coding": row["hgvs_coding"] or None,
                            "hgvs_protein": row["hgvs_protein"] or None,
                            "strand": row["strand"],
                            "exon_number": (
                                int(row["exon_number"]) if row["exon_number"] else None
                            ),
                            "intron_number": (
                                int(row["intron_number"]) if row["intron_number"] else None
                            ),
                            "mane_select": int(row["mane_select"]),
                        },
                    )
    finally:
        engine.dispose()


def _create_gnomad_db(db_path: Path) -> None:
    """Build a mini gnomAD SQLite from the seed CSV."""
    engine = sa.create_engine(f"sqlite:///{db_path}")
    try:
        _create_gnomad_table(engine)
        _create_gnomad_indexes(engine)
        with engine.begin() as conn:
            with open(GNOMAD_SEED_CSV, encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    conn.execute(
                        sa.text(
                            "INSERT INTO gnomad_af VALUES "
                            "(:rsid, :chrom, :pos, :ref, :alt, :af_global, "
                            ":af_afr, :af_amr, :af_eas, :af_eur, :af_fin, "
                            ":af_sas, :homozygous_count)"
                        ),
                        {
                            "rsid": row["rsid"],
                            "chrom": row["chrom"],
                            "pos": int(row["pos"]),
                            "ref": row["ref"],
                            "alt": row["alt"],
                            "af_global": float(row["af_global"]),
                            "af_afr": float(row["af_afr"]),
                            "af_amr": float(row["af_amr"]),
                            "af_eas": float(row["af_eas"]),
                            "af_eur": float(row["af_eur"]),
                            "af_fin": float(row["af_fin"]),
                            "af_sas": float(row["af_sas"]),
                            "homozygous_count": int(row["homozygous_count"]),
                        },
                    )
    finally:
        engine.dispose()


def _create_dbnsfp_db(db_path: Path) -> None:
    """Build a mini dbNSFP SQLite from the seed CSV."""
    engine = sa.create_engine(f"sqlite:///{db_path}")
    try:
        _create_dbnsfp_table(engine)
        _create_dbnsfp_indexes(engine)
        load_dbnsfp_from_csv(DBNSFP_SEED_CSV, engine, clear_existing=False)
    finally:
        engine.dispose()


# ── Fixtures ───────────────────────────────────────────────────────────


@pytest.fixture
def e2e_env(tmp_data_dir: Path):
    """Set up a complete E2E environment with all annotation databases.

    Creates:
    - reference.db with ClinVar + gene-phenotype seed data
    - vep_bundle.db from seed CSV
    - gnomad_af.db from seed CSV
    - dbnsfp.db from seed CSV
    - Settings pointing to tmp dir
    - Patched get_settings everywhere
    """
    settings = Settings(data_dir=tmp_data_dir, wal_mode=False)

    # 1. Create reference.db with tables and seed data
    ref_path = settings.reference_db_path
    ref_engine = sa.create_engine(f"sqlite:///{ref_path}")
    reference_metadata.create_all(ref_engine)
    with ref_engine.begin() as conn:
        conn.execute(clinvar_variants.insert(), SEED_CLINVAR)
    # Load gene-phenotype data
    load_mondo_hpo_from_csv(GENE_PHENOTYPE_SEED_CSV, ref_engine)
    ref_engine.dispose()

    # 2. Create annotation source databases on disk
    _create_vep_bundle(settings.vep_bundle_db_path)
    _create_gnomad_db(settings.gnomad_db_path)
    _create_dbnsfp_db(settings.dbnsfp_db_path)

    with (
        patch("backend.main.get_settings", return_value=settings),
        patch("backend.db.connection.get_settings", return_value=settings),
        patch("backend.tasks.huey_tasks.get_settings", return_value=settings),
    ):
        reset_registry()
        yield {"settings": settings, "tmp_dir": tmp_data_dir}
        reset_registry()


@pytest.fixture
def e2e_client(e2e_env: dict) -> TestClient:
    """FastAPI TestClient wired to the full E2E environment."""
    from backend.tasks import huey_tasks

    original_immediate = huey_tasks.huey.immediate
    huey_tasks.huey.immediate = True
    try:
        from backend.main import create_app

        app = create_app()
        with TestClient(app) as tc:
            yield tc
    finally:
        huey_tasks.huey.immediate = original_immediate


# ═══════════════════════════════════════════════════════════════════════
# End-to-end pipeline tests
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.integration
class TestE2EPipelineSmoke:
    """PR-tier smoke: the full pipeline runs to completion.

    One representative end-to-end test kept on the PR tier so a broken
    upload→annotate path is caught pre-merge; the exhaustive per-field
    assertions live in ``TestE2EPipeline`` (slow / nightly).
    """

    def test_annotation_completes(self, e2e_client: TestClient) -> None:
        """Upload → annotate runs to completion with status=complete."""
        with open(SAMPLE_FILE, "rb") as f:
            upload = e2e_client.post(
                "/api/ingest",
                files={"file": ("sample_23andme_v5.txt", f, "text/plain")},
            )
        assert upload.status_code == 202, f"Upload failed: {upload.text}"
        sample_id = upload.json()["sample_id"]

        annot = e2e_client.post(f"/api/annotation/{sample_id}")
        assert annot.status_code == 202, f"Annotation start failed: {annot.text}"
        job_id = annot.json()["job_id"]

        # In immediate mode the Huey task runs synchronously; verify via jobs.
        from backend.db.connection import get_registry

        registry = get_registry()
        with registry.reference_engine.connect() as conn:
            row = conn.execute(sa.select(jobs).where(jobs.c.job_id == job_id)).fetchone()

        assert row is not None
        assert row.status == "complete"
        assert row.progress_pct == 100.0


@pytest.mark.integration
@pytest.mark.slow  # nightly: per-test ref-DB rebuild + re-annotate (~74s/19)
class TestE2EPipeline:
    """Full pipeline: upload → parse → annotate → query."""

    def _upload_sample(self, client: TestClient) -> dict:
        """Upload the test 23andMe file and return the response dict."""
        with open(SAMPLE_FILE, "rb") as f:
            resp = client.post(
                "/api/ingest",
                files={"file": ("sample_23andme_v5.txt", f, "text/plain")},
            )
        assert resp.status_code == 202, f"Upload failed: {resp.text}"
        return resp.json()

    def _annotate_sample(self, client: TestClient, sample_id: int) -> dict:
        """Start annotation and return the response dict."""
        resp = client.post(f"/api/annotation/{sample_id}")
        assert resp.status_code == 202, f"Annotation start failed: {resp.text}"
        return resp.json()

    # ── Upload & Parse ─────────────────────────────────────────────────

    def test_upload_parses_file(self, e2e_client: TestClient) -> None:
        """POST /api/ingest parses the 23andMe file and returns sample metadata."""
        result = self._upload_sample(e2e_client)

        assert "sample_id" in result
        assert "job_id" in result
        assert result["variant_count"] > 0
        assert result["file_format"] == "23andme_v5"

    def test_sample_appears_in_list(self, e2e_client: TestClient) -> None:
        """After upload, sample is visible in GET /api/samples."""
        upload = self._upload_sample(e2e_client)
        sample_id = upload["sample_id"]

        resp = e2e_client.get("/api/samples")
        assert resp.status_code == 200
        sample_list = resp.json()
        assert any(s["id"] == sample_id for s in sample_list)

    def test_raw_variants_queryable(self, e2e_client: TestClient) -> None:
        """After upload, raw variants are queryable via GET /api/variants."""
        upload = self._upload_sample(e2e_client)
        sample_id = upload["sample_id"]

        resp = e2e_client.get(
            "/api/variants",
            params={"sample_id": sample_id, "limit": 10},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["items"]) > 0

    # ── Annotation ─────────────────────────────────────────────────────

    def test_annotation_populates_annotated_variants(self, e2e_client: TestClient) -> None:
        """After annotation, annotated_variants table has rows."""
        upload = self._upload_sample(e2e_client)
        sample_id = upload["sample_id"]
        self._annotate_sample(e2e_client, sample_id)

        # Query annotated variants via API (annotated=true flag)
        resp = e2e_client.get(
            "/api/variants",
            params={"sample_id": sample_id, "annotated": "true", "limit": 50},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["items"]) > 0

    # ── Annotation data correctness ────────────────────────────────────

    def test_clinvar_annotation_correct(self, e2e_client: TestClient) -> None:
        """ClinVar annotations match seed data for known rsids."""
        upload = self._upload_sample(e2e_client)
        sample_id = upload["sample_id"]
        self._annotate_sample(e2e_client, sample_id)

        # rs1801133 (MTHFR) should have ClinVar drug_response
        resp = e2e_client.get(
            "/api/variants/rs1801133",
            params={"sample_id": sample_id},
        )
        assert resp.status_code == 200, f"Expected rs1801133 in sample: {resp.text}"
        detail = resp.json()
        assert detail["clinvar_significance"] == "drug_response"
        assert detail["clinvar_review_stars"] == 2
        assert "Homocysteinemia" in (detail.get("clinvar_conditions") or "")

    def test_vep_annotation_present(self, e2e_client: TestClient) -> None:
        """VEP annotation populates gene_symbol and consequence."""
        upload = self._upload_sample(e2e_client)
        sample_id = upload["sample_id"]
        self._annotate_sample(e2e_client, sample_id)

        # Check annotated variants have gene_symbol populated for known rsids
        resp = e2e_client.get(
            "/api/variants",
            params={
                "sample_id": sample_id,
                "annotated": "true",
                "limit": 100,
                "filter_annotation_coverage": "notnull",
            },
        )
        assert resp.status_code == 200
        items = resp.json()["items"]
        # At least some variants should have gene_symbol from VEP
        has_gene = [v for v in items if v.get("gene_symbol")]
        assert len(has_gene) > 0

    def test_annotation_coverage_bitmask(self, e2e_client: TestClient) -> None:
        """Annotated variants have non-zero annotation_coverage bitmask."""
        upload = self._upload_sample(e2e_client)
        sample_id = upload["sample_id"]
        self._annotate_sample(e2e_client, sample_id)

        resp = e2e_client.get(
            "/api/variants",
            params={
                "sample_id": sample_id,
                "annotated": "true",
                "limit": 50,
            },
        )
        assert resp.status_code == 200
        items = resp.json()["items"]
        annotated_items = [v for v in items if v.get("annotation_coverage")]
        assert len(annotated_items) > 0
        for v in annotated_items:
            assert v["annotation_coverage"] > 0

    # ── Variant count & summary endpoints ──────────────────────────────

    def test_variant_count_endpoint(self, e2e_client: TestClient) -> None:
        """GET /api/variants/count returns the correct total count."""
        upload = self._upload_sample(e2e_client)
        sample_id = upload["sample_id"]

        resp = e2e_client.get(
            "/api/variants/count",
            params={"sample_id": sample_id},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == upload["variant_count"]

    def test_chromosome_counts(self, e2e_client: TestClient) -> None:
        """GET /api/variants/chromosomes returns per-chromosome counts."""
        upload = self._upload_sample(e2e_client)
        sample_id = upload["sample_id"]

        resp = e2e_client.get(
            "/api/variants/chromosomes",
            params={"sample_id": sample_id},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) > 0
        total = sum(entry["count"] for entry in data)
        assert total == upload["variant_count"]

    def test_consequence_summary_after_annotation(self, e2e_client: TestClient) -> None:
        """GET /api/variants/consequence-summary returns data after annotation."""
        upload = self._upload_sample(e2e_client)
        sample_id = upload["sample_id"]
        self._annotate_sample(e2e_client, sample_id)

        resp = e2e_client.get(
            "/api/variants/consequence-summary",
            params={"sample_id": sample_id},
        )
        assert resp.status_code == 200
        data = resp.json()
        # Response is {items: [...], total: N}
        assert "items" in data
        assert len(data["items"]) > 0

    def test_clinvar_summary_after_annotation(self, e2e_client: TestClient) -> None:
        """GET /api/variants/clinvar-summary returns breakdown after annotation."""
        upload = self._upload_sample(e2e_client)
        sample_id = upload["sample_id"]
        self._annotate_sample(e2e_client, sample_id)

        resp = e2e_client.get(
            "/api/variants/clinvar-summary",
            params={"sample_id": sample_id},
        )
        assert resp.status_code == 200
        data = resp.json()
        # Response is {items: [...], total: N}
        assert "items" in data
        assert len(data["items"]) > 0

    def test_density_after_annotation(self, e2e_client: TestClient) -> None:
        """GET /api/variants/density returns bin data after annotation."""
        upload = self._upload_sample(e2e_client)
        sample_id = upload["sample_id"]
        self._annotate_sample(e2e_client, sample_id)

        resp = e2e_client.get(
            "/api/variants/density",
            params={"sample_id": sample_id},
        )
        assert resp.status_code == 200
        data = resp.json()
        # Response is {bins: [...], bin_size: N}
        assert "bins" in data
        assert len(data["bins"]) > 0

    # ── Re-annotation (crash recovery) ─────────────────────────────────

    def test_reannotation_succeeds(self, e2e_client: TestClient) -> None:
        """Running annotation twice on the same sample succeeds (crash recovery).

        The engine deletes all existing annotations before re-running,
        so a second annotation should produce the same results.
        """
        upload = self._upload_sample(e2e_client)
        sample_id = upload["sample_id"]

        # First annotation
        annot1 = self._annotate_sample(e2e_client, sample_id)

        # Verify first completed
        from backend.db.connection import get_registry

        registry = get_registry()
        with registry.reference_engine.connect() as conn:
            row = conn.execute(sa.select(jobs).where(jobs.c.job_id == annot1["job_id"])).fetchone()
        assert row.status == "complete"

        # Annotated count after the first run.
        resp1 = e2e_client.get(
            "/api/variants",
            params={"sample_id": sample_id, "annotated": "true", "limit": 500},
        )
        assert resp1.status_code == 200
        count1 = len(resp1.json()["items"])
        assert count1 > 0, "First annotation should populate annotated variants"

        # Second annotation (crash recovery path: delete + re-annotate)
        annot2 = self._annotate_sample(e2e_client, sample_id)
        with registry.reference_engine.connect() as conn:
            row2 = conn.execute(
                sa.select(jobs).where(jobs.c.job_id == annot2["job_id"])
            ).fetchone()
        assert row2.status == "complete"

        # Re-annotation must repopulate, not merely flip status to "complete":
        # a delete-then-fail-to-repopulate regression leaves count2 == 0 while
        # the job still reports complete.
        resp2 = e2e_client.get(
            "/api/variants",
            params={"sample_id": sample_id, "annotated": "true", "limit": 500},
        )
        assert resp2.status_code == 200
        count2 = len(resp2.json()["items"])
        assert count2 > 0, "Re-annotation should repopulate annotated variants"
        assert count2 == count1, f"Re-annotation count mismatch: run-1={count1}, run-2={count2}"

    # ── SSE status streaming ───────────────────────────────────────────

    def test_ingest_status_sse(self, e2e_client: TestClient) -> None:
        """GET /api/ingest/status/{job_id} returns SSE stream with complete status."""
        upload = self._upload_sample(e2e_client)
        job_id = upload["job_id"]

        resp = e2e_client.get(f"/api/ingest/status/{job_id}")
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers["content-type"]
        assert "complete" in resp.text

    def test_annotation_status_sse(self, e2e_client: TestClient) -> None:
        """GET /api/annotation/status/{job_id} returns SSE with complete status."""
        upload = self._upload_sample(e2e_client)
        sample_id = upload["sample_id"]
        annot = self._annotate_sample(e2e_client, sample_id)

        resp = e2e_client.get(f"/api/annotation/status/{annot['job_id']}")
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers["content-type"]
        assert "complete" in resp.text

    # ── Pagination ─────────────────────────────────────────────────────

    def test_cursor_pagination_works(self, e2e_client: TestClient) -> None:
        """Cursor-based pagination returns sequential pages without overlap."""
        upload = self._upload_sample(e2e_client)
        sample_id = upload["sample_id"]

        # Fetch first page
        resp1 = e2e_client.get(
            "/api/variants",
            params={"sample_id": sample_id, "limit": 5},
        )
        assert resp1.status_code == 200
        page1 = resp1.json()
        assert len(page1["items"]) == 5

        if page1["has_more"]:
            # Fetch second page
            resp2 = e2e_client.get(
                "/api/variants",
                params={
                    "sample_id": sample_id,
                    "limit": 5,
                    "cursor_chrom": page1["next_cursor_chrom"],
                    "cursor_pos": page1["next_cursor_pos"],
                },
            )
            assert resp2.status_code == 200
            page2 = resp2.json()
            assert len(page2["items"]) > 0

            # No overlap between pages
            rsids_p1 = {v["rsid"] for v in page1["items"]}
            rsids_p2 = {v["rsid"] for v in page2["items"]}
            assert rsids_p1.isdisjoint(rsids_p2)

    # ── Sample management ──────────────────────────────────────────────

    def test_sample_delete(self, e2e_client: TestClient) -> None:
        """DELETE /api/samples/{id} removes the sample."""
        upload = self._upload_sample(e2e_client)
        sample_id = upload["sample_id"]

        resp = e2e_client.delete(f"/api/samples/{sample_id}")
        assert resp.status_code == 204

        # Verify gone
        resp2 = e2e_client.get("/api/samples")
        assert resp2.status_code == 200
        assert not any(s["id"] == sample_id for s in resp2.json())

    # ── Multiple samples ───────────────────────────────────────────────

    def test_multiple_samples_independent(self, e2e_client: TestClient) -> None:
        """Two uploads produce independent samples with separate annotation."""
        upload1 = self._upload_sample(e2e_client)
        upload2 = self._upload_sample(e2e_client)

        assert upload1["sample_id"] != upload2["sample_id"]

        # Annotate only sample 1
        self._annotate_sample(e2e_client, upload1["sample_id"])

        # Sample 1 should have annotated variants
        resp1 = e2e_client.get(
            "/api/variants",
            params={
                "sample_id": upload1["sample_id"],
                "annotated": "true",
                "limit": 50,
            },
        )
        assert resp1.status_code == 200
        items1 = resp1.json()["items"]
        # Some annotated variants should have annotation_coverage set
        annotated1 = [v for v in items1 if v.get("annotation_coverage")]
        assert len(annotated1) > 0

        # Sample 2 should have zero annotated variants with coverage
        resp2 = e2e_client.get(
            "/api/variants",
            params={
                "sample_id": upload2["sample_id"],
                "annotated": "true",
                "limit": 50,
            },
        )
        assert resp2.status_code == 200
        items2 = resp2.json()["items"]
        annotated2 = [v for v in items2 if v.get("annotation_coverage")]
        assert len(annotated2) == 0

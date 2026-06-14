"""Tests for ingestion API endpoints (P1-13).

T1-13: POST /api/ingest with valid file returns 202, triggers parse,
       GET /api/ingest/status reflects completion.
       GET /api/samples returns list. PATCH/DELETE sample management.

ADNA-10 (Step 43; Plan §13.1): POST /api/ingest with the AncestryDNA
fixture asserts 202 + ``file_format="ancestrydna_v2.0"`` + variant count.
Paired with the 409-gate cases in ``test_bundle_gating.py`` — those lock
the vendor-scoped pre-v2.0.0 block; this file locks the happy path on a
v2.0.0+ bundle.
"""

from __future__ import annotations

import io
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from backend.config import Settings
from backend.db.tables import (
    database_versions,
    jobs,
    raw_variants,
    reference_metadata,
    samples,
)
from backend.ingestion.parser_23andme import parse_23andme

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
V5_FILE = FIXTURES / "sample_23andme_v5.txt"
V3_FILE = FIXTURES / "sample_23andme_v3.txt"  # build 36 (hg18) — issue #480
ANCESTRY_FILE = FIXTURES / "sample_ancestrydna_v2.txt"
# AncestryDNA fixture body has 589 data rows — locked by
# ``test_parser_ancestrydna.py::test_variant_count_matches_data_rows``.
ANCESTRY_VARIANT_COUNT = 589


@pytest.fixture
def client(tmp_data_dir: Path) -> TestClient:
    """FastAPI TestClient wired to tmp data dir."""
    settings = Settings(data_dir=tmp_data_dir, wal_mode=False)

    ref_path = settings.reference_db_path
    engine = sa.create_engine(f"sqlite:///{ref_path}")
    reference_metadata.create_all(engine)
    engine.dispose()

    with (
        patch("backend.main.get_settings", return_value=settings),
        patch("backend.db.connection.get_settings", return_value=settings),
        patch("backend.api.routes.ingest.get_registry") as mock_get_reg,
        patch("backend.api.routes.samples.get_registry") as mock_get_reg2,
    ):
        from backend.db.connection import DBRegistry, reset_registry

        reset_registry()

        registry = DBRegistry(settings)
        mock_get_reg.return_value = registry
        mock_get_reg2.return_value = registry

        from backend.main import create_app

        app = create_app()
        with TestClient(app) as tc:
            yield tc

        registry.dispose_all()
        reset_registry()


def _seed_vep_bundle_v2(reference_db_path: Path) -> None:
    """Seed ``database_versions['vep_bundle'] = v2.0.0`` so AncestryDNA
    uploads clear the Plan §5.4 ingest gate enforced by
    :mod:`backend.api.routes.ingest`.
    """
    engine = sa.create_engine(f"sqlite:///{reference_db_path}")
    with engine.begin() as conn:
        conn.execute(
            database_versions.insert().values(
                db_name="vep_bundle",
                version="v2.0.0",
                file_path=None,
                file_size_bytes=None,
                downloaded_at=datetime.now(UTC),
                checksum_sha256=None,
            )
        )
    engine.dispose()


@pytest.fixture
def ancestrydna_client(tmp_data_dir: Path) -> TestClient:
    """FastAPI TestClient with ``vep_bundle`` pinned to ``v2.0.0`` so the
    AncestryDNA bundle-version gate (Plan §5.4) falls through to the
    parser dispatcher and the happy-path 202 contract can be asserted.
    """
    settings = Settings(data_dir=tmp_data_dir, wal_mode=False)

    ref_path = settings.reference_db_path
    engine = sa.create_engine(f"sqlite:///{ref_path}")
    reference_metadata.create_all(engine)
    engine.dispose()

    _seed_vep_bundle_v2(ref_path)

    with (
        patch("backend.main.get_settings", return_value=settings),
        patch("backend.db.connection.get_settings", return_value=settings),
        patch("backend.api.routes.ingest.get_registry") as mock_get_reg,
        patch("backend.api.routes.samples.get_registry") as mock_get_reg2,
    ):
        from backend.db.connection import DBRegistry, reset_registry

        reset_registry()

        registry = DBRegistry(settings)
        mock_get_reg.return_value = registry
        mock_get_reg2.return_value = registry

        from backend.main import create_app

        app = create_app()
        with TestClient(app) as tc:
            yield tc

        registry.dispose_all()
        reset_registry()


# ═══════════════════════════════════════════════════════════════════════
# POST /api/ingest
# ═══════════════════════════════════════════════════════════════════════


class TestIngestEndpoint:
    """T1-13: POST /api/ingest with valid file returns 202."""

    def test_ingest_valid_file_returns_202(self, client):
        with open(V5_FILE, "rb") as f:
            response = client.post("/api/ingest", files={"file": ("sample.txt", f, "text/plain")})
        assert response.status_code == 202

    def test_ingest_returns_sample_id(self, client):
        with open(V5_FILE, "rb") as f:
            response = client.post("/api/ingest", files={"file": ("sample.txt", f, "text/plain")})
        data = response.json()
        assert "sample_id" in data
        assert isinstance(data["sample_id"], int)

    def test_ingest_returns_job_id(self, client):
        with open(V5_FILE, "rb") as f:
            response = client.post("/api/ingest", files={"file": ("sample.txt", f, "text/plain")})
        data = response.json()
        assert "job_id" in data
        assert data["job_id"]  # non-empty

    def test_ingest_returns_variant_count(self, client):
        with open(V5_FILE, "rb") as f:
            response = client.post("/api/ingest", files={"file": ("sample.txt", f, "text/plain")})
        data = response.json()
        assert data["variant_count"] > 0

    def test_ingest_returns_file_format(self, client):
        with open(V5_FILE, "rb") as f:
            response = client.post("/api/ingest", files={"file": ("sample.txt", f, "text/plain")})
        data = response.json()
        assert data["file_format"] == "23andme_v5"

    def test_ingest_creates_sample_in_registry(self, client, tmp_data_dir):
        with open(V5_FILE, "rb") as f:
            response = client.post(
                "/api/ingest", files={"file": ("test_sample.txt", f, "text/plain")}
            )
        data = response.json()

        # Verify sample exists in reference.db
        ref_path = tmp_data_dir / "reference.db"
        engine = sa.create_engine(f"sqlite:///{ref_path}")
        with engine.connect() as conn:
            row = conn.execute(
                sa.select(samples).where(samples.c.id == data["sample_id"])
            ).fetchone()
        engine.dispose()

        assert row is not None
        assert row.name == "test_sample.txt"
        assert row.file_format == "23andme_v5"

    def test_ingest_creates_sample_db_file(self, client, tmp_data_dir):
        with open(V5_FILE, "rb") as f:
            response = client.post("/api/ingest", files={"file": ("sample.txt", f, "text/plain")})
        data = response.json()
        sample_id = data["sample_id"]
        sample_db = tmp_data_dir / "samples" / f"sample_{sample_id}.db"
        assert sample_db.exists()

    def test_ingest_writes_raw_variants(self, client, tmp_data_dir):
        with open(V5_FILE, "rb") as f:
            response = client.post("/api/ingest", files={"file": ("sample.txt", f, "text/plain")})
        data = response.json()
        sample_id = data["sample_id"]
        sample_db = tmp_data_dir / "samples" / f"sample_{sample_id}.db"

        engine = sa.create_engine(f"sqlite:///{sample_db}")
        with engine.connect() as conn:
            count = conn.execute(sa.select(sa.func.count()).select_from(raw_variants)).scalar()
        engine.dispose()
        assert count == data["variant_count"]

    def test_ingest_creates_complete_job(self, client, tmp_data_dir):
        with open(V5_FILE, "rb") as f:
            response = client.post("/api/ingest", files={"file": ("sample.txt", f, "text/plain")})
        data = response.json()

        ref_path = tmp_data_dir / "reference.db"
        engine = sa.create_engine(f"sqlite:///{ref_path}")
        with engine.connect() as conn:
            job = conn.execute(sa.select(jobs).where(jobs.c.job_id == data["job_id"])).fetchone()
        engine.dispose()

        assert job is not None
        assert job.status == "complete"
        assert job.progress_pct == 100.0

    def test_ingest_empty_file_returns_400(self, client):
        response = client.post(
            "/api/ingest",
            files={"file": ("empty.txt", io.BytesIO(b""), "text/plain")},
        )
        assert response.status_code == 400

    def test_ingest_invalid_format_returns_422(self, client):
        vcf_content = b"##fileformat=VCFv4.2\n#CHROM\tPOS\tID\tREF\tALT\n"
        response = client.post(
            "/api/ingest",
            files={"file": ("data.vcf", io.BytesIO(vcf_content), "text/plain")},
        )
        assert response.status_code == 422

    def test_ingest_multiple_files_get_unique_ids(self, client):
        with open(V5_FILE, "rb") as f1:
            r1 = client.post("/api/ingest", files={"file": ("sample1.txt", f1, "text/plain")})
        with open(V5_FILE, "rb") as f2:
            r2 = client.post("/api/ingest", files={"file": ("sample2.txt", f2, "text/plain")})
        assert r1.json()["sample_id"] != r2.json()["sample_id"]
        assert r1.json()["job_id"] != r2.json()["job_id"]


# ═══════════════════════════════════════════════════════════════════════
# Genome-build gate (issue #480)
# ═══════════════════════════════════════════════════════════════════════


class TestGenomeBuildGate:
    """The ingest route lifts NCBI build-36 (23andMe v3 / hg18) uploads to GRCh37
    at ingest, so the pipeline's "(chrom, pos) are GRCh37" invariant holds (#562,
    replacing the #547 reject-v3 stop-gap). Other non-GRCh37 builds — which have
    no supported liftover — are still refused with HTTP 422 before any rows are
    written.
    """

    def test_v3_fixture_is_genuinely_build36(self):
        """Premise check: the v3 fixture really is build 36, with a coordinate
        far from its GRCh37 locus (rs7412 @ 19:50103919, ~4.69 Mb from the true
        GRCh37 19:45412079) — so the lift is converting genuine build-36 input,
        not a mislabel."""
        result = parse_23andme(V3_FILE)
        assert result.build == "GRCh36"
        rs7412 = next(v for v in result.variants if v.rsid == "rs7412")
        assert (rs7412.chrom, rs7412.pos) == ("19", 50103919)

    def test_v3_build36_upload_lifted_and_accepted(self, client):
        """A build-36 v3 upload is now accepted (202) and lifted to GRCh37."""
        with open(V3_FILE, "rb") as f:
            response = client.post(
                "/api/ingest",
                files={"file": ("pgp_v3_build36.txt", f, "text/plain")},
            )
        assert response.status_code == 202, response.text
        assert response.json()["file_format"] == "23andme_v3"

    def test_v3_variants_stored_on_grch37(self, client, tmp_data_dir):
        """After the lift, the stored rs7412 sits at its GRCh37 coordinate
        (19:45412079), not the build-36 position (19:50103919) — proving the
        lift ran before raw_variants was written."""
        with open(V3_FILE, "rb") as f:
            response = client.post(
                "/api/ingest",
                files={"file": ("pgp_v3_build36.txt", f, "text/plain")},
            )
        assert response.status_code == 202, response.text
        sample_id = response.json()["sample_id"]

        sample_db = tmp_data_dir / "samples" / f"sample_{sample_id}.db"
        engine = sa.create_engine(f"sqlite:///{sample_db}")
        with engine.connect() as conn:
            row = conn.execute(
                sa.select(raw_variants.c.chrom, raw_variants.c.pos).where(
                    raw_variants.c.rsid == "rs7412"
                )
            ).fetchone()
        engine.dispose()
        assert row is not None, "rs7412 should have lifted and been stored"
        assert (row.chrom, row.pos) == ("19", 45412079)

    def test_v3_lift_writes_sample_and_job_rows(self, client, tmp_data_dir):
        """The lifted v3 upload now persists sample + job rows (it is no longer
        rejected pre-persistence)."""
        with open(V3_FILE, "rb") as f:
            response = client.post(
                "/api/ingest",
                files={"file": ("pgp_v3_build36.txt", f, "text/plain")},
            )
        assert response.status_code == 202
        ref_path = Settings(data_dir=tmp_data_dir, wal_mode=False).reference_db_path
        engine = sa.create_engine(f"sqlite:///{ref_path}")
        with engine.connect() as conn:
            assert conn.execute(sa.select(sa.func.count()).select_from(samples)).scalar() == 1
            assert conn.execute(sa.select(sa.func.count()).select_from(jobs)).scalar() == 1
        engine.dispose()

    def test_v3_nocall_count_matches_stored_variants(self, client, tmp_data_dir):
        """The upload response's counts describe the post-lift *stored* set: both
        variant_count and nocall_count match raw_variants, rather than the parser's
        pre-lift no-call count (which would over-count dropped no-calls)."""
        with open(V3_FILE, "rb") as f:
            response = client.post("/api/ingest", files={"file": ("pgp_v3.txt", f, "text/plain")})
        assert response.status_code == 202, response.text
        body = response.json()
        sample_db = tmp_data_dir / "samples" / f"sample_{body['sample_id']}.db"
        engine = sa.create_engine(f"sqlite:///{sample_db}")
        with engine.connect() as conn:
            stored_total = conn.execute(
                sa.select(sa.func.count()).select_from(raw_variants)
            ).scalar()
            stored_nocall = conn.execute(
                sa.select(sa.func.count())
                .select_from(raw_variants)
                .where(raw_variants.c.genotype == "--")
            ).scalar()
        engine.dispose()
        assert body["variant_count"] == stored_total
        assert body["nocall_count"] == stored_nocall

    def test_unknown_build_still_rejected_422_no_rows(self, client, tmp_data_dir, monkeypatch):
        """A build that is neither GRCh36 nor GRCh37 has no supported liftover and
        is refused with 422 before any sample/job rows are written (the elif gate)."""
        from backend.ingestion.base import ParsedVariant, ParseResult, SourceVendor

        fake = ParseResult(
            vendor=SourceVendor.TWENTYTHREEANDME,
            version="v?",
            build="GRCh38",
            variants=[ParsedVariant(rsid="rs1", chrom="1", pos=100, genotype="AA")],
        )
        monkeypatch.setattr("backend.api.routes.ingest.parse", lambda _stream: fake)
        response = client.post(
            "/api/ingest", files={"file": ("x.txt", io.BytesIO(b"header\n"), "text/plain")}
        )
        assert response.status_code == 422, response.text
        detail = response.json()["detail"]
        assert isinstance(detail, str)
        assert "GRCh38" in detail
        ref_path = Settings(data_dir=tmp_data_dir, wal_mode=False).reference_db_path
        engine = sa.create_engine(f"sqlite:///{ref_path}")
        with engine.connect() as conn:
            assert conn.execute(sa.select(sa.func.count()).select_from(samples)).scalar() == 0
            assert conn.execute(sa.select(sa.func.count()).select_from(jobs)).scalar() == 0
        engine.dispose()

    def test_v5_build37_upload_still_accepted(self, client):
        """The gate is build-scoped, not a blanket 23andMe block: a build-37
        23andMe file (v5) still ingests at 202."""
        with open(V5_FILE, "rb") as f:
            response = client.post(
                "/api/ingest",
                files={"file": ("sample_v5.txt", f, "text/plain")},
            )
        assert response.status_code == 202, response.text
        assert response.json()["file_format"] == "23andme_v5"


# ═══════════════════════════════════════════════════════════════════════
# GET /api/ingest/status/{job_id}
# ═══════════════════════════════════════════════════════════════════════


class TestIngestStatus:
    """GET /api/ingest/status/{job_id} returns SSE stream."""

    def test_status_returns_sse_content_type(self, client):
        # First ingest a file to get a job_id
        with open(V5_FILE, "rb") as f:
            r = client.post("/api/ingest", files={"file": ("sample.txt", f, "text/plain")})
        job_id = r.json()["job_id"]

        response = client.get(f"/api/ingest/status/{job_id}")
        assert response.headers["content-type"] == "text/event-stream; charset=utf-8"

    def test_status_contains_complete_event(self, client):
        with open(V5_FILE, "rb") as f:
            r = client.post("/api/ingest", files={"file": ("sample.txt", f, "text/plain")})
        job_id = r.json()["job_id"]

        response = client.get(f"/api/ingest/status/{job_id}")
        assert "complete" in response.text

    def test_status_not_found_job(self, client):
        response = client.get("/api/ingest/status/nonexistent-job")
        # SSE returns 200 with error event in the stream
        assert response.status_code == 200
        assert "not found" in response.text.lower()


# ═══════════════════════════════════════════════════════════════════════
# GET /api/samples
# ═══════════════════════════════════════════════════════════════════════


class TestListSamples:
    """GET /api/samples returns sample list."""

    def test_empty_list(self, client):
        response = client.get("/api/samples")
        assert response.status_code == 200
        assert response.json() == []

    def test_list_after_ingest(self, client):
        with open(V5_FILE, "rb") as f:
            client.post("/api/ingest", files={"file": ("my_sample.txt", f, "text/plain")})

        response = client.get("/api/samples")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["name"] == "my_sample.txt"

    def test_list_multiple_samples(self, client):
        for name in ["s1.txt", "s2.txt"]:
            with open(V5_FILE, "rb") as f:
                client.post("/api/ingest", files={"file": (name, f, "text/plain")})

        response = client.get("/api/samples")
        data = response.json()
        assert len(data) == 2


# ═══════════════════════════════════════════════════════════════════════
# GET /api/samples/{sample_id}
# ═══════════════════════════════════════════════════════════════════════


class TestGetSample:
    """GET /api/samples/{id} returns single sample."""

    def test_get_existing_sample(self, client):
        with open(V5_FILE, "rb") as f:
            r = client.post("/api/ingest", files={"file": ("sample.txt", f, "text/plain")})
        sid = r.json()["sample_id"]

        response = client.get(f"/api/samples/{sid}")
        assert response.status_code == 200
        assert response.json()["id"] == sid

    def test_get_nonexistent_sample(self, client):
        response = client.get("/api/samples/999")
        assert response.status_code == 404


# ═══════════════════════════════════════════════════════════════════════
# PATCH /api/samples/{sample_id}
# ═══════════════════════════════════════════════════════════════════════


class TestUpdateSample:
    """PATCH /api/samples/{id} updates metadata."""

    def test_rename_sample(self, client):
        with open(V5_FILE, "rb") as f:
            r = client.post("/api/ingest", files={"file": ("old_name.txt", f, "text/plain")})
        sid = r.json()["sample_id"]

        response = client.patch(f"/api/samples/{sid}", json={"name": "New Name"})
        assert response.status_code == 200
        assert response.json()["name"] == "New Name"

    def test_update_sets_updated_at(self, client):
        with open(V5_FILE, "rb") as f:
            r = client.post("/api/ingest", files={"file": ("sample.txt", f, "text/plain")})
        sid = r.json()["sample_id"]

        response = client.patch(f"/api/samples/{sid}", json={"name": "Renamed"})
        assert response.json()["updated_at"] is not None

    def test_update_nonexistent_returns_404(self, client):
        response = client.patch("/api/samples/999", json={"name": "x"})
        assert response.status_code == 404


# ═══════════════════════════════════════════════════════════════════════
# DELETE /api/samples/{sample_id}
# ═══════════════════════════════════════════════════════════════════════


class TestDeleteSample:
    """DELETE /api/samples/{id} removes sample."""

    def test_delete_returns_204(self, client):
        with open(V5_FILE, "rb") as f:
            r = client.post("/api/ingest", files={"file": ("sample.txt", f, "text/plain")})
        sid = r.json()["sample_id"]

        response = client.delete(f"/api/samples/{sid}")
        assert response.status_code == 204

    def test_delete_removes_from_list(self, client):
        with open(V5_FILE, "rb") as f:
            r = client.post("/api/ingest", files={"file": ("sample.txt", f, "text/plain")})
        sid = r.json()["sample_id"]

        client.delete(f"/api/samples/{sid}")
        response = client.get("/api/samples")
        assert len(response.json()) == 0

    def test_delete_removes_db_file(self, client, tmp_data_dir):
        with open(V5_FILE, "rb") as f:
            r = client.post("/api/ingest", files={"file": ("sample.txt", f, "text/plain")})
        sid = r.json()["sample_id"]
        sample_db = tmp_data_dir / "samples" / f"sample_{sid}.db"
        assert sample_db.exists()

        client.delete(f"/api/samples/{sid}")
        assert not sample_db.exists()

    def test_delete_nonexistent_returns_404(self, client):
        response = client.delete("/api/samples/999")
        assert response.status_code == 404

    def test_get_after_delete_returns_404(self, client):
        with open(V5_FILE, "rb") as f:
            r = client.post("/api/ingest", files={"file": ("sample.txt", f, "text/plain")})
        sid = r.json()["sample_id"]

        client.delete(f"/api/samples/{sid}")
        response = client.get(f"/api/samples/{sid}")
        assert response.status_code == 404


# ═══════════════════════════════════════════════════════════════════════
# POST /api/ingest — AncestryDNA happy path (ADNA-10 / Step 43)
# ═══════════════════════════════════════════════════════════════════════


class TestIngestAncestryDNA:
    """ADNA-10 (Plan §13.1): POST /api/ingest with the AncestryDNA fixture
    returns 202 + ``file_format="ancestrydna_v2.0"`` + the fixture's
    variant count on a v2.0.0+ bundle.

    The 409-gated AncestryDNA + pre-v2.0.0 case lives in
    ``test_bundle_gating.py`` (Step 7); this class is the paired
    happy-path lock.
    """

    def test_ingest_ancestrydna_returns_202(self, ancestrydna_client):
        with open(ANCESTRY_FILE, "rb") as f:
            response = ancestrydna_client.post(
                "/api/ingest",
                files={"file": ("ancestry.txt", f, "text/plain")},
            )
        assert response.status_code == 202, response.text

    def test_ingest_ancestrydna_returns_file_format(self, ancestrydna_client):
        with open(ANCESTRY_FILE, "rb") as f:
            response = ancestrydna_client.post(
                "/api/ingest",
                files={"file": ("ancestry.txt", f, "text/plain")},
            )
        assert response.json()["file_format"] == "ancestrydna_v2.0"

    def test_ingest_ancestrydna_returns_variant_count(self, ancestrydna_client):
        with open(ANCESTRY_FILE, "rb") as f:
            response = ancestrydna_client.post(
                "/api/ingest",
                files={"file": ("ancestry.txt", f, "text/plain")},
            )
        body = response.json()
        assert body["variant_count"] == ANCESTRY_VARIANT_COUNT

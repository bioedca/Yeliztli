"""Tests for VUS watched-variants API endpoints (P4-21h).

Covers:
- T4-22k: POST /api/watches creates watched_variants row with correct ClinVar significance snapshot
- T4-22l: DELETE /api/watches/{rsid} removes row correctly
- List, update notes, duplicate watch, unwatch nonexistent
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from backend.config import Settings
from backend.db.connection import reset_registry
from backend.db.sample_schema import create_sample_tables
from backend.db.tables import (
    annotated_variants,
    reference_metadata,
    samples,
)


@pytest.fixture
def watches_client(tmp_data_dir: Path) -> TestClient:
    """FastAPI TestClient with a sample DB containing annotated variants."""
    settings = Settings(data_dir=tmp_data_dir, wal_mode=False)

    # Create reference.db with tables and a sample record
    ref_path = settings.reference_db_path
    ref_engine = sa.create_engine(f"sqlite:///{ref_path}")
    reference_metadata.create_all(ref_engine)

    sample_db_rel = "samples/sample_1.db"
    sample_db_path = tmp_data_dir / sample_db_rel
    sample_db_path.parent.mkdir(parents=True, exist_ok=True)

    with ref_engine.begin() as conn:
        conn.execute(
            samples.insert().values(
                name="Test Sample",
                db_path=sample_db_rel,
                file_format="23andme_v5",
                file_hash="abc123",
            )
        )
    ref_engine.dispose()

    # Create sample DB with tables and seed annotated variants
    sample_engine = sa.create_engine(f"sqlite:///{sample_db_path}")
    create_sample_tables(sample_engine)

    with sample_engine.begin() as conn:
        conn.execute(
            annotated_variants.insert(),
            [
                {
                    "rsid": "rs12345",
                    "chrom": "1",
                    "pos": 100000,
                    "clinvar_significance": "Uncertain_significance",
                },
                {
                    "rsid": "rs80357906",
                    "chrom": "17",
                    "pos": 41209080,
                    "clinvar_significance": "Pathogenic",
                },
                {
                    "rsid": "rs99999",
                    "chrom": "2",
                    "pos": 200000,
                    "clinvar_significance": None,
                },
            ],
        )
    sample_engine.dispose()

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
# T4-22k: POST creates watched_variants row with correct ClinVar snapshot
# ═══════════════════════════════════════════════════════════════════════


class TestWatchVariant:
    def test_watch_with_clinvar_significance(self, watches_client: TestClient):
        """POST snapshots current ClinVar significance."""
        resp = watches_client.post(
            "/api/watches",
            json={"sample_id": 1, "rsid": "rs12345", "notes": "track this VUS"},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["rsid"] == "rs12345"
        assert data["clinvar_significance_at_watch"] == "Uncertain_significance"
        assert data["clinvar_significance_current"] == "Uncertain_significance"
        assert data["notes"] == "track this VUS"
        assert data["watched_at"]  # non-empty

    def test_watch_without_clinvar_significance(self, watches_client: TestClient):
        """POST handles variant with NULL ClinVar significance."""
        resp = watches_client.post(
            "/api/watches",
            json={"sample_id": 1, "rsid": "rs99999"},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["rsid"] == "rs99999"
        assert data["clinvar_significance_at_watch"] is None

    def test_watch_unannotated_variant(self, watches_client: TestClient):
        """POST handles variant not in annotated_variants (no annotation yet)."""
        resp = watches_client.post(
            "/api/watches",
            json={"sample_id": 1, "rsid": "rs000000"},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["rsid"] == "rs000000"
        assert data["clinvar_significance_at_watch"] is None

    def test_watch_duplicate_returns_409(self, watches_client: TestClient):
        """POST returns 409 if variant is already watched."""
        watches_client.post(
            "/api/watches",
            json={"sample_id": 1, "rsid": "rs12345"},
        )
        resp = watches_client.post(
            "/api/watches",
            json={"sample_id": 1, "rsid": "rs12345"},
        )
        assert resp.status_code == 409
        assert "already being watched" in resp.json()["detail"]

    def test_watch_nonexistent_sample_returns_404(self, watches_client: TestClient):
        """POST returns 404 for nonexistent sample."""
        resp = watches_client.post(
            "/api/watches",
            json={"sample_id": 999, "rsid": "rs12345"},
        )
        assert resp.status_code == 404


# ═══════════════════════════════════════════════════════════════════════
# T4-22l: DELETE removes row correctly
# ═══════════════════════════════════════════════════════════════════════


class TestUnwatchVariant:
    def test_unwatch_removes_row(self, watches_client: TestClient):
        """DELETE removes the watched variant."""
        watches_client.post(
            "/api/watches",
            json={"sample_id": 1, "rsid": "rs12345"},
        )

        resp = watches_client.delete("/api/watches/rs12345?sample_id=1")
        assert resp.status_code == 204

        # Confirm gone from list
        list_resp = watches_client.get("/api/watches?sample_id=1")
        assert list_resp.status_code == 200
        assert len(list_resp.json()) == 0

    def test_unwatch_nonexistent_returns_404(self, watches_client: TestClient):
        """DELETE returns 404 if variant is not being watched."""
        resp = watches_client.delete("/api/watches/rs12345?sample_id=1")
        assert resp.status_code == 404
        assert "not being watched" in resp.json()["detail"]

    def test_unwatch_nonexistent_sample_returns_404(self, watches_client: TestClient):
        """DELETE returns 404 for nonexistent sample."""
        resp = watches_client.delete("/api/watches/rs12345?sample_id=999")
        assert resp.status_code == 404


# ═══════════════════════════════════════════════════════════════════════
# List and update endpoints
# ═══════════════════════════════════════════════════════════════════════


class TestListWatched:
    def test_list_empty(self, watches_client: TestClient):
        """GET returns empty list when no variants are watched."""
        resp = watches_client.get("/api/watches?sample_id=1")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_list_multiple(self, watches_client: TestClient):
        """GET returns all watched variants ordered by watched_at desc."""
        from datetime import UTC, datetime, timedelta
        from itertools import count
        from unittest.mock import patch

        # Inject strictly-increasing watched_at timestamps instead of relying on a
        # real time.sleep, which flakes when the watched_at resolution is coarser
        # than the 10 ms sleep. The desc-ordering assertion is now deterministic.
        base = datetime(2026, 1, 1, tzinfo=UTC)
        ticks = count()
        with patch("backend.api.routes.watches.datetime") as mock_dt:
            mock_dt.now.side_effect = lambda *a, **k: base + timedelta(seconds=next(ticks))
            watches_client.post("/api/watches", json={"sample_id": 1, "rsid": "rs12345"})
            watches_client.post("/api/watches", json={"sample_id": 1, "rsid": "rs80357906"})

        resp = watches_client.get("/api/watches?sample_id=1")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2
        # Most recent first: rs80357906 got the later (larger) injected timestamp.
        assert data[0]["rsid"] == "rs80357906"
        assert data[1]["rsid"] == "rs12345"

    def test_list_includes_current_clinvar_significance(self, watches_client: TestClient):
        """GET returns clinvar_significance_current from annotated_variants (P4-21k)."""
        watches_client.post(
            "/api/watches",
            json={"sample_id": 1, "rsid": "rs12345"},
        )

        resp = watches_client.get("/api/watches?sample_id=1")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        # Current significance matches what's in annotated_variants
        assert data[0]["clinvar_significance_current"] == "Uncertain_significance"
        # At-watch snapshot also matches (no change since just watched)
        assert data[0]["clinvar_significance_at_watch"] == "Uncertain_significance"

    def test_list_detects_reclassification(self, watches_client: TestClient):
        """GET shows different at_watch vs current significance after DB update (P4-21k)."""
        # Watch variant — snapshots current ClinVar significance
        watches_client.post(
            "/api/watches",
            json={"sample_id": 1, "rsid": "rs12345"},
        )

        # Simulate a ClinVar reclassification by directly updating annotated_variants
        from backend.db.connection import get_registry
        from backend.db.tables import annotated_variants as av

        registry = get_registry()
        with registry.reference_engine.connect() as conn:
            row = conn.execute(sa.select(samples.c.db_path).where(samples.c.id == 1)).fetchone()

        sample_db_path = registry.settings.data_dir / row.db_path
        sample_engine = sa.create_engine(f"sqlite:///{sample_db_path}")
        with sample_engine.begin() as conn:
            conn.execute(
                av.update().where(av.c.rsid == "rs12345").values(clinvar_significance="Pathogenic")
            )
        sample_engine.dispose()

        # Now list should show different at_watch vs current
        resp = watches_client.get("/api/watches?sample_id=1")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["clinvar_significance_at_watch"] == "Uncertain_significance"
        assert data[0]["clinvar_significance_current"] == "Pathogenic"

    def test_list_unannotated_watched_variant(self, watches_client: TestClient):
        """GET returns null current significance for watched variant not in annotated_variants."""
        watches_client.post(
            "/api/watches",
            json={"sample_id": 1, "rsid": "rs000000"},
        )

        resp = watches_client.get("/api/watches?sample_id=1")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["clinvar_significance_at_watch"] is None
        assert data[0]["clinvar_significance_current"] is None


class TestUpdateWatchNotes:
    def test_update_notes(self, watches_client: TestClient):
        """PATCH updates notes on a watched variant."""
        watches_client.post(
            "/api/watches",
            json={"sample_id": 1, "rsid": "rs12345", "notes": "old note"},
        )

        resp = watches_client.patch(
            "/api/watches/rs12345",
            json={"sample_id": 1, "notes": "updated note"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["notes"] == "updated note"
        assert data["rsid"] == "rs12345"

    def test_update_notes_nonexistent_returns_404(self, watches_client: TestClient):
        """PATCH returns 404 if variant is not being watched."""
        resp = watches_client.patch(
            "/api/watches/rs12345",
            json={"sample_id": 1, "notes": "nope"},
        )
        assert resp.status_code == 404

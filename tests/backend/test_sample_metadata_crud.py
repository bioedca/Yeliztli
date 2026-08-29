"""Tests for sample metadata CRUD (P4-21f / T4-22h).

Covers: create, read, update, delete metadata fields including JSON extra.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from backend.config import Settings
from backend.db.tables import reference_metadata

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
V5_FILE = FIXTURES / "sample_23andme_v5.txt"


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


def _create_sample(client: TestClient) -> int:
    """Ingest a file and return the sample_id."""
    with open(V5_FILE, "rb") as f:
        r = client.post("/api/ingest", files={"file": ("sample.txt", f, "text/plain")})
    assert r.status_code == 202
    return r.json()["sample_id"]


# ═══════════════════════════════════════════════════════════════════════
# GET /api/samples/{id} — full metadata
# ═══════════════════════════════════════════════════════════════════════


class TestGetSampleMetadata:
    """GET /api/samples/{id} returns full metadata from sample DB."""

    def test_get_returns_notes_field(self, client):
        sid = _create_sample(client)
        data = client.get(f"/api/samples/{sid}").json()
        assert "notes" in data

    def test_get_returns_source_field(self, client):
        sid = _create_sample(client)
        data = client.get(f"/api/samples/{sid}").json()
        assert "source" in data

    def test_get_returns_date_collected_field(self, client):
        sid = _create_sample(client)
        data = client.get(f"/api/samples/{sid}").json()
        assert "date_collected" in data

    def test_get_returns_extra_field(self, client):
        sid = _create_sample(client)
        data = client.get(f"/api/samples/{sid}").json()
        assert "extra" in data
        assert isinstance(data["extra"], dict)

    def test_get_defaults_empty_extra(self, client):
        sid = _create_sample(client)
        data = client.get(f"/api/samples/{sid}").json()
        assert data["extra"] == {}


# ═══════════════════════════════════════════════════════════════════════
# PATCH /api/samples/{id} — update all metadata fields
# ═══════════════════════════════════════════════════════════════════════


class TestUpdateSampleMetadata:
    """PATCH /api/samples/{id} updates all metadata fields."""

    def test_update_notes(self, client):
        sid = _create_sample(client)
        r = client.patch(f"/api/samples/{sid}", json={"notes": "Test note"})
        assert r.status_code == 200
        assert r.json()["notes"] == "Test note"

    def test_update_source(self, client):
        sid = _create_sample(client)
        r = client.patch(f"/api/samples/{sid}", json={"source": "23andMe"})
        assert r.status_code == 200
        assert r.json()["source"] == "23andMe"

    def test_update_date_collected(self, client):
        sid = _create_sample(client)
        r = client.patch(f"/api/samples/{sid}", json={"date_collected": "2025-01-15"})
        assert r.status_code == 200
        assert r.json()["date_collected"] == "2025-01-15"

    def test_update_extra_json(self, client):
        sid = _create_sample(client)
        extra = {"ethnicity": "European", "kit_version": "v5.1"}
        r = client.patch(f"/api/samples/{sid}", json={"extra": extra})
        assert r.status_code == 200
        assert r.json()["extra"] == extra

    def test_update_multiple_fields(self, client):
        sid = _create_sample(client)
        r = client.patch(
            f"/api/samples/{sid}",
            json={
                "name": "Renamed",
                "notes": "Multi-field update",
                "source": "AncestryDNA",
                "date_collected": "2024-06-01",
                "extra": {"key": "value"},
            },
        )
        assert r.status_code == 200
        data = r.json()
        assert data["name"] == "Renamed"
        assert data["notes"] == "Multi-field update"
        assert data["source"] == "AncestryDNA"
        assert data["date_collected"] == "2024-06-01"
        assert data["extra"] == {"key": "value"}

    def test_update_extra_replaces_previous(self, client):
        sid = _create_sample(client)
        client.patch(f"/api/samples/{sid}", json={"extra": {"old": "data"}})
        r = client.patch(f"/api/samples/{sid}", json={"extra": {"new": "data"}})
        assert r.json()["extra"] == {"new": "data"}

    def test_update_extra_empty_object(self, client):
        sid = _create_sample(client)
        client.patch(f"/api/samples/{sid}", json={"extra": {"key": "val"}})
        r = client.patch(f"/api/samples/{sid}", json={"extra": {}})
        assert r.json()["extra"] == {}

    def test_update_preserves_unchanged_fields(self, client):
        sid = _create_sample(client)
        client.patch(f"/api/samples/{sid}", json={"notes": "Keep this"})
        r = client.patch(f"/api/samples/{sid}", json={"source": "NewLab"})
        data = r.json()
        assert data["notes"] == "Keep this"
        assert data["source"] == "NewLab"

    def test_update_sets_updated_at(self, client):
        sid = _create_sample(client)
        r = client.patch(f"/api/samples/{sid}", json={"notes": "Timestamp test"})
        assert r.json()["updated_at"] is not None

    def test_update_nonexistent_returns_404(self, client):
        r = client.patch("/api/samples/999", json={"notes": "x"})
        assert r.status_code == 404

    def test_nonexistent_sample_reports_404_even_with_an_invalid_date(self, client):
        """Existence outranks field validation (#2041 review).

        The plain 404 test sends only ``notes``, so it cannot see whether a bad
        date short-circuits the lookup. Validating the date before the existence
        check would answer 422 for a sample that does not exist, telling the
        caller their date was wrong when the real problem is the ID.
        """
        r = client.patch("/api/samples/999", json={"date_collected": "invalid-date"})
        assert r.status_code == 404

        r = client.patch(
            "/api/samples/999",
            json={"name": "Nope", "date_collected": "invalid-date"},
        )
        assert r.status_code == 404

    def test_update_invalid_date_format_returns_422(self, client):
        sid = _create_sample(client)
        r = client.patch(f"/api/samples/{sid}", json={"date_collected": "invalid-date"})
        assert r.status_code == 422

    def test_rejected_date_does_not_commit_the_rename(self, client):
        """A 422 must leave nothing behind (#2041).

        The rename lands in the registry (reference.db) while the date lands in
        the per-sample DB, so no shared transaction spans them -- validating
        before the first write is the only thing that keeps a rejected request
        from half-applying. The shipped editor sends every field in one PATCH,
        so "rename and mistype the date" is a single ordinary request.
        """
        sid = _create_sample(client)
        before = client.get(f"/api/samples/{sid}").json()

        r = client.patch(
            f"/api/samples/{sid}",
            json={"name": "RENAMED_BY_PARTIAL_COMMIT", "date_collected": "invalid-date"},
        )
        assert r.status_code == 422

        after = client.get(f"/api/samples/{sid}").json()
        assert after["name"] == before["name"], (
            f"partial commit: PATCH returned 422 but name changed "
            f"{before['name']!r} -> {after['name']!r}"
        )
        assert after["updated_at"] == before["updated_at"], (
            "partial commit: PATCH returned 422 but updated_at was bumped"
        )

    def test_clearing_the_date_does_not_commit_the_rename(self, client):
        """An <input type="date"> emits "" when cleared, and "" never parses.

        Same partial commit as the malformed-date case, reached without the
        user typing anything wrong.
        """
        sid = _create_sample(client)
        before = client.get(f"/api/samples/{sid}").json()

        r = client.patch(
            f"/api/samples/{sid}",
            json={"name": "RENAMED_BY_CLEARED_DATE", "date_collected": ""},
        )
        assert r.status_code == 422

        after = client.get(f"/api/samples/{sid}").json()
        assert after["name"] == before["name"], (
            f"partial commit: clearing the date returned 422 but name changed "
            f"{before['name']!r} -> {after['name']!r}"
        )

    def test_update_extra_invalid_json_returns_422(self, client):
        sid = _create_sample(client)
        r = client.patch(f"/api/samples/{sid}", json={"extra": "not-a-dict"})
        assert r.status_code == 422


# ═══════════════════════════════════════════════════════════════════════
# DELETE /api/samples/{id} — with metadata verification
# ═══════════════════════════════════════════════════════════════════════


class TestDeleteSampleWithMetadata:
    """DELETE /api/samples/{id} after metadata has been set."""

    def test_delete_sample_with_metadata(self, client):
        sid = _create_sample(client)
        # Set metadata first
        client.patch(
            f"/api/samples/{sid}",
            json={"notes": "Will be deleted", "extra": {"key": "val"}},
        )
        # Delete
        r = client.delete(f"/api/samples/{sid}")
        assert r.status_code == 204

    def test_deleted_sample_not_in_list(self, client):
        sid = _create_sample(client)
        client.patch(f"/api/samples/{sid}", json={"notes": "Bye"})
        client.delete(f"/api/samples/{sid}")
        r = client.get("/api/samples")
        ids = [s["id"] for s in r.json()]
        assert sid not in ids

    def test_get_deleted_sample_returns_404(self, client):
        sid = _create_sample(client)
        client.delete(f"/api/samples/{sid}")
        r = client.get(f"/api/samples/{sid}")
        assert r.status_code == 404

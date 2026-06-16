"""Tests for variant tagging API (P4-12b).

Covers:
  - GET    /api/tags                — List all tags for a sample
  - POST   /api/tags                — Create custom tag
  - PUT    /api/tags/{tag_id}       — Update custom tag
  - DELETE /api/tags/{tag_id}       — Delete custom tag
  - POST   /api/tags/variant        — Add tag to variant
  - DELETE /api/tags/variant        — Remove tag from variant
  - GET    /api/tags/variant/{rsid} — Get all tags for a variant
  - GET    /api/variants?tag=X      — Filter variants by tag
  - Variant rows include tags field

Integration test T4-22g: Variant tagging system with predefined + custom tags.
"""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path
from unittest.mock import patch

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from backend.config import Settings
from backend.db.connection import reset_registry
from backend.db.sample_schema import create_sample_tables
from backend.db.tables import raw_variants, reference_metadata, samples, tags, variant_tags

# ── Test data ────────────────────────────────────────────────────────

RAW_VARIANTS_DATA = [
    {"rsid": "rs12345", "chrom": "1", "pos": 100000, "genotype": "AA"},
    {"rsid": "rs429358", "chrom": "19", "pos": 44908684, "genotype": "TC"},
    {"rsid": "rs7412", "chrom": "19", "pos": 44908822, "genotype": "CC"},
]

PREDEFINED_TAG_NAMES = [
    "Review later",
    "Discuss with clinician",
    "False positive",
    "Actionable",
    "Benign override",
]


def _variant_tag_count(db_path: Path, tag_id: int) -> int:
    engine = sa.create_engine(f"sqlite:///{db_path}")
    try:
        with engine.connect() as conn:
            return conn.execute(
                sa.select(sa.func.count())
                .select_from(variant_tags)
                .where(variant_tags.c.tag_id == tag_id)
            ).scalar_one()
    finally:
        engine.dispose()


# ── Fixtures ─────────────────────────────────────────────────────────


@pytest.fixture()
def tmp_data_dir(tmp_path: Path) -> Path:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "samples").mkdir()
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
def tags_client(tmp_data_dir: Path, sample_db_path: Path) -> Generator[TestClient, None, None]:
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
                    "name": "Test Sample",
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
# GET /api/tags — List tags
# ═══════════════════════════════════════════════════════════════════════


class TestListTags:
    def test_list_tags_returns_predefined(self, tags_client: TestClient) -> None:
        """GET /api/tags returns the 5 predefined tags."""
        resp = tags_client.get("/api/tags", params={"sample_id": 1})
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) >= 5
        names = [t["name"] for t in data]
        for expected in PREDEFINED_TAG_NAMES:
            assert expected in names
        for t in data:
            if t["name"] in PREDEFINED_TAG_NAMES:
                assert t["is_predefined"] is True


# ═══════════════════════════════════════════════════════════════════════
# POST /api/tags — Create tag
# ═══════════════════════════════════════════════════════════════════════


class TestCreateTag:
    def test_create_custom_tag(self, tags_client: TestClient) -> None:
        """POST /api/tags creates a new custom tag."""
        resp = tags_client.post(
            "/api/tags",
            json={"sample_id": 1, "name": "My Custom Tag"},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "My Custom Tag"
        assert data["is_predefined"] is False
        assert data["id"] > 0

    def test_create_tag_duplicate_name_409(self, tags_client: TestClient) -> None:
        """POST /api/tags with existing name returns 409."""
        resp = tags_client.post(
            "/api/tags",
            json={"sample_id": 1, "name": "Review later"},
        )
        assert resp.status_code == 409

    def test_create_tag_empty_name_422(self, tags_client: TestClient) -> None:
        """POST /api/tags with empty name returns 422."""
        resp = tags_client.post(
            "/api/tags",
            json={"sample_id": 1, "name": ""},
        )
        assert resp.status_code == 422

    def test_create_tag_with_custom_color(self, tags_client: TestClient) -> None:
        """POST /api/tags with color param sets custom color."""
        resp = tags_client.post(
            "/api/tags",
            json={"sample_id": 1, "name": "Colored Tag", "color": "#FF5733"},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["color"] == "#FF5733"
        assert data["name"] == "Colored Tag"


# ═══════════════════════════════════════════════════════════════════════
# PUT /api/tags/{id} — Update tag
# ═══════════════════════════════════════════════════════════════════════


class TestUpdateTag:
    def test_update_custom_tag(self, tags_client: TestClient) -> None:
        """PUT /api/tags/{id} updates name and color."""
        # Create a custom tag first
        resp = tags_client.post(
            "/api/tags",
            json={"sample_id": 1, "name": "Original Name"},
        )
        assert resp.status_code == 201
        tag_id = resp.json()["id"]

        # Update it
        resp = tags_client.put(
            f"/api/tags/{tag_id}",
            json={"sample_id": 1, "name": "Updated Name", "color": "#00FF00"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "Updated Name"
        assert data["color"] == "#00FF00"

    def test_update_predefined_tag_403(self, tags_client: TestClient) -> None:
        """PUT /api/tags/{id} on predefined tag returns 403."""
        # Get predefined tag IDs
        resp = tags_client.get("/api/tags", params={"sample_id": 1})
        predefined = [t for t in resp.json() if t["is_predefined"]]
        assert len(predefined) > 0
        tag_id = predefined[0]["id"]

        resp = tags_client.put(
            f"/api/tags/{tag_id}",
            json={"sample_id": 1, "name": "Renamed"},
        )
        assert resp.status_code == 403

    def test_update_tag_duplicate_name_409(self, tags_client: TestClient) -> None:
        """PUT /api/tags/{id} with conflicting name returns 409."""
        # Create two custom tags
        resp1 = tags_client.post(
            "/api/tags",
            json={"sample_id": 1, "name": "Tag Alpha"},
        )
        assert resp1.status_code == 201

        resp2 = tags_client.post(
            "/api/tags",
            json={"sample_id": 1, "name": "Tag Beta"},
        )
        assert resp2.status_code == 201
        beta_id = resp2.json()["id"]

        # Try to rename Beta to Alpha
        resp = tags_client.put(
            f"/api/tags/{beta_id}",
            json={"sample_id": 1, "name": "Tag Alpha"},
        )
        assert resp.status_code == 409

    def test_update_nonexistent_tag_404(self, tags_client: TestClient) -> None:
        """PUT /api/tags/{id} on nonexistent tag returns 404."""
        resp = tags_client.put(
            "/api/tags/99999",
            json={"sample_id": 1, "name": "Ghost"},
        )
        assert resp.status_code == 404


# ═══════════════════════════════════════════════════════════════════════
# DELETE /api/tags/{id} — Delete tag
# ═══════════════════════════════════════════════════════════════════════


class TestDeleteTag:
    def test_delete_custom_tag(self, tags_client: TestClient) -> None:
        """DELETE /api/tags/{id} deletes the tag."""
        # Create a custom tag
        resp = tags_client.post(
            "/api/tags",
            json={"sample_id": 1, "name": "To Delete"},
        )
        assert resp.status_code == 201
        tag_id = resp.json()["id"]

        # Delete it
        resp = tags_client.delete(f"/api/tags/{tag_id}", params={"sample_id": 1})
        assert resp.status_code == 204

        # Verify it's gone
        resp = tags_client.get("/api/tags", params={"sample_id": 1})
        names = [t["name"] for t in resp.json()]
        assert "To Delete" not in names

    def test_delete_predefined_tag_403(self, tags_client: TestClient) -> None:
        """DELETE /api/tags/{id} on predefined tag returns 403."""
        resp = tags_client.get("/api/tags", params={"sample_id": 1})
        predefined = [t for t in resp.json() if t["is_predefined"]]
        assert len(predefined) > 0
        tag_id = predefined[0]["id"]

        resp = tags_client.delete(f"/api/tags/{tag_id}", params={"sample_id": 1})
        assert resp.status_code == 403

    def test_delete_tag_removes_variant_tags_directly(self, sample_db_path: Path) -> None:
        """delete_tag removes join rows even when SQLite FK cascades are disabled."""
        from backend.api.routes import tags as tag_routes

        engine = sa.create_engine(f"sqlite:///{sample_db_path}")
        try:
            with engine.begin() as conn:
                tag_id = conn.execute(
                    tags.insert().values(
                        name="Cascade Direct",
                        color="#6B7280",
                        is_predefined=False,
                    )
                ).inserted_primary_key[0]
                conn.execute(variant_tags.insert().values(rsid="rs12345", tag_id=tag_id))

            assert _variant_tag_count(sample_db_path, tag_id) == 1

            with patch.object(tag_routes, "_get_sample_engine", return_value=engine):
                tag_routes.delete_tag(tag_id=tag_id, sample_id=1)

            assert _variant_tag_count(sample_db_path, tag_id) == 0
            with engine.connect() as conn:
                assert (
                    conn.execute(sa.select(tags.c.id).where(tags.c.id == tag_id)).fetchone()
                    is None
                )
        finally:
            engine.dispose()

    def test_delete_tag_cascades(
        self,
        tags_client: TestClient,
        sample_db_path: Path,
    ) -> None:
        """Deleting a tag removes its variant_tags entries."""
        # Create custom tag
        resp = tags_client.post(
            "/api/tags",
            json={"sample_id": 1, "name": "Cascade Test"},
        )
        assert resp.status_code == 201
        tag_id = resp.json()["id"]

        # Tag a variant
        resp = tags_client.post(
            "/api/tags/variant",
            json={"sample_id": 1, "rsid": "rs12345", "tag_id": tag_id},
        )
        assert resp.status_code == 200

        # Verify tag is on the variant
        resp = tags_client.get("/api/tags/variant/rs12345", params={"sample_id": 1})
        tag_names = [t["name"] for t in resp.json()]
        assert "Cascade Test" in tag_names
        assert _variant_tag_count(sample_db_path, tag_id) == 1

        # Delete the tag
        resp = tags_client.delete(f"/api/tags/{tag_id}", params={"sample_id": 1})
        assert resp.status_code == 204

        # Verify the association row is gone directly; the joined read path masks orphans.
        assert _variant_tag_count(sample_db_path, tag_id) == 0

        # Verify tag is removed from variant
        resp = tags_client.get("/api/tags/variant/rs12345", params={"sample_id": 1})
        tag_names = [t["name"] for t in resp.json()]
        assert "Cascade Test" not in tag_names


# ═══════════════════════════════════════════════════════════════════════
# POST /api/tags/variant — Add tag to variant
# DELETE /api/tags/variant — Remove tag from variant
# GET /api/tags/variant/{rsid} — Get variant tags
# ═══════════════════════════════════════════════════════════════════════


class TestVariantTagging:
    def test_add_tag_to_variant(self, tags_client: TestClient) -> None:
        """POST /api/tags/variant adds tag to variant."""
        # Get a predefined tag ID
        resp = tags_client.get("/api/tags", params={"sample_id": 1})
        tag_id = resp.json()[0]["id"]

        resp = tags_client.post(
            "/api/tags/variant",
            json={"sample_id": 1, "rsid": "rs12345", "tag_id": tag_id},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_remove_tag_from_variant(self, tags_client: TestClient) -> None:
        """DELETE /api/tags/variant removes tag from variant."""
        # Get a tag ID
        resp = tags_client.get("/api/tags", params={"sample_id": 1})
        tag_id = resp.json()[0]["id"]

        # Add tag first
        add_resp = tags_client.post(
            "/api/tags/variant",
            json={"sample_id": 1, "rsid": "rs429358", "tag_id": tag_id},
        )
        assert add_resp.status_code == 200

        # Remove it
        resp = tags_client.delete(
            "/api/tags/variant",
            params={"sample_id": 1, "rsid": "rs429358", "tag_id": tag_id},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

        # Verify removed
        resp = tags_client.get("/api/tags/variant/rs429358", params={"sample_id": 1})
        tag_ids = [t["id"] for t in resp.json()]
        assert tag_id not in tag_ids

    def test_get_variant_tags(self, tags_client: TestClient) -> None:
        """GET /api/tags/variant/{rsid} returns tags for variant."""
        # Get two tag IDs
        resp = tags_client.get("/api/tags", params={"sample_id": 1})
        all_tags = resp.json()
        tag_id_1 = all_tags[0]["id"]
        tag_id_2 = all_tags[1]["id"]

        # Add both tags to a variant
        resp1 = tags_client.post(
            "/api/tags/variant",
            json={"sample_id": 1, "rsid": "rs7412", "tag_id": tag_id_1},
        )
        assert resp1.status_code == 200
        resp2 = tags_client.post(
            "/api/tags/variant",
            json={"sample_id": 1, "rsid": "rs7412", "tag_id": tag_id_2},
        )
        assert resp2.status_code == 200

        # Get tags for the variant
        resp = tags_client.get("/api/tags/variant/rs7412", params={"sample_id": 1})
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2
        returned_ids = {t["id"] for t in data}
        assert tag_id_1 in returned_ids
        assert tag_id_2 in returned_ids


# ═══════════════════════════════════════════════════════════════════════
# GET /api/variants?tag=X — Filter + tags in response
# ═══════════════════════════════════════════════════════════════════════


class TestVariantTagFiltering:
    def test_filter_variants_by_tag(self, tags_client: TestClient) -> None:
        """GET /api/variants?tag=X returns only tagged variants."""
        # Get a tag
        resp = tags_client.get("/api/tags", params={"sample_id": 1})
        tag = resp.json()[0]
        tag_id = tag["id"]
        tag_name = tag["name"]

        # Tag one variant only
        tags_client.post(
            "/api/tags/variant",
            json={"sample_id": 1, "rsid": "rs12345", "tag_id": tag_id},
        )

        # Filter by tag
        resp = tags_client.get(
            "/api/variants",
            params={"sample_id": 1, "tag": tag_name},
        )
        assert resp.status_code == 200
        data = resp.json()
        items = data["items"]
        assert len(items) == 1
        assert items[0]["rsid"] == "rs12345"

    def test_variant_rows_include_tags(self, tags_client: TestClient) -> None:
        """GET /api/variants returns tags field in each row."""
        # Tag a variant
        resp = tags_client.get("/api/tags", params={"sample_id": 1})
        tag = resp.json()[0]
        tag_id = tag["id"]
        tag_name = tag["name"]

        tags_client.post(
            "/api/tags/variant",
            json={"sample_id": 1, "rsid": "rs429358", "tag_id": tag_id},
        )

        # List variants
        resp = tags_client.get("/api/variants", params={"sample_id": 1})
        assert resp.status_code == 200
        items = resp.json()["items"]

        # Find the tagged variant
        tagged = [v for v in items if v["rsid"] == "rs429358"]
        assert len(tagged) == 1
        assert tag_name in tagged[0]["tags"]

        # Untagged variants should have null/None tags
        untagged = [v for v in items if v["rsid"] != "rs429358"]
        for v in untagged:
            assert v.get("tags") is None or v["tags"] == []

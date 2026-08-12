"""Tests for haplogroup_assignments table migration and API (P3-33).

Covers:
  - T3-34 (extended): haplogroup_assignments table created in sample DBs
  - Sample schema migration: ensure_sample_schema_current() adds missing tables
  - Haplogroup API endpoints: GET /haplogroups, POST /haplogroups/run
  - Schema version stamping via PRAGMA user_version
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from backend.config import Settings
from backend.db.connection import DBRegistry, reset_registry
from backend.db.sample_schema import (
    SAMPLE_SCHEMA_VERSION,
    create_sample_tables,
    ensure_sample_schema_current,
)
from backend.db.tables import (
    findings,
    haplogroup_assignments,
    reference_metadata,
    sample_metadata_obj,
    samples,
)

# ── Sample schema migration tests ────────────────────────────────────────


class TestEnsureSampleSchemaCurrent:
    """Test ensure_sample_schema_current() for upgrading sample DBs."""

    def test_no_op_on_current_schema(self, sample_engine: sa.Engine) -> None:
        """Already-current schema returns False."""
        updated = ensure_sample_schema_current(sample_engine)
        assert updated is False

    def test_adds_missing_tables(self) -> None:
        """Adds haplogroup_assignments to a pre-P3-33 sample DB."""
        engine = sa.create_engine("sqlite://")

        # Create a subset of tables (simulating a pre-P3-33 DB)
        with engine.connect() as conn:
            conn.execute(sa.text("PRAGMA journal_mode=WAL"))
            conn.commit()

        # Create all tables except haplogroup_assignments
        tables_to_create = [
            t for t in sample_metadata_obj.sorted_tables if t.name != "haplogroup_assignments"
        ]
        for table in tables_to_create:
            table.create(engine, checkfirst=True)

        # Verify haplogroup_assignments doesn't exist yet
        inspector = sa.inspect(engine)
        assert "haplogroup_assignments" not in inspector.get_table_names()

        # Run migration
        updated = ensure_sample_schema_current(engine)
        assert updated is True

        # Verify table was added
        inspector2 = sa.inspect(engine)
        assert "haplogroup_assignments" in inspector2.get_table_names()

    def test_stamps_schema_version(self) -> None:
        """Schema version is set after create_sample_tables."""
        engine = sa.create_engine("sqlite://")
        create_sample_tables(engine)

        with engine.connect() as conn:
            row = conn.execute(sa.text("PRAGMA user_version")).fetchone()
            assert row[0] == SAMPLE_SCHEMA_VERSION

    def test_upgrades_schema_version(self) -> None:
        """ensure_sample_schema_current bumps version on old DBs."""
        engine = sa.create_engine("sqlite://")

        # Set old version
        with engine.connect() as conn:
            conn.execute(sa.text("PRAGMA user_version = 1"))
            conn.commit()

        # Create tables manually (simulating old schema)
        sample_metadata_obj.create_all(engine, checkfirst=True)

        updated = ensure_sample_schema_current(engine)
        # No tables actually missing (all created), but version was old
        assert updated is False  # No new tables, but version stamped

        with engine.connect() as conn:
            row = conn.execute(sa.text("PRAGMA user_version")).fetchone()
            assert row[0] == SAMPLE_SCHEMA_VERSION

    def test_haplogroup_table_columns(self, sample_engine: sa.Engine) -> None:
        """haplogroup_assignments table has the expected columns per PRD §2.6."""
        inspector = sa.inspect(sample_engine)
        columns = {col["name"] for col in inspector.get_columns("haplogroup_assignments")}
        expected = {
            "id",
            "type",
            "haplogroup",
            "confidence",
            "defining_snps_present",
            "defining_snps_total",
            "assigned_at",
        }
        assert expected == columns

    def test_insert_and_read(self, sample_engine: sa.Engine) -> None:
        """Can insert and read haplogroup_assignments rows."""
        with sample_engine.begin() as conn:
            conn.execute(
                sa.insert(haplogroup_assignments),
                {
                    "type": "mt",
                    "haplogroup": "H1a",
                    "confidence": 0.94,
                    "defining_snps_present": 16,
                    "defining_snps_total": 17,
                },
            )

        with sample_engine.connect() as conn:
            rows = conn.execute(sa.select(haplogroup_assignments)).fetchall()
            assert len(rows) == 1
            row = rows[0]
            assert row.type == "mt"
            assert row.haplogroup == "H1a"
            assert row.confidence == pytest.approx(0.94)
            assert row.defining_snps_present == 16
            assert row.defining_snps_total == 17


# ── Haplogroup API tests ─────────────────────────────────────────────────


@pytest.fixture
def tmp_data_dir(tmp_path: Path) -> Path:
    """Temporary data directory for API tests."""
    (tmp_path / "samples").mkdir()
    (tmp_path / "downloads").mkdir()
    (tmp_path / "logs").mkdir()
    return tmp_path


@pytest.fixture
def haplogroup_client(tmp_data_dir: Path) -> TestClient:
    """FastAPI TestClient with sample DB containing haplogroup data."""
    settings = Settings(data_dir=tmp_data_dir, wal_mode=False)

    # Create reference DB with samples table
    ref_path = settings.reference_db_path
    ref_engine = sa.create_engine(f"sqlite:///{ref_path}")
    reference_metadata.create_all(ref_engine)

    # Register a sample (relative path mirrors production behavior)
    sample_db_rel = Path("samples") / "sample_1.db"
    sample_db_path = tmp_data_dir / sample_db_rel
    with ref_engine.begin() as conn:
        conn.execute(
            samples.insert(),
            {
                "name": "Test Sample",
                "db_path": str(sample_db_rel),
                "file_format": "23andme_v5",
            },
        )
    ref_engine.dispose()

    # Create sample DB with haplogroup data
    sample_engine = sa.create_engine(f"sqlite:///{sample_db_path}")
    create_sample_tables(sample_engine)

    # Seed haplogroup assignments and findings
    with sample_engine.begin() as conn:
        conn.execute(
            sa.insert(haplogroup_assignments),
            {
                "type": "mt",
                "haplogroup": "H1a",
                "confidence": 0.9412,
                "defining_snps_present": 16,
                "defining_snps_total": 17,
            },
        )
        conn.execute(
            sa.insert(findings),
            {
                "module": "ancestry",
                "category": "haplogroup_mt",
                "evidence_level": 2,
                "haplogroup": "H1a",
                "finding_text": (
                    "Mitochondrial haplogroup: H1a (16/17 defining SNPs matched, 94% confidence)"
                ),
                "detail_json": json.dumps(
                    {
                        "tree_type": "mt",
                        "haplogroup": "H1a",
                        "confidence": 0.9412,
                        "defining_snps_present": 16,
                        "defining_snps_total": 17,
                        "traversal_path": [
                            {"haplogroup": "L3", "snps_present": 3, "snps_total": 3},
                            {"haplogroup": "N", "snps_present": 5, "snps_total": 5},
                            {"haplogroup": "H", "snps_present": 2, "snps_total": 2},
                            {"haplogroup": "H1", "snps_present": 1, "snps_total": 1},
                            {"haplogroup": "H1a", "snps_present": 1, "snps_total": 2},
                        ],
                        "path_string": "L3 → N → H → H1 → H1a",
                    }
                ),
            },
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


class TestHaplogroupAPI:
    """Test haplogroup API endpoints."""

    def test_get_haplogroups(self, haplogroup_client: TestClient) -> None:
        """GET /haplogroups returns assignments with traversal path."""
        resp = haplogroup_client.get("/api/analysis/ancestry/haplogroups", params={"sample_id": 1})
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["assignments"]) == 1

        mt = data["assignments"][0]
        assert mt["type"] == "mt"
        assert mt["haplogroup"] == "H1a"
        assert mt["confidence"] == pytest.approx(0.9412)
        assert mt["defining_snps_present"] == 16
        assert mt["defining_snps_total"] == 17
        assert len(mt["traversal_path"]) == 5
        assert mt["traversal_path"][0]["haplogroup"] == "L3"
        assert mt["traversal_path"][-1]["haplogroup"] == "H1a"
        assert "H1a" in mt["finding_text"]

    def test_get_haplogroups_empty(self, tmp_data_dir: Path) -> None:
        """GET /haplogroups returns empty list when no assignments exist."""
        settings = Settings(data_dir=tmp_data_dir, wal_mode=False)

        ref_path = settings.reference_db_path
        ref_engine = sa.create_engine(f"sqlite:///{ref_path}")
        reference_metadata.create_all(ref_engine)

        sample_db_rel = Path("samples") / "sample_empty.db"
        sample_db_path = tmp_data_dir / sample_db_rel
        with ref_engine.begin() as conn:
            conn.execute(
                samples.insert(),
                {
                    "name": "Empty Sample",
                    "db_path": str(sample_db_rel),
                    "file_format": "23andme_v5",
                },
            )
        ref_engine.dispose()

        sample_engine = sa.create_engine(f"sqlite:///{sample_db_path}")
        create_sample_tables(sample_engine)
        sample_engine.dispose()

        with (
            patch("backend.main.get_settings", return_value=settings),
            patch("backend.db.connection.get_settings", return_value=settings),
        ):
            reset_registry()

            from backend.main import create_app

            app = create_app()
            with TestClient(app) as tc:
                resp = tc.get("/api/analysis/ancestry/haplogroups", params={"sample_id": 1})
                assert resp.status_code == 200
                data = resp.json()
                assert data["assignments"] == []

            reset_registry()

    def test_get_haplogroups_not_found(self, haplogroup_client: TestClient) -> None:
        """GET /haplogroups returns 404 for nonexistent sample."""
        resp = haplogroup_client.get(
            "/api/analysis/ancestry/haplogroups", params={"sample_id": 999}
        )
        assert resp.status_code == 404

    def test_get_haplogroups_withholds_cross_assignment_held_pair(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Separate safe assignments cannot combine into held guidance."""
        from backend.api.routes import ancestry as ancestry_route

        sample_engine = sa.create_engine("sqlite://")
        create_sample_tables(sample_engine)
        with sample_engine.begin() as conn:
            conn.execute(
                haplogroup_assignments.insert(),
                [
                    {
                        "type": "mt",
                        "haplogroup": "CYP2D6",
                        "confidence": 1.0,
                        "defining_snps_present": 1,
                        "defining_snps_total": 1,
                    },
                    {
                        "type": "Y",
                        "haplogroup": "tamoxifen",
                        "confidence": 1.0,
                        "defining_snps_present": 1,
                        "defining_snps_total": 1,
                    },
                ],
            )

        monkeypatch.setattr(ancestry_route, "_get_sample_engine", lambda _sample_id: sample_engine)
        try:
            response = ancestry_route.get_haplogroup_assignments(sample_id=1)
            assert response.assignments == []
        finally:
            sample_engine.dispose()

    def test_run_haplogroups_withholds_cross_assignment_held_pair(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The synchronous run response has the same aggregate guard."""
        from backend.analysis.ancestry import (
            HaplogroupResult,
            HaplogroupTraversalStep,
        )
        from backend.api.routes import ancestry as ancestry_route

        sample_engine = sa.create_engine("sqlite://")
        create_sample_tables(sample_engine)
        results = [
            HaplogroupResult(
                tree_type="mt",
                haplogroup="CYP2D6",
                confidence=1.0,
                defining_snps_present=1,
                defining_snps_total=1,
                traversal_path=[HaplogroupTraversalStep("H", 1, 1)],
                assignment_time_ms=0.0,
            ),
            HaplogroupResult(
                tree_type="Y",
                haplogroup="tamoxifen",
                confidence=1.0,
                defining_snps_present=1,
                defining_snps_total=1,
                traversal_path=[HaplogroupTraversalStep("R", 1, 1)],
                assignment_time_ms=0.0,
            ),
        ]
        monkeypatch.setattr(ancestry_route, "_get_sample_engine", lambda _sample_id: sample_engine)
        monkeypatch.setattr(
            "backend.analysis.ancestry.run_haplogroup_assignment",
            lambda _sample_engine: results,
        )

        try:
            response = ancestry_route.run_haplogroup(sample_id=1)
            assert response.assignments == []
        finally:
            sample_engine.dispose()


# ── DBRegistry integration test ──────────────────────────────────────────


class TestDBRegistrySchemaMigration:
    """Test that get_sample_engine triggers schema migration."""

    def test_get_sample_engine_upgrades(self, tmp_data_dir: Path) -> None:
        """get_sample_engine calls ensure_sample_schema_current."""
        settings = Settings(data_dir=tmp_data_dir, wal_mode=False)

        # Create reference DB
        ref_path = settings.reference_db_path
        ref_engine = sa.create_engine(f"sqlite:///{ref_path}")
        reference_metadata.create_all(ref_engine)
        ref_engine.dispose()

        # Create a sample DB *without* haplogroup_assignments
        sample_db_path = tmp_data_dir / "samples" / "sample_old.db"
        old_engine = sa.create_engine(f"sqlite:///{sample_db_path}")
        tables_to_create = [
            t for t in sample_metadata_obj.sorted_tables if t.name != "haplogroup_assignments"
        ]
        for table in tables_to_create:
            table.create(old_engine, checkfirst=True)
        old_engine.dispose()

        # Access via registry
        registry = DBRegistry(settings)
        try:
            engine = registry.get_sample_engine(sample_db_path)

            # haplogroup_assignments should now exist
            inspector = sa.inspect(engine)
            assert "haplogroup_assignments" in inspector.get_table_names()
        finally:
            registry.dispose_all()

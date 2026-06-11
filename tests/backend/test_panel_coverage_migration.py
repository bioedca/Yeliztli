"""Tests for panel_coverage table migration (P3-58).

Covers:
  - panel_coverage table created in sample DBs
  - Sample schema migration: ensure_sample_schema_current() adds panel_coverage
  - Schema version bumped to 3
  - CRUD operations on panel_coverage
  - Coverage status validation (called/no_call/not_on_array)
"""

from __future__ import annotations

import pytest
import sqlalchemy as sa

from backend.db.sample_schema import (
    SAMPLE_SCHEMA_VERSION,
    ensure_sample_schema_current,
)
from backend.db.tables import (
    panel_coverage,
    sample_metadata_obj,
)


class TestPanelCoverageSchema:
    """Test panel_coverage table definition and creation."""

    def test_schema_version_is_current(self) -> None:
        """SAMPLE_SCHEMA_VERSION tracks the latest additive sample DB migration."""
        assert SAMPLE_SCHEMA_VERSION == 12

    def test_table_created_on_new_sample(self, sample_engine: sa.Engine) -> None:
        """panel_coverage exists in freshly created sample DBs."""
        inspector = sa.inspect(sample_engine)
        assert "panel_coverage" in inspector.get_table_names()

    def test_table_columns(self, sample_engine: sa.Engine) -> None:
        """panel_coverage has the expected columns per PRD."""
        inspector = sa.inspect(sample_engine)
        columns = {col["name"] for col in inspector.get_columns("panel_coverage")}
        expected = {"module", "rsid", "gene", "expected_trait", "coverage_status"}
        assert expected == columns

    def test_composite_primary_key(self, sample_engine: sa.Engine) -> None:
        """Primary key is (module, rsid)."""
        inspector = sa.inspect(sample_engine)
        pk = inspector.get_pk_constraint("panel_coverage")
        assert set(pk["constrained_columns"]) == {"module", "rsid"}

    def test_insert_and_read(self, sample_engine: sa.Engine) -> None:
        """Can insert and read panel_coverage rows."""
        with sample_engine.begin() as conn:
            conn.execute(
                sa.insert(panel_coverage),
                {
                    "module": "skin",
                    "rsid": "rs1805007",
                    "gene": "MC1R",
                    "expected_trait": "R151C red hair variant",
                    "coverage_status": "called",
                },
            )

        with sample_engine.connect() as conn:
            rows = conn.execute(sa.select(panel_coverage)).fetchall()
            assert len(rows) == 1
            row = rows[0]
            assert row.module == "skin"
            assert row.rsid == "rs1805007"
            assert row.gene == "MC1R"
            assert row.expected_trait == "R151C red hair variant"
            assert row.coverage_status == "called"

    def test_all_coverage_statuses(self, sample_engine: sa.Engine) -> None:
        """All three coverage statuses can be stored."""
        rows = [
            {
                "module": "fitness",
                "rsid": "rs1815739",
                "gene": "ACTN3",
                "expected_trait": "Sprint/power performance",
                "coverage_status": "called",
            },
            {
                "module": "fitness",
                "rsid": "rs4341",
                "gene": "ACE",
                "expected_trait": "Endurance capacity",
                "coverage_status": "no_call",
            },
            {
                "module": "fitness",
                "rsid": "rs99999999",
                "gene": "FAKE",
                "expected_trait": "Test trait",
                "coverage_status": "not_on_array",
            },
        ]
        with sample_engine.begin() as conn:
            conn.execute(sa.insert(panel_coverage), rows)

        with sample_engine.connect() as conn:
            result = conn.execute(
                sa.select(panel_coverage.c.coverage_status).order_by(panel_coverage.c.rsid)
            ).fetchall()
            statuses = {r[0] for r in result}
            assert statuses == {"called", "no_call", "not_on_array"}

    def test_nullable_gene_and_trait(self, sample_engine: sa.Engine) -> None:
        """gene and expected_trait are nullable."""
        with sample_engine.begin() as conn:
            conn.execute(
                sa.insert(panel_coverage),
                {
                    "module": "allergy",
                    "rsid": "rs20541",
                    "gene": None,
                    "expected_trait": None,
                    "coverage_status": "called",
                },
            )

        with sample_engine.connect() as conn:
            row = conn.execute(sa.select(panel_coverage)).fetchone()
            assert row.gene is None
            assert row.expected_trait is None

    def test_duplicate_module_rsid_rejected(self, sample_engine: sa.Engine) -> None:
        """Duplicate (module, rsid) raises IntegrityError."""
        with sample_engine.begin() as conn:
            conn.execute(
                sa.insert(panel_coverage),
                {
                    "module": "skin",
                    "rsid": "rs1805007",
                    "gene": "MC1R",
                    "expected_trait": "R151C",
                    "coverage_status": "called",
                },
            )

        with pytest.raises(sa.exc.IntegrityError):
            with sample_engine.begin() as conn:
                conn.execute(
                    sa.insert(panel_coverage),
                    {
                        "module": "skin",
                        "rsid": "rs1805007",
                        "gene": "MC1R",
                        "expected_trait": "R151C",
                        "coverage_status": "no_call",
                    },
                )

    def test_same_rsid_different_modules(self, sample_engine: sa.Engine) -> None:
        """Same rsid in different modules is allowed."""
        rows = [
            {
                "module": "skin",
                "rsid": "rs1805007",
                "gene": "MC1R",
                "expected_trait": "Pigmentation",
                "coverage_status": "called",
            },
            {
                "module": "cancer",
                "rsid": "rs1805007",
                "gene": "MC1R",
                "expected_trait": "Melanoma risk",
                "coverage_status": "called",
            },
        ]
        with sample_engine.begin() as conn:
            conn.execute(sa.insert(panel_coverage), rows)

        with sample_engine.connect() as conn:
            count = conn.execute(sa.select(sa.func.count()).select_from(panel_coverage)).scalar()
            assert count == 2

    def test_query_coverage_by_module(self, sample_engine: sa.Engine) -> None:
        """Can filter coverage by module."""
        rows = [
            {
                "module": "skin",
                "rsid": "rs1805007",
                "gene": "MC1R",
                "expected_trait": "UV response",
                "coverage_status": "called",
            },
            {
                "module": "skin",
                "rsid": "rs61816761",
                "gene": "FLG",
                "expected_trait": "Skin barrier",
                "coverage_status": "not_on_array",
            },
            {
                "module": "fitness",
                "rsid": "rs1815739",
                "gene": "ACTN3",
                "expected_trait": "Sprint",
                "coverage_status": "called",
            },
        ]
        with sample_engine.begin() as conn:
            conn.execute(sa.insert(panel_coverage), rows)

        with sample_engine.connect() as conn:
            skin_rows = conn.execute(
                sa.select(panel_coverage).where(panel_coverage.c.module == "skin")
            ).fetchall()
            assert len(skin_rows) == 2

    def test_coverage_status_not_nullable(self, sample_engine: sa.Engine) -> None:
        """coverage_status column rejects NULL values."""
        with pytest.raises(sa.exc.IntegrityError):
            with sample_engine.begin() as conn:
                conn.execute(
                    sa.insert(panel_coverage),
                    {
                        "module": "test",
                        "rsid": "rs123",
                        "gene": "GENE",
                        "expected_trait": "Trait",
                        "coverage_status": None,
                    },
                )

    def test_invalid_coverage_status_rejected(self, sample_engine: sa.Engine) -> None:
        """CHECK constraint rejects invalid coverage_status values."""
        with pytest.raises(sa.exc.IntegrityError):
            with sample_engine.begin() as conn:
                conn.execute(
                    sa.insert(panel_coverage),
                    {
                        "module": "test",
                        "rsid": "rs123",
                        "gene": "GENE",
                        "expected_trait": "Trait",
                        "coverage_status": "invalid_status",
                    },
                )


class TestPanelCoverageMigration:
    """Test ensure_sample_schema_current() adds panel_coverage to old DBs."""

    def test_adds_panel_coverage_to_v2_db(self) -> None:
        """Upgrades a v2 sample DB (pre-P3-58) to include panel_coverage."""
        engine = sa.create_engine("sqlite://")

        with engine.connect() as conn:
            conn.execute(sa.text("PRAGMA journal_mode=WAL"))
            conn.commit()

        # Create all tables except panel_coverage (simulating v2 DB)
        tables_to_create = [
            t for t in sample_metadata_obj.sorted_tables if t.name != "panel_coverage"
        ]
        for table in tables_to_create:
            table.create(engine, checkfirst=True)

        # Set v2 schema version
        with engine.connect() as conn:
            conn.execute(sa.text("PRAGMA user_version = 2"))
            conn.commit()

        # Verify panel_coverage doesn't exist yet
        inspector = sa.inspect(engine)
        assert "panel_coverage" not in inspector.get_table_names()

        # Run migration
        updated = ensure_sample_schema_current(engine)
        assert updated is True

        # Verify table was added
        inspector2 = sa.inspect(engine)
        assert "panel_coverage" in inspector2.get_table_names()

        # Verify version stamped to 3
        with engine.connect() as conn:
            row = conn.execute(sa.text("PRAGMA user_version")).fetchone()
            assert row[0] == SAMPLE_SCHEMA_VERSION

    def test_no_op_on_current_schema(self, sample_engine: sa.Engine) -> None:
        """Already-current schema returns False."""
        updated = ensure_sample_schema_current(sample_engine)
        assert updated is False

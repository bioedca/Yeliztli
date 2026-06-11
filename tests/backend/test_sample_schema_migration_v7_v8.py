"""Tests for the v7 → v8 per-sample schema migration.

AncestryDNA Plan §10.4 step 3 (the schema bump itself):

  * v8 adds four provenance columns (``source``, ``concordance``,
    ``discordant_alt_genotype``, ``alt_rsid``) to ``raw_variants``; each is
    ``TEXT NOT NULL DEFAULT ''`` so unmerged samples carry no semantic load.
  * v8 creates the single-row ``merge_provenance`` table with a
    ``CheckConstraint("id = 1")`` enforcing one row max.

This test fixture builds a synthetic v7 sample DB (the predecessor schema —
``raw_variants`` without the provenance columns, no ``merge_provenance``
table) and exercises ``ensure_sample_schema_current()`` against it.

Step 64 introduces the ``is_merged_sample`` PK divergence; this step
intentionally leaves the in-place v7→v8 upgrade with ``rsid`` PK on
``raw_variants`` (Plan §10.4 final paragraph: "The (chrom, pos) PK
divergence does not apply to in-place v7→v8 upgrades").
"""

from __future__ import annotations

from pathlib import Path

import pytest
import sqlalchemy as sa

from backend.db.sample_schema import (
    SAMPLE_SCHEMA_VERSION,
    create_sample_tables,
    ensure_sample_schema_current,
)
from backend.db.tables import raw_variants as raw_variants_table

V8_PROVENANCE_COLUMNS = (
    "source",
    "concordance",
    "discordant_alt_genotype",
    "alt_rsid",
)


def _create_v7_sample_db(db_path: Path) -> sa.Engine:
    """Materialise a v7-shaped sample DB on disk.

    Pre-v8 ``raw_variants`` has only ``(rsid, chrom, pos, genotype)``; no
    ``merge_provenance`` table; ``user_version = 7``.
    """
    engine = sa.create_engine(f"sqlite:///{db_path}")
    with engine.connect() as conn:
        conn.execute(sa.text("PRAGMA journal_mode=WAL"))
        conn.execute(
            sa.text(
                """CREATE TABLE raw_variants (
                    rsid TEXT PRIMARY KEY,
                    chrom TEXT NOT NULL,
                    pos INTEGER NOT NULL,
                    genotype TEXT NOT NULL
                )"""
            )
        )
        conn.execute(sa.text("PRAGMA user_version = 7"))
        conn.commit()
    return engine


def _column_names(engine: sa.Engine, table: str) -> set[str]:
    inspector = sa.inspect(engine)
    return {col["name"] for col in inspector.get_columns(table)}


def _column_info(engine: sa.Engine, table: str) -> dict[str, dict]:
    inspector = sa.inspect(engine)
    return {col["name"]: col for col in inspector.get_columns(table)}


class TestRawVariantsProvenanceColumns:
    def test_v7_db_lacks_provenance_columns(self, tmp_path: Path) -> None:
        engine = _create_v7_sample_db(tmp_path / "sample_001.db")
        cols = _column_names(engine, "raw_variants")
        for col in V8_PROVENANCE_COLUMNS:
            assert col not in cols

    def test_upgrade_adds_all_four_provenance_columns(self, tmp_path: Path) -> None:
        engine = _create_v7_sample_db(tmp_path / "sample_001.db")

        updated = ensure_sample_schema_current(engine)
        assert updated is True

        cols = _column_names(engine, "raw_variants")
        for col in V8_PROVENANCE_COLUMNS:
            assert col in cols

    def test_upgrade_preserves_existing_rows(self, tmp_path: Path) -> None:
        engine = _create_v7_sample_db(tmp_path / "sample_001.db")
        with engine.begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO raw_variants (rsid, chrom, pos, genotype) "
                    "VALUES (:rsid, :chrom, :pos, :gt)"
                ),
                [
                    {"rsid": "rs429358", "chrom": "19", "pos": 45411941, "gt": "TT"},
                    {"rsid": "rs7412", "chrom": "19", "pos": 45412079, "gt": "CC"},
                ],
            )

        ensure_sample_schema_current(engine)

        with engine.connect() as conn:
            rows = conn.execute(
                sa.text(
                    "SELECT rsid, chrom, pos, genotype, source, concordance, "
                    "discordant_alt_genotype, alt_rsid FROM raw_variants "
                    "ORDER BY rsid"
                )
            ).fetchall()

        assert len(rows) == 2
        # Pre-existing rows: original payload intact, new columns default to ''.
        for row in rows:
            assert row.source == ""
            assert row.concordance == ""
            assert row.discordant_alt_genotype == ""
            assert row.alt_rsid == ""
        rsids = {row.rsid for row in rows}
        assert rsids == {"rs429358", "rs7412"}

    def test_new_provenance_columns_are_not_null(self, tmp_path: Path) -> None:
        """ALTER ... ADD COLUMN ... NOT NULL DEFAULT '' enforces the NOT NULL contract."""
        engine = _create_v7_sample_db(tmp_path / "sample_001.db")
        ensure_sample_schema_current(engine)

        info = _column_info(engine, "raw_variants")
        for col in V8_PROVENANCE_COLUMNS:
            assert info[col]["nullable"] is False, f"{col} should be NOT NULL after v8 migration"

    def test_explicit_null_into_new_columns_is_rejected(self, tmp_path: Path) -> None:
        engine = _create_v7_sample_db(tmp_path / "sample_001.db")
        ensure_sample_schema_current(engine)

        with pytest.raises(sa.exc.IntegrityError):
            with engine.begin() as conn:
                conn.execute(
                    sa.text(
                        "INSERT INTO raw_variants "
                        "(rsid, chrom, pos, genotype, source) "
                        "VALUES ('rs1', '1', 100, 'AA', NULL)"
                    )
                )

    def test_insert_without_provenance_columns_uses_defaults(self, tmp_path: Path) -> None:
        engine = _create_v7_sample_db(tmp_path / "sample_001.db")
        ensure_sample_schema_current(engine)

        with engine.begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO raw_variants (rsid, chrom, pos, genotype) "
                    "VALUES ('rs1', '1', 100, 'AA')"
                )
            )

        with engine.connect() as conn:
            row = conn.execute(
                sa.text(
                    "SELECT source, concordance, discordant_alt_genotype, alt_rsid "
                    "FROM raw_variants WHERE rsid = 'rs1'"
                )
            ).one()

        assert row.source == ""
        assert row.concordance == ""
        assert row.discordant_alt_genotype == ""
        assert row.alt_rsid == ""


class TestMergeProvenanceTable:
    def test_v7_db_lacks_merge_provenance(self, tmp_path: Path) -> None:
        engine = _create_v7_sample_db(tmp_path / "sample_001.db")
        inspector = sa.inspect(engine)
        assert "merge_provenance" not in inspector.get_table_names()

    def test_upgrade_creates_merge_provenance_table(self, tmp_path: Path) -> None:
        engine = _create_v7_sample_db(tmp_path / "sample_001.db")
        ensure_sample_schema_current(engine)

        inspector = sa.inspect(engine)
        assert "merge_provenance" in inspector.get_table_names()

        cols = _column_names(engine, "merge_provenance")
        assert cols == {
            "id",
            "merged_at",
            "strategy",
            "source_sample_ids",
            "source_file_hashes",
            "concordance_summary",
        }

    def test_merge_provenance_check_constraint_enforces_single_row(self, tmp_path: Path) -> None:
        engine = _create_v7_sample_db(tmp_path / "sample_001.db")
        ensure_sample_schema_current(engine)

        with engine.begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO merge_provenance "
                    "(id, strategy, source_sample_ids, source_file_hashes, "
                    "concordance_summary) "
                    "VALUES (1, 'flag_only', '[1,2]', '[\"h1\",\"h2\"]', '{}')"
                )
            )

        with pytest.raises(sa.exc.IntegrityError):
            with engine.begin() as conn:
                conn.execute(
                    sa.text(
                        "INSERT INTO merge_provenance "
                        "(id, strategy, source_sample_ids, source_file_hashes, "
                        "concordance_summary) "
                        "VALUES (2, 'flag_only', '[3,4]', '[\"h3\",\"h4\"]', '{}')"
                    )
                )

    def test_merge_provenance_required_columns_not_null(self, tmp_path: Path) -> None:
        engine = _create_v7_sample_db(tmp_path / "sample_001.db")
        ensure_sample_schema_current(engine)

        with pytest.raises(sa.exc.IntegrityError):
            with engine.begin() as conn:
                conn.execute(
                    sa.text(
                        "INSERT INTO merge_provenance "
                        "(id, strategy, source_sample_ids, source_file_hashes, "
                        "concordance_summary) "
                        "VALUES (1, NULL, '[1,2]', '[\"h1\",\"h2\"]', '{}')"
                    )
                )


class TestUpgradeStamping:
    def test_upgrade_stamps_v8(self, tmp_path: Path) -> None:
        engine = _create_v7_sample_db(tmp_path / "sample_001.db")
        ensure_sample_schema_current(engine)

        with engine.connect() as conn:
            row = conn.execute(sa.text("PRAGMA user_version")).fetchone()
        # v7 -> current stamps the latest version after all additive migrations.
        assert row[0] == SAMPLE_SCHEMA_VERSION

    def test_upgrade_is_idempotent(self, tmp_path: Path) -> None:
        engine = _create_v7_sample_db(tmp_path / "sample_001.db")

        first = ensure_sample_schema_current(engine)
        assert first is True

        # Second pass — already current, must be a no-op and must not raise
        # (would raise OperationalError "duplicate column name" if the v8
        # branch re-ran without its column-exists check).
        second = ensure_sample_schema_current(engine)
        assert second is False

        cols = _column_names(engine, "raw_variants")
        for col in V8_PROVENANCE_COLUMNS:
            assert col in cols

    def test_upgrade_returns_true_when_only_columns_change(self, tmp_path: Path) -> None:
        """Even when no *tables* are added by the upgrade (the merge_provenance
        path also fires), ``ensure_sample_schema_current`` reports True when
        the v8 column-add ran. Guards against a stale return-value contract.
        """
        engine = _create_v7_sample_db(tmp_path / "sample_001.db")
        updated = ensure_sample_schema_current(engine)
        assert updated is True


class TestFreshSampleStillCreatesV8Surfaces:
    """Sanity check that a freshly-created sample DB lands at v8 directly."""

    def test_fresh_db_has_provenance_columns_and_merge_provenance(
        self, sample_engine: sa.Engine
    ) -> None:
        cols = _column_names(sample_engine, "raw_variants")
        for col in V8_PROVENANCE_COLUMNS:
            assert col in cols

        inspector = sa.inspect(sample_engine)
        assert "merge_provenance" in inspector.get_table_names()

        with sample_engine.connect() as conn:
            row = conn.execute(sa.text("PRAGMA user_version")).fetchone()
        assert row[0] == SAMPLE_SCHEMA_VERSION


def _new_engine(db_path: Path) -> sa.Engine:
    return sa.create_engine(f"sqlite:///{db_path}")


def _pk_columns(engine: sa.Engine, table: str) -> list[str]:
    return sa.inspect(engine).get_pk_constraint(table)["constrained_columns"]


class TestIsMergedSampleFactory:
    """Step 64 — ``is_merged_sample`` factory parameter on
    ``create_sample_tables``.

    AncestryDNA Plan §10.4(a): merged-sample ``raw_variants`` uses
    ``(chrom, pos)`` as the primary key (the canonical merge key); unmerged
    sample DBs keep the historical ``rsid`` PK. Every other sample-DB table
    is identical across both branches.
    """

    def test_default_is_unmerged_rsid_pk(self, tmp_path: Path) -> None:
        engine = _new_engine(tmp_path / "default.db")
        create_sample_tables(engine)

        assert _pk_columns(engine, "raw_variants") == ["rsid"]

    def test_explicit_unmerged_keeps_rsid_pk(self, tmp_path: Path) -> None:
        engine = _new_engine(tmp_path / "unmerged.db")
        create_sample_tables(engine, is_merged_sample=False)

        assert _pk_columns(engine, "raw_variants") == ["rsid"]

    def test_merged_uses_chrom_pos_pk(self, tmp_path: Path) -> None:
        engine = _new_engine(tmp_path / "merged.db")
        create_sample_tables(engine, is_merged_sample=True)

        assert _pk_columns(engine, "raw_variants") == ["chrom", "pos"]

    def test_merged_db_has_full_v8_surface(self, tmp_path: Path) -> None:
        """A merged DB still ships every v8 column + the merge_provenance
        table — the PK is the *only* difference vs. the unmerged shape."""
        engine = _new_engine(tmp_path / "merged.db")
        create_sample_tables(engine, is_merged_sample=True)

        cols = _column_names(engine, "raw_variants")
        for col in V8_PROVENANCE_COLUMNS:
            assert col in cols
        for col in ("rsid", "chrom", "pos", "genotype"):
            assert col in cols

        inspector = sa.inspect(engine)
        assert "merge_provenance" in inspector.get_table_names()
        # annotated_variants stays rsid-PK per Plan §10.4(a) invariant.
        assert _pk_columns(engine, "annotated_variants") == ["rsid"]

    def test_merged_db_stamped_at_v8(self, tmp_path: Path) -> None:
        engine = _new_engine(tmp_path / "merged.db")
        create_sample_tables(engine, is_merged_sample=True)

        with engine.connect() as conn:
            row = conn.execute(sa.text("PRAGMA user_version")).fetchone()
        assert row[0] == SAMPLE_SCHEMA_VERSION

    def test_merged_pk_rejects_duplicate_coordinate(self, tmp_path: Path) -> None:
        """(chrom, pos) PK rejects a second insert at the same coordinate even
        when the rsid differs — proves the PK swapped sides."""
        engine = _new_engine(tmp_path / "merged.db")
        create_sample_tables(engine, is_merged_sample=True)

        with engine.begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO raw_variants (rsid, chrom, pos, genotype) "
                    "VALUES ('rs100', '1', 12345, 'AG')"
                )
            )

        with pytest.raises(sa.exc.IntegrityError):
            with engine.begin() as conn:
                conn.execute(
                    sa.text(
                        "INSERT INTO raw_variants (rsid, chrom, pos, genotype) "
                        "VALUES ('rs999', '1', 12345, 'CT')"
                    )
                )

    def test_merged_pk_allows_duplicate_rsid_across_coordinates(self, tmp_path: Path) -> None:
        """The mirror of the previous test: the same rsid at two different
        coordinates is now legal (it would have collided under the old
        ``rsid`` PK). Locks the swapped-PK semantics from the other side."""
        engine = _new_engine(tmp_path / "merged.db")
        create_sample_tables(engine, is_merged_sample=True)

        with engine.begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO raw_variants (rsid, chrom, pos, genotype) "
                    "VALUES (:rsid, :chrom, :pos, :gt)"
                ),
                [
                    {"rsid": "rs100", "chrom": "1", "pos": 12345, "gt": "AG"},
                    {"rsid": "rs100", "chrom": "2", "pos": 67890, "gt": "CT"},
                ],
            )

        with engine.connect() as conn:
            count = conn.execute(sa.text("SELECT COUNT(*) FROM raw_variants")).scalar()
        assert count == 2

    def test_unmerged_pk_rejects_duplicate_rsid(self, tmp_path: Path) -> None:
        """Negative control: the default (unmerged) PK is still ``rsid``, so
        a duplicate-rsid insert continues to fail. Pins the regression
        boundary: ``is_merged_sample=False`` must not silently lift this."""
        engine = _new_engine(tmp_path / "unmerged.db")
        create_sample_tables(engine, is_merged_sample=False)

        with engine.begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO raw_variants (rsid, chrom, pos, genotype) "
                    "VALUES ('rs100', '1', 12345, 'AG')"
                )
            )

        with pytest.raises(sa.exc.IntegrityError):
            with engine.begin() as conn:
                conn.execute(
                    sa.text(
                        "INSERT INTO raw_variants (rsid, chrom, pos, genotype) "
                        "VALUES ('rs100', '2', 67890, 'CT')"
                    )
                )

    def test_standard_reader_returns_rows_from_both(self, tmp_path: Path) -> None:
        """The module-level ``raw_variants`` Table object — the import every
        annotation reader uses (``backend/annotation/{clinvar,vep_bundle,
        dbsnp,vcfanno_runner}.py``, ``backend/ingestion/vcf_export.py``) —
        must continue to read rows out of *both* PK variants. PK is a
        constraint, not a query requirement, so ``sa.select(raw_variants)``
        should round-trip identical rows regardless of the on-disk PK."""
        rows = [
            {"rsid": "rs429358", "chrom": "19", "pos": 45411941, "gt": "TT"},
            {"rsid": "rs7412", "chrom": "19", "pos": 45412079, "gt": "CC"},
            {"rsid": "rs1801133", "chrom": "1", "pos": 11856378, "gt": "AG"},
        ]

        unmerged = _new_engine(tmp_path / "unmerged.db")
        create_sample_tables(unmerged, is_merged_sample=False)
        merged = _new_engine(tmp_path / "merged.db")
        create_sample_tables(merged, is_merged_sample=True)

        for engine in (unmerged, merged):
            with engine.begin() as conn:
                conn.execute(
                    sa.text(
                        "INSERT INTO raw_variants (rsid, chrom, pos, genotype) "
                        "VALUES (:rsid, :chrom, :pos, :gt)"
                    ),
                    rows,
                )

        select_stmt = sa.select(
            raw_variants_table.c.rsid,
            raw_variants_table.c.chrom,
            raw_variants_table.c.pos,
            raw_variants_table.c.genotype,
        ).order_by(raw_variants_table.c.rsid)

        with unmerged.connect() as conn:
            unmerged_rows = conn.execute(select_stmt).all()
        with merged.connect() as conn:
            merged_rows = conn.execute(select_stmt).all()

        assert unmerged_rows == merged_rows
        assert len(unmerged_rows) == 3
        assert {r.rsid for r in unmerged_rows} == {
            "rs429358",
            "rs7412",
            "rs1801133",
        }

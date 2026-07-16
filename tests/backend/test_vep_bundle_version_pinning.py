"""VEP bundle version is pinned to the generation the annotation queried (#1953).

``run_annotation`` resolves the VEP engine once, up front, then stamps coverage
telemetry / annotation_state / the reference snapshot with a single bundle
version. Before this fix the version was resolved *after* the run inside
``_build_coverage_stats``, so a bundle replacement that landed mid-run made the
stamped version describe a newer artifact than the one actually queried.

These tests prove the version is captured at engine-resolve time and is not
re-resolved later, so a controlled mid-run bump cannot change the stamp.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
import sqlalchemy as sa
from sqlalchemy.pool import StaticPool

from backend.annotation import engine as engine_mod
from backend.annotation.engine import _build_coverage_stats, run_annotation
from backend.db.sample_schema import create_sample_tables
from backend.db.tables import database_versions, raw_variants, reference_metadata

OLD_VERSION = "v2.0.0"
NEWER_VERSION = "v9.9.9"


def _new_engine() -> sa.Engine:
    return sa.create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )


def _seed_vep_bundle(engine: sa.Engine) -> None:
    """Create an empty (but structurally valid) VEP bundle.

    The pinning under test is independent of whether any variant matches, so an
    empty ``vep_annotations`` table is enough: the annotation batch loop still
    runs and fires ``progress_callback`` between engine resolution and coverage
    telemetry, which is where the interleave is injected.
    """
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
        conn.execute(sa.text("CREATE INDEX idx_vep_chrom_pos ON vep_annotations(chrom, pos)"))


def _stamp_version(reference_engine: sa.Engine, version: str) -> None:
    with reference_engine.begin() as conn:
        conn.execute(
            sa.delete(database_versions).where(database_versions.c.db_name == "vep_bundle")
        )
        conn.execute(database_versions.insert().values(db_name="vep_bundle", version=version))


def _read_stamped_version(reference_engine: sa.Engine) -> str | None:
    with reference_engine.connect() as conn:
        return conn.execute(
            sa.select(database_versions.c.version).where(
                database_versions.c.db_name == "vep_bundle"
            )
        ).scalar()


@pytest.fixture
def reference_engine() -> sa.Engine:
    engine = _new_engine()
    reference_metadata.create_all(engine)
    _stamp_version(engine, OLD_VERSION)
    return engine


@pytest.fixture
def registry(reference_engine: sa.Engine) -> MagicMock:
    """Minimal registry: VEP + reference only, other sources unavailable."""
    vep_engine = _new_engine()
    _seed_vep_bundle(vep_engine)

    reg = MagicMock()
    reg.reference_engine = reference_engine
    type(reg).vep_engine = property(lambda self: vep_engine)

    def _unavailable(self):
        raise RuntimeError("source intentionally unavailable for #1953 test")

    type(reg).gnomad_engine = property(_unavailable)
    type(reg).dbnsfp_engine = property(_unavailable)
    return reg


@pytest.fixture
def sample_engine() -> sa.Engine:
    engine = _new_engine()
    create_sample_tables(engine, is_merged_sample=False)
    with engine.begin() as conn:
        conn.execute(
            raw_variants.insert(),
            [
                {"rsid": "rs1801133", "chrom": "1", "pos": 11856378, "genotype": "GA"},
                {"rsid": "rs1801131", "chrom": "1", "pos": 11854476, "genotype": "AC"},
            ],
        )
    return engine


def test_mid_run_bundle_replacement_does_not_restamp_coverage(
    sample_engine: sa.Engine,
    reference_engine: sa.Engine,
    registry: MagicMock,
) -> None:
    """A bundle version bump landing mid-run must not change the stamped version.

    The ``progress_callback`` fires after the VEP engine is resolved but before
    coverage telemetry is built, so bumping ``database_versions`` there is a
    faithful controlled interleaving of ``run_vep_bundle_update`` against a live
    annotation.
    """
    bumped = {"done": False}

    def bump_version_mid_run(_done: int, _total: int) -> None:
        if not bumped["done"]:
            _stamp_version(reference_engine, NEWER_VERSION)
            bumped["done"] = True

    result = run_annotation(sample_engine, registry, progress_callback=bump_version_mid_run)

    # The interleave really happened: the resolver would now return the newer
    # version, and the row is bumped.
    assert bumped["done"] is True
    assert _read_stamped_version(reference_engine) == NEWER_VERSION

    # ...yet coverage telemetry is stamped with the generation actually queried.
    assert result.coverage_stats["bundle_version"] == OLD_VERSION


def test_pinned_version_wins_over_late_resolution() -> None:
    """A pinned ``bundle_version`` is used verbatim; the resolver is not consulted."""
    sample_engine = _new_engine()
    create_sample_tables(sample_engine, is_merged_sample=False)
    with sample_engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO sample_metadata (id, name, file_format, file_hash) "
                "VALUES (1, 'fixture', '23andme_v5', 'h')"
            )
        )

    stats = _build_coverage_stats(
        sample_engine,
        MagicMock(),
        total_variants=10,
        vep_rsid_hits=4,
        vep_coord_fallback_hits=1,
        bundle_version="v-pinned",
    )
    assert stats["bundle_version"] == "v-pinned"


def test_absent_pin_falls_back_to_resolver(monkeypatch: pytest.MonkeyPatch) -> None:
    """With no pinned value, ``_build_coverage_stats`` still resolves a version.

    Preserves the pre-#1953 behaviour for any caller that does not pin.
    """
    sample_engine = _new_engine()
    create_sample_tables(sample_engine, is_merged_sample=False)
    with sample_engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO sample_metadata (id, name, file_format, file_hash) "
                "VALUES (1, 'fixture', '23andme_v5', 'h')"
            )
        )

    monkeypatch.setattr(engine_mod, "_read_bundle_version", lambda _registry: "v-resolved")

    stats = _build_coverage_stats(
        sample_engine,
        MagicMock(),
        total_variants=10,
        vep_rsid_hits=4,
        vep_coord_fallback_hits=1,
    )
    assert stats["bundle_version"] == "v-resolved"

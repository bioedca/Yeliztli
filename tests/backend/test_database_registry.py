"""Unit tests for ``backend.db.database_registry`` helpers."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import sqlalchemy as sa

from backend.db.database_registry import _build_encode_ccres_db, _record_db_version
from backend.db.tables import database_versions, reference_metadata


def test_record_db_version_inserts_new_row(reference_engine: sa.Engine) -> None:
    _record_db_version(
        reference_engine,
        db_name="lai_bundle",
        version="v1.1",
        file_size_bytes=523_801_111,
        sha256="959ed0fd9ebe2ad8fa542776a59ce73072d928c7ce59839ea81d0f1e78a5c18e",
    )

    with reference_engine.connect() as conn:
        rows = conn.execute(sa.select(database_versions)).fetchall()

    assert len(rows) == 1
    row = rows[0]
    assert row.db_name == "lai_bundle"
    assert row.version == "v1.1"
    assert row.file_size_bytes == 523_801_111
    assert (
        row.checksum_sha256 == "959ed0fd9ebe2ad8fa542776a59ce73072d928c7ce59839ea81d0f1e78a5c18e"
    )
    assert row.downloaded_at is not None


def test_record_db_version_updates_existing_row(reference_engine: sa.Engine) -> None:
    _record_db_version(
        reference_engine,
        db_name="encode_ccres",
        version="20260101",
        file_size_bytes=30_000_000,
    )
    _record_db_version(
        reference_engine,
        db_name="encode_ccres",
        version="20260508",
        file_size_bytes=31_000_000,
        sha256="aa" * 32,
    )

    with reference_engine.connect() as conn:
        rows = conn.execute(sa.select(database_versions)).fetchall()

    assert len(rows) == 1
    row = rows[0]
    assert row.db_name == "encode_ccres"
    assert row.version == "20260508"
    assert row.file_size_bytes == 31_000_000
    assert row.checksum_sha256 == "aa" * 32


def test_record_db_version_sha256_defaults_to_null(reference_engine: sa.Engine) -> None:
    _record_db_version(
        reference_engine,
        db_name="vep_bundle",
        version="2026-04-07",
        file_size_bytes=12_000_000,
    )

    with reference_engine.connect() as conn:
        row = conn.execute(
            sa.select(database_versions).where(database_versions.c.db_name == "vep_bundle")
        ).fetchone()

    assert row is not None
    assert row.checksum_sha256 is None
    assert row.version == "2026-04-07"
    assert row.file_size_bytes == 12_000_000


def test_record_db_version_update_clears_sha256(reference_engine: sa.Engine) -> None:
    """Re-recording without sha256 should overwrite the prior checksum."""
    _record_db_version(
        reference_engine,
        db_name="ancestry_pca",
        version="v1.0",
        file_size_bytes=414_432,
        sha256="bb" * 32,
    )
    _record_db_version(
        reference_engine,
        db_name="ancestry_pca",
        version="v1.1",
        file_size_bytes=414_500,
    )

    with reference_engine.connect() as conn:
        row = conn.execute(
            sa.select(database_versions).where(database_versions.c.db_name == "ancestry_pca")
        ).fetchone()

    assert row is not None
    assert row.version == "v1.1"
    assert row.checksum_sha256 is None


def test_record_db_version_independent_rows(reference_engine: sa.Engine) -> None:
    """Different db_names live in independent rows."""
    _record_db_version(
        reference_engine, db_name="clinvar", version="20260301", file_size_bytes=100
    )
    _record_db_version(reference_engine, db_name="gnomad", version="v4.1", file_size_bytes=200)

    with reference_engine.connect() as conn:
        rows = conn.execute(
            sa.select(database_versions).order_by(database_versions.c.db_name)
        ).fetchall()

    assert [r.db_name for r in rows] == ["clinvar", "gnomad"]
    assert [r.version for r in rows] == ["20260301", "v4.1"]


def test_record_db_version_concurrent_stamps_one_row(tmp_path: Path) -> None:
    """Concurrent stamps for the same DB upsert to a single row, no IntegrityError.

    The old SELECT-then-INSERT/UPDATE raced: two finalizers (a wizard build and an
    update-manager run, or two workers) both saw "no row" and both INSERTed, and
    the loser hit an IntegrityError on the db_name primary key. The atomic
    ON CONFLICT DO UPDATE upsert can't.
    """
    import threading

    ref = tmp_path / "reference.db"
    engine = sa.create_engine(f"sqlite:///{ref}")
    reference_metadata.create_all(engine)

    errors: list[Exception] = []
    n = 8
    barrier = threading.Barrier(n)

    def worker(i: int) -> None:
        try:
            barrier.wait()  # release all writers at once to maximize contention
            _record_db_version(engine, db_name="clinvar", version=f"v{i}", file_size_bytes=i)
        except Exception as exc:  # an IntegrityError would surface here
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    with engine.connect() as conn:
        rows = conn.execute(
            sa.select(database_versions.c.db_name).where(database_versions.c.db_name == "clinvar")
        ).fetchall()
    assert len(rows) == 1  # exactly one row, not one-per-writer
    engine.dispose()


def test_record_db_version_persists_file_path(reference_engine: sa.Engine) -> None:
    """``file_path`` is stored on insert and refreshed on update."""
    _record_db_version(
        reference_engine,
        db_name="gnomad",
        version="r2.1.1",
        file_size_bytes=2_000_000_000,
        sha256="cc" * 32,
        file_path="/tmp/data/gnomad.vcf.gz",
    )

    with reference_engine.connect() as conn:
        row = conn.execute(
            sa.select(database_versions).where(database_versions.c.db_name == "gnomad")
        ).fetchone()
    assert row is not None
    assert row.file_path == "/tmp/data/gnomad.vcf.gz"

    # Update path: omit file_path → column resets to NULL (matches sha256 semantics).
    _record_db_version(
        reference_engine,
        db_name="gnomad",
        version="r2.2.0",
        file_size_bytes=2_100_000_000,
    )

    with reference_engine.connect() as conn:
        row = conn.execute(
            sa.select(database_versions).where(database_versions.c.db_name == "gnomad")
        ).fetchone()
    assert row is not None
    assert row.version == "r2.2.0"
    assert row.file_path is None


# ──────────────────────────────────────────────────────────────────────
# Step 6: _build_encode_ccres_db records a database_versions row
# ──────────────────────────────────────────────────────────────────────

_ENCODE_SAMPLE_BED = """\
#chrom\tstart\tend\taccession\tscore\tstrand\tthickStart\tthickEnd\titemRgb\tccre_class
chr1\t10000\t10500\tEH38E0000001\t0\t.\t10000\t10500\t255,0,0\tPLS
chr1\t20000\t20800\tEH38E0000002\t0\t.\t20000\t20800\t255,205,0\tpELS
chr2\t30000\t30600\tEH38E0000003\t0\t.\t30000\t30600\t0,176,240\tdELS
"""


def _make_data_dir_with_reference(tmp_path: Path) -> Path:
    """Build a tmp data dir containing an empty reference.db with all tables."""
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    ref_path = data_dir / "reference.db"
    engine = sa.create_engine(f"sqlite:///{ref_path}")
    reference_metadata.create_all(engine)
    engine.dispose()
    return data_dir


def test_build_encode_ccres_records_version(tmp_path: Path) -> None:
    """After build, a row is upserted into reference.db database_versions."""
    data_dir = _make_data_dir_with_reference(tmp_path)
    bed_path = data_dir / "GRCh38-cCREs.bed"
    bed_path.write_text(_ENCODE_SAMPLE_BED)
    db_path = data_dir / "encode_ccres.db"

    _build_encode_ccres_db(bed_path, db_path)

    # SQLite DB created, raw BED removed.
    assert db_path.exists()
    assert not bed_path.exists()

    expected_version = datetime.now(UTC).strftime("%Y%m%d")
    expected_size = db_path.stat().st_size

    engine = sa.create_engine(f"sqlite:///{data_dir / 'reference.db'}")
    try:
        with engine.connect() as conn:
            row = conn.execute(
                sa.select(database_versions).where(database_versions.c.db_name == "encode_ccres")
            ).fetchone()
    finally:
        engine.dispose()

    assert row is not None
    assert row.db_name == "encode_ccres"
    assert row.version == expected_version
    assert row.file_size_bytes == expected_size
    assert row.checksum_sha256 is None
    assert row.downloaded_at is not None


def test_build_encode_ccres_succeeds_when_reference_db_missing(tmp_path: Path) -> None:
    """No reference.db on disk: build still succeeds, recording is best-effort."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    bed_path = data_dir / "GRCh38-cCREs.bed"
    bed_path.write_text(_ENCODE_SAMPLE_BED)
    db_path = data_dir / "encode_ccres.db"

    # Should not raise even though reference.db has no schema.
    _build_encode_ccres_db(bed_path, db_path)

    assert db_path.exists()
    assert not bed_path.exists()


def test_build_encode_ccres_upserts_existing_row(tmp_path: Path) -> None:
    """A second build call updates the existing encode_ccres row in place."""
    data_dir = _make_data_dir_with_reference(tmp_path)

    # Seed an older row simulating a prior build.
    engine = sa.create_engine(f"sqlite:///{data_dir / 'reference.db'}")
    try:
        with engine.begin() as conn:
            conn.execute(
                database_versions.insert().values(
                    db_name="encode_ccres",
                    version="20200101",
                    file_size_bytes=1,
                    downloaded_at=datetime(2020, 1, 1, tzinfo=UTC),
                    checksum_sha256=None,
                )
            )
    finally:
        engine.dispose()

    bed_path = data_dir / "GRCh38-cCREs.bed"
    bed_path.write_text(_ENCODE_SAMPLE_BED)
    db_path = data_dir / "encode_ccres.db"

    _build_encode_ccres_db(bed_path, db_path)

    engine = sa.create_engine(f"sqlite:///{data_dir / 'reference.db'}")
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                sa.select(database_versions).where(database_versions.c.db_name == "encode_ccres")
            ).fetchall()
    finally:
        engine.dispose()

    # Single row, version refreshed.
    assert len(rows) == 1
    assert rows[0].version == datetime.now(UTC).strftime("%Y%m%d")
    assert rows[0].file_size_bytes == db_path.stat().st_size


# ──────────────────────────────────────────────────────────────────────
# Phase 0 closure (Step 18): semver `v2.0.0` recording for vep_bundle.
# Distinct from step 4's manifest fixture surface — this anchors the
# registry-level write path against the v2.0.0 string per Plan §5.2.
# ──────────────────────────────────────────────────────────────────────


def test_record_db_version_accepts_v2_0_0_semver(reference_engine: sa.Engine) -> None:
    """`_record_db_version` round-trips the v2.0.0 semver string verbatim."""
    _record_db_version(
        reference_engine,
        db_name="vep_bundle",
        version="v2.0.0",
        file_size_bytes=600_000_000,
        sha256="0" * 64,
        file_path="/tmp/vep_bundle.db",
    )

    with reference_engine.connect() as conn:
        row = conn.execute(
            sa.select(database_versions).where(database_versions.c.db_name == "vep_bundle")
        ).fetchone()

    assert row is not None
    assert row.db_name == "vep_bundle"
    assert row.version == "v2.0.0"
    assert row.file_size_bytes == 600_000_000
    assert row.checksum_sha256 == "0" * 64
    assert row.file_path == "/tmp/vep_bundle.db"
    assert row.downloaded_at is not None


def test_record_db_version_upgrades_v1_to_v2(reference_engine: sa.Engine) -> None:
    """Upgrading the recorded version from v1.0.0 → v2.0.0 leaves a single row."""
    _record_db_version(
        reference_engine,
        db_name="vep_bundle",
        version="v1.0.0",
        file_size_bytes=12_000_000,
    )
    _record_db_version(
        reference_engine,
        db_name="vep_bundle",
        version="v2.0.0",
        file_size_bytes=600_000_000,
        sha256="0" * 64,
    )

    with reference_engine.connect() as conn:
        rows = conn.execute(
            sa.select(database_versions).where(database_versions.c.db_name == "vep_bundle")
        ).fetchall()

    assert len(rows) == 1
    assert rows[0].version == "v2.0.0"
    assert rows[0].file_size_bytes == 600_000_000
    assert rows[0].checksum_sha256 == "0" * 64


def test_vep_bundle_registry_entry_reflects_v2_0_0_sizing() -> None:
    """`DATABASES['vep_bundle'].expected_size_bytes` was bumped to the v2.0.0 footprint.

    Step 15 raised the expected size from ~12 MB to ~600 MB to fit the union
    catalog. This closure-step assertion keeps the registry entry pinned so a
    future edit can't silently roll back to the pre-v2.0.0 sizing.
    """
    from backend.db.database_registry import DATABASES

    entry = DATABASES["vep_bundle"]
    assert entry.expected_size_bytes >= 500_000_000
    assert "AncestryDNA" in entry.description


def test_vep_bundle_registry_url_points_at_v2_0_0_release() -> None:
    """Phase 0i (PR-0z) rewrites the fallback URL from the non-existent
    ``raw.githubusercontent.com/.../bundles/vep_bundle.db`` path to the v2.0.0
    GitHub Release asset, so the manifest-CDN-outage fallback actually resolves.
    """
    from backend.db.database_registry import DATABASES

    entry = DATABASES["vep_bundle"]
    assert entry.url.endswith("/releases/download/bundle-v2.0.0/vep_bundle.db")
    assert "raw.githubusercontent.com" not in entry.url


def test_gnomad_registry_entry_is_bundled() -> None:
    """gnomAD now ships as a prebuilt downloadable bundle, not a pipeline build.

    Flipping build_mode to "bundled" + removing it from _BUILD_FN_REGISTRY routes
    the setup wizard / scheduler through run_gnomad_bundle_update (manifest
    download) instead of the ~16 GB VCF rebuild. filename/target/required/phase
    stay put so the read path (gnomad_engine on data_dir/gnomad_af.db) is untouched.
    """
    from backend.db.database_registry import DATABASES, get_build_fn

    entry = DATABASES["gnomad"]
    assert entry.build_mode == "bundled"
    assert entry.target_db == "standalone"
    assert entry.filename == "gnomad_af.db"
    assert entry.required is True
    assert entry.phase == 2
    # sha256 is unpinned in bundled mode — the manifest is the source of truth.
    assert entry.sha256 is None
    # No build function: the VCF rebuild is retired from the wizard/scheduler path.
    assert get_build_fn("gnomad") is None


def test_gnomad_registry_url_targets_release_asset() -> None:
    """The fallback URL points at the published gnomad-bundle release asset.

    In bundled mode the runner reads the authoritative url/sha/size from the
    manifest (bundles["gnomad"]); this registry URL is documentation/fallback. It
    is pinned here so a future edit can't silently point it elsewhere.
    """
    from backend.db.database_registry import DATABASES

    entry = DATABASES["gnomad"]
    assert entry.url.endswith("/releases/download/gnomad-bundle-v1.0.0/gnomad_af.db")


def test_gnomad_registry_matches_manifest_bundle() -> None:
    """Publish path: the registry url + expected_size_bytes byte-match the
    committed ``bundles/manifest.json`` ``bundles.gnomad`` entry.

    Bundled mode pins the registry ``sha256`` to ``None`` (the manifest is the
    single source of truth), so this asserts URL + size parity instead of sha —
    the standalone-bundle analogue of
    ``test_lai_bundle_registry_sha_matches_manifest``.
    """
    import json

    import pytest

    from backend.db.database_registry import DATABASES

    repo_manifest = Path(__file__).resolve().parents[2] / "bundles" / "manifest.json"
    if not repo_manifest.is_file():
        pytest.skip("bundles/manifest.json not present in this checkout")
    payload = json.loads(repo_manifest.read_text(encoding="utf-8"))
    entry = payload["bundles"]["gnomad"]
    reg = DATABASES["gnomad"]
    assert reg.url == entry["url"]
    assert reg.expected_size_bytes == entry["size_bytes"]


# ── SW-B8 follow-up: pgs_scores bundle wiring ─────────────────────────


def test_pgs_scores_registry_entry_is_bundled() -> None:
    """The PGS Catalog scores DB ships as a downloadable standalone bundle."""
    from backend.db.database_registry import DATABASES

    entry = DATABASES["pgs_scores"]
    assert entry.build_mode == "bundled"
    assert entry.target_db == "standalone"
    assert entry.filename == "pgs_scores.db"
    assert entry.required is False
    assert entry.sha256 is None  # manifest is the source of truth in bundled mode
    assert entry.url.endswith("/releases/download/pgs-scores-v1.0.0/pgs_scores.db")


def test_pgs_scores_registry_matches_manifest_bundle() -> None:
    """Registry url + expected_size_bytes byte-match bundles.pgs_scores."""
    import json

    import pytest

    from backend.db.database_registry import DATABASES

    repo_manifest = Path(__file__).resolve().parents[2] / "bundles" / "manifest.json"
    if not repo_manifest.is_file():
        pytest.skip("bundles/manifest.json not present in this checkout")
    payload = json.loads(repo_manifest.read_text(encoding="utf-8"))
    entry = payload["bundles"]["pgs_scores"]
    reg = DATABASES["pgs_scores"]
    assert reg.url == entry["url"]
    assert reg.expected_size_bytes == entry["size_bytes"]


def test_pgs_scores_db_health_spec() -> None:
    """db_health checks pgs_score_weights (must be non-empty) + pgs_score_metadata."""
    from backend.db.db_health import _STANDALONE_TABLE_SPEC

    spec = dict(_STANDALONE_TABLE_SPEC["pgs_scores"])
    assert spec["pgs_score_weights"] is True
    assert spec["pgs_score_metadata"] is False


# ── F30: genome-build provenance ──────────────────────────────────────


def _build_of(engine: sa.Engine, db_name: str) -> str | None:
    with engine.connect() as conn:
        return conn.execute(
            sa.select(database_versions.c.genome_build).where(
                database_versions.c.db_name == db_name
            )
        ).scalar_one()


def test_record_db_version_auto_stamps_grch37(reference_engine: sa.Engine) -> None:
    """A GRCh37 source's build is resolved from the map with no explicit arg."""
    _record_db_version(reference_engine, db_name="clinvar", version="20260101", file_size_bytes=1)
    assert _build_of(reference_engine, "clinvar") == "GRCh37"


def test_record_db_version_auto_stamps_gnomad_grch37(reference_engine: sa.Engine) -> None:
    """The live gnomAD bundle update path records GRCh37 through _record_db_version."""
    _record_db_version(reference_engine, db_name="gnomad", version="r2.1.1", file_size_bytes=1)
    assert _build_of(reference_engine, "gnomad") == "GRCh37"


def test_record_db_version_auto_stamps_dbnsfp_grch38(reference_engine: sa.Engine) -> None:
    """dbNSFP is the lone GRCh38 source (F35) — auto-resolved, not flagged."""
    _record_db_version(reference_engine, db_name="dbnsfp", version="5.1", file_size_bytes=1)
    assert _build_of(reference_engine, "dbnsfp") == "GRCh38"


def test_record_db_version_build_agnostic_source_is_null(reference_engine: sa.Engine) -> None:
    """A source absent from EXPECTED_GENOME_BUILD records NULL."""
    _record_db_version(reference_engine, db_name="dbsnp", version="b151", file_size_bytes=1)
    assert _build_of(reference_engine, "dbsnp") is None


def test_record_db_version_explicit_build_overrides_map(reference_engine: sa.Engine) -> None:
    """An explicit build wins over the map (used to plant a skew in tests)."""
    _record_db_version(
        reference_engine,
        db_name="gnomad",
        version="r4.1",
        file_size_bytes=1,
        genome_build="GRCh38",
    )
    assert _build_of(reference_engine, "gnomad") == "GRCh38"


def test_record_db_version_update_preserves_auto_build(reference_engine: sa.Engine) -> None:
    """Re-recording an existing row keeps the auto-stamped build."""
    _record_db_version(reference_engine, db_name="cpic", version="1.0", file_size_bytes=1)
    _record_db_version(reference_engine, db_name="cpic", version="1.1", file_size_bytes=2)
    assert _build_of(reference_engine, "cpic") == "GRCh37"


def test_record_version_wrappers_stamp_expected_build(reference_engine: sa.Engine) -> None:
    """Each per-source wrapper records the build its source ships in."""
    from backend.annotation.clinvar import record_clinvar_version
    from backend.annotation.cpic import record_cpic_version
    from backend.annotation.dbnsfp import record_dbnsfp_version
    from backend.annotation.gnomad_constraint import record_constraint_version
    from backend.annotation.gwas import record_gwas_version

    record_clinvar_version(reference_engine, version="20260101")
    record_gwas_version(reference_engine, version="20260101")
    record_cpic_version(reference_engine, version="1.0")
    record_constraint_version(reference_engine, version="v2.1.1")
    record_dbnsfp_version(reference_engine, version="5.1")

    assert _build_of(reference_engine, "clinvar") == "GRCh37"
    assert _build_of(reference_engine, "gwas_catalog") == "GRCh37"
    assert _build_of(reference_engine, "cpic") == "GRCh37"
    assert _build_of(reference_engine, "gnomad_constraint") == "GRCh37"
    assert _build_of(reference_engine, "dbnsfp") == "GRCh38"


def test_check_genome_build_consistency_clean_on_expected_set(
    reference_engine: sa.Engine,
) -> None:
    """The expected GRCh37 ∪ {dbNSFP→GRCh38} set produces no warnings."""
    from backend.db.database_registry import check_genome_build_consistency

    for name in ("clinvar", "gnomad", "gwas_catalog", "cpic", "vep_bundle", "gnomad_constraint"):
        _record_db_version(reference_engine, db_name=name, version="x", file_size_bytes=1)
    _record_db_version(reference_engine, db_name="dbnsfp", version="5.1", file_size_bytes=1)
    # A build-agnostic source with NULL build must not be flagged either.
    _record_db_version(reference_engine, db_name="dbsnp", version="b151", file_size_bytes=1)

    assert check_genome_build_consistency(reference_engine) == []


def test_check_genome_build_consistency_flags_planted_skew(
    reference_engine: sa.Engine,
) -> None:
    """A GRCh38 gnomAD bundle where GRCh37 is expected is surfaced."""
    from backend.db.database_registry import check_genome_build_consistency

    _record_db_version(reference_engine, db_name="clinvar", version="x", file_size_bytes=1)
    _record_db_version(
        reference_engine,
        db_name="gnomad",
        version="r4.1",
        file_size_bytes=1,
        genome_build="GRCh38",  # skew: gnomAD GRCh38 bundle on a GRCh37 pipeline
    )

    assert check_genome_build_consistency(reference_engine) == ["gnomad"]

"""Unit tests for :mod:`backend.db.db_health`.

Covers the full database-health state matrix and integrity edge cases for the
DB-download hardening change:

* :func:`validate_database` for every database type — valid / empty / absent /
  corrupt / truncated-npz / incomplete-bundle, including the ``deep`` quick_check
  path.
* :func:`get_database_health` state derivation: not_installed / partial / ready /
  corrupt / downloading / building, plus resumable-partial progress.
* :func:`find_resumable_download` download-mode discovery.
* :func:`recover_orphaned_downloads` crash sweep.
* :func:`clean_database_artifacts` corrupt-file removal + row clearing (and the
  reference.db never-delete guarantee).

Conventions (match tests/backend/conftest.py — do NOT modify it):
  * A real reference.db file is built in ``tmp_path`` via
    ``reference_metadata.create_all`` so the health code's throwaway engines and
    version-stamp lookups hit a real on-disk SQLite.
  * Standalone DBs (gnomad/dbnsfp/vep_bundle/encode_ccres) are written as tiny
    valid SQLite files with the expected table + 1 row to exercise "ready".
  * No real network / no real bundles — fast unit tests only.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pytest
import sqlalchemy as sa

from backend.config import Settings
from backend.db.database_registry import _record_db_version, get_database
from backend.db.db_health import (
    _ACTIVE_DOWNLOAD_STATES,
    _RESUMABLE_DOWNLOAD_STATES,
    DatabaseHealth,
    IntegrityResult,
    artifact_present,
    clean_database_artifacts,
    find_resumable_download,
    get_all_database_health,
    get_database_health,
    recover_orphaned_downloads,
    validate_database,
)
from backend.db.download_manager import INCOMPLETE_DOWNLOAD_STATES
from backend.db.tables import (
    clinvar_variants,
    cpic_alleles,
    database_versions,
    dbsnp_merges,
    download_session_jobs,
    download_sessions,
    downloads,
    gene_phenotype,
    gwas_associations,
    jobs,
    reference_metadata,
)

# Module-level constants for the ancestry-PCA archive keys the loader reads.
# Must match db_health._ANCESTRY_PCA_KEYS (the full set load_ancestry_bundle
# dereferences) so a "valid" fixture passes the integrity check.
_ANCESTRY_PCA_KEYS = (
    "n_significant_pcs",
    "n_total_snps",
    "n_selected_aims",
    "loadings",
    "means",
    "stds",
    "eigenvalues",
    "tw_pvalues",
    "populations",
    "population_centroids",
    "ref_pca_coords",
    "ref_labels",
    "aim_rsids_23andme",
    "aim_chroms",
    "aim_positions_grch38",
    "aim_a1",
    "aim_a2",
)


# ═══════════════════════════════════════════════════════════════════════
# Fixtures: real reference.db on disk + helpers
# ═══════════════════════════════════════════════════════════════════════


@pytest.fixture
def settings(tmp_data_dir: Path) -> Settings:
    """Settings pointed at a temp data dir (samples/downloads/logs exist)."""
    return Settings(data_dir=tmp_data_dir, wal_mode=False)


@pytest.fixture
def ref_db(settings: Settings) -> sa.Engine:
    """Create a real reference.db file with all reference tables (empty).

    Returns an engine bound to that file. The same physical file is what the
    health code opens via its own throwaway engines, so writes here are visible.
    """
    engine = sa.create_engine(f"sqlite:///{settings.reference_db_path}")
    reference_metadata.create_all(engine)
    return engine


# ── Reference-resident seeding helpers ───────────────────────────────


def _seed_clinvar(engine: sa.Engine) -> None:
    with engine.begin() as conn:
        conn.execute(
            clinvar_variants.insert(),
            [{"rsid": "rs429358", "chrom": "19", "pos": 44908684, "ref": "T", "alt": "C"}],
        )


def _seed_cpic(engine: sa.Engine) -> None:
    with engine.begin() as conn:
        conn.execute(
            cpic_alleles.insert(),
            [{"gene": "CYP2D6", "allele_name": "*1", "defining_variants": json.dumps([])}],
        )


def _seed_gwas(engine: sa.Engine) -> None:
    with engine.begin() as conn:
        conn.execute(
            gwas_associations.insert(),
            [{"rsid": "rs429358", "chrom": "19", "pos": 44908684, "trait": "AD"}],
        )


def _seed_dbsnp(engine: sa.Engine) -> None:
    with engine.begin() as conn:
        conn.execute(
            dbsnp_merges.insert(),
            [{"old_rsid": "rs1", "current_rsid": "rs2", "build_id": 151}],
        )


def _seed_mondo(engine: sa.Engine) -> None:
    with engine.begin() as conn:
        conn.execute(
            gene_phenotype.insert(),
            [
                {
                    "gene_symbol": "BRCA1",
                    "disease_name": "HBOC",
                    "disease_id": "MONDO:0011450",
                    "source": "mondo_hpo",
                }
            ],
        )


_REFERENCE_SEEDERS = {
    "clinvar": _seed_clinvar,
    "cpic": _seed_cpic,
    "gwas_catalog": _seed_gwas,
    "dbsnp": _seed_dbsnp,
    "mondo_hpo": _seed_mondo,
}


# ── Standalone SQLite + bundle artifact builders ─────────────────────


def _make_valid_gnomad(settings: Settings, *, rows: bool = True) -> Path:
    path = settings.gnomad_db_path
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    try:
        conn.execute("CREATE TABLE gnomad_af (chrom TEXT, pos INTEGER, af_global REAL)")
        if rows:
            conn.execute("INSERT INTO gnomad_af VALUES ('19', 44908684, 0.15)")
        conn.commit()
    finally:
        conn.close()
    return path


def _make_valid_dbnsfp(settings: Settings, *, rows: bool = True) -> Path:
    path = settings.dbnsfp_db_path
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    try:
        conn.execute("CREATE TABLE dbnsfp_scores (rsid TEXT, cadd_phred REAL)")
        if rows:
            conn.execute("INSERT INTO dbnsfp_scores VALUES ('rs429358', 12.3)")
        conn.commit()
    finally:
        conn.close()
    return path


def _make_valid_encode(settings: Settings, *, rows: bool = True) -> Path:
    path = settings.encode_ccres_db_path
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    try:
        conn.execute("CREATE TABLE encode_ccres (chrom TEXT, start INTEGER, end INTEGER)")
        if rows:
            conn.execute("INSERT INTO encode_ccres VALUES ('chr19', 100, 200)")
        conn.commit()
    finally:
        conn.close()
    return path


def _make_valid_vep_bundle(settings: Settings, *, rows: bool = True) -> Path:
    path = settings.vep_bundle_db_path
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    try:
        conn.execute("CREATE TABLE vep_annotations (rsid TEXT, gene_symbol TEXT)")
        conn.execute("CREATE TABLE bundle_metadata (key TEXT, value TEXT)")
        conn.execute("INSERT INTO bundle_metadata VALUES ('bundle_version', 'v2.0.0')")
        if rows:
            conn.execute("INSERT INTO vep_annotations VALUES ('rs429358', 'APOE')")
        conn.commit()
    finally:
        conn.close()
    return path


def _make_valid_ancestry_pca(settings: Settings) -> Path:
    path = settings.data_dir / "ancestry_pca_bundle.npz"
    path.parent.mkdir(parents=True, exist_ok=True)
    arrays = {k: np.arange(3, dtype=float) for k in _ANCESTRY_PCA_KEYS}
    np.savez(path, **arrays)
    return path


def _make_valid_lai_bundle(settings: Settings) -> Path:
    """Create the minimal extracted LAI bundle directory structure."""
    bundle = settings.resolved_lai_bundle_path
    for chrom in range(1, 23):
        model_dir = bundle / "gnomix_models" / f"chr{chrom}"
        model_dir.mkdir(parents=True, exist_ok=True)
        (model_dir / "base_coefs.npz").write_bytes(b"\x00")
        (model_dir / "metadata.npz").write_bytes(b"\x00")
        (model_dir / "smoother.json").write_text("{}")
    return bundle


# ═══════════════════════════════════════════════════════════════════════
# validate_database — per-DB-type integrity matrix
# ═══════════════════════════════════════════════════════════════════════


class TestValidateReferenceResident:
    """Reference.db-resident DBs: integrity = consumer table non-empty."""

    @pytest.mark.parametrize(
        "db_name",
        ["clinvar", "cpic", "gwas_catalog", "dbsnp", "mondo_hpo"],
    )
    def test_seeded_table_ok(self, db_name: str, settings: Settings, ref_db: sa.Engine) -> None:
        _REFERENCE_SEEDERS[db_name](ref_db)
        result = validate_database(db_name, settings, engine=ref_db)
        assert isinstance(result, IntegrityResult)
        assert result.ok is True
        assert result.depth == "structural"

    @pytest.mark.parametrize(
        "db_name",
        ["clinvar", "cpic", "gwas_catalog", "dbsnp", "mondo_hpo"],
    )
    def test_empty_table_not_ok(self, db_name: str, settings: Settings, ref_db: sa.Engine) -> None:
        # reference.db exists with empty tables.
        result = validate_database(db_name, settings, engine=ref_db)
        assert result.ok is False
        assert "is empty" in result.detail

    def test_reference_db_missing_is_absent(self, settings: Settings) -> None:
        # No reference.db file on disk at all.
        assert not settings.reference_db_path.exists()
        result = validate_database("clinvar", settings)
        assert result.ok is False
        assert result.depth == "absent"
        assert result.detail == "not present"

    def test_reference_resident_without_engine_opens_file(
        self, settings: Settings, ref_db: sa.Engine
    ) -> None:
        # When no engine is supplied, validate_database opens reference.db itself.
        _seed_clinvar(ref_db)
        ref_db.dispose()
        result = validate_database("clinvar", settings)  # no engine kwarg
        assert result.ok is True

    def test_deep_quick_check_passes_on_valid(self, settings: Settings, ref_db: sa.Engine) -> None:
        _seed_clinvar(ref_db)
        result = validate_database("clinvar", settings, engine=ref_db, deep=True)
        assert result.ok is True
        assert result.depth == "deep"

    def test_deep_quick_check_empty_still_reports_empty(
        self, settings: Settings, ref_db: sa.Engine
    ) -> None:
        result = validate_database("clinvar", settings, engine=ref_db, deep=True)
        assert result.ok is False
        assert "is empty" in result.detail
        assert result.depth == "deep"


class TestValidateStandalone:
    """Standalone SQLite DBs: gnomad / dbnsfp / vep_bundle / encode_ccres."""

    def test_gnomad_valid_ok(self, settings: Settings) -> None:
        _make_valid_gnomad(settings)
        result = validate_database("gnomad", settings)
        assert result.ok is True
        assert result.depth == "structural"

    def test_gnomad_empty_not_ok(self, settings: Settings) -> None:
        _make_valid_gnomad(settings, rows=False)
        result = validate_database("gnomad", settings)
        assert result.ok is False
        assert "gnomad_af" in result.detail
        assert "is empty" in result.detail

    def test_gnomad_absent(self, settings: Settings) -> None:
        result = validate_database("gnomad", settings)
        assert result.ok is False
        assert result.depth == "absent"

    def test_gnomad_corrupt_file(self, settings: Settings) -> None:
        path = settings.gnomad_db_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"not a sqlite database at all")
        result = validate_database("gnomad", settings)
        assert result.ok is False
        # A garbage file fails to open/query -> structural depth, not "is empty".
        assert "is empty" not in result.detail

    def test_dbnsfp_valid_ok(self, settings: Settings) -> None:
        _make_valid_dbnsfp(settings)
        result = validate_database("dbnsfp", settings)
        assert result.ok is True

    def test_dbnsfp_corrupt_file(self, settings: Settings) -> None:
        path = settings.dbnsfp_db_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"\x00\x01\x02 not sqlite")
        result = validate_database("dbnsfp", settings)
        assert result.ok is False

    def test_encode_valid_ok(self, settings: Settings) -> None:
        _make_valid_encode(settings)
        result = validate_database("encode_ccres", settings)
        assert result.ok is True

    def test_encode_empty_not_ok(self, settings: Settings) -> None:
        _make_valid_encode(settings, rows=False)
        result = validate_database("encode_ccres", settings)
        assert result.ok is False
        assert "is empty" in result.detail

    def test_vep_bundle_valid_ok(self, settings: Settings) -> None:
        _make_valid_vep_bundle(settings)
        result = validate_database("vep_bundle", settings)
        assert result.ok is True

    def test_vep_bundle_empty_annotations_not_ok(self, settings: Settings) -> None:
        # vep_annotations is the data table (must_have_rows=True).
        _make_valid_vep_bundle(settings, rows=False)
        result = validate_database("vep_bundle", settings)
        assert result.ok is False
        assert "vep_annotations" in result.detail

    def test_vep_bundle_missing_metadata_table_not_ok(self, settings: Settings) -> None:
        # bundle_metadata only needs to *exist*; a missing table is still an error.
        path = settings.vep_bundle_db_path
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(path))
        try:
            conn.execute("CREATE TABLE vep_annotations (rsid TEXT, gene_symbol TEXT)")
            conn.execute("INSERT INTO vep_annotations VALUES ('rs1', 'GENE')")
            conn.commit()
        finally:
            conn.close()
        result = validate_database("vep_bundle", settings)
        assert result.ok is False
        assert "bundle_metadata" in result.detail

    def test_standalone_deep_corrupt_quick_check(self, settings: Settings) -> None:
        # Truncate a valid SQLite mid-file so the header survives but quick_check
        # detects the damage on the deep path.
        path = _make_valid_gnomad(settings)
        data = path.read_bytes()
        path.write_bytes(data[: len(data) // 2])
        result = validate_database("gnomad", settings, deep=True)
        assert result.ok is False
        assert result.depth == "deep"

    def test_standalone_deep_valid_quick_check_ok(self, settings: Settings) -> None:
        _make_valid_gnomad(settings)
        result = validate_database("gnomad", settings, deep=True)
        assert result.ok is True
        assert result.depth == "deep"


class TestValidateAncestryPCA:
    """ancestry_pca: numpy .npz with required array keys."""

    def test_valid_ok(self, settings: Settings) -> None:
        _make_valid_ancestry_pca(settings)
        result = validate_database("ancestry_pca", settings)
        assert result.ok is True

    def test_missing_keys_not_ok(self, settings: Settings) -> None:
        path = settings.data_dir / "ancestry_pca_bundle.npz"
        path.parent.mkdir(parents=True, exist_ok=True)
        # Only a subset of required keys present.
        np.savez(path, loadings=np.arange(3.0), means=np.arange(3.0))
        result = validate_database("ancestry_pca", settings)
        assert result.ok is False
        assert "missing array" in result.detail

    def test_garbage_npz_not_ok(self, settings: Settings) -> None:
        path = settings.data_dir / "ancestry_pca_bundle.npz"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"this is not a valid npz archive")
        result = validate_database("ancestry_pca", settings)
        assert result.ok is False

    def test_absent(self, settings: Settings) -> None:
        result = validate_database("ancestry_pca", settings)
        assert result.ok is False
        assert result.depth == "absent"


class TestValidateLAIBundle:
    """lai_bundle: extracted directory validated via validate_lai_bundle."""

    def test_complete_bundle_ok(self, settings: Settings) -> None:
        _make_valid_lai_bundle(settings)
        result = validate_database("lai_bundle", settings)
        assert result.ok is True

    def test_incomplete_bundle_not_ok(self, settings: Settings) -> None:
        bundle = _make_valid_lai_bundle(settings)
        # Remove one required model file -> incomplete.
        (bundle / "gnomix_models" / "chr5" / "smoother.json").unlink()
        result = validate_database("lai_bundle", settings)
        assert result.ok is False
        assert "incomplete" in result.detail.lower()

    def test_absent_bundle(self, settings: Settings) -> None:
        result = validate_database("lai_bundle", settings)
        assert result.ok is False
        assert result.depth == "absent"


class TestValidateUnknown:
    def test_unknown_database(self, settings: Settings) -> None:
        result = validate_database("does_not_exist", settings)
        assert result.ok is False
        assert result.depth == "absent"
        assert "unknown database" in result.detail


# ═══════════════════════════════════════════════════════════════════════
# artifact_present
# ═══════════════════════════════════════════════════════════════════════


class TestArtifactPresent:
    def test_standalone_present(self, settings: Settings) -> None:
        _make_valid_gnomad(settings)
        assert artifact_present(get_database("gnomad"), settings) is True

    def test_standalone_absent(self, settings: Settings) -> None:
        assert artifact_present(get_database("gnomad"), settings) is False

    def test_reference_resident_uses_reference_db(
        self, settings: Settings, ref_db: sa.Engine
    ) -> None:
        # reference.db exists -> artifact_present True even though table is empty.
        assert artifact_present(get_database("clinvar"), settings) is True

    def test_lai_bundle_dir_with_contents(self, settings: Settings) -> None:
        _make_valid_lai_bundle(settings)
        assert artifact_present(get_database("lai_bundle"), settings) is True

    def test_lai_bundle_empty_dir_not_present(self, settings: Settings) -> None:
        settings.resolved_lai_bundle_path.mkdir(parents=True, exist_ok=True)
        assert artifact_present(get_database("lai_bundle"), settings) is False

    def test_lai_bundle_missing_dir_not_present(self, settings: Settings) -> None:
        assert artifact_present(get_database("lai_bundle"), settings) is False


# ═══════════════════════════════════════════════════════════════════════
# find_resumable_download
# ═══════════════════════════════════════════════════════════════════════


def _downloads_dest_str(settings: Settings, db_name: str) -> str:
    db_info = get_database(db_name)
    return str(settings.downloads_dir / db_info.filename)


def _write_tmp_partial(settings: Settings, db_name: str, nbytes: int) -> Path:
    db_info = get_database(db_name)
    dl_dest = settings.downloads_dir / db_info.filename
    tmp_path = dl_dest.with_suffix(dl_dest.suffix + ".tmp")
    tmp_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path.write_bytes(b"x" * nbytes)
    return tmp_path


def test_resumable_and_active_states_partition_incomplete() -> None:
    """db_health's resumable/active split must exactly cover DownloadManager's
    incomplete-download set, with no overlap — so a row the transport would
    resume is never reported here as unresumable, and vice versa."""
    assert _RESUMABLE_DOWNLOAD_STATES | _ACTIVE_DOWNLOAD_STATES == INCOMPLETE_DOWNLOAD_STATES
    assert _RESUMABLE_DOWNLOAD_STATES & _ACTIVE_DOWNLOAD_STATES == frozenset()


class TestFindResumableDownload:
    def test_returns_dict_for_download_mode(self, settings: Settings, ref_db: sa.Engine) -> None:
        # encode_ccres is build_mode="download".
        _write_tmp_partial(settings, "encode_ccres", 512)
        with ref_db.begin() as conn:
            result = conn.execute(
                downloads.insert().values(
                    url="http://example/encode_ccres.db",
                    dest_path=_downloads_dest_str(settings, "encode_ccres"),
                    total_bytes=2048,
                    downloaded_bytes=0,
                    status="failed",
                )
            )
            download_id = result.lastrowid

        info = find_resumable_download(ref_db, get_database("encode_ccres"), settings)
        assert info is not None
        assert info["download_id"] == download_id
        # Byte count comes from the .tmp file on disk, not the DB checkpoint.
        assert info["downloaded_bytes"] == 512
        assert info["total_bytes"] == 2048

    def test_total_bytes_falls_back_to_expected_size_when_null(
        self, settings: Settings, ref_db: sa.Engine
    ) -> None:
        """A partial whose original transfer died before learning the real total
        (``total_bytes`` NULL) still reports a total — the artifact's known size —
        so the resume UI can always show a "% saved" instead of a blank."""
        _write_tmp_partial(settings, "encode_ccres", 256)
        with ref_db.begin() as conn:
            conn.execute(
                downloads.insert().values(
                    url="http://example/encode_ccres.db",
                    dest_path=_downloads_dest_str(settings, "encode_ccres"),
                    total_bytes=None,  # died before the first response set it
                    status="failed",
                )
            )
        db_info = get_database("encode_ccres")
        info = find_resumable_download(ref_db, db_info, settings)
        assert info is not None
        assert info["downloaded_bytes"] == 256
        assert info["total_bytes"] == db_info.expected_size_bytes
        assert info["total_bytes"]  # non-zero → a percentage is computable

    def test_lai_bundle_download_mode_resumable(
        self, settings: Settings, ref_db: sa.Engine
    ) -> None:
        _write_tmp_partial(settings, "lai_bundle", 1024)
        with ref_db.begin() as conn:
            conn.execute(
                downloads.insert().values(
                    url="http://example/lai.tar.gz",
                    dest_path=_downloads_dest_str(settings, "lai_bundle"),
                    total_bytes=4096,
                    # "failed" = an interrupted/resumable partial. An active
                    # "downloading" row is reported as in-flight, not resumable.
                    status="failed",
                )
            )
        info = find_resumable_download(ref_db, get_database("lai_bundle"), settings)
        assert info is not None
        assert info["downloaded_bytes"] == 1024

    def test_none_for_pipeline_db(self, settings: Settings, ref_db: sa.Engine) -> None:
        # gnomad is build_mode="pipeline" — never resumes via DownloadManager.
        # dbnsfp is build_mode="pipeline" — it builds from upstream sources via
        # stream_download (resumable=False), never through DownloadManager, so it
        # leaves no downloads_dir/<filename>.tmp to resume. (gnomad is no longer a
        # valid pipeline example — #312 ships it as a prebuilt bundle.)
        _write_tmp_partial(settings, "dbnsfp", 512)
        with ref_db.begin() as conn:
            conn.execute(
                downloads.insert().values(
                    url="http://example/dbnsfp.db",
                    dest_path=_downloads_dest_str(settings, "dbnsfp"),
                    total_bytes=2048,
                    status="failed",
                )
            )
        info = find_resumable_download(ref_db, get_database("dbnsfp"), settings)
        assert info is None

    def test_none_when_no_tmp(self, settings: Settings, ref_db: sa.Engine) -> None:
        # downloads row exists but there is no .tmp file on disk.
        with ref_db.begin() as conn:
            conn.execute(
                downloads.insert().values(
                    url="http://example/encode_ccres.db",
                    dest_path=_downloads_dest_str(settings, "encode_ccres"),
                    total_bytes=2048,
                    status="failed",
                )
            )
        info = find_resumable_download(ref_db, get_database("encode_ccres"), settings)
        assert info is None

    def test_none_when_no_row(self, settings: Settings, ref_db: sa.Engine) -> None:
        # .tmp exists but no downloads row.
        _write_tmp_partial(settings, "encode_ccres", 512)
        info = find_resumable_download(ref_db, get_database("encode_ccres"), settings)
        assert info is None

    def test_none_when_tmp_empty(self, settings: Settings, ref_db: sa.Engine) -> None:
        _write_tmp_partial(settings, "encode_ccres", 0)
        with ref_db.begin() as conn:
            conn.execute(
                downloads.insert().values(
                    url="http://example/encode_ccres.db",
                    dest_path=_downloads_dest_str(settings, "encode_ccres"),
                    total_bytes=2048,
                    status="failed",
                )
            )
        info = find_resumable_download(ref_db, get_database("encode_ccres"), settings)
        assert info is None

    def test_none_when_row_terminal_complete(self, settings: Settings, ref_db: sa.Engine) -> None:
        # A 'complete' row is not in the resumable set.
        _write_tmp_partial(settings, "encode_ccres", 512)
        with ref_db.begin() as conn:
            conn.execute(
                downloads.insert().values(
                    url="http://example/encode_ccres.db",
                    dest_path=_downloads_dest_str(settings, "encode_ccres"),
                    total_bytes=2048,
                    status="complete",
                )
            )
        info = find_resumable_download(ref_db, get_database("encode_ccres"), settings)
        assert info is None

    def test_bundled_gnomad_resumable(self, settings: Settings, ref_db: sa.Engine) -> None:
        # The prebuilt gnomAD bundle (#312, build_mode="bundled", ~2 GB) is
        # fetched through DownloadManager, so an interrupted transfer leaves a
        # resumable .tmp — exactly the big-download case the UI must surface.
        assert get_database("gnomad").build_mode == "bundled"
        _write_tmp_partial(settings, "gnomad", 1_000_000)
        with ref_db.begin() as conn:
            conn.execute(
                downloads.insert().values(
                    url="http://example/gnomad_af.db",
                    dest_path=_downloads_dest_str(settings, "gnomad"),
                    total_bytes=2_000_000,
                    status="failed",
                )
            )
        info = find_resumable_download(ref_db, get_database("gnomad"), settings)
        assert info is not None
        assert info["downloaded_bytes"] == 1_000_000


# ═══════════════════════════════════════════════════════════════════════
# get_database_health — state matrix
# ═══════════════════════════════════════════════════════════════════════


def _health(settings: Settings, engine: sa.Engine, db_name: str) -> DatabaseHealth:
    return get_database_health(get_database(db_name), settings, engine)


class TestReferenceResidentHealthMatrix:
    def test_empty_no_version_not_installed(self, settings: Settings, ref_db: sa.Engine) -> None:
        h = _health(settings, ref_db, "clinvar")
        assert h.state == "not_installed"
        assert h.present is False

    def test_rows_no_version_partial(self, settings: Settings, ref_db: sa.Engine) -> None:
        _seed_clinvar(ref_db)
        h = _health(settings, ref_db, "clinvar")
        assert h.state == "partial"
        assert h.present is True
        assert h.integrity_ok is True

    def test_rows_with_version_ready(self, settings: Settings, ref_db: sa.Engine) -> None:
        _seed_clinvar(ref_db)
        _record_db_version(ref_db, "clinvar", "2026-01", 12345)
        h = _health(settings, ref_db, "clinvar")
        assert h.state == "ready"
        assert h.present is True
        assert h.version == "2026-01"
        assert h.downloaded_at is not None

    def test_empty_with_version_corrupt(self, settings: Settings, ref_db: sa.Engine) -> None:
        # Stamp present but the data table is empty (data lost) -> corrupt.
        _record_db_version(ref_db, "clinvar", "2026-01", 12345)
        h = _health(settings, ref_db, "clinvar")
        assert h.state == "corrupt"
        assert h.integrity_ok is False

    def test_reference_db_file_missing_not_installed(self, settings: Settings) -> None:
        # No reference.db on disk and no job rows. Build an empty in-memory engine
        # only for the version/job lookups; the file-missing branch fires first.
        engine = sa.create_engine("sqlite://")
        reference_metadata.create_all(engine)
        assert not settings.reference_db_path.exists()
        h = _health(settings, engine, "clinvar")
        assert h.state == "not_installed"


class TestStandaloneHealthMatrix:
    def test_absent_not_installed(self, settings: Settings, ref_db: sa.Engine) -> None:
        h = _health(settings, ref_db, "gnomad")
        assert h.state == "not_installed"
        assert h.present is False

    def test_corrupt_file_corrupt_and_cleanable(
        self, settings: Settings, ref_db: sa.Engine
    ) -> None:
        path = settings.gnomad_db_path
        path.write_bytes(b"not a sqlite file")
        h = _health(settings, ref_db, "gnomad")
        assert h.state == "corrupt"
        assert h.present is True
        assert h.integrity_ok is False
        assert h.can_clean is True

    def test_present_valid_no_version_partial(self, settings: Settings, ref_db: sa.Engine) -> None:
        # A pipeline DB present on disk but without a version stamp is an
        # interrupted build -> "partial". dbnsfp is the pipeline standalone DB
        # (gnomad ships as a bundle since #312, so bundled gnomad would be "ready"
        # without a version row).
        _make_valid_dbnsfp(settings)
        h = _health(settings, ref_db, "dbnsfp")
        assert h.state == "partial"
        assert h.integrity_ok is True

    def test_valid_rows_version_ready(self, settings: Settings, ref_db: sa.Engine) -> None:
        path = _make_valid_gnomad(settings)
        _record_db_version(ref_db, "gnomad", "v4.1", path.stat().st_size)
        h = _health(settings, ref_db, "gnomad")
        assert h.state == "ready"
        assert h.integrity_ok is True
        assert h.version == "v4.1"
        assert h.file_size_bytes == path.stat().st_size
        assert h.can_verify is True


class TestBundledHealthMatrix:
    def test_vep_bundle_present_valid_no_version_ready(
        self, settings: Settings, ref_db: sa.Engine
    ) -> None:
        # Bundled DBs are "ready" on present+integrity even without a version row.
        _make_valid_vep_bundle(settings)
        h = _health(settings, ref_db, "vep_bundle")
        assert h.state == "ready"
        assert h.version is None
        assert h.integrity_ok is True

    def test_vep_bundle_corrupt_is_corrupt(self, settings: Settings, ref_db: sa.Engine) -> None:
        settings.vep_bundle_db_path.write_bytes(b"not sqlite")
        h = _health(settings, ref_db, "vep_bundle")
        assert h.state == "corrupt"

    def test_ancestry_pca_present_valid_no_version_ready(
        self, settings: Settings, ref_db: sa.Engine
    ) -> None:
        _make_valid_ancestry_pca(settings)
        h = _health(settings, ref_db, "ancestry_pca")
        assert h.state == "ready"
        assert h.version is None
        assert h.integrity_ok is True

    def test_ancestry_pca_absent_not_installed(
        self, settings: Settings, ref_db: sa.Engine
    ) -> None:
        h = _health(settings, ref_db, "ancestry_pca")
        assert h.state == "not_installed"

    def test_gnomad_bundled_present_valid_no_version_ready(
        self, settings: Settings, ref_db: sa.Engine
    ) -> None:
        # Since #312 gnomad ships as a prebuilt bundle (build_mode="bundled"), so
        # a valid gnomad_af.db is "ready" on present+integrity even without a
        # version stamp — it is NOT an interrupted pipeline build.
        assert get_database("gnomad").build_mode == "bundled"
        _make_valid_gnomad(settings)
        h = _health(settings, ref_db, "gnomad")
        assert h.state == "ready"
        assert h.integrity_ok is True


class TestActiveJobHealth:
    """An active jobs row (linked via download_session_jobs) wins precedence."""

    def _register_job(
        self,
        engine: sa.Engine,
        db_name: str,
        *,
        status: str,
        error: str | None = None,
        job_id: str = "job-1",
        created_at: datetime | None = None,
    ) -> None:
        now = created_at or datetime.now(UTC)
        with engine.begin() as conn:
            conn.execute(
                jobs.insert().values(
                    job_id=job_id,
                    job_type="download",
                    status=status,
                    error=error,
                    created_at=now,
                    updated_at=now,
                )
            )
            conn.execute(
                download_sessions.insert().values(
                    session_id="sess-1",
                    status="in_progress",
                    created_at=now,
                    updated_at=now,
                )
            )
            conn.execute(
                download_session_jobs.insert().values(
                    session_id="sess-1",
                    db_name=db_name,
                    job_id=job_id,
                )
            )

    def test_running_pipeline_db_building(self, settings: Settings, ref_db: sa.Engine) -> None:
        self._register_job(ref_db, "gnomad", status="running")
        h = _health(settings, ref_db, "gnomad")
        # gnomad build_mode="pipeline" -> "building".
        assert h.state == "building"
        assert h.active_job_id == "job-1"

    def test_running_download_db_downloading(self, settings: Settings, ref_db: sa.Engine) -> None:
        self._register_job(ref_db, "encode_ccres", status="running")
        h = _health(settings, ref_db, "encode_ccres")
        # encode_ccres build_mode="download" -> "downloading".
        assert h.state == "downloading"
        assert h.active_job_id == "job-1"

    def test_pending_job_is_active(self, settings: Settings, ref_db: sa.Engine) -> None:
        self._register_job(ref_db, "gnomad", status="pending")
        h = _health(settings, ref_db, "gnomad")
        assert h.state == "building"

    def test_failed_job_no_artifact_reports_failed(
        self, settings: Settings, ref_db: sa.Engine
    ) -> None:
        self._register_job(ref_db, "gnomad", status="failed", error="boom", job_id="failed-job")
        h = _health(settings, ref_db, "gnomad")
        assert h.state == "failed"
        assert h.last_error == "boom"


class TestResumablePartialHealth:
    def test_partial_resumable_with_progress(self, settings: Settings, ref_db: sa.Engine) -> None:
        # encode_ccres: .tmp partial + a failed downloads row -> partial+resumable.
        _write_tmp_partial(settings, "encode_ccres", 500)
        with ref_db.begin() as conn:
            conn.execute(
                downloads.insert().values(
                    url="http://example/encode_ccres.db",
                    dest_path=_downloads_dest_str(settings, "encode_ccres"),
                    total_bytes=1000,
                    status="failed",
                )
            )
        h = _health(settings, ref_db, "encode_ccres")
        assert h.state == "partial"
        assert h.resumable is True
        assert h.can_resume is True
        assert h.can_clean is True
        assert h.downloaded_bytes == 500
        assert h.total_bytes == 1000
        assert h.progress_pct == 50.0
        assert h.download_id is not None

    def test_partial_resumable_falls_back_to_expected_size_for_progress(
        self, settings: Settings, ref_db: sa.Engine
    ) -> None:
        """A resumable partial whose row never learned the real total still
        reports a percentage, derived from the artifact's known expected size
        (PR-16) — previously this showed a blank "% saved"."""
        _write_tmp_partial(settings, "encode_ccres", 300)
        with ref_db.begin() as conn:
            conn.execute(
                downloads.insert().values(
                    url="http://example/encode_ccres.db",
                    dest_path=_downloads_dest_str(settings, "encode_ccres"),
                    total_bytes=None,
                    status="failed",
                )
            )
        db_info = get_database("encode_ccres")
        h = _health(settings, ref_db, "encode_ccres")
        assert h.state == "partial"
        assert h.resumable is True
        assert h.total_bytes == db_info.expected_size_bytes
        assert h.progress_pct == round(300 / db_info.expected_size_bytes * 100.0, 1)


class TestGetAllDatabaseHealth:
    def test_returns_one_record_per_registered_db(
        self, settings: Settings, ref_db: sa.Engine
    ) -> None:
        from backend.db.database_registry import get_all_databases

        records = get_all_database_health(settings, ref_db)
        assert len(records) == len(get_all_databases())
        assert all(isinstance(r, DatabaseHealth) for r in records)
        names = {r.name for r in records}
        assert {"clinvar", "gnomad", "vep_bundle", "lai_bundle", "ancestry_pca"} <= names


# ═══════════════════════════════════════════════════════════════════════
# recover_orphaned_downloads
# ═══════════════════════════════════════════════════════════════════════


class TestRecoverOrphanedDownloads:
    def test_downloading_row_swept_to_failed(self, settings: Settings, ref_db: sa.Engine) -> None:
        with ref_db.begin() as conn:
            conn.execute(
                downloads.insert().values(
                    url="http://example/a.db",
                    dest_path=str(settings.downloads_dir / "a.db"),
                    total_bytes=100,
                    status="downloading",
                )
            )
        count = recover_orphaned_downloads(ref_db)
        assert count == 1
        with ref_db.connect() as conn:
            status = conn.execute(sa.select(downloads.c.status)).scalar()
        assert status == "failed"

    def test_pending_row_also_swept(self, settings: Settings, ref_db: sa.Engine) -> None:
        with ref_db.begin() as conn:
            conn.execute(
                downloads.insert().values(
                    url="http://example/b.db",
                    dest_path=str(settings.downloads_dir / "b.db"),
                    status="pending",
                )
            )
        assert recover_orphaned_downloads(ref_db) == 1

    def test_terminal_rows_untouched(self, settings: Settings, ref_db: sa.Engine) -> None:
        with ref_db.begin() as conn:
            conn.execute(
                downloads.insert().values(
                    url="http://example/c.db",
                    dest_path=str(settings.downloads_dir / "c.db"),
                    status="complete",
                )
            )
            conn.execute(
                downloads.insert().values(
                    url="http://example/d.db",
                    dest_path=str(settings.downloads_dir / "d.db"),
                    status="failed",
                )
            )
        assert recover_orphaned_downloads(ref_db) == 0

    def test_no_rows_returns_zero(self, settings: Settings, ref_db: sa.Engine) -> None:
        assert recover_orphaned_downloads(ref_db) == 0


# ═══════════════════════════════════════════════════════════════════════
# clean_database_artifacts
# ═══════════════════════════════════════════════════════════════════════


class TestCleanDatabaseArtifacts:
    def test_removes_corrupt_standalone_and_sidecars(
        self, settings: Settings, ref_db: sa.Engine
    ) -> None:
        path = settings.gnomad_db_path
        path.write_bytes(b"corrupt")
        Path(f"{path}-wal").write_bytes(b"w")
        Path(f"{path}-shm").write_bytes(b"s")
        # Record a version row so we can assert it is cleared.
        _record_db_version(ref_db, "gnomad", "v4.1", 7)

        result = clean_database_artifacts(get_database("gnomad"), settings, ref_db)

        assert result["db_name"] == "gnomad"
        assert str(path) in result["removed"]
        assert str(Path(f"{path}-wal")) in result["removed"]
        assert str(Path(f"{path}-shm")) in result["removed"]
        assert not path.exists()
        assert not Path(f"{path}-wal").exists()
        assert not Path(f"{path}-shm").exists()
        # database_versions row cleared.
        with ref_db.connect() as conn:
            row = conn.execute(
                sa.select(database_versions).where(database_versions.c.db_name == "gnomad")
            ).fetchone()
        assert row is None

    def test_removes_download_tmp_and_clears_downloads_row(
        self, settings: Settings, ref_db: sa.Engine
    ) -> None:
        # download-mode DB: clean removes the .tmp partial + the downloads row.
        tmp_path = _write_tmp_partial(settings, "encode_ccres", 256)
        with ref_db.begin() as conn:
            conn.execute(
                downloads.insert().values(
                    url="http://example/encode_ccres.db",
                    dest_path=_downloads_dest_str(settings, "encode_ccres"),
                    total_bytes=1024,
                    status="failed",
                )
            )
        result = clean_database_artifacts(get_database("encode_ccres"), settings, ref_db)
        assert str(tmp_path) in result["removed"]
        assert not tmp_path.exists()
        with ref_db.connect() as conn:
            cnt = conn.execute(sa.select(sa.func.count()).select_from(downloads)).scalar()
        assert cnt == 0

    def test_does_not_delete_reference_db(self, settings: Settings, ref_db: sa.Engine) -> None:
        # reference.db must survive cleaning a reference-resident DB.
        _seed_clinvar(ref_db)
        _record_db_version(ref_db, "clinvar", "2026-01", 100)
        assert settings.reference_db_path.exists()

        result = clean_database_artifacts(get_database("clinvar"), settings, ref_db)

        # The shared reference.db file is never in the removed list and survives.
        assert str(settings.reference_db_path) not in result["removed"]
        assert settings.reference_db_path.exists()
        # The version stamp is still cleared.
        with ref_db.connect() as conn:
            row = conn.execute(
                sa.select(database_versions).where(database_versions.c.db_name == "clinvar")
            ).fetchone()
        assert row is None
        # The clinvar data table itself is left intact (rebuild replaces it).
        with ref_db.connect() as conn:
            cnt = conn.execute(sa.select(sa.func.count()).select_from(clinvar_variants)).scalar()
        assert cnt == 1

    def test_removes_lai_bundle_directory(self, settings: Settings, ref_db: sa.Engine) -> None:
        bundle = _make_valid_lai_bundle(settings)
        result = clean_database_artifacts(get_database("lai_bundle"), settings, ref_db)
        assert str(bundle) in result["removed"]
        assert not bundle.exists()

    def test_removes_ancestry_pca_npz(self, settings: Settings, ref_db: sa.Engine) -> None:
        path = _make_valid_ancestry_pca(settings)
        result = clean_database_artifacts(get_database("ancestry_pca"), settings, ref_db)
        assert str(path) in result["removed"]
        assert not path.exists()

    def test_clean_absent_artifact_returns_empty(
        self, settings: Settings, ref_db: sa.Engine
    ) -> None:
        # Nothing on disk and no rows -> removed list empty, no error.
        result = clean_database_artifacts(get_database("gnomad"), settings, ref_db)
        assert result["db_name"] == "gnomad"
        assert result["removed"] == []

"""Reference-database health, integrity, and resume observability.

The download/build transport is already hardened (resumable streaming with
SHA-256 in :mod:`backend.annotation.http_download` /
:mod:`backend.db.download_manager`, per-DB build serialization in
:mod:`backend.db.build_guard`, and crash recovery for the ``jobs`` table in
:func:`backend.tasks.huey_tasks.recover_orphaned_jobs`). What was missing — and
what this module adds — is *observability*: a single, honest answer to "what
state is every reference database in, is it actually readable by the annotation
code, and can an interrupted download be resumed?"

Three concerns live here:

* **Integrity / readability** (:func:`validate_database`). A file that merely
  *exists* is not the same as a file the annotation pipeline can *read*. A build
  killed mid-``INSERT`` leaves a structurally valid SQLite whose target table is
  empty; a download truncated at the filesystem leaves a malformed DB image; a
  half-extracted bundle is missing model files. Each check opens the artifact the
  way its consumer does (the exact table / npz keys / directory layout that
  ``backend.annotation.*`` and ``backend.analysis.*`` query) so "healthy" means
  "the code that needs this can use it."

* **State derivation** (:func:`get_database_health`). Fuses on-disk presence, the
  ``database_versions`` stamp, any active download/build job, a resumable partial
  (``.tmp`` + ``downloads`` row), and the integrity result into one ``state``:
  ``not_installed | downloading | building | partial | corrupt | ready |
  failed``. This is what the UI renders for 100% database-health observability.

* **Crash reconciliation** (:func:`recover_orphaned_downloads`). The companion to
  ``recover_orphaned_jobs``: a process killed mid-download leaves a ``downloads``
  row stuck in ``downloading`` forever. Sweeping those to ``failed`` on startup
  turns them into honestly-reported, resumable partials instead of a phantom
  "in progress" that never moves.

Design notes:

* Read-only and side-effect-light. Unlike
  :func:`backend.db.database_registry.get_database_status`, nothing here copies a
  committed bundled fixture into ``data_dir`` as a side effect of *checking*
  status.
* ``GET /databases/health`` calls the fast *structural* check (open + the target
  table is queryable and non-empty). The slower ``PRAGMA quick_check`` is run
  only on the explicit ``POST /databases/{name}/verify`` action so the health
  poll stays cheap even with multi-GB databases.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import sqlalchemy as sa
import structlog
from sqlalchemy.pool import NullPool

from backend.db.database_registry import (
    DatabaseInfo,
    get_all_databases,
    get_database,
    validate_lai_bundle,
)
from backend.db.download_manager import INCOMPLETE_DOWNLOAD_STATES
from backend.db.tables import database_versions, download_session_jobs, downloads, jobs

if TYPE_CHECKING:
    from backend.config import Settings

logger = structlog.get_logger(__name__)


# ── Integrity specification ──────────────────────────────────────────
# Per database: which tables (and whether they must be non-empty) the
# consuming annotation/analysis code reads. Validating exactly these makes
# "healthy" mean "readable by the code that needs it".
#
# Reference-resident DBs live as tables inside reference.db; standalone DBs are
# their own SQLite file; ancestry_pca is an .npz; lai_bundle is a directory.

# Tables required for each reference.db-resident logical database.
# (table_name, must_have_rows)
_REFERENCE_TABLE_SPEC: dict[str, list[tuple[str, bool]]] = {
    "clinvar": [("clinvar_variants", True)],
    "cpic": [
        ("cpic_alleles", True),
        ("cpic_diplotypes", False),
        ("cpic_guidelines", False),
    ],
    "clingen": [("clingen_gene_validity", True)],
    "gwas_catalog": [("gwas_associations", True)],
    "dbsnp": [("dbsnp_merges", True)],
    "mondo_hpo": [("gene_phenotype", True)],
}

# Tables required for each standalone SQLite file. (table_name, must_have_rows)
_STANDALONE_TABLE_SPEC: dict[str, list[tuple[str, bool]]] = {
    "gnomad": [("gnomad_af", True)],
    "dbnsfp": [("dbnsfp_scores", True)],
    "alphamissense": [("alphamissense_scores", True)],
    "gtex_eqtl": [("gtex_eqtl", True)],
    # bundle_metadata only needs to exist; vep_annotations carries the data.
    "vep_bundle": [("vep_annotations", True), ("bundle_metadata", False)],
    "encode_ccres": [("encode_ccres", True)],
    # pgs_score_weights carries the data; pgs_score_metadata only needs to exist.
    "pgs_scores": [("pgs_score_weights", True), ("pgs_score_metadata", False)],
}

# numpy array keys that ``backend.analysis.ancestry.load_ancestry_bundle`` (the
# ancestry_pca consumer) dereferences. This MUST match every ``data[...]`` read
# in that loader — a missing key would otherwise let a structurally-incomplete
# bundle report "ready" yet crash ancestry analysis with a KeyError. (The
# haplogroup ``trees``/``haplogroup`` keys belong to a separate bundle, not this
# one, so they are intentionally excluded.)
_ANCESTRY_PCA_KEYS: frozenset[str] = frozenset(
    {
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
    }
)

# build_modes whose "ready" state requires a recorded version stamp. A pipeline
# or download artifact present on disk *without* a database_versions row is an
# interrupted build/finalize, not a finished DB. Bundled DBs may legitimately
# exist as the committed offline fixture without a version row.
_VERSION_REQUIRED_MODES: frozenset[str] = frozenset({"pipeline", "download"})

# downloads.status values that indicate an in-flight transfer.
_ACTIVE_DOWNLOAD_STATES: frozenset[str] = frozenset({"downloading"})

# downloads.status values that carry a *resumable* partial: every incomplete
# state that is not currently active. Derived from DownloadManager's single
# source of truth (the set its resume path reuses) so the "what can resume"
# definition cannot drift between the transport and health reporting — a row the
# DownloadManager would resume is never reported here as unresumable. Excludes
# "downloading": an actively-downloading row is in-progress, not a resumable
# leftover, and is reported as state="downloading" (see
# :func:`_active_download_row`); the startup sweep flips orphaned "downloading"
# rows to "failed", moving them into this set.
_RESUMABLE_DOWNLOAD_STATES: frozenset[str] = INCOMPLETE_DOWNLOAD_STATES - _ACTIVE_DOWNLOAD_STATES

# build_modes whose artifact is fetched through :class:`DownloadManager` (which
# tracks a ``downloads`` row + a checkpointed ``.tmp`` in downloads_dir, so the
# transfer is resumable). "download" = lai/encode; "bundled" covers the
# manifest-driven bundle downloads (ancestry_pca, and gnomAD once it ships as a
# prebuilt bundle) that route through ``_run_bundle_download``. Pipeline builders
# stream raw inputs under different filenames with resumable=False, so they never
# leave a downloads_dir/<filename>.tmp and are correctly excluded.
_DOWNLOAD_MANAGER_MODES: frozenset[str] = frozenset({"download", "bundled"})


# ── Result types ─────────────────────────────────────────────────────


@dataclass(frozen=True)
class IntegrityResult:
    """Outcome of a readability / integrity check for one database."""

    ok: bool
    detail: str
    depth: str  # "structural" | "deep" | "absent"


@dataclass(frozen=True)
class DatabaseHealth:
    """Fused health record for a single reference database."""

    name: str
    display_name: str
    build_mode: str
    required: bool
    state: str  # not_installed|downloading|building|partial|corrupt|ready|failed
    present: bool
    version: str | None
    downloaded_at: str | None
    file_size_bytes: int | None
    expected_size_bytes: int
    integrity_ok: bool | None
    integrity_detail: str | None
    resumable: bool
    download_id: int | None
    downloaded_bytes: int | None
    total_bytes: int | None
    progress_pct: float | None
    active_job_id: str | None
    last_error: str | None
    can_clean: bool
    can_resume: bool
    can_verify: bool


# ── Artifact resolution ──────────────────────────────────────────────


def _artifact_path(db_info: DatabaseInfo, settings: Settings) -> Path:
    """Resolve the on-disk artifact for ``db_info`` without side effects.

    Returns the directory for the LAI bundle, the ``.npz`` for ancestry_pca,
    ``reference.db`` for reference-resident DBs, otherwise the standalone file.
    """
    if db_info.name == "lai_bundle":
        return settings.resolved_lai_bundle_path
    if db_info.name == "ancestry_pca":
        return settings.data_dir / db_info.filename
    if db_info.target_db == "reference":
        return settings.reference_db_path
    return db_info.dest_path(settings)


def artifact_present(db_info: DatabaseInfo, settings: Settings) -> bool:
    """Whether the database's primary artifact exists on disk.

    For reference-resident DBs, file presence (reference.db) is necessary but
    not sufficient — emptiness/corruption is caught by :func:`validate_database`.
    This is read-only: it never materializes a committed bundled fixture (which
    :func:`backend.db.database_registry.get_database_status` does as a side
    effect).
    """
    path = _artifact_path(db_info, settings)
    if db_info.name == "lai_bundle":
        return path.is_dir() and any(path.iterdir()) if path.exists() else False
    return path.exists()


# ── Low-level integrity probes ───────────────────────────────────────


def _check_sqlite_tables(
    engine: sa.Engine,
    spec: list[tuple[str, bool]],
    *,
    deep: bool,
) -> IntegrityResult:
    """Validate that ``spec`` tables exist (and, where required, are non-empty).

    With ``deep=True`` a ``PRAGMA quick_check`` runs first to detect a corrupt
    or truncated SQLite image. The structural pass then confirms each consumer
    table is queryable; a missing table surfaces as an ``OperationalError``.
    """
    try:
        with engine.connect() as conn:
            if deep:
                rows = conn.execute(sa.text("PRAGMA quick_check")).fetchall()
                verdict = rows[0][0] if rows else "no result"
                if verdict != "ok":
                    return IntegrityResult(
                        ok=False, detail=f"quick_check: {verdict}", depth="deep"
                    )
            for table, must_have_rows in spec:
                # Existence + queryability: a missing table raises here.
                conn.execute(sa.text(f'SELECT 1 FROM "{table}" LIMIT 1'))  # noqa: S608
                if must_have_rows:
                    has_row = conn.execute(
                        sa.text(f'SELECT EXISTS(SELECT 1 FROM "{table}")')  # noqa: S608
                    ).scalar()
                    if not has_row:
                        return IntegrityResult(
                            ok=False,
                            detail=f"table '{table}' is empty",
                            depth="deep" if deep else "structural",
                        )
    except Exception as exc:
        return IntegrityResult(
            ok=False,
            detail=f"{type(exc).__name__}: {exc}",
            depth="deep" if deep else "structural",
        )
    return IntegrityResult(ok=True, detail="ok", depth="deep" if deep else "structural")


def _standalone_engine(path: Path) -> sa.Engine:
    """A throwaway, pool-less engine for probing a standalone SQLite file."""
    return sa.create_engine(f"sqlite:///{path}", poolclass=NullPool)


def _check_npz(path: Path, required_keys: frozenset[str]) -> IntegrityResult:
    """Validate that an ``.npz`` loads and carries the required array keys."""
    try:
        import numpy as np

        with np.load(path, allow_pickle=False) as data:
            present = set(data.files)
        missing = sorted(required_keys - present)
        if missing:
            return IntegrityResult(
                ok=False,
                detail=f"missing array(s): {', '.join(missing)}",
                depth="structural",
            )
    except Exception as exc:
        return IntegrityResult(ok=False, detail=f"{type(exc).__name__}: {exc}", depth="structural")
    return IntegrityResult(ok=True, detail="ok", depth="structural")


# ── Public integrity API ─────────────────────────────────────────────


def validate_database(
    db_name: str,
    settings: Settings,
    *,
    engine: sa.Engine | None = None,
    deep: bool = False,
) -> IntegrityResult:
    """Check that ``db_name``'s artifact is present and readable by its consumer.

    Args:
        db_name: Registry database name.
        settings: Active settings (resolves artifact paths).
        engine: Optional reference.db engine reused for reference-resident DBs
            (avoids re-opening the shared file). Standalone DBs always use a
            throwaway pool-less engine.
        deep: Run ``PRAGMA quick_check`` (SQLite) in addition to the structural
            check. Slower; used by the explicit verify action.

    Returns:
        :class:`IntegrityResult`. ``depth="absent"`` when the artifact is
        missing (not an error per se — the caller maps that to ``not_installed``).
    """
    db_info = get_database(db_name)
    if db_info is None:
        return IntegrityResult(ok=False, detail=f"unknown database '{db_name}'", depth="absent")

    path = _artifact_path(db_info, settings)
    if not path.exists():
        return IntegrityResult(ok=False, detail="not present", depth="absent")

    # LAI bundle: directory of model files.
    if db_name == "lai_bundle":
        if validate_lai_bundle(path):
            return IntegrityResult(ok=True, detail="ok", depth="structural")
        return IntegrityResult(
            ok=False, detail="incomplete LAI bundle (missing chr/model files)", depth="structural"
        )

    # ancestry_pca: numpy archive.
    if db_name == "ancestry_pca":
        return _check_npz(path, _ANCESTRY_PCA_KEYS)

    # reference-resident DBs: probe the shared reference.db.
    ref_spec = _REFERENCE_TABLE_SPEC.get(db_name)
    if ref_spec is not None:
        if engine is not None:
            return _check_sqlite_tables(engine, ref_spec, deep=deep)
        probe = _standalone_engine(path)
        try:
            return _check_sqlite_tables(probe, ref_spec, deep=deep)
        finally:
            probe.dispose()

    # standalone SQLite DBs.
    std_spec = _STANDALONE_TABLE_SPEC.get(db_name)
    if std_spec is not None:
        probe = _standalone_engine(path)
        try:
            return _check_sqlite_tables(probe, std_spec, deep=deep)
        finally:
            probe.dispose()

    # No integrity contract registered (e.g. manual-mode DBs): presence is all
    # we can vouch for.
    return IntegrityResult(ok=True, detail="present (no integrity contract)", depth="structural")


# ── Resumable-partial discovery ──────────────────────────────────────


def find_resumable_download(
    engine: sa.Engine,
    db_info: DatabaseInfo,
    settings: Settings,
) -> dict | None:
    """Return resume metadata for an interrupted ``download``-mode transfer.

    A resumable partial = a ``downloads`` row for this DB's download destination
    in a non-terminal/failed state *and* a ``.tmp`` file with bytes on disk. The
    byte count comes from the file (the authoritative resume offset), not the DB
    checkpoint. Only ``build_mode == "download"`` DBs flow through
    :class:`DownloadManager`, so only they can resume this way.

    Returns ``{download_id, downloaded_bytes, total_bytes}`` or ``None``.
    """
    # Only DownloadManager-backed DBs leave a resumable .tmp; an empty filename
    # (pipeline DBs with no standalone artifact) has no downloads_dir target.
    if db_info.build_mode not in _DOWNLOAD_MANAGER_MODES or not db_info.filename:
        return None

    dl_dest = settings.downloads_dir / db_info.filename
    tmp_path = dl_dest.with_suffix(dl_dest.suffix + ".tmp")
    if not tmp_path.exists():
        return None
    try:
        on_disk = tmp_path.stat().st_size
    except OSError:
        return None
    if on_disk <= 0:
        return None

    with engine.connect() as conn:
        row = conn.execute(
            sa.select(downloads.c.id, downloads.c.total_bytes, downloads.c.status)
            .where(
                downloads.c.dest_path == str(dl_dest),
                downloads.c.status.in_(tuple(_RESUMABLE_DOWNLOAD_STATES)),
            )
            .order_by(downloads.c.created_at.desc())
            .limit(1)
        ).fetchone()
    if row is None:
        return None
    return {
        "download_id": row.id,
        "downloaded_bytes": on_disk,
        # Fall back to the artifact's known size when the row never learned the
        # real total (the original transfer died before the first response set
        # it), so "% saved" is always computable instead of a blank percentage.
        "total_bytes": row.total_bytes or db_info.expected_size_bytes or None,
    }


def _active_download_row(
    engine: sa.Engine, db_info: DatabaseInfo, settings: Settings
) -> dict | None:
    """Return progress for an *in-flight* ``download``-mode transfer, or ``None``.

    Distinct from :func:`find_resumable_download`: this matches a ``downloads``
    row still in the ``downloading`` state (set by :class:`DownloadManager` for
    the duration of an active transfer; swept to ``failed`` on startup by
    :func:`recover_orphaned_downloads`). It catches Update-Manager-triggered
    bundle downloads (lai/ancestry/gnomad), which DownloadManager tracks but
    which do not register a ``download_session_jobs`` row.
    """
    if db_info.build_mode not in _DOWNLOAD_MANAGER_MODES or not db_info.filename:
        return None

    dl_dest = settings.downloads_dir / db_info.filename
    with engine.connect() as conn:
        row = conn.execute(
            sa.select(downloads.c.id, downloads.c.downloaded_bytes, downloads.c.total_bytes)
            .where(
                downloads.c.dest_path == str(dl_dest),
                downloads.c.status.in_(tuple(_ACTIVE_DOWNLOAD_STATES)),
            )
            .order_by(downloads.c.created_at.desc())
            .limit(1)
        ).fetchone()
    if row is None:
        return None
    tmp_path = dl_dest.with_suffix(dl_dest.suffix + ".tmp")
    on_disk = tmp_path.stat().st_size if tmp_path.exists() else (row.downloaded_bytes or 0)
    return {
        "download_id": row.id,
        "downloaded_bytes": on_disk,
        # Same known-size fallback as find_resumable_download, so an active
        # transfer whose total has not yet been learned still shows a percentage.
        "total_bytes": row.total_bytes or db_info.expected_size_bytes or None,
    }


# ── Job correlation ──────────────────────────────────────────────────


def _job_for_db(engine: sa.Engine, db_name: str, statuses: tuple[str, ...]) -> sa.Row | None:
    """Most-recent job for ``db_name`` (linked via download_session_jobs).

    Covers the setup-wizard download/build flow and the resume endpoint, which
    both register a ``download_session_jobs`` row. (Update-manager-triggered
    builds are tracked by the Update Manager's own job polling.)
    """
    with engine.connect() as conn:
        row = conn.execute(
            sa.select(jobs.c.job_id, jobs.c.status, jobs.c.error, jobs.c.message)
            .select_from(
                download_session_jobs.join(jobs, download_session_jobs.c.job_id == jobs.c.job_id)
            )
            .where(
                download_session_jobs.c.db_name == db_name,
                jobs.c.status.in_(statuses),
            )
            .order_by(jobs.c.created_at.desc())
            .limit(1)
        ).fetchone()
    return row


# ── Version stamp lookup ─────────────────────────────────────────────


def _version_stamp(engine: sa.Engine, db_name: str) -> tuple[str | None, str | None, int | None]:
    """Return ``(version, downloaded_at_iso, file_size_bytes)`` from the stamp table."""
    with engine.connect() as conn:
        row = conn.execute(
            sa.select(
                database_versions.c.version,
                database_versions.c.downloaded_at,
                database_versions.c.file_size_bytes,
            ).where(database_versions.c.db_name == db_name)
        ).fetchone()
    if row is None:
        return None, None, None
    downloaded_at = row.downloaded_at.isoformat() if row.downloaded_at else None
    return row.version, downloaded_at, row.file_size_bytes


def _file_size(db_info: DatabaseInfo, settings: Settings) -> int | None:
    """Best-effort on-disk size of the artifact (sum for the LAI directory)."""
    path = _artifact_path(db_info, settings)
    try:
        if db_info.name == "lai_bundle":
            if not path.is_dir():
                return None
            return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())
        return path.stat().st_size if path.exists() else None
    except OSError:
        return None


# ── Health aggregation ───────────────────────────────────────────────


def get_database_health(
    db_info: DatabaseInfo,
    settings: Settings,
    engine: sa.Engine,
) -> DatabaseHealth:
    """Derive the fused :class:`DatabaseHealth` record for one database.

    State precedence: an active transfer/build wins (a session-linked job, an
    in-flight ``downloads`` row, or a held per-DB build lock); then a resumable
    partial; then on-disk presence resolves to ready/partial/corrupt by
    integrity + version stamp; finally absence resolves to failed (if the last
    job failed) or not_installed.
    """
    from backend.db.build_guard import is_build_locked

    name = db_info.name
    is_reference_resident = db_info.target_db == "reference"
    needs_version = db_info.build_mode in _VERSION_REQUIRED_MODES

    version, downloaded_at, stamp_size = _version_stamp(engine, name)
    resumable_info = find_resumable_download(engine, db_info, settings)
    active_dl = _active_download_row(engine, db_info, settings)

    present = False
    file_size: int | None = None
    state = "not_installed"
    integrity_ok: bool | None = None
    integrity_detail: str | None = None
    download_id: int | None = None
    downloaded_bytes: int | None = None
    total_bytes: int | None = None
    progress_pct: float | None = None
    active_job_id: str | None = None
    last_error: str | None = None

    def _failure_or_absent() -> tuple[str, str | None]:
        """Map the no-data case to ``failed`` (last job failed) or ``not_installed``."""
        failed = _job_for_db(engine, name, ("failed",))
        if failed is not None:
            return "failed", failed.error
        return "not_installed", None

    active = _job_for_db(engine, name, ("pending", "running"))
    # A held build lock means a pipeline build is running right now (setup wizard
    # OR the update manager — both acquire it), even when no session job links it.
    build_active = db_info.build_mode == "pipeline" and is_build_locked(name)

    if active is not None:
        active_job_id = active.job_id
        state = "downloading" if db_info.build_mode == "download" else "building"
        present = artifact_present(db_info, settings)
        file_size = stamp_size if is_reference_resident else _file_size(db_info, settings)
    elif active_dl is not None:
        # Update-manager-triggered bundle download (lai/ancestry): tracked by
        # DownloadManager's downloads row but not a session job.
        state = "downloading"
        download_id = active_dl["download_id"]
        downloaded_bytes = active_dl["downloaded_bytes"]
        total_bytes = active_dl["total_bytes"]
        if total_bytes:
            progress_pct = round(min(100.0, downloaded_bytes / total_bytes * 100.0), 1)
        present = artifact_present(db_info, settings)
        file_size = _file_size(db_info, settings)
    elif build_active:
        state = "building"
        present = artifact_present(db_info, settings)
        file_size = stamp_size if is_reference_resident else _file_size(db_info, settings)
    elif resumable_info is not None:
        # Download-mode interrupted transfer with a checkpointed .tmp partial.
        state = "partial"
        download_id = resumable_info["download_id"]
        downloaded_bytes = resumable_info["downloaded_bytes"]
        total_bytes = resumable_info["total_bytes"]
        if total_bytes:
            progress_pct = round(min(100.0, downloaded_bytes / total_bytes * 100.0), 1)
        failed = _job_for_db(engine, name, ("failed",))
        last_error = failed.error if failed is not None else None
    elif is_reference_resident:
        # reference.db-resident: "present" means the consumer table holds data.
        # The shared reference.db file existing is not, by itself, an install.
        if not settings.reference_db_path.exists():
            state, last_error = _failure_or_absent()
        else:
            integ = validate_database(name, settings, engine=engine, deep=False)
            integrity_ok = integ.ok
            integrity_detail = integ.detail
            present = integ.ok
            file_size = stamp_size
            if integ.ok:
                state = "ready" if version is not None else "partial"
            elif "is empty" in integ.detail and version is None:
                # Table empty and never recorded → not built (or cleaned), not corrupt.
                state, last_error = _failure_or_absent()
            else:
                # Empty-but-stamped (data lost) or a malformed reference.db image.
                state = "corrupt"
    else:
        # File/dir-based DB (standalone SQLite, npz bundle, LAI directory).
        present = artifact_present(db_info, settings)
        if present:
            file_size = _file_size(db_info, settings)
            integ = validate_database(name, settings, engine=engine, deep=False)
            integrity_ok = integ.ok
            integrity_detail = integ.detail
            if not integ.ok:
                state = "corrupt"
            elif needs_version and version is None:
                # Artifact + data on disk but the finalize never stamped a
                # version — an interrupted build/extract, not a finished DB.
                state = "partial"
            else:
                state = "ready"
        else:
            file_size = stamp_size
            state, last_error = _failure_or_absent()

    can_resume = resumable_info is not None and state in {"partial", "failed"}
    can_clean = state in {"partial", "corrupt", "failed"} or can_resume
    can_verify = present and state in {"ready", "corrupt", "partial"}

    return DatabaseHealth(
        name=name,
        display_name=db_info.display_name,
        build_mode=db_info.build_mode,
        required=db_info.required,
        state=state,
        present=present,
        version=version,
        downloaded_at=downloaded_at,
        file_size_bytes=file_size if file_size is not None else stamp_size,
        expected_size_bytes=db_info.expected_size_bytes,
        integrity_ok=integrity_ok,
        integrity_detail=integrity_detail,
        resumable=can_resume,
        download_id=download_id,
        downloaded_bytes=downloaded_bytes,
        total_bytes=total_bytes,
        progress_pct=progress_pct,
        active_job_id=active_job_id,
        last_error=last_error,
        can_clean=can_clean,
        can_resume=can_resume,
        can_verify=can_verify,
    )


def get_all_database_health(settings: Settings, engine: sa.Engine) -> list[DatabaseHealth]:
    """Health records for every registered database."""
    return [get_database_health(db, settings, engine) for db in get_all_databases()]


# ── Crash reconciliation ─────────────────────────────────────────────


def recover_orphaned_downloads(engine: sa.Engine) -> int:
    """Mark ``downloads`` rows stuck mid-transfer as failed (startup sweep).

    The companion to :func:`backend.tasks.huey_tasks.recover_orphaned_jobs`. A
    process killed mid-download leaves a row in ``downloading`` (or ``pending``)
    that never advances, so the UI shows a phantom in-progress transfer forever.
    Sweeping them to ``failed`` keeps the byte checkpoint intact, so the partial
    surfaces honestly as resumable. Returns the number of rows updated.
    """
    with engine.begin() as conn:
        result = conn.execute(
            downloads.update()
            .where(downloads.c.status.in_(("downloading", "pending")))
            .values(status="failed", updated_at=datetime.now(UTC))
        )
        count = result.rowcount or 0
    if count:
        logger.info("orphaned_downloads_recovered", count=count)
    return count


# ── Artifact cleanup (partial/corrupt removal) ───────────────────────


def clean_database_artifacts(db_info: DatabaseInfo, settings: Settings, engine: sa.Engine) -> dict:
    """Remove a partial/corrupt artifact so a fresh download/build can proceed.

    Deletes the standalone DB file (plus its ``-wal``/``-shm`` sidecars), the
    extracted LAI directory, the ``.npz``, or — for download-mode DBs — the
    ``downloads`` ``.tmp`` partial, then clears the matching ``downloads`` and
    ``database_versions`` rows. Reference-resident tables are left to the
    rebuild (which replaces them) since reference.db is shared across DBs.

    Returns a summary of what was removed.
    """
    import shutil

    removed: list[str] = []

    # DownloadManager-backed .tmp partial in downloads_dir. Guard on a non-empty
    # filename so a DB with filename="" (reference-resident pipeline DBs) never
    # resolves downloads_dir / "" == downloads_dir and targets the directory.
    has_dl_target = db_info.build_mode in _DOWNLOAD_MANAGER_MODES and bool(db_info.filename)
    if has_dl_target:
        dl_dest = settings.downloads_dir / db_info.filename
        for p in (dl_dest, dl_dest.with_suffix(dl_dest.suffix + ".tmp")):
            if p.exists():
                try:
                    p.unlink()
                    removed.append(str(p))
                except OSError as exc:
                    logger.warning("clean_unlink_failed", path=str(p), error=str(exc))

    # The primary artifact (skip the shared reference.db — never delete it).
    path = _artifact_path(db_info, settings)
    if db_info.target_db != "reference" or db_info.name in ("ancestry_pca", "lai_bundle"):
        if db_info.name == "lai_bundle" and path.is_dir():
            try:
                shutil.rmtree(path)
                removed.append(str(path))
            except OSError as exc:
                logger.warning("clean_rmtree_failed", path=str(path), error=str(exc))
        elif path.exists() and path.is_file():
            for p in (path, Path(f"{path}-wal"), Path(f"{path}-shm")):
                if p.exists():
                    try:
                        p.unlink()
                        removed.append(str(p))
                    except OSError as exc:
                        logger.warning("clean_unlink_failed", path=str(p), error=str(exc))

    # Clear tracking rows so the DB reads as not_installed afterwards.
    with engine.begin() as conn:
        if db_info.filename:
            dl_dest = settings.downloads_dir / db_info.filename
            conn.execute(downloads.delete().where(downloads.c.dest_path == str(dl_dest)))
        conn.execute(database_versions.delete().where(database_versions.c.db_name == db_info.name))

    logger.info("database_artifacts_cleaned", db_name=db_info.name, removed=removed)
    return {"db_name": db_info.name, "removed": removed}

"""Setup wizard API routes (P1-19a, P1-19b, P1-19c, P1-19e).

Endpoints:
    GET  /api/setup/status             — Check first-launch state and disclaimer acceptance
    POST /api/setup/accept-disclaimer  — Record global disclaimer acceptance
    GET  /api/setup/disclaimer         — Get disclaimer text
    GET  /api/setup/detect-existing    — Auto-detect existing installation
    POST /api/setup/import-backup      — Import from .tar.gz backup archive
    GET  /api/setup/storage-info       — Get current storage path and disk space info
    POST /api/setup/set-storage-path   — Validate/create a storage path
    GET  /api/setup/credentials        — Get current external service credentials
    POST /api/setup/credentials        — Save external service credentials to config.toml
"""

from __future__ import annotations

import errno
import json
import os
import re
import shutil
import tarfile
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import sqlalchemy as sa
import structlog
from fastapi import APIRouter, HTTPException, UploadFile
from packaging.version import InvalidVersion, Version
from pydantic import BaseModel, field_validator

from backend.config import (
    config_toml_path,
    config_write_lock,
    get_settings,
    read_config_section,
    write_config_section,
    write_config_toml,
    write_data_dir_pointer,
)
from backend.db.connection import get_registry
from backend.db.database_registry import get_all_databases
from backend.db.db_health import get_database_health
from backend.disclaimers import (
    GLOBAL_DISCLAIMER_ACCEPT_LABEL,
    GLOBAL_DISCLAIMER_TEXT,
    GLOBAL_DISCLAIMER_TITLE,
)

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/setup", tags=["setup"])

# ── Response models ──────────────────────────────────────────────────


class DbReadiness(BaseModel):
    """Health/readiness of one database that gates the dashboard."""

    name: str
    state: str  # mirrors DatabaseHealth.state
    ready: bool
    build_mode: str


class SetupStatusResponse(BaseModel):
    """Current setup status — determines whether wizard should be shown."""

    needs_setup: bool
    disclaimer_accepted: bool
    has_databases: bool
    required_dbs_ready: bool
    db_readiness: list[DbReadiness]
    has_samples: bool
    data_dir: str


class DisclaimerResponse(BaseModel):
    """Global disclaimer text for the setup wizard."""

    title: str
    text: str
    accept_label: str


class AcceptDisclaimerResponse(BaseModel):
    """Confirmation of disclaimer acceptance."""

    accepted: bool
    accepted_at: str


class DetectExistingResponse(BaseModel):
    """Result of auto-detecting an existing installation."""

    existing_found: bool
    has_config: bool
    has_samples: bool
    has_databases: bool
    data_dir: str


class ImportBackupResponse(BaseModel):
    """Result of importing a backup archive."""

    success: bool
    samples_restored: int
    config_restored: bool
    message: str


class StorageInfoResponse(BaseModel):
    """Current storage path and disk space information."""

    data_dir: str
    free_space_bytes: int
    free_space_gb: float
    total_space_bytes: int
    total_space_gb: float
    status: Literal["ok", "warning", "blocked"]
    message: str
    path_exists: bool
    path_writable: bool


class SetStoragePathRequest(BaseModel):
    """Request to set the storage path."""

    path: str


class SetStoragePathResponse(BaseModel):
    """Result of setting the storage path."""

    success: bool
    data_dir: str
    free_space_gb: float
    status: Literal["ok", "warning", "blocked"]
    message: str


# ── Helpers ──────────────────────────────────────────────────────────


# Bump when the disclaimer text materially changes — a stored flag with an older
# (or missing/corrupt) version is treated as not-accepted, forcing re-acceptance.
_DISCLAIMER_VERSION = "1.0"


def _disclaimer_flag_path() -> Path:
    """Path to the disclaimer acceptance flag file."""
    settings = get_settings()
    return settings.data_dir / ".disclaimer_accepted"


def _is_disclaimer_accepted() -> bool:
    """Whether the current global disclaimer has been accepted.

    Parses the flag file rather than checking mere existence: a truncated or
    corrupt flag (e.g. a crash mid-write) no longer counts as accepted, and a
    flag written for an older disclaimer version forces re-acceptance.
    """
    try:
        data = json.loads(_disclaimer_flag_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(data, dict) and data.get("version") == _DISCLAIMER_VERSION


def _is_writable(path: Path) -> bool:
    """Whether ``path`` (an existing directory) accepts a new file.

    Uses a uniquely-named temp file rather than a fixed ``.write_test`` name, so
    concurrent probes can't race on the same path and a crash never leaves a
    stray file behind.
    """
    try:
        with tempfile.NamedTemporaryFile(dir=path, prefix=".write_test_"):
            return True
    except OSError:
        return False


def _has_any_databases() -> bool:
    """Check if any reference databases have been downloaded or built."""
    settings = get_settings()
    # Check standalone DB files
    standalone_files = [
        settings.data_dir / "gnomad_af.db",
        settings.data_dir / "dbnsfp.db",
    ]
    if any(f.exists() for f in standalone_files):
        return True
    # Check reference.db-resident databases via database_versions table
    try:
        import sqlalchemy as sa

        from backend.db.tables import database_versions

        ref_path = settings.reference_db_path
        if not ref_path.exists():
            return False
        engine = sa.create_engine(f"sqlite:///{ref_path}")
        try:
            with engine.connect() as conn:
                count = conn.execute(
                    sa.select(sa.func.count()).select_from(database_versions)
                ).scalar()
            return (count or 0) > 0
        finally:
            engine.dispose()
    except Exception:
        return False


def _has_any_samples() -> bool:
    """Check if any sample databases exist."""
    settings = get_settings()
    samples_dir = settings.samples_dir
    if not samples_dir.exists():
        return False
    return any(samples_dir.glob("sample_*.db"))


# Build modes the readiness gate enforces. ``bundled`` ships with the app and
# ``manual`` is user-built, so both are exempt — only artifacts the wizard must
# fetch/build (download/pipeline) gate the dashboard.
_GATE_BUILD_MODES = frozenset({"download", "pipeline"})


def _required_dbs_ready() -> tuple[bool, list[DbReadiness]]:
    """Whether every required, downloadable database is integrity-``ready``.

    Reuses the :mod:`backend.db.db_health` state machine (it never re-implements
    integrity), so this gate and ``GET /databases/health`` cannot disagree. Only
    databases with ``required and build_mode in {download, pipeline}`` count;
    ``bundled``/``manual`` are exempt. Fails closed: if health cannot be
    determined the database is treated as not-ready, so a broken install never
    silently satisfies setup and routes the user to a non-functional dashboard.
    """
    settings = get_settings()
    try:
        engine = get_registry().reference_engine
    except Exception:
        logger.warning("readiness_engine_unavailable")
        return False, []

    readiness: list[DbReadiness] = []
    all_ready = True
    for db in get_all_databases():
        if not (db.required and db.build_mode in _GATE_BUILD_MODES):
            continue
        try:
            state = get_database_health(db, settings, engine).state
            ready = state == "ready"
        except Exception:
            logger.warning("readiness_health_failed", db_name=db.name)
            state, ready = "unknown", False
        readiness.append(
            DbReadiness(name=db.name, state=state, ready=ready, build_mode=db.build_mode)
        )
        all_ready = all_ready and ready
    return all_ready, readiness


# ── GET /api/setup/status ────────────────────────────────────────────


@router.get("/status", response_model=SetupStatusResponse)
async def setup_status() -> SetupStatusResponse:
    """Check the current setup status.

    Returns whether the app needs first-run setup, including
    disclaimer acceptance state, database availability, and sample presence.
    """
    settings = get_settings()
    disclaimer_accepted = _is_disclaimer_accepted()
    required_ready, db_readiness = _required_dbs_ready()
    has_samples = _has_any_samples()

    # Needs setup until the disclaimer is accepted AND every required,
    # downloadable reference database is integrity-``ready`` (health-verified,
    # not merely present). A present-but-empty/partial/corrupt file must NOT
    # satisfy setup — that is the hole that routed users to a broken dashboard.
    needs_setup = not disclaimer_accepted or not required_ready

    return SetupStatusResponse(
        needs_setup=needs_setup,
        disclaimer_accepted=disclaimer_accepted,
        has_databases=_has_any_databases(),
        required_dbs_ready=required_ready,
        db_readiness=db_readiness,
        has_samples=has_samples,
        data_dir=str(settings.data_dir),
    )


# ── GET /api/setup/disclaimer ────────────────────────────────────────


@router.get("/disclaimer", response_model=DisclaimerResponse)
async def get_disclaimer() -> DisclaimerResponse:
    """Get the global disclaimer text."""
    return DisclaimerResponse(
        title=GLOBAL_DISCLAIMER_TITLE,
        text=GLOBAL_DISCLAIMER_TEXT,
        accept_label=GLOBAL_DISCLAIMER_ACCEPT_LABEL,
    )


# ── POST /api/setup/accept-disclaimer ────────────────────────────────


@router.post("/accept-disclaimer", response_model=AcceptDisclaimerResponse)
async def accept_disclaimer() -> AcceptDisclaimerResponse:
    """Record that the user has accepted the global disclaimer.

    Creates a flag file in the data directory. This is checked on every
    app launch to determine whether to show the setup wizard.
    """
    settings = get_settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)

    flag_path = _disclaimer_flag_path()
    accepted_at = datetime.now(UTC).isoformat()

    # Atomic write: a crash mid-write must not leave a partial flag (and
    # _is_disclaimer_accepted now also parse-validates it).
    tmp_path = flag_path.parent / (flag_path.name + ".tmp")
    tmp_path.write_text(
        json.dumps({"accepted_at": accepted_at, "version": _DISCLAIMER_VERSION}),
        encoding="utf-8",
    )
    os.replace(tmp_path, flag_path)

    logger.info("global_disclaimer_accepted", accepted_at=accepted_at)

    return AcceptDisclaimerResponse(accepted=True, accepted_at=accepted_at)


# ── GET /api/setup/detect-existing ────────────────────────────────


@router.get("/detect-existing", response_model=DetectExistingResponse)
async def detect_existing() -> DetectExistingResponse:
    """Auto-detect an existing Yeliztli installation.

    Checks if ~/.yeliztli/ already has data (config.toml, samples, DBs).
    If config.toml exists but DBs are missing, the frontend should resume
    the wizard at the download step.
    """
    settings = get_settings()
    data_dir = settings.data_dir

    has_config = config_toml_path().exists()
    has_samples = _has_any_samples()
    has_dbs = _has_any_databases()
    existing_found = has_config or has_samples or has_dbs

    return DetectExistingResponse(
        existing_found=existing_found,
        has_config=has_config,
        has_samples=has_samples,
        has_databases=has_dbs,
        data_dir=str(data_dir),
    )


# ── POST /api/setup/import-backup ─────────────────────────────────

# Max upload size: 5 GB (sample DBs can be large)
_MAX_BACKUP_SIZE = 5 * 1024 * 1024 * 1024

# Allowed top-level entries in a valid backup archive
_ALLOWED_ARCHIVE_ENTRIES = {"config.toml", "samples", ".disclaimer_accepted"}


def _validate_tar_member(member: tarfile.TarInfo) -> bool:
    """Validate a tar member is safe to extract (no path traversal)."""
    # Reject absolute paths
    if member.name.startswith("/") or member.name.startswith(".."):
        return False
    # Reject path traversal
    if ".." in member.name.split("/"):
        return False
    # Reject symlinks and hardlinks
    if member.issym() or member.islnk():
        return False
    # Reject device files
    if member.isdev():
        return False
    return True


def _validate_archive_structure(tf: tarfile.TarFile) -> list[str]:
    """Validate archive has expected structure. Return list of issues."""
    issues: list[str] = []
    members = tf.getmembers()

    if not members:
        issues.append("Archive is empty")
        return issues

    has_samples = False
    for member in members:
        if not _validate_tar_member(member):
            issues.append(f"Unsafe entry: {member.name}")
            continue

        # Check top-level entry is allowed
        top_level = member.name.split("/")[0]
        if top_level not in _ALLOWED_ARCHIVE_ENTRIES:
            issues.append(f"Unexpected entry: {top_level}")

        if top_level == "samples":
            has_samples = True

    if not has_samples:
        issues.append("Archive does not contain a 'samples' directory")

    return issues


# ── Bundle-version gate (Plan §7.6, ADNA-00f) ────────────────────

# Per Plan §7.6 — backups predating Phase 0 lack the `annotation_state`
# table; treat their recorded bundle version as v1.0.0.
_FALLBACK_BACKUP_VERSION = "v1.0.0"


def _coerce_semver(raw: str | None) -> Version | None:
    """Parse a version string (with optional leading 'v') as semver."""
    if not raw:
        return None
    try:
        return Version(raw.lstrip("v"))
    except InvalidVersion:
        return None


def _read_installed_vep_bundle_version() -> str | None:
    """Return the raw ``database_versions['vep_bundle'].version`` string.

    Returns ``None`` when the reference DB or row is missing — a fresh
    install with no recorded bundle is allowed to restore.
    """
    settings = get_settings()
    ref_path = settings.reference_db_path
    if not ref_path.exists():
        return None
    try:
        from backend.db.tables import database_versions

        engine = sa.create_engine(f"sqlite:///{ref_path}")
        try:
            with engine.connect() as conn:
                row = conn.execute(
                    sa.select(database_versions.c.version).where(
                        database_versions.c.db_name == "vep_bundle"
                    )
                ).fetchone()
        finally:
            engine.dispose()
        return row.version if row else None
    except sa.exc.SQLAlchemyError:
        return None


def _read_sample_db_bundle_version(sample_db_path: Path) -> str:
    """Return a sample DB's recorded ``annotation_state.vep_bundle_version``.

    Falls back to ``v1.0.0`` (per Plan §7.6) when the DB is unreachable,
    the ``annotation_state`` table is absent (pre-Phase-0 backup), or the
    row is missing. Also tolerates non-SQLite blobs (legacy/test fixtures)
    — anything that fails to open returns the fallback.
    """
    if not sample_db_path.exists():
        return _FALLBACK_BACKUP_VERSION
    try:
        engine = sa.create_engine(f"sqlite:///{sample_db_path}")
        try:
            with engine.connect() as conn:
                row = conn.execute(
                    sa.text("SELECT value FROM annotation_state WHERE key = 'vep_bundle_version'")
                ).fetchone()
        finally:
            engine.dispose()
    except sa.exc.SQLAlchemyError:
        return _FALLBACK_BACKUP_VERSION
    return row[0] if row and row[0] else _FALLBACK_BACKUP_VERSION


def _inspect_archive_bundle_versions(
    tf: tarfile.TarFile, staging_dir: Path
) -> list[tuple[str, str]]:
    """Extract sample DBs to ``staging_dir`` and read each recorded version.

    Returns a list of ``(member_name, version_string)`` pairs. Used purely
    for the §7.6 pre-flight gate — extraction here is to an isolated
    temporary directory, not the data directory.
    """
    versions: list[tuple[str, str]] = []
    for member in tf.getmembers():
        if not _validate_tar_member(member) or not member.isfile():
            continue
        top_level = member.name.split("/")[0]
        if top_level != "samples" or not member.name.endswith(".db"):
            continue
        leaf = Path(member.name).name
        tmp_db = staging_dir / leaf
        src = tf.extractfile(member)
        if src is None:
            continue
        with tmp_db.open("wb") as out:
            shutil.copyfileobj(src, out)
        try:
            version = _read_sample_db_bundle_version(tmp_db)
        finally:
            tmp_db.unlink(missing_ok=True)
        versions.append((member.name, version))
    return versions


def _bundle_compatibility_payload(
    installed_raw: str | None, sample_versions: list[tuple[str, str]]
) -> dict[str, str] | None:
    """Return a 409 payload describing the mismatch, or ``None`` when OK.

    Major-version mismatch in either direction blocks the restore
    (Plan §7.6). When the installed bundle is missing/unparseable, the
    comparison is skipped — a fresh install can restore any backup.
    """
    installed = _coerce_semver(installed_raw)
    if installed is None:
        return None

    for member_name, version_raw in sample_versions:
        backup = _coerce_semver(version_raw)
        if backup is None:
            # Defensive fallback (Plan §7.6) — treat unparseable as v1.0.0.
            backup = _coerce_semver(_FALLBACK_BACKUP_VERSION)
            assert backup is not None
        if backup.major == installed.major:
            continue
        direction = (
            "backup_below_installed"
            if backup.major < installed.major
            else "backup_above_installed"
        )
        return {
            "error": "bundle_version_mismatch",
            "installed_version": installed_raw or "",
            "backup_version": version_raw,
            "direction": direction,
            "sample_member": member_name,
        }
    return None


def _upgrade_restored_sample_db(sample_db_path: Path) -> None:
    """Run the three-step idempotent post-restore upgrade on one sample DB.

    Per Plan §7.6:
      1. ``_add_missing_columns(engine, from_version)`` forward-migrates.
      2. ``sample_metadata_obj.create_all(engine, checkfirst=True)`` adds
         tables that pre-Phase-0 backups never had (e.g. ``annotation_state``).
      3. Reapplies migration 008 backfill semantics:
         ``INSERT OR IGNORE`` ``vep_bundle_version='v1.0.0'``.

    All three steps are idempotent; corrupt or non-SQLite blobs are
    logged and skipped without raising — defensive against legacy /
    test-fixture dummy files.
    """
    from backend.db.sample_schema import _add_missing_columns, _get_schema_version
    from backend.db.tables import sample_metadata_obj

    try:
        engine = sa.create_engine(f"sqlite:///{sample_db_path}")
        try:
            from_version = _get_schema_version(engine)
            _add_missing_columns(engine, from_version)
            sample_metadata_obj.create_all(engine, checkfirst=True)
            with engine.begin() as conn:
                conn.execute(
                    sa.text(
                        "INSERT OR IGNORE INTO annotation_state "
                        "(key, value) VALUES ('vep_bundle_version', :v)"
                    ),
                    {"v": _FALLBACK_BACKUP_VERSION},
                )
        finally:
            engine.dispose()
    except sa.exc.SQLAlchemyError as exc:
        logger.warning(
            "restore_sample_upgrade_skipped",
            sample_db=str(sample_db_path),
            error=str(exc),
        )


@router.post("/import-backup", response_model=ImportBackupResponse)
async def import_backup(file: UploadFile) -> ImportBackupResponse:
    """Import data from a .tar.gz backup archive.

    Accepts a .tar.gz file containing:
    - samples/ directory with sample_*.db files
    - config.toml (optional)
    - .disclaimer_accepted (optional)

    Extracts contents to the data directory. Reference DBs are NOT expected
    in the archive — they will be re-downloaded in a later wizard step.

    Plan §7.6: before any extraction to ``data_dir``, sample DBs are
    inspected in an isolated staging directory and their recorded
    ``annotation_state.vep_bundle_version`` is compared against the
    installed ``database_versions['vep_bundle'].version``. A major-version
    mismatch in either direction halts the restore with HTTP 409.
    """
    settings = get_settings()
    data_dir = settings.data_dir

    # Validate file type
    if not file.filename or not file.filename.endswith((".tar.gz", ".tgz")):
        raise HTTPException(
            status_code=400,
            detail="File must be a .tar.gz or .tgz archive.",
        )

    # Save uploaded file to temp location
    data_dir.mkdir(parents=True, exist_ok=True)
    tmp_archive = data_dir / ".import_backup_tmp.tar.gz"

    try:
        # Stream upload to disk to avoid memory issues
        total_written = 0
        with tmp_archive.open("wb") as f:
            while chunk := await file.read(64 * 1024):
                total_written += len(chunk)
                if total_written > _MAX_BACKUP_SIZE:
                    raise HTTPException(
                        status_code=413,
                        detail="Archive exceeds maximum size of 5 GB.",
                    )
                f.write(chunk)

        if total_written == 0:
            raise HTTPException(status_code=400, detail="Uploaded file is empty.")

        # Validate archive
        try:
            with tarfile.open(tmp_archive, "r:gz") as tf:
                issues = _validate_archive_structure(tf)
                if issues:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Invalid backup archive: {'; '.join(issues)}",
                    )

                # Pre-flight bundle-version gate (Plan §7.6). Sample DBs are
                # extracted to an isolated tempdir — nothing has been
                # written to data_dir yet.
                with tempfile.TemporaryDirectory(prefix="gi_restore_inspect_") as inspect_dir:
                    sample_versions = _inspect_archive_bundle_versions(tf, Path(inspect_dir))
                installed_raw = _read_installed_vep_bundle_version()
                mismatch = _bundle_compatibility_payload(installed_raw, sample_versions)
                if mismatch is not None:
                    logger.warning(
                        "restore_bundle_version_mismatch",
                        **mismatch,
                    )
                    raise HTTPException(status_code=409, detail=mismatch)

                # Extract + upgrade in a staging dir on the SAME filesystem as
                # data_dir, then move into place — so a mid-extraction or
                # mid-upgrade failure never leaves data_dir half-populated with
                # partial sample DBs. Nothing is visible under data_dir until the
                # whole archive has been extracted and upgraded.
                samples_restored = 0
                config_restored = False
                with tempfile.TemporaryDirectory(
                    dir=data_dir, prefix=".import_staging_"
                ) as staging:
                    staging_dir = Path(staging)
                    staged_samples: list[tuple[Path, Path]] = []  # (staged, final)
                    staged_config: Path | None = None
                    staged_disclaimer: Path | None = None

                    for member in tf.getmembers():
                        if not _validate_tar_member(member):
                            continue
                        top_level = member.name.split("/")[0]
                        if top_level not in _ALLOWED_ARCHIVE_ENTRIES:
                            continue
                        if member.isdir():
                            (staging_dir / member.name).mkdir(parents=True, exist_ok=True)
                            continue
                        if not member.isfile():
                            continue
                        staged = staging_dir / member.name
                        staged.parent.mkdir(parents=True, exist_ok=True)
                        src = tf.extractfile(member)
                        if src is None:
                            continue
                        with staged.open("wb") as out:
                            shutil.copyfileobj(src, out)
                        if top_level == "samples" and member.name.endswith(".db"):
                            staged_samples.append((staged, data_dir / member.name))
                        elif member.name == "config.toml":
                            staged_config = staged
                        elif member.name == ".disclaimer_accepted":
                            staged_disclaimer = staged

                    # Idempotent v7→v8 / annotation_state / bundle-version upgrade
                    # on each staged sample, before it becomes visible in data_dir.
                    for staged, _final in staged_samples:
                        _upgrade_restored_sample_db(staged)

                    # Commit. Move the staged samples into place as a SINGLE atomic
                    # directory rename — onto the (startup-created) empty samples
                    # dir or an absent one, so all samples appear together or not
                    # at all, never a half-populated set. Only a NON-empty samples
                    # dir (a rarer re-restore) raises ENOTEMPTY and falls back to a
                    # per-file merge; any other error propagates so the restore
                    # fails cleanly. Same filesystem (staging is under data_dir) →
                    # os.replace is atomic.
                    staged_samples_dir = staging_dir / "samples"
                    final_samples_dir = data_dir / "samples"
                    if staged_samples_dir.is_dir():
                        try:
                            os.replace(staged_samples_dir, final_samples_dir)
                        except OSError as exc:
                            if exc.errno != errno.ENOTEMPTY:
                                raise
                            for staged, final in staged_samples:
                                final.parent.mkdir(parents=True, exist_ok=True)
                                os.replace(staged, final)
                        samples_restored = len(staged_samples)
                    # config.toml goes to the home dir (config_toml_path), which may
                    # be a different filesystem than a relocated data_dir, so copy
                    # that single small file.
                    if staged_config is not None:
                        config_dest = config_toml_path()
                        config_dest.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copyfile(staged_config, config_dest)
                        config_restored = True
                    if staged_disclaimer is not None:
                        os.replace(staged_disclaimer, data_dir / ".disclaimer_accepted")

        except tarfile.TarError as exc:
            raise HTTPException(
                status_code=400,
                detail=f"Failed to read archive: {exc}",
            ) from exc

        logger.info(
            "backup_imported",
            samples_restored=samples_restored,
            config_restored=config_restored,
        )

        return ImportBackupResponse(
            success=True,
            samples_restored=samples_restored,
            config_restored=config_restored,
            message=f"Restored {samples_restored} sample(s)"
            + (" and configuration" if config_restored else "")
            + ".",
        )

    finally:
        # Clean up temp file
        if tmp_archive.exists():
            tmp_archive.unlink()


# ── P1-19c: Storage path + disk space check ──────────────────────

# Thresholds per PRD §2.18
_WARN_THRESHOLD_GB = 10
_BLOCK_THRESHOLD_GB = 5


def _get_disk_space(path: Path) -> tuple[int, int]:
    """Get free and total disk space for a path.

    Walks up the path tree until an existing ancestor is found,
    then uses shutil.disk_usage on that ancestor.

    Returns (free_bytes, total_bytes).
    """
    check_path = path
    while not check_path.exists():
        parent = check_path.parent
        if parent == check_path:
            break
        check_path = parent

    usage = shutil.disk_usage(check_path)
    return usage.free, usage.total


def _assess_disk_space(free_bytes: int) -> tuple[Literal["ok", "warning", "blocked"], str]:
    """Assess disk space and return (status, message)."""
    free_gb = free_bytes / (1024**3)
    if free_gb < _BLOCK_THRESHOLD_GB:
        return (
            "blocked",
            f"Insufficient disk space. Yeliztli requires at least "
            f"{_BLOCK_THRESHOLD_GB} GB free. Current: {free_gb:.1f} GB.",
        )
    if free_gb < _WARN_THRESHOLD_GB:
        return (
            "warning",
            f"Low disk space ({free_gb:.1f} GB free). Yeliztli reference "
            f"databases require ~4 GB, and sample data needs additional headroom. "
            f"Consider freeing space or choosing a different path.",
        )
    return "ok", f"{free_gb:.1f} GB free — sufficient for Yeliztli."


def _resolve_storage_path(raw_path: str) -> Path:
    """Resolve a user-provided storage path, expanding ~ and env vars."""
    return Path(raw_path).expanduser().resolve()


@router.get("/storage-info", response_model=StorageInfoResponse)
async def storage_info() -> StorageInfoResponse:
    """Get current storage path and disk space information.

    Returns the current data_dir, free/total disk space, and whether
    the space is sufficient (ok), low (warning), or insufficient (blocked).
    """
    settings = get_settings()
    data_dir = settings.data_dir

    free_bytes, total_bytes = _get_disk_space(data_dir)
    free_gb = free_bytes / (1024**3)
    total_gb = total_bytes / (1024**3)
    status, message = _assess_disk_space(free_bytes)

    path_exists = data_dir.exists()
    path_writable = False
    if path_exists:
        path_writable = _is_writable(data_dir)
    else:
        # Check if the parent is writable (for creating the directory)
        parent = data_dir.parent
        while not parent.exists():
            parent = parent.parent
        path_writable = parent.exists() and os.access(parent, os.W_OK)

    return StorageInfoResponse(
        data_dir=str(data_dir),
        free_space_bytes=free_bytes,
        free_space_gb=round(free_gb, 1),
        total_space_bytes=total_bytes,
        total_space_gb=round(total_gb, 1),
        status=status,
        message=message,
        path_exists=path_exists,
        path_writable=path_writable,
    )


@router.post("/set-storage-path", response_model=SetStoragePathResponse)
async def set_storage_path(body: SetStoragePathRequest) -> SetStoragePathResponse:
    """Validate and create the requested storage path.

    Validates the path, checks disk space, creates the directory structure, and
    persists the chosen path to the fixed-location ``data_dir`` pointer (NOT
    config.toml, which lives inside data_dir and so can't define its own
    location) so the effective data directory survives a restart. The settings
    cache is then cleared so the new path takes effect immediately.
    Does NOT block on low disk space — the frontend enforces the block threshold.
    """
    resolved = _resolve_storage_path(body.path)

    # Validate path is absolute after resolution
    if not resolved.is_absolute():
        raise HTTPException(
            status_code=400,
            detail="Storage path must be absolute.",
        )

    # Create directory structure
    try:
        resolved.mkdir(parents=True, exist_ok=True)
        (resolved / "samples").mkdir(exist_ok=True)
        (resolved / "downloads").mkdir(exist_ok=True)
        (resolved / "logs").mkdir(exist_ok=True)
    except PermissionError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot create directory at {resolved}: permission denied.",
        ) from exc
    except OSError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot create directory at {resolved}: {exc}",
        ) from exc

    # Verify writability
    if not _is_writable(resolved):
        raise HTTPException(
            status_code=400,
            detail=f"Directory at {resolved} is not writable.",
        )

    # Check disk space
    free_bytes, _ = _get_disk_space(resolved)
    free_gb = free_bytes / (1024**3)
    status, message = _assess_disk_space(free_bytes)

    # Persist the chosen path so it survives a restart, and bust the settings
    # cache so subsequent reads in this process use it immediately.
    write_data_dir_pointer(resolved)
    get_settings.cache_clear()

    logger.info(
        "storage_path_set",
        data_dir=str(resolved),
        free_gb=round(free_gb, 1),
        status=status,
    )

    return SetStoragePathResponse(
        success=True,
        data_dir=str(resolved),
        free_space_gb=round(free_gb, 1),
        status=status,
        message=message,
    )


def _read_config_toml(config_path: Path) -> dict[str, dict[str, object]]:
    """Read and parse config.toml, returning empty dict on missing or invalid file."""
    if not config_path.exists():
        return {}
    try:
        import tomllib

        return tomllib.loads(config_path.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, OSError) as exc:
        logger.warning(
            "config_toml_parse_failed",
            path=str(config_path),
            error=str(exc),
        )
        return {}


# ── P1-19e: External service credentials ─────────────────────────


class CredentialsResponse(BaseModel):
    """Current external service credentials."""

    pubmed_email: str
    ncbi_api_key: str
    omim_api_key: str


# Basic email shape — same contract the CredentialsStep UI enforces client-side.
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class SaveCredentialsRequest(BaseModel):
    """Request to save external service credentials."""

    pubmed_email: str
    ncbi_api_key: str = ""
    omim_api_key: str = ""

    @field_validator("pubmed_email", mode="after")
    @classmethod
    def _require_valid_email(cls, value: str) -> str:
        # NCBI Entrez TOS requires a contact email; reject empty/malformed
        # server-side too (422) so an empty pubmed_email can never be persisted.
        value = value.strip()
        if not _EMAIL_RE.match(value):
            raise ValueError("pubmed_email must be a valid email address")
        return value


class SaveCredentialsResponse(BaseModel):
    """Result of saving credentials."""

    success: bool
    message: str


@router.get("/credentials", response_model=CredentialsResponse)
async def get_credentials() -> CredentialsResponse:
    """Get current external service credentials from config.

    Note: The Settings model uses ``pubmed_api_key`` (matching NCBI Entrez naming),
    but the API exposes it as ``ncbi_api_key`` for clarity to end users.
    """
    settings = get_settings()
    return CredentialsResponse(
        pubmed_email=settings.pubmed_email,
        ncbi_api_key=settings.pubmed_api_key,
        omim_api_key=settings.omim_api_key,
    )


@router.post("/credentials", response_model=SaveCredentialsResponse)
async def save_credentials(body: SaveCredentialsRequest) -> SaveCredentialsResponse:
    """Save external service credentials to config.toml.

    PubMed email is required by NCBI Terms of Service for Entrez API usage.
    NCBI API key is optional but raises the rate limit from 3 to 10 req/sec.
    OMIM API key is optional — enables gene-phenotype enrichment beyond MONDO/HPO.
    """
    # The single config.toml the Settings read source loads (home dir); writing
    # to a relocated data_dir would never round-trip back. write_config_toml
    # creates the parent dir as needed.
    config_path = config_toml_path()

    # Read existing config and update credentials under the shared lock so a
    # concurrent theme/auth save can't clobber these keys (or vice versa).
    with config_write_lock:
        existing_content = _read_config_toml(config_path)
        section = read_config_section(existing_content)
        section["pubmed_email"] = body.pubmed_email
        # Config key is pubmed_api_key (matching Settings/Entrez naming);
        # API field is ncbi_api_key for end-user clarity.
        section["pubmed_api_key"] = body.ncbi_api_key
        section["omim_api_key"] = body.omim_api_key
        write_config_section(existing_content, section)
        write_config_toml(config_path, existing_content)

    # Bust the settings cache so the saved credentials take effect immediately in
    # this process (NCBI calls in the same run use the new email/key, not stale
    # empties), mirroring auth/preferences/storage-path.
    get_settings.cache_clear()

    logger.info(
        "credentials_saved",
        has_pubmed_email=bool(body.pubmed_email),
        has_ncbi_api_key=bool(body.ncbi_api_key),
        has_omim_api_key=bool(body.omim_api_key),
    )

    return SaveCredentialsResponse(
        success=True,
        message="Credentials saved successfully.",
    )

"""Database update manager (P4-16).

Checks for new versions of reference databases, downloads updates
(respecting bandwidth windows), records history, and generates
re-annotation prompts for affected samples.

Scheduler behaviour (§2.20):
- Always fires once on app startup regardless of config.
- ``update_check_interval``: "startup" | "daily" | "weekly".
- Per-database auto-update toggles (most default on;
  VEP bundle default off).
- ``update_download_window``: optional time window for >100 MB downloads.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from datetime import time as dt_time
from pathlib import Path
from typing import TYPE_CHECKING

import httpx
import sqlalchemy as sa
import structlog

from backend.annotation.cpic import check_cpic_update
from backend.annotation.dbnsfp import check_dbnsfp_update
from backend.annotation.dbsnp import check_dbsnp_update
from backend.annotation.gwas import check_gwas_update
from backend.annotation.mondo_hpo import check_mondo_hpo_update
from backend.db.tables import (
    annotated_variants,
    auto_update_settings,
    clinvar_variants,
    database_versions,
    reannotation_prompts,
    samples,
    update_history,
    watched_variants,
)

if TYPE_CHECKING:
    from sqlalchemy import Engine

    from backend.config import Settings
    from backend.db.connection import DBRegistry

logger = structlog.get_logger(__name__)

# ── Per-database update policy (§2.20) ────────────────────────────────

AUTO_UPDATE_DEFAULTS: dict[str, bool] = {
    "clinvar": True,
    "gwas_catalog": True,
    "gnomad": True,
    "dbnsfp": True,
    "dbsnp": True,
    "mondo_hpo": True,
    "vep_bundle": False,  # Manual updates only; release-asset bundle, not auto-pulled.
    "lai_bundle": True,
    "cpic": True,
    "encode_ccres": True,
    "ancestry_pca": True,
}

# Size threshold for bandwidth-window enforcement (100 MB)
BANDWIDTH_WINDOW_THRESHOLD = 100 * 1024 * 1024

# ── Data classes ──────────────────────────────────────────────────────


@dataclass
class VersionInfo:
    """Remote version information for a single database."""

    db_name: str
    latest_version: str
    download_url: str
    download_size_bytes: int
    release_date: str | None = None


@dataclass
class UpdateCheckResult:
    """Result of checking all databases for updates."""

    available: list[VersionInfo] = field(default_factory=list)
    up_to_date: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    checked_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass
class PreCheckResult:
    """Result of comparing updated reference data against a sample."""

    sample_id: int
    db_name: str
    candidate_count: int
    reclassified_variants: list[dict] = field(default_factory=list)
    watched_reclassified: list[dict] = field(default_factory=list)


@dataclass
class UpdateResult:
    """Result of a single database update operation."""

    db_name: str
    previous_version: str | None
    new_version: str
    variants_added: int = 0
    variants_reclassified: int = 0
    download_size_bytes: int = 0
    duration_seconds: int = 0


# ── Bandwidth window ─────────────────────────────────────────────────


def parse_time_window(window: str) -> tuple[dt_time, dt_time]:
    """Parse a time window string like '02:00-06:00' into (start, end) times."""
    parts = window.strip().split("-")
    if len(parts) != 2:
        raise ValueError(f"Invalid time window format: {window!r} (expected 'HH:MM-HH:MM')")
    start = dt_time.fromisoformat(parts[0].strip())
    end = dt_time.fromisoformat(parts[1].strip())
    return start, end


def should_download_now(
    download_size_bytes: int,
    window: str | None,
) -> bool:
    """Determine if a download should proceed now given the bandwidth window.

    Downloads under 100 MB always proceed. Larger downloads are gated
    by the optional time window configuration.
    """
    if download_size_bytes < BANDWIDTH_WINDOW_THRESHOLD:
        return True  # Small downloads always proceed
    if window is None:
        return True  # No window configured
    start, end = parse_time_window(window)
    now = datetime.now().time()
    if start <= end:
        return start <= now <= end
    # Window spans midnight (e.g. "22:00-06:00")
    return now >= start or now <= end


# ── Version checking ─────────────────────────────────────────────────


def get_current_version(engine: Engine, db_name: str) -> str | None:
    """Get the currently installed version of a database."""
    with engine.connect() as conn:
        row = conn.execute(
            sa.select(database_versions.c.version).where(database_versions.c.db_name == db_name)
        ).fetchone()
    return row.version if row else None


def get_all_version_stamps(engine: Engine) -> list[dict]:
    """Return all version stamps from the database_versions table.

    Each dict includes db_name, version, downloaded_at, file_size_bytes,
    and checksum_sha256.
    """
    with engine.connect() as conn:
        rows = conn.execute(
            sa.select(
                database_versions.c.db_name,
                database_versions.c.version,
                database_versions.c.downloaded_at,
                database_versions.c.file_size_bytes,
                database_versions.c.checksum_sha256,
            )
        ).fetchall()

    return [
        {
            "db_name": row.db_name,
            "version": row.version,
            "downloaded_at": row.downloaded_at.isoformat() if row.downloaded_at else None,
            "file_size_bytes": row.file_size_bytes,
            "checksum_sha256": row.checksum_sha256,
        }
        for row in rows
    ]


# Databases that use YYYYMMDD date-based versioning.
DATE_VERSIONED_DATABASES: set[str] = {"clinvar"}


def format_version_display(version: str | None, db_name: str) -> str | None:
    """Format a version string for display in the status bar.

    Date-versioned databases (YYYYMMDD) are formatted as "Mar 2026".
    Other versions are returned as-is.
    """
    if version is None:
        return None

    if db_name in DATE_VERSIONED_DATABASES and len(version) == 8 and version.isdigit():
        try:
            dt = datetime.strptime(version, "%Y%m%d")
            return dt.strftime("%b %Y")
        except ValueError:
            return version

    return version


def check_clinvar_update(
    reference_engine: Engine,
    settings: Settings | None = None,
    *,
    timeout: float = 30.0,
) -> VersionInfo | None:
    """Check if a newer ClinVar VCF is available from NCBI FTP.

    Uses HTTP HEAD to read the Last-Modified header from the ClinVar
    VCF endpoint without downloading the full file.

    ``settings`` is accepted for dispatch-signature parity with the other
    ``check_*_update`` callables registered in :data:`CHECK_FNS` and is unused.
    """
    del settings  # unused; kept for dispatch-signature parity
    from backend.annotation.clinvar import CLINVAR_VCF_URL

    current = get_current_version(reference_engine, "clinvar")

    try:
        with httpx.Client(
            follow_redirects=True,
            timeout=httpx.Timeout(timeout, connect=10.0),
        ) as client:
            resp = client.head(CLINVAR_VCF_URL)
            resp.raise_for_status()

        last_modified = resp.headers.get("Last-Modified", "")
        content_length = int(resp.headers.get("Content-Length", "0"))

        # Parse Last-Modified into a version string (YYYYMMDD)
        if last_modified:
            from email.utils import parsedate_to_datetime

            dt = parsedate_to_datetime(last_modified)
            remote_version = dt.strftime("%Y%m%d")
        else:
            remote_version = datetime.now(UTC).strftime("%Y%m%d")

        if current and current >= remote_version:
            return None  # Already up to date

        return VersionInfo(
            db_name="clinvar",
            latest_version=remote_version,
            download_url=CLINVAR_VCF_URL,
            download_size_bytes=content_length,
            release_date=remote_version,
        )
    except Exception as exc:
        logger.warning("clinvar_update_check_failed", error=str(exc))
        return None


def check_vep_bundle_update(
    reference_engine: Engine,
    settings: Settings | None = None,
    *,
    timeout: float = 30.0,
) -> VersionInfo | None:
    """Check if a newer VEP bundle is available on GitHub.

    Reads the ``bundle_metadata`` table from the local VEP bundle to get
    the current build_date, then queries the GitHub API for the latest
    commit date on ``bundles/vep_bundle.db`` in the main branch.
    """
    import sqlite3

    from backend.config import get_settings
    from backend.db.database_registry import BUNDLED_DIR, DATABASES

    if settings is None:
        settings = get_settings()

    db_info = DATABASES["vep_bundle"]

    # 1. Get local build date from the installed VEP bundle
    local_path = db_info.dest_path(settings)
    local_build_date: str | None = None

    if local_path.exists():
        try:
            with sqlite3.connect(str(local_path)) as conn:
                row = conn.execute(
                    "SELECT value FROM bundle_metadata WHERE key = 'build_date'"
                ).fetchone()
                if row:
                    local_build_date = row[0]
        except Exception:
            pass

    if local_build_date is None:
        # Also check bundled source
        bundled_src = BUNDLED_DIR / db_info.filename
        if bundled_src.exists():
            try:
                with sqlite3.connect(str(bundled_src)) as conn:
                    row = conn.execute(
                        "SELECT value FROM bundle_metadata WHERE key = 'build_date'"
                    ).fetchone()
                    if row:
                        local_build_date = row[0]
            except Exception:
                pass

    # 2. Check GitHub for the latest commit on the VEP bundle file
    try:
        with httpx.Client(
            follow_redirects=True,
            timeout=httpx.Timeout(timeout, connect=10.0),
        ) as client:
            resp = client.get(
                "https://api.github.com/repos/bioedca/Yeliztli/commits",
                params={"path": "bundles/vep_bundle.db", "per_page": "1"},
                headers={"Accept": "application/vnd.github.v3+json"},
            )
            resp.raise_for_status()

        commits = resp.json()
        if not commits:
            return None

        # Extract the commit date (YYYY-MM-DD)
        try:
            commit_date_str = commits[0]["commit"]["committer"]["date"][:10]
        except (KeyError, TypeError, IndexError) as exc:
            logger.warning("vep_bundle_commit_parse_failed", error=str(exc))
            return None

        # Compare dates — if remote commit is newer than local build, update available
        if local_build_date and commit_date_str <= local_build_date:
            return None  # Already up to date

        return VersionInfo(
            db_name="vep_bundle",
            latest_version=commit_date_str,
            download_url=db_info.url,
            download_size_bytes=db_info.expected_size_bytes,
            release_date=commit_date_str,
        )
    except Exception as exc:
        logger.warning("vep_bundle_update_check_failed", error=str(exc))
        return None


def run_vep_bundle_update(
    settings: Settings | None = None,
    *,
    timeout: float = 120.0,
) -> UpdateResult | None:
    """Download the latest VEP bundle from GitHub and replace the local copy.

    Also updates the bundled copy in the repo's ``bundles/`` directory.
    Manifest URL/sha256 override the registry default when reachable; on
    success a row is written to both ``database_versions`` and
    ``update_history`` so the Update Manager surfaces the bundle.

    Version stamp (Plan §5.5): when the manifest is reachable, its
    ``version`` field (semver — e.g. ``"v2.0.0"``) is the authoritative
    value written to ``database_versions``. The downloaded SQLite's own
    ``bundle_metadata.bundle_version`` is compared for parity: a mismatch
    logs ``vep_bundle_metadata_version_mismatch`` but does not fail the
    update (the manifest is the contract). Pre-v2.0.0 bundles omit
    ``bundle_version`` and the parity check is silently skipped.
    """
    import hashlib
    import shutil
    import sqlite3
    import tempfile

    from backend.config import get_settings
    from backend.db.database_registry import BUNDLED_DIR, DATABASES, _record_db_version
    from backend.db.manifest import get_bundle_info

    if settings is None:
        settings = get_settings()

    db_info = DATABASES["vep_bundle"]
    dest = db_info.dest_path(settings)
    start_time = time.monotonic()

    # Manifest is the authoritative source when reachable.
    manifest_entry = get_bundle_info("vep_bundle", timeout=timeout)
    download_url = manifest_entry.url if manifest_entry and manifest_entry.url else db_info.url
    expected_sha256 = manifest_entry.sha256 if manifest_entry else None

    # Get previous version (prefer the database_versions row; fall back to the
    # installed bundle's build_date so first-time updates still log a delta).
    reference_engine: Engine | None = None
    previous_version: str | None = None
    ref_path = settings.reference_db_path
    if ref_path.exists():
        try:
            reference_engine = sa.create_engine(f"sqlite:///{ref_path}")
            previous_version = get_current_version(reference_engine, "vep_bundle")
        except Exception as exc:
            logger.warning(
                "vep_bundle_reference_engine_failed",
                error=str(exc),
                reference_db=str(ref_path),
            )
            if reference_engine is not None:
                reference_engine.dispose()
                reference_engine = None

    if previous_version is None and dest.exists():
        try:
            with sqlite3.connect(str(dest)) as conn:
                row = conn.execute(
                    "SELECT value FROM bundle_metadata WHERE key = 'build_date'"
                ).fetchone()
                if row:
                    previous_version = row[0]
        except Exception:
            pass

    # Download to a temporary file first
    tmp_path: Path | None = None
    try:
        with httpx.Client(
            follow_redirects=True,
            timeout=httpx.Timeout(timeout, connect=15.0),
        ) as client:
            resp = client.get(download_url)
            resp.raise_for_status()

        dest.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            dir=str(dest.parent), suffix=".db.tmp", delete=False
        ) as tmp:
            tmp.write(resp.content)
            tmp_path = Path(tmp.name)

        if expected_sha256:
            digest = hashlib.sha256(tmp_path.read_bytes()).hexdigest()
            if digest != expected_sha256:
                logger.error(
                    "vep_bundle_checksum_mismatch",
                    expected=expected_sha256,
                    actual=digest,
                )
                tmp_path.unlink(missing_ok=True)
                return None

        # Verify it's a valid SQLite with bundle_metadata. The build_date row
        # acts as the structural sentinel (Plan §5.5: the manifest's semver is
        # the authoritative version we record; build_date stays for
        # human-readable display and validates that the downloaded file is a
        # real bundle).
        new_build_date: str | None = None
        metadata_bundle_version: str | None = None
        try:
            with sqlite3.connect(str(tmp_path)) as conn:
                row = conn.execute(
                    "SELECT value FROM bundle_metadata WHERE key = 'build_date'"
                ).fetchone()
                if row:
                    new_build_date = row[0]
                meta_row = conn.execute(
                    "SELECT value FROM bundle_metadata WHERE key = 'bundle_version'"
                ).fetchone()
                if meta_row:
                    metadata_bundle_version = meta_row[0]
        finally:
            if new_build_date is None:
                tmp_path.unlink(missing_ok=True)

        if new_build_date is None:
            logger.error("vep_bundle_update_invalid", error="No build_date in metadata")
            return None

        # Manifest's `version` is the authoritative semver (Plan §5.5). Falls
        # back to the bundle's own ``build_date`` when the manifest is
        # unreachable so the function still records a version stamp.
        manifest_version = manifest_entry.version if manifest_entry else None
        new_version = manifest_version if manifest_version else new_build_date

        # Advisory parity check: pre-v2.0.0 bundles omit ``bundle_version`` from
        # ``bundle_metadata`` — tolerate ``None`` silently. When present, a
        # mismatch against the manifest logs a structured warning but never
        # fails the update (the manifest is the contract).
        #
        # G1: this warning is *expected* after the vep_bundle re-annotation
        # version bump — the manifest leads at v3.0.0 while the unchanged
        # bundle-v2.0.0 asset still self-reports ``v2.0.0`` (see
        # bundles/manifest.json's vep_bundle ``comment``). It is advisory only.
        if (
            manifest_version is not None
            and metadata_bundle_version is not None
            and metadata_bundle_version != manifest_version
        ):
            logger.warning(
                "vep_bundle_metadata_version_mismatch",
                manifest_version=manifest_version,
                metadata_bundle_version=metadata_bundle_version,
                build_date=new_build_date,
            )

        # Replace the installed copy
        shutil.move(str(tmp_path), str(dest))
        tmp_path = None  # move consumed it

        # Also update the bundled copy in the repo
        bundled_dest = BUNDLED_DIR / db_info.filename
        if BUNDLED_DIR.exists():
            try:
                shutil.copy2(str(dest), str(bundled_dest))
            except OSError as copy_err:
                logger.warning(
                    "bundled_copy_failed",
                    dest=str(bundled_dest),
                    error=str(copy_err),
                )

        duration = int(time.monotonic() - start_time)
        download_size = dest.stat().st_size

        # Persist version + history rows (best-effort: missing reference.db
        # leaves the update result intact but unrecorded).
        if reference_engine is None and ref_path.exists():
            try:
                reference_engine = sa.create_engine(f"sqlite:///{ref_path}")
            except Exception as exc:
                logger.warning(
                    "vep_bundle_reference_engine_failed",
                    error=str(exc),
                    reference_db=str(ref_path),
                )

        if reference_engine is not None:
            try:
                _record_db_version(
                    reference_engine,
                    db_name="vep_bundle",
                    version=new_version,
                    file_size_bytes=download_size,
                    sha256=expected_sha256,
                )
                _record_update_history(
                    reference_engine,
                    db_name="vep_bundle",
                    previous_version=previous_version,
                    new_version=new_version,
                    download_size_bytes=download_size,
                    duration_seconds=duration,
                )
            except Exception as exc:
                logger.warning("vep_bundle_record_failed", error=str(exc))

        logger.info(
            "vep_bundle_update_complete",
            previous_version=previous_version,
            new_version=new_version,
            size_bytes=download_size,
            duration_seconds=duration,
        )

        return UpdateResult(
            db_name="vep_bundle",
            previous_version=previous_version,
            new_version=new_version,
            download_size_bytes=download_size,
            duration_seconds=duration,
        )

    except Exception as exc:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)
        logger.exception("vep_bundle_update_failed", error=str(exc))
        return None
    finally:
        if reference_engine is not None:
            reference_engine.dispose()


# ── Bundle update functions (manifest + DownloadManager) ─────────────


def _run_bundle_download(
    settings: Settings,
    engine: Engine,
    *,
    url: str,
    filename: str,
    expected_sha256: str | None,
    total_timeout: float,
) -> tuple[Path, int] | None:
    """Download a bundle via :class:`DownloadManager` and return ``(path, bytes)``.

    Returns ``None`` on download failure or checksum mismatch. The file lives
    in ``settings.downloads_dir`` after a successful return — callers are
    responsible for moving / extracting it into ``settings.data_dir``.
    """
    from backend.db.download_manager import ChecksumMismatchError, DownloadManager

    dm = DownloadManager(engine, settings.downloads_dir)
    try:
        result = dm.start(
            url=url,
            filename=filename,
            expected_sha256=expected_sha256,
            total_timeout=total_timeout,
        )
    except ChecksumMismatchError as exc:
        logger.error("bundle_download_checksum_mismatch", filename=filename, error=str(exc))
        return None

    if result.error:
        logger.error("bundle_download_failed", filename=filename, error=result.error)
        return None

    return result.dest_path, result.total_bytes


def run_lai_bundle_update(
    settings: Settings | None = None,
    *,
    timeout: float = 3600.0,
) -> UpdateResult | None:
    """Download and extract the latest LAI bundle via the manifest.

    Uses :class:`DownloadManager` for resumable + SHA-256-verified delivery,
    then reuses :func:`_extract_lai_bundle` to unpack the tarball and write
    the ``database_versions`` row. An ``update_history`` row is added after
    extraction so the Update Manager history log mirrors the pipeline DBs.

    Returns ``None`` when the manifest is unreachable / missing the entry,
    or the download / extraction fails.
    """
    from backend.config import get_settings
    from backend.db.database_registry import (
        DATABASES,
        _extract_lai_bundle,
        _record_db_version,
    )
    from backend.db.manifest import get_bundle_info

    if settings is None:
        settings = get_settings()

    db_info = DATABASES["lai_bundle"]
    entry = get_bundle_info("lai_bundle", timeout=min(timeout, 30.0))
    if entry is None or not entry.url:
        logger.warning("lai_bundle_update_skipped_no_manifest")
        return None

    ref_path = settings.reference_db_path
    if not ref_path.exists():
        logger.warning("lai_bundle_update_skipped_no_reference_db", path=str(ref_path))
        return None

    engine = sa.create_engine(f"sqlite:///{ref_path}")
    start_time = time.monotonic()

    try:
        previous_version = get_current_version(engine, "lai_bundle")

        download = _run_bundle_download(
            settings,
            engine,
            url=entry.url,
            filename=db_info.filename,
            expected_sha256=entry.sha256,
            total_timeout=timeout,
        )
        if download is None:
            return None
        downloaded_path, downloaded_bytes = download

        try:
            _extract_lai_bundle(downloaded_path, settings.data_dir / db_info.filename)
        except Exception as exc:
            downloaded_path.unlink(missing_ok=True)
            logger.exception("lai_bundle_update_extract_failed", error=str(exc))
            return None

        bundle_dir = settings.data_dir / "lai_bundle"
        extracted_size = sum(p.stat().st_size for p in bundle_dir.rglob("*") if p.is_file())

        # _extract_lai_bundle already wrote a database_versions row using whatever
        # the manifest fetch returned at extraction time. Re-record with the
        # values we used for the download so a stale or partial first write is
        # corrected (idempotent upsert).
        _record_db_version(
            engine,
            db_name="lai_bundle",
            version=entry.version,
            file_size_bytes=extracted_size,
            sha256=entry.sha256,
        )

        duration = int(time.monotonic() - start_time)

        _record_update_history(
            engine,
            db_name="lai_bundle",
            previous_version=previous_version,
            new_version=entry.version,
            download_size_bytes=downloaded_bytes,
            duration_seconds=duration,
        )

        logger.info(
            "lai_bundle_update_complete",
            previous_version=previous_version,
            new_version=entry.version,
            download_size_bytes=downloaded_bytes,
            extracted_size_bytes=extracted_size,
            duration_seconds=duration,
        )

        return UpdateResult(
            db_name="lai_bundle",
            previous_version=previous_version,
            new_version=entry.version,
            download_size_bytes=downloaded_bytes,
            duration_seconds=duration,
        )
    finally:
        engine.dispose()


def run_ancestry_pca_bundle_update(
    settings: Settings | None = None,
    *,
    timeout: float = 600.0,
) -> UpdateResult | None:
    """Download the latest ancestry-PCA bundle via the manifest.

    The PCA bundle ships in-repo for first-launch installs but updates flow
    through the manifest. When the manifest entry has no ``url`` (the
    out-of-band repo case), this function returns ``None`` without raising —
    the scheduler will simply skip it until a hosted release exists.
    """
    import shutil

    from backend.config import get_settings
    from backend.db.database_registry import DATABASES, _record_db_version
    from backend.db.manifest import get_bundle_info

    if settings is None:
        settings = get_settings()

    db_info = DATABASES["ancestry_pca"]
    entry = get_bundle_info("ancestry_pca", timeout=min(timeout, 30.0))
    if entry is None:
        logger.warning("ancestry_pca_update_skipped_no_manifest")
        return None
    if not entry.url:
        logger.info("ancestry_pca_update_skipped_no_url", version=entry.version)
        return None

    ref_path = settings.reference_db_path
    if not ref_path.exists():
        logger.warning("ancestry_pca_update_skipped_no_reference_db", path=str(ref_path))
        return None

    engine = sa.create_engine(f"sqlite:///{ref_path}")
    start_time = time.monotonic()

    try:
        previous_version = get_current_version(engine, "ancestry_pca")

        download = _run_bundle_download(
            settings,
            engine,
            url=entry.url,
            filename=db_info.filename,
            expected_sha256=entry.sha256,
            total_timeout=timeout,
        )
        if download is None:
            return None
        downloaded_path, downloaded_bytes = download

        final_dest = db_info.dest_path(settings)
        final_dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.move(str(downloaded_path), str(final_dest))
        except OSError as exc:
            downloaded_path.unlink(missing_ok=True)
            logger.exception("ancestry_pca_move_failed", error=str(exc))
            return None

        file_size = final_dest.stat().st_size
        duration = int(time.monotonic() - start_time)

        _record_db_version(
            engine,
            db_name="ancestry_pca",
            version=entry.version,
            file_size_bytes=file_size,
            sha256=entry.sha256,
        )
        _record_update_history(
            engine,
            db_name="ancestry_pca",
            previous_version=previous_version,
            new_version=entry.version,
            download_size_bytes=downloaded_bytes,
            duration_seconds=duration,
        )

        logger.info(
            "ancestry_pca_update_complete",
            previous_version=previous_version,
            new_version=entry.version,
            download_size_bytes=downloaded_bytes,
            duration_seconds=duration,
        )

        return UpdateResult(
            db_name="ancestry_pca",
            previous_version=previous_version,
            new_version=entry.version,
            download_size_bytes=downloaded_bytes,
            duration_seconds=duration,
        )
    finally:
        engine.dispose()


def run_gnomad_bundle_update(
    settings: Settings | None = None,
    *,
    timeout: float = 3600.0,
) -> UpdateResult | None:
    """Download the latest prebuilt gnomAD bundle (gnomad_af.db) via the manifest.

    Mirrors :func:`run_ancestry_pca_bundle_update`: a single SQLite file, no
    extract. Downloads to ``downloads_dir`` (SHA-256 verified by
    :class:`DownloadManager`), moves it into ``data_dir/gnomad_af.db``
    (standalone), then records the version + history rows in ``reference.db``.
    Returns ``None`` (no raise) when the manifest entry is missing or carries
    no URL — e.g. the pre-publish deferred state, where ``bundles["gnomad"]``
    is intentionally absent — so the scheduler simply skips until a hosted
    release exists.
    """
    import shutil

    from backend.config import get_settings
    from backend.db.database_registry import DATABASES, _record_db_version
    from backend.db.manifest import get_bundle_info

    if settings is None:
        settings = get_settings()

    db_info = DATABASES["gnomad"]
    entry = get_bundle_info("gnomad", timeout=min(timeout, 30.0))
    if entry is None:
        logger.warning("gnomad_update_skipped_no_manifest")
        return None
    if not entry.url:
        logger.info("gnomad_update_skipped_no_url", version=entry.version)
        return None

    ref_path = settings.reference_db_path
    if not ref_path.exists():
        logger.warning("gnomad_update_skipped_no_reference_db", path=str(ref_path))
        return None

    engine = sa.create_engine(f"sqlite:///{ref_path}")
    start_time = time.monotonic()

    try:
        previous_version = get_current_version(engine, "gnomad")

        download = _run_bundle_download(
            settings,
            engine,
            url=entry.url,
            filename=db_info.filename,  # "gnomad_af.db"
            expected_sha256=entry.sha256,
            total_timeout=timeout,  # large file — default 1h
        )
        if download is None:
            return None
        downloaded_path, downloaded_bytes = download

        final_dest = db_info.dest_path(settings)  # data_dir/gnomad_af.db
        final_dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.move(str(downloaded_path), str(final_dest))
        except OSError as exc:
            downloaded_path.unlink(missing_ok=True)
            logger.exception("gnomad_move_failed", error=str(exc))
            return None

        file_size = final_dest.stat().st_size
        duration = int(time.monotonic() - start_time)

        _record_db_version(
            engine,
            db_name="gnomad",
            version=entry.version,
            file_size_bytes=file_size,
            sha256=entry.sha256,
        )
        _record_update_history(
            engine,
            db_name="gnomad",
            previous_version=previous_version,
            new_version=entry.version,
            download_size_bytes=downloaded_bytes,
            duration_seconds=duration,
        )

        logger.info(
            "gnomad_update_complete",
            previous_version=previous_version,
            new_version=entry.version,
            download_size_bytes=downloaded_bytes,
            duration_seconds=duration,
        )

        return UpdateResult(
            db_name="gnomad",
            previous_version=previous_version,
            new_version=entry.version,
            download_size_bytes=downloaded_bytes,
            duration_seconds=duration,
        )
    finally:
        engine.dispose()


def _check_manifest_bundle_update(
    reference_engine: Engine,
    db_name: str,
    *,
    timeout: float = 30.0,
) -> VersionInfo | None:
    """Generic manifest-driven bundle update check.

    Returns a :class:`VersionInfo` when the manifest version differs from the
    one recorded in ``database_versions`` (including the "no row" case so a
    freshly-installed bundle without a recorded version is offered an update).
    Returns ``None`` when the manifest entry is missing/unreachable, the
    versions match, or the manifest carries no download URL.

    The no-URL guard matters for out-of-band bundles such as ``ancestry_pca``
    (shipped in-repo, ``url=""``): there is nothing to download, so surfacing
    an "update available" the user can never satisfy — and that the trigger
    endpoint rejects — is a dead end. Skip it.
    """
    from backend.db.manifest import get_bundle_info

    entry = get_bundle_info(db_name, timeout=timeout)
    if entry is None or not entry.url:
        return None

    current = get_current_version(reference_engine, db_name)
    if current is not None and current == entry.version:
        return None

    return VersionInfo(
        db_name=db_name,
        latest_version=entry.version,
        download_url=entry.url,
        download_size_bytes=entry.size_bytes,
        release_date=entry.build_date,
    )


def check_lai_bundle_update(
    reference_engine: Engine,
    settings: Settings | None = None,
    *,
    timeout: float = 30.0,
) -> VersionInfo | None:
    """Check whether the LAI bundle in the manifest is newer than the installed copy.

    ``settings`` is accepted for dispatch-signature parity with
    :func:`check_vep_bundle_update` but is unused — the manifest is the
    authoritative source for the bundle's version.
    """
    del settings  # unused; kept for signature parity
    return _check_manifest_bundle_update(reference_engine, "lai_bundle", timeout=timeout)


def check_ancestry_pca_update(
    reference_engine: Engine,
    settings: Settings | None = None,
    *,
    timeout: float = 30.0,
) -> VersionInfo | None:
    """Check whether the ancestry-PCA bundle in the manifest is newer than the installed copy."""
    del settings  # unused; kept for signature parity
    return _check_manifest_bundle_update(reference_engine, "ancestry_pca", timeout=timeout)


def check_gnomad_bundle_update(
    reference_engine: Engine,
    settings: Settings | None = None,
    *,
    timeout: float = 30.0,
) -> VersionInfo | None:
    """Check whether the gnomAD bundle in the manifest is newer than the installed copy.

    gnomAD ships as a prebuilt SQLite bundle (``bundles["gnomad"]``). This is
    the registered CHECK_FN for gnomAD, replacing the former pipeline-pin
    check. Until the asset is published the bundle entry is absent, so this
    returns ``None`` (no spurious update offer).
    """
    del settings  # unused; kept for signature parity
    return _check_manifest_bundle_update(reference_engine, "gnomad", timeout=timeout)


# ── Check-function dispatch ──────────────────────────────────────────
# Maps db_name → callable that returns ``VersionInfo | None``.
#
# Each callable accepts ``(reference_engine, settings=None, *, timeout=...)``
# (with ``settings`` ignored when not needed) so the scheduler can dispatch
# uniformly. Pipeline-DB entries are added in Steps 19–24.

CHECK_FNS: dict[str, object] = {
    "clinvar": check_clinvar_update,
    "vep_bundle": check_vep_bundle_update,
    "lai_bundle": check_lai_bundle_update,
    "ancestry_pca": check_ancestry_pca_update,
    "gnomad": check_gnomad_bundle_update,
    "dbnsfp": check_dbnsfp_update,
    "cpic": check_cpic_update,
    "gwas_catalog": check_gwas_update,
    "dbsnp": check_dbsnp_update,
    "mondo_hpo": check_mondo_hpo_update,
}


def check_all_updates(
    reference_engine: Engine,
    *,
    timeout: float = 30.0,
    settings: Settings | None = None,
) -> UpdateCheckResult:
    """Check every database in :data:`CHECK_FNS` for available updates.

    Each registered callable is invoked uniformly as
    ``fn(reference_engine, settings, timeout=timeout)``. A returned
    :class:`VersionInfo` lands in :attr:`UpdateCheckResult.available`; a
    ``None`` return lands in :attr:`UpdateCheckResult.up_to_date`. Any
    exception raised by a check function is caught, logged, and recorded as
    ``"<db_name>: <message>"`` in :attr:`UpdateCheckResult.errors` so a single
    failing check does not abort the sweep.
    """
    result = UpdateCheckResult()

    for db_name, check_fn in CHECK_FNS.items():
        try:
            info = check_fn(reference_engine, settings, timeout=timeout)
        except Exception as exc:
            logger.warning(
                "update_check_failed",
                db_name=db_name,
                error=str(exc),
            )
            result.errors.append(f"{db_name}: {exc}")
            continue

        if info is None:
            result.up_to_date.append(db_name)
        else:
            result.available.append(info)

    return result


# ── ClinVar differential update ──────────────────────────────────────


def run_clinvar_update(
    registry: DBRegistry,
    *,
    timeout: float = 300.0,
) -> UpdateResult:
    """Download and reload ClinVar, then run pre-checks on all samples.

    This is a full re-download (the ClinVar VCF is ~30 MB compressed).
    "Differential" refers to the fact that we detect which variants
    changed significance between the old and new data.
    """
    from backend.annotation.clinvar import (
        CLINVAR_VCF_URL,
        download_clinvar_vcf,
        iter_clinvar_vcf,
        load_clinvar_from_iter,
    )

    engine = registry.reference_engine
    settings = registry.settings
    start_time = time.monotonic()

    # 1. Record previous version
    previous_version = get_current_version(engine, "clinvar")

    # 2. Snapshot old ClinVar significances for reclassification detection
    old_significances: dict[str, str | None] = {}
    with engine.connect() as conn:
        rows = conn.execute(
            sa.select(clinvar_variants.c.rsid, clinvar_variants.c.significance)
        ).fetchall()
        for row in rows:
            old_significances[row.rsid] = row.significance

    # 3. Download new ClinVar VCF
    dest_dir = settings.downloads_dir
    dest_dir.mkdir(parents=True, exist_ok=True)
    vcf_path = download_clinvar_vcf(dest_dir, url=CLINVAR_VCF_URL, timeout=timeout)

    # 4. Stream-load into reference.db (replaces existing data)
    row_iter = iter_clinvar_vcf(vcf_path)
    load_stats = load_clinvar_from_iter(row_iter, engine, clear_existing=True)

    # 5. Compute reclassification stats
    new_significances: dict[str, str | None] = {}
    variants_added = 0
    variants_reclassified = 0
    with engine.connect() as conn:
        rows = conn.execute(
            sa.select(clinvar_variants.c.rsid, clinvar_variants.c.significance)
        ).fetchall()
        for row in rows:
            new_significances[row.rsid] = row.significance
            if row.rsid not in old_significances:
                variants_added += 1
            elif old_significances[row.rsid] != row.significance:
                variants_reclassified += 1

    # 6. Record new version
    new_version = load_stats.file_date or datetime.now(UTC).strftime("%Y%m%d")
    download_size = vcf_path.stat().st_size if vcf_path.exists() else 0
    duration = int(time.monotonic() - start_time)

    _record_version(engine, "clinvar", new_version, download_size)

    # 7. Write update_history row
    _record_update_history(
        engine,
        db_name="clinvar",
        previous_version=previous_version,
        new_version=new_version,
        variants_added=variants_added,
        variants_reclassified=variants_reclassified,
        download_size_bytes=download_size,
        duration_seconds=duration,
    )

    # 8. Run pre-check on all samples
    pre_check_results = run_precheck_all_samples(
        registry,
        db_name="clinvar",
        db_version=new_version,
        old_significances=old_significances,
        new_significances=new_significances,
    )

    logger.info(
        "clinvar_update_complete",
        previous_version=previous_version,
        new_version=new_version,
        variants_added=variants_added,
        variants_reclassified=variants_reclassified,
        affected_samples=len(pre_check_results),
        duration_seconds=duration,
    )

    return UpdateResult(
        db_name="clinvar",
        previous_version=previous_version,
        new_version=new_version,
        variants_added=variants_added,
        variants_reclassified=variants_reclassified,
        download_size_bytes=download_size,
        duration_seconds=duration,
    )


# ── Re-annotation pre-check ──────────────────────────────────────────


def run_precheck_single_sample(
    sample_engine: Engine,
    reference_engine: Engine,
    *,
    sample_id: int,
    db_name: str,
    old_significances: dict[str, str | None] | None = None,
    new_significances: dict[str, str | None] | None = None,
) -> PreCheckResult:
    """Compare a sample's annotations against updated reference data.

    For ClinVar: finds variants where significance changed. If
    old/new significance dicts are provided, uses them directly.
    Otherwise queries the current reference and sample DBs.
    """
    result = PreCheckResult(
        sample_id=sample_id,
        db_name=db_name,
        candidate_count=0,
    )

    if db_name == "clinvar":
        result = _precheck_clinvar(
            sample_engine,
            reference_engine,
            sample_id=sample_id,
            old_significances=old_significances,
            new_significances=new_significances,
        )

    return result


def _precheck_clinvar(
    sample_engine: Engine,
    reference_engine: Engine,
    *,
    sample_id: int,
    old_significances: dict[str, str | None] | None = None,
    new_significances: dict[str, str | None] | None = None,
) -> PreCheckResult:
    """ClinVar-specific pre-check: detect significance changes."""
    result = PreCheckResult(
        sample_id=sample_id,
        db_name="clinvar",
        candidate_count=0,
    )

    # Get sample's annotated variants that have ClinVar data
    with sample_engine.connect() as conn:
        sample_rows = conn.execute(
            sa.select(
                annotated_variants.c.rsid,
                annotated_variants.c.gene_symbol,
                annotated_variants.c.clinvar_significance,
            ).where(annotated_variants.c.clinvar_significance.isnot(None))
        ).fetchall()

    if not sample_rows:
        return result

    # If we have precomputed significance dicts, use them
    if old_significances is not None and new_significances is not None:
        for row in sample_rows:
            old_sig = old_significances.get(row.rsid)
            new_sig = new_significances.get(row.rsid)
            if old_sig is not None and new_sig is not None and old_sig != new_sig:
                result.reclassified_variants.append(
                    {
                        "rsid": row.rsid,
                        "gene_symbol": row.gene_symbol,
                        "old_significance": old_sig,
                        "new_significance": new_sig,
                    }
                )
    else:
        # Query reference DB directly for current significances
        sample_rsids = [r.rsid for r in sample_rows]
        current_sigs: dict[str, str | None] = {}
        with reference_engine.connect() as conn:
            for i in range(0, len(sample_rsids), 500):
                batch = sample_rsids[i : i + 500]
                rows = conn.execute(
                    sa.select(
                        clinvar_variants.c.rsid,
                        clinvar_variants.c.significance,
                    ).where(clinvar_variants.c.rsid.in_(batch))
                ).fetchall()
                for r in rows:
                    current_sigs[r.rsid] = r.significance

        for row in sample_rows:
            new_sig = current_sigs.get(row.rsid)
            if row.clinvar_significance != new_sig and new_sig is not None:
                result.reclassified_variants.append(
                    {
                        "rsid": row.rsid,
                        "gene_symbol": row.gene_symbol,
                        "old_significance": row.clinvar_significance,
                        "new_significance": new_sig,
                    }
                )

    result.candidate_count = len(result.reclassified_variants)

    # Check watched variants for reclassification (P4-21i)
    try:
        with sample_engine.connect() as conn:
            watched_rows = conn.execute(
                sa.select(
                    watched_variants.c.rsid,
                    watched_variants.c.clinvar_significance_at_watch,
                )
            ).fetchall()

        if watched_rows:
            # Build a lookup for current ClinVar significances
            if new_significances is not None:
                sig_lookup = new_significances
            else:
                # Fallback: query reference DB for watched variant rsids
                watched_rsids = [wr.rsid for wr in watched_rows]
                sig_lookup = {}
                with reference_engine.connect() as conn:
                    for i in range(0, len(watched_rsids), 500):
                        batch = watched_rsids[i : i + 500]
                        rows = conn.execute(
                            sa.select(
                                clinvar_variants.c.rsid,
                                clinvar_variants.c.significance,
                            ).where(clinvar_variants.c.rsid.in_(batch))
                        ).fetchall()
                        for r in rows:
                            sig_lookup[r.rsid] = r.significance

            # Build gene symbol lookup from reclassified list and sample rows
            gene_symbols: dict[str, str | None] = {}
            for rv in result.reclassified_variants:
                gene_symbols[rv["rsid"]] = rv.get("gene_symbol")
            for sr in sample_rows:
                if sr.rsid not in gene_symbols:
                    gene_symbols[sr.rsid] = sr.gene_symbol

            for wr in watched_rows:
                new_sig = sig_lookup.get(wr.rsid)
                if (
                    new_sig is not None
                    and wr.clinvar_significance_at_watch is not None
                    and new_sig != wr.clinvar_significance_at_watch
                ):
                    result.watched_reclassified.append(
                        {
                            "rsid": wr.rsid,
                            "gene_symbol": gene_symbols.get(wr.rsid),
                            "old_significance": wr.clinvar_significance_at_watch,
                            "new_significance": new_sig,
                        }
                    )
    except sa.exc.OperationalError:
        # watched_variants table may not exist in older sample DBs
        logger.debug("watched_variants_check_skipped", sample_id=sample_id)

    return result


def run_precheck_all_samples(
    registry: DBRegistry,
    *,
    db_name: str,
    db_version: str,
    old_significances: dict[str, str | None] | None = None,
    new_significances: dict[str, str | None] | None = None,
) -> list[PreCheckResult]:
    """Run pre-check across all samples and create re-annotation prompts."""
    engine = registry.reference_engine
    results: list[PreCheckResult] = []

    # Get all samples
    with engine.connect() as conn:
        sample_rows = conn.execute(
            sa.select(samples.c.id, samples.c.name, samples.c.db_path)
        ).fetchall()

    for sample_row in sample_rows:
        sample_db_path = registry.settings.data_dir / sample_row.db_path
        if not sample_db_path.exists():
            continue

        try:
            sample_engine = registry.get_sample_engine(sample_db_path)
            pre_check = run_precheck_single_sample(
                sample_engine,
                engine,
                sample_id=sample_row.id,
                db_name=db_name,
                old_significances=old_significances,
                new_significances=new_significances,
            )

            if pre_check.candidate_count > 0 or pre_check.watched_reclassified:
                results.append(pre_check)
                _create_reannotation_prompt(
                    engine,
                    sample_id=sample_row.id,
                    db_name=db_name,
                    db_version=db_version,
                    candidate_count=pre_check.candidate_count,
                    watched_count=len(pre_check.watched_reclassified),
                    watched_details=pre_check.watched_reclassified,
                )
        except Exception as exc:
            logger.warning(
                "precheck_sample_failed",
                sample_id=sample_row.id,
                error=str(exc),
            )

    return results


# ── Re-annotation prompt management ──────────────────────────────────


def _create_reannotation_prompt(
    engine: Engine,
    *,
    sample_id: int,
    db_name: str,
    db_version: str,
    candidate_count: int,
    watched_count: int = 0,
    watched_details: list[dict] | None = None,
) -> None:
    """Create or update a re-annotation prompt for a sample.

    When watched variants have been reclassified, ``watched_count``
    and ``watched_details`` upgrade the banner to include a
    watched-variant callout (P4-21i).
    """
    details_json = json.dumps(watched_details or [])
    with engine.begin() as conn:
        # Check for existing undismissed prompt
        existing = conn.execute(
            sa.select(reannotation_prompts.c.id).where(
                reannotation_prompts.c.sample_id == sample_id,
                reannotation_prompts.c.db_name == db_name,
                reannotation_prompts.c.dismissed == sa.false(),
            )
        ).fetchone()

        if existing:
            conn.execute(
                reannotation_prompts.update()
                .where(reannotation_prompts.c.id == existing.id)
                .values(
                    db_version=db_version,
                    candidate_count=candidate_count,
                    watched_count=watched_count,
                    watched_details=details_json,
                    created_at=datetime.now(UTC),
                )
            )
        else:
            conn.execute(
                reannotation_prompts.insert().values(
                    sample_id=sample_id,
                    db_name=db_name,
                    db_version=db_version,
                    candidate_count=candidate_count,
                    watched_count=watched_count,
                    watched_details=details_json,
                    dismissed=False,
                )
            )


def _safe_parse_json_list(value: str | None) -> list:
    """Parse a JSON array string, returning [] on any failure."""
    if not value:
        return []
    try:
        result = json.loads(value)
        return result if isinstance(result, list) else []
    except (json.JSONDecodeError, TypeError):
        return []


def get_active_prompts(
    engine: Engine,
    *,
    sample_id: int | None = None,
) -> list[dict]:
    """Get all active (undismissed) re-annotation prompts."""
    stmt = sa.select(reannotation_prompts).where(reannotation_prompts.c.dismissed == sa.false())
    if sample_id is not None:
        stmt = stmt.where(reannotation_prompts.c.sample_id == sample_id)

    with engine.connect() as conn:
        rows = conn.execute(stmt).fetchall()

    return [
        {
            "id": row.id,
            "sample_id": row.sample_id,
            "db_name": row.db_name,
            "db_version": row.db_version,
            "candidate_count": row.candidate_count,
            "watched_count": row.watched_count or 0,
            "watched_details": _safe_parse_json_list(row.watched_details),
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }
        for row in rows
    ]


def dismiss_prompt(engine: Engine, prompt_id: int) -> bool:
    """Dismiss a re-annotation prompt. Returns True if found and updated."""
    with engine.begin() as conn:
        result = conn.execute(
            reannotation_prompts.update()
            .where(reannotation_prompts.c.id == prompt_id)
            .values(dismissed=True)
        )
    return result.rowcount > 0


# ── Update history ───────────────────────────────────────────────────


def _record_update_history(
    engine: Engine,
    *,
    db_name: str,
    previous_version: str | None,
    new_version: str,
    variants_added: int = 0,
    variants_reclassified: int = 0,
    download_size_bytes: int = 0,
    duration_seconds: int = 0,
) -> None:
    """Write a row to the update_history table."""
    with engine.begin() as conn:
        conn.execute(
            update_history.insert().values(
                db_name=db_name,
                previous_version=previous_version,
                new_version=new_version,
                updated_at=datetime.now(UTC),
                variants_added=variants_added,
                variants_reclassified=variants_reclassified,
                download_size_bytes=download_size_bytes,
                duration_seconds=duration_seconds,
            )
        )


def get_update_history(
    engine: Engine,
    *,
    db_name: str | None = None,
    limit: int = 50,
) -> list[dict]:
    """Retrieve update history records, most recent first."""
    stmt = sa.select(update_history).order_by(update_history.c.updated_at.desc())
    if db_name is not None:
        stmt = stmt.where(update_history.c.db_name == db_name)
    stmt = stmt.limit(limit)

    with engine.connect() as conn:
        rows = conn.execute(stmt).fetchall()

    return [
        {
            "id": row.id,
            "db_name": row.db_name,
            "previous_version": row.previous_version,
            "new_version": row.new_version,
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
            "variants_added": row.variants_added,
            "variants_reclassified": row.variants_reclassified,
            "download_size_bytes": row.download_size_bytes,
            "duration_seconds": row.duration_seconds,
        }
        for row in rows
    ]


# ── Version recording helper ─────────────────────────────────────────


def _record_version(
    engine: Engine,
    db_name: str,
    version: str,
    file_size_bytes: int = 0,
) -> None:
    """Insert or update the version in the database_versions table.

    Thin pass-through to :func:`backend.db.database_registry._record_db_version`
    retained for the historical ClinVar update call site and existing tests.
    """
    from backend.db.database_registry import _record_db_version

    _record_db_version(
        engine,
        db_name=db_name,
        version=version,
        file_size_bytes=file_size_bytes,
    )


# ── Auto-update toggle persistence ───────────────────────────────────


def get_auto_update(engine: Engine, db_name: str) -> bool:
    """Return the per-database auto-update toggle.

    Reads from the ``auto_update_settings`` table. Falls back to
    :data:`AUTO_UPDATE_DEFAULTS` when no row exists (defensive — the 007
    migration seeds defaults, but tests / freshly-created reference DBs
    may not have run it yet). Unknown databases default to ``False``.
    """
    with engine.connect() as conn:
        row = conn.execute(
            sa.select(auto_update_settings.c.enabled).where(
                auto_update_settings.c.db_name == db_name
            )
        ).fetchone()
    if row is not None:
        return bool(row.enabled)
    return AUTO_UPDATE_DEFAULTS.get(db_name, False)


def set_auto_update(engine: Engine, db_name: str, enabled: bool) -> None:
    """Insert or update the per-database auto-update toggle."""
    now = datetime.now(UTC)
    with engine.begin() as conn:
        existing = conn.execute(
            sa.select(auto_update_settings.c.db_name).where(
                auto_update_settings.c.db_name == db_name
            )
        ).fetchone()
        if existing is None:
            conn.execute(
                auto_update_settings.insert().values(
                    db_name=db_name,
                    enabled=enabled,
                    updated_at=now,
                )
            )
        else:
            conn.execute(
                auto_update_settings.update()
                .where(auto_update_settings.c.db_name == db_name)
                .values(enabled=enabled, updated_at=now)
            )


# ── Scheduler orchestrator ───────────────────────────────────────────


# Bundles whose updates run synchronously inside the scheduler via the
# manifest-driven ``run_<bundle>_update`` functions defined above. Each
# runner is resolved by name at dispatch time so test patches against this
# module's attributes are honored.
_BUNDLE_DBS: frozenset[str] = frozenset({"vep_bundle", "lai_bundle", "ancestry_pca", "gnomad"})


def _dispatch_auto_update(registry: DBRegistry, db_name: str) -> None:
    """Apply an auto-update for a single database.

    Bundle and ClinVar updates run synchronously inside the scheduler.
    Pipeline DBs are queued via the standard
    :func:`backend.tasks.huey_tasks.run_database_update_task` plumbing so they
    flow through the same code path as a user-triggered update (consistent
    progress-job rows, identical error handling, single transaction boundary
    per database in the underlying build function).
    """
    settings = registry.settings

    if db_name == "clinvar":
        run_clinvar_update(registry)
        return

    if db_name in _BUNDLE_DBS:
        # Resolve via module globals so unit tests can patch each runner.
        runner_name = {
            "vep_bundle": "run_vep_bundle_update",
            "lai_bundle": "run_lai_bundle_update",
            "ancestry_pca": "run_ancestry_pca_bundle_update",
            "gnomad": "run_gnomad_bundle_update",
        }[db_name]
        runner = globals()[runner_name]
        result = runner(settings)
        if result is None:
            raise RuntimeError(f"{db_name} auto-update failed")
        return

    from backend.db.database_registry import get_build_fn

    if get_build_fn(db_name) is None:
        logger.warning("auto_update_no_dispatch", db_name=db_name)
        return

    from backend.tasks.huey_tasks import (
        create_database_update_job,
        run_database_update_task,
    )

    job_id = create_database_update_job(db_name)
    run_database_update_task(job_id, db_name)


def run_scheduled_update_check(registry: DBRegistry) -> UpdateCheckResult:
    """Run a scheduled update check and apply auto-updates.

    Called by the Huey periodic task. Walks every entry in :data:`CHECK_FNS`
    (via :func:`check_all_updates`) and applies an auto-update for each
    available result that satisfies both the per-DB :func:`get_auto_update`
    toggle and the :func:`should_download_now` bandwidth-window check.
    Dispatch is centralized in :func:`_dispatch_auto_update`.
    """
    settings = registry.settings
    engine = registry.reference_engine

    # 1. Check for updates across all registered databases.
    check_result = check_all_updates(engine, settings=settings)

    logger.info(
        "update_check_complete",
        available=len(check_result.available),
        up_to_date=len(check_result.up_to_date),
        errors=len(check_result.errors),
    )

    # 2. Apply auto-updates for each candidate that passes the toggle +
    #    bandwidth gates. A failure in one dispatch is logged and recorded
    #    in ``check_result.errors`` so the sweep continues.
    for update_info in check_result.available:
        db_name = update_info.db_name

        if not get_auto_update(engine, db_name):
            logger.info("update_skipped_auto_disabled", db_name=db_name)
            continue

        if not should_download_now(
            update_info.download_size_bytes, settings.update_download_window
        ):
            logger.info(
                "update_deferred_bandwidth_window",
                db_name=db_name,
                size_bytes=update_info.download_size_bytes,
                window=settings.update_download_window,
            )
            continue

        try:
            _dispatch_auto_update(registry, db_name)
        except Exception as exc:
            logger.exception("auto_update_failed", db_name=db_name, error=str(exc))
            check_result.errors.append(f"{db_name} update failed: {exc}")

    return check_result

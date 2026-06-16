"""dbSNP rsid validation and cross-reference.

Downloads the NCBI RsMergeArch file, loads merged rsid mappings into
the ``dbsnp_merges`` table in reference.db, and provides validation/
annotation functions for sample variants.

Validates each sample rsid as:
- **valid**: matches ``rs\\d+`` format and is not in the merge table
- **merged**: rsid has been merged into a newer rsid (maps to current)
- **i_prefix**: 23andMe internal id (starts with 'i')
- **invalid**: does not match expected rsid format

Usage::

    from backend.annotation.dbsnp import (
        download_rsmerge_arch,
        load_rsmerge_into_db,
        annotate_sample_dbsnp,
    )

    # Download + load
    path = download_rsmerge_arch(dest_dir)
    stats = load_rsmerge_into_db(path, engine)

    # Annotation
    result = annotate_sample_dbsnp(sample_engine, reference_engine)
"""

from __future__ import annotations

import gzip
import hashlib
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from itertools import islice
from pathlib import Path
from typing import TYPE_CHECKING

import httpx
import sqlalchemy as sa
import structlog
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from backend.annotation.http_download import (
    clear_validator_sidecar,
    read_validator_sidecar,
    stream_download,
    write_validator_sidecar,
)
from backend.db.tables import (
    annotated_variants,
    dbsnp_merges,
    raw_variants,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

logger = structlog.get_logger(__name__)

# NCBI FTP URL for dbSNP RsMergeArch (GRCh38p7 / build 151)
# RsMergeArch contains rsID merge history which is assembly-independent.
RSMERGE_URL = (
    "https://ftp.ncbi.nlm.nih.gov/snp/organisms/"
    "human_9606_b151_GRCh38p7/database/organism_data/RsMergeArch.bcp.gz"
)

# Batch size for bulk inserts
BATCH_SIZE = 10_000

# Regex for standard dbSNP rsid format
_RSID_RE = re.compile(r"^rs\d+$")

# Regex for 23andMe internal id format
_I_PREFIX_RE = re.compile(r"^i\d+", re.IGNORECASE)


class ValidationStatus:
    """Enum-like constants for rsid validation outcomes."""

    VALID = "valid"
    MERGED = "merged"
    I_PREFIX = "i_prefix"
    INVALID = "invalid"


@dataclass
class MergeRecord:
    """A single parsed rsid merge record."""

    old_rsid: str
    current_rsid: str
    build_id: int | None = None


@dataclass
class LoadStats:
    """Statistics from a dbSNP merge-archive load operation."""

    total_lines: int = 0
    merges_loaded: int = 0
    skipped_malformed: int = 0
    file_date: str | None = None
    sha256: str | None = None


@dataclass
class ValidationResult:
    """Result of validating a single rsid."""

    rsid: str
    status: str  # ValidationStatus value
    current_rsid: str | None = None
    build_id: int | None = None


@dataclass
class AnnotationResult:
    """Statistics from a dbSNP annotation run."""

    total_variants: int = 0
    valid_rsids: int = 0
    merged_rsids: int = 0
    i_prefix_rsids: int = 0
    invalid_rsids: int = 0
    rows_written: int = 0

    @property
    def validated(self) -> int:
        return self.valid_rsids + self.merged_rsids


def _is_valid_rsid(rsid: str) -> bool:
    """Check if a string matches the standard dbSNP rsid format (rs\\d+)."""
    return bool(_RSID_RE.match(rsid))


def _is_i_prefix(rsid: str) -> bool:
    """Check if a string is a 23andMe internal id (i-prefixed)."""
    return bool(_I_PREFIX_RE.match(rsid))


def parse_rsmerge_line(line: str) -> MergeRecord | None:
    """Parse a single line from RsMergeArch.bcp.

    The BCP format is tab-separated with columns:
        rsHigh  rsLow  build_id  orien  create_time  last_updated  rsCurrent  orien2  comment

    We extract rsHigh (the old/retired rsid) and rsCurrent (the final
    current rsid after following the merge chain). If rsCurrent is empty,
    we fall back to rsLow.

    Returns:
        MergeRecord or None if the line is malformed.
    """
    parts = line.rstrip("\n\r").split("\t")
    if len(parts) < 7:
        return None

    rs_high = parts[0].strip()
    rs_low = parts[1].strip()
    build_str = parts[2].strip()
    rs_current = parts[6].strip() if len(parts) > 6 else ""

    if not rs_high:
        return None

    # Parse build_id
    build_id: int | None = None
    try:
        build_id = int(build_str) if build_str else None
    except ValueError:
        pass

    # Determine the current rsid: prefer rsCurrent, fall back to rsLow
    target = rs_current if rs_current else rs_low
    if not target:
        return None

    # Format as proper rsids
    old_rsid = f"rs{rs_high}" if not rs_high.startswith("rs") else rs_high
    current_rsid = f"rs{target}" if not target.startswith("rs") else target

    # Skip self-merges
    if old_rsid == current_rsid:
        return None

    return MergeRecord(
        old_rsid=old_rsid,
        current_rsid=current_rsid,
        build_id=build_id,
    )


def iter_rsmerge_file(
    path: Path,
    *,
    progress_callback: Callable[[int], None] | None = None,
) -> Iterator[tuple[dict, LoadStats]]:
    """Iterate over RsMergeArch.bcp rows, yielding (row_dict, stats).

    Args:
        path: Path to the BCP or BCP.gz file.
        progress_callback: Optional callback called with line count.

    Yields:
        Tuple of (row dict for insert, running LoadStats).
    """
    stats = LoadStats()

    open_fn = gzip.open if path.suffix == ".gz" else open
    with open_fn(path, "rt", encoding="utf-8", errors="replace") as fh:  # type: ignore[call-overload]
        for line in fh:
            stats.total_lines += 1

            record = parse_rsmerge_line(line)
            if record is None:
                stats.skipped_malformed += 1
                continue

            stats.merges_loaded += 1
            yield (
                {
                    "old_rsid": record.old_rsid,
                    "current_rsid": record.current_rsid,
                    "build_id": record.build_id,
                },
                stats,
            )

            if progress_callback and stats.total_lines % 100_000 == 0:
                progress_callback(stats.total_lines)


def _batched(iterator: Iterator[dict], size: int) -> Iterator[list[dict]]:
    """Yield successive batches from an iterator."""
    while True:
        batch = list(islice(iterator, size))
        if not batch:
            break
        yield batch


def _wal_checkpoint(engine: sa.Engine) -> None:
    """Run WAL checkpoint if file-backed."""
    url = str(engine.url)
    if url == "sqlite://" or ":memory:" in url:
        return
    with engine.connect() as conn:
        conn.execute(sa.text("PRAGMA wal_checkpoint(TRUNCATE)"))
        conn.commit()


def _compute_sha256(path: Path) -> str:
    """Compute SHA-256 hex digest of a file."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def load_rsmerge_into_db(
    rows: list[dict],
    engine: sa.Engine,
    *,
    stats: LoadStats | None = None,
    clear_existing: bool = True,
) -> LoadStats:
    """Bulk-load parsed merge rows into the dbsnp_merges table.

    Args:
        rows: List of dicts with old_rsid, current_rsid, build_id.
        engine: SQLAlchemy engine for reference.db.
        stats: Optional LoadStats to return.
        clear_existing: Whether to DELETE existing rows first.

    Returns:
        LoadStats with merge counts.
    """
    if stats is None:
        stats = LoadStats(merges_loaded=len(rows))

    if clear_existing:
        with engine.begin() as conn:
            conn.execute(dbsnp_merges.delete())

    for i in range(0, len(rows), BATCH_SIZE):
        batch = rows[i : i + BATCH_SIZE]
        with engine.begin() as conn:
            # Use INSERT OR REPLACE to handle duplicate old_rsids
            # (some rsids may appear multiple times in the merge chain)
            stmt = sqlite_insert(dbsnp_merges).values(batch)
            stmt = stmt.on_conflict_do_update(
                index_elements=["old_rsid"],
                set_={
                    "current_rsid": stmt.excluded.current_rsid,
                    "build_id": stmt.excluded.build_id,
                },
            )
            conn.execute(stmt)

    _wal_checkpoint(engine)

    logger.info("dbsnp_merges_loaded", merges=stats.merges_loaded)
    return stats


def load_rsmerge_from_iter(
    row_iter: Iterator[tuple[dict, LoadStats]],
    engine: sa.Engine,
    *,
    clear_existing: bool = True,
) -> LoadStats:
    """Stream-load merge rows from an iterator.

    Memory-efficient: only holds one batch at a time.

    Args:
        row_iter: Iterator yielding (row_dict, running_stats).
        engine: SQLAlchemy engine for reference.db.
        clear_existing: Whether to DELETE existing rows first.

    Returns:
        Final LoadStats.
    """
    stats = LoadStats()

    def rows_only() -> Iterator[dict]:
        nonlocal stats
        for row, stats in row_iter:
            yield row

    if clear_existing:
        with engine.begin() as conn:
            conn.execute(dbsnp_merges.delete())

    for batch in _batched(rows_only(), BATCH_SIZE):
        with engine.begin() as conn:
            stmt = sqlite_insert(dbsnp_merges).values(batch)
            stmt = stmt.on_conflict_do_update(
                index_elements=["old_rsid"],
                set_={
                    "current_rsid": stmt.excluded.current_rsid,
                    "build_id": stmt.excluded.build_id,
                },
            )
            conn.execute(stmt)

    _wal_checkpoint(engine)

    logger.info("dbsnp_merges_loaded", merges=stats.merges_loaded)
    return stats


def record_dbsnp_version(
    engine: sa.Engine,
    *,
    version: str,
    file_path: str | None = None,
    file_size_bytes: int | None = None,
    checksum: str | None = None,
) -> None:
    """Insert or update the dbSNP version in the database_versions table."""
    from backend.db.database_registry import _record_db_version

    _record_db_version(
        engine,
        db_name="dbsnp",
        version=version,
        file_size_bytes=file_size_bytes,
        sha256=checksum,
        file_path=file_path,
    )


def check_dbsnp_update(
    reference_engine: sa.Engine,
    settings: object | None = None,
    *,
    timeout: float = 30.0,
):
    """Check whether the dbSNP RsMergeArch pinned in the manifest is newer than installed.

    Uses ``pipeline_pins["dbsnp"]`` from ``bundles/manifest.json`` as the
    authoritative source for the latest URL, then performs an HTTP HEAD on
    the pinned URL. NCBI publishes the RsMergeArch.bcp.gz file without a
    static release tag, so the remote version is derived from the response's
    ``Last-Modified`` header (formatted YYYYMMDD to match
    :func:`download_and_load_rsmerge`'s recorded value). The ``Content-Length``
    response header populates the download-size estimate used by the
    bandwidth-window check. Returns ``None`` when the manifest pin is
    missing/unreachable, the HEAD call fails, ``Last-Modified`` is absent,
    or the recorded version is the same as or newer than the remote.

    Args:
        reference_engine: Reference DB engine for ``database_versions`` lookup.
        settings: Accepted for dispatch-signature parity with other
            ``check_*_update`` functions; unused.
        timeout: HTTP timeout in seconds for both the manifest fetch and HEAD.

    Returns:
        ``VersionInfo`` when the remote ``Last-Modified`` date is newer than
        the recorded version, otherwise ``None``.
    """
    del settings  # unused; kept for dispatch-signature parity
    from email.utils import parsedate_to_datetime

    from backend.db.manifest import get_pipeline_pin
    from backend.db.update_manager import VersionInfo, get_current_version

    pin = get_pipeline_pin("dbsnp", timeout=timeout)
    if pin is None or not pin.url:
        return None

    try:
        with httpx.Client(
            follow_redirects=True,
            timeout=httpx.Timeout(timeout, connect=10.0),
        ) as client:
            resp = client.head(pin.url)
            resp.raise_for_status()
            last_modified = resp.headers.get("Last-Modified", "")
            content_length = resp.headers.get("Content-Length")
    except Exception as exc:
        logger.warning("dbsnp_update_check_failed", error=str(exc))
        return None

    if not last_modified:
        return None

    try:
        remote_version = parsedate_to_datetime(last_modified).strftime("%Y%m%d")
    except (TypeError, ValueError) as exc:
        logger.warning("dbsnp_update_check_bad_last_modified", error=str(exc))
        return None

    current = get_current_version(reference_engine, "dbsnp")
    if current is not None and current >= remote_version:
        return None

    download_size = 0
    if content_length:
        try:
            download_size = int(content_length)
        except ValueError:
            download_size = 0

    return VersionInfo(
        db_name="dbsnp",
        latest_version=remote_version,
        download_url=pin.url,
        download_size_bytes=download_size,
        release_date=remote_version,
    )


def download_rsmerge_arch(
    dest_dir: Path,
    *,
    url: str = RSMERGE_URL,
    progress_callback: Callable[[int, int | None], None] | None = None,
    timeout: float = 600.0,
    meta: dict | None = None,
) -> Path:
    """Download the dbSNP RsMergeArch file from NCBI FTP.

    Writes to a temporary file and renames on success.

    Args:
        dest_dir: Directory to save the downloaded file.
        url: Override URL (useful for testing).
        progress_callback: Called with (bytes_downloaded, total_bytes).
        timeout: HTTP timeout in seconds.
        meta: Optional mutable dict populated with response metadata. When
            the server sends a ``Last-Modified`` header, ``meta["version"]``
            is set to the parsed ``YYYYMMDD`` string so callers can record the
            same version that :func:`check_dbsnp_update` compares against.

    Returns:
        Path to the downloaded .bcp.gz file.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_path = dest_dir / "RsMergeArch.bcp.gz"
    tmp_path = dest_dir / "RsMergeArch.bcp.gz.tmp"

    logger.info("dbsnp_download_start", url=url)

    # Resumable so a partial survives a failed/restarted build and the next run
    # continues via HTTP Range; the validator sidecar persists the If-Range token
    # across runs so a rotated upstream restarts clean instead of splicing.
    outcome = stream_download(
        url,
        tmp_path,
        progress_callback=progress_callback,
        timeout=timeout,
        resumable=True,
        validator=read_validator_sidecar(tmp_path),
        on_validator=lambda v: write_validator_sidecar(tmp_path, v),
    )

    if meta is not None:
        last_modified = outcome.headers.get("Last-Modified", "")
        if last_modified:
            from email.utils import parsedate_to_datetime

            try:
                meta["version"] = parsedate_to_datetime(last_modified).strftime("%Y%m%d")
            except (TypeError, ValueError) as exc:
                logger.warning("dbsnp_download_bad_last_modified", error=str(exc))

    # Atomic rename on success; drop the now-stale validator sidecar.
    tmp_path.replace(dest_path)
    clear_validator_sidecar(tmp_path)

    logger.info("dbsnp_download_complete", path=str(dest_path))
    return dest_path


def download_and_load_rsmerge(
    engine: sa.Engine,
    dest_dir: Path,
    *,
    url: str = RSMERGE_URL,
    download_progress: Callable[[int, int | None], None] | None = None,
    parse_progress: Callable[[int], None] | None = None,
    timeout: float = 600.0,
) -> LoadStats:
    """Full pipeline: download RsMergeArch, parse, and load into reference.db.

    Args:
        engine: SQLAlchemy engine for reference.db.
        dest_dir: Directory for downloaded files.
        url: Override URL for testing.
        download_progress: Callback for download progress.
        parse_progress: Callback for parse progress.
        timeout: HTTP timeout in seconds.

    Returns:
        LoadStats with merge counts.
    """
    meta: dict = {}
    path = download_rsmerge_arch(
        dest_dir,
        url=url,
        progress_callback=download_progress,
        timeout=timeout,
        meta=meta,
    )

    sha256 = _compute_sha256(path)

    row_iter = iter_rsmerge_file(path, progress_callback=parse_progress)
    stats = load_rsmerge_from_iter(row_iter, engine)
    stats.sha256 = sha256

    # Persist the Last-Modified-derived YYYYMMDD so it matches the value
    # check_dbsnp_update compares against; fall back to the install date when
    # the server did not provide a usable Last-Modified header.
    version = meta.get("version") or datetime.now(UTC).strftime("%Y%m%d")
    stats.file_date = version
    record_dbsnp_version(
        engine,
        version=version,
        file_path=str(path),
        file_size_bytes=path.stat().st_size,
        checksum=sha256,
    )

    return stats


# ═══════════════════════════════════════════════════════════════════════
# dbSNP rsid Validation & Cross-Reference (P1-12)
# ═══════════════════════════════════════════════════════════════════════


def lookup_merged_rsids(
    rsids: list[str],
    reference_engine: sa.Engine,
) -> dict[str, MergeRecord]:
    """Look up merged rsid mappings for a batch of rsids.

    Args:
        rsids: List of rsid strings to check.
        reference_engine: SQLAlchemy engine for reference.db.

    Returns:
        Dict mapping old_rsid → MergeRecord for rsids that have been merged.
    """
    if not rsids:
        return {}

    results: dict[str, MergeRecord] = {}

    with reference_engine.connect() as conn:
        for i in range(0, len(rsids), 500):
            batch = rsids[i : i + 500]

            stmt = sa.select(
                dbsnp_merges.c.old_rsid,
                dbsnp_merges.c.current_rsid,
                dbsnp_merges.c.build_id,
            ).where(dbsnp_merges.c.old_rsid.in_(batch))

            rows = conn.execute(stmt).fetchall()

            for row in rows:
                results[row.old_rsid] = MergeRecord(
                    old_rsid=row.old_rsid,
                    current_rsid=row.current_rsid,
                    build_id=row.build_id,
                )

    return results


def validate_rsids(
    rsids: list[str],
    reference_engine: sa.Engine,
) -> list[ValidationResult]:
    """Validate a list of rsids against dbSNP merge data.

    Classification:
    - ``valid``: Standard rs-format, not found in merge table (assumed current)
    - ``merged``: Found in merge table, has a current replacement rsid
    - ``i_prefix``: 23andMe internal identifier (not in dbSNP)
    - ``invalid``: Does not match expected rsid formats

    Args:
        rsids: List of rsid strings to validate.
        reference_engine: SQLAlchemy engine for reference.db.

    Returns:
        List of ValidationResult objects in the same order as input.
    """
    # First classify by format
    rs_format_rsids = [r for r in rsids if _is_valid_rsid(r)]

    # Look up merges for rs-format rsids only
    merged = lookup_merged_rsids(rs_format_rsids, reference_engine)

    results: list[ValidationResult] = []
    for rsid in rsids:
        if _is_i_prefix(rsid):
            results.append(ValidationResult(rsid=rsid, status=ValidationStatus.I_PREFIX))
        elif not _is_valid_rsid(rsid):
            results.append(ValidationResult(rsid=rsid, status=ValidationStatus.INVALID))
        elif rsid in merged:
            rec = merged[rsid]
            results.append(
                ValidationResult(
                    rsid=rsid,
                    status=ValidationStatus.MERGED,
                    current_rsid=rec.current_rsid,
                    build_id=rec.build_id,
                )
            )
        else:
            results.append(ValidationResult(rsid=rsid, status=ValidationStatus.VALID))

    return results


def annotate_sample_dbsnp(
    sample_engine: sa.Engine,
    reference_engine: sa.Engine,
) -> AnnotationResult:
    """Validate and cross-reference a sample's rsids against dbSNP.

    Reads all raw_variants from the sample database, validates each rsid,
    resolves merged rsids, and upserts dbSNP columns into annotated_variants.

    This does NOT set an annotation_coverage bitmask bit — dbSNP validation
    is a cross-reference utility, not one of the 6 annotation sources.

    Args:
        sample_engine: SQLAlchemy engine for the per-sample database.
        reference_engine: SQLAlchemy engine for reference.db.

    Returns:
        AnnotationResult with validation statistics.
    """
    result = AnnotationResult()

    # 1. Read all raw variants
    with sample_engine.connect() as conn:
        raw_rows = conn.execute(
            sa.select(
                raw_variants.c.rsid,
                raw_variants.c.chrom,
                raw_variants.c.pos,
                raw_variants.c.genotype,
            )
        ).fetchall()

    result.total_variants = len(raw_rows)
    if not raw_rows:
        return result

    # 2. Validate all rsids
    all_rsids = [r.rsid for r in raw_rows]
    validations = validate_rsids(all_rsids, reference_engine)
    raw_by_rsid = {r.rsid: r for r in raw_rows}

    # 3. Build upsert rows
    rows_to_upsert: list[dict] = []
    for v in validations:
        raw = raw_by_rsid[v.rsid]

        if v.status == ValidationStatus.VALID:
            result.valid_rsids += 1
        elif v.status == ValidationStatus.MERGED:
            result.merged_rsids += 1
        elif v.status == ValidationStatus.I_PREFIX:
            result.i_prefix_rsids += 1
        else:
            result.invalid_rsids += 1

        rows_to_upsert.append(
            {
                "rsid": v.rsid,
                "chrom": raw.chrom,
                "pos": raw.pos,
                "genotype": raw.genotype,
                "dbsnp_build": v.build_id,
                "dbsnp_rsid_current": v.current_rsid,
                "dbsnp_validation": v.status,
            }
        )

    # 4. Upsert into annotated_variants
    if rows_to_upsert:
        with sample_engine.begin() as conn:
            for batch_start in range(0, len(rows_to_upsert), BATCH_SIZE):
                batch = rows_to_upsert[batch_start : batch_start + BATCH_SIZE]

                stmt = sqlite_insert(annotated_variants).values(batch)
                stmt = stmt.on_conflict_do_update(
                    index_elements=["rsid"],
                    set_={
                        "dbsnp_build": stmt.excluded.dbsnp_build,
                        "dbsnp_rsid_current": stmt.excluded.dbsnp_rsid_current,
                        "dbsnp_validation": stmt.excluded.dbsnp_validation,
                    },
                )
                conn.execute(stmt)

        result.rows_written = len(rows_to_upsert)

    # WAL checkpoint
    _wal_checkpoint(sample_engine)

    logger.info(
        "dbsnp_validation_complete",
        total=result.total_variants,
        valid=result.valid_rsids,
        merged=result.merged_rsids,
        i_prefix=result.i_prefix_rsids,
        invalid=result.invalid_rsids,
    )

    return result

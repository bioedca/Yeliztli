"""ENCODE cCREs (candidate cis-Regulatory Elements) data loader.

Downloads the ENCODE Registry of cCREs BED file (GRCh38/hg38), parses it,
and loads into a local SQLite database for efficient region-based queries.
This data is used exclusively for IGV.js track visualization — it is NOT
integrated into the annotation pipeline, findings, or reference.db.

Data is stored at ``data_dir / encode_ccres.db``.

cCRE classifications:
    - PLS:  promoter-like signature
    - pELS: proximal enhancer-like signature
    - dELS: distal enhancer-like signature
    - CTCF-only: CTCF-bound only
    - DNase-H3K4me3: DNase + H3K4me3

Usage::

    from backend.annotation.encode_ccres import (
        load_encode_ccres,
        query_ccres_by_region,
    )

    stats = load_encode_ccres(bed_path, engine)
    hits = query_ccres_by_region("1", 1_000_000, 2_000_000, engine)
"""

from __future__ import annotations

import gzip
import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import sqlalchemy as sa
import structlog

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

logger = structlog.get_logger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────

# ENCODE Registry of cCREs V3, GRCh38/hg38
# This is the SCREEN (Search Candidate cis-Regulatory Elements) BED file.
# Used only for IGV.js track visualization, not part of the annotation pipeline.
ENCODE_CCRES_URL = "https://downloads.wenglab.org/V3/GRCh38-cCREs.bed"

# Batch sizes
BATCH_SIZE = 10_000

# Valid chromosomes (matching 23andMe scope)
VALID_CHROMS = {str(i) for i in range(1, 23)} | {"X", "Y"}

# Known cCRE classifications
CCRE_CLASSIFICATIONS = frozenset(
    {
        "PLS",
        "pELS",
        "dELS",
        "CTCF-only",
        "DNase-H3K4me3",
    }
)

# ── SQL for encode_ccres table creation ──────────────────────────────────

CREATE_TABLE_SQL = """\
CREATE TABLE IF NOT EXISTS encode_ccres (
    accession   TEXT PRIMARY KEY,
    chrom       TEXT NOT NULL,
    start_pos   INTEGER NOT NULL,
    end_pos     INTEGER NOT NULL,
    ccre_class  TEXT NOT NULL
)
"""

CREATE_INDEXES_SQL = [
    "CREATE INDEX IF NOT EXISTS idx_ccres_region ON encode_ccres (chrom, start_pos, end_pos)",
    "CREATE INDEX IF NOT EXISTS idx_ccres_class ON encode_ccres (ccre_class)",
]

# Version tracking table (same pattern as other loaders)
CREATE_VERSION_SQL = """\
CREATE TABLE IF NOT EXISTS encode_ccres_version (
    loaded_at   TEXT NOT NULL,
    source_url  TEXT NOT NULL,
    record_count INTEGER NOT NULL,
    sha256      TEXT
)
"""


# ── Data classes ─────────────────────────────────────────────────────────


@dataclass
class CCRERecord:
    """A single parsed ENCODE cCRE record."""

    accession: str
    chrom: str
    start_pos: int
    end_pos: int
    ccre_class: str


@dataclass
class LoadStats:
    """Statistics from an ENCODE cCREs load operation."""

    total_lines: int = 0
    records_loaded: int = 0
    skipped_invalid_chrom: int = 0
    skipped_malformed: int = 0
    skipped_unknown_class: int = 0
    sha256: str | None = None


@dataclass
class CCREResult:
    """A cCRE record returned from a region query."""

    accession: str
    chrom: str
    start_pos: int
    end_pos: int
    ccre_class: str


# ── Helpers ──────────────────────────────────────────────────────────────


def _normalize_chrom(chrom: str) -> str | None:
    """Normalize chromosome name (strip 'chr' prefix). Returns None for invalid."""
    c = chrom.removeprefix("chr").upper()
    if c in VALID_CHROMS:
        return c
    return None


# ── Parsing ──────────────────────────────────────────────────────────────


def _extract_ccre_class(value: str) -> str | None:
    """Extract a known cCRE classification from a value that may contain modifiers.

    The V3 format uses comma-separated values like ``CTCF-only,CTCF-bound``
    where the primary class comes first.
    """
    # Try the raw value first
    stripped = value.strip()
    if stripped in CCRE_CLASSIFICATIONS:
        return stripped
    # Split on comma — the primary class is the first token
    for token in stripped.split(","):
        token = token.strip()
        if token in CCRE_CLASSIFICATIONS:
            return token
    return None


def _parse_ccre_bed_line(line: str) -> tuple[CCRERecord | None, str | None]:
    """Parse a single line from the ENCODE cCREs BED file.

    Supports multiple ENCODE BED formats:
      - V3 (6-col): chrom start end rdhs_accession ccre_accession class[,modifier]
      - BED9+:      chrom start end accession score strand ... ccre_class
      - Simplified: chrom start end accession ccre_class

    Args:
        line: A single BED line.

    Returns:
        Tuple of (record, skip_reason). One will be None.
    """
    parts = line.rstrip("\n\r").split("\t")

    if len(parts) < 5:
        return None, "malformed"

    chrom_raw = parts[0]
    chrom = _normalize_chrom(chrom_raw)
    if chrom is None:
        return None, "invalid_chrom"

    try:
        start_pos = int(parts[1])
        end_pos = int(parts[2])
    except (ValueError, IndexError):
        return None, "malformed"

    accession = parts[3]

    ccre_class = None

    # Registry V3 6-column rows place an rDHS/source identifier in column 4
    # and the cCRE accession in column 5.
    if len(parts) == 6:
        v3_class = _extract_ccre_class(parts[5])
        if v3_class is not None:
            accession = parts[4]
            ccre_class = v3_class

    # Try to find the cCRE classification across known column layouts:
    # 1. Column 9 (BED9+ full ENCODE format)
    # 2. Column 5 (V3 6-column format: accession1 accession2 class)
    # 3. Column 4 (simplified 5-column format: accession class)
    for col_idx in (9, 5, 4):
        if ccre_class is None and len(parts) > col_idx:
            ccre_class = _extract_ccre_class(parts[col_idx])

    # Last resort: extract from accession if it embeds the class
    # (e.g. EH38E1234567,PLS)
    if ccre_class is None and "," in accession:
        _acc, _, cls = accession.partition(",")
        if cls.strip() in CCRE_CLASSIFICATIONS:
            accession = _acc
            ccre_class = cls.strip()

    if ccre_class is None:
        return None, "unknown_class"

    return CCRERecord(
        accession=accession,
        chrom=chrom,
        start_pos=start_pos,
        end_pos=end_pos,
        ccre_class=ccre_class,
    ), None


def iter_ccre_bed(
    bed_path: Path,
    *,
    progress_callback: Callable[[int], None] | None = None,
) -> Iterator[tuple[dict, LoadStats]]:
    """Stream-parse the ENCODE cCREs BED file, yielding row dicts.

    Args:
        bed_path: Path to the BED file (plain or gzipped).
        progress_callback: Optional callback(lines_processed).

    Yields:
        Tuples of (row_dict, running_stats).
    """
    stats = LoadStats()
    opener = gzip.open if str(bed_path).endswith(".gz") else open

    with opener(bed_path, "rt", encoding="utf-8") as f:
        for line in f:
            stats.total_lines += 1

            # Skip comment/header lines
            if line.startswith("#") or line.startswith("track") or line.startswith("browser"):
                continue

            stripped = line.strip()
            if not stripped:
                continue

            record, skip_reason = _parse_ccre_bed_line(stripped)

            if record is None:
                if skip_reason == "invalid_chrom":
                    stats.skipped_invalid_chrom += 1
                elif skip_reason == "unknown_class":
                    stats.skipped_unknown_class += 1
                else:
                    stats.skipped_malformed += 1
                continue

            stats.records_loaded += 1
            row = {
                "accession": record.accession,
                "chrom": record.chrom,
                "start_pos": record.start_pos,
                "end_pos": record.end_pos,
                "ccre_class": record.ccre_class,
            }

            if progress_callback and stats.total_lines % 50_000 == 0:
                progress_callback(stats.total_lines)

            yield row, stats


# ── Table creation ───────────────────────────────────────────────────────


def create_encode_ccres_tables(engine: sa.Engine) -> None:
    """Create the encode_ccres table and indexes."""
    with engine.connect() as conn:
        conn.execute(sa.text(CREATE_TABLE_SQL))
        for idx_sql in CREATE_INDEXES_SQL:
            conn.execute(sa.text(idx_sql))
        conn.execute(sa.text(CREATE_VERSION_SQL))
        conn.commit()


# ── Bulk loading ─────────────────────────────────────────────────────────


def _insert_batch(engine: sa.Engine, batch: list[dict]) -> None:
    """Insert a batch of cCRE records using INSERT OR IGNORE."""
    if not batch:
        return
    sql = sa.text(
        "INSERT OR IGNORE INTO encode_ccres (accession, chrom, start_pos, end_pos, ccre_class) "
        "VALUES (:accession, :chrom, :start_pos, :end_pos, :ccre_class)"
    )
    with engine.begin() as conn:
        conn.execute(sql, batch)


def _compute_sha256(file_path: Path) -> str:
    """Compute SHA-256 hex digest of a file."""
    sha = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(1_048_576), b""):
            sha.update(chunk)
    return sha.hexdigest()


def load_encode_ccres(
    bed_path: Path,
    engine: sa.Engine,
    *,
    progress_callback: Callable[[int], None] | None = None,
) -> LoadStats:
    """Load ENCODE cCREs BED file into the SQLite database.

    Creates tables if they don't exist, then streams the BED file
    and bulk-inserts in batches.

    Args:
        bed_path: Path to the ENCODE cCREs BED file.
        engine: SQLAlchemy engine for the encode_ccres database.
        progress_callback: Optional callback(lines_processed).

    Returns:
        LoadStats with counts of loaded/skipped records.
    """
    logger.info("encode_ccres_load_start", path=str(bed_path))

    create_encode_ccres_tables(engine)

    stats = LoadStats()
    batch: list[dict] = []

    for row, running_stats in iter_ccre_bed(bed_path, progress_callback=progress_callback):
        batch.append(row)
        stats = running_stats

        if len(batch) >= BATCH_SIZE:
            _insert_batch(engine, batch)
            batch = []

    # Final batch
    if batch:
        _insert_batch(engine, batch)

    # Compute SHA-256
    stats.sha256 = _compute_sha256(bed_path)

    # Record version
    _record_version(engine, stats)

    # WAL checkpoint
    with engine.connect() as conn:
        conn.execute(sa.text("PRAGMA wal_checkpoint(TRUNCATE)"))
        conn.commit()

    logger.info(
        "encode_ccres_load_complete",
        records_loaded=stats.records_loaded,
        total_lines=stats.total_lines,
        skipped_invalid_chrom=stats.skipped_invalid_chrom,
        skipped_malformed=stats.skipped_malformed,
        skipped_unknown_class=stats.skipped_unknown_class,
    )

    return stats


def _record_version(engine: sa.Engine, stats: LoadStats) -> None:
    """Record load metadata in the version table."""
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO encode_ccres_version (loaded_at, source_url, record_count, sha256) "
                "VALUES (:loaded_at, :source_url, :record_count, :sha256)"
            ),
            {
                "loaded_at": datetime.now(UTC).isoformat(),
                "source_url": ENCODE_CCRES_URL,
                "record_count": stats.records_loaded,
                "sha256": stats.sha256,
            },
        )


# ── Region queries (for IGV.js track) ───────────────────────────────────


def query_ccres_by_region(
    chrom: str,
    start: int,
    end: int,
    engine: sa.Engine,
) -> list[CCREResult]:
    """Query cCREs overlapping a genomic region.

    Uses the (chrom, start_pos, end_pos) index for efficient range queries.
    Returns all cCREs where the cCRE interval overlaps [start, end].

    Args:
        chrom: Chromosome (without 'chr' prefix).
        start: Region start position (0-based).
        end: Region end position (0-based).
        engine: SQLAlchemy engine for the encode_ccres database.

    Returns:
        List of CCREResult records overlapping the region.
    """
    chrom_norm = _normalize_chrom(chrom) or chrom
    sql = sa.text(
        "SELECT accession, chrom, start_pos, end_pos, ccre_class "
        "FROM encode_ccres "
        "WHERE chrom = :chrom AND end_pos >= :start AND start_pos <= :end "
        "ORDER BY start_pos"
    )

    with engine.connect() as conn:
        rows = conn.execute(sql, {"chrom": chrom_norm, "start": start, "end": end}).fetchall()

    return [
        CCREResult(
            accession=row[0],
            chrom=row[1],
            start_pos=row[2],
            end_pos=row[3],
            ccre_class=row[4],
        )
        for row in rows
    ]


def get_ccre_summary(engine: sa.Engine) -> dict[str, int]:
    """Get counts of cCREs by classification.

    Args:
        engine: SQLAlchemy engine for the encode_ccres database.

    Returns:
        Dict mapping classification -> count.
    """
    sql = sa.text(
        "SELECT ccre_class, COUNT(*) FROM encode_ccres GROUP BY ccre_class ORDER BY ccre_class"
    )
    with engine.connect() as conn:
        rows = conn.execute(sql).fetchall()
    return {row[0]: row[1] for row in rows}


def is_loaded(engine: sa.Engine) -> bool:
    """Check whether ENCODE cCREs data has been loaded.

    Args:
        engine: SQLAlchemy engine for the encode_ccres database.

    Returns:
        True if the encode_ccres table exists and has data.
    """
    try:
        with engine.connect() as conn:
            result = conn.execute(sa.text("SELECT COUNT(*) FROM encode_ccres")).scalar()
            return (result or 0) > 0
    except sa.exc.OperationalError:
        return False

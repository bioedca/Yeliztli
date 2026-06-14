"""AlphaMissense proteome-wide missense pathogenicity loader (SW-A12).

EXPANSION_STRATEGY second-wave SW-A12. Streams the AlphaMissense hg19 predictions
TSV (Cheng et al., *Science* 2023) into a standalone, position-keyed SQLite DB
(``alphamissense.db``) and provides a batch lookup by ``(chrom, pos, ref, alt)``.
Mirrors :mod:`backend.annotation.gnomad_constraint` / :mod:`backend.annotation.gnomad`:
a thin, idempotent (``INSERT OR REPLACE``) loader with a CSV path for fixtures, a
streaming downloader, and a ``database_versions`` row.

**Licensing (owner decision: bundle as CC-BY-4.0).** Pinned to Zenodo record
10813168, whose record-level grant is CC-BY-4.0 (Cheng et al., *Science* 2023) —
the deliberately-chosen redistributable source, distinct from the original
CC-BY-NC-SA records 8208688/8360242. NOTE: the data *file* still embeds a stale
``# Licensed under CC BY-NC-SA 4.0`` header from that original release; the
authoritative Zenodo record grant (CC-BY-4.0) governs (see NOTICE).

hg19 is GRCh37, matching the consumer-array build. Chromosome names are
normalized to the project convention (``chr1`` → ``1``). This is **context only**:
it is an additive *complement* to REVEL (:mod:`backend.analysis.insilico_tiers`),
NOT a third independent in-silico vote — see :mod:`backend.analysis.alphamissense`.
"""

from __future__ import annotations

import gzip
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import sqlalchemy as sa
import structlog

from backend.annotation.bulk_load import bulk_write_connection, execute_write, insert_batch
from backend.annotation.http_download import (
    clear_validator_sidecar,
    read_validator_sidecar,
    stream_download,
    write_validator_sidecar,
)

if TYPE_CHECKING:
    from collections.abc import Callable

logger = structlog.get_logger(__name__)

# Zenodo record 10813168 (CC-BY-4.0 grant), AlphaMissense hg19 predictions.
ALPHAMISSENSE_URL = (
    "https://zenodo.org/records/10813168/files/AlphaMissense_hg19.tsv.gz?download=1"
)
ALPHAMISSENSE_VERSION = "hg19-2023"  # Cheng et al. Science 2023, Zenodo 10813168 v3
ALPHAMISSENSE_MD5 = "3f14fba08c60b09a90a2ae14b7802e4c"

BATCH_SIZE = 20_000

VALID_CHROMS = {str(i) for i in range(1, 23)} | {"X", "Y", "MT"}

# AlphaMissense paper am_pathogenicity thresholds (the am_class column encodes them):
#   < 0.34          -> likely benign
#   0.34 .. 0.564   -> ambiguous
#   > 0.564         -> likely pathogenic
AM_BENIGN_MAX = 0.34
AM_PATHOGENIC_MIN = 0.564

CREATE_TABLE_SQL = """\
CREATE TABLE IF NOT EXISTS alphamissense_scores (
    chrom            TEXT    NOT NULL,
    pos              INTEGER NOT NULL,
    ref              TEXT    NOT NULL,
    alt              TEXT    NOT NULL,
    am_pathogenicity REAL,
    am_class         TEXT,
    PRIMARY KEY (chrom, pos, ref, alt)
)
"""

CREATE_INDEX_SQL = (
    "CREATE INDEX IF NOT EXISTS idx_alphamissense_chrom_pos ON alphamissense_scores (chrom, pos)"
)

_INSERT_SQL = sa.text(
    "INSERT OR REPLACE INTO alphamissense_scores "
    "(chrom, pos, ref, alt, am_pathogenicity, am_class) "
    "VALUES (:chrom, :pos, :ref, :alt, :am_pathogenicity, :am_class)"
)

# Column order in the AlphaMissense TSV (after the '#CHROM' header line).
_COLS = (
    "CHROM",
    "POS",
    "REF",
    "ALT",
    "genome",
    "uniprot_id",
    "transcript_id",
    "protein_variant",
    "am_pathogenicity",
    "am_class",
)


@dataclass
class AlphaMissenseLoadStats:
    total_lines: int = 0
    loaded: int = 0
    skipped: int = 0
    sha256: str | None = None
    version: str | None = None


def _normalize_chrom(chrom: str) -> str | None:
    """``chr1`` → ``1``; returns None for non-canonical chromosomes."""
    c = chrom.strip().removeprefix("chr").removeprefix("CHR").upper()
    if c == "M":
        c = "MT"
    return c if c in VALID_CHROMS else None


def _parse_float(value: str | None) -> float | None:
    if value is None:
        return None
    v = value.strip()
    if not v or v == ".":
        return None
    try:
        return float(v)
    except ValueError:
        return None


def _file_digest(path: Path, algo: str) -> str:
    """Streaming hex digest of a (possibly multi-GB) file."""
    h = hashlib.new(algo)
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def create_alphamissense_table(engine: sa.Engine) -> None:
    """Create the alphamissense table + index if absent (idempotent)."""
    with bulk_write_connection(engine) as conn:
        execute_write(conn, sa.text(CREATE_TABLE_SQL))
        execute_write(conn, sa.text(CREATE_INDEX_SQL))


def _record_from_fields(fields: list[str], idx: dict[str, int]) -> dict | None:
    """Map a split TSV data row to a record dict, or None to skip."""
    try:
        chrom = _normalize_chrom(fields[idx["CHROM"]])
        if chrom is None:
            return None
        return {
            "chrom": chrom,
            "pos": int(fields[idx["POS"]]),
            "ref": fields[idx["REF"]].strip(),
            "alt": fields[idx["ALT"]].strip(),
            "am_pathogenicity": _parse_float(fields[idx["am_pathogenicity"]]),
            "am_class": fields[idx["am_class"]].strip() or None,
        }
    except (IndexError, ValueError):
        return None


def _column_index(header_line: str) -> dict[str, int]:
    """Map column name → position from the AlphaMissense ``#CHROM`` header line."""
    names = header_line.lstrip("#").rstrip("\n").split("\t")
    idx = {name: i for i, name in enumerate(names)}
    missing = [
        c for c in ("CHROM", "POS", "REF", "ALT", "am_pathogenicity", "am_class") if c not in idx
    ]
    if missing:
        raise ValueError(f"AlphaMissense header missing columns: {missing}")
    return idx


def load_alphamissense_from_tsv(tsv_path: Path, engine: sa.Engine) -> AlphaMissenseLoadStats:
    """Stream the AlphaMissense hg19 TSV (gzip/bgzip or plain) into the table.

    Skips ``#`` comment lines; the ``#CHROM`` line supplies the column order.
    """
    create_alphamissense_table(engine)
    opener: Callable = gzip.open if str(tsv_path).endswith((".gz", ".bgz")) else open
    stats = AlphaMissenseLoadStats()
    idx: dict[str, int] | None = None
    batch: list[dict] = []
    with opener(tsv_path, "rt", encoding="utf-8") as f, bulk_write_connection(engine) as conn:
        for line in f:
            if line.startswith("#"):
                if line.startswith("#CHROM") or line[1:].startswith("CHROM"):
                    idx = _column_index(line)
                continue
            if idx is None:
                # No header seen yet → assume the canonical fixed column order.
                idx = {name: i for i, name in enumerate(_COLS)}
            stats.total_lines += 1
            record = _record_from_fields(line.rstrip("\n").split("\t"), idx)
            if record is None:
                stats.skipped += 1
                continue
            batch.append(record)
            stats.loaded += 1
            if len(batch) >= BATCH_SIZE:
                insert_batch(conn, _INSERT_SQL, batch)
                batch = []
        if batch:
            insert_batch(conn, _INSERT_SQL, batch)
    logger.info("alphamissense_tsv_loaded", loaded=stats.loaded, skipped=stats.skipped)
    return stats


def load_alphamissense_from_csv(csv_path: Path, engine: sa.Engine) -> AlphaMissenseLoadStats:
    """Load a CSV seed (chrom,pos,ref,alt,am_pathogenicity,am_class) — for fixtures/tests."""
    import csv

    create_alphamissense_table(engine)
    stats = AlphaMissenseLoadStats()
    batch: list[dict] = []
    with open(csv_path, encoding="utf-8") as f, bulk_write_connection(engine) as conn:
        for row in csv.DictReader(f):
            stats.total_lines += 1
            chrom = _normalize_chrom(row.get("chrom", ""))
            if chrom is None or not row.get("pos"):
                stats.skipped += 1
                continue
            try:
                pos = int(row["pos"])
            except (ValueError, TypeError):
                stats.skipped += 1
                continue
            batch.append(
                {
                    "chrom": chrom,
                    "pos": pos,
                    "ref": (row.get("ref") or "").strip(),
                    "alt": (row.get("alt") or "").strip(),
                    "am_pathogenicity": _parse_float(row.get("am_pathogenicity")),
                    "am_class": (row.get("am_class") or "").strip() or None,
                }
            )
            stats.loaded += 1
        if batch:
            insert_batch(conn, _INSERT_SQL, batch)
    logger.info("alphamissense_csv_loaded", loaded=stats.loaded)
    return stats


def download_alphamissense(
    dest_dir: Path,
    *,
    url: str = ALPHAMISSENSE_URL,
    progress_callback: Callable[[int, int | None], None] | None = None,
    timeout: float = 1800.0,
) -> Path:
    """Download the AlphaMissense hg19 TSV to ``dest_dir`` (atomic rename on success)."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_path = dest_dir / "AlphaMissense_hg19.tsv.gz"
    tmp_path = dest_path.with_suffix(dest_path.suffix + ".tmp")
    logger.info("alphamissense_download_start", url=url)
    # Already resumable; add validator-sidecar persistence so a cross-run resume
    # validates the partial (If-Range) instead of risking a stale splice.
    stream_download(
        url,
        tmp_path,
        progress_callback=progress_callback,
        timeout=timeout,
        resumable=True,
        validator=read_validator_sidecar(tmp_path),
        on_validator=lambda v: write_validator_sidecar(tmp_path, v),
    )
    tmp_path.rename(dest_path)
    clear_validator_sidecar(tmp_path)
    return dest_path


def record_alphamissense_version(
    engine: sa.Engine,
    *,
    version: str = ALPHAMISSENSE_VERSION,
    file_path: str | None = None,
    file_size_bytes: int | None = None,
    checksum: str | None = None,
) -> None:
    """Record the AlphaMissense version in ``database_versions`` (GRCh37)."""
    from backend.db.database_registry import _record_db_version

    _record_db_version(
        engine,
        db_name="alphamissense",
        version=version,
        file_size_bytes=file_size_bytes,
        sha256=checksum,
        file_path=file_path,
        genome_build="GRCh37",
    )


def download_and_load_alphamissense(
    engine: sa.Engine,
    dest_dir: Path,
    *,
    download_progress: Callable[[int, int | None], None] | None = None,
    parse_progress: Callable[[int], None] | None = None,
    timeout: float = 1800.0,
    reference_engine: sa.Engine | None = None,
) -> AlphaMissenseLoadStats:
    """Build-mode entry point: download AlphaMissense hg19 and load into ``alphamissense.db``.

    Pipeline build (like dbNSFP): the ~622 MB CC-BY-4.0 TSV is fetched from Zenodo
    record 10813168 and streamed into a standalone position-keyed SQLite DB. Heavy —
    run on a cluster where possible.
    """
    del parse_progress  # progress is coarse for this single-stream build
    tsv_path = download_alphamissense(
        dest_dir, progress_callback=download_progress, timeout=timeout
    )

    # Integrity: verify the pinned MD5 before loading; a corrupt/truncated download
    # must never silently populate the table with partial/garbage data.
    actual_md5 = _file_digest(tsv_path, "md5")
    if actual_md5 != ALPHAMISSENSE_MD5:
        tsv_path.unlink(missing_ok=True)
        raise ValueError(
            f"AlphaMissense MD5 mismatch for {tsv_path.name}: expected "
            f"{ALPHAMISSENSE_MD5}, got {actual_md5} — download corrupt, aborting."
        )

    stats = load_alphamissense_from_tsv(tsv_path, engine)
    stats.version = ALPHAMISSENSE_VERSION
    stats.sha256 = _file_digest(tsv_path, "sha256")
    record_alphamissense_version(
        reference_engine or engine,
        file_path=str(tsv_path),
        file_size_bytes=tsv_path.stat().st_size if tsv_path.exists() else None,
        checksum=stats.sha256,
    )
    if download_progress is not None:
        download_progress(100, 100)
    return stats


def lookup_alphamissense_by_positions(
    positions: list[tuple[str, int, str, str]],
    engine: sa.Engine,
) -> dict[tuple[str, int, str, str], dict]:
    """Batch lookup → ``{(chrom,pos,ref,alt): {am_pathogenicity, am_class}}``.

    ``chrom`` inputs are normalized (``chr1`` → ``1``) before matching, so callers
    may pass either convention. Unknown variants are simply absent from the result.
    """
    if not positions:
        return {}
    out: dict[tuple[str, int, str, str], dict] = {}
    # Query per (chrom,pos); ref/alt matched in Python (keeps the SQL simple and
    # uses the (chrom,pos) index). Dedup positions first.
    by_chrom_pos: dict[tuple[str, int], list[tuple[str, int, str, str]]] = {}
    for chrom_raw, pos, ref, alt in positions:
        chrom = _normalize_chrom(chrom_raw)
        if chrom is None:
            continue
        by_chrom_pos.setdefault((chrom, pos), []).append((chrom_raw, pos, ref, alt))
    with engine.connect() as conn:
        for (chrom, pos), wanted in by_chrom_pos.items():
            rows = conn.execute(
                sa.text(
                    "SELECT ref, alt, am_pathogenicity, am_class FROM alphamissense_scores "
                    "WHERE chrom = :chrom AND pos = :pos"
                ),
                {"chrom": chrom, "pos": pos},
            ).fetchall()
            by_allele = {(r.ref, r.alt): r for r in rows}
            for key in wanted:
                _craw, _pos, ref, alt = key
                hit = by_allele.get((ref, alt))
                if hit is not None:
                    out[key] = {
                        "am_pathogenicity": hit.am_pathogenicity,
                        "am_class": hit.am_class,
                    }
    return out

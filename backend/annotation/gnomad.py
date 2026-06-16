"""gnomAD AF-only SQLite index builder and annotation lookup.

Downloads the gnomAD r2.1.1 exomes sites VCF, extracts allele frequency
fields per population, and builds an indexed SQLite database
(``gnomad_af.db``).  Also provides batch lookup functions used by the
annotation engine.

The ``gnomad_af`` table stores one row per variant with columns:
rsid, chrom, pos, ref, alt, af_global, af_afr, af_amr, af_eas,
af_eur, af_fin, af_sas, homozygous_count.

Usage::

    from backend.annotation.gnomad import (
        download_gnomad_vcf,
        load_gnomad_from_vcf,
        lookup_gnomad_by_rsids,
    )

    vcf_path = download_gnomad_vcf(dest_dir)
    stats = load_gnomad_from_vcf(vcf_path, gnomad_engine)
    matches = lookup_gnomad_by_rsids(["rs429358", "rs7412"], gnomad_engine)
"""

from __future__ import annotations

import csv
import gzip
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import sqlalchemy as sa
import structlog

from backend.annotation.bulk_load import (
    bulk_write_connection,
    execute_write,
    insert_batch,
    retry_on_locked,
)
from backend.annotation.http_download import stream_download
from backend.annotation.sqlite_limits import SQLITE_MAX_VARIABLE_NUMBER as _SQLITE_VAR_LIMIT

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

logger = structlog.get_logger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────

# gnomAD r2.1.1 exomes sites VCF (GRCh37)
GNOMAD_VCF_URL = (
    "https://storage.googleapis.com/gcp-public-data--gnomad/"
    "release/2.1.1/vcf/exomes/"
    "gnomad.exomes.r2.1.1.sites.vcf.bgz"
)

# Batch sizes
BATCH_SIZE = 10_000
# Default lookup batch sizes; upgraded at module load when SQLite supports
# a higher SQLITE_MAX_VARIABLE_NUMBER.
LOOKUP_BATCH_SIZE = max(500, _SQLITE_VAR_LIMIT - 10)
POSITION_LOOKUP_BATCH_SIZE = max(250, (_SQLITE_VAR_LIMIT - 10) // 4)

# Chromosomes we accept (matching 23andMe scope)
VALID_CHROMS = {str(i) for i in range(1, 23)} | {"X", "Y", "MT"}

# gnomAD annotation bitmask bit (bit 2, value 4)
GNOMAD_BITMASK = 0b000100

# Rare variant AF thresholds
RARE_AF_THRESHOLD = 0.01
ULTRA_RARE_AF_THRESHOLD = 0.001

# ── SQL for gnomad_af table creation ──────────────────────────────────────

CREATE_TABLE_SQL = """\
CREATE TABLE IF NOT EXISTS gnomad_af (
    rsid             TEXT PRIMARY KEY,
    chrom            TEXT NOT NULL,
    pos              INTEGER NOT NULL,
    ref              TEXT NOT NULL,
    alt              TEXT NOT NULL,
    af_global        REAL,
    af_afr           REAL,
    af_amr           REAL,
    af_eas           REAL,
    af_eur           REAL,
    af_fin           REAL,
    af_sas           REAL,
    homozygous_count INTEGER DEFAULT 0
)
"""

CREATE_INDEXES_SQL = [
    "CREATE INDEX IF NOT EXISTS idx_gnomad_chrom_pos ON gnomad_af (chrom, pos)",
    "CREATE INDEX IF NOT EXISTS idx_gnomad_chrom_pos_ref_alt ON gnomad_af (chrom, pos, ref, alt)",
]

# Bulk-insert statement (idempotent upsert on the rsid primary key).
_INSERT_GNOMAD_SQL = sa.text(
    "INSERT OR REPLACE INTO gnomad_af "
    "(rsid, chrom, pos, ref, alt, af_global, af_afr, af_amr, "
    "af_eas, af_eur, af_fin, af_sas, homozygous_count) "
    "VALUES (:rsid, :chrom, :pos, :ref, :alt, :af_global, "
    ":af_afr, :af_amr, :af_eas, :af_eur, :af_fin, :af_sas, "
    ":homozygous_count)"
)


# ── Data classes ─────────────────────────────────────────────────────────


@dataclass
class GnomADRecord:
    """A single parsed gnomAD variant record."""

    rsid: str
    chrom: str
    pos: int
    ref: str
    alt: str
    af_global: float | None = None
    af_afr: float | None = None
    af_amr: float | None = None
    af_eas: float | None = None
    af_eur: float | None = None
    af_fin: float | None = None
    af_sas: float | None = None
    homozygous_count: int = 0


@dataclass
class LoadStats:
    """Statistics from a gnomAD load operation."""

    total_lines: int = 0
    variants_loaded: int = 0
    skipped_no_rsid: int = 0
    skipped_invalid_chrom: int = 0
    skipped_malformed: int = 0
    skipped_multiallelic: int = 0
    sha256: str | None = None


@dataclass
class GnomADAnnotation:
    """gnomAD annotation data for a single variant."""

    rsid: str
    af_global: float | None
    af_afr: float | None
    af_amr: float | None
    af_eas: float | None
    af_eur: float | None
    af_fin: float | None
    af_sas: float | None
    homozygous_count: int
    rare_flag: bool
    ultra_rare_flag: bool
    af_popmax: float | None = None


def compute_af_popmax(
    af_global: float | None,
    af_afr: float | None = None,
    af_amr: float | None = None,
    af_eas: float | None = None,
    af_eur: float | None = None,
    af_fin: float | None = None,
    af_sas: float | None = None,
) -> float | None:
    """Compute the population-maximum allele frequency (F15).

    Rarity must be judged on the population where the variant is *most* common,
    not on the global average: a variant can sit at <1% globally yet be common
    in a single ancestry (e.g. afr ≈ 0.11), and global-AF rarity would mislabel
    it "rare". The popmax is the max over all non-null AFs (the per-ancestry
    values plus the global average, so popmax ≥ global); ``None`` only when no
    gnomAD frequency is available at all.

    Returns:
        The maximum non-null allele frequency, or ``None`` if all are null.
    """
    present = [
        af for af in (af_global, af_afr, af_amr, af_eas, af_eur, af_fin, af_sas) if af is not None
    ]
    return max(present) if present else None


def compute_rare_flags(af_popmax: float | None) -> tuple[bool, bool]:
    """Compute rare and ultra-rare boolean flags from the population-max AF (F15).

    Pass the popmax (see :func:`compute_af_popmax`), not the global AF: a variant
    is rare only when it is rare in *every* population, so the most-common-ancestry
    frequency is the correct denominator.

    Args:
        af_popmax: Population-maximum allele frequency from gnomAD.

    Returns:
        Tuple of (rare_flag, ultra_rare_flag).
    """
    if af_popmax is None:
        return False, False
    # AF == 0 means the ALT was never observed in gnomAD — a monomorphic
    # reference site, NOT an observed ultra-rare allele (F26). Treating it as
    # rare/ultra-rare conflates "absent from the cohort" with "vanishingly rare
    # but seen". The rare-variant finder still surfaces a *carried* AF=0 variant
    # via its own AF predicate; these column flags must not mislabel it.
    if af_popmax == 0:
        return False, False
    return af_popmax < RARE_AF_THRESHOLD, af_popmax < ULTRA_RARE_AF_THRESHOLD


# ── Helpers ──────────────────────────────────────────────────────────────


def _normalize_chrom(chrom: str) -> str | None:
    """Normalize chromosome name. Returns None for invalid chromosomes."""
    c = chrom.removeprefix("chr").upper()
    if c in VALID_CHROMS:
        return c
    return None


def _parse_float(value: str | None) -> float | None:
    """Parse a float from a VCF INFO value, returning None on failure."""
    if value is None or value == "." or value == "":
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


def _parse_int(value: str | None) -> int:
    """Parse an int from a VCF INFO value, returning 0 on failure."""
    if value is None or value == "." or value == "":
        return 0
    try:
        return int(value)
    except (ValueError, TypeError):
        return 0


def _parse_info_field(info: str) -> dict[str, str]:
    """Parse a VCF INFO field into a dict of key=value pairs."""
    result: dict[str, str] = {}
    for part in info.split(";"):
        if "=" in part:
            key, _, value = part.partition("=")
            result[key] = value
        else:
            result[part] = ""
    return result


def _compute_sha256(file_path: Path) -> str:
    """Compute SHA-256 checksum of a file."""
    sha = hashlib.sha256()
    with open(file_path, "rb") as f:
        while True:
            chunk = f.read(65536)
            if not chunk:
                break
            sha.update(chunk)
    return sha.hexdigest()


def _wal_checkpoint(engine: sa.Engine) -> None:
    """Run WAL checkpoint if the engine is file-backed."""
    url = str(engine.url)
    if url == "sqlite://" or ":memory:" in url:
        return
    with engine.connect() as conn:
        conn.execute(sa.text("PRAGMA wal_checkpoint(TRUNCATE)"))
        conn.commit()


# ── VCF parsing ──────────────────────────────────────────────────────────


def parse_gnomad_vcf_line(line: str) -> tuple[GnomADRecord | None, str | None]:
    """Parse a single gnomAD VCF data line.

    Returns:
        Tuple of (record, skip_reason). If record is None, skip_reason
        indicates why the line was skipped.
    """
    parts = line.rstrip("\n\r").split("\t")
    if len(parts) < 8:
        return None, "malformed"

    chrom_raw, pos_str, var_id, ref, alt, _qual, _filt, info_str = parts[:8]

    # Normalize chromosome
    chrom = _normalize_chrom(chrom_raw)
    if chrom is None:
        return None, "invalid_chrom"

    # Validate position
    try:
        pos = int(pos_str)
    except (ValueError, TypeError):
        return None, "malformed"

    # Extract rsid from ID column
    rsid: str | None = None
    if var_id and var_id != ".":
        # gnomAD ID column may contain multiple IDs separated by ;
        for vid in var_id.split(";"):
            if vid.startswith("rs"):
                rsid = vid
                break

    if not rsid:
        return None, "no_rsid"

    # Skip multi-allelic (contains comma in ALT)
    if "," in alt:
        return None, "multiallelic"

    # Parse INFO fields for allele frequencies
    info = _parse_info_field(info_str)

    record = GnomADRecord(
        rsid=rsid,
        chrom=chrom,
        pos=pos,
        ref=ref,
        alt=alt,
        af_global=_parse_float(info.get("AF")),
        af_afr=_parse_float(info.get("AF_afr")),
        af_amr=_parse_float(info.get("AF_amr")),
        af_eas=_parse_float(info.get("AF_eas")),
        af_eur=_parse_float(info.get("AF_nfe")),
        af_fin=_parse_float(info.get("AF_fin")),
        af_sas=_parse_float(info.get("AF_sas")),
        homozygous_count=_parse_int(info.get("nhomalt")),
    )

    return record, None


def iter_gnomad_vcf(
    vcf_path: Path,
    *,
    progress_callback: Callable[[int], None] | None = None,
) -> Iterator[tuple[dict, LoadStats]]:
    """Iterate over gnomAD VCF rows lazily, yielding (row_dict, stats).

    Memory-efficient: yields one row at a time for streaming inserts.

    Args:
        vcf_path: Path to the VCF or VCF.gz / .bgz file.
        progress_callback: Optional callback called with parsed line count
            at regular intervals.

    Yields:
        Tuple of (row dict ready for insert, running LoadStats).
    """
    stats = LoadStats()

    open_fn = gzip.open if vcf_path.suffix in (".gz", ".bgz") else open
    with open_fn(vcf_path, "rt", encoding="utf-8") as fh:  # type: ignore[call-overload]
        for line in fh:
            if line.startswith("#"):
                continue

            stats.total_lines += 1

            record, skip_reason = parse_gnomad_vcf_line(line)

            if record is None:
                if skip_reason == "no_rsid":
                    stats.skipped_no_rsid += 1
                elif skip_reason == "invalid_chrom":
                    stats.skipped_invalid_chrom += 1
                elif skip_reason == "multiallelic":
                    stats.skipped_multiallelic += 1
                else:
                    stats.skipped_malformed += 1
                continue

            stats.variants_loaded += 1

            row = {
                "rsid": record.rsid,
                "chrom": record.chrom,
                "pos": record.pos,
                "ref": record.ref,
                "alt": record.alt,
                "af_global": record.af_global,
                "af_afr": record.af_afr,
                "af_amr": record.af_amr,
                "af_eas": record.af_eas,
                "af_eur": record.af_eur,
                "af_fin": record.af_fin,
                "af_sas": record.af_sas,
                "homozygous_count": record.homozygous_count,
            }

            if progress_callback and stats.total_lines % 100_000 == 0:
                progress_callback(stats.total_lines)

            yield row, stats


# ── Database creation & loading ──────────────────────────────────────────


def _create_gnomad_table(engine: sa.Engine) -> None:
    """Create only the gnomad_af table (no indexes). Safe to call repeatedly."""
    with engine.begin() as conn:
        conn.execute(sa.text(CREATE_TABLE_SQL))


def _create_gnomad_indexes(engine: sa.Engine) -> None:
    """Create the gnomad_af indexes (idempotent). Retries on lock contention.

    The load path defers index creation to after the bulk insert so the indexes
    are built once over a fully populated table rather than maintained per-row.
    """

    def _do() -> None:
        with engine.begin() as conn:
            for idx_sql in CREATE_INDEXES_SQL:
                conn.execute(sa.text(idx_sql))

    retry_on_locked(_do)


def load_gnomad_from_vcf(
    vcf_path: Path,
    engine: sa.Engine,
    *,
    clear_existing: bool = True,
    progress_callback: Callable[[int], None] | None = None,
) -> LoadStats:
    """Parse a gnomAD VCF and load AF data into the gnomad_af table.

    Uses streaming parse + batch insert to keep memory usage low.

    Args:
        vcf_path: Path to the gnomAD VCF (.vcf.gz or .bgz).
        engine: SQLAlchemy engine for gnomad_af.db.
        clear_existing: Whether to DELETE all existing rows first.
        progress_callback: Called with parsed line count at intervals.

    Returns:
        LoadStats with counts and metadata.
    """
    # Create the table only; indexes are built once after the bulk insert.
    _create_gnomad_table(engine)

    batch: list[dict] = []
    final_stats = LoadStats()

    with bulk_write_connection(engine) as conn:
        if clear_existing:
            execute_write(conn, sa.text("DELETE FROM gnomad_af"))

        for row, final_stats in iter_gnomad_vcf(vcf_path, progress_callback=progress_callback):
            batch.append(row)

            if len(batch) >= BATCH_SIZE:
                insert_batch(conn, _INSERT_GNOMAD_SQL, batch)
                batch = []

        # Flush remaining
        if batch:
            insert_batch(conn, _INSERT_GNOMAD_SQL, batch)

    # Build indexes over the populated table, then truncate the WAL.
    _create_gnomad_indexes(engine)
    _wal_checkpoint(engine)

    logger.info(
        "gnomad_loaded",
        variants=final_stats.variants_loaded,
        skipped_no_rsid=final_stats.skipped_no_rsid,
        skipped_invalid_chrom=final_stats.skipped_invalid_chrom,
        skipped_multiallelic=final_stats.skipped_multiallelic,
    )

    return final_stats


def load_gnomad_from_csv(
    csv_path: Path,
    engine: sa.Engine,
    *,
    clear_existing: bool = True,
) -> LoadStats:
    """Seed the ``gnomad_af`` table from a small CSV fixture — TEST SUPPORT ONLY.

    CSV is **not** a production or bundle-build input format: the real pipeline
    loads gnomAD from its native VCF via :func:`load_gnomad_from_vcf` (see
    ``scripts/build_gnomad_bundle.py``). This loader exists solely so tests can
    seed the table from a compact CSV fixture instead of standing up the full
    VCF machinery; it is on no production/build path.

    Args:
        csv_path: Path to the CSV fixture with gnomAD data.
        engine: SQLAlchemy engine for gnomad_af.db.
        clear_existing: Whether to DELETE all existing rows first.

    Returns:
        LoadStats with counts.
    """
    # Create the table only; indexes are built once after the bulk insert.
    _create_gnomad_table(engine)

    stats = LoadStats()
    batch: list[dict] = []

    with bulk_write_connection(engine) as conn:
        if clear_existing:
            execute_write(conn, sa.text("DELETE FROM gnomad_af"))

        with open(csv_path, encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                stats.total_lines += 1
                batch.append(
                    {
                        "rsid": row["rsid"],
                        "chrom": row["chrom"],
                        "pos": int(row["pos"]),
                        "ref": row["ref"],
                        "alt": row["alt"],
                        "af_global": _parse_float(row.get("af_global")),
                        "af_afr": _parse_float(row.get("af_afr")),
                        "af_amr": _parse_float(row.get("af_amr")),
                        "af_eas": _parse_float(row.get("af_eas")),
                        "af_eur": _parse_float(row.get("af_eur")),
                        "af_fin": _parse_float(row.get("af_fin")),
                        "af_sas": _parse_float(row.get("af_sas")),
                        "homozygous_count": _parse_int(row.get("homozygous_count")),
                    }
                )
                stats.variants_loaded += 1

                if len(batch) >= BATCH_SIZE:
                    insert_batch(conn, _INSERT_GNOMAD_SQL, batch)
                    batch = []

        if batch:
            insert_batch(conn, _INSERT_GNOMAD_SQL, batch)

    _create_gnomad_indexes(engine)
    _wal_checkpoint(engine)

    logger.info("gnomad_csv_loaded", variants=stats.variants_loaded)
    return stats


# ── Download ─────────────────────────────────────────────────────────────


def download_gnomad_vcf(
    dest_dir: Path,
    *,
    url: str = GNOMAD_VCF_URL,
    progress_callback: Callable[[int, int | None], None] | None = None,
    timeout: float = 3600.0,
) -> Path:
    """Download the gnomAD exomes sites VCF.

    Writes to a temporary file and renames on success to avoid
    leaving partial files on failure.

    Args:
        dest_dir: Directory to save the downloaded file.
        url: Override URL (useful for testing).
        progress_callback: Called with (bytes_downloaded, total_bytes).
        timeout: HTTP request timeout in seconds (default 1h for large file).

    Returns:
        Path to the downloaded VCF file.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_path = dest_dir / "gnomad_exomes_r2.1.1.vcf.bgz"
    tmp_path = dest_dir / "gnomad_exomes_r2.1.1.vcf.bgz.tmp"

    logger.info("gnomad_download_start", url=url)

    outcome = stream_download(
        url,
        tmp_path,
        progress_callback=progress_callback,
        timeout=timeout,
    )

    # Atomic rename on success (stream_download cleans up the .tmp on failure).
    tmp_path.replace(dest_path)

    logger.info("gnomad_download_complete", path=str(dest_path), bytes=outcome.total_bytes)
    return dest_path


# ── Annotation lookup ────────────────────────────────────────────────────


def lookup_gnomad_by_rsids(
    rsids: list[str],
    gnomad_engine: sa.Engine,
) -> dict[str, GnomADAnnotation]:
    """Look up gnomAD allele frequencies for a batch of rsids.

    Processes in batches of 500 to stay under SQLite's 999-variable limit.

    Args:
        rsids: List of rsid strings (e.g. ["rs429358", "rs7412"]).
        gnomad_engine: SQLAlchemy engine for gnomad_af.db.

    Returns:
        Dict mapping rsid → GnomADAnnotation for matched variants.
    """
    if not rsids:
        return {}

    results: dict[str, GnomADAnnotation] = {}

    with gnomad_engine.connect() as conn:
        for i in range(0, len(rsids), LOOKUP_BATCH_SIZE):
            batch = rsids[i : i + LOOKUP_BATCH_SIZE]
            placeholders = ", ".join(f":r{j}" for j in range(len(batch)))
            params = {f"r{j}": rsid for j, rsid in enumerate(batch)}

            stmt = sa.text(
                "SELECT rsid, af_global, af_afr, af_amr, af_eas, af_eur, "  # noqa: S608
                f"af_fin, af_sas, homozygous_count FROM gnomad_af WHERE rsid IN ({placeholders})"
            )
            rows = conn.execute(stmt, params).fetchall()

            for row in rows:
                popmax = compute_af_popmax(
                    row.af_global,
                    row.af_afr,
                    row.af_amr,
                    row.af_eas,
                    row.af_eur,
                    row.af_fin,
                    row.af_sas,
                )
                rare, ultra_rare = compute_rare_flags(popmax)
                results[row.rsid] = GnomADAnnotation(
                    rsid=row.rsid,
                    af_global=row.af_global,
                    af_afr=row.af_afr,
                    af_amr=row.af_amr,
                    af_eas=row.af_eas,
                    af_eur=row.af_eur,
                    af_fin=row.af_fin,
                    af_sas=row.af_sas,
                    homozygous_count=row.homozygous_count or 0,
                    rare_flag=rare,
                    ultra_rare_flag=ultra_rare,
                    af_popmax=popmax,
                )

    return results


def lookup_gnomad_by_positions(
    positions: list[tuple[str, int, str, str]],
    gnomad_engine: sa.Engine,
) -> dict[tuple[str, int, str, str], GnomADAnnotation]:
    """Look up gnomAD annotations by (chrom, pos, ref, alt).

    Fallback strategy when rsid matching fails. Uses the composite
    index on (chrom, pos, ref, alt) for efficient lookups.

    Args:
        positions: List of (chrom, pos, ref, alt) tuples.
        gnomad_engine: SQLAlchemy engine for gnomad_af.db.

    Returns:
        Dict mapping (chrom, pos, ref, alt) → GnomADAnnotation.
    """
    if not positions:
        return {}

    results: dict[tuple[str, int, str, str], GnomADAnnotation] = {}

    with gnomad_engine.connect() as conn:
        for i in range(0, len(positions), POSITION_LOOKUP_BATCH_SIZE):
            batch = positions[i : i + POSITION_LOOKUP_BATCH_SIZE]

            # Build OR conditions for (chrom, pos, ref, alt) tuples
            conditions = []
            params: dict[str, str | int] = {}
            for j, (chrom, pos, ref, alt) in enumerate(batch):
                conditions.append(
                    f"(chrom = :c{j} AND pos = :p{j} AND ref = :r{j} AND alt = :a{j})"
                )
                params[f"c{j}"] = chrom
                params[f"p{j}"] = pos
                params[f"r{j}"] = ref
                params[f"a{j}"] = alt

            where_clause = " OR ".join(conditions)
            stmt = sa.text(
                "SELECT rsid, chrom, pos, ref, alt, af_global, af_afr, af_amr, "  # noqa: S608
                "af_eas, af_eur, af_fin, af_sas, homozygous_count "
                f"FROM gnomad_af WHERE {where_clause}"
            )
            rows = conn.execute(stmt, params).fetchall()

            for row in rows:
                popmax = compute_af_popmax(
                    row.af_global,
                    row.af_afr,
                    row.af_amr,
                    row.af_eas,
                    row.af_eur,
                    row.af_fin,
                    row.af_sas,
                )
                rare, ultra_rare = compute_rare_flags(popmax)
                key = (row.chrom, row.pos, row.ref, row.alt)
                results[key] = GnomADAnnotation(
                    rsid=row.rsid,
                    af_global=row.af_global,
                    af_afr=row.af_afr,
                    af_amr=row.af_amr,
                    af_eas=row.af_eas,
                    af_eur=row.af_eur,
                    af_fin=row.af_fin,
                    af_sas=row.af_sas,
                    homozygous_count=row.homozygous_count or 0,
                    rare_flag=rare,
                    ultra_rare_flag=ultra_rare,
                    af_popmax=popmax,
                )

    return results

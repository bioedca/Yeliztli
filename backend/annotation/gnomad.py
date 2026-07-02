"""gnomAD allele-frequency SQLite index builder and annotation lookup.

Downloads the gnomAD r2.1.1 exomes sites VCF, extracts allele frequency
and observed allele count fields per population, and builds an indexed SQLite database
(``gnomad_af.db``).  Also provides batch lookup functions used by the
annotation engine.

The ``gnomad_af`` table stores one row per alternate allele with columns:
rsid, chrom, pos, ref, alt, AF and AN fields per population, and
homozygous_count.

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

GNOMAD_AN_INFO_KEYS = (
    "AN",
    "AN_afr",
    "AN_amr",
    "AN_asj",
    "AN_eas",
    "AN_nfe",
    "AN_fin",
    "AN_sas",
)

# ── SQL for gnomad_af table creation ──────────────────────────────────────

CREATE_TABLE_SQL = """\
CREATE TABLE IF NOT EXISTS gnomad_af (
    rsid             TEXT,
    chrom            TEXT NOT NULL,
    pos              INTEGER NOT NULL,
    ref              TEXT NOT NULL,
    alt              TEXT NOT NULL,
    af_global        REAL,
    af_afr           REAL,
    af_amr           REAL,
    af_asj           REAL,
    af_eas           REAL,
    af_eur           REAL,
    af_fin           REAL,
    af_sas           REAL,
    an_global        INTEGER,
    an_afr           INTEGER,
    an_amr           INTEGER,
    an_asj           INTEGER,
    an_eas           INTEGER,
    an_eur           INTEGER,
    an_fin           INTEGER,
    an_sas           INTEGER,
    homozygous_count INTEGER DEFAULT 0,
    PRIMARY KEY (chrom, pos, ref, alt)
)
"""

CREATE_INDEXES_SQL = [
    "CREATE INDEX IF NOT EXISTS idx_gnomad_rsid ON gnomad_af (rsid)",
    "CREATE INDEX IF NOT EXISTS idx_gnomad_chrom_pos ON gnomad_af (chrom, pos)",
    "CREATE INDEX IF NOT EXISTS idx_gnomad_chrom_pos_ref_alt ON gnomad_af (chrom, pos, ref, alt)",
]

# Bulk-insert statement (idempotent upsert on the coordinate/allele primary key).
_INSERT_GNOMAD_SQL = sa.text(
    "INSERT OR REPLACE INTO gnomad_af "
    "(rsid, chrom, pos, ref, alt, af_global, af_afr, af_amr, "
    "af_asj, af_eas, af_eur, af_fin, af_sas, an_global, an_afr, "
    "an_amr, an_asj, an_eas, an_eur, an_fin, an_sas, homozygous_count) "
    "VALUES (:rsid, :chrom, :pos, :ref, :alt, :af_global, "
    ":af_afr, :af_amr, :af_asj, :af_eas, :af_eur, :af_fin, :af_sas, "
    ":an_global, :an_afr, :an_amr, :an_asj, :an_eas, :an_eur, :an_fin, "
    ":an_sas, :homozygous_count)"
)


# ── Data classes ─────────────────────────────────────────────────────────


@dataclass
class GnomADRecord:
    """A single parsed gnomAD variant record."""

    rsid: str | None
    chrom: str
    pos: int
    ref: str
    alt: str
    af_global: float | None = None
    af_afr: float | None = None
    af_amr: float | None = None
    af_asj: float | None = None
    af_eas: float | None = None
    af_eur: float | None = None
    af_fin: float | None = None
    af_sas: float | None = None
    an_global: int | None = None
    an_afr: int | None = None
    an_amr: int | None = None
    an_asj: int | None = None
    an_eas: int | None = None
    an_eur: int | None = None
    an_fin: int | None = None
    an_sas: int | None = None
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
    multiallelic_sites_split: int = 0
    multiallelic_records_loaded: int = 0
    sha256: str | None = None


@dataclass
class GnomADAnnotation:
    """gnomAD annotation data for a single variant."""

    rsid: str | None
    af_global: float | None
    af_afr: float | None
    af_amr: float | None
    af_asj: float | None
    af_eas: float | None
    af_eur: float | None
    af_fin: float | None
    af_sas: float | None
    homozygous_count: int
    rare_flag: bool
    ultra_rare_flag: bool
    af_popmax: float | None = None
    an_global: int | None = None
    an_afr: int | None = None
    an_amr: int | None = None
    an_asj: int | None = None
    an_eas: int | None = None
    an_eur: int | None = None
    an_fin: int | None = None
    an_sas: int | None = None
    an_popmax: int | None = None


def compute_af_popmax_with_an(
    af_global: float | None,
    af_afr: float | None = None,
    af_amr: float | None = None,
    af_eas: float | None = None,
    af_eur: float | None = None,
    af_fin: float | None = None,
    af_sas: float | None = None,
    af_asj: float | None = None,
    *,
    an_global: int | None = None,
    an_afr: int | None = None,
    an_amr: int | None = None,
    an_eas: int | None = None,
    an_eur: int | None = None,
    an_fin: int | None = None,
    an_sas: int | None = None,
    an_asj: int | None = None,
) -> tuple[float | None, int | None]:
    """Compute popmax AF and the observed-allele count for that population.

    The order mirrors :func:`compute_af_popmax`'s historical tie-breaking so
    existing popmax behavior is unchanged; the added return value simply carries
    the AN paired with the selected AF.
    """
    pairs = [
        (af_global, an_global),
        (af_afr, an_afr),
        (af_amr, an_amr),
        (af_eas, an_eas),
        (af_eur, an_eur),
        (af_fin, an_fin),
        (af_sas, an_sas),
        (af_asj, an_asj),
    ]
    best_af: float | None = None
    best_an: int | None = None
    for af, an in pairs:
        if af is None:
            continue
        if best_af is None or af > best_af:
            best_af = af
            best_an = an
    return best_af, best_an


def compute_af_popmax(
    af_global: float | None,
    af_afr: float | None = None,
    af_amr: float | None = None,
    af_eas: float | None = None,
    af_eur: float | None = None,
    af_fin: float | None = None,
    af_sas: float | None = None,
    af_asj: float | None = None,
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
    af_popmax, _ = compute_af_popmax_with_an(
        af_global, af_afr, af_amr, af_eas, af_eur, af_fin, af_sas, af_asj
    )
    return af_popmax


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


def _parse_optional_int(value: str | None) -> int | None:
    """Parse an optional int from a VCF INFO value."""
    if value is None or value == "." or value == "":
        return None
    try:
        return int(value)
    except (ValueError, TypeError):
        return None


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


def _extract_rsids(var_id: str) -> list[str]:
    """Extract rsIDs from a VCF ID field, preserving their listed order."""
    if not var_id or var_id == ".":
        return []
    return [vid for vid in var_id.split(";") if vid.startswith("rs")]


def _rsid_for_alt(rsids: list[str], alt_index: int, alt_count: int) -> str | None:
    """Choose the best rsID available for an ALT-specific row.

    gnomAD's VCF ID column is site-level, not a structured per-ALT field. When
    the number of rsIDs matches the number of ALTs, keep that order; otherwise
    use the first rsID and let the coordinate primary key disambiguate rows.
    """
    if not rsids:
        return None
    if len(rsids) == alt_count:
        return rsids[alt_index]
    return rsids[0]


def _info_value_for_alt(
    info: dict[str, str],
    key: str,
    alt_index: int,
    alt_count: int,
) -> str | None:
    """Return an ALT-specific INFO value.

    gnomAD frequency/count fields are Number=A in the source VCF: a multi-ALT
    row stores comma-indexed values aligned to the ALT field. If a multi-ALT row
    has a mismatched value count, treat that field as missing rather than copying
    one allele's value to another allele.
    """
    value = info.get(key)
    if value is None or value == "" or value == ".":
        return value
    values = value.split(",")
    if alt_count == 1:
        return value
    if len(values) == alt_count:
        return values[alt_index]
    return None


def _info_site_or_alt_value_for_alt(
    info: dict[str, str],
    key: str,
    alt_index: int,
    alt_count: int,
) -> str | None:
    """Return a site-level or ALT-specific INFO value for an ALT row.

    gnomAD AN fields are site/population denominators in some VCF releases but
    may appear as comma-aligned fields in derived fixtures. A single value is
    valid for every ALT; a value list must match the ALT count.
    """
    value = info.get(key)
    if value is None or value == "" or value == ".":
        return value
    values = value.split(",")
    if len(values) == 1:
        return value
    if len(values) == alt_count:
        return values[alt_index]
    return None


def _warn_site_or_alt_value_count_mismatch(
    info: dict[str, str],
    key: str,
    alt_count: int,
) -> None:
    """Warn when a site-level-or-ALT-aligned INFO value is neither shape."""
    value = info.get(key)
    if value is None or value == "" or value == ".":
        return
    value_count = len(value.split(","))
    if value_count not in (1, alt_count):
        logger.warning(
            "gnomad_info_value_count_mismatch",
            field=key,
            alt_count=alt_count,
            value_count=value_count,
        )


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


def parse_gnomad_vcf_records(line: str) -> tuple[list[GnomADRecord], str | None]:
    """Parse one gnomAD VCF data line into one record per ALT allele.

    Returns:
        Tuple of (records, skip_reason). If records is empty, skip_reason
        indicates why the line was skipped.
    """
    parts = line.rstrip("\n\r").split("\t")
    if len(parts) < 8:
        return [], "malformed"

    chrom_raw, pos_str, var_id, ref, alt, _qual, _filt, info_str = parts[:8]

    # Normalize chromosome
    chrom = _normalize_chrom(chrom_raw)
    if chrom is None:
        return [], "invalid_chrom"

    # Validate position
    try:
        pos = int(pos_str)
    except (ValueError, TypeError):
        return [], "malformed"

    # Extract optional rsID from the ID column. gnomAD rows are keyed by
    # coordinates; many rare/recent variants have no dbSNP rsID yet.
    rsids = _extract_rsids(var_id)

    alts = alt.split(",")
    if any(not allele for allele in alts):
        return [], "malformed"

    # Parse INFO fields for allele frequencies
    info = _parse_info_field(info_str)
    alt_count = len(alts)
    for an_key in GNOMAD_AN_INFO_KEYS:
        _warn_site_or_alt_value_count_mismatch(info, an_key, alt_count)

    records = [
        GnomADRecord(
            rsid=_rsid_for_alt(rsids, alt_index, alt_count),
            chrom=chrom,
            pos=pos,
            ref=ref,
            alt=alt_allele,
            af_global=_parse_float(_info_value_for_alt(info, "AF", alt_index, alt_count)),
            af_afr=_parse_float(_info_value_for_alt(info, "AF_afr", alt_index, alt_count)),
            af_amr=_parse_float(_info_value_for_alt(info, "AF_amr", alt_index, alt_count)),
            af_asj=_parse_float(_info_value_for_alt(info, "AF_asj", alt_index, alt_count)),
            af_eas=_parse_float(_info_value_for_alt(info, "AF_eas", alt_index, alt_count)),
            af_eur=_parse_float(_info_value_for_alt(info, "AF_nfe", alt_index, alt_count)),
            af_fin=_parse_float(_info_value_for_alt(info, "AF_fin", alt_index, alt_count)),
            af_sas=_parse_float(_info_value_for_alt(info, "AF_sas", alt_index, alt_count)),
            an_global=_parse_optional_int(
                _info_site_or_alt_value_for_alt(info, "AN", alt_index, alt_count)
            ),
            an_afr=_parse_optional_int(
                _info_site_or_alt_value_for_alt(info, "AN_afr", alt_index, alt_count)
            ),
            an_amr=_parse_optional_int(
                _info_site_or_alt_value_for_alt(info, "AN_amr", alt_index, alt_count)
            ),
            an_asj=_parse_optional_int(
                _info_site_or_alt_value_for_alt(info, "AN_asj", alt_index, alt_count)
            ),
            an_eas=_parse_optional_int(
                _info_site_or_alt_value_for_alt(info, "AN_eas", alt_index, alt_count)
            ),
            an_eur=_parse_optional_int(
                _info_site_or_alt_value_for_alt(info, "AN_nfe", alt_index, alt_count)
            ),
            an_fin=_parse_optional_int(
                _info_site_or_alt_value_for_alt(info, "AN_fin", alt_index, alt_count)
            ),
            an_sas=_parse_optional_int(
                _info_site_or_alt_value_for_alt(info, "AN_sas", alt_index, alt_count)
            ),
            homozygous_count=_parse_int(
                _info_value_for_alt(info, "nhomalt", alt_index, alt_count)
            ),
        )
        for alt_index, alt_allele in enumerate(alts)
    ]

    return records, None


def parse_gnomad_vcf_line(line: str) -> tuple[GnomADRecord | None, str | None]:
    """Parse a gnomAD VCF data line and return the first ALT record.

    This compatibility wrapper is for callers/tests that expect a single record.
    The loader uses :func:`parse_gnomad_vcf_records` so multi-allelic rows are
    preserved as one record per ALT allele.
    """
    records, skip_reason = parse_gnomad_vcf_records(line)
    if not records:
        return None, skip_reason
    return records[0], skip_reason


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

            records, skip_reason = parse_gnomad_vcf_records(line)

            if not records:
                if skip_reason == "no_rsid":
                    stats.skipped_no_rsid += 1
                elif skip_reason == "invalid_chrom":
                    stats.skipped_invalid_chrom += 1
                elif skip_reason == "multiallelic":
                    stats.skipped_multiallelic += 1
                else:
                    stats.skipped_malformed += 1
                continue

            stats.variants_loaded += len(records)
            if len(records) > 1:
                stats.multiallelic_sites_split += 1
                stats.multiallelic_records_loaded += len(records)

            if progress_callback and stats.total_lines % 100_000 == 0:
                progress_callback(stats.total_lines)

            for record in records:
                row = {
                    "rsid": record.rsid,
                    "chrom": record.chrom,
                    "pos": record.pos,
                    "ref": record.ref,
                    "alt": record.alt,
                    "af_global": record.af_global,
                    "af_afr": record.af_afr,
                    "af_amr": record.af_amr,
                    "af_asj": record.af_asj,
                    "af_eas": record.af_eas,
                    "af_eur": record.af_eur,
                    "af_fin": record.af_fin,
                    "af_sas": record.af_sas,
                    "an_global": record.an_global,
                    "an_afr": record.an_afr,
                    "an_amr": record.an_amr,
                    "an_asj": record.an_asj,
                    "an_eas": record.an_eas,
                    "an_eur": record.an_eur,
                    "an_fin": record.an_fin,
                    "an_sas": record.an_sas,
                    "homozygous_count": record.homozygous_count,
                }

                yield row, stats


# ── Database creation & loading ──────────────────────────────────────────


def _create_gnomad_table(engine: sa.Engine, *, recreate_legacy_rsid_pk: bool = False) -> None:
    """Create only the gnomad_af table (no indexes). Safe to call repeatedly."""
    with engine.begin() as conn:
        if recreate_legacy_rsid_pk and (
            _gnomad_table_primary_key(conn) == ("rsid",) or _gnomad_rsid_is_not_null(conn)
        ):
            conn.execute(sa.text("DROP TABLE gnomad_af"))
        elif _gnomad_rsid_is_not_null(conn):
            raise RuntimeError(
                "Existing gnomad_af table has rsid NOT NULL; reload with clear_existing=True "
                "or migrate the table before loading nullable rsid rows."
            )
        conn.execute(sa.text(CREATE_TABLE_SQL))
        existing_cols = _gnomad_table_columns(conn)
        for col_name, col_type in (
            ("af_asj", "REAL"),
            ("an_global", "INTEGER"),
            ("an_afr", "INTEGER"),
            ("an_amr", "INTEGER"),
            ("an_asj", "INTEGER"),
            ("an_eas", "INTEGER"),
            ("an_eur", "INTEGER"),
            ("an_fin", "INTEGER"),
            ("an_sas", "INTEGER"),
        ):
            if col_name not in existing_cols:
                conn.execute(sa.text(f"ALTER TABLE gnomad_af ADD COLUMN {col_name} {col_type}"))


def _gnomad_table_primary_key(conn: sa.Connection) -> tuple[str, ...]:
    """Return the local gnomad_af primary-key columns in key order."""
    rows = conn.execute(sa.text("PRAGMA table_info(gnomad_af)")).fetchall()
    return tuple(name for _pk_order, name in sorted((row[5], row[1]) for row in rows if row[5]))


def _gnomad_table_columns(conn: sa.Connection) -> set[str]:
    """Return column names for the local gnomad_af table."""
    return {row[1] for row in conn.execute(sa.text("PRAGMA table_info(gnomad_af)"))}


def _gnomad_rsid_is_not_null(conn: sa.Connection) -> bool:
    """Return whether the local gnomad_af.rsid column still has NOT NULL."""
    rows = conn.execute(sa.text("PRAGMA table_info(gnomad_af)")).fetchall()
    return any(row[1] == "rsid" and bool(row[3]) for row in rows)


def _gnomad_af_select_sql(conn: sa.Connection) -> str:
    """Return AF/AN select list, tolerating older read-only bundles."""
    cols = _gnomad_table_columns(conn)

    def _select_col(name: str) -> str:
        return name if name in cols else f"NULL AS {name}"

    return ", ".join(
        [
            _select_col("af_global"),
            _select_col("af_afr"),
            _select_col("af_amr"),
            _select_col("af_asj"),
            _select_col("af_eas"),
            _select_col("af_eur"),
            _select_col("af_fin"),
            _select_col("af_sas"),
            _select_col("an_global"),
            _select_col("an_afr"),
            _select_col("an_amr"),
            _select_col("an_asj"),
            _select_col("an_eas"),
            _select_col("an_eur"),
            _select_col("an_fin"),
            _select_col("an_sas"),
        ]
    )


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
    _create_gnomad_table(engine, recreate_legacy_rsid_pk=clear_existing)

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
        multiallelic_sites_split=final_stats.multiallelic_sites_split,
        multiallelic_records_loaded=final_stats.multiallelic_records_loaded,
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
    _create_gnomad_table(engine, recreate_legacy_rsid_pk=clear_existing)

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
                        "rsid": row.get("rsid") or None,
                        "chrom": row["chrom"],
                        "pos": int(row["pos"]),
                        "ref": row["ref"],
                        "alt": row["alt"],
                        "af_global": _parse_float(row.get("af_global")),
                        "af_afr": _parse_float(row.get("af_afr")),
                        "af_amr": _parse_float(row.get("af_amr")),
                        "af_asj": _parse_float(row.get("af_asj")),
                        "af_eas": _parse_float(row.get("af_eas")),
                        "af_eur": _parse_float(row.get("af_eur")),
                        "af_fin": _parse_float(row.get("af_fin")),
                        "af_sas": _parse_float(row.get("af_sas")),
                        "an_global": _parse_optional_int(row.get("an_global")),
                        "an_afr": _parse_optional_int(row.get("an_afr")),
                        "an_amr": _parse_optional_int(row.get("an_amr")),
                        "an_asj": _parse_optional_int(row.get("an_asj")),
                        "an_eas": _parse_optional_int(row.get("an_eas")),
                        "an_eur": _parse_optional_int(row.get("an_eur")),
                        "an_fin": _parse_optional_int(row.get("an_fin")),
                        "an_sas": _parse_optional_int(row.get("an_sas")),
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


def _annotation_from_row(row: sa.Row) -> GnomADAnnotation:
    """Build a lookup annotation from a gnomAD result row."""
    popmax, an_popmax = compute_af_popmax_with_an(
        row.af_global,
        row.af_afr,
        row.af_amr,
        row.af_eas,
        row.af_eur,
        row.af_fin,
        row.af_sas,
        af_asj=row.af_asj,
        an_global=row.an_global,
        an_afr=row.an_afr,
        an_amr=row.an_amr,
        an_eas=row.an_eas,
        an_eur=row.an_eur,
        an_fin=row.an_fin,
        an_sas=row.an_sas,
        an_asj=row.an_asj,
    )
    rare, ultra_rare = compute_rare_flags(popmax)
    return GnomADAnnotation(
        rsid=row.rsid,
        af_global=row.af_global,
        af_afr=row.af_afr,
        af_amr=row.af_amr,
        af_asj=row.af_asj,
        af_eas=row.af_eas,
        af_eur=row.af_eur,
        af_fin=row.af_fin,
        af_sas=row.af_sas,
        homozygous_count=row.homozygous_count or 0,
        rare_flag=rare,
        ultra_rare_flag=ultra_rare,
        af_popmax=popmax,
        an_global=row.an_global,
        an_afr=row.an_afr,
        an_amr=row.an_amr,
        an_asj=row.an_asj,
        an_eas=row.an_eas,
        an_eur=row.an_eur,
        an_fin=row.an_fin,
        an_sas=row.an_sas,
        an_popmax=an_popmax,
    )


def _annotation_rank(annot: GnomADAnnotation) -> float:
    """Rank ambiguous rsID-only hits by conservative popmax."""
    return -1.0 if annot.af_popmax is None else annot.af_popmax


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
        af_select = _gnomad_af_select_sql(conn)
        for i in range(0, len(rsids), LOOKUP_BATCH_SIZE):
            batch = rsids[i : i + LOOKUP_BATCH_SIZE]
            placeholders = ", ".join(f":r{j}" for j in range(len(batch)))
            params = {f"r{j}": rsid for j, rsid in enumerate(batch)}

            stmt = sa.text(
                "SELECT rsid, "  # noqa: S608
                f"{af_select}, homozygous_count FROM gnomad_af WHERE rsid IN ({placeholders}) "
                "ORDER BY rsid, chrom, pos, ref, alt"
            )
            rows = conn.execute(stmt, params).fetchall()

            for row in rows:
                annot = _annotation_from_row(row)
                current = results.get(row.rsid)
                if current is None or _annotation_rank(annot) > _annotation_rank(current):
                    results[row.rsid] = annot

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
        af_select = _gnomad_af_select_sql(conn)
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
                "SELECT rsid, chrom, pos, ref, alt, "  # noqa: S608
                f"{af_select}, homozygous_count "
                f"FROM gnomad_af WHERE {where_clause}"
            )
            rows = conn.execute(stmt, params).fetchall()

            for row in rows:
                key = (row.chrom, row.pos, row.ref, row.alt)
                results[key] = _annotation_from_row(row)

    return results

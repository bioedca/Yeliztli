"""ClinVar VCF downloader, SQLite loader, and annotation lookup.

Downloads the ClinVar VCF (GRCh37) from NCBI FTP, parses variant records,
and bulk-loads them into the ``clinvar_variants`` table in reference.db.

Also provides annotation lookup: given a sample's raw variants, matches
them against clinvar_variants by rsid (primary) and (chrom, pos) fallback,
then writes ClinVar columns into the annotated_variants table.

Usage::

    from backend.annotation.clinvar import download_clinvar_vcf, load_clinvar_vcf
    from backend.annotation.clinvar import annotate_sample_clinvar

    vcf_path = download_clinvar_vcf(dest_dir)
    stats = load_clinvar_vcf(vcf_path, engine)

    # Annotation lookup
    result = annotate_sample_clinvar(sample_engine, reference_engine)
"""

from __future__ import annotations

import gzip
import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from itertools import islice
from pathlib import Path
from typing import TYPE_CHECKING

import httpx
import sqlalchemy as sa
import structlog
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from backend.analysis.zygosity import CARRIED_ZYGOSITIES, classify_zygosity
from backend.annotation.http_download import (
    clear_validator_sidecar,
    read_validator_sidecar,
    stream_download,
    write_validator_sidecar,
)
from backend.db.tables import annotated_variants, clinvar_variants, raw_variants

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

logger = structlog.get_logger(__name__)

# NCBI FTP URL for ClinVar VCF (GRCh37/hg19)
CLINVAR_VCF_URL = "https://ftp.ncbi.nlm.nih.gov/pub/clinvar/vcf_GRCh37/clinvar.vcf.gz"

# Batch size for bulk inserts (executemany)
BATCH_SIZE = 10_000

# Map ClinVar CLNREVSTAT values to review star counts
REVIEW_STATUS_STARS: dict[str, int] = {
    "practice_guideline": 4,
    "reviewed_by_expert_panel": 3,
    "criteria_provided,_multiple_submitters,_no_conflicts": 2,
    "criteria_provided,_single_submitter": 1,
    "criteria_provided,_conflicting_interpretations": 1,
    "criteria_provided,_conflicting_classifications": 1,
    "no_assertion_criteria_provided": 0,
    "no_assertion_provided": 0,
    "no_classification_provided": 0,
    "no_classification_for_the_single_variant": 0,
}

# Chromosomes we accept (matching 23andMe scope)
VALID_CHROMS = {str(i) for i in range(1, 23)} | {"X", "Y", "MT"}


class SkipReason:
    """Enum-like constants for why a VCF line was skipped."""

    NO_RSID = "no_rsid"
    INVALID_CHROM = "invalid_chrom"
    MALFORMED = "malformed"


@dataclass
class LoadStats:
    """Statistics from a ClinVar VCF load operation."""

    total_lines: int = 0
    variants_loaded: int = 0
    skipped_no_rsid: int = 0
    skipped_invalid_chrom: int = 0
    skipped_malformed: int = 0
    file_date: str | None = None
    sha256: str | None = None


@dataclass
class ClinVarRecord:
    """A single parsed ClinVar variant record."""

    rsid: str
    chrom: str
    pos: int
    ref: str
    alt: str
    significance: str | None = None
    review_stars: int = 0
    accession: str | None = None
    conditions: str | None = None
    gene_symbol: str | None = None
    variation_id: int | None = None


def _parse_info_field(info: str) -> dict[str, str]:
    """Parse a VCF INFO field into a dict of key=value pairs.

    Flag fields (no ``=``) are stored with value ``""``.
    """
    result: dict[str, str] = {}
    for part in info.split(";"):
        if "=" in part:
            key, _, value = part.partition("=")
            result[key] = value
        else:
            result[part] = ""
    return result


def _review_status_to_stars(revstat: str) -> int:
    """Convert a CLNREVSTAT string to a review star count (0-4).

    CLNREVSTAT may contain multiple comma-separated tokens that together
    form a single status key (e.g. ``criteria_provided,_single_submitter``).
    """
    # Normalize underscores (some versions use spaces)
    normalized = revstat.strip().replace(" ", "_").lower()
    if normalized in REVIEW_STATUS_STARS:
        return REVIEW_STATUS_STARS[normalized]
    # Try the raw value
    if revstat in REVIEW_STATUS_STARS:
        return REVIEW_STATUS_STARS[revstat]
    return 0


def _normalize_chrom(chrom: str) -> str | None:
    """Normalize chromosome name. Returns None for invalid chromosomes."""
    c = chrom.removeprefix("chr").upper()
    if c in VALID_CHROMS:
        return c
    return None


def _extract_gene_symbol(geneinfo: str | None) -> str | None:
    """Extract gene symbol from GENEINFO field (format: ``GENE:GENEID``)."""
    if not geneinfo:
        return None
    # May have multiple genes separated by |
    first_gene = geneinfo.split("|")[0]
    symbol = first_gene.split(":")[0]
    return symbol if symbol else None


def parse_clinvar_vcf_line(line: str) -> tuple[ClinVarRecord | None, str | None]:
    """Parse a single non-header VCF line into a ClinVarRecord.

    Returns:
        Tuple of (record, skip_reason). If record is None, skip_reason
        indicates why the line was skipped.
    """
    parts = line.rstrip("\n\r").split("\t")
    if len(parts) < 8:
        return None, SkipReason.MALFORMED

    chrom_raw, pos_str, var_id, ref, alt, _qual, _filt, info_str = parts[:8]

    # Normalize chromosome
    chrom = _normalize_chrom(chrom_raw)
    if chrom is None:
        return None, SkipReason.INVALID_CHROM

    # Validate position
    try:
        pos = int(pos_str)
    except (ValueError, TypeError):
        return None, SkipReason.MALFORMED

    # Parse INFO
    info = _parse_info_field(info_str)

    # Extract rsid — require a numeric RS field. dbSNP rs numbers are bare
    # positive integers, so a non-numeric RS value (e.g. ``abc``, ``12abc``,
    # ``-5``) is malformed: turning it into ``rs{value}`` would store a junk
    # rsid (``rsabc``, ``rs-5``) that never matches a real ``rsNNN`` lookup.
    rs_val = info.get("RS")
    if not rs_val:
        return None, SkipReason.NO_RSID
    if not (rs_val.isascii() and rs_val.isdigit()):
        return None, SkipReason.MALFORMED
    rsid = f"rs{rs_val}"

    # Parse variation ID from the ID column
    variation_id: int | None = None
    try:
        variation_id = int(var_id)
    except (ValueError, TypeError):
        pass

    # Clinical significance
    significance = info.get("CLNSIG")
    if significance:
        # Replace underscores with spaces for readability,
        # but keep the standard ClinVar casing
        significance = significance.replace("_", " ").strip()
        # Use first significance if multiple separated by /
        # (multi-allelic sites)
        if "/" in significance:
            significance = significance.split("/")[0].strip()

    # Review stars
    revstat = info.get("CLNREVSTAT", "")
    review_stars = _review_status_to_stars(revstat)

    # Accession (VCV preferred, fall back to CLNACC)
    accession = None
    clnvcid = info.get("CLNVCID")
    if clnvcid:
        accession = f"VCV{clnvcid.zfill(9)}"
    elif "CLNACC" in info:
        accession = info["CLNACC"].split("|")[0]

    # Conditions / disease name
    conditions = info.get("CLNDN")
    if conditions:
        conditions = conditions.replace("_", " ")

    # Gene symbol
    gene_symbol = _extract_gene_symbol(info.get("GENEINFO", ""))

    # Handle multi-allelic ALTs: create record for first ALT only
    # (ClinVar VCF typically has one ALT per line)
    first_alt = alt.split(",")[0]

    record = ClinVarRecord(
        rsid=rsid,
        chrom=chrom,
        pos=pos,
        ref=ref,
        alt=first_alt,
        significance=significance,
        review_stars=review_stars,
        accession=accession,
        conditions=conditions,
        gene_symbol=gene_symbol,
        variation_id=variation_id,
    )
    return record, None


def _extract_file_date(header_lines: list[str]) -> str | None:
    """Extract the fileDate from VCF header lines."""
    for line in header_lines:
        if line.startswith("##fileDate="):
            return line.split("=", 1)[1].strip()
    return None


def _record_to_dict(record: ClinVarRecord) -> dict:
    """Convert a ClinVarRecord to a dict for database insertion."""
    return {
        "rsid": record.rsid,
        "chrom": record.chrom,
        "pos": record.pos,
        "ref": record.ref,
        "alt": record.alt,
        "significance": record.significance,
        "review_stars": record.review_stars,
        "accession": record.accession,
        "conditions": record.conditions,
        "gene_symbol": record.gene_symbol,
        "variation_id": record.variation_id,
    }


def iter_clinvar_vcf(
    vcf_path: Path,
    *,
    progress_callback: Callable[[int], None] | None = None,
) -> Iterator[tuple[dict, LoadStats]]:
    """Iterate over ClinVar VCF rows lazily, yielding (row_dict, stats).

    The final stats are accumulated across all yields. Callers should use
    the stats from the last yielded item for final counts.

    Args:
        vcf_path: Path to the VCF or VCF.gz file.
        progress_callback: Optional callback called with the count of
            parsed lines at regular intervals.

    Yields:
        Tuple of (row dict ready for insert, running LoadStats).
    """
    stats = LoadStats()
    header_lines: list[str] = []

    open_fn = gzip.open if vcf_path.suffix == ".gz" else open
    with open_fn(vcf_path, "rt", encoding="utf-8") as fh:  # type: ignore[call-overload]
        for line in fh:
            if line.startswith("##"):
                header_lines.append(line.rstrip())
                continue
            if line.startswith("#"):
                continue

            stats.total_lines += 1

            record, skip_reason = parse_clinvar_vcf_line(line)
            if record is None:
                if skip_reason == SkipReason.NO_RSID:
                    stats.skipped_no_rsid += 1
                elif skip_reason == SkipReason.INVALID_CHROM:
                    stats.skipped_invalid_chrom += 1
                else:
                    stats.skipped_malformed += 1
                continue

            stats.variants_loaded += 1
            yield _record_to_dict(record), stats

            if progress_callback and stats.total_lines % 10_000 == 0:
                progress_callback(stats.total_lines)

    stats.file_date = _extract_file_date(header_lines)


def parse_clinvar_vcf(
    vcf_path: Path,
    *,
    progress_callback: Callable[[int], None] | None = None,
) -> tuple[list[dict], LoadStats]:
    """Parse a ClinVar VCF file (plain or gzipped) and return rows + stats.

    For small files / testing. For large files, prefer ``iter_clinvar_vcf``
    with ``load_clinvar_from_iter`` to avoid loading all rows into memory.

    Args:
        vcf_path: Path to the VCF or VCF.gz file.
        progress_callback: Optional callback called with the count of
            parsed lines at regular intervals.

    Returns:
        Tuple of (list of row dicts ready for insert, LoadStats).
    """
    rows: list[dict] = []
    stats = LoadStats()
    for row, stats in iter_clinvar_vcf(vcf_path, progress_callback=progress_callback):
        rows.append(row)
    return rows, stats


def _compute_sha256(path: Path) -> str:
    """Compute SHA-256 hex digest of a file."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _batched(iterator: Iterator[dict], size: int) -> Iterator[list[dict]]:
    """Yield successive batches of ``size`` items from an iterator."""
    while True:
        batch = list(islice(iterator, size))
        if not batch:
            break
        yield batch


def _wal_checkpoint(engine: sa.Engine) -> None:
    """Run WAL checkpoint if the engine is file-backed (not in-memory)."""
    url = str(engine.url)
    if url == "sqlite://" or ":memory:" in url:
        return
    with engine.connect() as conn:
        conn.execute(sa.text("PRAGMA wal_checkpoint(TRUNCATE)"))
        conn.commit()


def load_clinvar_into_db(
    rows: list[dict],
    engine: sa.Engine,
    *,
    stats: LoadStats | None = None,
    clear_existing: bool = True,
) -> LoadStats:
    """Bulk-load parsed ClinVar rows into the clinvar_variants table.

    Args:
        rows: List of dicts matching clinvar_variants columns.
        engine: SQLAlchemy engine for reference.db.
        stats: Optional LoadStats to update (if None, a new one is created).
        clear_existing: Whether to DELETE all existing rows first.

    Returns:
        Updated LoadStats with variants_loaded count.
    """
    if stats is None:
        stats = LoadStats(variants_loaded=len(rows))

    if clear_existing:
        with engine.begin() as conn:
            conn.execute(clinvar_variants.delete())

    # Bulk insert in batches (per-batch transactions to avoid long locks)
    for i in range(0, len(rows), BATCH_SIZE):
        batch = rows[i : i + BATCH_SIZE]
        with engine.begin() as conn:
            conn.execute(clinvar_variants.insert(), batch)

    # WAL checkpoint after bulk load (outside transaction)
    _wal_checkpoint(engine)

    logger.info(
        "clinvar_loaded",
        variants=stats.variants_loaded,
    )

    return stats


def load_clinvar_from_iter(
    row_iter: Iterator[tuple[dict, LoadStats]],
    engine: sa.Engine,
    *,
    clear_existing: bool = True,
) -> LoadStats:
    """Stream-load ClinVar rows from an iterator into the database.

    Memory-efficient: only holds one batch at a time, suitable for
    the full ClinVar VCF (~1.5M variants).

    Args:
        row_iter: Iterator yielding (row_dict, running_stats) tuples,
            as produced by ``iter_clinvar_vcf``.
        engine: SQLAlchemy engine for reference.db.
        clear_existing: Whether to DELETE all existing rows first.

    Returns:
        Final LoadStats.
    """
    stats = LoadStats()

    # Strip stats from iterator to get plain row dicts
    def rows_only() -> Iterator[dict]:
        nonlocal stats
        for row, stats in row_iter:
            yield row

    if clear_existing:
        with engine.begin() as conn:
            conn.execute(clinvar_variants.delete())

    for batch in _batched(rows_only(), BATCH_SIZE):
        with engine.begin() as conn:
            conn.execute(clinvar_variants.insert(), batch)

    # WAL checkpoint after bulk load (outside transaction)
    _wal_checkpoint(engine)

    logger.info(
        "clinvar_loaded",
        variants=stats.variants_loaded,
    )

    return stats


def record_clinvar_version(
    engine: sa.Engine,
    *,
    version: str,
    file_path: str | None = None,
    file_size_bytes: int | None = None,
    checksum: str | None = None,
) -> None:
    """Insert or update the ClinVar version in the database_versions table."""
    from backend.db.database_registry import _record_db_version

    _record_db_version(
        engine,
        db_name="clinvar",
        version=version,
        file_size_bytes=file_size_bytes,
        sha256=checksum,
        file_path=file_path,
    )


def download_clinvar_vcf(
    dest_dir: Path,
    *,
    url: str = CLINVAR_VCF_URL,
    progress_callback: Callable[[int, int | None], None] | None = None,
    timeout: float = 300.0,
) -> Path:
    """Download the ClinVar VCF (GRCh37) from NCBI FTP.

    Writes to a temporary file and renames on success to avoid
    leaving partial files on failure.

    Args:
        dest_dir: Directory to save the downloaded file.
        url: Override URL (useful for testing).
        progress_callback: Called with (bytes_downloaded, total_bytes).
            ``total_bytes`` may be None if Content-Length is absent.
        timeout: HTTP request timeout in seconds.

    Returns:
        Path to the downloaded .vcf.gz file.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_path = dest_dir / "clinvar_GRCh37.vcf.gz"
    tmp_path = dest_dir / "clinvar_GRCh37.vcf.gz.tmp"

    logger.info("clinvar_download_start", url=url)

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

    # Atomic rename on success; drop the now-stale validator sidecar.
    tmp_path.replace(dest_path)
    clear_validator_sidecar(tmp_path)

    logger.info("clinvar_download_complete", path=str(dest_path), bytes=outcome.total_bytes)
    return dest_path


def _get_clinvar_last_modified_version(url: str = CLINVAR_VCF_URL) -> str | None:
    """Get the ClinVar VCF version from the HTTP Last-Modified header.

    Returns YYYYMMDD string or None on failure.
    """
    try:
        timeout = httpx.Timeout(30.0, connect=10.0)
        with httpx.Client(follow_redirects=True, timeout=timeout) as client:
            resp = client.head(url)
            resp.raise_for_status()
        last_modified = resp.headers.get("Last-Modified", "")
        if last_modified:
            from email.utils import parsedate_to_datetime

            dt = parsedate_to_datetime(last_modified)
            return dt.strftime("%Y%m%d")
    except Exception:
        logger.debug("clinvar_last_modified_fetch_failed")
    return None


def download_and_load_clinvar(
    engine: sa.Engine,
    dest_dir: Path,
    *,
    url: str = CLINVAR_VCF_URL,
    download_progress: Callable[[int, int | None], None] | None = None,
    parse_progress: Callable[[int], None] | None = None,
    timeout: float = 300.0,
) -> LoadStats:
    """Full pipeline: download ClinVar VCF, parse, and load into reference.db.

    Uses streaming parse + batch insert to keep memory usage low.

    Args:
        engine: SQLAlchemy engine for reference.db.
        dest_dir: Directory for downloaded files.
        url: ClinVar VCF URL (override for testing).
        download_progress: Callback for download progress.
        parse_progress: Callback for parse progress.
        timeout: HTTP timeout in seconds.

    Returns:
        LoadStats with counts and metadata.
    """
    # Download
    vcf_path = download_clinvar_vcf(
        dest_dir,
        url=url,
        progress_callback=download_progress,
        timeout=timeout,
    )

    # Compute checksum
    sha256 = _compute_sha256(vcf_path)

    # Stream parse + load
    row_iter = iter_clinvar_vcf(vcf_path, progress_callback=parse_progress)
    stats = load_clinvar_from_iter(row_iter, engine)
    stats.sha256 = sha256

    # Record version using HTTP Last-Modified (same source as check_clinvar_update)
    # so the stored version matches what the update checker compares against.
    # The VCF fileDate is typically 1 day earlier than the FTP Last-Modified.
    version = _get_clinvar_last_modified_version(url) or datetime.now(UTC).strftime("%Y%m%d")
    record_clinvar_version(
        engine,
        version=version,
        file_path=str(vcf_path),
        file_size_bytes=vcf_path.stat().st_size,
        checksum=sha256,
    )

    return stats


# ═══════════════════════════════════════════════════════════════════════
# ClinVar Annotation Lookup (P1-11)
# ═══════════════════════════════════════════════════════════════════════

# Annotation coverage bitmask: bit 1 = ClinVar (value 2)
CLINVAR_BITMASK = 0b000010  # bit 1 = 2


@dataclass
class ClinVarAnnotation:
    """ClinVar annotation data for a single variant."""

    rsid: str
    clinvar_significance: str | None
    clinvar_review_stars: int
    clinvar_accession: str | None
    clinvar_conditions: str | None
    matched_by: str  # "rsid" or "chrom_pos"
    # Reference/alternate alleles from the matched ClinVar record. Needed to
    # determine whether the sample's genotype actually carries the ALT allele
    # (zygosity) — a chip genotypes every probe regardless of carriage.
    ref: str | None = None
    alt: str | None = None


@dataclass
class AnnotationResult:
    """Statistics from a ClinVar annotation lookup run."""

    total_variants: int = 0
    matched_by_rsid: int = 0
    matched_by_position: int = 0
    not_matched: int = 0
    rows_written: int = 0

    @property
    def total_matched(self) -> int:
        return self.matched_by_rsid + self.matched_by_position


def _pick_clinvar_row(rows: list[sa.Row], genotype: str | None) -> sa.Row:
    """Choose which ClinVar record to annotate from same-site candidates.

    ``rows`` are the candidate ClinVar records for one rsid (or one
    ``(chrom, pos)``), ordered by ``review_stars`` descending.

    At multi-allelic sites the candidates can have different ``ref``/``alt``
    pairs, so picking purely by review stars can score the sample against an
    allele it does not carry (missing a true carrier or attaching the wrong
    condition). When a sample ``genotype`` is available, prefer the
    highest-star record whose ALT the genotype actually carries; otherwise fall
    back to the highest-star record so the ClinVar significance is still
    recorded (and scored as homozygous reference downstream).
    """
    if genotype is not None:
        for row in rows:  # already sorted by review_stars descending
            if classify_zygosity(genotype, row.ref, row.alt) in CARRIED_ZYGOSITIES:
                return row
    return rows[0]


def lookup_clinvar_by_rsids(
    rsids: list[str],
    reference_engine: sa.Engine,
    *,
    genotype_by_rsid: dict[str, str] | None = None,
) -> dict[str, ClinVarAnnotation]:
    """Look up ClinVar annotations for a batch of rsids.

    When multiple ClinVar records share the same rsid (e.g. multi-allelic
    or multi-condition) the record with the highest review_stars is returned —
    except that, when ``genotype_by_rsid`` is supplied, a record whose ALT the
    sample actually carries is preferred over a higher-star record it does not
    carry (multi-allelic carriage correctness; see ``_pick_clinvar_row``).

    Args:
        rsids: List of rsid strings (e.g. ["rs429358", "rs7412"]).
        reference_engine: SQLAlchemy engine for reference.db.
        genotype_by_rsid: Optional map of rsid → sample genotype enabling
            carriage-aware record selection at multi-allelic sites. When omitted
            selection is by review_stars only (backward-compatible).

    Returns:
        Dict mapping rsid → ClinVarAnnotation for matched variants.
    """
    if not rsids:
        return {}

    results: dict[str, ClinVarAnnotation] = {}

    # Process in batches to avoid SQLite variable limit (default 999)
    with reference_engine.connect() as conn:
        for i in range(0, len(rsids), 500):
            batch = rsids[i : i + 500]

            stmt = (
                sa.select(
                    clinvar_variants.c.rsid,
                    clinvar_variants.c.significance,
                    clinvar_variants.c.review_stars,
                    clinvar_variants.c.accession,
                    clinvar_variants.c.conditions,
                    clinvar_variants.c.ref,
                    clinvar_variants.c.alt,
                )
                .where(clinvar_variants.c.rsid.in_(batch))
                .order_by(
                    clinvar_variants.c.rsid,
                    clinvar_variants.c.review_stars.desc(),
                )
            )

            rows = conn.execute(stmt).fetchall()

            # Group candidate records per rsid (preserving review_stars order),
            # then choose carriage-aware.
            candidates: dict[str, list[sa.Row]] = {}
            for row in rows:
                candidates.setdefault(row.rsid, []).append(row)

            for rsid, cand_rows in candidates.items():
                if rsid in results:
                    continue
                genotype = genotype_by_rsid.get(rsid) if genotype_by_rsid else None
                row = _pick_clinvar_row(cand_rows, genotype)
                results[rsid] = ClinVarAnnotation(
                    rsid=rsid,
                    clinvar_significance=row.significance,
                    clinvar_review_stars=row.review_stars or 0,
                    clinvar_accession=row.accession,
                    clinvar_conditions=row.conditions,
                    matched_by="rsid",
                    ref=row.ref,
                    alt=row.alt,
                )

    return results


def lookup_clinvar_by_positions(
    positions: list[tuple[str, int, str]],
    reference_engine: sa.Engine,
    *,
    genotype_by_rsid: dict[str, str] | None = None,
) -> dict[str, ClinVarAnnotation]:
    """Look up ClinVar annotations by (chrom, pos) for unmatched variants.

    This is the fallback strategy when rsid matching fails (e.g. the
    variant has an i-prefixed rsid or the ClinVar record uses a
    different rsid for the same position).

    Args:
        positions: List of (chrom, pos, rsid) tuples. The rsid is the
            sample variant's rsid, used as the key in the result dict.
        reference_engine: SQLAlchemy engine for reference.db.
        genotype_by_rsid: Optional map of sample rsid → genotype enabling
            carriage-aware record selection at multi-allelic positions. When
            omitted selection is by review_stars only (backward-compatible).

    Returns:
        Dict mapping sample rsid → ClinVarAnnotation for position-matched variants.
    """
    if not positions:
        return {}

    results: dict[str, ClinVarAnnotation] = {}

    # Process in batches
    with reference_engine.connect() as conn:
        for i in range(0, len(positions), 250):
            batch = positions[i : i + 250]

            # Build OR conditions for (chrom, pos) pairs
            conditions = [
                sa.and_(
                    clinvar_variants.c.chrom == chrom,
                    clinvar_variants.c.pos == pos,
                )
                for chrom, pos, _ in batch
            ]

            stmt = (
                sa.select(
                    clinvar_variants.c.chrom,
                    clinvar_variants.c.pos,
                    clinvar_variants.c.significance,
                    clinvar_variants.c.review_stars,
                    clinvar_variants.c.accession,
                    clinvar_variants.c.conditions,
                    clinvar_variants.c.ref,
                    clinvar_variants.c.alt,
                )
                .where(sa.or_(*conditions))
                .order_by(
                    clinvar_variants.c.chrom,
                    clinvar_variants.c.pos,
                    clinvar_variants.c.review_stars.desc(),
                )
            )

            rows = conn.execute(stmt).fetchall()

            # Group candidate records per (chrom, pos), preserving review_stars
            # order, then choose carriage-aware per sample variant below.
            pos_candidates: dict[tuple[str, int], list[sa.Row]] = {}
            for row in rows:
                pos_candidates.setdefault((row.chrom, row.pos), []).append(row)

            # Map back to sample rsids
            for chrom, pos, sample_rsid in batch:
                key = (chrom, pos)
                if key in pos_candidates and sample_rsid not in results:
                    genotype = genotype_by_rsid.get(sample_rsid) if genotype_by_rsid else None
                    row = _pick_clinvar_row(pos_candidates[key], genotype)
                    results[sample_rsid] = ClinVarAnnotation(
                        rsid=sample_rsid,
                        clinvar_significance=row.significance,
                        clinvar_review_stars=row.review_stars or 0,
                        clinvar_accession=row.accession,
                        clinvar_conditions=row.conditions,
                        matched_by="chrom_pos",
                        ref=row.ref,
                        alt=row.alt,
                    )

    return results


def annotate_sample_clinvar(
    sample_engine: sa.Engine,
    reference_engine: sa.Engine,
) -> AnnotationResult:
    """Annotate a sample's variants with ClinVar data.

    Reads all raw_variants from the sample database, matches them
    against clinvar_variants in reference.db (rsid first, then
    chrom/pos fallback), and upserts results into annotated_variants
    with bitmask bit 1 set.

    Args:
        sample_engine: SQLAlchemy engine for the per-sample database.
        reference_engine: SQLAlchemy engine for reference.db.

    Returns:
        AnnotationResult with match statistics.
    """
    result = AnnotationResult()

    # 1. Read all raw variants from the sample
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

    # Build lookup structures
    all_rsids = [r.rsid for r in raw_rows]
    raw_by_rsid = {r.rsid: r for r in raw_rows}
    # Genotypes drive carriage-aware ClinVar record selection at multi-allelic
    # sites (so the sample is scored against the allele it actually carries).
    genotype_by_rsid = {r.rsid: r.genotype for r in raw_rows}

    # 2. Primary match: by rsid
    rsid_matches = lookup_clinvar_by_rsids(
        all_rsids, reference_engine, genotype_by_rsid=genotype_by_rsid
    )
    result.matched_by_rsid = len(rsid_matches)

    # 3. Fallback: by (chrom, pos) for unmatched variants
    unmatched_positions = [
        (r.chrom, r.pos, r.rsid) for r in raw_rows if r.rsid not in rsid_matches
    ]
    pos_matches = lookup_clinvar_by_positions(
        unmatched_positions, reference_engine, genotype_by_rsid=genotype_by_rsid
    )
    result.matched_by_position = len(pos_matches)

    # 4. Merge all matches
    all_matches: dict[str, ClinVarAnnotation] = {**rsid_matches, **pos_matches}
    result.not_matched = result.total_variants - result.total_matched

    # 5. Upsert into annotated_variants
    rows_to_upsert = []
    for rsid, annot in all_matches.items():
        raw = raw_by_rsid[rsid]
        # Determine whether the sample's genotype actually carries the ClinVar
        # ALT allele. Without this, every chip probe that overlaps a ClinVar
        # record is mislabelled with that record's significance even when the
        # individual is homozygous reference (the carriage bug downstream
        # modules relied on). ``None`` means unscoreable (indel/no-call/strand).
        zygosity = classify_zygosity(raw.genotype, annot.ref, annot.alt)
        rows_to_upsert.append(
            {
                "rsid": rsid,
                "chrom": raw.chrom,
                "pos": raw.pos,
                "ref": annot.ref,
                "alt": annot.alt,
                "genotype": raw.genotype,
                "zygosity": zygosity,
                "clinvar_significance": annot.clinvar_significance,
                "clinvar_review_stars": annot.clinvar_review_stars,
                "clinvar_accession": annot.clinvar_accession,
                "clinvar_conditions": annot.clinvar_conditions,
                "annotation_coverage": CLINVAR_BITMASK,
            }
        )

    if rows_to_upsert:
        with sample_engine.begin() as conn:
            for batch_start in range(0, len(rows_to_upsert), BATCH_SIZE):
                batch = rows_to_upsert[batch_start : batch_start + BATCH_SIZE]

                stmt = sqlite_insert(annotated_variants).values(batch)
                stmt = stmt.on_conflict_do_update(
                    index_elements=["rsid"],
                    set_={
                        "ref": stmt.excluded.ref,
                        "alt": stmt.excluded.alt,
                        "zygosity": stmt.excluded.zygosity,
                        "clinvar_significance": stmt.excluded.clinvar_significance,
                        "clinvar_review_stars": stmt.excluded.clinvar_review_stars,
                        "clinvar_accession": stmt.excluded.clinvar_accession,
                        "clinvar_conditions": stmt.excluded.clinvar_conditions,
                        # OR the ClinVar bit into existing coverage
                        "annotation_coverage": sa.case(
                            (
                                annotated_variants.c.annotation_coverage.is_(None),
                                stmt.excluded.annotation_coverage,
                            ),
                            else_=annotated_variants.c.annotation_coverage.op("|")(
                                CLINVAR_BITMASK
                            ),
                        ),
                    },
                )
                conn.execute(stmt)

        result.rows_written = len(rows_to_upsert)

    # WAL checkpoint after annotation
    _wal_checkpoint(sample_engine)

    logger.info(
        "clinvar_annotation_complete",
        total=result.total_variants,
        rsid_matches=result.matched_by_rsid,
        pos_matches=result.matched_by_position,
        unmatched=result.not_matched,
    )

    return result

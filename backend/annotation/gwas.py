"""GWAS Catalog TSV downloader and EFO-filtered SQLite loader.

Downloads the GWAS Catalog associations TSV from the EBI GWAS Catalog,
filters to a whitelist of EFO trait terms relevant to Yeliztli modules
(nutrigenomics, fitness, sleep, skin, allergy, traits), and bulk-loads
into the ``gwas_associations`` table in reference.db.

Analysis modules read GWAS-match presence through ``gwas_matched_rsids``.

Usage::

    from backend.annotation.gwas import (
        download_gwas_catalog,
        gwas_matched_rsids,
        load_gwas_into_db,
    )

    tsv_path = download_gwas_catalog(dest_dir)
    stats = load_gwas_into_db(tsv_path, engine)
"""

from __future__ import annotations

import csv
import gzip
import hashlib
import re
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from itertools import islice
from pathlib import Path
from typing import TYPE_CHECKING

import httpx
import sqlalchemy as sa
import structlog

from backend.annotation.http_download import (
    clear_validator_sidecar,
    read_validator_sidecar,
    stream_download,
    write_validator_sidecar,
)
from backend.db.tables import gwas_associations

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

logger = structlog.get_logger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────

# GWAS Catalog associations (ontology-annotated, alternative format) — ZIP from EBI FTP.
# Contains a TSV with the same columns as the former /downloads/alternative endpoint.
GWAS_CATALOG_URL = (
    "https://ftp.ebi.ac.uk/pub/databases/gwas/releases/latest/"
    "gwas-catalog-associations_ontology-annotated-full.zip"
)

# Batch size for bulk inserts (executemany)
BATCH_SIZE = 10_000

# Batch size for GWAS presence lookups. SQLAlchemy expands IN lists into bound
# parameters, so keep this below SQLite's default 999-variable limit.
GWAS_MATCH_BATCH_SIZE = 900

# Valid chromosomes (matching 23andMe scope)
VALID_CHROMS = {str(i) for i in range(1, 23)} | {"X", "Y", "MT"}

# rsid pattern
_RSID_PATTERN = re.compile(r"^rs\d+$")

# ── EFO Term Whitelist ────────────────────────────────────────────────────
# Each module defines a whitelist of EFO trait terms (case-insensitive
# substring matching against GWAS Catalog MAPPED_TRAIT / DISEASE/TRAIT).
# PRD §3.4b: "GWAS Catalog EFO filtering is mandatory."

# Terms are grouped by module for clarity but merged into a single set.

_NUTRIGENOMICS_TERMS = frozenset(
    {
        "folate",
        "homocysteine",
        "vitamin d",
        "vitamin b12",
        "vitamin b6",
        "omega-3",
        "omega-6",
        "fatty acid",
        "iron",
        "ferritin",
        "transferrin",
        "lactose",
        "lactase",
        "caffeine",
        "caffeine consumption",
        "caffeine metabolism",
        "alcohol consumption",
        "alcohol dependence",
        "body mass index",
        "obesity",
        "type 2 diabetes",
        "triglyceride",
        "hdl cholesterol",
        "ldl cholesterol",
        "total cholesterol",
        "blood pressure",
        "selenium",
        "zinc",
        "magnesium",
        "calcium",
        "celiac",
        "gluten",
    }
)

_FITNESS_TERMS = frozenset(
    {
        "muscle",
        "exercise",
        "physical activity",
        "endurance",
        "power",
        "aerobic capacity",
        "vo2max",
        "sprint",
        "grip strength",
        "bone mineral density",
        "tendon",
        "ligament",
        "injury",
        "recovery",
        "athletic",
        "muscle fiber",
        "creatine kinase",
        "lactate",
    }
)

_SLEEP_TERMS = frozenset(
    {
        "sleep",
        "insomnia",
        "chronotype",
        "circadian",
        "restless legs",
        "sleep duration",
        "sleep apnea",
        "melatonin",
        "narcolepsy",
        "morningness",
        "eveningness",
    }
)

_SKIN_TERMS = frozenset(
    {
        "skin",
        "pigmentation",
        "melanoma",
        "hair color",
        "eye color",
        "freckling",
        "sun sensitivity",
        "sunburn",
        "psoriasis",
        "eczema",
        "dermatitis",
        "acne",
        "aging",
        "wrinkle",
        "vitiligo",
        "tanning",
        "skin cancer",
        "basal cell carcinoma",
        "squamous cell",
        "collagen",
    }
)

_ALLERGY_TERMS = frozenset(
    {
        "allergy",
        "allergic",
        "asthma",
        "atopic",
        "ige",
        "eosinophil",
        "rhinitis",
        "urticaria",
        "anaphylaxis",
        "drug hypersensitivity",
        "food allergy",
        "histamine",
        "mast cell",
        "celiac disease",
        "inflammatory bowel",
        "crohn",
        "ulcerative colitis",
    }
)

_METHYLATION_TERMS = frozenset(
    {
        "methylation",
        "homocysteine",
        "folate",
        "methionine",
        "s-adenosylmethionine",
        "glutathione",
        "choline",
        "betaine",
        "one-carbon",
        "vitamin b12",
        "vitamin b6",
        "cobalamin",
    }
)

_TRAITS_TERMS = frozenset(
    {
        "educational attainment",
        "cognitive",
        "intelligence",
        "neuroticism",
        "extraversion",
        "risk tolerance",
        "risk taking",
        "adhd",
        "attention deficit",
        "depression",
        "anxiety",
        "schizophrenia",
        "bipolar",
        "personality",
        "memory",
        "reaction time",
        "openness",
        "agreeableness",
        "conscientiousness",
        "subjective well-being",
        "loneliness",
        "pain sensitivity",
        "reward",
    }
)

# Merged whitelist for filtering
EFO_WHITELIST: frozenset[str] = (
    _NUTRIGENOMICS_TERMS
    | _FITNESS_TERMS
    | _SLEEP_TERMS
    | _SKIN_TERMS
    | _ALLERGY_TERMS
    | _METHYLATION_TERMS
    | _TRAITS_TERMS
)

# Module-level groupings for downstream module use
EFO_MODULES: dict[str, frozenset[str]] = {
    "nutrigenomics": _NUTRIGENOMICS_TERMS,
    "fitness": _FITNESS_TERMS,
    "sleep": _SLEEP_TERMS,
    "skin": _SKIN_TERMS,
    "allergy": _ALLERGY_TERMS,
    "methylation": _METHYLATION_TERMS,
    "traits": _TRAITS_TERMS,
}


# ── Dataclasses ───────────────────────────────────────────────────────────


@dataclass
class GWASLoadStats:
    """Statistics from a GWAS Catalog load operation."""

    total_lines: int = 0
    associations_loaded: int = 0
    skipped_no_rsid: int = 0
    skipped_invalid_chrom: int = 0
    skipped_no_trait: int = 0
    skipped_efo_filter: int = 0
    skipped_malformed: int = 0
    file_date: str | None = None
    sha256: str | None = None


# ── Parsing helpers ───────────────────────────────────────────────────────


def _normalize_chrom(chrom: str) -> str | None:
    """Normalize chromosome name. Returns None for invalid chromosomes."""
    c = chrom.strip().removeprefix("chr").upper()
    if c in VALID_CHROMS:
        return c
    return None


def _parse_float(value: str | None) -> float | None:
    """Safely parse a float, returning None on failure."""
    if not value or value.strip() in ("", "NR", "NA", "-"):
        return None
    try:
        return float(value.strip())
    except (ValueError, TypeError):
        return None


def _parse_int(value: str | None) -> int | None:
    """Safely parse an integer, returning None on failure."""
    if not value or value.strip() in ("", "NR", "NA", "-"):
        return None
    try:
        # Handle comma-separated numbers (e.g. "74,046")
        return int(value.strip().replace(",", ""))
    except (ValueError, TypeError):
        return None


def _extract_rsid(snp_field: str) -> str | None:
    """Extract a valid rsid from the GWAS Catalog SNPs column.

    The SNPs column may contain multiple SNPs separated by '; ' or ' x ',
    or haplotype notations. We extract the first valid rsid.
    """
    if not snp_field:
        return None

    # Split on common delimiters
    for sep in (";", " x ", ","):
        if sep in snp_field:
            parts = snp_field.split(sep)
            for part in parts:
                candidate = part.strip().lower()
                if _RSID_PATTERN.match(candidate):
                    return candidate
            return None

    # Single value
    candidate = snp_field.strip().lower()
    if _RSID_PATTERN.match(candidate):
        return candidate
    return None


def _extract_risk_allele(strongest_snp_risk_allele: str) -> str | None:
    """Extract risk allele from 'STRONGEST SNP-RISK ALLELE' column.

    Format is typically 'rs12345-A' or 'rs12345-?' — extract the allele
    after the last hyphen.
    """
    if not strongest_snp_risk_allele:
        return None
    parts = strongest_snp_risk_allele.strip().rsplit("-", 1)
    if len(parts) == 2:
        allele = parts[1].strip().upper()
        if allele and allele != "?" and len(allele) <= 10:
            return allele
    return None


def _trait_matches_whitelist(trait: str) -> bool:
    """Check if a trait string matches any EFO whitelist term.

    Uses case-insensitive substring matching — a GWAS Catalog trait
    like "Type 2 diabetes mellitus" matches the whitelist term
    "type 2 diabetes".
    """
    trait_lower = trait.lower()
    return any(term in trait_lower for term in EFO_WHITELIST)


# ── GWAS presence lookup ─────────────────────────────────────────────────


def _iter_rsid_batches(rsids: list[str], size: int) -> Iterator[list[str]]:
    """Yield unique rsids in stable batches."""
    seen: set[str] = set()
    batch: list[str] = []

    for rsid in rsids:
        if rsid in seen:
            continue
        seen.add(rsid)
        batch.append(rsid)
        if len(batch) == size:
            yield batch
            batch = []

    if batch:
        yield batch


def gwas_matched_rsids(
    rsids: list[str],
    reference_engine: sa.Engine,
) -> set[str]:
    """Return rsids that have at least one GWAS Catalog association."""
    if not rsids:
        return set()

    matched: set[str] = set()
    with reference_engine.connect() as conn:
        for batch in _iter_rsid_batches(rsids, GWAS_MATCH_BATCH_SIZE):
            stmt = (
                sa.select(gwas_associations.c.rsid)
                .where(gwas_associations.c.rsid.in_(batch))
                .distinct()
            )
            for row in conn.execute(stmt):
                matched.add(row.rsid)

    return matched


# ── GWAS Catalog TSV column mapping ──────────────────────────────────────
# GWAS Catalog alternative format TSV columns (as of 2024):
# https://www.ebi.ac.uk/gwas/docs/file-downloads

# Key columns we use (0-indexed):
_COL_PUBMEDID = "PUBMEDID"
_COL_STUDY = "STUDY"
_COL_DISEASE_TRAIT = "DISEASE/TRAIT"
_COL_INITIAL_SAMPLE = "INITIAL SAMPLE SIZE"
_COL_CHR_ID = "CHR_ID"
_COL_CHR_POS = "CHR_POS"
_COL_SNPS = "SNPS"
_COL_STRONGEST_ALLELE = "STRONGEST SNP-RISK ALLELE"
_COL_PVALUE = "P-VALUE"
_COL_OR_BETA = "OR or BETA"
_COL_CI_95 = "95% CI (TEXT)"
_COL_MAPPED_TRAIT = "MAPPED_TRAIT"


def _parse_sample_size(initial_sample: str | None) -> int | None:
    """Extract total sample size from INITIAL SAMPLE SIZE field.

    Format varies: "1,234 European ancestry cases, 5,678 controls"
    We extract all numbers and sum them.
    """
    if not initial_sample:
        return None
    numbers = re.findall(r"[\d,]+", initial_sample)
    if not numbers:
        return None
    total = 0
    for n in numbers:
        try:
            total += int(n.replace(",", ""))
        except ValueError:
            continue
    return total if total > 0 else None


def _is_odds_ratio(ci_text: str | None, or_beta_val: float | None) -> bool:
    """Heuristic to distinguish OR from beta in the 'OR or BETA' column.

    If the 95% CI text contains language like "increase"/"decrease"/"unit",
    it's a beta coefficient. If it only has brackets like [1.2-1.5], it's OR.
    """
    if ci_text:
        ci_lower = ci_text.lower().strip()
        # "increase" / "decrease" / "unit" / "sd" language suggests beta
        # (check before brackets — CI for betas also has brackets)
        if any(w in ci_lower for w in ("increase", "decrease", "unit", "sd")):
            return False
        # CI text with brackets only (no beta language) → OR
        if "[" in ci_lower or "(" in ci_lower:
            return True
    # Fallback heuristic: ORs are always positive (centered around 1.0).
    # Negative values are always betas.
    if or_beta_val is not None:
        if or_beta_val < 0:
            return False
        return 0.1 <= or_beta_val <= 20
    return True


def parse_gwas_tsv_row(
    row: dict[str, str],
) -> tuple[dict | None, str | None]:
    """Parse a single GWAS Catalog TSV row into a dict for insertion.

    Args:
        row: Dict from csv.DictReader with GWAS Catalog column names.

    Returns:
        Tuple of (row_dict, skip_reason). If row_dict is None,
        skip_reason indicates why the row was skipped.
    """
    # Extract rsid
    rsid = _extract_rsid(row.get(_COL_SNPS, ""))
    if not rsid:
        return None, "no_rsid"

    # Trait — try MAPPED_TRAIT first (standardized), fall back to DISEASE/TRAIT
    trait = (row.get(_COL_MAPPED_TRAIT) or row.get(_COL_DISEASE_TRAIT) or "").strip()
    if not trait:
        return None, "no_trait"

    # EFO filter
    if not _trait_matches_whitelist(trait):
        return None, "efo_filter"

    # Chromosome & position
    chrom_raw = row.get(_COL_CHR_ID, "")
    chrom = _normalize_chrom(chrom_raw) if chrom_raw.strip() else None
    pos = _parse_int(row.get(_COL_CHR_POS))

    # p-value
    p_value = _parse_float(row.get(_COL_PVALUE))

    # OR or Beta
    or_beta_raw = _parse_float(row.get(_COL_OR_BETA))
    ci_text = row.get(_COL_CI_95)
    odds_ratio: float | None = None
    beta: float | None = None

    if or_beta_raw is not None:
        if _is_odds_ratio(ci_text, or_beta_raw):
            odds_ratio = or_beta_raw
        else:
            beta = or_beta_raw

    # Risk allele
    risk_allele = _extract_risk_allele(row.get(_COL_STRONGEST_ALLELE, ""))

    # PubMed ID and study
    pubmed_id = row.get(_COL_PUBMEDID, "").strip() or None
    study = row.get(_COL_STUDY, "").strip() or None

    # Sample size
    sample_size = _parse_sample_size(row.get(_COL_INITIAL_SAMPLE))

    return {
        "rsid": rsid,
        "chrom": chrom,
        "pos": pos,
        "trait": trait,
        "p_value": p_value,
        "odds_ratio": odds_ratio,
        "beta": beta,
        "risk_allele": risk_allele,
        "pubmed_id": pubmed_id,
        "study": study,
        "sample_size": sample_size,
    }, None


# ── Iterator / streaming parse ────────────────────────────────────────────


def iter_gwas_tsv(
    tsv_path: Path,
    *,
    progress_callback: Callable[[int], None] | None = None,
) -> Iterator[tuple[dict, GWASLoadStats]]:
    """Iterate over GWAS Catalog TSV rows lazily, yielding (row_dict, stats).

    Filters rows against the EFO whitelist. Only matching rows are yielded.

    Args:
        tsv_path: Path to the TSV or TSV.gz file.
        progress_callback: Optional callback called with line count.

    Yields:
        Tuple of (row dict ready for insert, running GWASLoadStats).
    """
    stats = GWASLoadStats()

    open_fn = gzip.open if tsv_path.suffix == ".gz" else open
    with open_fn(tsv_path, "rt", encoding="utf-8") as fh:  # type: ignore[call-overload]
        reader = csv.DictReader(fh, delimiter="\t")

        for row in reader:
            stats.total_lines += 1

            parsed, skip_reason = parse_gwas_tsv_row(row)

            if parsed is None:
                if skip_reason == "no_rsid":
                    stats.skipped_no_rsid += 1
                elif skip_reason == "no_trait":
                    stats.skipped_no_trait += 1
                elif skip_reason == "efo_filter":
                    stats.skipped_efo_filter += 1
                else:
                    stats.skipped_malformed += 1
                continue

            # Validate chromosome if present
            if parsed.get("chrom") is None and row.get(_COL_CHR_ID, "").strip():
                stats.skipped_invalid_chrom += 1
                continue

            stats.associations_loaded += 1
            yield parsed, stats

            if progress_callback and stats.total_lines % 10_000 == 0:
                progress_callback(stats.total_lines)


def parse_gwas_tsv(
    tsv_path: Path,
    *,
    progress_callback: Callable[[int], None] | None = None,
) -> tuple[list[dict], GWASLoadStats]:
    """Parse a GWAS Catalog TSV file and return all matching rows + stats.

    For small files / testing. For large files, prefer ``iter_gwas_tsv``
    with ``load_gwas_from_iter`` to keep memory usage low.
    """
    rows: list[dict] = []
    stats = GWASLoadStats()
    for row, stats in iter_gwas_tsv(tsv_path, progress_callback=progress_callback):
        rows.append(row)
    return rows, stats


# ── Database loading ──────────────────────────────────────────────────────


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


def _compute_sha256(path: Path) -> str:
    """Compute SHA-256 hex digest of a file."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def load_gwas_into_db(
    rows: list[dict],
    engine: sa.Engine,
    *,
    stats: GWASLoadStats | None = None,
    clear_existing: bool = True,
) -> GWASLoadStats:
    """Bulk-load parsed GWAS rows into the gwas_associations table.

    Args:
        rows: List of dicts matching gwas_associations columns.
        engine: SQLAlchemy engine for reference.db.
        stats: Optional GWASLoadStats to update.
        clear_existing: Whether to DELETE all existing rows first.

    Returns:
        Updated GWASLoadStats.
    """
    # Guard: refuse a destructive clear when there is nothing to load — an empty
    # or malformed parse must never silently wipe the curated gwas_associations
    # table (mirrors load_cpic_into_db / load_clingen_into_db).
    if clear_existing and not rows:
        raise ValueError(
            "Refusing to clear gwas_associations with 0 rows to load "
            "(likely an empty or malformed GWAS Catalog source)."
        )

    if stats is None:
        stats = GWASLoadStats(associations_loaded=len(rows))

    if clear_existing:
        with engine.begin() as conn:
            conn.execute(gwas_associations.delete())

    for i in range(0, len(rows), BATCH_SIZE):
        batch = rows[i : i + BATCH_SIZE]
        with engine.begin() as conn:
            conn.execute(gwas_associations.insert(), batch)

    _wal_checkpoint(engine)

    logger.info(
        "gwas_loaded",
        associations=stats.associations_loaded,
    )

    return stats


def load_gwas_from_iter(
    row_iter: Iterator[tuple[dict, GWASLoadStats]],
    engine: sa.Engine,
    *,
    clear_existing: bool = True,
) -> GWASLoadStats:
    """Stream-load GWAS rows from an iterator into the database.

    Memory-efficient: only holds one batch at a time.
    """
    stats = GWASLoadStats()

    def rows_only() -> Iterator[dict]:
        nonlocal stats
        for row, stats in row_iter:
            yield row

    # Peek the first batch BEFORE any destructive clear so a zero-row stream
    # (empty/truncated download, upstream format change) never wipes the curated
    # table to nothing. Only one batch is held in memory, preserving streaming.
    batches = _batched(rows_only(), BATCH_SIZE)
    first_batch = next(batches, None)
    if clear_existing and not first_batch:
        raise ValueError(
            "Refusing to clear gwas_associations with 0 rows to load "
            "(likely an empty or malformed GWAS Catalog source)."
        )

    if clear_existing:
        with engine.begin() as conn:
            conn.execute(gwas_associations.delete())

    if first_batch is not None:
        with engine.begin() as conn:
            conn.execute(gwas_associations.insert(), first_batch)
        for batch in batches:
            with engine.begin() as conn:
                conn.execute(gwas_associations.insert(), batch)

    _wal_checkpoint(engine)

    logger.info(
        "gwas_loaded",
        associations=stats.associations_loaded,
    )

    return stats


def record_gwas_version(
    engine: sa.Engine,
    *,
    version: str,
    file_path: str | None = None,
    file_size_bytes: int | None = None,
    checksum: str | None = None,
) -> None:
    """Insert or update the GWAS Catalog version in database_versions."""
    from backend.db.database_registry import _record_db_version

    _record_db_version(
        engine,
        db_name="gwas_catalog",
        version=version,
        file_size_bytes=file_size_bytes,
        sha256=checksum,
        file_path=file_path,
    )


def _parse_last_modified_version(last_modified: str | None) -> str | None:
    """Parse an HTTP ``Last-Modified`` header into a ``YYYYMMDD`` version string.

    Returns ``None`` when the header is absent or cannot be parsed. Shared by
    :func:`check_gwas_update` (remote version on read) and
    :func:`download_and_load_gwas` (recorded version on write) so both sides use
    the same source and an identical date format.
    """
    if not last_modified:
        return None
    from email.utils import parsedate_to_datetime

    try:
        return parsedate_to_datetime(last_modified).strftime("%Y%m%d")
    except (TypeError, ValueError) as exc:
        logger.warning("gwas_last_modified_parse_failed", error=str(exc))
        return None


def check_gwas_update(
    reference_engine: sa.Engine,
    settings: object | None = None,
    *,
    timeout: float = 30.0,
):
    """Check whether the GWAS Catalog release pinned in the manifest is newer than installed.

    Uses ``pipeline_pins["gwas_catalog"]`` from ``bundles/manifest.json`` as
    the authoritative source for the latest URL, then performs an HTTP HEAD
    on the pinned URL. The EBI GWAS Catalog publishes a rolling "latest"
    archive without a static release tag, so the remote version is derived
    from the response's ``Last-Modified`` header (formatted YYYYMMDD to
    match :func:`download_and_load_gwas`'s recorded value). The
    ``Content-Length`` response header populates the download-size estimate
    used by the bandwidth-window check. Returns ``None`` when the manifest
    pin is missing/unreachable, the HEAD call fails, ``Last-Modified`` is
    absent, or the recorded version is the same as or newer than the remote.

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
    from backend.db.manifest import get_pipeline_pin
    from backend.db.update_manager import VersionInfo, get_current_version

    pin = get_pipeline_pin("gwas_catalog", timeout=timeout)
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
        logger.warning("gwas_update_check_failed", error=str(exc))
        return None

    remote_version = _parse_last_modified_version(last_modified)
    if remote_version is None:
        return None

    current = get_current_version(reference_engine, "gwas_catalog")
    if current is not None and current >= remote_version:
        return None

    download_size = 0
    if content_length:
        try:
            download_size = int(content_length)
        except ValueError:
            download_size = 0

    return VersionInfo(
        db_name="gwas_catalog",
        latest_version=remote_version,
        download_url=pin.url,
        download_size_bytes=download_size,
        release_date=remote_version,
    )


# ── Download ──────────────────────────────────────────────────────────────


def download_gwas_catalog(
    dest_dir: Path,
    *,
    url: str = GWAS_CATALOG_URL,
    progress_callback: Callable[[int, int | None], None] | None = None,
    timeout: float = 600.0,
    meta: dict | None = None,
) -> Path:
    """Download the GWAS Catalog associations from EBI FTP.

    The source is a ZIP archive containing a single TSV file. Downloads
    the ZIP, extracts the TSV, and removes the ZIP.

    Args:
        dest_dir: Directory to save the downloaded file.
        url: Override URL (useful for testing).
        progress_callback: Called with (bytes_downloaded, total_bytes).
        timeout: HTTP request timeout in seconds.
        meta: Optional mutable dict populated with response metadata. When the
            server sends a ``Last-Modified`` header, ``meta["version"]`` is set
            to the parsed ``YYYYMMDD`` string so callers can record the same
            version :func:`check_gwas_update` compares against — captured from
            the existing download response, with no extra request.

    Returns:
        Path to the extracted TSV file.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_path = dest_dir / "gwas_catalog_associations.tsv"
    zip_path = dest_dir / "gwas_catalog_associations.zip"
    zip_tmp = dest_dir / "gwas_catalog_associations.zip.tmp"

    logger.info("gwas_download_start", url=url)

    # Resumable so a partial ZIP survives a failed/restarted build and the next run
    # continues via HTTP Range; the validator sidecar persists the If-Range token
    # across runs so a rotated upstream restarts clean instead of splicing.
    outcome = stream_download(
        url,
        zip_tmp,
        progress_callback=progress_callback,
        timeout=timeout,
        resumable=True,
        validator=read_validator_sidecar(zip_tmp),
        on_validator=lambda v: write_validator_sidecar(zip_tmp, v),
    )

    if meta is not None:
        remote_version = _parse_last_modified_version(outcome.headers.get("Last-Modified"))
        if remote_version:
            meta["version"] = remote_version

    # Atomic rename on success; drop the now-stale validator sidecar.
    zip_tmp.replace(zip_path)
    clear_validator_sidecar(zip_tmp)

    # Extract the TSV from the ZIP. Write to a temp file and atomically rename so
    # a failure mid-extraction (corrupt ZIP, disk full) never leaves a truncated
    # dest_path that a later run could silently load as if complete.
    dest_tmp = dest_path.with_suffix(dest_path.suffix + ".tmp")
    try:
        with zipfile.ZipFile(zip_path) as zf:
            tsv_names = [n for n in zf.namelist() if n.endswith(".tsv")]
            if not tsv_names:
                raise ValueError("No TSV file found inside GWAS Catalog ZIP")
            with zf.open(tsv_names[0]) as src, open(dest_tmp, "wb") as dst:
                while True:
                    chunk = src.read(65536)
                    if not chunk:
                        break
                    dst.write(chunk)
        dest_tmp.replace(dest_path)
    except BaseException:
        dest_tmp.unlink(missing_ok=True)
        raise
    finally:
        zip_path.unlink(missing_ok=True)

    logger.info("gwas_download_complete", path=str(dest_path))
    return dest_path


def download_and_load_gwas(
    engine: sa.Engine,
    dest_dir: Path,
    *,
    url: str = GWAS_CATALOG_URL,
    download_progress: Callable[[int, int | None], None] | None = None,
    parse_progress: Callable[[int], None] | None = None,
    timeout: float = 600.0,
) -> GWASLoadStats:
    """Full pipeline: download GWAS Catalog, parse with EFO filter, load into reference.db.

    Uses streaming parse + batch insert to keep memory usage low.
    """
    meta: dict = {}
    tsv_path = download_gwas_catalog(
        dest_dir,
        url=url,
        progress_callback=download_progress,
        timeout=timeout,
        meta=meta,
    )

    sha256 = _compute_sha256(tsv_path)

    row_iter = iter_gwas_tsv(tsv_path, progress_callback=parse_progress)
    stats = load_gwas_from_iter(row_iter, engine)
    stats.sha256 = sha256

    # Record the upstream Last-Modified date (YYYYMMDD), captured from the
    # download response above, so the recorded version matches the same
    # source/format check_gwas_update compares against. The EBI "latest" archive
    # has no static release tag. Fall back to the install date only when the
    # server did not provide a usable Last-Modified header.
    version = meta.get("version") or datetime.now(UTC).strftime("%Y%m%d")
    record_gwas_version(
        engine,
        version=version,
        file_path=str(tsv_path),
        file_size_bytes=tsv_path.stat().st_size,
        checksum=sha256,
    )

    return stats

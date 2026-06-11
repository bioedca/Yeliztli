"""CPIC data loader: allele definitions, diplotype→phenotype tables, and guidelines.

Parses CPIC machine-readable CSV files and bulk-loads them into three tables
in ``reference.db``: ``cpic_alleles``, ``cpic_diplotypes``, ``cpic_guidelines``.

Also provides lookup functions for downstream star-allele calling (P3-02).

Usage::

    from backend.annotation.cpic import (
        parse_cpic_alleles_csv,
        parse_cpic_diplotypes_csv,
        parse_cpic_guidelines_csv,
        load_cpic_into_db,
        lookup_alleles_by_gene,
        lookup_diplotypes_by_gene,
        lookup_guidelines_by_gene_drug,
    )

    alleles = parse_cpic_alleles_csv(Path("cpic_alleles.csv"))
    diplotypes = parse_cpic_diplotypes_csv(Path("cpic_diplotypes.csv"))
    guidelines = parse_cpic_guidelines_csv(Path("cpic_guidelines.csv"))
    stats = load_cpic_into_db(alleles, diplotypes, guidelines, engine)
"""

from __future__ import annotations

import csv
import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import httpx
import sqlalchemy as sa
import structlog

from backend.db.tables import (
    cpic_alleles,
    cpic_diplotypes,
    cpic_guidelines,
)

if TYPE_CHECKING:
    from collections.abc import Callable

logger = structlog.get_logger(__name__)

# Batch size for bulk inserts (executemany)
BATCH_SIZE = 5_000

# Supported CPIC genes (per PRD P3-02)
CPIC_GENES = frozenset(
    {
        "CYP2D6",
        "CYP2C19",
        "CYP2C9",
        "CYP3A5",
        "SLCO1B1",
        "DPYD",
        "TPMT",
        "UGT1A1",
        "NUDT15",
        "NAT2",
        "CYP2B6",
    }
)


@dataclass
class CPICLoadStats:
    """Statistics from a CPIC data load operation."""

    alleles_loaded: int = 0
    diplotypes_loaded: int = 0
    guidelines_loaded: int = 0
    alleles_skipped: int = 0
    diplotypes_skipped: int = 0
    guidelines_skipped: int = 0
    genes_found: set[str] = field(default_factory=set)
    sha256: str | None = None
    version: str | None = None


@dataclass
class CPICAllele:
    """A single CPIC allele definition record."""

    gene: str
    allele_name: str
    defining_variants: str  # JSON array of {rsid, ref, alt}
    function: str | None = None
    activity_score: float | None = None


@dataclass
class CPICDiplotype:
    """A single CPIC diplotype→phenotype mapping."""

    gene: str
    diplotype: str
    phenotype: str
    ehr_notation: str | None = None
    activity_score: float | None = None


@dataclass
class CPICGuideline:
    """A single CPIC drug guideline record."""

    gene: str
    drug: str
    phenotype: str
    recommendation: str | None = None
    classification: str | None = None
    guideline_url: str | None = None


def _parse_float(value: str | None) -> float | None:
    """Safely parse a float from a CSV field."""
    if not value or value.strip() == "":
        return None
    try:
        return float(value.strip())
    except (ValueError, TypeError):
        return None


def _allele_to_dict(record: CPICAllele) -> dict:
    """Convert a CPICAllele to a dict for database insertion."""
    return {
        "gene": record.gene,
        "allele_name": record.allele_name,
        "defining_variants": record.defining_variants,
        "function": record.function,
        "activity_score": record.activity_score,
    }


def _diplotype_to_dict(record: CPICDiplotype) -> dict:
    """Convert a CPICDiplotype to a dict for database insertion."""
    return {
        "gene": record.gene,
        "diplotype": record.diplotype,
        "phenotype": record.phenotype,
        "ehr_notation": record.ehr_notation,
        "activity_score": record.activity_score,
    }


def _guideline_to_dict(record: CPICGuideline) -> dict:
    """Convert a CPICGuideline to a dict for database insertion."""
    return {
        "gene": record.gene,
        "drug": record.drug,
        "phenotype": record.phenotype,
        "recommendation": record.recommendation,
        "classification": record.classification,
        "guideline_url": record.guideline_url,
    }


def parse_cpic_alleles_csv(
    csv_path: Path,
    *,
    progress_callback: Callable[[int], None] | None = None,
) -> tuple[list[dict], CPICLoadStats]:
    """Parse a CPIC allele definitions CSV file.

    Expected columns: gene, allele_name, defining_variants, function, activity_score

    Args:
        csv_path: Path to the allele definitions CSV.
        progress_callback: Optional callback called with row count.

    Returns:
        Tuple of (list of row dicts, CPICLoadStats).
    """
    rows: list[dict] = []
    stats = CPICLoadStats()

    with open(csv_path, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for i, row in enumerate(reader, 1):
            gene = row.get("gene", "").strip()
            allele_name = row.get("allele_name", "").strip()

            if not gene or not allele_name:
                stats.alleles_skipped += 1
                continue

            # Validate defining_variants is valid JSON
            raw_variants = row.get("defining_variants", "[]").strip()
            try:
                parsed = json.loads(raw_variants)
                if not isinstance(parsed, list):
                    raw_variants = "[]"
            except json.JSONDecodeError:
                raw_variants = "[]"

            record = CPICAllele(
                gene=gene,
                allele_name=allele_name,
                defining_variants=raw_variants,
                function=row.get("function", "").strip() or None,
                activity_score=_parse_float(row.get("activity_score", "")),
            )

            rows.append(_allele_to_dict(record))
            stats.alleles_loaded += 1
            stats.genes_found.add(gene)

            if progress_callback and i % 1000 == 0:
                progress_callback(i)

    return rows, stats


def parse_cpic_diplotypes_csv(
    csv_path: Path,
    *,
    progress_callback: Callable[[int], None] | None = None,
) -> tuple[list[dict], CPICLoadStats]:
    """Parse a CPIC diplotype→phenotype CSV file.

    Expected columns: gene, diplotype, phenotype, ehr_notation, activity_score

    Args:
        csv_path: Path to the diplotype CSV.
        progress_callback: Optional callback called with row count.

    Returns:
        Tuple of (list of row dicts, CPICLoadStats).
    """
    rows: list[dict] = []
    stats = CPICLoadStats()

    with open(csv_path, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for i, row in enumerate(reader, 1):
            gene = row.get("gene", "").strip()
            diplotype = row.get("diplotype", "").strip()
            phenotype = row.get("phenotype", "").strip()

            if not gene or not diplotype or not phenotype:
                stats.diplotypes_skipped += 1
                continue

            record = CPICDiplotype(
                gene=gene,
                diplotype=diplotype,
                phenotype=phenotype,
                ehr_notation=row.get("ehr_notation", "").strip() or None,
                activity_score=_parse_float(row.get("activity_score", "")),
            )

            rows.append(_diplotype_to_dict(record))
            stats.diplotypes_loaded += 1
            stats.genes_found.add(gene)

            if progress_callback and i % 1000 == 0:
                progress_callback(i)

    return rows, stats


def parse_cpic_guidelines_csv(
    csv_path: Path,
    *,
    progress_callback: Callable[[int], None] | None = None,
) -> tuple[list[dict], CPICLoadStats]:
    """Parse a CPIC drug guidelines CSV file.

    Expected columns: gene, drug, phenotype, recommendation, classification, guideline_url

    Args:
        csv_path: Path to the guidelines CSV.
        progress_callback: Optional callback called with row count.

    Returns:
        Tuple of (list of row dicts, CPICLoadStats).
    """
    rows: list[dict] = []
    stats = CPICLoadStats()

    with open(csv_path, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for i, row in enumerate(reader, 1):
            gene = row.get("gene", "").strip()
            drug = row.get("drug", "").strip()
            phenotype = row.get("phenotype", "").strip()

            if not gene or not drug or not phenotype:
                stats.guidelines_skipped += 1
                continue

            record = CPICGuideline(
                gene=gene,
                drug=drug,
                phenotype=phenotype,
                recommendation=row.get("recommendation", "").strip() or None,
                classification=row.get("classification", "").strip() or None,
                guideline_url=row.get("guideline_url", "").strip() or None,
            )

            rows.append(_guideline_to_dict(record))
            stats.guidelines_loaded += 1
            stats.genes_found.add(gene)

            if progress_callback and i % 1000 == 0:
                progress_callback(i)

    return rows, stats


def _wal_checkpoint(engine: sa.Engine) -> None:
    """Run WAL checkpoint if the engine is file-backed (not in-memory)."""
    url = str(engine.url)
    if url == "sqlite://" or ":memory:" in url:
        return
    with engine.connect() as conn:
        conn.execute(sa.text("PRAGMA wal_checkpoint(TRUNCATE)"))
        conn.commit()


def load_cpic_into_db(
    allele_rows: list[dict],
    diplotype_rows: list[dict],
    guideline_rows: list[dict],
    engine: sa.Engine,
    *,
    clear_existing: bool = True,
) -> CPICLoadStats:
    """Bulk-load parsed CPIC data into the three CPIC tables.

    Args:
        allele_rows: List of dicts matching cpic_alleles columns.
        diplotype_rows: List of dicts matching cpic_diplotypes columns.
        guideline_rows: List of dicts matching cpic_guidelines columns.
        engine: SQLAlchemy engine for reference.db.
        clear_existing: Whether to DELETE all existing rows first.

    Returns:
        CPICLoadStats with counts.
    """
    stats = CPICLoadStats(
        alleles_loaded=len(allele_rows),
        diplotypes_loaded=len(diplotype_rows),
        guidelines_loaded=len(guideline_rows),
    )

    # Collect unique genes
    for row in allele_rows:
        stats.genes_found.add(row["gene"])
    for row in diplotype_rows:
        stats.genes_found.add(row["gene"])
    for row in guideline_rows:
        stats.genes_found.add(row["gene"])

    with engine.begin() as conn:
        if clear_existing:
            conn.execute(cpic_guidelines.delete())
            conn.execute(cpic_diplotypes.delete())
            conn.execute(cpic_alleles.delete())

        # Bulk insert alleles in batches
        for i in range(0, len(allele_rows), BATCH_SIZE):
            batch = allele_rows[i : i + BATCH_SIZE]
            conn.execute(cpic_alleles.insert(), batch)

        # Bulk insert diplotypes in batches
        for i in range(0, len(diplotype_rows), BATCH_SIZE):
            batch = diplotype_rows[i : i + BATCH_SIZE]
            conn.execute(cpic_diplotypes.insert(), batch)

        # Bulk insert guidelines in batches
        for i in range(0, len(guideline_rows), BATCH_SIZE):
            batch = guideline_rows[i : i + BATCH_SIZE]
            conn.execute(cpic_guidelines.insert(), batch)

    # WAL checkpoint after bulk load
    _wal_checkpoint(engine)

    logger.info(
        "cpic_loaded",
        alleles=stats.alleles_loaded,
        diplotypes=stats.diplotypes_loaded,
        guidelines=stats.guidelines_loaded,
        genes=sorted(stats.genes_found),
    )

    return stats


def record_cpic_version(
    engine: sa.Engine,
    *,
    version: str,
    file_path: str | None = None,
    file_size_bytes: int | None = None,
    checksum: str | None = None,
) -> None:
    """Insert or update the CPIC version in the database_versions table."""
    from backend.db.database_registry import _record_db_version

    _record_db_version(
        engine,
        db_name="cpic",
        version=version,
        file_size_bytes=file_size_bytes,
        sha256=checksum,
        file_path=file_path,
    )


def record_pharmvar_version(
    engine: sa.Engine,
    *,
    version: str,
    checksum: str | None = None,
) -> None:
    """Record the PharmVar star-allele-definition source version (SW-E1).

    PharmVar (pharmvar.org) is the canonical, versioned source of star-allele
    definitions; the bundled CPIC allele table is aligned to this release. Tracked
    as its own ``database_versions`` row so the provenance/version of the
    definitions is auditable alongside CPIC. Definition source is gene/allele-keyed
    (GRCh37 plus-strand rsids), so no separate ``genome_build`` is recorded — it is
    intentionally absent from ``EXPECTED_GENOME_BUILD``.
    """
    from backend.db.database_registry import _record_db_version

    _record_db_version(
        engine,
        db_name="pharmvar",
        version=version,
        file_size_bytes=None,
        sha256=checksum,
    )


def check_cpic_update(
    reference_engine: sa.Engine,
    settings: object | None = None,
    *,
    timeout: float = 30.0,
):
    """Check whether a newer CPIC release is available on GitHub.

    Uses ``pipeline_pins["cpic"]`` from ``bundles/manifest.json`` to locate
    the GitHub releases-API endpoint (``…/repos/cpicpgx/cpic-data/releases/latest``),
    then performs an HTTP GET to read the latest release's ``tag_name``.
    The tag is compared against the recorded value in ``database_versions``;
    when they differ a :class:`VersionInfo` is returned with the release tag
    as ``latest_version`` and the first GitHub asset (if any) as the download
    URL/size. Returns ``None`` when the manifest pin is missing/unreachable,
    the GitHub API call fails, the payload lacks ``tag_name``, or the
    recorded version already matches the latest tag.

    Args:
        reference_engine: Reference DB engine for ``database_versions`` lookup.
        settings: Accepted for dispatch-signature parity with other
            ``check_*_update`` functions; unused.
        timeout: HTTP timeout in seconds for both the manifest fetch and GET.

    Returns:
        ``VersionInfo`` when the GitHub release tag differs from the recorded
        version, otherwise ``None``.
    """
    del settings  # unused; kept for dispatch-signature parity
    from backend.db.manifest import get_pipeline_pin
    from backend.db.update_manager import VersionInfo, get_current_version

    pin = get_pipeline_pin("cpic", timeout=timeout)
    if pin is None or not pin.url:
        return None

    try:
        with httpx.Client(
            follow_redirects=True,
            timeout=httpx.Timeout(timeout, connect=10.0),
        ) as client:
            resp = client.get(
                pin.url,
                headers={"Accept": "application/vnd.github.v3+json"},
            )
            resp.raise_for_status()
            payload = resp.json()
    except Exception as exc:
        logger.warning("cpic_update_check_failed", error=str(exc))
        return None

    if not isinstance(payload, dict):
        return None
    tag_name = payload.get("tag_name")
    if not isinstance(tag_name, str) or not tag_name:
        return None

    current = get_current_version(reference_engine, "cpic")
    if current is not None and current == tag_name:
        return None

    download_url = pin.url
    download_size = 0
    assets = payload.get("assets")
    if isinstance(assets, list) and assets:
        first = assets[0]
        if isinstance(first, dict):
            asset_url = first.get("browser_download_url")
            if isinstance(asset_url, str) and asset_url:
                download_url = asset_url
            asset_size = first.get("size")
            if isinstance(asset_size, int):
                download_size = asset_size

    release_date: str | None = None
    published_at = payload.get("published_at")
    if isinstance(published_at, str) and len(published_at) >= 10:
        release_date = published_at[:10]

    return VersionInfo(
        db_name="cpic",
        latest_version=tag_name,
        download_url=download_url,
        download_size_bytes=download_size,
        release_date=release_date,
    )


def _compute_sha256(path: Path) -> str:
    """Compute SHA-256 hex digest of a file."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def load_cpic_from_csvs(
    alleles_csv: Path,
    diplotypes_csv: Path,
    guidelines_csv: Path,
    engine: sa.Engine,
    *,
    clear_existing: bool = True,
    version: str | None = None,
) -> CPICLoadStats:
    """Full pipeline: parse all three CPIC CSVs and load into reference.db.

    Args:
        alleles_csv: Path to allele definitions CSV.
        diplotypes_csv: Path to diplotype→phenotype CSV.
        guidelines_csv: Path to drug guidelines CSV.
        engine: SQLAlchemy engine for reference.db.
        clear_existing: Whether to DELETE existing rows first.
        version: Optional version string. Defaults to current date (YYYYMMDD).

    Returns:
        CPICLoadStats with counts and metadata.
    """
    # Parse all three CSVs
    allele_rows, allele_stats = parse_cpic_alleles_csv(alleles_csv)
    diplotype_rows, diplo_stats = parse_cpic_diplotypes_csv(diplotypes_csv)
    guideline_rows, guide_stats = parse_cpic_guidelines_csv(guidelines_csv)

    # Load into database
    stats = load_cpic_into_db(
        allele_rows, diplotype_rows, guideline_rows, engine, clear_existing=clear_existing
    )

    # Carry over skip counts from parsing
    stats.alleles_skipped = allele_stats.alleles_skipped
    stats.diplotypes_skipped = diplo_stats.diplotypes_skipped
    stats.guidelines_skipped = guide_stats.guidelines_skipped

    # Compute combined checksum from all three files
    combined_hash = hashlib.sha256()
    combined_hash.update(_compute_sha256(alleles_csv).encode())
    combined_hash.update(_compute_sha256(diplotypes_csv).encode())
    combined_hash.update(_compute_sha256(guidelines_csv).encode())
    stats.sha256 = combined_hash.hexdigest()

    # Record version
    resolved_version = version or datetime.now(UTC).strftime("%Y%m%d")
    stats.version = resolved_version
    record_cpic_version(
        engine,
        version=resolved_version,
        checksum=stats.sha256,
    )

    return stats


# ═══════════════════════════════════════════════════════════════════════
# Pipeline entry point (for setup wizard build dispatch)
# ═══════════════════════════════════════════════════════════════════════

CPIC_DATA_DIR = Path(__file__).parent.parent / "data" / "cpic"


def download_and_load_cpic(
    engine: sa.Engine,
    dest_dir: Path,
    *,
    download_progress: Callable[[int, int | None], None] | None = None,
    parse_progress: Callable[[int], None] | None = None,
    timeout: float = 60.0,
) -> CPICLoadStats:
    """Load CPIC data from bundled CSV files into reference.db.

    This is the build-mode entry point called by the setup wizard's
    database download dispatcher. CPIC data ships as bundled CSVs
    rather than requiring an upstream download.

    The recorded version is the CPIC release tag the bundled CSVs were
    built from, taken from ``pipeline_pins["cpic"].last_known_version`` in
    ``bundles/manifest.json`` — the same release tag :func:`check_cpic_update`
    compares the GitHub-latest ``tag_name`` against. Sourcing it from the
    manifest (rather than a hardcoded literal) lets the equality check
    succeed once the pin carries a real tag, and keeps the version stamp in
    sync with the manifest source of truth. Falls back to ``"bundled"`` only
    when the manifest pin is missing or unreachable.
    """
    from backend.db.manifest import get_pipeline_pin

    alleles_csv = CPIC_DATA_DIR / "cpic_alleles.csv"
    diplotypes_csv = CPIC_DATA_DIR / "cpic_diplotypes.csv"
    guidelines_csv = CPIC_DATA_DIR / "cpic_guidelines.csv"

    if download_progress is not None:
        download_progress(50, 100)  # signal "download" half-done (bundled)

    pin = get_pipeline_pin("cpic", timeout=timeout)
    bundled_version = pin.last_known_version if pin and pin.last_known_version else "bundled"

    stats = load_cpic_from_csvs(
        alleles_csv, diplotypes_csv, guidelines_csv, engine, version=bundled_version
    )

    # SW-E1: record the PharmVar definition-source version alongside CPIC, so the
    # provenance of the star-allele definitions is auditable.
    pharmvar_pin = get_pipeline_pin("pharmvar", timeout=timeout)
    pharmvar_version = (
        pharmvar_pin.last_known_version
        if pharmvar_pin and pharmvar_pin.last_known_version
        else "bundled"
    )
    record_pharmvar_version(engine, version=pharmvar_version)

    if download_progress is not None:
        download_progress(100, 100)

    return stats


# ═══════════════════════════════════════════════════════════════════════
# CPIC Lookup Functions (for star-allele calling in P3-02)
# ═══════════════════════════════════════════════════════════════════════


def lookup_alleles_by_gene(
    gene: str,
    engine: sa.Engine,
) -> list[dict]:
    """Look up all CPIC allele definitions for a given gene.

    Args:
        gene: Gene symbol (e.g. "CYP2D6").
        engine: SQLAlchemy engine for reference.db.

    Returns:
        List of dicts with keys: allele_name, defining_variants (parsed JSON),
        function, activity_score.
    """
    with engine.connect() as conn:
        stmt = (
            sa.select(
                cpic_alleles.c.allele_name,
                cpic_alleles.c.defining_variants,
                cpic_alleles.c.function,
                cpic_alleles.c.activity_score,
            )
            .where(cpic_alleles.c.gene == gene)
            .order_by(cpic_alleles.c.allele_name)
        )

        rows = conn.execute(stmt).fetchall()

    results = []
    for row in rows:
        defining = row.defining_variants
        try:
            variants = json.loads(defining) if defining else []
        except json.JSONDecodeError:
            variants = []

        results.append(
            {
                "allele_name": row.allele_name,
                "defining_variants": variants,
                "function": row.function,
                "activity_score": row.activity_score,
            }
        )

    return results


def lookup_alleles_by_rsids(
    rsids: list[str],
    engine: sa.Engine,
) -> dict[str, list[dict]]:
    """Find alleles whose defining variants include the given rsids.

    This enables reverse lookup: given a sample's genotyped rsids, find
    which CPIC alleles they participate in.

    Args:
        rsids: List of rsid strings to search for.
        engine: SQLAlchemy engine for reference.db.

    Returns:
        Dict mapping rsid → list of {gene, allele_name, ref, alt, function,
        activity_score} for alleles that use that rsid.
    """
    if not rsids:
        return {}

    results: dict[str, list[dict]] = {}

    # Query all alleles that have non-empty defining_variants
    with engine.connect() as conn:
        stmt = sa.select(
            cpic_alleles.c.gene,
            cpic_alleles.c.allele_name,
            cpic_alleles.c.defining_variants,
            cpic_alleles.c.function,
            cpic_alleles.c.activity_score,
        ).where(cpic_alleles.c.defining_variants.isnot(None))

        rows = conn.execute(stmt).fetchall()

    rsid_set = set(rsids)

    for row in rows:
        try:
            variants = json.loads(row.defining_variants) if row.defining_variants else []
        except json.JSONDecodeError:
            continue

        for variant in variants:
            rsid = variant.get("rsid", "")
            if rsid in rsid_set:
                entry = {
                    "gene": row.gene,
                    "allele_name": row.allele_name,
                    "ref": variant.get("ref"),
                    "alt": variant.get("alt"),
                    "function": row.function,
                    "activity_score": row.activity_score,
                }
                results.setdefault(rsid, []).append(entry)

    return results


def lookup_diplotypes_by_gene(
    gene: str,
    engine: sa.Engine,
) -> list[dict]:
    """Look up all diplotype→phenotype mappings for a given gene.

    Args:
        gene: Gene symbol (e.g. "CYP2D6").
        engine: SQLAlchemy engine for reference.db.

    Returns:
        List of dicts with keys: diplotype, phenotype, ehr_notation, activity_score.
    """
    with engine.connect() as conn:
        stmt = (
            sa.select(
                cpic_diplotypes.c.diplotype,
                cpic_diplotypes.c.phenotype,
                cpic_diplotypes.c.ehr_notation,
                cpic_diplotypes.c.activity_score,
            )
            .where(cpic_diplotypes.c.gene == gene)
            .order_by(cpic_diplotypes.c.diplotype)
        )

        rows = conn.execute(stmt).fetchall()

    return [
        {
            "diplotype": row.diplotype,
            "phenotype": row.phenotype,
            "ehr_notation": row.ehr_notation,
            "activity_score": row.activity_score,
        }
        for row in rows
    ]


def lookup_guidelines_by_gene_drug(
    gene: str,
    drug: str,
    engine: sa.Engine,
) -> list[dict]:
    """Look up CPIC guidelines for a specific gene-drug pair.

    Args:
        gene: Gene symbol (e.g. "CYP2D6").
        drug: Drug name (e.g. "codeine").
        engine: SQLAlchemy engine for reference.db.

    Returns:
        List of dicts with keys: phenotype, recommendation, classification,
        guideline_url.
    """
    with engine.connect() as conn:
        stmt = (
            sa.select(
                cpic_guidelines.c.phenotype,
                cpic_guidelines.c.recommendation,
                cpic_guidelines.c.classification,
                cpic_guidelines.c.guideline_url,
            )
            .where(
                sa.and_(
                    cpic_guidelines.c.gene == gene,
                    cpic_guidelines.c.drug == drug,
                )
            )
            .order_by(cpic_guidelines.c.phenotype)
        )

        rows = conn.execute(stmt).fetchall()

    return [
        {
            "phenotype": row.phenotype,
            "recommendation": row.recommendation,
            "classification": row.classification,
            "guideline_url": row.guideline_url,
        }
        for row in rows
    ]


def lookup_guidelines_by_gene(
    gene: str,
    engine: sa.Engine,
) -> list[dict]:
    """Look up all CPIC guidelines for a gene (all drugs).

    Args:
        gene: Gene symbol (e.g. "CYP2D6").
        engine: SQLAlchemy engine for reference.db.

    Returns:
        List of dicts with keys: drug, phenotype, recommendation,
        classification, guideline_url.
    """
    with engine.connect() as conn:
        stmt = (
            sa.select(
                cpic_guidelines.c.drug,
                cpic_guidelines.c.phenotype,
                cpic_guidelines.c.recommendation,
                cpic_guidelines.c.classification,
                cpic_guidelines.c.guideline_url,
            )
            .where(cpic_guidelines.c.gene == gene)
            .order_by(cpic_guidelines.c.drug, cpic_guidelines.c.phenotype)
        )

        rows = conn.execute(stmt).fetchall()

    return [
        {
            "drug": row.drug,
            "phenotype": row.phenotype,
            "recommendation": row.recommendation,
            "classification": row.classification,
            "guideline_url": row.guideline_url,
        }
        for row in rows
    ]


def lookup_all_cpic_drugs(
    engine: sa.Engine,
) -> list[dict]:
    """Look up all unique gene-drug pairs with CPIC guidelines.

    Args:
        engine: SQLAlchemy engine for reference.db.

    Returns:
        List of dicts with keys: gene, drug, classification.
    """
    with engine.connect() as conn:
        stmt = (
            sa.select(
                cpic_guidelines.c.gene,
                cpic_guidelines.c.drug,
                sa.func.min(cpic_guidelines.c.classification).label("classification"),
            )
            .group_by(cpic_guidelines.c.gene, cpic_guidelines.c.drug)
            .order_by(cpic_guidelines.c.gene, cpic_guidelines.c.drug)
        )

        rows = conn.execute(stmt).fetchall()

    return [
        {
            "gene": row.gene,
            "drug": row.drug,
            "classification": row.classification,
        }
        for row in rows
    ]

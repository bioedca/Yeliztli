"""MONDO/HPO gene-phenotype loader and lookup.

Downloads the MONDO gene-disease association file (TSV) from the Monarch
Initiative, parses gene-phenotype records, and bulk-loads them into the
``gene_phenotype`` table in reference.db.  HPO phenotype annotations are
fetched from the HPO ``genes_to_phenotype.txt`` file and merged by gene
symbol.

Also provides a lookup function for querying gene-phenotype associations
by gene symbol, used during annotation (P2-15).

Usage::

    from backend.annotation.mondo_hpo import (
        download_mondo_hpo,
        load_mondo_hpo_from_csv,
        lookup_gene_phenotypes,
    )

    stats = download_and_load_mondo_hpo(reference_engine, dest_dir)
    phenotypes = lookup_gene_phenotypes(["BRCA1", "CFTR"], reference_engine)
"""

from __future__ import annotations

import csv
import functools
import gzip
import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import httpx
import sqlalchemy as sa
import structlog

from backend.annotation.bulk_load import serialized_write
from backend.annotation.http_download import stream_download
from backend.db.tables import gene_phenotype

if TYPE_CHECKING:
    from collections.abc import Callable

logger = structlog.get_logger(__name__)


# ── Gene-phenotype hygiene (validation strategy F14, F21) ─────────────────


@functools.lru_cache(maxsize=1)
def _load_inheritance_overrides() -> dict[str, str]:
    """Load curated gene→inheritance overrides (F14).

    The MONDO/HPO export stamps one gene-wide inheritance value (first-in-file)
    onto every disease, mislabelling classic dominant genes (BRCA1/2, LMNA, …)
    as recessive. These overrides assert the established mode of inheritance for
    the well-characterised genes the audit named. Returns ``{}`` if the file is
    missing/malformed (no override applied — falls back to the source value).
    """
    path = Path(__file__).resolve().parent.parent / "data" / "gene_inheritance_overrides.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.warning("gene_inheritance_overrides_unavailable", path=str(path))
        return {}
    return {str(k).upper(): v for k, v in data.get("overrides", {}).items()}


def _is_obsolete_disease(name: str | None) -> bool:
    """Whether a disease label is an ``obsolete *`` MONDO term (F21)."""
    return bool(name) and name.strip().lower().startswith("obsolete")


# ── Data source URLs ─────────────────────────────────────────────────────

# Monarch Initiative MONDO gene-disease associations (gzipped TSV, human only)
MONDO_GENE_DISEASE_URL = (
    "https://data.monarchinitiative.org/monarch-kg/latest/tsv/"
    "gene_associations/gene_disease.9606.tsv.gz"
)

# HPO genes-to-phenotype annotations
HPO_GENES_TO_PHENOTYPE_URL = "https://purl.obolibrary.org/obo/hp/hpoa/genes_to_phenotype.txt"

# Batch size for bulk inserts
BATCH_SIZE = 10_000


# ── Data classes ─────────────────────────────────────────────────────────


@dataclass
class GenePhenotypeRecord:
    """A single gene-phenotype association."""

    gene_symbol: str
    disease_name: str
    disease_id: str
    hpo_terms: list[str] = field(default_factory=list)
    source: str = "mondo_hpo"
    inheritance: str | None = None


@dataclass
class LoadStats:
    """Statistics from a MONDO/HPO load operation."""

    total_lines: int = 0
    records_loaded: int = 0
    skipped_no_gene: int = 0
    skipped_no_disease: int = 0
    skipped_duplicate: int = 0
    hpo_genes_mapped: int = 0
    sha256: str | None = None
    version: str | None = None


# ── Parse helpers ────────────────────────────────────────────────────────

# Known inheritance patterns from MONDO/HPO data
_INHERITANCE_MAP = {
    "HP:0000006": "Autosomal dominant",
    "HP:0000007": "Autosomal recessive",
    "HP:0001417": "X-linked",
    "HP:0001419": "X-linked recessive",
    "HP:0001423": "X-linked dominant",
    "HP:0001426": "Multifactorial",
    "HP:0001427": "Mitochondrial",
    "HP:0001428": "Somatic",
    "HP:0001450": "Y-linked",
    "HP:0010982": "Polygenic",
    "HP:0025352": "Autosomal dominant with reduced penetrance",
    "HP:0032113": "Semidominant",
}


def _extract_gene_symbol_from_subject(subject: str) -> str | None:
    """Extract gene symbol from Monarch subject column.

    Subject may be in formats like ``HGNC:1100`` or ``NCBIGene:672``.
    For label-based data, the gene symbol is in a separate column.
    """
    if not subject:
        return None
    # If it looks like a bare symbol (no colon prefix), return it
    if ":" not in subject:
        return subject.strip() if subject.strip() else None
    return None


def parse_mondo_gene_disease_tsv(
    tsv_path: Path,
) -> tuple[dict[str, list[GenePhenotypeRecord]], LoadStats]:
    """Parse the MONDO gene-disease association TSV.

    The Monarch Initiative gene_disease TSV has columns like:
    subject, subject_label, predicate, object, object_label, ...

    We extract gene symbol (subject_label) and disease (object_label,
    object as MONDO ID).

    Returns:
        Tuple of (dict mapping gene_symbol -> list of records, stats).
    """
    stats = LoadStats()
    records_by_gene: dict[str, list[GenePhenotypeRecord]] = {}
    seen: set[tuple[str, str]] = set()

    open_fn = gzip.open if tsv_path.suffix == ".gz" else open
    with open_fn(tsv_path, mode="rt", encoding="utf-8") as fh:
        reader = csv.DictReader(fh, delimiter="\t")
        for row in reader:
            stats.total_lines += 1

            # Extract gene symbol from subject_label column
            gene_symbol = (row.get("subject_label") or "").strip()
            if not gene_symbol:
                gene_symbol_alt = _extract_gene_symbol_from_subject(row.get("subject", ""))
                if gene_symbol_alt:
                    gene_symbol = gene_symbol_alt
                else:
                    stats.skipped_no_gene += 1
                    continue

            # Extract disease info
            disease_name = (row.get("object_label") or "").strip()
            disease_id = (row.get("object") or "").strip()
            if not disease_name:
                stats.skipped_no_disease += 1
                continue

            # Deduplicate by (gene, disease_id)
            dedup_key = (gene_symbol, disease_id)
            if dedup_key in seen:
                stats.skipped_duplicate += 1
                continue
            seen.add(dedup_key)

            record = GenePhenotypeRecord(
                gene_symbol=gene_symbol,
                disease_name=disease_name,
                disease_id=disease_id,
            )
            records_by_gene.setdefault(gene_symbol, []).append(record)

    return records_by_gene, stats


def parse_hpo_genes_to_phenotype(
    hpo_path: Path,
) -> dict[str, dict[str, list[str]]]:
    """Parse the HPO genes_to_phenotype.txt file.

    Returns a dict mapping gene_symbol -> {
        "hpo_terms": [HPO IDs],
        "inheritance": inheritance pattern or None
    }.

    The file format is tab-separated with columns:
    gene_id, gene_symbol, hpo_id, hpo_name, frequency, disease_id
    """
    gene_hpo: dict[str, set[str]] = {}
    gene_inheritance: dict[str, str | None] = {}

    with open(hpo_path, encoding="utf-8") as fh:
        for line in fh:
            if line.startswith("#"):
                continue
            parts = line.strip().split("\t")
            if len(parts) < 4:
                continue

            gene_symbol = parts[1].strip()
            hpo_id = parts[2].strip()

            if not gene_symbol or not hpo_id:
                continue

            gene_hpo.setdefault(gene_symbol, set()).add(hpo_id)

            # Check if this HPO term is an inheritance pattern
            if hpo_id in _INHERITANCE_MAP and gene_symbol not in gene_inheritance:
                gene_inheritance[gene_symbol] = _INHERITANCE_MAP[hpo_id]

    result: dict[str, dict[str, list[str]]] = {}
    for gene, terms in gene_hpo.items():
        # Filter out inheritance-pattern HPO terms from the phenotype list
        phenotype_terms = sorted(t for t in terms if t not in _INHERITANCE_MAP)
        result[gene] = {
            "hpo_terms": phenotype_terms,
            "inheritance": gene_inheritance.get(gene),
        }
    return result


# ── CSV seed loader ──────────────────────────────────────────────────────


def load_mondo_hpo_from_csv(
    csv_path: Path,
    engine: sa.Engine,
    *,
    clear_existing: bool = True,
) -> LoadStats:
    """Load gene-phenotype records from a seed CSV into reference.db.

    The CSV must have columns matching the gene_phenotype table:
    gene_symbol, disease_name, disease_id, hpo_terms, source, inheritance.

    Args:
        csv_path: Path to the CSV file.
        engine: SQLAlchemy engine for reference.db.
        clear_existing: Whether to DELETE existing mondo_hpo rows first.

    Returns:
        LoadStats with counts.
    """
    stats = LoadStats()
    rows: list[dict] = []

    with open(csv_path, encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            stats.total_lines += 1
            gene = row.get("gene_symbol", "").strip()
            disease = row.get("disease_name", "").strip()
            if not gene:
                stats.skipped_no_gene += 1
                continue
            if not disease:
                stats.skipped_no_disease += 1
                continue

            rows.append(
                {
                    "gene_symbol": gene,
                    "disease_name": disease,
                    "disease_id": row.get("disease_id", "").strip() or None,
                    "hpo_terms": row.get("hpo_terms", "").strip() or None,
                    "source": row.get("source", "mondo_hpo").strip(),
                    "inheritance": row.get("inheritance", "").strip() or None,
                }
            )

    stats.records_loaded = len(rows)

    # Guard: refuse a destructive clear when the CSV yielded no loadable rows —
    # an empty/malformed seed must never silently wipe the curated mondo_hpo rows.
    if clear_existing and not rows:
        raise ValueError(
            "Refusing to clear gene_phenotype (mondo_hpo) with 0 rows to load "
            "(likely an empty or malformed MONDO/HPO seed CSV)."
        )

    if clear_existing:
        with serialized_write(engine), engine.begin() as conn:
            conn.execute(gene_phenotype.delete().where(gene_phenotype.c.source == "mondo_hpo"))

    for i in range(0, len(rows), BATCH_SIZE):
        batch = rows[i : i + BATCH_SIZE]
        with serialized_write(engine), engine.begin() as conn:
            conn.execute(gene_phenotype.insert(), batch)

    _wal_checkpoint(engine)

    logger.info("mondo_hpo_csv_loaded", records=stats.records_loaded)
    return stats


# ── Full download + load pipeline ────────────────────────────────────────


def _compute_sha256(path: Path) -> str:
    """Compute SHA-256 hex digest of a file."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _parse_last_modified_version(last_modified: str | None) -> str | None:
    """Parse an HTTP ``Last-Modified`` header into a ``YYYYMMDD`` version string.

    Formats the header exactly the way :func:`check_mondo_hpo_update` does, so
    the recorded version (captured from the download response) and the
    update-check comparison stay consistent. Returns ``None`` when the header is
    absent or unparseable.
    """
    if not last_modified:
        return None
    from email.utils import parsedate_to_datetime

    try:
        return parsedate_to_datetime(last_modified).strftime("%Y%m%d")
    except (TypeError, ValueError) as exc:
        logger.warning("mondo_hpo_source_version_bad_last_modified", error=str(exc))
        return None


def _wal_checkpoint(engine: sa.Engine) -> None:
    """Run WAL checkpoint if the engine is file-backed."""
    url = str(engine.url)
    if url == "sqlite://" or ":memory:" in url:
        return
    # Serialize the exclusive TRUNCATE checkpoint with concurrent writers.
    with serialized_write(engine), engine.connect() as conn:
        conn.execute(sa.text("PRAGMA wal_checkpoint(TRUNCATE)"))
        conn.commit()


def download_file(
    url: str,
    dest_dir: Path,
    filename: str,
    *,
    progress_callback: Callable[[int, int | None], None] | None = None,
    timeout: float = 300.0,
    meta: dict | None = None,
) -> Path:
    """Download a file with streaming and atomic rename.

    Args:
        url: URL to download.
        dest_dir: Directory to save to.
        filename: Final filename.
        progress_callback: Optional (downloaded, total) callback.
        timeout: HTTP timeout seconds.
        meta: Optional mutable dict populated with response metadata. When the
            server sends a ``Last-Modified`` header, ``meta["version"]`` is set
            to the parsed ``YYYYMMDD`` string (captured from this download
            response, with no extra request).

    Returns:
        Path to the downloaded file.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_path = dest_dir / filename
    tmp_path = dest_dir / f"{filename}.tmp"

    logger.info("download_start", url=url, dest=str(dest_path))

    outcome = stream_download(
        url,
        tmp_path,
        progress_callback=progress_callback,
        timeout=timeout,
    )

    if meta is not None:
        source_version = _parse_last_modified_version(outcome.headers.get("Last-Modified"))
        if source_version:
            meta["version"] = source_version

    # Atomic rename on success (stream_download cleans up the .tmp on failure).
    tmp_path.replace(dest_path)

    logger.info("download_complete", path=str(dest_path))
    return dest_path


def _records_to_rows(
    records_by_gene: dict[str, list[GenePhenotypeRecord]],
    hpo_data: dict[str, dict[str, list[str]]],
) -> list[dict]:
    """Convert parsed records + HPO data into insert-ready row dicts."""
    rows: list[dict] = []
    for gene, recs in records_by_gene.items():
        hpo_info = hpo_data.get(gene, {})
        hpo_terms = hpo_info.get("hpo_terms", [])
        hpo_inheritance = hpo_info.get("inheritance")

        for rec in recs:
            # Use HPO-derived inheritance if the record doesn't have one
            inheritance = rec.inheritance or hpo_inheritance

            rows.append(
                {
                    "gene_symbol": rec.gene_symbol,
                    "disease_name": rec.disease_name,
                    "disease_id": rec.disease_id,
                    "hpo_terms": json.dumps(hpo_terms) if hpo_terms else None,
                    "source": "mondo_hpo",
                    "inheritance": inheritance,
                }
            )
    return rows


def load_mondo_hpo_rows(
    rows: list[dict],
    engine: sa.Engine,
    *,
    clear_existing: bool = True,
) -> int:
    """Bulk-load gene-phenotype rows into the gene_phenotype table.

    Args:
        rows: List of dicts matching gene_phenotype columns.
        engine: SQLAlchemy engine for reference.db.
        clear_existing: Delete existing mondo_hpo rows first.

    Returns:
        Number of rows loaded.
    """
    # Guard: refuse a destructive clear when there is nothing to load — an empty
    # or malformed parse must never silently wipe the curated mondo_hpo rows
    # (mirrors load_cpic_into_db / load_clingen_into_db).
    if clear_existing and not rows:
        raise ValueError(
            "Refusing to clear gene_phenotype (mondo_hpo) with 0 rows to load "
            "(likely an empty or malformed MONDO/HPO source)."
        )

    if clear_existing:
        with serialized_write(engine), engine.begin() as conn:
            conn.execute(gene_phenotype.delete().where(gene_phenotype.c.source == "mondo_hpo"))

    for i in range(0, len(rows), BATCH_SIZE):
        batch = rows[i : i + BATCH_SIZE]
        with serialized_write(engine), engine.begin() as conn:
            conn.execute(gene_phenotype.insert(), batch)

    _wal_checkpoint(engine)
    return len(rows)


def record_mondo_hpo_version(
    engine: sa.Engine,
    *,
    version: str,
    file_path: str | None = None,
    file_size_bytes: int | None = None,
    checksum: str | None = None,
) -> None:
    """Insert or update the MONDO/HPO version in database_versions."""
    from backend.db.database_registry import _record_db_version

    _record_db_version(
        engine,
        db_name="mondo_hpo",
        version=version,
        file_size_bytes=file_size_bytes,
        sha256=checksum,
        file_path=file_path,
    )


def check_mondo_hpo_update(
    reference_engine: sa.Engine,
    settings: object | None = None,
    *,
    timeout: float = 30.0,
):
    """Check whether the Monarch MONDO/HPO release pinned in the manifest is newer than installed.

    Uses ``pipeline_pins["mondo_hpo"]`` from ``bundles/manifest.json`` as the
    authoritative source for the latest URL, then performs an HTTP HEAD on
    the pinned URL. The Monarch Initiative publishes a rolling "latest"
    gene-disease archive without a static release tag, so the remote version
    is derived from the response's ``Last-Modified`` header (formatted
    YYYYMMDD to match :func:`download_and_load_mondo_hpo`'s recorded value).
    The ``Content-Length`` response header populates the download-size
    estimate used by the bandwidth-window check. Returns ``None`` when the
    manifest pin is missing/unreachable, the HEAD call fails,
    ``Last-Modified`` is absent, or the recorded version is the same as or
    newer than the remote.

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

    pin = get_pipeline_pin("mondo_hpo", timeout=timeout)
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
        logger.warning("mondo_hpo_update_check_failed", error=str(exc))
        return None

    if not last_modified:
        return None

    try:
        remote_version = parsedate_to_datetime(last_modified).strftime("%Y%m%d")
    except (TypeError, ValueError) as exc:
        logger.warning("mondo_hpo_update_check_bad_last_modified", error=str(exc))
        return None

    current = get_current_version(reference_engine, "mondo_hpo")
    if current is not None and current >= remote_version:
        return None

    download_size = 0
    if content_length:
        try:
            download_size = int(content_length)
        except ValueError:
            download_size = 0

    return VersionInfo(
        db_name="mondo_hpo",
        latest_version=remote_version,
        download_url=pin.url,
        download_size_bytes=download_size,
        release_date=remote_version,
    )


def download_and_load_mondo_hpo(
    engine: sa.Engine,
    dest_dir: Path,
    *,
    mondo_url: str = MONDO_GENE_DISEASE_URL,
    hpo_url: str = HPO_GENES_TO_PHENOTYPE_URL,
    download_progress: Callable[[int, int | None], None] | None = None,
    timeout: float = 300.0,
) -> LoadStats:
    """Full pipeline: download MONDO + HPO data, parse, merge, and load.

    Args:
        engine: SQLAlchemy engine for reference.db.
        dest_dir: Directory for downloaded files.
        mondo_url: Override URL for MONDO gene-disease TSV.
        hpo_url: Override URL for HPO genes-to-phenotype file.
        download_progress: Callback for download progress.
        timeout: HTTP timeout seconds.

    Returns:
        LoadStats with counts and metadata.
    """
    # Download MONDO gene-disease associations
    mondo_meta: dict = {}
    mondo_path = download_file(
        mondo_url,
        dest_dir,
        "gene_disease.9606.tsv.gz",
        progress_callback=download_progress,
        timeout=timeout,
        meta=mondo_meta,
    )

    # Download HPO genes-to-phenotype
    hpo_path = download_file(
        hpo_url,
        dest_dir,
        "genes_to_phenotype.txt",
        progress_callback=download_progress,
        timeout=timeout,
    )

    # Parse
    records_by_gene, stats = parse_mondo_gene_disease_tsv(mondo_path)
    hpo_data = parse_hpo_genes_to_phenotype(hpo_path)
    stats.hpo_genes_mapped = len(set(records_by_gene.keys()) & set(hpo_data.keys()))

    # Merge and load
    rows = _records_to_rows(records_by_gene, hpo_data)
    stats.records_loaded = load_mondo_hpo_rows(rows, engine)

    # Checksums and version
    sha256 = _compute_sha256(mondo_path)
    stats.sha256 = sha256
    # Record the source publication date (Last-Modified, captured from the MONDO
    # download response above) so the value read by get_current_version is the
    # same kind of date check_mondo_hpo_update compares against, avoiding false
    # "already up to date" skips. Fall back to the install date only when the
    # server did not provide a usable Last-Modified header.
    stats.version = mondo_meta.get("version") or datetime.now(UTC).strftime("%Y%m%d")

    record_mondo_hpo_version(
        engine,
        version=stats.version,
        file_path=str(mondo_path),
        file_size_bytes=mondo_path.stat().st_size,
        checksum=sha256,
    )

    logger.info(
        "mondo_hpo_loaded",
        records=stats.records_loaded,
        genes=len(records_by_gene),
        hpo_mapped=stats.hpo_genes_mapped,
    )

    return stats


# ═══════════════════════════════════════════════════════════════════════
# Gene-Phenotype Lookup (used by P2-15 annotation)
# ═══════════════════════════════════════════════════════════════════════


@dataclass
class GenePhenotypeAnnotation:
    """Gene-phenotype annotation for a single gene."""

    gene_symbol: str
    disease_name: str
    disease_id: str | None
    hpo_terms: list[str]
    source: str
    inheritance: str | None


def lookup_gene_phenotypes(
    gene_symbols: list[str],
    reference_engine: sa.Engine,
    *,
    source_filter: str | None = None,
) -> dict[str, list[GenePhenotypeAnnotation]]:
    """Look up gene-phenotype associations for a batch of gene symbols.

    Args:
        gene_symbols: List of gene symbol strings (e.g. ["BRCA1", "CFTR"]).
        reference_engine: SQLAlchemy engine for reference.db.
        source_filter: Optional filter by source ("mondo_hpo" or "omim").
            If None, returns all sources.

    Returns:
        Dict mapping gene_symbol -> list of GenePhenotypeAnnotation.
    """
    if not gene_symbols:
        return {}

    results: dict[str, list[GenePhenotypeAnnotation]] = {}

    with reference_engine.connect() as conn:
        for i in range(0, len(gene_symbols), 500):
            batch = gene_symbols[i : i + 500]

            conditions = [gene_phenotype.c.gene_symbol.in_(batch)]
            if source_filter:
                conditions.append(gene_phenotype.c.source == source_filter)

            stmt = (
                sa.select(
                    gene_phenotype.c.gene_symbol,
                    gene_phenotype.c.disease_name,
                    gene_phenotype.c.disease_id,
                    gene_phenotype.c.hpo_terms,
                    gene_phenotype.c.source,
                    gene_phenotype.c.inheritance,
                )
                .where(sa.and_(*conditions))
                # Deterministic order so "first record per gene" (the engine's
                # primary association) is reproducible, not MIN(id)=insertion
                # order (F23).
                .order_by(gene_phenotype.c.gene_symbol, gene_phenotype.c.disease_id)
            )

            rows = conn.execute(stmt).fetchall()

            overrides = _load_inheritance_overrides()
            for row in rows:
                # Drop obsolete MONDO terms so they never reach the user (F21).
                if _is_obsolete_disease(row.disease_name):
                    continue

                hpo_terms: list[str] = []
                if row.hpo_terms:
                    try:
                        hpo_terms = json.loads(row.hpo_terms)
                    except (json.JSONDecodeError, TypeError):
                        pass

                # Curated inheritance override for known-mislabelled genes (F14).
                inheritance = overrides.get((row.gene_symbol or "").upper(), row.inheritance)

                annot = GenePhenotypeAnnotation(
                    gene_symbol=row.gene_symbol,
                    disease_name=row.disease_name,
                    disease_id=row.disease_id,
                    hpo_terms=hpo_terms,
                    source=row.source,
                    inheritance=inheritance,
                )
                results.setdefault(row.gene_symbol, []).append(annot)

    return results

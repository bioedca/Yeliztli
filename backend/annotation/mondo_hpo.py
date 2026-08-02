"""MONDO/HPO gene-phenotype loader and lookup.

Downloads the MONDO gene-disease association file (TSV) from the Monarch
Initiative, parses gene-phenotype records, and bulk-loads them into the
``gene_phenotype`` table in reference.db. HPO phenotype annotations are
fetched from the HPO ``genes_to_phenotype.txt`` file and attached only when
their source disease identifier has a validated exact MONDO cross-reference.

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
import gzip
import hashlib
import json
import shutil
import tempfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, TypedDict
from urllib.parse import urlsplit, urlunsplit

import httpx
import sqlalchemy as sa
import structlog
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from backend.annotation.bulk_load import serialized_write
from backend.annotation.http_download import stream_download
from backend.db.tables import database_versions, gene_phenotype

if TYPE_CHECKING:
    from collections.abc import Callable

logger = structlog.get_logger(__name__)


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

# MONDO exact cross-references in SSSOM form. The loader intentionally uses
# only ``skos:exactMatch`` records from this authoritative MONDO export.
MONDO_SSSOM_URL = "https://purl.obolibrary.org/obo/mondo/mappings/mondo.sssom.tsv"

# A loader revision marker lets the update check rebuild labelled-but-gene-wide
# installations once, without conflating source release dates with schema data.
MONDO_HPO_INGESTION_REVISION = "disease-scope-v2"

# The authoritative MONDO SSSOM export had 109,306 unambiguous exact source
# identifiers on 2026-08-01. A large floor catches a valid-looking but heavily
# truncated mapping file before it can erase most disease-scoped HPO context.
MINIMUM_UNAMBIGUOUS_MONDO_XREFS = 50_000

# Batch size for bulk inserts
BATCH_SIZE = 10_000

# Immutable validated source bundles live beneath the configured downloads
# directory. A short-lived sentinel prevents a concurrent successful loader
# from pruning a bundle that has been published but not yet recorded in SQLite.
SOURCE_BUNDLE_DIRECTORY = "mondo_hpo_sources"
SOURCE_BUNDLE_MANIFEST = "mondo_hpo_sources.json"
SOURCE_BUNDLE_PENDING = ".mondo-hpo-pending"


# ── Data classes ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class HpoTerm:
    """A Human Phenotype Ontology term retained from the HPO export."""

    id: str
    name: str | None = None


class DiseaseHpoData(TypedDict):
    """HPO terms and inheritance for one source disease identifier."""

    hpo_terms: list[HpoTerm]
    inheritance: str | None


HpoDataByGene = dict[str, dict[str, DiseaseHpoData]]


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
    hpo_disease_matches: int = 0
    hpo_disease_unmatched: int = 0
    sha256: str | None = None
    hpo_sha256: str | None = None
    mondo_sssom_sha256: str | None = None
    version: str | None = None
    hpo_version: str | None = None
    mondo_sssom_version: str | None = None


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


def _is_hpo_id(value: str) -> bool:
    """Return whether *value* has the canonical ``HP:ddddddd`` shape."""
    prefix, separator, local_id = value.partition(":")
    return prefix == "HP" and separator == ":" and len(local_id) == 7 and local_id.isdigit()


def _is_mondo_id(value: str) -> bool:
    """Return whether *value* has the canonical ``MONDO:ddddddd`` shape."""
    prefix, separator, local_id = value.partition(":")
    return prefix == "MONDO" and separator == ":" and len(local_id) == 7 and local_id.isdigit()


def _normalize_source_disease_id(value: str) -> str | None:
    """Normalize a source disease CURIE without inferring equivalence.

    HPO currently writes Orphanet identifiers as ``ORPHA:`` while MONDO's
    SSSOM file writes the same namespace as ``Orphanet:``. This is a namespace
    spelling normalization only; all other equivalence decisions come from the
    explicit ``skos:exactMatch`` mapping file.
    """
    identifier = value.strip()
    prefix, separator, local_id = identifier.partition(":")
    if (
        not prefix
        or not separator
        or not local_id
        or any(char.isspace() for char in identifier)
        or not prefix[0].isalpha()
        or not all(char.isalnum() or char in "._-" for char in prefix)
    ):
        return None

    if prefix.upper() == "ORPHA":
        return f"Orphanet:{local_id}"
    if prefix.upper() == "MONDO":
        return f"MONDO:{local_id}"
    return identifier


def decode_hpo_terms(raw: str | None) -> list[HpoTerm]:
    """Decode legacy HPO ID arrays and current labelled term arrays.

    Older reference databases store ``["HP:0000001"]`` while newly ingested
    HPO data stores ``[{"id": "HP:0000001", "name": "All"}]``. Invalid
    entries are ignored so malformed reference data cannot leak arbitrary
    values into API responses. Input order is retained and duplicate IDs are
    collapsed, preferring a non-empty label when one is available.
    """
    if not raw:
        return []

    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []
    if not isinstance(payload, list):
        return []

    terms_by_id: dict[str, HpoTerm] = {}
    for item in payload:
        name: str | None = None
        if isinstance(item, str):
            hpo_id = item.strip()
        elif isinstance(item, dict):
            raw_id = item.get("id")
            if not isinstance(raw_id, str):
                continue
            hpo_id = raw_id.strip()
            raw_name = item.get("name")
            if isinstance(raw_name, str):
                name = raw_name.strip() or None
        else:
            continue

        if not _is_hpo_id(hpo_id):
            continue

        existing = terms_by_id.get(hpo_id)
        if existing is None or (existing.name is None and name is not None):
            terms_by_id[hpo_id] = HpoTerm(id=hpo_id, name=name)

    return list(terms_by_id.values())


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
            if not disease_name or not _is_mondo_id(disease_id):
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
) -> HpoDataByGene:
    """Parse the HPO genes_to_phenotype.txt file.

    Returns a dict mapping each ``gene_symbol`` to source disease identifiers,
    then to ``hpo_terms`` and ``inheritance``. Terms are never pooled across
    source diseases for the same gene.

    The file format is tab-separated with columns:
    gene_id, gene_symbol, hpo_id, hpo_name, frequency, disease_id
    """
    gene_hpo: dict[str, dict[str, dict[str, HpoTerm]]] = {}
    gene_inheritance: dict[str, dict[str, set[str]]] = {}

    with open(hpo_path, encoding="utf-8") as fh:
        for line in fh:
            if line.startswith("#"):
                continue
            # ``rstrip`` preserves leading/trailing tab fields so an omitted
            # disease identifier cannot silently turn into a four-column row.
            parts = line.rstrip("\r\n").split("\t")
            if len(parts) < 6:
                continue

            gene_symbol = parts[1].strip()
            hpo_id = parts[2].strip()
            hpo_name = parts[3].strip() or None
            source_disease_id = _normalize_source_disease_id(parts[5])

            if not gene_symbol or not _is_hpo_id(hpo_id) or source_disease_id is None:
                continue

            terms_by_id = gene_hpo.setdefault(gene_symbol, {}).setdefault(source_disease_id, {})
            existing = terms_by_id.get(hpo_id)
            if existing is None or (existing.name is None and hpo_name is not None):
                terms_by_id[hpo_id] = HpoTerm(id=hpo_id, name=hpo_name)

            # Check if this HPO term is an inheritance pattern
            if hpo_id in _INHERITANCE_MAP:
                gene_inheritance.setdefault(gene_symbol, {}).setdefault(
                    source_disease_id, set()
                ).add(_INHERITANCE_MAP[hpo_id])

    result: HpoDataByGene = {}
    for gene, diseases in gene_hpo.items():
        result[gene] = {}
        for source_disease_id, terms in diseases.items():
            # Filter out inheritance-pattern HPO terms from the phenotype list.
            phenotype_terms = sorted(
                (term for term in terms.values() if term.id not in _INHERITANCE_MAP),
                key=lambda term: term.id,
            )
            inheritance_values = gene_inheritance.get(gene, {}).get(source_disease_id, set())
            # The storage contract has one scalar inheritance value. Multiple
            # distinct source modes are ambiguous, so withhold rather than pick
            # an arbitrary mode.
            inheritance = sorted(inheritance_values)[0] if len(inheritance_values) == 1 else None
            result[gene][source_disease_id] = {
                "hpo_terms": phenotype_terms,
                "inheritance": inheritance,
            }
    return result


def parse_mondo_sssom(sssom_path: Path) -> dict[str, str]:
    """Return unambiguous source-disease → MONDO exact cross-references.

    The MONDO SSSOM export can contain broad/narrow mappings and multiple
    exact targets for one source identifier. Broad/narrow mappings are never
    sufficient for this clinical-context join, and ambiguous exact targets are
    deliberately omitted instead of guessing.
    """
    candidates: dict[str, set[str]] = {}
    with open(sssom_path, encoding="utf-8") as fh:
        reader = csv.DictReader((line for line in fh if not line.startswith("#")), delimiter="\t")
        for row in reader:
            if row.get("predicate_id") != "skos:exactMatch":
                continue
            mondo_id = (row.get("subject_id") or "").strip()
            source_disease_id = _normalize_source_disease_id(row.get("object_id") or "")
            if not _is_mondo_id(mondo_id) or source_disease_id is None:
                continue
            candidates.setdefault(source_disease_id, set()).add(mondo_id)

    return {
        source_disease_id: next(iter(mondo_ids))
        for source_disease_id, mondo_ids in candidates.items()
        if len(mondo_ids) == 1
    }


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


def _source_manifest_entry(
    path: Path, url: str, role: str, meta: dict[str, str]
) -> dict[str, str | int | None]:
    """Build a public, portable provenance entry for one loader input."""
    return {
        "role": role,
        "url": _public_source_url(url),
        "filename": path.name,
        "size_bytes": path.stat().st_size,
        "sha256": _compute_sha256(path),
        "etag": meta.get("etag"),
        "last_modified": meta.get("last_modified"),
        "version": meta.get("version"),
    }


def _source_validator_fingerprint(meta: dict[str, str]) -> str | None:
    """Return a stable, non-secret marker for a source response validator."""
    validator = meta.get("etag") or meta.get("last_modified")
    if not validator:
        return None
    return hashlib.sha256(validator.encode("utf-8")).hexdigest()[:16]


def _mondo_hpo_install_version(
    primary_version: str,
    hpo_meta: dict[str, str],
    mondo_sssom_meta: dict[str, str],
) -> str:
    """Build the stored version from the primary date and secondary validators."""
    hpo_fingerprint = _source_validator_fingerprint(hpo_meta) or "unknown"
    sssom_fingerprint = _source_validator_fingerprint(mondo_sssom_meta) or "unknown"
    return (
        f"{primary_version}+{MONDO_HPO_INGESTION_REVISION}"
        f"+hpo-{hpo_fingerprint}+mondo-sssom-{sssom_fingerprint}"
    )


def _version_component(version: str, prefix: str) -> str | None:
    """Extract a ``+``-delimited version component with *prefix*."""
    return next(
        (
            component.removeprefix(prefix)
            for component in version.split("+")
            if component.startswith(prefix)
        ),
        None,
    )


def _public_source_url(url: str) -> str:
    """Return a provenance-safe URL without credentials or request parameters.

    Source URLs are operator-supplied overrides in tests and maintenance tools,
    so a durable manifest and structured log must never retain a signed URL,
    password, query parameter, or fragment.
    """
    try:
        parsed = urlsplit(url)
        if not parsed.scheme or not parsed.hostname:
            return "<invalid source URL>"
        hostname = parsed.hostname
        host = f"[{hostname}]" if ":" in hostname else hostname
        port = f":{parsed.port}" if parsed.port is not None else ""
    except ValueError:
        return "<invalid source URL>"
    return urlunsplit((parsed.scheme, f"{host}{port}", parsed.path, "", ""))


def _write_source_manifest(
    dest_dir: Path, sources: list[dict[str, str | int | None]]
) -> tuple[Path, str]:
    """Write the source-level provenance manifest atomically and return its hash."""
    manifest_path = dest_dir / SOURCE_BUNDLE_MANIFEST
    temp_path = manifest_path.with_suffix(".json.tmp")
    payload = {
        "schema_version": 1,
        "ingestion_revision": MONDO_HPO_INGESTION_REVISION,
        "retrieved_at": datetime.now(UTC).isoformat(),
        "sources": sources,
    }
    temp_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temp_path.replace(manifest_path)
    return manifest_path, _compute_sha256(manifest_path)


def _source_bundle_id(sources: list[dict[str, str | int | None]]) -> str:
    """Return a stable content/validator identifier for an immutable source bundle."""
    payload = {
        "ingestion_revision": MONDO_HPO_INGESTION_REVISION,
        "sources": sources,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _publish_staged_source_bundle(
    staging_dir: Path,
    dest_dir: Path,
    bundle_id: str,
    sources: list[dict[str, str | int | None]],
) -> tuple[Path, bool]:
    """Publish validated inputs and say whether this call created the bundle."""
    bundles_dir = dest_dir / SOURCE_BUNDLE_DIRECTORY
    bundles_dir.mkdir(parents=True, exist_ok=True)
    bundle_dir = bundles_dir / bundle_id
    if bundle_dir.exists():
        manifest_path = bundle_dir / SOURCE_BUNDLE_MANIFEST
        try:
            existing = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(
                f"Existing MONDO/HPO source bundle is not valid: {bundle_dir}"
            ) from exc
        if (
            existing.get("ingestion_revision") != MONDO_HPO_INGESTION_REVISION
            or existing.get("sources") != sources
        ):
            raise ValueError(f"MONDO/HPO source bundle ID collision: {bundle_dir}")
        shutil.rmtree(staging_dir)
        return bundle_dir, False

    staging_dir.replace(bundle_dir)
    return bundle_dir, True


def _source_bundle_for_file_path(file_path: str | None, bundles_dir: Path) -> Path | None:
    """Return a direct managed bundle parent for a recorded primary source path."""
    if not file_path:
        return None
    primary_path = Path(file_path)
    bundle_dir = primary_path.parent
    if bundle_dir.parent != bundles_dir:
        return None
    return bundle_dir


def _recorded_source_bundle(engine: sa.Engine, bundles_dir: Path) -> Path | None:
    """Find the currently recorded managed MONDO/HPO source bundle, if any."""
    with engine.connect() as conn:
        file_path = conn.execute(
            sa.select(database_versions.c.file_path).where(
                database_versions.c.db_name == "mondo_hpo"
            )
        ).scalar_one_or_none()
    return _source_bundle_for_file_path(file_path, bundles_dir)


def _is_prunable_source_bundle(bundle_dir: Path) -> bool:
    """Whether a bundle is a complete, current-schema directory owned by this loader."""
    if bundle_dir.is_symlink() or not bundle_dir.is_dir():
        return False
    pending_path = bundle_dir / SOURCE_BUNDLE_PENDING
    if pending_path.exists() or pending_path.is_symlink():
        return False

    manifest_path = bundle_dir / SOURCE_BUNDLE_MANIFEST
    if manifest_path.is_symlink() or not manifest_path.is_file():
        return False
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False

    if not isinstance(manifest, dict):
        return False
    sources = manifest.get("sources")
    if not (
        manifest.get("schema_version") == 1
        and manifest.get("ingestion_revision") == MONDO_HPO_INGESTION_REVISION
        and isinstance(sources, list)
        and all(isinstance(source, dict) for source in sources)
    ):
        return False
    return bundle_dir.name == _source_bundle_id(sources)


def _prune_source_bundles(
    engine: sa.Engine,
    dest_dir: Path,
    *,
    current_bundle: Path,
    previous_bundle: Path | None,
) -> None:
    """Retain the active and immediately previous valid source bundles.

    A new bundle carries :data:`SOURCE_BUNDLE_PENDING` until its rows and
    version are committed. That makes pruning safe even for direct callers that
    are not wrapped by the higher-level cross-process build claim.
    """
    bundles_dir = dest_dir / SOURCE_BUNDLE_DIRECTORY
    try:
        with serialized_write(engine):
            recorded_bundle = _recorded_source_bundle(engine, bundles_dir)
            retained = {current_bundle, previous_bundle, recorded_bundle}
            for candidate in bundles_dir.iterdir():
                if candidate in retained or not _is_prunable_source_bundle(candidate):
                    continue
                try:
                    shutil.rmtree(candidate)
                except OSError as exc:
                    logger.warning(
                        "mondo_hpo_source_bundle_cleanup_failed",
                        bundle_name=candidate.name,
                        error=str(exc),
                    )
    except OSError as exc:
        logger.warning("mondo_hpo_source_bundle_cleanup_failed", error=str(exc))


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

    logger.info("download_start", url=_public_source_url(url), dest=str(dest_path))

    outcome = stream_download(
        url,
        tmp_path,
        progress_callback=progress_callback,
        timeout=timeout,
    )

    if meta is not None:
        last_modified = outcome.headers.get("Last-Modified")
        meta["etag"] = outcome.headers.get("ETag", "")
        meta["last_modified"] = last_modified or ""
        source_version = _parse_last_modified_version(last_modified)
        if source_version:
            meta["version"] = source_version

    # Atomic rename on success (stream_download cleans up the .tmp on failure).
    tmp_path.replace(dest_path)

    logger.info("download_complete", path=str(dest_path))
    return dest_path


def _resolve_source_disease_to_mondo(
    source_disease_id: str,
    mondo_xrefs: dict[str, str],
) -> str | None:
    """Resolve one source disease only through a direct MONDO ID or exact xref."""
    if _is_mondo_id(source_disease_id):
        return source_disease_id
    return mondo_xrefs.get(source_disease_id)


def _hpo_data_for_record(
    record: GenePhenotypeRecord,
    hpo_by_source_disease: dict[str, DiseaseHpoData],
    mondo_xrefs: dict[str, str],
) -> DiseaseHpoData | None:
    """Merge only source HPO scopes that resolve exactly to *record*."""
    terms_by_id: dict[str, HpoTerm] = {}
    inheritance_values: set[str] = set()
    found = False

    for source_disease_id, hpo_info in hpo_by_source_disease.items():
        if _resolve_source_disease_to_mondo(source_disease_id, mondo_xrefs) != record.disease_id:
            continue
        found = True
        for term in hpo_info["hpo_terms"]:
            existing = terms_by_id.get(term.id)
            if existing is None or (existing.name is None and term.name is not None):
                terms_by_id[term.id] = term
        if hpo_info["inheritance"]:
            inheritance_values.add(hpo_info["inheritance"])

    if not found:
        return None

    return {
        "hpo_terms": sorted(terms_by_id.values(), key=lambda term: term.id),
        "inheritance": sorted(inheritance_values)[0] if len(inheritance_values) == 1 else None,
    }


def _count_scoped_hpo_matches(
    records_by_gene: dict[str, list[GenePhenotypeRecord]],
    hpo_data: HpoDataByGene,
    mondo_xrefs: dict[str, str],
) -> tuple[int, int]:
    """Count source disease scopes that do and do not resolve to loaded MONDO rows."""
    matches = 0
    unmatched = 0
    for gene, hpo_by_source_disease in hpo_data.items():
        mondo_ids = {record.disease_id for record in records_by_gene.get(gene, [])}
        for source_disease_id in hpo_by_source_disease:
            if _resolve_source_disease_to_mondo(source_disease_id, mondo_xrefs) in mondo_ids:
                matches += 1
            else:
                unmatched += 1
    return matches, unmatched


def _records_to_rows(
    records_by_gene: dict[str, list[GenePhenotypeRecord]],
    hpo_data: HpoDataByGene,
    mondo_xrefs: dict[str, str],
) -> list[dict]:
    """Convert parsed records + disease-scoped HPO data into insert-ready rows."""
    rows: list[dict] = []
    for gene, recs in records_by_gene.items():
        hpo_by_source_disease = hpo_data.get(gene, {})

        for rec in recs:
            hpo_info = _hpo_data_for_record(rec, hpo_by_source_disease, mondo_xrefs)
            hpo_terms = hpo_info["hpo_terms"] if hpo_info else []
            hpo_inheritance = hpo_info["inheritance"] if hpo_info else None
            serialized_hpo_terms = [{"id": term.id, "name": term.name} for term in hpo_terms]
            # Use HPO-derived inheritance if the record doesn't have one
            inheritance = rec.inheritance or hpo_inheritance

            rows.append(
                {
                    "gene_symbol": rec.gene_symbol,
                    "disease_name": rec.disease_name,
                    "disease_id": rec.disease_id,
                    "hpo_terms": (
                        json.dumps(serialized_hpo_terms) if serialized_hpo_terms else None
                    ),
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
    version: str | None = None,
    file_path: str | None = None,
    file_size_bytes: int | None = None,
    checksum: str | None = None,
) -> int:
    """Atomically bulk-load MONDO/HPO rows and optional source provenance.

    Args:
        rows: List of dicts matching gene_phenotype columns.
        engine: SQLAlchemy engine for reference.db.
        clear_existing: Delete existing mondo_hpo rows first.
        version: Installed source version to upsert in the same transaction.
        file_path: Primary source path recorded with *version*.
        file_size_bytes: Primary source size recorded with *version*.
        checksum: Primary source SHA-256 recorded with *version*.

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

    # Delete, every insert batch, and the version row share one transaction so
    # a failed load cannot expose partial disease rows or a mismatched version.
    with serialized_write(engine), engine.begin() as conn:
        if clear_existing:
            conn.execute(gene_phenotype.delete().where(gene_phenotype.c.source == "mondo_hpo"))

        for i in range(0, len(rows), BATCH_SIZE):
            batch = rows[i : i + BATCH_SIZE]
            conn.execute(gene_phenotype.insert(), batch)

        if version is not None:
            _record_mondo_hpo_version_on_connection(
                conn,
                version=version,
                file_path=file_path,
                file_size_bytes=file_size_bytes,
                checksum=checksum,
            )

    _wal_checkpoint(engine)
    return len(rows)


def _record_mondo_hpo_version_on_connection(
    conn: sa.Connection,
    *,
    version: str,
    file_path: str | None,
    file_size_bytes: int | None,
    checksum: str | None,
) -> None:
    """Upsert the MONDO/HPO version using an existing write transaction."""
    now = datetime.now(UTC)
    values = {
        "db_name": "mondo_hpo",
        "version": version,
        "file_path": file_path,
        "file_size_bytes": file_size_bytes,
        "downloaded_at": now,
        "checksum_sha256": checksum,
        "genome_build": None,
    }
    stmt = sqlite_insert(database_versions).values(**values)
    stmt = stmt.on_conflict_do_update(
        index_elements=[database_versions.c.db_name],
        set_={
            "version": stmt.excluded.version,
            "file_path": stmt.excluded.file_path,
            "file_size_bytes": stmt.excluded.file_size_bytes,
            "downloaded_at": stmt.excluded.downloaded_at,
            "checksum_sha256": stmt.excluded.checksum_sha256,
            "genome_build": stmt.excluded.genome_build,
        },
    )
    conn.execute(stmt)


def record_mondo_hpo_version(
    engine: sa.Engine,
    *,
    version: str,
    file_path: str | None = None,
    file_size_bytes: int | None = None,
    checksum: str | None = None,
) -> None:
    """Insert or update the MONDO/HPO version in database_versions."""
    with serialized_write(engine), engine.begin() as conn:
        _record_mondo_hpo_version_on_connection(
            conn,
            version=version,
            file_path=file_path,
            file_size_bytes=file_size_bytes,
            checksum=checksum,
        )


def _hpo_labels_need_refresh(reference_engine: sa.Engine) -> bool:
    """Whether installed MONDO/HPO rows still use the legacy ID-only shape."""
    stmt = (
        sa.select(gene_phenotype.c.hpo_terms)
        .distinct()
        .where(
            gene_phenotype.c.source == "mondo_hpo",
            gene_phenotype.c.hpo_terms.is_not(None),
            gene_phenotype.c.hpo_terms.notin_(("", "[]")),
        )
    )
    try:
        with reference_engine.connect() as conn:
            stored_payloads = conn.execute(stmt).scalars()
            for raw in stored_payloads:
                try:
                    payload = json.loads(raw)
                except (json.JSONDecodeError, TypeError):
                    return True

                if not (
                    isinstance(payload, list)
                    and bool(payload)
                    and all(
                        isinstance(item, dict)
                        and isinstance(item.get("id"), str)
                        and _is_hpo_id(item["id"])
                        and "name" in item
                        and (item["name"] is None or isinstance(item["name"], str))
                        for item in payload
                    )
                ):
                    return True
    except sa.exc.SQLAlchemyError as exc:
        logger.warning("mondo_hpo_label_shape_check_failed", error=str(exc))
        return False

    return False


def _mondo_hpo_source_version(version: str) -> str:
    """Extract the primary MONDO source date from an installed loader version."""
    return version.partition("+")[0]


def _is_mondo_hpo_source_date(version: str) -> bool:
    """Whether *version* is a comparable primary MONDO source date."""
    return len(version) == 8 and version.isdigit()


def _has_current_ingestion_revision(version: str) -> bool:
    """Whether *version* records the current disease-scoped loader revision."""
    return MONDO_HPO_INGESTION_REVISION in version.split("+")


def _has_current_secondary_validators(
    version: str,
    hpo_meta: dict[str, str],
    mondo_sssom_meta: dict[str, str],
) -> bool:
    """Whether stored secondary validators equal the freshly fetched values."""
    hpo_fingerprint = _source_validator_fingerprint(hpo_meta)
    sssom_fingerprint = _source_validator_fingerprint(mondo_sssom_meta)
    if hpo_fingerprint is None or sssom_fingerprint is None:
        return False
    return (
        _version_component(version, "hpo-") == hpo_fingerprint
        and _version_component(version, "mondo-sssom-") == sssom_fingerprint
    )


def _is_legacy_disease_scope_install(version: str | None) -> bool:
    """Whether an installed version lacks proof of disease-scoped HPO rows."""
    return version is not None and not _has_current_ingestion_revision(version)


def _content_length_or_zero(response: httpx.Response) -> int:
    """Return a non-negative response content length when the server provides one."""
    content_length = response.headers.get("Content-Length")
    if not content_length:
        return 0
    try:
        return max(0, int(content_length))
    except ValueError:
        return 0


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
    YYYYMMDD to match the primary date in
    :func:`download_and_load_mondo_hpo`'s recorded value).
    The ``Content-Length`` response header populates the download-size
    estimate used by the bandwidth-window check. When the primary source date
    matches, it also compares HPO and MONDO SSSOM response validators recorded
    in the installed version. A missing validator is fail-closed and offers a
    rebuild rather than silently declaring secondary inputs current.

    Args:
        reference_engine: Reference DB engine for ``database_versions`` lookup.
        settings: Accepted for dispatch-signature parity with other
            ``check_*_update`` functions; unused.
        timeout: HTTP timeout in seconds for the manifest fetch and source HEAD requests.

    Returns:
        ``VersionInfo`` when the primary source date is newer, a same-date
        installation needs a content migration, or an HPO/SSSOM validator
        changed; otherwise ``None``. A newer primary source is never
        downgraded.
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

    primary_size = _content_length_or_zero(resp)
    current = get_current_version(reference_engine, "mondo_hpo")
    current_source_version: str | None = None
    if current is not None:
        current_source_version = _mondo_hpo_source_version(current)
        if not _is_mondo_hpo_source_date(current_source_version):
            logger.warning("mondo_hpo_update_check_uncomparable_installed_version")
            return None
        if current_source_version > remote_version:
            return None

    try:
        with httpx.Client(
            follow_redirects=True,
            timeout=httpx.Timeout(timeout, connect=10.0),
        ) as client:
            hpo_resp = client.head(HPO_GENES_TO_PHENOTYPE_URL)
            hpo_resp.raise_for_status()
            sssom_resp = client.head(MONDO_SSSOM_URL)
            sssom_resp.raise_for_status()
    except Exception as exc:
        logger.warning("mondo_hpo_secondary_update_check_failed", error=str(exc))
        return VersionInfo(
            db_name="mondo_hpo",
            latest_version=remote_version,
            download_url=pin.url,
            download_size_bytes=primary_size,
            release_date=remote_version,
        )

    # Setup fetches all three source files, so bandwidth policy needs their
    # complete known transfer footprint rather than only the small primary TSV.
    available = VersionInfo(
        db_name="mondo_hpo",
        latest_version=remote_version,
        download_url=pin.url,
        download_size_bytes=(
            primary_size + _content_length_or_zero(hpo_resp) + _content_length_or_zero(sssom_resp)
        ),
        release_date=remote_version,
    )
    if current is None or current_source_version < remote_version:
        return available
    if not _has_current_ingestion_revision(current) or _hpo_labels_need_refresh(reference_engine):
        return available

    hpo_meta = {
        "etag": hpo_resp.headers.get("ETag", ""),
        "last_modified": hpo_resp.headers.get("Last-Modified", ""),
    }
    mondo_sssom_meta = {
        "etag": sssom_resp.headers.get("ETag", ""),
        "last_modified": sssom_resp.headers.get("Last-Modified", ""),
    }
    if _has_current_secondary_validators(current, hpo_meta, mondo_sssom_meta):
        return None

    logger.warning("mondo_hpo_secondary_source_changed_or_unverifiable")
    return available


def download_and_load_mondo_hpo(
    engine: sa.Engine,
    dest_dir: Path,
    *,
    mondo_url: str = MONDO_GENE_DISEASE_URL,
    hpo_url: str = HPO_GENES_TO_PHENOTYPE_URL,
    mondo_sssom_url: str = MONDO_SSSOM_URL,
    download_progress: Callable[[int, int | None], None] | None = None,
    timeout: float = 300.0,
) -> LoadStats:
    """Full pipeline: download MONDO, HPO, and exact xrefs, then load.

    Args:
        engine: SQLAlchemy engine for reference.db.
        dest_dir: Directory for downloaded files.
        mondo_url: Override URL for MONDO gene-disease TSV.
        hpo_url: Override URL for HPO genes-to-phenotype file.
        mondo_sssom_url: Override URL for MONDO's SSSOM exact cross-references.
        download_progress: Callback for download progress.
        timeout: HTTP timeout seconds.

    Returns:
        LoadStats with counts and metadata.
    """
    source_filenames = (
        "gene_disease.9606.tsv.gz",
        "genes_to_phenotype.txt",
        "mondo.sssom.tsv",
        SOURCE_BUNDLE_MANIFEST,
    )
    dest_dir.mkdir(parents=True, exist_ok=True)
    staging_dir = Path(tempfile.mkdtemp(prefix=".mondo-hpo-", dir=dest_dir))
    try:
        # Download into an isolated directory first. Existing validated source
        # artifacts are untouched until every input is parsed and accepted.
        mondo_meta: dict[str, str] = {}
        mondo_path = download_file(
            mondo_url,
            staging_dir,
            source_filenames[0],
            progress_callback=download_progress,
            timeout=timeout,
            meta=mondo_meta,
        )
        hpo_meta: dict[str, str] = {}
        hpo_path = download_file(
            hpo_url,
            staging_dir,
            source_filenames[1],
            progress_callback=download_progress,
            timeout=timeout,
            meta=hpo_meta,
        )
        mondo_sssom_meta: dict[str, str] = {}
        mondo_sssom_path = download_file(
            mondo_sssom_url,
            staging_dir,
            source_filenames[2],
            progress_callback=download_progress,
            timeout=timeout,
            meta=mondo_sssom_meta,
        )

        records_by_gene, stats = parse_mondo_gene_disease_tsv(mondo_path)
        hpo_data = parse_hpo_genes_to_phenotype(hpo_path)
        mondo_xrefs = parse_mondo_sssom(mondo_sssom_path)
        if len(mondo_xrefs) < MINIMUM_UNAMBIGUOUS_MONDO_XREFS:
            raise ValueError(
                "Refusing to replace MONDO/HPO rows because MONDO SSSOM yielded only "
                f"{len(mondo_xrefs):,} unambiguous exact mappings; expected at least "
                f"{MINIMUM_UNAMBIGUOUS_MONDO_XREFS:,}."
            )

        stats.hpo_genes_mapped = len(set(records_by_gene.keys()) & set(hpo_data.keys()))
        stats.hpo_disease_matches, stats.hpo_disease_unmatched = _count_scoped_hpo_matches(
            records_by_gene,
            hpo_data,
            mondo_xrefs,
        )
        if records_by_gene and not hpo_data:
            raise ValueError(
                "Refusing to replace MONDO/HPO rows because the HPO source yielded no valid "
                "disease-scoped annotations."
            )
        if records_by_gene and hpo_data and stats.hpo_disease_matches == 0:
            raise ValueError(
                "Refusing to replace MONDO/HPO rows because no HPO disease scope resolved "
                "through a validated exact MONDO cross-reference."
            )

        rows = _records_to_rows(records_by_gene, hpo_data, mondo_xrefs)

        # ``stats.sha256`` and the database_versions row remain tied to the
        # primary Monarch archive for compatibility with the existing updater.
        mondo_size = mondo_path.stat().st_size
        stats.sha256 = _compute_sha256(mondo_path)
        stats.hpo_sha256 = _compute_sha256(hpo_path)
        stats.mondo_sssom_sha256 = _compute_sha256(mondo_sssom_path)
        sources = [
            _source_manifest_entry(mondo_path, mondo_url, "mondo_gene_disease", mondo_meta),
            _source_manifest_entry(hpo_path, hpo_url, "hpo_genes_to_phenotype", hpo_meta),
            _source_manifest_entry(
                mondo_sssom_path,
                mondo_sssom_url,
                "mondo_sssom_exact_cross_references",
                mondo_sssom_meta,
            ),
        ]
        source_manifest_path, source_manifest_sha256 = _write_source_manifest(staging_dir, sources)
        source_version = mondo_meta.get("version") or datetime.now(UTC).strftime("%Y%m%d")
        stats.version = _mondo_hpo_install_version(
            source_version,
            hpo_meta,
            mondo_sssom_meta,
        )
        stats.hpo_version = hpo_meta.get("version")
        stats.mondo_sssom_version = mondo_sssom_meta.get("version")

        # Publish a unique immutable source bundle after validation. Its pending
        # sentinel remains until the row/version transaction succeeds, so an
        # overlapping loader cannot prune this bundle before it is referenced.
        bundles_dir = dest_dir / SOURCE_BUNDLE_DIRECTORY
        previous_source_bundle = _recorded_source_bundle(engine, bundles_dir)
        (staging_dir / SOURCE_BUNDLE_PENDING).write_text("pending\n", encoding="utf-8")
        source_bundle_dir, source_bundle_created = _publish_staged_source_bundle(
            staging_dir,
            dest_dir,
            _source_bundle_id(sources),
            sources,
        )
        final_mondo_path = source_bundle_dir / mondo_path.name
        final_source_manifest_path = source_bundle_dir / source_manifest_path.name
        stats.records_loaded = load_mondo_hpo_rows(
            rows,
            engine,
            version=stats.version,
            file_path=str(final_mondo_path),
            file_size_bytes=mondo_size,
            checksum=stats.sha256,
        )
        if source_bundle_created:
            pending_path = source_bundle_dir / SOURCE_BUNDLE_PENDING
            try:
                pending_path.unlink()
            except OSError as exc:
                logger.warning(
                    "mondo_hpo_source_bundle_pending_cleanup_failed",
                    bundle_name=source_bundle_dir.name,
                    error=str(exc),
                )
            else:
                _prune_source_bundles(
                    engine,
                    dest_dir,
                    current_bundle=source_bundle_dir,
                    previous_bundle=previous_source_bundle,
                )

        logger.info(
            "mondo_hpo_loaded",
            records=stats.records_loaded,
            genes=len(records_by_gene),
            hpo_mapped=stats.hpo_genes_mapped,
            hpo_disease_matches=stats.hpo_disease_matches,
            hpo_disease_unmatched=stats.hpo_disease_unmatched,
            mondo_sha256=stats.sha256,
            hpo_sha256=stats.hpo_sha256,
            mondo_sssom_sha256=stats.mondo_sssom_sha256,
            source_manifest_path=str(final_source_manifest_path),
            source_manifest_sha256=source_manifest_sha256,
        )
        return stats
    finally:
        shutil.rmtree(staging_dir, ignore_errors=True)


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
    hpo_term_details: list[HpoTerm]
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
        installed_version = conn.execute(
            sa.select(database_versions.c.version).where(
                database_versions.c.db_name == "mondo_hpo"
            )
        ).scalar_one_or_none()
        withhold_legacy_mondo = _is_legacy_disease_scope_install(installed_version)
        if withhold_legacy_mondo:
            logger.warning(
                "mondo_hpo_legacy_disease_scope_withheld",
                installed_version=installed_version,
            )
            if source_filter == "mondo_hpo":
                return {}

        for i in range(0, len(gene_symbols), 500):
            batch = gene_symbols[i : i + 500]

            conditions = [gene_phenotype.c.gene_symbol.in_(batch)]
            if source_filter:
                conditions.append(gene_phenotype.c.source == source_filter)
            elif withhold_legacy_mondo:
                conditions.append(gene_phenotype.c.source != "mondo_hpo")

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

            for row in rows:
                # Drop obsolete MONDO terms so they never reach the user (F21).
                if _is_obsolete_disease(row.disease_name):
                    continue

                hpo_term_details = decode_hpo_terms(row.hpo_terms)
                hpo_terms = [term.id for term in hpo_term_details]

                annot = GenePhenotypeAnnotation(
                    gene_symbol=row.gene_symbol,
                    disease_name=row.disease_name,
                    disease_id=row.disease_id,
                    hpo_terms=hpo_terms,
                    hpo_term_details=hpo_term_details,
                    source=row.source,
                    inheritance=row.inheritance,
                )
                results.setdefault(row.gene_symbol, []).append(annot)

    return results

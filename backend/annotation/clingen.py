"""ClinGen Gene-Disease Validity loader (SW-A11, roadmap #14).

Parses ClinGen's machine-readable Gene-Disease Validity CSV
(https://search.clinicalgenome.org/kb/gene-validity/download — CC0 1.0) and
bulk-loads it into the ``clingen_gene_validity`` table in ``reference.db``. The
classification is the ClinGen 6-tier gene-disease *validity* scale (Strande 2017,
PMID 28552198): how strong the evidence is that a gene causes a disease at all —
distinct from variant-level ACMG pathogenicity.

This is **context/guardrail data only**. Downstream
(:mod:`backend.analysis.gene_validity`) it powers a reliability flag on
actionable findings, mirroring the gene-constraint badge
(:mod:`backend.analysis.gene_constraint`) and the Weedon array-reliability badge
(:mod:`backend.analysis.array_confidence`): it NEVER changes a finding's
``evidence_level`` or ``clinvar_significance``.

The CSV is gene/disease-keyed (MONDO ids, no genomic coordinates), so it is
genome-build-agnostic — it records no ``genome_build`` and is intentionally
absent from ``EXPECTED_GENOME_BUILD``.

Usage::

    from backend.annotation.clingen import load_clingen_from_csv, lookup_gene_validities

    stats = load_clingen_from_csv(Path("clingen_gene_validity.csv"), engine)
    curations = lookup_gene_validities(reference_engine, ["BRCA1", "TTN"])
"""

from __future__ import annotations

import csv
import hashlib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import sqlalchemy as sa
import structlog

from backend.db.tables import clingen_gene_validity

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

logger = structlog.get_logger(__name__)

BATCH_SIZE = 5_000

# The seven ClinGen gene-disease validity classifications (Strande 2017). Rows
# carrying any other value are skipped as unparseable rather than silently
# mislabelled — a wrong tier would corrupt the guardrail.
VALID_CLASSIFICATIONS = frozenset(
    {
        "Definitive",
        "Strong",
        "Moderate",
        "Limited",
        "Disputed",
        "Refuted",
        "No Known Disease Relationship",
    }
)

# Column header -> our field name. ClinGen's CSV uses fixed upper-case headers.
_HEADER_MAP = {
    "GENE SYMBOL": "gene_symbol",
    "GENE ID (HGNC)": "hgnc_id",
    "DISEASE LABEL": "disease_label",
    "DISEASE ID (MONDO)": "disease_id",
    "MOI": "moi",
    "SOP": "sop",
    "CLASSIFICATION": "classification",
    "ONLINE REPORT": "report_url",
    "CLASSIFICATION DATE": "classification_date",
    "GCEP": "gcep",
}


@dataclass
class ClinGenLoadStats:
    """Statistics from a ClinGen gene-validity load operation."""

    rows_loaded: int = 0
    rows_skipped: int = 0
    genes_found: set[str] = field(default_factory=set)
    classifications: dict[str, int] = field(default_factory=dict)
    sha256: str | None = None
    version: str | None = None


def _clean(value: str | None) -> str:
    return (value or "").strip()


def parse_file_created_date(csv_path: Path) -> str | None:
    """Read the ``FILE CREATED: YYYY-MM-DD`` preamble line as the version stamp.

    ClinGen builds the CSV in real time and stamps the build date in its
    preamble. We use that date (not the wall clock) as the recorded version so a
    re-load of the same committed snapshot is idempotent.
    """
    with open(csv_path, newline="", encoding="utf-8") as fh:
        for raw in fh:
            # The field is quoted: "FILE CREATED: 2026-06-11","",...
            cell = raw.split(",", 1)[0].strip().strip('"')
            if cell.upper().startswith("FILE CREATED:"):
                date = cell.split(":", 1)[1].strip()
                return date or None
            # The header marks the end of the preamble; stop scanning.
            if cell.upper() == "GENE SYMBOL":
                break
    return None


def parse_clingen_csv(
    csv_path: Path,
    *,
    progress_callback: Callable[[int], None] | None = None,
) -> tuple[list[dict[str, Any]], ClinGenLoadStats]:
    """Parse a ClinGen gene-validity CSV into row dicts + stats.

    Tolerates ClinGen's preamble: a title block, a ``FILE CREATED`` line, a
    webpage line, and ``++++`` separator rows surrounding the real header. The
    header row is located by its ``GENE SYMBOL`` first cell; rows before it and
    the separator rows are ignored.
    """
    rows: list[dict[str, Any]] = []
    stats = ClinGenLoadStats()

    with open(csv_path, newline="", encoding="utf-8") as fh:
        reader = csv.reader(fh)
        header_idx: dict[int, str] | None = None
        seen = 0
        for record in reader:
            if not record:
                continue
            first = _clean(record[0])
            if header_idx is None:
                if first == "GENE SYMBOL":
                    header_idx = {
                        i: _HEADER_MAP[col]
                        for i, col in ((j, _clean(c)) for j, c in enumerate(record))
                        if _clean(col) in _HEADER_MAP
                    }
                continue
            # Past the header: skip separator rows and blanks.
            if not first or set(first) == {"+"}:
                continue

            record_dict = {
                field_name: _clean(record[i]) if i < len(record) else ""
                for i, field_name in header_idx.items()
            }
            gene = record_dict.get("gene_symbol", "")
            classification = record_dict.get("classification", "")
            if not gene or classification not in VALID_CLASSIFICATIONS:
                stats.rows_skipped += 1
                continue

            rows.append(
                {
                    "gene_symbol": gene,
                    "hgnc_id": record_dict.get("hgnc_id") or None,
                    "disease_label": record_dict.get("disease_label") or "",
                    "disease_id": record_dict.get("disease_id") or None,
                    "moi": record_dict.get("moi") or None,
                    "sop": record_dict.get("sop") or None,
                    "classification": classification,
                    "report_url": record_dict.get("report_url") or None,
                    "classification_date": record_dict.get("classification_date") or None,
                    "gcep": record_dict.get("gcep") or None,
                }
            )
            stats.rows_loaded += 1
            stats.genes_found.add(gene)
            stats.classifications[classification] = (
                stats.classifications.get(classification, 0) + 1
            )
            seen += 1
            if progress_callback and seen % 1000 == 0:
                progress_callback(seen)

    if header_idx is None:
        raise ValueError(
            f"No 'GENE SYMBOL' header row found in ClinGen CSV {csv_path}; "
            "file format may have changed."
        )
    return rows, stats


def _wal_checkpoint(engine: sa.Engine) -> None:
    """Run a WAL checkpoint if the engine is file-backed (not in-memory)."""
    url = str(engine.url)
    if url == "sqlite://" or ":memory:" in url:
        return
    with engine.connect() as conn:
        conn.execute(sa.text("PRAGMA wal_checkpoint(TRUNCATE)"))
        conn.commit()


def load_clingen_into_db(
    rows: list[dict[str, Any]],
    engine: sa.Engine,
    *,
    clear_existing: bool = True,
) -> ClinGenLoadStats:
    """Bulk-load parsed ClinGen rows into ``clingen_gene_validity``.

    Guard: a destructive ``clear_existing`` with **zero** rows to load is refused
    — an empty/corrupt parse (e.g. an upstream format change) must never silently
    wipe the existing curated table to nothing.
    """
    if clear_existing and not rows:
        raise ValueError(
            "Refusing to clear clingen_gene_validity with 0 rows to load "
            "(likely an empty or malformed ClinGen source)."
        )
    stats = ClinGenLoadStats(rows_loaded=len(rows))
    for row in rows:
        stats.genes_found.add(row["gene_symbol"])
        stats.classifications[row["classification"]] = (
            stats.classifications.get(row["classification"], 0) + 1
        )

    with engine.begin() as conn:
        if clear_existing:
            conn.execute(clingen_gene_validity.delete())
        for i in range(0, len(rows), BATCH_SIZE):
            conn.execute(clingen_gene_validity.insert(), rows[i : i + BATCH_SIZE])

    _wal_checkpoint(engine)
    logger.info(
        "clingen_loaded",
        rows=stats.rows_loaded,
        genes=len(stats.genes_found),
        classifications=stats.classifications,
    )
    return stats


def record_clingen_version(
    engine: sa.Engine,
    *,
    version: str,
    file_path: str | None = None,
    file_size_bytes: int | None = None,
    checksum: str | None = None,
) -> None:
    """Insert/update the ClinGen version in ``database_versions``.

    ClinGen is gene/disease-keyed (no genomic coordinates), so it records no
    ``genome_build`` — it is intentionally absent from ``EXPECTED_GENOME_BUILD``.
    """
    from backend.db.database_registry import _record_db_version

    _record_db_version(
        engine,
        db_name="clingen",
        version=version,
        file_size_bytes=file_size_bytes,
        sha256=checksum,
        file_path=file_path,
    )


def _compute_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def load_clingen_from_csv(
    csv_path: Path,
    engine: sa.Engine,
    *,
    clear_existing: bool = True,
    version: str | None = None,
) -> ClinGenLoadStats:
    """Full pipeline: parse the ClinGen CSV and load it into ``reference.db``.

    The recorded version defaults to the CSV's ``FILE CREATED`` date, falling
    back to today's date only when that preamble line is absent.
    """
    rows, parse_stats = parse_clingen_csv(csv_path)
    stats = load_clingen_into_db(rows, engine, clear_existing=clear_existing)
    stats.rows_skipped = parse_stats.rows_skipped
    stats.sha256 = _compute_sha256(csv_path)

    resolved_version = (
        version or parse_file_created_date(csv_path) or datetime.now(UTC).strftime("%Y-%m-%d")
    )
    stats.version = resolved_version
    record_clingen_version(
        engine,
        version=resolved_version,
        file_path=str(csv_path),
        file_size_bytes=csv_path.stat().st_size,
        checksum=stats.sha256,
    )
    return stats


# ═══════════════════════════════════════════════════════════════════════
# Pipeline entry point (setup-wizard build dispatch)
# ═══════════════════════════════════════════════════════════════════════

CLINGEN_DATA_DIR = Path(__file__).parent.parent / "data" / "clingen"


def download_and_load_clingen(
    engine: sa.Engine,
    dest_dir: Path,
    *,
    download_progress: Callable[[int, int | None], None] | None = None,
    parse_progress: Callable[[int], None] | None = None,
    timeout: float = 60.0,
) -> ClinGenLoadStats:
    """Load ClinGen gene-validity data from the bundled CSV into ``reference.db``.

    Build-mode entry point for the setup wizard's database dispatcher. ClinGen
    ships as a committed CC0 CSV (~1.1 MB, gene-keyed, build-independent) rather
    than an upstream download, mirroring CPIC.
    """
    del dest_dir, timeout  # bundled CSV; signature parity with other builders
    csv_path = CLINGEN_DATA_DIR / "clingen_gene_validity.csv"

    if download_progress is not None:
        download_progress(50, 100)

    stats = load_clingen_from_csv(csv_path, engine)

    if parse_progress is not None:
        parse_progress(stats.rows_loaded)
    if download_progress is not None:
        download_progress(100, 100)
    return stats


# ═══════════════════════════════════════════════════════════════════════
# Lookup
# ═══════════════════════════════════════════════════════════════════════


def lookup_gene_validities(
    reference_engine: sa.Engine, gene_symbols: Sequence[str | None]
) -> dict[str, list[dict[str, Any]]]:
    """Batch lookup → ``{gene_symbol: [curation, ...]}`` for the genes found.

    A gene with no ClinGen curation is simply absent from the result (callers
    treat "no curation" as "not evaluated", never as "no disease relationship").
    """
    wanted = sorted({g for g in gene_symbols if g})
    if not wanted:
        return {}
    out: dict[str, list[dict[str, Any]]] = {}
    with reference_engine.connect() as conn:
        rows = conn.execute(
            sa.select(clingen_gene_validity)
            .where(clingen_gene_validity.c.gene_symbol.in_(wanted))
            .order_by(
                clingen_gene_validity.c.gene_symbol,
                clingen_gene_validity.c.disease_label,
            )
        ).fetchall()
    for row in rows:
        out.setdefault(row.gene_symbol, []).append(
            {
                "gene_symbol": row.gene_symbol,
                "hgnc_id": row.hgnc_id,
                "disease_label": row.disease_label,
                "disease_id": row.disease_id,
                "moi": row.moi,
                "sop": row.sop,
                "classification": row.classification,
                "report_url": row.report_url,
                "classification_date": row.classification_date,
                "gcep": row.gcep,
            }
        )
    return out

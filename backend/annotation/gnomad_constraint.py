"""gnomAD gene-constraint (LOEUF / pLI / missense-z) loader.

EXPANSION_STRATEGY.md §7 / roadmap #12. Loads the gnomAD v2.1.1
(GRCh37, CC0 — redistributable) constraint seed into the
``gnomad_gene_constraint`` table in ``reference.db``. Provides a thin,
idempotent (``INSERT OR REPLACE``) CSV loader for fixtures and a
``database_versions`` row.

v2.1.1 is GRCh37, matching the consumer-array build and the existing gnomAD AF
pin (Karczewski 2020, *Nature*; PMID 32461654). The constraint table is
gene-keyed and read by :mod:`backend.analysis.gene_constraint`.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

import sqlalchemy as sa
import structlog

from backend.annotation.bulk_load import bulk_write_connection, execute_write, insert_batch

logger = structlog.get_logger(__name__)

GNOMAD_CONSTRAINT_VERSION = "2.1.1"

CREATE_TABLE_SQL = """\
CREATE TABLE IF NOT EXISTS gnomad_gene_constraint (
    gene_symbol TEXT PRIMARY KEY,
    transcript  TEXT,
    oe_lof      REAL,
    loeuf       REAL,
    pli         REAL,
    mis_z       REAL,
    syn_z       REAL
)
"""

CREATE_INDEX_SQL = (
    "CREATE INDEX IF NOT EXISTS idx_gnomad_constraint_loeuf ON gnomad_gene_constraint (loeuf)"
)

_INSERT_SQL = sa.text(
    "INSERT OR REPLACE INTO gnomad_gene_constraint "
    "(gene_symbol, transcript, oe_lof, loeuf, pli, mis_z, syn_z) "
    "VALUES (:gene_symbol, :transcript, :oe_lof, :loeuf, :pli, :mis_z, :syn_z)"
)


@dataclass
class ConstraintLoadStats:
    total_lines: int = 0
    genes_loaded: int = 0
    skipped: int = 0


def _parse_float(value: str | None) -> float | None:
    """Parse a gnomAD numeric cell; ``NA``/empty → ``None``."""
    if value is None:
        return None
    v = value.strip()
    if not v or v.upper() == "NA":
        return None
    try:
        return float(v)
    except ValueError:
        return None


def create_constraint_table(engine: sa.Engine) -> None:
    """Create the constraint table + LOEUF index if absent (idempotent)."""
    with bulk_write_connection(engine) as conn:
        execute_write(conn, sa.text(CREATE_TABLE_SQL))
        execute_write(conn, sa.text(CREATE_INDEX_SQL))


def load_constraint_from_csv(csv_path: Path, engine: sa.Engine) -> ConstraintLoadStats:
    """Load a CSV seed file (gene,transcript,oe_lof,loeuf,pli,mis_z,syn_z) — for tests/fixtures."""
    create_constraint_table(engine)
    batch: list[dict] = []
    stats = ConstraintLoadStats()
    with (
        open(csv_path, encoding="utf-8") as f,
        bulk_write_connection(engine) as conn,
    ):
        reader = csv.DictReader(f)
        for row in reader:
            stats.total_lines += 1
            gene = (row.get("gene_symbol") or row.get("gene") or "").strip()
            if not gene:
                stats.skipped += 1
                continue
            batch.append(
                {
                    "gene_symbol": gene,
                    "transcript": (row.get("transcript") or "").strip() or None,
                    "oe_lof": _parse_float(row.get("oe_lof")),
                    "loeuf": _parse_float(row.get("loeuf")),
                    "pli": _parse_float(row.get("pli")),
                    "mis_z": _parse_float(row.get("mis_z")),
                    "syn_z": _parse_float(row.get("syn_z")),
                }
            )
            stats.genes_loaded += 1
        if batch:
            insert_batch(conn, _INSERT_SQL, batch)
    logger.info("gnomad_constraint_csv_loaded", genes=stats.genes_loaded)
    return stats


def record_constraint_version(
    engine: sa.Engine,
    *,
    version: str = GNOMAD_CONSTRAINT_VERSION,
    file_path: str | None = None,
    file_size_bytes: int | None = None,
    checksum: str | None = None,
) -> None:
    """Record the gnomAD constraint version in ``database_versions``."""
    from backend.db.database_registry import _record_db_version

    _record_db_version(
        engine,
        db_name="gnomad_constraint",
        version=version,
        file_size_bytes=file_size_bytes,
        sha256=checksum,
        file_path=file_path,
    )

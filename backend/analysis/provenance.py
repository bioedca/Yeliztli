"""Per-finding provenance + version pinning (SW-A4 / roadmap #8).

A post-run pass that stamps every finding with the *release snapshot* used to
produce it: the source-database versions and genome builds (ClinVar / gnomAD /
dbNSFP / CPIC / VEP, read from ``database_versions`` — F30 supplies the build),
the variant's variation IDs, its ``annotation_coverage`` bitmask, and the
pipeline version. This makes each finding self-describing for audit and
reproducibility, and is the substrate a later "finding changed" diff builds on.

Audit metadata only: stamping never reads or changes ``evidence_level`` /
``clinvar_significance`` — it writes one new ``findings.provenance`` JSON column.
Runs once after ``run_all_analyses`` (best-effort, in the Huey scheduler), so all
of a sample's findings share one consistent release snapshot.
"""

from __future__ import annotations

import json
import logging
from importlib.metadata import PackageNotFoundError, version
from typing import Any

import sqlalchemy as sa

from backend.db.database_registry import PIPELINE_GENOME_BUILD
from backend.db.tables import annotated_variants, database_versions, findings

logger = logging.getLogger(__name__)

# annotation_coverage bitmask → human labels. Mirrors the bit constants in
# backend.annotation.engine (kept local to keep this post-run module lightweight;
# the engine carries the canonical definitions). Order is ascending bit value.
_COVERAGE_BITS: tuple[tuple[int, str], ...] = (
    (0b0000001, "VEP"),
    (0b0000010, "ClinVar"),
    (0b0000100, "gnomAD"),
    (0b0001000, "dbNSFP"),
    (0b0010000, "gene_phenotype"),
    (0b0100000, "GWAS"),
    (0b1000000, "CPIC"),
    (0b10000000, "AlphaMissense"),
)


def pipeline_version() -> str:
    """The installed package version — the best available pipeline pin.

    There is no git-SHA capture in the runtime, so the distribution version
    (pyproject ``yeliztli``) is the pinning identity. Returns ``"unknown"`` when
    the package is not installed as a distribution (e.g. a bare source checkout).
    """
    try:
        return version("yeliztli")
    except PackageNotFoundError:
        return "unknown"


def decode_coverage(mask: int | None) -> list[str]:
    """Decode an ``annotation_coverage`` bitmask into source labels."""
    if not mask:
        return []
    return [label for bit, label in _COVERAGE_BITS if mask & bit]


def read_release_snapshot(reference_engine: sa.Engine) -> dict[str, dict[str, Any]]:
    """Snapshot ``database_versions`` → ``{db_name: {version, genome_build}}``.

    One read of the reference DB; the same snapshot is stamped on every finding
    in a run so they pin an identical set of source releases.
    """
    with reference_engine.connect() as conn:
        rows = conn.execute(
            sa.select(
                database_versions.c.db_name,
                database_versions.c.version,
                database_versions.c.genome_build,
            )
        ).fetchall()
    return {
        row.db_name: {"version": row.version, "genome_build": row.genome_build} for row in rows
    }


def build_finding_provenance(
    snapshot: dict[str, dict[str, Any]],
    *,
    rsid: str | None,
    clinvar_accession: str | None,
    coverage_mask: int | None,
) -> dict[str, Any]:
    """Assemble the provenance block for one finding."""
    variation_ids: dict[str, str] = {}
    if rsid:
        variation_ids["rsid"] = rsid
    if clinvar_accession:
        variation_ids["clinvar_accession"] = clinvar_accession
    return {
        "pipeline_version": pipeline_version(),
        "pipeline_genome_build": PIPELINE_GENOME_BUILD,
        "sources": snapshot,
        "variation_ids": variation_ids,
        "annotation_coverage": coverage_mask,
        "annotation_coverage_sources": decode_coverage(coverage_mask),
    }


def stamp_findings_provenance(sample_engine: sa.Engine, reference_engine: sa.Engine) -> int:
    """Stamp every finding in a sample with its provenance. Returns count stamped.

    Reads the release snapshot once, left-joins ``findings`` to
    ``annotated_variants`` on ``rsid`` for per-variant variation IDs +
    ``annotation_coverage``, and bulk-updates ``findings.provenance``.

    Provenance pins the snapshot that produced the *current* findings, not an
    immutable historical log: re-annotation deletes and re-inserts findings (each
    module clears its own rows), so a re-stamp records the releases behind the
    rows that now exist. Re-running on unchanged rows is therefore idempotent —
    it refreshes them to the current snapshot, which equals what produced them.
    """
    snapshot = read_release_snapshot(reference_engine)

    av = annotated_variants
    join = findings.join(av, findings.c.rsid == av.c.rsid, isouter=True)
    select_stmt = (
        sa.select(
            findings.c.id,
            findings.c.rsid,
            av.c.clinvar_accession,
            av.c.annotation_coverage,
        )
        .select_from(join)
        .order_by(findings.c.id)
    )
    with sample_engine.connect() as conn:
        rows = conn.execute(select_stmt).fetchall()

    updates = [
        {
            "_id": row.id,
            "_provenance": json.dumps(
                build_finding_provenance(
                    snapshot,
                    rsid=row.rsid,
                    clinvar_accession=row.clinvar_accession,
                    coverage_mask=row.annotation_coverage,
                )
            ),
        }
        for row in rows
    ]
    if not updates:
        return 0

    update_stmt = (
        findings.update()
        .where(findings.c.id == sa.bindparam("_id"))
        .values(provenance=sa.bindparam("_provenance"))
    )
    with sample_engine.begin() as conn:
        conn.execute(update_stmt, updates)

    logger.info("findings_provenance_stamped", extra={"count": len(updates)})
    return len(updates)

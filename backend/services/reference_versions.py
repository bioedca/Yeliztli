"""Build read-only snapshots of the reference releases in active use.

Most sources record their release in ``database_versions``.  The VEP bundle
also supports a status-only committed copy that deliberately has no registry
row, so snapshots must overlay its effective installed version without
stamping new state into ``reference.db``.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import TypedDict

import sqlalchemy as sa
import structlog

from backend.db.database_registry import EXPECTED_GENOME_BUILD
from backend.db.tables import database_versions
from backend.db.vep_version import (
    VERSIONLESS_VEP_BUNDLE_BASELINE,
    resolve_effective_vep_bundle_version,
)

logger = structlog.get_logger(__name__)


class ReferenceRelease(TypedDict):
    """Version and coordinate-build metadata for one reference source."""

    version: str
    genome_build: str | None


ReferenceVersionSnapshot = dict[str, ReferenceRelease]


def _vep_release(version: str, genome_build: str | None) -> ReferenceRelease:
    """Return the normalized VEP entry used by every snapshot consumer."""
    return {
        "version": version,
        "genome_build": genome_build,
    }


def read_current_reference_snapshot(
    reference_engine: sa.Engine,
    vep_db_path: Path | None = None,
    *,
    effective_vep_version: str | None = None,
) -> ReferenceVersionSnapshot:
    """Return the row-backed release snapshot plus the effective VEP release.

    ``effective_vep_version`` lets an annotation run reuse the version it has
    already resolved for its dedicated state and coverage records.  Otherwise
    this function applies the shared explicit-row, embedded-metadata, then
    versionless-baseline resolver.  The overlay is entirely in memory and
    never writes ``database_versions``.

    If the registry table is unreadable, explicit-row precedence cannot be
    established.  A version already resolved by the active annotation run is
    retained; otherwise match its failure fallback by returning only the
    documented versionless VEP baseline rather than claiming embedded or
    remote-manifest state.
    """
    try:
        with reference_engine.connect() as conn:
            rows = conn.execute(
                sa.select(
                    database_versions.c.db_name,
                    database_versions.c.version,
                    database_versions.c.genome_build,
                )
            ).fetchall()
    except sa.exc.OperationalError as exc:
        logger.warning(
            "database_versions_unreadable",
            reason="table_or_db_unreachable",
            error=str(exc),
        )
        fallback = (
            effective_vep_version
            if effective_vep_version is not None
            else VERSIONLESS_VEP_BUNDLE_BASELINE
        )
        return {
            "vep_bundle": _vep_release(
                fallback,
                EXPECTED_GENOME_BUILD["vep_bundle"],
            )
        }

    snapshot: ReferenceVersionSnapshot = {
        str(row.db_name): {
            "version": str(row.version),
            "genome_build": row.genome_build,
        }
        for row in rows
        if row.version
    }

    existing_vep = snapshot.get("vep_bundle")
    if effective_vep_version is None:
        if existing_vep is not None:
            effective_vep_version = existing_vep["version"]
        else:
            try:
                effective_vep_version = resolve_effective_vep_bundle_version(
                    reference_engine,
                    vep_db_path,
                )
            except sa.exc.OperationalError as exc:
                logger.warning(
                    "database_versions_unreadable",
                    reason="table_or_db_unreachable",
                    error=str(exc),
                )
                effective_vep_version = VERSIONLESS_VEP_BUNDLE_BASELINE

    snapshot["vep_bundle"] = _vep_release(
        effective_vep_version,
        (
            existing_vep["genome_build"]
            if existing_vep is not None
            else EXPECTED_GENOME_BUILD["vep_bundle"]
        ),
    )
    return snapshot


def compact_reference_versions(
    snapshot: Mapping[str, ReferenceRelease],
) -> dict[str, str]:
    """Project a rich release snapshot into ``{source: version}`` state."""
    return {db_name: release["version"] for db_name, release in snapshot.items()}

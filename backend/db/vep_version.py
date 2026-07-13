"""Resolve the version of the VEP bundle that is actually installed.

The status-only committed-bundle fallback intentionally does not write a
``database_versions`` row.  Readers therefore need a single precedence rule
that distinguishes an explicit install stamp from a self-described embedded
bundle and from the legacy versionless fixture.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

import sqlalchemy as sa

from backend.db.tables import database_versions

VERSIONLESS_VEP_BUNDLE_BASELINE = "v1.0.0"


def resolve_effective_vep_bundle_version(
    reference_engine: sa.Engine,
    vep_db_path: Path | None,
) -> str:
    """Return the effective installed VEP bundle version.

    Resolution is read-only and follows this precedence:

    1. ``database_versions['vep_bundle']`` (an explicit install/update stamp),
    2. ``bundle_metadata.bundle_version`` in the installed VEP database,
    3. :data:`VERSIONLESS_VEP_BUNDLE_BASELINE` for the committed legacy fixture.

    Reference-database query errors deliberately propagate so callers can keep
    their existing fail-open/logging policy. Embedded metadata is probed with
    SQLite's read-only immutable URI mode: installed bundles are static files
    replaced atomically, and a stray uncheckpointed WAL is therefore treated
    as unreadable rather than mutating the bundle or creating sidecars. Missing
    or unreadable embedded metadata is equivalent to a versionless legacy
    fixture and uses the documented baseline.
    """
    with reference_engine.connect() as conn:
        recorded = conn.execute(
            sa.select(database_versions.c.version).where(
                database_versions.c.db_name == "vep_bundle"
            )
        ).scalar()
    if recorded is not None:
        return str(recorded)

    if vep_db_path is not None:
        try:
            uri = f"{vep_db_path.resolve().as_uri()}?mode=ro&immutable=1"
            with closing(sqlite3.connect(uri, uri=True)) as conn:
                row = conn.execute(
                    "SELECT value FROM bundle_metadata WHERE key = 'bundle_version'"
                ).fetchone()
            embedded = row[0] if row is not None else None
        except (sqlite3.Error, OSError, RuntimeError, ValueError):
            embedded = None
        if embedded:
            return str(embedded)

    return VERSIONLESS_VEP_BUNDLE_BASELINE

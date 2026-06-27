"""LAI bundle staleness soft-gate (Step 23, Plan §6.7).

When the installed ``lai_bundle`` is older than ``v2.0.0`` *and* the
requested sample carries an AncestryDNA contribution (single-source or
merged), the LAI endpoints flag ``degraded_coverage=True`` in their HTTP
200 payload — the gate is advisory, never 423. 23andMe-only samples
never carry the flag regardless of bundle version (Plan §6.7 negative
case).

The helper layer here is intentionally pure: each function takes its
inputs explicitly and never touches the global registry. Routes and
tests call the small wrappers below; the wrappers do the registry
lookups.
"""

from __future__ import annotations

import json

import sqlalchemy as sa
import structlog
from packaging.version import InvalidVersion, Version

from backend.db.connection import get_registry
from backend.db.tables import database_versions, merge_provenance, samples

logger = structlog.get_logger(__name__)

# Plan §6.7 — v2.0.0 is the first bundle with full AncestryDNA chromosome
# painting coverage. Anything strictly below is degraded.
_LAI_BUNDLE_V2 = Version("2.0.0")
_MERGED_FILE_FORMAT = "merged_v1"


def file_format_has_ancestrydna(file_format: str | None) -> bool:
    """Return True when ``file_format`` indicates an AncestryDNA contribution.

    Single-source AncestryDNA samples carry ``file_format`` strings like
    ``"ancestrydna_v2.0"`` (Plan §8.7). Merged samples carry the neutral
    ``"merged_v1"`` token and are resolved through their source provenance
    by the registry-aware wrappers below.
    """
    if not file_format:
        return False
    return file_format.lower().startswith("ancestrydna")


def lai_bundle_below_v2(lai_bundle_version: str | None) -> bool:
    """Return True when ``lai_bundle_version`` parses as ``< v2.0.0``.

    Tolerates a leading ``v`` and ``None``. Unparseable values short-
    circuit to ``False`` — the user-facing surface is the bundle Update
    Manager, not this helper.
    """
    if not lai_bundle_version:
        return False
    try:
        return Version(lai_bundle_version.lstrip("v")) < _LAI_BUNDLE_V2
    except InvalidVersion:
        logger.warning(
            "lai_bundle_version_unparseable",
            recorded_version=lai_bundle_version,
        )
        return False


def is_lai_coverage_degraded(
    file_format: str | None,
    lai_bundle_version: str | None,
) -> bool:
    """Pure predicate combining the file-format and bundle-version gates."""
    return file_format_has_ancestrydna(file_format) and lai_bundle_below_v2(lai_bundle_version)


def _read_installed_lai_version() -> str | None:
    """Read ``database_versions['lai_bundle'].version`` or ``None``."""
    registry = get_registry()
    with registry.reference_engine.connect() as conn:
        row = conn.execute(
            sa.select(database_versions.c.version).where(
                database_versions.c.db_name == "lai_bundle"
            )
        ).fetchone()
    return row.version if row else None


def _read_sample_file_format(sample_id: int) -> str | None:
    """Read ``samples.file_format`` for ``sample_id`` from the reference DB."""
    registry = get_registry()
    with registry.reference_engine.connect() as conn:
        row = conn.execute(
            sa.select(samples.c.file_format).where(samples.c.id == sample_id)
        ).fetchone()
    return row.file_format if row else None


def _source_ids_from_merged_sample(
    registry: object,
    sample_id: int,
    db_path: str | None,
) -> list[int]:
    """Read ``merge_provenance.source_sample_ids`` for a merged sample.

    The LAI gate is advisory, so unreadable or malformed provenance logs and
    returns an empty list instead of failing the ancestry endpoint.
    """
    if not db_path:
        return []
    sample_db_path = registry.settings.data_dir / db_path  # type: ignore[attr-defined]
    if not sample_db_path.exists():
        logger.warning(
            "lai_merged_sample_db_missing",
            sample_id=sample_id,
            db_path=str(sample_db_path),
        )
        return []
    try:
        engine = registry.get_sample_engine(sample_db_path)  # type: ignore[attr-defined]
        with engine.connect() as conn:
            prov_row = conn.execute(sa.select(merge_provenance.c.source_sample_ids)).fetchone()
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "lai_merged_provenance_read_failed",
            sample_id=sample_id,
            db_path=str(sample_db_path),
            error=str(exc),
        )
        return []
    if prov_row is None:
        return []
    try:
        raw_source_ids = json.loads(prov_row.source_sample_ids)
    except (json.JSONDecodeError, TypeError):
        logger.warning(
            "lai_merged_provenance_malformed",
            sample_id=sample_id,
            source_sample_ids_raw=prov_row.source_sample_ids,
        )
        return []
    if not isinstance(raw_source_ids, list):
        logger.warning(
            "lai_merged_provenance_malformed",
            sample_id=sample_id,
            source_sample_ids_raw=prov_row.source_sample_ids,
        )
        return []
    if not all(
        isinstance(source_id, int) and not isinstance(source_id, bool)
        for source_id in raw_source_ids
    ):
        logger.warning(
            "lai_merged_provenance_malformed",
            sample_id=sample_id,
            source_sample_ids_raw=prov_row.source_sample_ids,
        )
        return []
    return raw_source_ids


def _source_file_formats_have_ancestrydna(registry: object, source_sample_ids: list[int]) -> bool:
    """Return True when any source sample row is AncestryDNA-sourced."""
    if not source_sample_ids:
        return False
    with registry.reference_engine.connect() as conn:  # type: ignore[attr-defined]
        rows = conn.execute(
            sa.select(samples.c.file_format).where(samples.c.id.in_(source_sample_ids))
        ).fetchall()
    return any(file_format_has_ancestrydna(row.file_format) for row in rows)


def _sample_row_has_ancestrydna_contribution(registry: object, row: sa.Row) -> bool:
    """Return True for direct or merged AncestryDNA contributions."""
    if file_format_has_ancestrydna(row.file_format):
        return True
    if (row.file_format or "").lower() != _MERGED_FILE_FORMAT:
        return False
    source_ids = _source_ids_from_merged_sample(registry, int(row.id), row.db_path)
    return _source_file_formats_have_ancestrydna(registry, source_ids)


def is_degraded_for_sample(sample_id: int) -> bool:
    """Resolve degraded-coverage status for ``sample_id`` against the install.

    Per-sample wrapper used by the per-sample LAI routes. Returns ``False``
    when the sample row is missing — the gate is best-effort advisory.
    """
    bundle_version = _read_installed_lai_version()
    if not lai_bundle_below_v2(bundle_version):
        return False
    registry = get_registry()
    with registry.reference_engine.connect() as conn:
        row = conn.execute(
            sa.select(samples.c.id, samples.c.file_format, samples.c.db_path).where(
                samples.c.id == sample_id
            )
        ).fetchone()
    if row is None:
        return False
    return _sample_row_has_ancestrydna_contribution(registry, row)


def is_degraded_globally() -> bool:
    """True when *any* installed sample would trigger the soft gate.

    Powers the dashboard-mounted ``<AppUpdateBanner>`` (Plan §6.7) — the
    banner surfaces once per install, independent of which sample is
    currently selected. Direct vendor checks stay in the reference DB;
    ``merged_v1`` rows open their per-sample DB once to read merge
    provenance.
    """
    bundle_version = _read_installed_lai_version()
    if not lai_bundle_below_v2(bundle_version):
        return False
    registry = get_registry()
    with registry.reference_engine.connect() as conn:
        rows = conn.execute(
            sa.select(samples.c.id, samples.c.file_format, samples.c.db_path)
        ).fetchall()
    return any(_sample_row_has_ancestrydna_contribution(registry, row) for row in rows)

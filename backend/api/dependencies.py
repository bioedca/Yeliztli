"""FastAPI dependencies for sample-staleness gating.

Plan §7.5 — ``require_fresh_sample(sample_id)`` is declared on every
sample-scoped analysis route (the mechanical wire-up lands in step 13).
It calls :func:`backend.services.staleness.is_sample_stale` and raises
``HTTPException(423, ...)`` when the per-sample
``annotation_state.vep_bundle_version`` has a strictly lower **major**
``packaging.version.Version`` than the installed bundle.

The 423 ``detail`` payload carries the four keys mandated by Plan §7.5:

* ``installed_version`` — the version recorded in the sample's
  ``annotation_state`` row (treated as ``"v1.0.0"`` per the Plan §7.4
  missing-state fallback when the row, table, or per-sample DB is
  absent or malformed).
* ``required_version`` — the installed bundle's semver. Sourced from the
  manifest's ``version`` field (the authoritative value per Plan §5.5)
  with the ``database_versions['vep_bundle']`` row as fallback when no
  manifest is reachable.
* ``update_url`` — bundle download URL (manifest, registry fallback).
* ``reannotate_url`` — re-annotation escape hatch
  (``POST /api/annotation/{sample_id}``). Plan §7.5 pins this to the
  same route ``annotation.py`` opts out of in the drift guard.

Drift guard lives at ``tests/backend/test_stale_sample_dependency.py``.
"""

from __future__ import annotations

import sqlalchemy as sa
from fastapi import HTTPException

from backend.db.connection import get_registry
from backend.db.database_registry import DATABASES
from backend.db.manifest import get_bundle_info
from backend.db.tables import annotation_state, database_versions, samples
from backend.services.staleness import get_recorded_bundle_version, is_sample_stale

_BUNDLE_KEY = "vep_bundle"
# Plan §7.4 — every pre-Phase-0 sample state is treated as v1.0.0.
_FALLBACK_SAMPLE_VERSION = "v1.0.0"


def _read_recorded_sample_version(sample_id: int) -> str:
    """Return the sample's recorded ``vep_bundle_version`` (fallback ``v1.0.0``).

    Mirrors the staleness service's read path so the 423 payload reports
    the same value the gate decision was made on, without re-emitting
    the structured ``annotation_state_missing`` warning that
    :func:`backend.services.staleness.is_sample_stale` has already
    logged.
    """
    registry = get_registry()
    with registry.reference_engine.connect() as conn:
        row = conn.execute(
            sa.select(samples.c.db_path).where(samples.c.id == sample_id)
        ).fetchone()
    if row is None:
        return _FALLBACK_SAMPLE_VERSION

    sample_db = registry.settings.data_dir / row.db_path
    # Guard before get_sample_engine, which materializes an empty DB
    # (and schema) on a missing path. Mirror the not-found fallback.
    if not sample_db.exists():
        return _FALLBACK_SAMPLE_VERSION

    try:
        engine = registry.get_sample_engine(sample_db)
        with engine.connect() as conn:
            value_row = conn.execute(
                sa.select(annotation_state.c.value).where(
                    annotation_state.c.key == "vep_bundle_version"
                )
            ).fetchone()
    except sa.exc.OperationalError:
        return _FALLBACK_SAMPLE_VERSION

    if value_row is None or not value_row.value:
        return _FALLBACK_SAMPLE_VERSION
    return value_row.value


def _sample_exists(sample_id: int) -> bool:
    """Whether ``sample_id`` has a row in the reference DB ``samples`` table.

    Returns ``False`` defensively when the reference DB or ``samples``
    table is unreachable (e.g. a fresh install before setup) — the caller
    then falls through to :func:`is_sample_stale`, which already tolerates
    a missing table. This mirrors the staleness service's "never raise"
    contract so the gate cannot turn an unreadable reference DB into a 500.
    """
    registry = get_registry()
    try:
        with registry.reference_engine.connect() as conn:
            row = conn.execute(sa.select(samples.c.id).where(samples.c.id == sample_id)).fetchone()
    except sa.exc.OperationalError:
        return False
    return row is not None


def _read_installed_version() -> str:
    registry = get_registry()
    with registry.reference_engine.connect() as conn:
        row = conn.execute(
            sa.select(database_versions.c.version).where(
                database_versions.c.db_name == _BUNDLE_KEY
            )
        ).fetchone()
    return row.version if row else ""


def _resolve_required_version() -> str:
    """Manifest version, falling back to the ``database_versions`` row."""
    manifest_entry = get_bundle_info(_BUNDLE_KEY)
    if manifest_entry is not None and manifest_entry.version:
        return manifest_entry.version
    return _read_installed_version()


def _resolve_update_url() -> str:
    manifest_entry = get_bundle_info(_BUNDLE_KEY)
    if manifest_entry is not None and manifest_entry.url:
        return manifest_entry.url
    registry_entry = DATABASES.get(_BUNDLE_KEY)
    return registry_entry.url if registry_entry else ""


def require_fresh_sample(sample_id: int) -> int:
    """Block stale samples (Plan §7.5).

    When :func:`backend.services.staleness.is_sample_stale` returns
    ``True``, raise ``HTTPException(423, detail={...})`` with the
    Plan §7.5 payload. Otherwise return ``sample_id`` unchanged so
    routes can declare the dependency without losing path-parameter
    access (``sample_id: int = Depends(require_fresh_sample)`` keeps
    the value bound to the handler signature).

    An **existing** sample that has never completed an annotation run (no
    recorded ``annotation_state.vep_bundle_version`` row) is *not* gated
    here: it has no stale data to block, it needs its *first* annotation.
    Gating it would surface the re-annotation banner (implying the bundle
    is out of date) instead of the dashboard's "Run Annotation" CTA — the
    bug a freshly imported sample otherwise hits, since Plan §7.4's
    missing-state fallback treats an absent row as ``v1.0.0``. That
    fallback predates the migration-008 / restore explicit ``v1.0.0``
    backfill, which now covers every genuinely pre-Phase-0 *annotated*
    sample, so an absent row on an existing sample reliably means "never
    annotated". (``is_sample_stale`` keeps the fallback for the merge
    stale-source gate, where blocking an un-annotated source before merge
    is intended.)

    A *missing* ``samples`` row falls through to the gate below (423),
    preserving the existing contract that gated routes do not leak sample
    existence via a 404.
    """
    if _sample_exists(sample_id) and get_recorded_bundle_version(sample_id) is None:
        return sample_id
    if not is_sample_stale(sample_id):
        return sample_id

    detail = {
        "error": "sample_annotation_stale",
        "installed_version": _read_recorded_sample_version(sample_id),
        "required_version": _resolve_required_version(),
        "update_url": _resolve_update_url(),
        "reannotate_url": f"/api/annotation/{sample_id}",
    }
    raise HTTPException(status_code=423, detail=detail)


def require_fresh_merged_sample(merged_id: int) -> int:
    """``require_fresh_sample`` alias for ``{merged_id}``-pathed routes.

    Plan §10.6's post-merge re-watch route is spelled
    ``GET /api/samples/{merged_id}/watched-variants/migrate-from-sources``
    — the ``merged_id`` name pins the route to merged samples. FastAPI
    binds a dependency's parameters by name against the request's
    path / query parameters, so :func:`require_fresh_sample` (which
    takes ``sample_id``) cannot be wired directly there. This thin
    wrapper exists solely so the route can declare
    ``Depends(require_fresh_merged_sample)`` and have the gate fire.
    """
    return require_fresh_sample(merged_id)

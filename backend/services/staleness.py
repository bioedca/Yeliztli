"""Sample-annotation staleness service (Plan §7.4 step 3, ADNA-00c part 4).

``is_sample_stale(sample_id)`` returns ``True`` when the bundle recorded in
the per-sample ``annotation_state`` row has a strictly lower **major**
``packaging.version.Version`` than the installed ``vep_bundle``. Minor or
patch differences are not stale.

Missing-state fallback (Plan §7.4): when the per-sample DB has no
``annotation_state`` table, the ``vep_bundle_version`` row is absent, or
its value cannot be parsed as a semver, the sample is treated as
``v1.0.0`` and a structured ``annotation_state_missing`` warning is
emitted. The helper never raises on a malformed per-sample DB — the gate
(step 12's ``require_fresh_sample``) is the user-facing surface, not this
function.
"""

from __future__ import annotations

import json

import sqlalchemy as sa
import structlog
from packaging.version import InvalidVersion, Version

from backend.db.connection import get_registry
from backend.db.tables import annotation_state, database_versions, samples

logger = structlog.get_logger(__name__)

# Per Plan §7.4 — every pre-Phase-0 sample is treated as having been
# annotated against the v1.0.0 bundle.
_FALLBACK_SAMPLE_VERSION = "v1.0.0"

# Per-sample annotation_state key holding the reference database versions used
# by the latest successful annotation + analysis run.
REFERENCE_VERSION_SNAPSHOT_KEY = "reference_versions_json"


def _coerce_major(raw: str | None) -> int | None:
    """Return the semver major of ``raw`` or ``None`` when unparseable."""
    if not raw:
        return None
    try:
        return Version(raw.lstrip("v")).major
    except InvalidVersion:
        return None


def read_current_reference_versions(reference_engine: sa.Engine) -> dict[str, str]:
    """Return current ``database_versions`` as ``{db_name: version}``.

    The reference DB may be partially initialized in tests or first-run setup.
    Missing/unreadable version state declines to raise so callers can avoid
    turning an informational staleness signal into a hard failure.
    """
    try:
        with reference_engine.connect() as conn:
            rows = conn.execute(
                sa.select(database_versions.c.db_name, database_versions.c.version)
            ).fetchall()
    except sa.exc.OperationalError as exc:
        logger.warning(
            "database_versions_unreadable",
            reason="table_or_db_unreachable",
            error=str(exc),
        )
        return {}
    return {row.db_name: row.version for row in rows if row.version}


def _parse_reference_versions(value: str | None) -> dict[str, str] | None:
    """Parse a recorded reference-version snapshot.

    Accepts either the compact ``{db: version}`` shape written by
    ``annotation_state`` or the provenance-style ``{db: {version, ...}}`` shape.
    Returns ``None`` when the blob is absent or malformed.
    """
    if not value:
        return None
    try:
        parsed = json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(parsed, dict):
        return None

    versions: dict[str, str] = {}
    for db_name, raw in parsed.items():
        if not isinstance(db_name, str):
            return None
        if isinstance(raw, dict):
            version = raw.get("version")
        elif isinstance(raw, str):
            version = raw
        else:
            return None
        if not isinstance(version, str) or not version:
            return None
        versions[db_name] = version
    return versions


def read_recorded_reference_versions(sample_engine: sa.Engine) -> dict[str, str] | None:
    """Read the sample's recorded reference-version snapshot, if present."""
    try:
        with sample_engine.connect() as conn:
            row = conn.execute(
                sa.select(annotation_state.c.value).where(
                    annotation_state.c.key == REFERENCE_VERSION_SNAPSHOT_KEY
                )
            ).fetchone()
    except sa.exc.OperationalError as exc:
        logger.warning(
            "reference_versions_missing",
            reason="table_or_db_unreachable",
            error=str(exc),
        )
        return None
    return _parse_reference_versions(row.value if row else None)


def find_stale_reference_versions(
    sample_engine: sa.Engine,
    reference_engine: sa.Engine,
) -> list[dict[str, str | None]]:
    """Return reference DBs newer/different than the sample annotation snapshot.

    A missing recorded version for a currently-installed DB means that source was
    absent from the successful annotation snapshot, so re-annotation may add or
    refresh findings from it.
    """
    recorded = read_recorded_reference_versions(sample_engine)
    if recorded is None:
        return []

    current = read_current_reference_versions(reference_engine)
    stale: list[dict[str, str | None]] = []
    for db_name, current_version in sorted(current.items()):
        recorded_version = recorded.get(db_name)
        if recorded_version != current_version:
            stale.append(
                {
                    "db_name": db_name,
                    "recorded_version": recorded_version,
                    "current_version": current_version,
                }
            )
    return stale


def _read_installed_major() -> int | None:
    """Read ``database_versions['vep_bundle'].version``'s semver major.

    A readable registry with no row uses the documented ``v1.0.0`` baseline.
    That is the expected state when first-run setup copies the committed
    offline-fallback bundle without claiming that the manifest's downloadable
    release is installed. Returns ``None`` when the table is unreadable or a
    stored version is malformed, logging the reason at the point it is known;
    ``is_sample_stale`` treats ``None`` as "decline to gate".
    """
    registry = get_registry()
    try:
        with registry.reference_engine.connect() as conn:
            row = conn.execute(
                sa.select(database_versions.c.version).where(
                    database_versions.c.db_name == "vep_bundle"
                )
            ).fetchone()
    except sa.exc.OperationalError as exc:
        logger.warning(
            "database_versions_unreadable",
            reason="table_or_db_unreachable",
            error=str(exc),
        )
        return None
    if row is None:
        return _coerce_major(_FALLBACK_SAMPLE_VERSION)
    major = _coerce_major(row.version)
    if major is None:
        logger.warning(
            "vep_bundle_version_unreadable",
            reason="malformed_installed_version",
            installed_version=row.version,
        )
    return major


def get_recorded_bundle_version(sample_id: int) -> str | None:
    """Return the sample's recorded ``annotation_state.vep_bundle_version``.

    Returns ``None`` when the sample has **never completed an annotation
    run** — i.e. the per-sample row is missing from the reference DB, the
    per-sample DB is unreachable, the ``annotation_state`` table is
    absent, or the reserved ``vep_bundle_version`` row is absent/empty.
    The annotation task writes this row only on a successful completion
    (``backend.tasks.huey_tasks``), so an absent row distinguishes a
    freshly-imported (or mid-first-annotation) sample from one that
    finished annotating against some bundle.

    Emits a structured ``annotation_state_missing`` warning on each
    defensive path so observability is unchanged for callers that treat
    the missing state as the Plan §7.4 ``v1.0.0`` fallback.
    """
    registry = get_registry()
    with registry.reference_engine.connect() as conn:
        row = conn.execute(
            sa.select(samples.c.db_path).where(samples.c.id == sample_id)
        ).fetchone()
    if row is None:
        logger.warning(
            "annotation_state_missing",
            sample_id=sample_id,
            reason="sample_row_missing",
        )
        return None

    sample_db = registry.settings.data_dir / row.db_path
    try:
        sample_engine = registry.get_sample_engine(sample_db)
        with sample_engine.connect() as conn:
            value_row = conn.execute(
                sa.select(annotation_state.c.value).where(
                    annotation_state.c.key == "vep_bundle_version"
                )
            ).fetchone()
    except sa.exc.OperationalError as exc:
        logger.warning(
            "annotation_state_missing",
            sample_id=sample_id,
            reason="table_or_db_unreachable",
            error=str(exc),
        )
        return None

    if value_row is None or not value_row.value:
        logger.warning(
            "annotation_state_missing",
            sample_id=sample_id,
            reason="vep_bundle_version_row_missing",
        )
        return None

    return value_row.value


def _read_sample_bundle_version(sample_id: int) -> str:
    """Recorded ``vep_bundle_version`` with the Plan §7.4 ``v1.0.0`` fallback.

    Wraps :func:`get_recorded_bundle_version`, substituting the
    missing-state fallback so :func:`is_sample_stale` keeps its
    major-version comparison semantics (an absent row is treated as a
    pre-Phase-0 ``v1.0.0`` annotation). Callers that need to distinguish
    "never annotated" from "annotated against v1.0.0" should use
    :func:`get_recorded_bundle_version` directly.
    """
    return get_recorded_bundle_version(sample_id) or _FALLBACK_SAMPLE_VERSION


def is_sample_stale(sample_id: int) -> bool:
    """Return ``True`` when ``sample_id``'s bundle major < installed major.

    Comparison is on ``packaging.version.Version.major`` only — minor and
    patch differences are not stale (Plan §7.4 step 3). The
    missing-state fallback treats a per-sample DB without an
    ``annotation_state.vep_bundle_version`` row as ``v1.0.0``.
    """
    installed_major = _read_installed_major()
    if installed_major is None:
        # The registry reader logged the specific failure. Decline to gate;
        # the bundle-update flow is the user's path back to a known state.
        return False

    sample_raw = _read_sample_bundle_version(sample_id)
    sample_major = _coerce_major(sample_raw)
    if sample_major is None:
        logger.warning(
            "annotation_state_missing",
            sample_id=sample_id,
            reason="malformed_recorded_version",
            recorded_version=sample_raw,
        )
        sample_major = _coerce_major(_FALLBACK_SAMPLE_VERSION)

    return sample_major < installed_major

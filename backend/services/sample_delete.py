"""Source-deletion cascade service (AncestryDNA Plan §10.8; Step 66 / MRG-02a).

Deleting a sample that any ``merge_provenance.source_sample_ids`` JSON array
references must cascade: every merged child is destroyed first (DB file +
reference row), then the source row + DB. The walk lives in one module so
every ``DELETE`` path uses identical semantics — Plan §10.8 declares the
cascade as the contract, not the route layout.

The traversal is O(N) over rows where ``samples.file_format == 'merged_v1'``
because ``merge_provenance`` is a single-row table inside the *merged
sample's* per-sample DB (Plan §10.4 c), not the reference DB. Real installs
carry a handful of merged samples, so opening each per-sample DB once per
deletion is acceptable.

Defensive contract: a half-broken install (missing DB file, malformed JSON,
unreadable engine) is *logged and skipped*, never raised. The user-facing
DELETE flow must keep working when the registry is partially corrupt — the
single-confirmation cascade in the UI is the only place a user can recover
the orphaned rows from.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import sqlalchemy as sa

from backend.db.tables import merge_provenance, reannotation_prompts, samples
from backend.services.sample_operation_lock import (
    SampleOperationConflictError,
    merge_sources_in_use,
)

if TYPE_CHECKING:
    from backend.db.connection import DBRegistry

logger = logging.getLogger(__name__)

# Plan §10.5 step 5 ships this token; the deletion walk filters merged
# children by it.
_MERGED_FILE_FORMAT = "merged_v1"


@dataclass(frozen=True)
class MergedChild:
    """A merged sample whose ``merge_provenance`` lists the target as a source."""

    id: int
    name: str


@dataclass(frozen=True)
class DeleteCascadeResult:
    """Outcome of :func:`delete_sample_with_cascade` — surfaces in the log."""

    deleted_sample_id: int
    deleted_sample_name: str
    deleted_merged_children: list[MergedChild]


def list_merged_children(registry: DBRegistry, sample_id: int) -> list[MergedChild]:
    """Return every merged sample that lists ``sample_id`` in its sources.

    Empty list when the sample has never been merged. A merged child whose
    per-sample DB is missing on disk or whose provenance row is malformed is
    skipped with a structured warning (the admin log explorer surfaces it),
    so a partial install still completes the cascade on the legible rows.
    """
    settings = registry.settings
    with registry.reference_engine.connect() as conn:
        merged_rows = list(
            conn.execute(
                sa.select(samples.c.id, samples.c.name, samples.c.db_path).where(
                    samples.c.file_format == _MERGED_FILE_FORMAT
                )
            )
        )

    children: list[MergedChild] = []
    for row in merged_rows:
        merged_db_path = settings.data_dir / row.db_path
        if not merged_db_path.exists():
            logger.warning(
                "merged_sample_db_missing",
                extra={
                    "merged_sample_id": int(row.id),
                    "db_path": str(merged_db_path),
                },
            )
            continue
        try:
            engine = registry.get_sample_engine(merged_db_path)
            with engine.connect() as conn:
                prov_row = conn.execute(sa.select(merge_provenance.c.source_sample_ids)).fetchone()
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "merged_provenance_read_failed",
                extra={
                    "merged_sample_id": int(row.id),
                    "db_path": str(merged_db_path),
                    "error": str(exc),
                },
            )
            continue
        if prov_row is None:
            # ``file_format == 'merged_v1'`` with no provenance row. Since
            # #2329 a merge publishes its registry row only after the database
            # (provenance included) is complete, so this is no longer reachable
            # for an in-flight merge — the publication window that made it
            # dangerous is gone. What can still reach it is legacy debris from
            # the old publish-first ordering, whose parentage is unknowable:
            # treating it as a child of whichever source is being deleted would
            # risk destroying an unrelated sample. Skip, but say so.
            logger.warning(
                "merged_provenance_missing",
                extra={"merged_sample_id": int(row.id), "db_path": str(merged_db_path)},
            )
            continue
        try:
            source_ids = json.loads(prov_row.source_sample_ids)
        except (json.JSONDecodeError, TypeError):
            logger.warning(
                "merged_provenance_malformed",
                extra={
                    "merged_sample_id": int(row.id),
                    "source_sample_ids_raw": prov_row.source_sample_ids,
                },
            )
            continue
        if not isinstance(source_ids, list):
            # Valid JSON, wrong shape (object/number/string). ``in`` would
            # raise or silently check keys — log-and-skip per the module
            # contract instead.
            logger.warning(
                "merged_provenance_malformed",
                extra={
                    "merged_sample_id": int(row.id),
                    "source_sample_ids_raw": prov_row.source_sample_ids,
                },
            )
            continue
        if sample_id in source_ids:
            children.append(MergedChild(id=int(row.id), name=str(row.name)))
    return children


def _delete_sample_files(registry: DBRegistry, db_path: str | None) -> None:
    """Dispose the cached engine then remove the SQLite file + WAL/SHM siblings."""
    if not db_path:
        return
    sample_db_path = registry.settings.data_dir / db_path
    registry.dispose_sample_engine(sample_db_path)
    sample_db_path.unlink(missing_ok=True)
    Path(f"{sample_db_path}-wal").unlink(missing_ok=True)
    Path(f"{sample_db_path}-shm").unlink(missing_ok=True)


def _delete_sample_reference_rows(conn: sa.Connection, sample_id: int) -> None:
    """Delete sample-owned central state before its reusable registry ID."""
    # A partially upgraded registry can predate the prompt table. Absence means
    # there is no prompt state to orphan; real delete/lock failures still raise
    # and roll back with the sample-row deletion below.
    if sa.inspect(conn).has_table(reannotation_prompts.name):
        conn.execute(
            reannotation_prompts.delete().where(reannotation_prompts.c.sample_id == sample_id)
        )
    conn.execute(samples.delete().where(samples.c.id == sample_id))


def delete_sample_with_cascade(registry: DBRegistry, sample_id: int) -> DeleteCascadeResult | None:
    """Delete ``sample_id`` and every merged child that referenced it.

    Returns ``None`` when ``sample_id`` does not exist (caller surfaces 404).

    Plan §10.8 ordering: merged children — DB file *then* reference row — go
    first; the source last. If the process is interrupted mid-cascade, a
    merged sample whose source rows the registry still believes exist is the
    worse failure mode (would silently mask the source's deletion), so the
    source row is the last write.
    """
    with registry.reference_engine.connect() as conn:
        row = conn.execute(
            sa.select(samples.c.id, samples.c.name, samples.c.db_path).where(
                samples.c.id == sample_id
            )
        ).fetchone()
    if row is None:
        return None

    # A merge streams its sources for its whole materialisation, so removing
    # one underneath it would leave the merge reading a deleted database and
    # could publish a child naming a source that no longer exists (#2329).
    # Refuse rather than queue: the merge lease can be held for a long time and
    # a DELETE that silently blocks reads as a hang, matching how annotation
    # and export already answer "this sample is busy".
    busy = merge_sources_in_use(registry.reference_engine, [sample_id])
    if busy:
        raise SampleOperationConflictError(
            f"Sample {sample_id} is being merged; retry the delete once that merge completes."
        )

    children = list_merged_children(registry, sample_id)

    for child in children:
        with registry.reference_engine.connect() as conn:
            child_row = conn.execute(
                sa.select(samples.c.db_path).where(samples.c.id == child.id)
            ).fetchone()
        if child_row is not None:
            _delete_sample_files(registry, child_row.db_path)
        with registry.reference_engine.begin() as conn:
            _delete_sample_reference_rows(conn, child.id)

    _delete_sample_files(registry, row.db_path)
    with registry.reference_engine.begin() as conn:
        _delete_sample_reference_rows(conn, sample_id)

    logger.info(
        "sample_delete_cascade",
        extra={
            "deleted_sample_id": int(row.id),
            "deleted_sample_name": str(row.name),
            "deleted_merged_children": [{"id": c.id, "name": c.name} for c in children],
        },
    )
    return DeleteCascadeResult(
        deleted_sample_id=int(row.id),
        deleted_sample_name=str(row.name),
        deleted_merged_children=children,
    )

"""Finding-level change diff (SW-A4b / roadmap #8).

The provenance core (SW-A4) pins every finding to the source releases that
produced it. This module builds on that foundation: after a sample is
re-annotated, it surfaces *what changed at the finding level* since the previous
analysis — findings that were added, removed, or whose meaning shifted (e.g.
"BRCA1: VUS → Pathogenic") — attributed to the source-release delta recorded in
provenance.

It generalises the existing *variant-level* watched-variant reclassification
banner into a *whole-sample, every-finding* diff.

Disclosure only. The diff never reads or alters ``evidence_level`` /
``clinvar_significance`` / carriage — it reports what the upstream releases
changed. The honest framing is "this changed because the ClinVar release
advanced," never "your risk increased."

Design (Option A — no schema change). Findings are deleted and re-inserted on
every run, so the prior findings are snapshotted *in memory* before
``run_all_analyses`` clears them; after the fresh findings are stamped with
provenance, the diff is computed against that snapshot and stored as a JSON blob
in the per-sample ``annotation_state`` kv table under ``last_finding_diff_json``.

A finding's identity across runs is the stable composite key
``(module, category, gene_symbol, rsid, drug, diplotype, pathway)`` — the columns
that pin "the same biological statement," excluding the volatile ``id`` /
``created_at`` / ``provenance`` and the free-text ``finding_text``.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from backend.analysis.provenance import read_release_snapshot
from backend.db.tables import annotation_state, findings

logger = logging.getLogger(__name__)

# Per-sample annotation_state key holding the most recent computed diff.
DIFF_STATE_KEY = "last_finding_diff_json"
DIFF_SCHEMA_VERSION = 1

# Columns that identify "the same biological statement" across runs. A finding's
# id/created_at/provenance are volatile and finding_text is free-text, so neither
# pins identity. ``pathway`` is included because the categorical modules emit one
# ``pathway_summary`` finding per pathway, all sharing ``category="pathway_summary"``
# with NULL gene_symbol/rsid/drug/diplotype — only ``pathway`` distinguishes them.
# Omitting it collapsed every pathway summary of a module onto one identity key, so
# simultaneous per-pathway changes were mis-paired (wrong "before") or cancelled
# out and missed entirely (#575).
_IDENTITY_FIELDS: tuple[str, ...] = (
    "module",
    "category",
    "gene_symbol",
    "rsid",
    "drug",
    "diplotype",
    "pathway",
)

# Source-driven fields whose change constitutes a finding "meaning shift."
# Deliberately excludes carriage/zygosity (sample-derived, not release-driven)
# and the free-text finding_text (not a structured reclassification).
_MEANING_FIELDS: tuple[str, ...] = (
    "clinvar_significance",
    "evidence_level",
    "metabolizer_status",
    "pathway_level",
)

# Columns read into each finding snapshot record.
_SNAPSHOT_COLUMNS = (*_IDENTITY_FIELDS, "finding_text", *_MEANING_FIELDS)


# ── Release-version helpers ───────────────────────────────────────────────


def _release_versions(snapshot: dict[str, Any]) -> dict[str, str]:
    """Reduce a ``{db: {version, genome_build}}`` snapshot to ``{db: version}``."""
    out: dict[str, str] = {}
    for db_name, info in snapshot.items():
        version = info.get("version") if isinstance(info, dict) else info
        if version is not None:
            out[db_name] = version
    return out


def _prior_releases(prior: list[dict[str, Any]]) -> dict[str, str]:
    """The source releases behind the prior findings.

    Findings stamped in one run share a single release snapshot, but a sample can
    carry findings stamped across runs (or pre-SW-A4 rows with no provenance), so
    union every record's releases rather than trust the first — a partial first
    record must not drop sources the others recorded. Empty when no prior finding
    carries provenance.
    """
    versions: dict[str, str] = {}
    for rec in prior:
        versions.update(rec.get("release_versions") or {})
    return versions


# ── Snapshot ──────────────────────────────────────────────────────────────


def snapshot_findings(sample_engine: sa.Engine) -> list[dict[str, Any]]:
    """Read the current findings into plain records for diffing.

    Each record carries the identity + meaning columns plus ``finding_text`` (for
    display) and ``release_versions`` (the ``{db: version}`` reduction of the
    finding's provenance, used to label the "before" side of the diff).
    """
    cols = [getattr(findings.c, name) for name in _SNAPSHOT_COLUMNS]
    cols.append(findings.c.provenance)
    with sample_engine.connect() as conn:
        rows = conn.execute(sa.select(*cols)).fetchall()

    records: list[dict[str, Any]] = []
    for row in rows:
        mapping = row._mapping
        record: dict[str, Any] = {name: mapping[name] for name in _SNAPSHOT_COLUMNS}
        release_versions: dict[str, str] = {}
        provenance = mapping["provenance"]
        if provenance:
            try:
                parsed = json.loads(provenance)
                release_versions = _release_versions(parsed.get("sources") or {})
            except (json.JSONDecodeError, TypeError, AttributeError):
                release_versions = {}
        record["release_versions"] = release_versions
        records.append(record)
    return records


# ── Diff ──────────────────────────────────────────────────────────────────


def _identity_key(rec: dict[str, Any]) -> tuple[str, ...]:
    return tuple((rec.get(field) or "") for field in _IDENTITY_FIELDS)


def _group_by_key(records: list[dict[str, Any]]) -> dict[tuple[str, ...], list[dict[str, Any]]]:
    """Group records by identity key (collisions go in the same list)."""
    groups: dict[tuple[str, ...], list[dict[str, Any]]] = {}
    for rec in records:
        groups.setdefault(_identity_key(rec), []).append(rec)
    return groups


def _meaning_tuple(rec: dict[str, Any]) -> tuple[Any, ...]:
    return tuple(rec.get(field) for field in _MEANING_FIELDS)


def _field_changes(
    old_rec: dict[str, Any], new_rec: dict[str, Any]
) -> list[dict[str, str | None]]:
    return [
        {
            "field": field,
            "before": _as_text(old_rec.get(field)),
            "after": _as_text(new_rec.get(field)),
        }
        for field in _MEANING_FIELDS
        if old_rec.get(field) != new_rec.get(field)
    ]


def _diff_group(
    olds: list[dict[str, Any]], news: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Diff one identity-key group → ``(changed, added, removed)`` projections.

    Findings sharing an identity key (a collision — e.g. several ancestry
    summaries with NULL identity columns) are matched **meaning-aware**, never
    positionally: rows equal on every meaning field are paired off as *unchanged*
    first, so an untouched row is never reported as changed and never
    double-counted across buckets. Only the genuinely-different remainder is
    paired as ``changed`` (deterministically by finding_text); any surplus is
    ``removed`` (prior) or ``added`` (current). Within a group of simultaneous
    changes the old↔new pairing is inherently ambiguous, but every such row did
    change, so the honest-framing invariant (no false "your finding changed" on a
    stable row) always holds.
    """
    olds = sorted(olds, key=lambda r: r.get("finding_text") or "")
    news = sorted(news, key=lambda r: r.get("finding_text") or "")

    # 1. Pair off unchanged rows (identity already equal) by meaning-tuple.
    news_by_meaning: dict[tuple[Any, ...], list[int]] = {}
    for idx, new_rec in enumerate(news):
        news_by_meaning.setdefault(_meaning_tuple(new_rec), []).append(idx)

    used: set[int] = set()
    leftover_old: list[dict[str, Any]] = []
    for old_rec in olds:
        bucket = news_by_meaning.get(_meaning_tuple(old_rec), ())
        match_idx = next((i for i in bucket if i not in used), None)
        if match_idx is None:
            leftover_old.append(old_rec)
        else:
            used.add(match_idx)  # unchanged — emit nothing
    leftover_new = [new_rec for idx, new_rec in enumerate(news) if idx not in used]

    # 2. Pair genuinely-different rows as changed; surplus is removed / added.
    changed: list[dict[str, Any]] = []
    paired = min(len(leftover_old), len(leftover_new))
    for old_rec, new_rec in zip(leftover_old[:paired], leftover_new[:paired], strict=False):
        field_changes = _field_changes(old_rec, new_rec)
        if field_changes:  # always true here (meaning differs), but stay defensive
            entry = _display(new_rec)
            entry["changes"] = field_changes
            changed.append(entry)
    removed = [_display(rec) for rec in leftover_old[paired:]]
    added = [_display(rec) for rec in leftover_new[paired:]]
    return changed, added, removed


def _as_text(value: Any) -> str | None:
    return None if value is None else str(value)


def _display(rec: dict[str, Any]) -> dict[str, Any]:
    """The display projection of a finding (identity + meaning + finding_text)."""
    out: dict[str, Any] = {field: rec.get(field) for field in _IDENTITY_FIELDS}
    out["finding_text"] = rec.get("finding_text") or ""
    for field in _MEANING_FIELDS:
        out[field] = rec.get(field)
    return out


def _sort_key(entry: dict[str, Any]) -> tuple[str, ...]:
    return (
        entry.get("module") or "",
        entry.get("gene_symbol") or "",
        entry.get("rsid") or "",
        entry.get("drug") or "",
        entry.get("diplotype") or "",
        entry.get("pathway") or "",
        entry.get("finding_text") or "",
    )


def _empty_diff(after_releases: dict[str, str]) -> dict[str, Any]:
    return {
        "schema_version": DIFF_SCHEMA_VERSION,
        "before_releases": {},
        "after_releases": after_releases,
        "release_deltas": [],
        "changed": [],
        "added": [],
        "removed": [],
        "counts": {"changed": 0, "added": 0, "removed": 0},
    }


def compute_finding_diff(
    prior: list[dict[str, Any]] | None,
    current: list[dict[str, Any]],
    after_releases: dict[str, str],
) -> dict[str, Any]:
    """Diff ``current`` findings against the ``prior`` snapshot.

    Returns a JSON-serialisable diff: ``changed`` (a meaning field shifted),
    ``added`` (new identity key), ``removed`` (identity key gone), the source
    ``release_deltas`` that explain the change, and per-bucket ``counts``.

    With no prior snapshot to compare against (first analysis, or prior findings
    predating this feature) the diff is **empty** — never a flood of "everything
    is new."
    """
    if not prior:
        return _empty_diff(after_releases)

    before_releases = _prior_releases(prior)
    release_deltas = [
        {"db_name": db, "before": before_releases.get(db), "after": after_releases.get(db)}
        for db in sorted(set(before_releases) | set(after_releases))
        if before_releases.get(db) != after_releases.get(db)
    ]

    prior_groups = _group_by_key(prior)
    current_groups = _group_by_key(current)

    changed: list[dict[str, Any]] = []
    added: list[dict[str, Any]] = []
    removed: list[dict[str, Any]] = []

    for key in set(prior_groups) | set(current_groups):
        g_changed, g_added, g_removed = _diff_group(
            prior_groups.get(key, []), current_groups.get(key, [])
        )
        changed.extend(g_changed)
        added.extend(g_added)
        removed.extend(g_removed)

    changed.sort(key=_sort_key)
    added.sort(key=_sort_key)
    removed.sort(key=_sort_key)

    return {
        "schema_version": DIFF_SCHEMA_VERSION,
        "before_releases": before_releases,
        "after_releases": after_releases,
        "release_deltas": release_deltas,
        "changed": changed,
        "added": added,
        "removed": removed,
        "counts": {
            "changed": len(changed),
            "added": len(added),
            "removed": len(removed),
        },
    }


def has_changes(diff: dict[str, Any]) -> bool:
    """True when the diff has at least one added/removed/changed finding."""
    counts = diff.get("counts") or {}
    return bool(counts.get("changed") or counts.get("added") or counts.get("removed"))


# ── Storage (Option A: annotation_state JSON) ─────────────────────────────


def _write_state(sample_engine: sa.Engine, key: str, value: str) -> None:
    stmt = sqlite_insert(annotation_state).values(key=key, value=value)
    stmt = stmt.on_conflict_do_update(
        index_elements=[annotation_state.c.key],
        set_={"value": stmt.excluded.value, "updated_at": datetime.now(UTC)},
    )
    with sample_engine.begin() as conn:
        conn.execute(stmt)


def compute_and_store_finding_diff(
    sample_engine: sa.Engine,
    reference_engine: sa.Engine,
    prior: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    """Compute the diff of the fresh findings against ``prior`` and persist it.

    Called once after provenance stamping in the annotation task. ``prior`` is
    the snapshot taken before ``run_all_analyses`` cleared the previous run's
    findings. Best-effort: the caller wraps this so a failure never affects the
    annotation run or the staleness gate.
    """
    current = snapshot_findings(sample_engine)
    after_releases = _release_versions(read_release_snapshot(reference_engine))
    diff = compute_finding_diff(prior, current, after_releases)
    diff["dismissed"] = False
    diff["generated_at"] = datetime.now(UTC).isoformat()
    _write_state(sample_engine, DIFF_STATE_KEY, json.dumps(diff))
    logger.info(
        "finding_diff_stored",
        extra={
            "changed": diff["counts"]["changed"],
            "added": diff["counts"]["added"],
            "removed": diff["counts"]["removed"],
        },
    )
    return diff


def read_finding_diff(sample_engine: sa.Engine) -> dict[str, Any] | None:
    """Read the stored finding diff, or None when absent/unparseable."""
    with sample_engine.connect() as conn:
        row = conn.execute(
            sa.select(annotation_state.c.value).where(annotation_state.c.key == DIFF_STATE_KEY)
        ).fetchone()
    if row is None:
        return None
    try:
        return json.loads(row.value)
    except (json.JSONDecodeError, TypeError):
        return None


def dismiss_finding_diff(sample_engine: sa.Engine) -> bool:
    """Mark the stored diff dismissed (it then hides from the banner).

    Read-modify-write in a single transaction so a concurrent
    ``compute_and_store_finding_diff`` cannot resurrect a just-dismissed banner.
    Returns False when there is no stored diff to dismiss.
    """
    with sample_engine.begin() as conn:
        row = conn.execute(
            sa.select(annotation_state.c.value).where(annotation_state.c.key == DIFF_STATE_KEY)
        ).fetchone()
        if row is None:
            return False
        try:
            diff = json.loads(row.value)
        except (json.JSONDecodeError, TypeError):
            return False
        diff["dismissed"] = True
        stmt = sqlite_insert(annotation_state).values(key=DIFF_STATE_KEY, value=json.dumps(diff))
        stmt = stmt.on_conflict_do_update(
            index_elements=[annotation_state.c.key],
            set_={"value": stmt.excluded.value, "updated_at": datetime.now(UTC)},
        )
        conn.execute(stmt)
    return True

"""Whether a route-only polygenic score is current for a sample (#2118).

Metabolic, FH and eBMD are scored only by their ``POST /run`` endpoints, never
by the annotation pipeline, and a run takes minutes on a full array. Their
pages used to re-score on every visit. Each successful run now records a
fingerprint of everything the score reads, and a page re-scores only when no
fingerprint is recorded (``never``) or the current inputs no longer match it
(``stale``).

The fingerprint is deliberately conservative. A false ``stale`` costs one
re-score; a false ``current`` would show a score computed from inputs the
sample no longer has, so anything that might change a score is included:

- every backend module and the bundled score registry, and the app version
  (the scorers reach well beyond ``backend/analysis``, e.g. the ancestry
  finding selection in ``backend/services``);
- the installed ``pgs_scores.db`` file's identity (size, mtime, inode, and a
  non-empty WAL), because its tables carry no content version and a reload
  can change weights without changing their metadata;
- the reference-database release snapshot;
- the per-sample ``annotated_variants`` and ``imputed_variants`` generation
  tokens, written in the same transaction that replaces those tables;
- the content of the sample's ancestry findings (calibration, ancestry
  caveat) and of every ``monogenic_variant`` finding (the FH monogenic
  disclosure). Row ids, ``created_at`` and ``svg_path`` are left out, since
  they change when an identical finding is rewritten or re-rendered.

``annotation_state`` is never hashed wholesale: some of its keys change on
unrelated events, such as dismissing a finding diff or opening the sample.
"""

from __future__ import annotations

import functools
import hashlib
import json
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, NamedTuple

import sqlalchemy as sa

from backend.analysis.pgs_bridge import pgs_scores_db_path
from backend.db.sample_generations import (
    ANNOTATED_VARIANTS_GENERATION_KEY,
    IMPUTED_VARIANTS_GENERATION_KEY,
    upsert_state,
)
from backend.db.tables import annotation_state, findings
from backend.services.reference_versions import (
    compact_reference_versions,
    read_current_reference_snapshot,
)
from backend.version import app_version

ScoringState = Literal["current", "stale", "never"]

_FINGERPRINT_KEY_PREFIX = "route_scoring_fingerprint:"
_FINGERPRINT_SCHEMA = 1
_BACKEND_DIR = Path(__file__).resolve().parents[1]
# Finding columns that change when an identical finding is rewritten or its
# SVG is re-rendered, so they say nothing about the score's inputs.
_VOLATILE_FINDING_COLUMNS = frozenset({"id", "created_at", "svg_path"})


def _source_paths(root: Path) -> list[Path]:
    """Every backend module plus the bundled score registry under ``root``."""
    paths = sorted(root.rglob("*.py"))
    paths.append(root / "data" / "panels" / "pgs_score_registry.json")
    return paths


def _source_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in _source_paths(root):
        try:
            content = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            content = "unreadable"
        digest.update(f"{path.relative_to(root)}\0{content}\n".encode())
    return digest.hexdigest()


@functools.cache
def _code_digest() -> str:
    """Digest of the code the scorers run, cached for the process's lifetime."""
    return _source_digest(_BACKEND_DIR)


def _file_identity(path: Path) -> list[int] | None:
    try:
        st = path.stat()
    except FileNotFoundError:
        return None
    return [st.st_size, st.st_mtime_ns, st.st_ino]


def _pgs_scores_identity() -> dict[str, list[int] | None]:
    path = pgs_scores_db_path()
    wal = _file_identity(path.with_name(f"{path.name}-wal"))
    # An empty WAL holds no pages, so it is the same content as no WAL.
    return {"db": _file_identity(path), "wal": wal if wal and wal[0] > 0 else None}


def _state_value(conn: sa.Connection, key: str) -> str | None:
    return conn.execute(
        sa.select(annotation_state.c.value).where(annotation_state.c.key == key)
    ).scalar_one_or_none()


def _findings_digest(conn: sa.Connection, where: sa.ColumnElement[bool]) -> str:
    columns = [c for c in findings.c if c.name not in _VOLATILE_FINDING_COLUMNS]
    rows = conn.execute(sa.select(*columns).where(where)).fetchall()
    canonical = sorted(json.dumps(list(row), default=str) for row in rows)
    return hashlib.sha256("\n".join(canonical).encode()).hexdigest()


def compute_scoring_fingerprint(
    module: str,
    sample_engine: sa.Engine,
    reference_engine: sa.Engine,
) -> str:
    """Fingerprint of everything ``module``'s score reads for this sample."""
    with sample_engine.connect() as conn:
        sample_inputs = {
            "annotated_variants": _state_value(conn, ANNOTATED_VARIANTS_GENERATION_KEY),
            "imputed_variants": _state_value(conn, IMPUTED_VARIANTS_GENERATION_KEY),
            "ancestry_findings": _findings_digest(conn, findings.c.module == "ancestry"),
            "monogenic_findings": _findings_digest(
                conn, findings.c.category == "monogenic_variant"
            ),
        }
    payload = {
        "schema": _FINGERPRINT_SCHEMA,
        "module": module,
        "app_version": app_version(),
        "code": _code_digest(),
        "pgs_scores": _pgs_scores_identity(),
        "reference_versions": compact_reference_versions(
            read_current_reference_snapshot(reference_engine)
        ),
        "sample": sample_inputs,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


_run_locks: dict[tuple[str, str], threading.Lock] = {}
_run_locks_guard = threading.Lock()


@contextmanager
def serialized_scoring_run(module: str, sample_engine: sa.Engine) -> Iterator[None]:
    """Let one run of ``module`` per sample proceed at a time.

    A run takes its fingerprint, stores its findings and then records the
    fingerprint, in separate transactions. Two overlapping runs whose inputs
    differ could leave one run's findings under the other's fingerprint, so a
    second run waits for the first. The app serves from a single process
    (``backend/main.py``), so an in-process lock covers every run.
    """
    key = (module, str(sample_engine.url.database))
    with _run_locks_guard:
        lock = _run_locks.setdefault(key, threading.Lock())
    with lock:
        yield


def record_scoring_fingerprint(module: str, sample_engine: sa.Engine, fingerprint: str) -> None:
    """Record that ``module`` was scored from inputs matching ``fingerprint``.

    Call only after the run has stored every finding, with the fingerprint
    taken before scoring began: an input that changes during the run then
    leaves the score ``stale`` rather than wrongly ``current``.
    """
    value = json.dumps({"fingerprint": fingerprint, "scored_at": datetime.now(UTC).isoformat()})
    with sample_engine.begin() as conn:
        upsert_state(conn, _FINGERPRINT_KEY_PREFIX + module, value)


class ScoringRead(NamedTuple):
    """A scoring state and the recorded fingerprint it was judged against."""

    state: ScoringState
    recorded: str | None


def read_scoring(
    module: str,
    sample_engine: sa.Engine,
    reference_engine: sa.Engine,
) -> ScoringRead:
    """``never`` with no recorded run, ``stale`` when an input has changed since."""
    with sample_engine.connect() as conn:
        raw = _state_value(conn, _FINGERPRINT_KEY_PREFIX + module)
    if raw is None:
        return ScoringRead("never", None)
    try:
        recorded = json.loads(raw)["fingerprint"]
    except (ValueError, KeyError, TypeError):
        return ScoringRead("stale", None)
    current = compute_scoring_fingerprint(module, sample_engine, reference_engine)
    return ScoringRead("current" if recorded == current else "stale", recorded)


def confirm_scoring(
    first: ScoringRead,
    module: str,
    sample_engine: sa.Engine,
    reference_engine: sa.Engine,
) -> ScoringRead:
    """Confirm a ``current`` read once the response's findings have been read.

    Call with the read taken before the findings. It stays ``current`` only if
    a second read is ``current`` against the same recorded fingerprint, so
    neither an input written meanwhile (a re-annotation swap, an imputation
    write) nor another run recording a new fingerprint can let the findings
    already read be served as current. A first read that was not ``current``
    already makes the page re-score.

    The returned ``recorded`` fingerprint identifies the scoring generation the
    response belongs to, so a page that reads one module through two requests
    can tell whether both came from the same run; it is ``None`` when the reads
    disagree, since the findings could then belong to either run.
    """
    if first.state != "current":
        return first
    second = read_scoring(module, sample_engine, reference_engine)
    if second.state == "current" and second.recorded == first.recorded:
        return second
    # The findings were read between two different recordings, so which run they
    # came from is unknown: no generation, which never matches a real one.
    return ScoringRead("stale", None)

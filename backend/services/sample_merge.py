"""Sample-merge service core (AncestryDNA Plan §10.2, §10.5, §10.6; Steps 65, 67).

Materialises a merged per-sample DB from two source samples linked to the
same ``individuals`` row. The merge is a stand-alone artefact: every existing
analysis module reads the merged sample exactly like any other sample because
the resulting ``raw_variants`` table carries the same columns plus four
provenance fields (``source``, ``concordance``, ``discordant_alt_genotype``,
``alt_rsid``) introduced in Plan §10.4 (b) and ship-defaulted to ``''`` on
every unmerged sample (Step 63). Per-row PK is ``(chrom, pos)`` rather than
``rsid`` (Plan §10.4 a, Step 64) so two source rows carrying different rsids
at the same coordinate collapse to one row — the canonical merge-key
contract.

Public entry points:

* :func:`merge_samples` (Step 65 / MRG-02) — validates the request, opens
  both source DBs read-only, streams ``raw_variants`` into a coordinate-
  indexed in-memory map, applies the §10.2 / §10.3 semantics under the
  requested strategy, writes the new ``samples`` row + per-sample DB + single
  ``merge_provenance`` row, and enqueues the standard annotation job. The
  caller polls existing ``GET /api/annotation/status/{job_id}``.
* :func:`preview_merge` (Step 67 / MRG-03) — dry-run that runs the same
  validation + read + semantics pass and returns
  ``{concordance_summary, est_duration_seconds}`` without writing anything.
  Backs the merge wizard's preview step (Plan §10.6 / §10.7).

Both entry points share :func:`_compute_merge_plan`, which is the canonical
pre-write pipeline; the preview/commit split therefore exercises the exact
same validation surface (Plan §10.5 step 1) and the exact same §10.2 /
§10.3 semantics. The only difference is what happens *after* the plan is
computed.

Validation contract (Plan §10.5 step 1):

* ``source_sample_ids`` must list exactly two distinct samples.
* Both samples must belong to ``individual_id``.
* Both samples' most-recent annotation job must be ``status='complete'``.
* Neither source may be stale per :func:`backend.services.staleness.is_sample_stale`.

Membership / status / shape failures raise
:class:`InvalidMergeRequestError` (the API routes map to HTTP 422). Stale-
source failures raise :class:`StaleSourceError` carrying a structured
detail dict (the API routes map to HTTP 423 — same shape Plan §7.5
declares for ``require_fresh_sample``).
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING

import sqlalchemy as sa

from backend.db.sample_schema import SAMPLE_SCHEMA_VERSION, create_sample_tables
from backend.db.sqlite_engine import make_sqlite_engine
from backend.db.tables import (
    jobs,
    merge_provenance,
    raw_variants,
    reannotation_prompts,
    sample_metadata_table,
    samples,
)
from backend.services.sample_operation_lock import sample_merge_lease
from backend.services.staleness import is_sample_stale

if TYPE_CHECKING:
    from pathlib import Path

    from backend.db.connection import DBRegistry

logger = logging.getLogger(__name__)


# Plan §10.5 step 6: per-batch insert size matches the file-ingest path.
_INSERT_BATCH = 10_000
# Plan §10.2 step 2: keep the VEP-bundle conflict probe under SQLite's
# 999-variable limit.
_VEP_BUNDLE_PROBE_BATCH = 500
# Plan §10.3: merged ``flag_only`` strategy writes this canonical "ambiguous"
# sentinel — it round-trips through ``is_no_call`` so every analysis module
# treats it as a no-call until the user resolves the conflict.
_NO_CALL_SENTINEL = "??"
# Plan §10.5 step 5: distinct ``file_format`` token identifying merged samples
# across every reader (`_vendor_token` extracts the prefix; the dashboard +
# variant table render it).
_MERGED_FILE_FORMAT = "merged_v1"
# Narrower no-call set than ``backend.analysis.zygosity.is_no_call`` —
# excludes indel codes (``II``/``DD``/``DI``/``ID``) because at the merge
# boundary indels are real calls (just unscoreable for trait modules
# downstream). Plan §15.1 MRG-08 mandates ``II`` vs ``DI`` resolves to
# ``discordant`` (not collapsed-as-no-call ``match``); the trait modules
# separately call ``is_no_call`` to skip indel rows they can't score.
_MERGE_NO_CALL_TOKENS = frozenset({"", "--", "??", "-", "0", "00"})


def _is_merge_no_call(genotype: str | None) -> bool:
    """No-call predicate scoped to the merge boundary (Plan §15.1 MRG-08).

    Distinct from :func:`backend.analysis.zygosity.is_no_call`, which
    additionally treats indel codes as unscoreable for trait scoring.
    Indels are full calls at the merge layer — homozygous insertion vs.
    heterozygous indel is a real concordance signal that must not be
    swallowed.
    """
    if genotype is None:
        return True
    return genotype.strip() in _MERGE_NO_CALL_TOKENS


def _canonical_genotype(genotype: str) -> str:
    """Defensive sorted-pair canonicalization (Plan §10.2 step 3 bullet 1).

    The parser canonicalizes at ingestion (sorted uppercase pair) so a
    Phase-1+ sample DB never sees ``"GA"``. Pre-Phase-1 sample DBs imported
    from backup, however, may still carry un-sorted pairs from the legacy
    23andMe parser, and Plan §10.2 explicitly notes "Defensive in-merge
    re-sort remains in case a pre-Phase-1 sample DB carries an
    un-canonicalized genotype." This helper is that defensive layer: a
    no-op on every already-canonical input, and idempotent.

    Two-character allele pairs are sorted into canonical form. Sentinels
    (``--`` / ``??`` / ``00`` / …) and lengths other than two are left
    unchanged so this helper composes safely with :func:`_is_merge_no_call`.
    """
    if len(genotype) != 2:
        return genotype
    a, b = genotype[0], genotype[1]
    return genotype if a <= b else b + a


class MergeStrategy(StrEnum):
    """The §10.3 strategies that decide which call wins at a discordant locus.

    The default is ``FLAG_ONLY`` (clinically safest — emits the canonical
    no-call sentinel instead of guessing). ``PREFER_S1`` and ``PREFER_S2``
    keep the explicitly named source-order side. The legacy vendor strategies
    keep whichever side matches the named vendor by reading each source
    sample's ``file_format`` prefix (so source order in the request is
    irrelevant for those strategy semantics; it only matters for the
    rsid-collapse tiebreaker per §10.2 step 2).
    """

    PREFER_S1 = "prefer_s1"
    PREFER_S2 = "prefer_s2"
    PREFER_23ANDME = "prefer_23andme"
    PREFER_ANCESTRYDNA = "prefer_ancestrydna"
    FLAG_ONLY = "flag_only"


class MergeError(Exception):
    """Base class for sample-merge errors surfaced by :func:`merge_samples`."""


class InvalidMergeRequestError(MergeError):
    """Validation failure that the API route maps to HTTP 422.

    Covers: wrong source count, sample not found, sample not linked to the
    named individual, source is itself an already-merged sample, source
    annotation not complete, unknown strategy.
    """


class StaleSourceError(MergeError):
    """One or more source samples are stale per Plan §7.4.

    Carries the structured ``detail`` dict the Step 68 route surfaces as the
    HTTP 423 body so the frontend can render the re-annotate banner with the
    same payload shape Plan §7.5 declares for ``require_fresh_sample``.
    """

    def __init__(self, stale_sample_ids: list[int], detail: dict) -> None:
        super().__init__(detail.get("message", "Source sample is stale"))
        self.stale_sample_ids = stale_sample_ids
        self.detail = detail


@dataclass(frozen=True)
class _MergedRow:
    """One row destined for the merged sample's ``raw_variants`` table."""

    rsid: str
    chrom: str
    pos: int
    genotype: str
    source: str  # 'S1' | 'S2' | 'both'
    concordance: str  # 'match' | 'filled_nocall' | 'discordant' | 'unique'
    discordant_alt_genotype: str
    alt_rsid: str


@dataclass
class _ConcordanceSummary:
    """Aggregate counts written into ``merge_provenance.concordance_summary``."""

    match: int = 0
    filled_nocall: int = 0
    discordant: int = 0
    unique_S1: int = 0
    unique_S2: int = 0
    collapsed_rsid: int = 0

    def to_dict(self) -> dict[str, int]:
        return {
            "match": self.match,
            "filled_nocall": self.filled_nocall,
            "discordant": self.discordant,
            "unique_S1": self.unique_S1,
            "unique_S2": self.unique_S2,
            "collapsed_rsid": self.collapsed_rsid,
        }


def _read_sample_row(reference_engine: sa.Engine, sample_id: int) -> sa.Row | None:
    with reference_engine.connect() as conn:
        return conn.execute(sa.select(samples).where(samples.c.id == sample_id)).fetchone()


def _latest_annotation_status(reference_engine: sa.Engine, sample_id: int) -> str | None:
    """Return the most-recent annotation job's status for ``sample_id``.

    ``None`` when no annotation job exists yet (a sample that was uploaded
    but never annotated — Plan §10.5 step 1 blocks merging it).
    """
    with reference_engine.connect() as conn:
        row = conn.execute(
            sa.select(jobs.c.status)
            .where(jobs.c.sample_id == sample_id)
            .where(jobs.c.job_type == "annotation")
            .order_by(jobs.c.updated_at.desc())
            .limit(1)
        ).fetchone()
    return row.status if row else None


def _stream_raw_variants(engine: sa.Engine) -> dict[tuple[str, int], dict[str, str]]:
    """Stream a source sample's ``raw_variants`` into a coordinate-keyed map.

    Plan §10.5 step 3: ~840 k loci × ~80 bytes per dict ≈ 80 MB peak per source
    — acceptable for a one-shot merge pass on a workstation.
    """
    coords: dict[tuple[str, int], dict[str, str]] = {}
    with engine.connect() as conn:
        for row in conn.execute(
            sa.select(
                raw_variants.c.rsid,
                raw_variants.c.chrom,
                raw_variants.c.pos,
                raw_variants.c.genotype,
            )
            # Deterministic ORDER BY: a single-vendor source DB keeps the
            # rsid PK (the v7→v8 in-place upgrade path — see
            # backend/db/sample_schema.py §10.4a), so two distinct rows can
            # legitimately share one (chrom, pos). The coordinate-keyed map
            # below collapses them with last-write-wins, so without a stable
            # ordering the survivor would depend on physical row order,
            # making merge results / file_hash non-deterministic. Ordering by
            # (chrom, pos, rsid) fixes the highest rsid as the deterministic
            # winner for any duplicate coordinate.
            .order_by(
                raw_variants.c.chrom,
                raw_variants.c.pos,
                raw_variants.c.rsid,
            )
        ):
            coords[(row.chrom, int(row.pos))] = {
                "rsid": row.rsid,
                "genotype": row.genotype,
            }
    return coords


def _rsids_in_vep_bundle(vep_engine: sa.Engine, rsids: set[str]) -> set[str]:
    """Return the subset of ``rsids`` present in ``vep_annotations``.

    Best-effort: a missing / unreadable bundle returns an empty set, which
    correctly falls through to the §10.2 step 2 "both or neither in the
    bundle → prefer S1" branch.
    """
    if not rsids:
        return set()
    try:
        hits: set[str] = set()
        rsid_list = list(rsids)
        with vep_engine.connect() as conn:
            for i in range(0, len(rsid_list), _VEP_BUNDLE_PROBE_BATCH):
                batch = rsid_list[i : i + _VEP_BUNDLE_PROBE_BATCH]
                placeholders = ",".join("?" * len(batch))
                result = conn.exec_driver_sql(
                    "SELECT DISTINCT rsid FROM vep_annotations "  # noqa: S608
                    f"WHERE rsid IN ({placeholders})",
                    tuple(batch),
                )
                hits.update(row[0] for row in result if row[0])
        return hits
    except Exception as exc:  # noqa: BLE001
        # Bundle file missing / table missing / engine cannot connect — log
        # and fall through to the S1 tiebreaker. Never bubble up: the merge
        # service must keep working on machines that haven't installed the
        # production VEP bundle yet (Step 4 / ADNA-00a).
        logger.warning(
            "merge_vep_bundle_unreachable",
            extra={"error": str(exc), "conflict_rsid_count": len(rsids)},
        )
        return set()


def _vendor_token(file_format: str | None) -> str:
    """Extract the vendor prefix from ``samples.file_format``.

    Mirrors ``backend/api/routes/individuals.py::_vendor_from_file_format``;
    duplicated here to keep ``services/sample_merge.py`` free of API-layer
    imports.
    """
    if not file_format:
        return ""
    return file_format.split("_", 1)[0].lower()


def _resolve_winner(strategy: MergeStrategy, s1_vendor: str, s2_vendor: str) -> str:
    """Return ``'S1'`` or ``'S2'`` for the winning side at a discordant locus.

    Returns ``''`` for ``FLAG_ONLY`` — the caller writes the no-call sentinel
    and the ``"S1=...;S2=..."`` paired encoding instead.

    Sample-position strategies return their named side directly. Tiebreaker
    for legacy vendor strategies when both samples share the strategy's
    target vendor (or neither does): S1 wins by convention, matching Plan
    §10.2 step 2's fallback for ambiguous rsid choices.
    """
    if strategy is MergeStrategy.FLAG_ONLY:
        return ""
    if strategy is MergeStrategy.PREFER_S1:
        return "S1"
    if strategy is MergeStrategy.PREFER_S2:
        return "S2"
    target = "23andme" if strategy is MergeStrategy.PREFER_23ANDME else "ancestrydna"
    s1_is_target = s1_vendor == target
    s2_is_target = s2_vendor == target
    if s1_is_target and not s2_is_target:
        return "S1"
    if s2_is_target and not s1_is_target:
        return "S2"
    return "S1"


def _apply_semantics(
    s1_coords: dict[tuple[str, int], dict[str, str]],
    s2_coords: dict[tuple[str, int], dict[str, str]],
    *,
    strategy: MergeStrategy,
    rsids_in_bundle: set[str],
    s1_vendor: str,
    s2_vendor: str,
) -> tuple[list[_MergedRow], _ConcordanceSummary]:
    """Apply §10.2 / §10.3 semantics over the coordinate union.

    Output is sorted by ``(chrom, pos)`` so test assertions are deterministic
    and bulk inserts land in PK order (small SQLite write-throughput win on
    the merged sample's ``(chrom, pos)`` PK).
    """
    summary = _ConcordanceSummary()
    rows: list[_MergedRow] = []

    for coord in sorted(set(s1_coords) | set(s2_coords)):
        chrom, pos = coord
        s1 = s1_coords.get(coord)
        s2 = s2_coords.get(coord)

        # §10.2 step 4: coordinate present in only one side.
        if s1 is not None and s2 is None:
            rows.append(
                _MergedRow(
                    rsid=s1["rsid"],
                    chrom=chrom,
                    pos=pos,
                    genotype=s1["genotype"],
                    source="S1",
                    concordance="unique",
                    discordant_alt_genotype="",
                    alt_rsid="",
                )
            )
            summary.unique_S1 += 1
            continue
        if s2 is not None and s1 is None:
            rows.append(
                _MergedRow(
                    rsid=s2["rsid"],
                    chrom=chrom,
                    pos=pos,
                    genotype=s2["genotype"],
                    source="S2",
                    concordance="unique",
                    discordant_alt_genotype="",
                    alt_rsid="",
                )
            )
            summary.unique_S2 += 1
            continue

        # Both sides present at this coordinate. Resolve rsid first (§10.2
        # step 2), then genotype concordance (§10.2 step 3).
        assert s1 is not None and s2 is not None
        s1_rsid = s1["rsid"]
        s2_rsid = s2["rsid"]
        if s1_rsid != s2_rsid:
            s1_hit = s1_rsid in rsids_in_bundle
            s2_hit = s2_rsid in rsids_in_bundle
            if s1_hit and not s2_hit:
                chosen_rsid, lost_rsid = s1_rsid, s2_rsid
            elif s2_hit and not s1_hit:
                chosen_rsid, lost_rsid = s2_rsid, s1_rsid
            else:
                chosen_rsid, lost_rsid = s1_rsid, s2_rsid
            summary.collapsed_rsid += 1
        else:
            chosen_rsid = s1_rsid
            lost_rsid = ""

        s1_gt = s1["genotype"]
        s2_gt = s2["genotype"]
        s1_nc = _is_merge_no_call(s1_gt)
        s2_nc = _is_merge_no_call(s2_gt)

        # Both no-call: emit one row as 'match' (no disagreement to surface).
        # The merged genotype keeps S1's no-call sentinel so the round-trip
        # through ``is_no_call`` continues to suppress downstream findings.
        if s1_nc and s2_nc:
            rows.append(
                _MergedRow(
                    rsid=chosen_rsid,
                    chrom=chrom,
                    pos=pos,
                    genotype=s1_gt,
                    source="both",
                    concordance="match",
                    discordant_alt_genotype="",
                    alt_rsid=lost_rsid,
                )
            )
            summary.match += 1
            continue

        # Exactly one side no-call: emit the called row (§10.2 step 3 bullet 2).
        if s1_nc:
            rows.append(
                _MergedRow(
                    rsid=chosen_rsid,
                    chrom=chrom,
                    pos=pos,
                    genotype=s2_gt,
                    source="S2",
                    concordance="filled_nocall",
                    discordant_alt_genotype="",
                    alt_rsid=lost_rsid,
                )
            )
            summary.filled_nocall += 1
            continue
        if s2_nc:
            rows.append(
                _MergedRow(
                    rsid=chosen_rsid,
                    chrom=chrom,
                    pos=pos,
                    genotype=s1_gt,
                    source="S1",
                    concordance="filled_nocall",
                    discordant_alt_genotype="",
                    alt_rsid=lost_rsid,
                )
            )
            summary.filled_nocall += 1
            continue

        # Both called. Defensive in-merge re-sort (Plan §10.2 step 3
        # bullet 1) absorbs any un-canonicalized genotype that survived from
        # a pre-Phase-1 sample DB so ``"AG" == "GA"`` resolves to ``match``.
        s1_canon = _canonical_genotype(s1_gt)
        s2_canon = _canonical_genotype(s2_gt)
        if s1_canon == s2_canon:
            rows.append(
                _MergedRow(
                    rsid=chosen_rsid,
                    chrom=chrom,
                    pos=pos,
                    genotype=s1_canon,
                    source="both",
                    concordance="match",
                    discordant_alt_genotype="",
                    alt_rsid=lost_rsid,
                )
            )
            summary.match += 1
            continue

        # Discordant call — apply strategy.
        if strategy is MergeStrategy.FLAG_ONLY:
            kept_gt = _NO_CALL_SENTINEL
            discordant_alt = f"S1={s1_gt};S2={s2_gt}"
        else:
            winner = _resolve_winner(strategy, s1_vendor, s2_vendor)
            if winner == "S2":
                kept_gt = s2_gt
                discordant_alt = f"S1={s1_gt}"
            else:
                kept_gt = s1_gt
                discordant_alt = f"S2={s2_gt}"

        rows.append(
            _MergedRow(
                rsid=chosen_rsid,
                chrom=chrom,
                pos=pos,
                genotype=kept_gt,
                source="both",
                concordance="discordant",
                discordant_alt_genotype=discordant_alt,
                alt_rsid=lost_rsid,
            )
        )
        summary.discordant += 1

    return rows, summary


def _validate_merged_rsids_annotation_safe(rows: list[_MergedRow]) -> None:
    """Reject merged rows that cannot be keyed safely by annotated_variants.rsid."""
    seen: dict[str, tuple[str, int]] = {}
    collisions: dict[str, list[tuple[str, int]]] = {}

    for row in rows:
        coord = (row.chrom, row.pos)
        first_coord = seen.setdefault(row.rsid, coord)
        if first_coord != coord:
            collisions.setdefault(row.rsid, [first_coord]).append(coord)

    if collisions:
        details = "; ".join(
            f"{rsid}: {', '.join(f'{chrom}:{pos}' for chrom, pos in coords)}"
            for rsid, coords in sorted(collisions.items())[:5]
        )
        suffix = "..." if len(collisions) > 5 else ""
        raise InvalidMergeRequestError(
            "merged sample has rsID(s) at multiple coordinates; "
            "annotated_variants is keyed by rsid, so annotation would silently collapse "
            f"distinct loci ({details}{suffix})"
        )


def _compute_file_hash(s1_hash: str, s2_hash: str, strategy: MergeStrategy) -> str:
    """SHA-256 over ``S1 ‖ S2 ‖ strategy ‖ SAMPLE_SCHEMA_VERSION`` (Plan §10.5 step 5).

    Order-sensitive on purpose. Including ``SAMPLE_SCHEMA_VERSION`` means a
    re-merge after a v8 → v9 bump produces a distinct hash even when the
    same sources + strategy come in.
    """
    payload = (f"{s1_hash}|{s2_hash}|{strategy.value}|{SAMPLE_SCHEMA_VERSION}").encode()
    return hashlib.sha256(payload).hexdigest()


@dataclass(frozen=True)
class _MergePlan:
    """Result of the shared pre-write pipeline (validation + read + semantics).

    Shared by :func:`merge_samples` and :func:`preview_merge`: the former
    writes the per-sample DB + provenance + enqueues annotation; the latter
    discards everything except ``summary`` and ``rows`` (the latter feeds
    the duration estimator).
    """

    s1_row: sa.Row
    s2_row: sa.Row
    rows: list[_MergedRow]
    summary: _ConcordanceSummary


def _validate_request_shape(source_sample_ids: list[int], strategy: MergeStrategy) -> None:
    """Plan §10.5 step 1 — count / distinctness / strategy enum validation.

    Display-name validation is NOT here because :func:`preview_merge` does
    not take a display_name (the wizard fills it in only on the confirm
    step). :func:`merge_samples` performs the display-name check separately.
    """
    if len(source_sample_ids) != 2:
        raise InvalidMergeRequestError(
            f"source_sample_ids must contain exactly 2 ids; got {len(source_sample_ids)}"
        )
    if source_sample_ids[0] == source_sample_ids[1]:
        raise InvalidMergeRequestError("source_sample_ids must reference two distinct samples")
    if not isinstance(strategy, MergeStrategy):
        raise InvalidMergeRequestError(f"unknown merge strategy: {strategy!r}")


def _validate_samples_and_freshness(
    registry: DBRegistry, source_sample_ids: list[int], individual_id: int
) -> tuple[sa.Row, sa.Row]:
    """Plan §10.5 step 1 — sample existence / linkage / status + Plan §7.4 freshness.

    Returns the two ``samples`` rows in the same order as
    ``source_sample_ids``. Raises :class:`InvalidMergeRequestError` on
    membership / status failures and :class:`StaleSourceError` on a stale
    source (the route maps to HTTP 423 with the structured payload Plan
    §7.5 declares for ``require_fresh_sample``).
    """
    reference_engine = registry.reference_engine
    s1_id, s2_id = source_sample_ids

    sample_rows: list[sa.Row] = []
    for sid in (s1_id, s2_id):
        row = _read_sample_row(reference_engine, sid)
        if row is None:
            raise InvalidMergeRequestError(f"sample {sid} not found")
        if row.individual_id != individual_id:
            raise InvalidMergeRequestError(
                f"sample {sid} is not linked to individual {individual_id}"
            )
        # Sources must be unmerged. The wizard already filters merged samples out
        # of the picker and the user documentation says "exactly two unmerged
        # source samples", but nothing stopped a direct API caller — and a merged
        # source is not merely unsupported, it is lossy: ``_stream_raw_variants``
        # reads only rsid/chrom/pos/genotype, so the child's ``source`` /
        # ``concordance`` / ``discordant_alt_genotype`` provenance is dropped, a
        # flagged discordance re-reads as a plain no-call, and ``_vendor_token``
        # yields "merged", which matches neither vendor preference strategy and
        # silently falls through to the S1 tiebreaker. It also produces a
        # second-generation child that single-level cascade deletion cannot see.
        #
        # Ordered after the linkage check so a merged sample belonging to someone
        # else keeps the more specific error, and before the annotation-status
        # check so this reason wins deterministically over "annotation not
        # complete" for a merged sample that is still being annotated.
        if row.file_format == _MERGED_FILE_FORMAT:
            raise InvalidMergeRequestError(
                f"sample {sid} is already a merged sample and cannot be used as a merge source"
            )
        status = _latest_annotation_status(reference_engine, sid)
        if status != "complete":
            raise InvalidMergeRequestError(
                f"sample {sid} annotation not complete (status={status!r})"
            )
        sample_rows.append(row)

    stale_ids = [sid for sid in (s1_id, s2_id) if is_sample_stale(sid)]
    if stale_ids:
        detail = {
            "error": "stale_source_sample",
            "stale_sample_ids": stale_ids,
            "message": (
                "One or more source samples were annotated against an older "
                "VEP bundle. Re-annotate before merging."
            ),
            "reannotate_url": "/api/annotation/{sample_id}",
        }
        raise StaleSourceError(stale_ids, detail)

    return sample_rows[0], sample_rows[1]


def _compute_merge_plan(
    registry: DBRegistry,
    source_sample_ids: list[int],
    individual_id: int,
    strategy: MergeStrategy,
) -> _MergePlan:
    """Run Plan §10.5 steps 1–4: validate, open, stream, apply semantics.

    The shared pre-write pipeline. :func:`merge_samples` calls this and
    then performs steps 5–8 (write + enqueue); :func:`preview_merge` calls
    this and packages the result as the dry-run response (Plan §10.6).
    """
    _validate_request_shape(source_sample_ids, strategy)
    s1_row, s2_row = _validate_samples_and_freshness(registry, source_sample_ids, individual_id)

    settings = registry.settings
    s1_engine = registry.get_sample_engine(settings.data_dir / s1_row.db_path)
    s2_engine = registry.get_sample_engine(settings.data_dir / s2_row.db_path)

    s1_coords = _stream_raw_variants(s1_engine)
    s2_coords = _stream_raw_variants(s2_engine)

    # Plan §10.2 step 2: probe the VEP bundle only for the rsids that
    # actually conflict — typically <1% of total loci on real samples.
    conflict_rsids: set[str] = set()
    for coord in set(s1_coords) & set(s2_coords):
        a = s1_coords[coord]["rsid"]
        b = s2_coords[coord]["rsid"]
        if a != b:
            conflict_rsids.update({a, b})
    rsids_in_bundle: set[str] = set()
    if conflict_rsids:
        rsids_in_bundle = _rsids_in_vep_bundle(registry.vep_engine, conflict_rsids)

    merged_rows, summary = _apply_semantics(
        s1_coords,
        s2_coords,
        strategy=strategy,
        rsids_in_bundle=rsids_in_bundle,
        s1_vendor=_vendor_token(s1_row.file_format),
        s2_vendor=_vendor_token(s2_row.file_format),
    )
    _validate_merged_rsids_annotation_safe(merged_rows)
    return _MergePlan(s1_row=s1_row, s2_row=s2_row, rows=merged_rows, summary=summary)


# Plan §10.6 / §10.7: the wizard renders ``est_duration_seconds`` as a
# rough "this will take ~N seconds" hint on the confirm step. The estimate
# covers commit-time work (write the per-sample DB rows + the standard
# annotation pass). Bucketed against the merge perf budget locked by
# Step 85 / MRG-09a (700k-variant samples land in <30 s on the WSL2
# reference machine): a 5 s baseline absorbs annotation-queue overhead +
# ~25k rows/sec accounts for the write + downstream annotation pass.
_DURATION_BASELINE_SECONDS = 5
_DURATION_ROWS_PER_SECOND = 25_000


def _estimate_duration_seconds(merged_row_count: int) -> int:
    """Plan §10.6: return an integer-second estimate for the commit phase."""
    return _DURATION_BASELINE_SECONDS + (merged_row_count // _DURATION_ROWS_PER_SECOND)


def preview_merge(
    registry: DBRegistry,
    source_sample_ids: list[int],
    individual_id: int,
    strategy: MergeStrategy,
) -> dict:
    """Dry-run preview backing ``POST /api/individuals/{id}/merge/preview``.

    Runs Plan §10.5 steps 1–4 (validation, read, semantics) and returns
    the §10.6 wizard payload without creating any sample rows, per-sample
    DB files, or merge-provenance rows. Same error surface as
    :func:`merge_samples`:

    * :class:`InvalidMergeRequestError` for shape / membership / status
      failures (the route maps to HTTP 422).
    * :class:`StaleSourceError` for stale-source failures (HTTP 423 with
      the same payload shape declared by Plan §7.5).
    """
    plan = _compute_merge_plan(registry, source_sample_ids, individual_id, strategy)
    return {
        "concordance_summary": plan.summary.to_dict(),
        "est_duration_seconds": _estimate_duration_seconds(len(plan.rows)),
    }


# A merged child is built at this name before it is published, and keeps it
# afterwards. The embedded uuid is the publication identity: unlike the numeric
# ``samples.id`` and the ``samples/sample_<id>.db`` path derived from it, SQLite
# never hands it out twice, so a leftover file provably belongs to the one merge
# attempt that created it and can never be confused with a later sample that
# reused a vacated rowid (#2329).
_MERGED_DB_NAME_PREFIX = "merged_"
_MERGED_DB_NAME_RE = re.compile(r"merged_[0-9a-f]{32}\.db\Z")


# The bootstrap engine is opened with ``wal=False``, so an interrupted merge
# leaves a ``-journal`` rollback journal rather than ``-wal``/``-shm``. Sweeping
# only the WAL pair left that journal behind permanently.
_SAMPLE_DB_SIDECAR_SUFFIXES = ("", "-journal", "-wal", "-shm")


def _sample_db_sidecars(sample_db_path: Path) -> tuple[Path, ...]:
    """The database file and every sidecar SQLite may leave beside it."""
    return tuple(
        sample_db_path if not suffix else sample_db_path.with_name(sample_db_path.name + suffix)
        for suffix in _SAMPLE_DB_SIDECAR_SUFFIXES
    )


def _discard_unpublished_merge(sample_db_path: Path) -> None:
    """Remove a merged database that was never published.

    Nothing in the registry references this file — publication is a single
    commit that happens only after the database is complete — so there is no
    row to roll back and no cached engine to dispose. Cleanup sub-failures are
    logged, never raised: the caller re-raises the original error, and the
    startup sweep collects anything left behind.
    """
    for candidate in _sample_db_sidecars(sample_db_path):
        try:
            candidate.unlink(missing_ok=True)
        except OSError as exc:
            logger.warning(
                "merge_discard_unlink_failed",
                extra={"path": str(candidate), "error": str(exc)},
            )


def _is_published(registry: DBRegistry, db_path: str) -> bool:
    """Whether a ``samples`` row already references ``db_path``.

    Consulted only on the failure path, to distinguish "the publishing commit
    never landed" from "it landed and something interrupted the unwind". An
    unreadable registry answers ``True`` so the database is preserved: keeping
    an orphan file is recoverable by the startup sweep, deleting a published
    one is not.
    """
    try:
        with registry.reference_engine.connect() as conn:
            return (
                conn.execute(
                    sa.select(sa.func.count())
                    .select_from(samples)
                    .where(samples.c.db_path == db_path)
                ).scalar_one()
                > 0
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning("merge_publication_check_failed", extra={"error": str(exc)})
        return True


def recover_unpublished_merge_artifacts(registry: DBRegistry) -> list[str]:
    """Delete merged databases that no ``samples`` row references.

    Runs at startup. A merge that is interrupted between creating its database
    and publishing its registry row leaves exactly one such file; because the
    file name carries the publication uuid rather than a sample id, "no row
    points here" is a sound ownership proof. A successor that reused the
    numeric id lives at ``samples/sample_<id>.db`` and is structurally
    ineligible — this sweep only ever considers ``merged_<uuid>.db`` names.

    Returns the names removed, for logging and tests.
    """
    samples_dir = registry.settings.data_dir / "samples"
    if not samples_dir.is_dir():
        return []

    try:
        with registry.reference_engine.connect() as conn:
            referenced = {
                str(row.db_path) for row in conn.execute(sa.select(samples.c.db_path)).fetchall()
            }
    except Exception as exc:  # noqa: BLE001
        # A half-broken install must still start. Sweeping without a readable
        # registry could delete a published child, so do nothing at all.
        logger.warning("merge_recovery_registry_unreadable", extra={"error": str(exc)})
        return []

    removed: list[str] = []
    for candidate in sorted(samples_dir.glob(f"{_MERGED_DB_NAME_PREFIX}*.db")):
        if not _MERGED_DB_NAME_RE.fullmatch(candidate.name):
            continue
        if f"samples/{candidate.name}" in referenced:
            continue
        _discard_unpublished_merge(candidate)
        removed.append(candidate.name)

    if removed:
        logger.info("merge_recovery_removed_unpublished", extra={"artifacts": removed})
    return removed


def merge_samples(
    registry: DBRegistry,
    source_sample_ids: list[int],
    individual_id: int,
    strategy: MergeStrategy,
    display_name: str,
) -> int:
    """Merge two source samples into a new merged sample. Returns the new ``sample_id``.

    Plan §10.5 contract:

    1. Validate membership, completion, and freshness.
    2. Open both source DBs read-only.
    3. Stream their ``raw_variants`` into a ``(chrom, pos)``-keyed map.
    4. Apply §10.2 / §10.3 semantics.
    5. Insert a new ``samples`` row (``file_format='merged_v1'``,
       deterministic ``file_hash``, ``individual_id`` propagated).
    6. Create the per-sample DB with ``is_merged_sample=True`` so
       ``raw_variants`` materialises with the ``(chrom, pos)`` PK.
    7. Write the single ``merge_provenance`` row.
    8. Enqueue the standard annotation job (the caller polls
       ``GET /api/annotation/status/{job_id}``).
    """
    if not display_name or not display_name.strip():
        raise InvalidMergeRequestError("display_name is required")

    # Reject an invalid request before touching the lease registry. A merge
    # refused for membership, annotation status or staleness must leave no
    # artefact at all, and a lease row is an artefact — `test_no_new_jobs_row`
    # in tests/backend/test_sample_merge_commit_stale.py pins that.
    _validate_request_shape(source_sample_ids, strategy)
    _validate_samples_and_freshness(registry, source_sample_ids, individual_id)

    # Then hold both sources for the whole operation. The merge streams their
    # databases for its entire materialisation, so deletion must not remove one
    # underneath it (#2329); `delete_sample_with_cascade` refuses while this
    # lease is held. `_compute_merge_plan` re-runs the validation above inside
    # the lease, which closes the window between the pre-check and acquisition.
    with sample_merge_lease(
        registry.reference_engine, list(source_sample_ids), operation="Sample merge"
    ):
        return _merge_samples_locked(
            registry, source_sample_ids, individual_id, strategy, display_name
        )


def _merge_samples_locked(
    registry: DBRegistry,
    source_sample_ids: list[int],
    individual_id: int,
    strategy: MergeStrategy,
    display_name: str,
) -> int:
    """Body of :func:`merge_samples`, executed while both sources are reserved."""
    plan = _compute_merge_plan(registry, source_sample_ids, individual_id, strategy)
    s1_id, s2_id = source_sample_ids
    s1_row, s2_row = plan.s1_row, plan.s2_row
    merged_rows, summary = plan.rows, plan.summary
    settings = registry.settings
    reference_engine = registry.reference_engine

    now = datetime.now(UTC)
    merged_file_hash = _compute_file_hash(
        s1_row.file_hash or "",
        s2_row.file_hash or "",
        strategy,
    )

    # Build the child database under a task-owned name FIRST, and publish the
    # registry row only once it is complete (#2329). The previous ordering
    # committed the row up front so the path could be derived from the new id,
    # which left a window where the merged sample was discoverable but its
    # database was missing or half-written: a concurrent reader resolving that
    # path through ``get_sample_engine`` would create the file with the wrong
    # (rsid-PK) schema and destroy the merge, and a process death anywhere in
    # the window stranded a registered row pointing at nothing.
    #
    # The name carries a uuid rather than the sample id precisely because
    # SQLite reuses vacated rowids: an id-derived name cannot distinguish this
    # attempt's leftovers from a later sample that inherited the same id.
    publication_id = uuid.uuid4().hex
    new_db_path = f"samples/{_MERGED_DB_NAME_PREFIX}{publication_id}.db"
    sample_db_path = settings.data_dir / new_db_path
    try:
        sample_db_path.parent.mkdir(parents=True, exist_ok=True)
        # Build on throwaway engines only. Nothing may enter the registry's
        # engine cache before publication, or a later ``dispose_sample_engine``
        # keyed on the published path would miss this handle.
        bootstrap_engine = make_sqlite_engine(sample_db_path, wal=False)
        try:
            # Plan §10.4 (a): merged sample's raw_variants PK is (chrom, pos).
            create_sample_tables(bootstrap_engine, is_merged_sample=True)

            with bootstrap_engine.begin() as conn:
                conn.execute(
                    sample_metadata_table.insert().values(
                        id=1,
                        name=display_name,
                        file_format=_MERGED_FILE_FORMAT,
                        file_hash=merged_file_hash,
                        created_at=now,
                        updated_at=now,
                    )
                )
                conn.execute(
                    merge_provenance.insert().values(
                        id=1,
                        merged_at=now,
                        strategy=strategy.value,
                        source_sample_ids=json.dumps([s1_id, s2_id]),
                        source_file_hashes=json.dumps(
                            [s1_row.file_hash or "", s2_row.file_hash or ""]
                        ),
                        concordance_summary=json.dumps(summary.to_dict()),
                    )
                )

                if merged_rows:
                    payload = [
                        {
                            "rsid": r.rsid,
                            "chrom": r.chrom,
                            "pos": r.pos,
                            "genotype": r.genotype,
                            "source": r.source,
                            "concordance": r.concordance,
                            "discordant_alt_genotype": r.discordant_alt_genotype,
                            "alt_rsid": r.alt_rsid,
                        }
                        for r in merged_rows
                    ]
                    for i in range(0, len(payload), _INSERT_BATCH):
                        batch = payload[i : i + _INSERT_BATCH]
                        conn.execute(raw_variants.insert(), batch)
        finally:
            bootstrap_engine.dispose()

        # Publication: one transaction, after the database is complete. Before
        # this commit the merged sample does not exist to any reader; after it,
        # its database, provenance and variants are all already in place.
        with reference_engine.begin() as conn:
            result = conn.execute(
                samples.insert().values(
                    name=display_name,
                    db_path=new_db_path,
                    file_format=_MERGED_FILE_FORMAT,
                    file_hash=merged_file_hash,
                    individual_id=individual_id,
                    created_at=now,
                    updated_at=now,
                )
            )
            new_sample_id = int(result.inserted_primary_key[0])
            # SQLite can reuse a deleted rowid. Remove any prompt left by a
            # legacy occupant inside the publishing transaction.
            conn.execute(
                reannotation_prompts.delete().where(
                    reannotation_prompts.c.sample_id == new_sample_id
                )
            )
    except BaseException:
        # No registry row exists on this path, so there is nothing to roll back
        # but the file itself. ``BaseException`` so an interrupt still cleans up
        # — but an interrupt can also be delivered *after* the publishing commit
        # returns, while the transaction context is unwinding. Discarding then
        # would unlink a database the registry already points at, and startup
        # recovery would preserve the dangling row precisely because it is
        # referenced. Re-read the registry and keep anything already published.
        if not _is_published(registry, new_db_path):
            _discard_unpublished_merge(sample_db_path)
        raise

    # Plan §10.5 step 8: enqueue the standard annotation job. Imported lazily
    # to keep the service free of Huey at import time (tests can monkey-patch
    # via ``backend.tasks.huey_tasks``).
    try:
        from backend.tasks.huey_tasks import enqueue_annotation_job

        enqueue_annotation_job(new_sample_id)
    except Exception as exc:  # noqa: BLE001
        # A failure to enqueue is non-fatal at the merge boundary — the merged
        # sample exists and the user can retry annotation via the
        # POST /api/annotation/{sample_id} escape hatch. Log so the failure is
        # surfaced in the admin log explorer.
        logger.warning(
            "merge_annotation_enqueue_failed",
            extra={"merged_sample_id": new_sample_id, "error": str(exc)},
        )

    logger.info(
        "sample_merge_complete",
        extra={
            "merged_sample_id": new_sample_id,
            "source_sample_ids": [s1_id, s2_id],
            "strategy": strategy.value,
            "concordance_summary": summary.to_dict(),
            "merged_row_count": len(merged_rows),
        },
    )
    return new_sample_id

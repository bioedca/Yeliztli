"""Runs-of-Homozygosity (ROH) / FROH autozygosity estimate — roadmap #29.

A clean-room, PLINK-inspired sliding-run detector (no GPL code) that scans the
autosomal genotypes for long stretches of consecutive homozygous calls — the
signature of autozygosity (a segment inherited identical-by-descent from a
shared ancestor). The summed ROH length over the autosomal genome gives **FROH**,
a standard genomic estimate of autozygosity.

What this is *not* (the load-bearing honesty guardrail, §12): FROH is a
genome-wide *estimate* derived from one array, **not** a diagnosis or a statement
about whether a person's parents are related. Long ROH arise from many benign
causes — population history, genuine isolation, large pericentromeric LD blocks —
and a single chip cannot distinguish them. The finding states this plainly and
never names or infers a relationship.

Method (parameters documented and tuned for a dense ~600–700k-marker array):

  - Homozygosity is read straight from the genotype string (``"AA"`` hom,
    ``"AG"`` het, ``"--"``/haploid/indel → missing); ROH is strand-independent so
    no ref/alt is needed.
  - Per autosome, SNPs are walked in position order. A run extends across
    consecutive non-missing SNPs while (a) every local ``HET_WINDOW_SNPS``
    neighborhood contains at most ``HET_WINDOW_TOLERANCE`` heterozygous calls
    (genotyping-error slack proportional to run length) and (b) no gap between
    adjacent typed SNPs exceeds ``MAX_GAP_KB`` (so coverage gaps / centromeres
    break a run instead of being spanned).
  - A run is recorded as an ROH segment when, after trimming to homozygous
    endpoints, it spans ≥ ``MIN_ROH_KB`` and contains ≥ ``MIN_ROH_SNPS``
    homozygous SNPs.
  - ``FROH = Σ segment length / AUTOSOMAL_GENOME_KB`` (a fixed ~2.77 Gb
    denominator, the convention from McQuillan 2008, so FROH is comparable
    across samples rather than array-relative).
  - Before any of that, the sample must be *evaluable*: unless some autosome
    carries a gap-free block of ``MIN_ROH_SNPS`` typed markers spanning
    ``MIN_ROH_KB``, no genotype arrangement could produce a segment, so the
    empty result is withheld as indeterminate rather than reported as
    ``FROH = 0``. See ``_segment_eligible_region_exists`` for why a genome-wide
    marker total is not sufficient, and why this is a provable floor rather
    than a coverage-adequacy threshold.

This is a **route-triggered** metric (``POST /api/analysis/roh/run``), not part of
the auto-run :mod:`backend.analysis.run_all` pipeline: it is a full-genome scan
that always emits a summary finding, so running it on demand keeps the standard
post-annotation finding set (and its validation golden snapshot) unchanged.
"""

from __future__ import annotations

import json
import math
from collections import deque
from dataclasses import dataclass, field
from typing import Any

import sqlalchemy as sa
import structlog

from backend.analysis.zygosity import is_no_call
from backend.db.tables import findings, raw_variants

logger = structlog.get_logger(__name__)

MODULE = "roh"
CATEGORY = "autozygosity"

# ── Detection parameters (documented; tuned for dense consumer arrays) ───────
MIN_ROH_KB = 1500  # minimum segment span to count as an ROH (autozygosity focus)
MIN_ROH_SNPS = 100  # minimum homozygous SNPs in a segment (guards against sparse spans)
MAX_GAP_KB = 1000  # a gap > this between adjacent typed SNPs breaks a run
HET_WINDOW_SNPS = 50  # typed-SNP neighborhood used for local error tolerance
HET_WINDOW_TOLERANCE = 1  # heterozygous calls allowed per local neighborhood

# Weakest count-only floor: a segment needs >= MIN_ROH_SNPS homozygous calls,
# which are necessarily a subset of the sample's callable autosomal calls, so
# below this total no segment can exist. Fresh detection uses the stronger
# structural test in ``_segment_eligible_region_exists``; this constant remains
# because a *persisted* legacy row records only the marker count, and that is
# the strongest sound inference available from a count alone.
#
# Both are floors on what is *provably* uninformative, never claims that a
# larger count or region constitutes adequate coverage: a validated,
# platform-aware density/distribution gate is a separate, calibration-dependent
# question (issue #2220) and must not be inferred from either.
MIN_EVALUABLE_AUTOSOMAL_SNPS = MIN_ROH_SNPS

# ``RohResult.indeterminate_reason`` vocabulary.
INSUFFICIENT_AUTOSOMAL_MARKERS = "insufficient_autosomal_markers"
NO_SEGMENT_ELIGIBLE_REGION = "no_segment_eligible_region"
DETAIL_UNAVAILABLE = "detail_unavailable"

# A stored reason is honoured only if it is one of these: the narrative branches
# on the value, so an unrecognised one would silently take another cause's wording.
_KNOWN_INDETERMINATE_REASONS = frozenset(
    {INSUFFICIENT_AUTOSOMAL_MARKERS, NO_SEGMENT_ELIGIBLE_REGION, DETAIL_UNAVAILABLE}
)

# FROH denominator: the autosomal genome length (~2.77 Gb), McQuillan 2008
# convention, so FROH is comparable across samples rather than array-relative.
AUTOSOMAL_GENOME_KB = 2_770_000

_AUTOSOMES = frozenset(str(n) for n in range(1, 23))
_ACGT = frozenset("ACGT")

# Cap on how many segments we persist in detail_json (longest first), to bound row size.
_MAX_PERSISTED_SEGMENTS = 25

# Genotype-state vocabulary.
_HOM = "hom"
_HET = "het"
_MISS = "miss"


@dataclass(frozen=True)
class RohSegment:
    """One run of homozygosity."""

    chrom: str
    start: int
    end: int
    length_kb: float
    n_snps: int  # homozygous SNPs spanned


@dataclass
class RohResult:
    """The autozygosity assessment for one sample.

    ``froh`` is ``None`` — never ``0.0`` — when the sample could not be
    assessed, so an unmeasurable genome is never reported as a measured absence
    of autozygosity. ``indeterminate_reason`` is set exactly when ``froh`` is
    ``None``.
    """

    segments: list[RohSegment] = field(default_factory=list)
    froh: float | None = 0.0
    total_roh_kb: float = 0.0
    longest_kb: float = 0.0
    autosomal_snps_used: int = 0
    indeterminate_reason: str | None = None

    @property
    def evaluable(self) -> bool:
        return self.indeterminate_reason is None


def _genotype_state(genotype: str | None) -> str:
    """Classify a genotype string as homozygous / heterozygous / missing.

    Diploid two-character ACGT calls only contribute hom/het; everything else
    (no-call, haploid single base, indel I/D tokens) is missing — never a false
    homozygous call.
    """
    if is_no_call(genotype):
        return _MISS
    assert genotype is not None
    gt = genotype.strip().upper()
    if len(gt) != 2 or gt[0] not in _ACGT or gt[1] not in _ACGT:
        return _MISS
    return _HOM if gt[0] == gt[1] else _HET


def _read_autosomal_states(sample_engine: sa.Engine) -> dict[str, list[tuple[int, str]]]:
    """Return ``{chrom: [(pos, state), ...]}`` for autosomes, sorted by position.

    Missing SNPs are dropped from the sequence (they neither extend nor break a
    run on their own); coverage gaps are handled by the position-gap rule.
    """
    by_chrom: dict[str, list[tuple[int, str]]] = {}
    with sample_engine.connect() as conn:
        stmt = (
            sa.select(raw_variants.c.chrom, raw_variants.c.pos, raw_variants.c.genotype)
            .where(raw_variants.c.chrom.in_(_AUTOSOMES))
            .order_by(raw_variants.c.chrom, raw_variants.c.pos)
        )
        for chrom, pos, genotype in conn.execute(stmt):
            if pos is None:
                continue
            state = _genotype_state(genotype)
            if state == _MISS:
                continue
            by_chrom.setdefault(chrom, []).append((int(pos), state))
    return by_chrom


def _segment_eligible_region_exists(by_chrom: dict[str, list[tuple[int, str]]]) -> bool:
    """Could *any* chromosome yield a segment under *some* genotype arrangement?

    This reads marker positions only and never the calls, so it cannot suppress
    a genuine negative: it asks whether the scan is capable of reporting a run
    at all, not whether this person has one.

    A reported segment must lie within one chromosome, contain at least
    ``MIN_ROH_SNPS`` homozygous SNPs, span at least ``MIN_ROH_KB`` between its
    homozygous endpoints, and never cross a gap wider than ``MAX_GAP_KB``
    (``_scan_chromosome``). Every segment therefore lies inside a single
    gap-free block of typed markers, whose marker count and span bound the
    segment's own from above. When no block clears both thresholds, no genotype
    assignment whatsoever can produce a segment — so a genome-wide marker total
    is not sufficient: 60 markers on each of two autosomes, or 100 markers
    packed into 99 kb, clear any count-only floor while remaining incapable of
    ever yielding a run.
    """
    for snps in by_chrom.values():
        start = 0
        for i in range(1, len(snps) + 1):
            at_end = i == len(snps)
            if not at_end and snps[i][0] - snps[i - 1][0] <= MAX_GAP_KB * 1000:
                continue
            block = snps[start:i]
            if len(block) >= MIN_ROH_SNPS and (block[-1][0] - block[0][0]) / 1000.0 >= MIN_ROH_KB:
                return True
            start = i
    return False


def _scan_chromosome(chrom: str, snps: list[tuple[int, str]]) -> list[RohSegment]:
    """Detect non-overlapping ROH with a local heterozygote-density guard.

    The window rule is deliberately simpler than PLINK's full overlapping-window
    hit-rate algorithm: it rejects a candidate as soon as adding a heterozygous
    call would put more than ``HET_WINDOW_TOLERANCE`` such calls in the trailing
    ``HET_WINDOW_SNPS`` typed SNPs. Isolated errors can therefore accumulate
    across a long run, while a locally heterozygous stretch still splits it.
    """
    segments: list[RohSegment] = []
    n = len(snps)
    i = 0
    while i < n:
        # Extend a run from i while local het-density and gap rules hold.
        window_hets: deque[int] = deque()
        if snps[i][1] == _HET:
            window_hets.append(i)
        k = i + 1
        while k < n:
            if snps[k][0] - snps[k - 1][0] > MAX_GAP_KB * 1000:
                break

            window_start = k - HET_WINDOW_SNPS + 1
            while window_hets and window_hets[0] < window_start:
                window_hets.popleft()
            if snps[k][1] == _HET:
                if len(window_hets) >= HET_WINDOW_TOLERANCE:
                    break
                window_hets.append(k)
            k += 1
        # Run is snps[i .. k-1]; trim to homozygous endpoints.
        lo, hi = i, k - 1
        while lo <= hi and snps[lo][1] != _HOM:
            lo += 1
        while hi >= lo and snps[hi][1] != _HOM:
            hi -= 1
        if lo <= hi:
            start, end = snps[lo][0], snps[hi][0]
            length_kb = (end - start) / 1000.0
            n_hom = sum(1 for j in range(lo, hi + 1) if snps[j][1] == _HOM)
            if length_kb >= MIN_ROH_KB and n_hom >= MIN_ROH_SNPS:
                segments.append(RohSegment(chrom, start, end, round(length_kb, 1), n_hom))
        i = max(k, i + 1)
    return segments


def detect_roh(sample_engine: sa.Engine) -> RohResult:
    """Detect ROH segments and compute FROH for a sample."""
    by_chrom = _read_autosomal_states(sample_engine)
    autosomal_snps = sum(len(v) for v in by_chrom.values())

    # Evaluability is decided before any segment interpretation: if no region
    # could satisfy the segment rules, the scan below is structurally incapable
    # of emitting anything, so its empty result would be a foregone conclusion
    # rather than evidence that the genome carries no long homozygous runs.
    if not _segment_eligible_region_exists(by_chrom):
        logger.info(
            "roh_not_evaluable",
            autosomal_snps=autosomal_snps,
            min_roh_snps=MIN_ROH_SNPS,
            min_roh_kb=MIN_ROH_KB,
            reason=NO_SEGMENT_ELIGIBLE_REGION,
        )
        return RohResult(
            segments=[],
            froh=None,
            total_roh_kb=0.0,
            longest_kb=0.0,
            autosomal_snps_used=autosomal_snps,
            indeterminate_reason=NO_SEGMENT_ELIGIBLE_REGION,
        )

    segments: list[RohSegment] = []
    for chrom in sorted(by_chrom, key=lambda c: int(c)):
        segments.extend(_scan_chromosome(chrom, by_chrom[chrom]))

    total_kb = round(sum(s.length_kb for s in segments), 1)
    longest_kb = max((s.length_kb for s in segments), default=0.0)
    froh = round(total_kb / AUTOSOMAL_GENOME_KB, 5) if total_kb else 0.0

    logger.info(
        "roh_detected",
        segments=len(segments),
        total_roh_kb=total_kb,
        froh=froh,
        autosomal_snps=autosomal_snps,
    )
    return RohResult(
        segments=segments,
        froh=froh,
        total_roh_kb=total_kb,
        longest_kb=longest_kb,
        autosomal_snps_used=autosomal_snps,
    )


def unevaluable_text(autosomal_snps_used: int, reason: str) -> str:
    """Narrative for a sample whose autozygosity could not be assessed.

    Kept separate and public so the API can serve it for findings persisted
    before the evaluability gate existed, whose stored text asserts the very
    negative this gate withholds. The cause is stated explicitly because the
    two reasons are genuinely different: too few markers overall, versus enough
    markers that are nowhere arranged into a region a run could occupy.

    ``reason`` is deliberately required. It once defaulted, and a caller that
    resolved the correct reason then omitted it silently paired that reason
    with the other cause's wording — every call site must state which applies.
    """
    if reason == DETAIL_UNAVAILABLE:
        return (
            "Runs of homozygosity are not being reported: the stored result for "
            "this sample could not be read, so neither an FROH estimate nor the "
            "coverage behind it can be shown. Re-running the analysis will "
            "produce a current result. This says nothing about your genome "
            "either way."
        )
    if reason == INSUFFICIENT_AUTOSOMAL_MARKERS:
        cause = (
            f"this sample has {autosomal_snps_used} callable autosomal SNP(s), "
            f"fewer than the {MIN_ROH_SNPS} homozygous SNPs any single reported "
            f"run must itself contain"
        )
    else:
        cause = (
            f"no autosome carries {MIN_ROH_SNPS} typed markers spanning "
            f"{MIN_ROH_KB} kb without a coverage gap, which is the smallest "
            f"region a reported run could occupy ({autosomal_snps_used} "
            f"callable autosomal SNP(s) in total)"
        )
    return (
        f"Runs of homozygosity were not assessed: {cause}, so no run could have "
        f"been reported whatever the genome contains. No FROH estimate is given, "
        f"because none can be measured from this input. This reflects the "
        f"coverage of the data analysed, not a finding about your genome — it "
        f"does not indicate that long runs of homozygosity are absent."
    )


def _sample_coverage(sample_engine: sa.Engine) -> tuple[int, bool]:
    """Return ``(callable_autosomal_markers, could_emit_a_segment)`` for a sample.

    Lets a *legacy* row — which records a marker count at best — be judged by
    the same rule as a fresh scan, and lets a row that records no count at all
    have one read from the sample rather than assumed.

    Cost: a sample holds at most one ROH finding (``store_roh_findings``
    deletes then inserts on module+category), so this is bounded by the number
    of times a caller *evaluates*, not by row count. That is why a consumer
    needing both the narrative and the detail must go through
    ``normalize_legacy_row`` — calling the two single-purpose helpers evaluates
    twice and doubles this scan on exactly the dense samples it is written for.

    Measured at ~0.6–1.6 s for a 594k-marker sample on a workstation roughly
    2.4x slower than CI. Since *every* verdict is revalidated — including an
    explicit stored ``evaluable: true`` — that is paid once per read of a
    sample's ROH finding. Correctness over latency is deliberate here: a stored
    verdict honoured on its own say-so is how this module reported FROH = 0
    from markers that could never produce one (#2177). If the cost bites, the
    fix is a cheaper eligibility probe, not trusting the blob again.
    """
    by_chrom = _read_autosomal_states(sample_engine)
    return sum(len(v) for v in by_chrom.values()), _segment_eligible_region_exists(by_chrom)


def _in_range(value: Any, ceiling: float) -> bool:
    """Whether ``value`` is a number within ``[0, ceiling]``, by comparison only.

    Deliberately a pure comparison and never a conversion. ``math.isfinite`` was
    used here and raised ``OverflowError`` on a large stored integer such as
    ``10**400`` -- turning a validator whose whole purpose is to withhold into a
    500 on every user-visible read path, since neither the ROH route's except
    tuple nor the generic normalizers catch it. Python compares an int of any
    size against a float exactly and without converting, so a bounded comparison
    rejects that value, a NaN (all comparisons false) and an infinity alike, and
    cannot itself raise. A validator that can throw is a liability, not a guard.
    """
    return isinstance(value, int | float) and not isinstance(value, bool) and 0 <= value <= ceiling


def _is_measured_quantity(value: Any) -> bool:
    """Whether ``value`` is a length in kb a scan could actually have emitted.

    Bounded by the autosomal genome itself: no run, and no sum of runs, can span
    more than the genome they lie in. Type-checking alone vouched for
    `total_roh_kb: -1`, a measurement the detector cannot produce.
    """
    return _in_range(value, AUTOSOMAL_GENOME_KB)


def _reason_supported_by(count: int, reason: str, eligible: bool | None = None) -> str:
    """Downgrade an indeterminate reason the evidence cannot justify.

    Each stored reason makes a factual claim, and each is checked against the
    evidence that would refute it. `insufficient_autosomal_markers` claims the
    count is below the floor: honouring it beside a well-typed count at or above
    the floor made `unevaluable_text` state, verbatim, that 600000 callable SNPs
    are "fewer than the 100" required. `no_segment_eligible_region` claims no
    autosome carries a qualifying block: honouring it while the sample proves
    otherwise asserts a structural absence the sample refutes -- which happens
    whenever the sample is re-imported after the row was written.

    `eligible` is tri-state on purpose. ``None`` means the coverage scan was not
    run, and an unmeasured claim cannot be contradicted, so the reason stands;
    only an observed ``True`` refutes it. Downgrading on a value we never
    measured would be the same substitution this module exists to prevent.

    In both cases the verdict stands -- the row was withheld and stays withheld
    -- and only the explanation is replaced by one that claims nothing.
    """
    if reason == INSUFFICIENT_AUTOSOMAL_MARKERS and count >= MIN_EVALUABLE_AUTOSOMAL_SNPS:
        return DETAIL_UNAVAILABLE
    if reason == NO_SEGMENT_ELIGIBLE_REGION and eligible:
        return DETAIL_UNAVAILABLE
    return reason


# The shape `detect_roh` actually writes, stated once. Only these keys are
# served as measurements, so only these are checked; `froh` has its own bounded
# check in `evaluability_from_detail`.

# Base-pair ceiling for a coordinate or a marker tally, from the same genome
# bound as the kb metrics. Nothing on an autosome sits past it.
_AUTOSOMAL_GENOME_BP = AUTOSOMAL_GENOME_KB * 1000

# The writer stores every kb quantity as `round(x, 1)`. Comparisons therefore
# apply the same rounding and check for equality, rather than allowing a half-
# step tolerance: a half-step allowance is itself wrong at a rounding tie, where
# binary float makes the gap fractionally *larger* than half a step. A real
# 2_048_250 bp span rounds to 2048.2 while the unrounded value is 2048.25, and
# their difference evaluates to 0.0500000000001819 -- so a nominal 0.05 slack
# withheld a freshly written, entirely genuine result. This epsilon absorbs only
# float representation, never a rounding step.
_KB_EPSILON = 1e-6
_FROH_EPSILON = 1e-12


def _is_count(value: Any) -> bool:
    """A non-negative integer coordinate or tally, bounded by the genome."""
    return isinstance(value, int) and _in_range(value, _AUTOSOMAL_GENOME_BP)


def _run_count_can_cover_total(
    total_kb: float,
    *,
    minimum_count: int,
    maximum_count: int,
    maximum_length_kb: float,
) -> bool:
    """Whether some integer run count can sum to ``total_kb``.

    Each run contributes between ``MIN_ROH_KB`` and ``maximum_length_kb``.
    The feasible totals are therefore a union of intervals, one per integer
    count, rather than every value between the overall minimum and maximum.
    """
    if total_kb < -_KB_EPSILON or maximum_count < minimum_count:
        return False
    if abs(total_kb) <= _KB_EPSILON:
        return minimum_count == 0
    if maximum_length_kb < MIN_ROH_KB:
        return False
    least_for_total = math.ceil((total_kb - _KB_EPSILON) / maximum_length_kb)
    most_for_total = math.floor((total_kb + _KB_EPSILON) / MIN_ROH_KB)
    return max(minimum_count, least_for_total) <= min(maximum_count, most_for_total)


def _rounded_froh_has_feasible_total(
    froh: float,
    denominator_kb: float,
    *,
    fixed_total_kb: float,
    minimum_hidden: int,
    maximum_hidden: int,
    hidden_length_ceiling_kb: float,
) -> bool:
    """Whether a one-decimal writer total can round to ``froh``.

    A five-decimal FROH bin spans at most 27.7 kb under the largest accepted
    denominator, so checking every one-decimal total in that narrow bin is
    bounded (at most 278 candidates) and reproduces Python's exact rounding,
    including ties, instead of approximating the bin edges.
    """
    half_bin = 0.5 * 10**-5
    lower_kb = max(0.0, (froh - half_bin) * denominator_kb - _KB_EPSILON)
    upper_kb = min(
        AUTOSOMAL_GENOME_KB,
        (froh + half_bin) * denominator_kb + _KB_EPSILON,
    )
    first_tenth = math.ceil(lower_kb * 10)
    last_tenth = math.floor(upper_kb * 10)
    for tenth in range(first_tenth, last_tenth + 1):
        candidate_total = tenth / 10
        if abs(round(candidate_total / denominator_kb, 5) - froh) > _FROH_EPSILON:
            continue
        if _run_count_can_cover_total(
            candidate_total - fixed_total_kb,
            minimum_count=minimum_hidden,
            maximum_count=maximum_hidden,
            maximum_length_kb=hidden_length_ceiling_kb,
        ):
            return True
    return False


_SEGMENT_FIELD_CHECKS: dict[str, Any] = {
    "chrom": lambda v: isinstance(v, str) and v in _AUTOSOMES,
    "start": _is_count,
    "end": _is_count,
    "length_kb": _is_measured_quantity,
    "n_snps": _is_count,
}

_DETECTOR_PARAMS = (
    ("min_roh_kb", MIN_ROH_KB),
    ("min_roh_snps", MIN_ROH_SNPS),
    ("max_gap_kb", MAX_GAP_KB),
    ("het_window_snps", HET_WINDOW_SNPS),
    ("het_window_tolerance", HET_WINDOW_TOLERANCE),
)


def _detector_params_match(detail: dict[str, Any]) -> bool:
    """Whether every present segment-defining parameter matches this writer."""
    if "params" not in detail:
        return True
    params = detail["params"]
    if not isinstance(params, dict):
        return False
    for key, current in _DETECTOR_PARAMS:
        if key not in params:
            continue
        recorded = params[key]
        # A present parameter is writer provenance, not an optional hint. Keep
        # legacy rows that omit it readable, but require a present value to
        # match both the value and integer type this detector writes (``True``
        # otherwise compares equal to ``1`` in Python).
        if type(recorded) is not type(current) or recorded != current:
            return False
    return True


def _segments_are_disjoint(segments: list[dict[str, Any]]) -> bool:
    """Whether no stored interval is repeated or overlaps on one chromosome.

    ``_scan_chromosome`` advances beyond each emitted marker before looking for
    another run, but distinct rsIDs can occupy the same coordinate. Consecutive
    runs may therefore touch at an endpoint while covering no shared span.
    Summing a duplicate or true overlap as two independent runs would fabricate
    both segment count and total length.
    """
    last_end_by_chrom: dict[str, int] = {}
    for segment in sorted(segments, key=lambda item: (int(item["chrom"]), item["start"])):
        previous_end = last_end_by_chrom.get(segment["chrom"])
        if previous_end is not None and segment["start"] < previous_end:
            return False
        last_end_by_chrom[segment["chrom"]] = segment["end"]
    return True


def _segments_clear_emission_thresholds(segments: list[dict[str, Any]]) -> bool:
    """Whether every listed segment is one *this* detector could have emitted.

    ``_scan_chromosome`` records a run only once it spans ``MIN_ROH_KB`` and
    holds ``MIN_ROH_SNPS`` homozygous calls, and writes ``length_kb`` as
    ``round((end - start) / 1000, 1)``. A stored `length_kb: 0, n_snps: 0`
    segment is not a short run -- it is not a run at all -- and a length that
    disagrees with its own coordinates is not a measurement of them.

    Thresholds come from today's constants. ``_detector_params_match`` first
    rejects a row whose recorded parameters name different ones rather than
    honouring a second source of truth. A result computed by another algorithm
    version is not this algorithm's result, and ``detail_unavailable`` tells the
    reader to rerun the analysis under the current rules.
    """
    for segment in segments:
        if segment["length_kb"] <= 0 or segment["n_snps"] <= 0:
            return False
        if segment["length_kb"] < MIN_ROH_KB or segment["n_snps"] < MIN_ROH_SNPS:
            return False
        # The length is a measurement *of* the coordinates, so it has to agree
        # with them -- reproducing the writer's own `round(span_kb, 1)` rather
        # than allowing a half-step, which misfires at a rounding tie.
        span_kb = (segment["end"] - segment["start"]) / 1000
        if abs(segment["length_kb"] - round(span_kb, 1)) > _KB_EPSILON:
            return False
    return True


def _metrics_agree(
    detail: dict[str, Any],
    segments: list[dict[str, Any]] | None,
    callable_snps: int,
) -> bool:
    """Whether the stored metrics are consistent with each other.

    Per-field validity is not enough: a blob can hold nothing but well-typed,
    in-range values and still describe an impossible scan, and the APIs then
    serve the contradiction verbatim -- `n_segments: 1` beside `segments: []`
    and `segments_truncated: false` reports one segment and shows none.

    The relations checked are read off ``store_roh_findings``, which is the only
    writer, rather than accumulated one review round at a time. They are the ones
    true of *any* correct ROH result, not of one cap or schema version: the
    persisted list is the longest segments up to a cap, ``segments_truncated``
    records whether that cap bit, and total/longest are sums and maxima over the
    same set. Cap-specific relations (`len(segments) == the cap` when truncated)
    are deliberately excluded -- a row written under a different
    ``_MAX_PERSISTED_SEGMENTS`` is stale, not incoherent.

    ``froh`` is cross-checked only when the row records its own denominator.
    That reads the convention from the blob instead of guessing which version
    applied; a legacy row without it remains readable, while a row that does
    record it cannot report a fraction inconsistent with its own total.

    Only relations between fields that are *both present* are checked, so a
    legacy blob recording a subset stays evaluable.
    """
    n_segments = detail.get("n_segments")
    total = detail.get("total_roh_kb")
    longest = detail.get("longest_kb")
    froh = detail.get("froh")

    # Persisted precision is part of the writer contract, not presentation.
    # Accepting extra digits lets a well-typed blob assert a measurement the
    # detector never emitted and can also slip between rounded relation bounds.
    if froh is not None and abs(froh - round(froh, 5)) > _FROH_EPSILON:
        return False
    if any(
        value is not None and abs(value - round(value, 1)) > _KB_EPSILON
        for value in (total, longest)
    ):
        return False

    params = detail.get("params")
    denominator_recorded = False
    froh_denominator = AUTOSOMAL_GENOME_KB
    if "params" in detail and not isinstance(params, dict):
        return False
    if isinstance(params, dict) and "froh_denominator_kb" in params:
        denominator = params["froh_denominator_kb"]
        if not _is_measured_quantity(denominator) or denominator <= 0:
            return False
        denominator_recorded = True
        froh_denominator = denominator
        if (
            total is not None
            and froh is not None
            and abs(froh - round(total / denominator, 5)) > _FROH_EPSILON
        ):
            return False

    # Read as a boolean, never coerced. `bool("false")` is True, and this flag is
    # the one field that *excuses* a short segment list -- so a drifted string
    # here would wave the count/list check through and report a truncation that
    # never happened. The writer emits a real bool; anything else is unreadable.
    raw_truncated = detail.get("segments_truncated", False)
    if not isinstance(raw_truncated, bool):
        return False
    truncated = raw_truncated

    if n_segments is not None and segments is not None:
        # The list is capped, so it may be shorter than the count -- but only
        # when the blob says so. A true cap bit therefore requires a strict
        # shortfall; equality would claim truncation while naming every segment
        # and would incorrectly allow an unexplained excess in the stored total.
        if truncated:
            if len(segments) >= n_segments:
                return False
        elif len(segments) != n_segments:
            return False

    # Every summary describes the same set of emitted segments, so all present
    # summaries must agree on whether that set is empty. Missing fields remain a
    # valid legacy projection; contradictory zero/positive evidence does not.
    # A non-empty list and a true truncation bit prove that segments exist, while
    # a complete empty list proves that none do. This stays independent of the
    # optional count/list fields and of the current persistence cap.
    summaries = (detail.get("froh"), n_segments, total, longest)
    zero_evidence = any(value == 0 for value in summaries if value is not None)
    positive_evidence = any(value > 0 for value in summaries if value is not None)
    if segments:
        positive_evidence = True
    elif segments == [] and not truncated:
        zero_evidence = True
    if truncated:
        positive_evidence = True
    if zero_evidence and positive_evidence:
        return False

    listed_total: float | int | None = None
    minimum_from_list: float | int | None = None
    omitted_segments = 0
    known_longest = longest
    listed_markers = 0
    if segments is not None:
        listed_total = round(sum(segment["length_kb"] for segment in segments), 1)
        listed_markers = sum(segment["n_snps"] for segment in segments)
        if known_longest is None and segments:
            known_longest = max(segment["length_kb"] for segment in segments)
        if truncated:
            # A true cap bit proves at least one omitted run even if a legacy
            # projection dropped the count. With a count, its exact shortfall
            # names how many minimum-length runs remain hidden.
            omitted_segments = n_segments - len(segments) if n_segments is not None else 1
            minimum_from_list = listed_total + omitted_segments * MIN_ROH_KB
        else:
            # A non-truncated list is complete, including in a legacy
            # projection that omitted n_segments or the total itself.
            minimum_from_list = listed_total

        # Listed and omitted runs consume disjoint callable markers. Enforce the
        # exact listed usage plus the minimum for every omission before using
        # the remaining marker budget to derive any upper bound.
        if listed_markers + omitted_segments * MIN_ROH_SNPS > callable_snps:
            return False

    # The retained intervals are the longest ones. Their shortest length is
    # therefore an upper bound for every omitted run, while callable markers
    # bound how many runs can exist when a legacy projection dropped the exact
    # count. This yields a total-length ceiling without relying on today's
    # persistence cap.
    maximum_from_shape: float | int | None = None
    if segments is not None:
        if not truncated:
            maximum_from_shape = listed_total
        else:
            maximum_hidden = (
                n_segments - len(segments)
                if n_segments is not None
                else (callable_snps - listed_markers) // MIN_ROH_SNPS
            )
            hidden_length_ceiling = min(
                (segment["length_kb"] for segment in segments),
                default=known_longest,
            )
            if hidden_length_ceiling is not None:
                maximum_from_shape = listed_total + maximum_hidden * hidden_length_ceiling
    elif known_longest is not None:
        maximum_segments = n_segments if n_segments is not None else callable_snps // MIN_ROH_SNPS
        maximum_from_shape = maximum_segments * known_longest

    # One descriptor covers exact and omitted segment counts for both a stored
    # total and the narrow total bin implied by a recorded-denominator FROH.
    # It states the fixed retained length, allowed hidden-count range, and the
    # largest any hidden run may be.
    run_count_shape: tuple[float | int, int, int, float | int]
    if segments is not None and not truncated:
        run_count_shape = (
            listed_total,
            0,
            0,
            known_longest or AUTOSOMAL_GENOME_KB,
        )
    elif segments:
        if n_segments is not None:
            minimum_hidden = maximum_hidden = n_segments - len(segments)
        else:
            minimum_hidden = 1
            maximum_hidden = (callable_snps - listed_markers) // MIN_ROH_SNPS
        run_count_shape = (
            listed_total,
            minimum_hidden,
            maximum_hidden,
            min(segment["length_kb"] for segment in segments),
        )
    elif known_longest is not None and known_longest > 0:
        if n_segments is not None:
            minimum_hidden = maximum_hidden = n_segments - 1
        else:
            minimum_hidden = 0
            maximum_hidden = callable_snps // MIN_ROH_SNPS - 1
        run_count_shape = (
            known_longest,
            minimum_hidden,
            maximum_hidden,
            known_longest,
        )
    else:
        if n_segments is not None:
            minimum_hidden = maximum_hidden = n_segments
        else:
            minimum_hidden = 1 if truncated else 0
            maximum_hidden = callable_snps // MIN_ROH_SNPS
        run_count_shape = (
            0,
            minimum_hidden,
            maximum_hidden,
            AUTOSOMAL_GENOME_KB,
        )

    # Even a summary-only legacy projection must describe segments this writer
    # could have emitted. Every positive run is at least MIN_ROH_KB long and
    # contains MIN_ROH_SNPS callable markers, so a positive summary below those
    # floors cannot be rescued merely by omitting the interval list. A present
    # positive count/total/longest strengthens the minimum total length implied
    # by that projection, and therefore its minimum possible rounded FROH. The
    # current denominator is conservative when provenance is absent; a smaller
    # validated recorded denominator supplies the stronger factual bound.
    if froh is not None and froh > 0:
        implied_total = MIN_ROH_KB
        if n_segments is not None and n_segments > 0:
            implied_total = max(implied_total, n_segments * MIN_ROH_KB)
        if total is not None and total > 0:
            implied_total = max(implied_total, total)
        if longest is not None and longest > 0:
            implied_total = max(implied_total, longest)
        if minimum_from_list is not None:
            implied_total = max(implied_total, minimum_from_list)

        if n_segments is not None and n_segments > 0 and known_longest is not None:
            implied_total = max(
                implied_total,
                known_longest + (n_segments - 1) * MIN_ROH_KB,
            )
        minimum_froh = round(implied_total / froh_denominator, 5)
        if froh < minimum_froh - _FROH_EPSILON:
            return False

        # An upper FROH bound needs a known denominator; absent legacy
        # provenance could have used any smaller historical denominator. A
        # recorded denominator plus the retained count/list/coverage shape
        # supplies a safe upper bound.
        maximum_total = total
        if maximum_from_shape is not None:
            maximum_total = (
                maximum_from_shape
                if maximum_total is None
                else min(maximum_total, maximum_from_shape)
            )
        if denominator_recorded and maximum_total is not None:
            maximum_froh = round(maximum_total / froh_denominator, 5)
            if froh > maximum_froh + _FROH_EPSILON:
                return False
        if denominator_recorded and total is None:
            fixed_total, minimum_hidden, maximum_hidden, hidden_ceiling = run_count_shape
            if not _rounded_froh_has_feasible_total(
                froh,
                froh_denominator,
                fixed_total_kb=fixed_total,
                minimum_hidden=minimum_hidden,
                maximum_hidden=maximum_hidden,
                hidden_length_ceiling_kb=hidden_ceiling,
            ):
                return False
    if total is not None and 0 < total < MIN_ROH_KB:
        return False
    if longest is not None and 0 < longest < MIN_ROH_KB:
        return False
    if (
        total is not None
        and maximum_from_shape is not None
        and total > maximum_from_shape + _KB_EPSILON
    ):
        return False
    if n_segments is not None and n_segments > 0:
        # Emitted runs are disjoint in marker-index space, so their minimum
        # marker requirements cannot collectively exceed callable coverage.
        if n_segments * MIN_ROH_SNPS > callable_snps:
            return False
        if total is not None and total < n_segments * MIN_ROH_KB - _KB_EPSILON:
            return False
        if total is not None and longest is not None:
            # The longest is an upper bound for every segment; the remaining
            # n-1 segments each contribute at least the emission floor.
            if total > n_segments * longest + _KB_EPSILON:
                return False
            minimum_total = longest + (n_segments - 1) * MIN_ROH_KB
            if total < minimum_total - _KB_EPSILON:
                return False

    # The longest segment is one of those summed, so it cannot exceed the total.
    if total is not None and longest is not None and longest > total:
        return False

    # With an omitted segment count, the broad minimum/maximum bounds above are
    # not sufficient: feasible totals form one interval per *integer* run
    # count, and those intervals can have gaps. Treat one known longest run (or
    # every retained top-k run) as fixed, then ask whether any permitted number
    # of remaining runs can cover the residual total.
    if total is not None:
        fixed_total, minimum_hidden, maximum_hidden, hidden_ceiling = run_count_shape
        if not _run_count_can_cover_total(
            total - fixed_total,
            minimum_count=minimum_hidden,
            maximum_count=maximum_hidden,
            maximum_length_kb=hidden_ceiling,
        ):
            return False

    # ...and because persistence keeps the longest entries first, the recorded
    # longest must equal the maximum listed value even when the list is capped.
    if longest is not None and segments:
        if abs(longest - max(segment["length_kb"] for segment in segments)) > _KB_EPSILON:
            return False

    if segments is not None:
        # Each segment's homozygous markers are drawn from the callable sample,
        # and emitted runs are disjoint. Neither one segment nor their sum can
        # therefore exceed the callable coverage used to vouch for this row.
        # The aggregate (including omissions) was checked above while deriving
        # shape bounds; retain the direct per-segment guard and disjointness
        # check here beside the other interval relations.
        if any(segment["n_snps"] > callable_snps for segment in segments):
            return False
        if not _segments_are_disjoint(segments):
            return False

    # The total is a sum over every segment, so it must cover the ones listed --
    # exactly when the list is complete, and with room to spare when the cap bit.
    # Without this, a blob listing two 6200 kb runs could record a 6200 kb total
    # and be served as a total smaller than its own segments.
    if total is not None and listed_total is not None:
        # Same rule as the segment lengths: apply the writer's own rounding and
        # compare, since the writer stores `round(sum(lengths), 1)`. When the
        # list is complete that sum is over exactly these segments, so equality
        # holds; when the cap bit, the unlisted segments counted too, so the
        # total may only be larger. A half-step tolerance would misfire here for
        # the same tie reason -- `round(4096.45, 1)` is 4096.4 and the raw gap
        # evaluates to 0.0500000000001819.
        minimum_list_total = minimum_from_list if truncated else listed_total
        if total < minimum_list_total - _KB_EPSILON:
            return False
        if not truncated and total > listed_total + _KB_EPSILON:
            return False

    return True


def _companion_metrics_readable(detail: dict[str, Any], callable_snps: int) -> bool:
    """Whether every companion metric a consumer will serve is actually readable.

    Vouching on ``froh`` alone let the two read paths disagree about the same
    row. The dedicated ROH route materialises ``segments`` into response models
    inside a ``try``, so a blob holding ``"segments": null`` raised there and
    was reported ``detail_unavailable`` — while the generic findings API and the
    report generator, which only consult the verdict, kept serving the stored
    "typical result" narrative for that very row. A row is vouched for on every
    path or none.

    Absent is not malformed: a pre-gate blob may record only ``froh``, and the
    consumers already serve the rest as ``null`` in that case. This rejects
    values that are *present and not what a scan writes* — the same rule already
    applied to ``froh``, ``autosomal_snps_used`` and ``evaluable``.
    """
    # FROH and totals are products of the segmentation rules even when a
    # legacy/subset payload omits the segment list itself. Validate recorded
    # detector provenance before taking that optional-list branch.
    if not _detector_params_match(detail):
        return False
    for key in ("total_roh_kb", "longest_kb"):
        value = detail.get(key)
        if value is not None and not _is_measured_quantity(value):
            return False
    n_segments = detail.get("n_segments")
    if n_segments is not None and not _is_count(n_segments):
        return False
    # Absent and explicitly-null differ here, so membership is tested rather
    # than `.get(...) is None`. The consumers read `detail.get("segments", [])`,
    # whose default applies only when the key is missing: a stored
    # `"segments": null` returns None and is then iterated, which is the exact
    # blob that raised in the ROH route. `total_roh_kb` and friends are
    # `float | None` in the response, so an explicit null is legitimate there.
    if "segments" not in detail:
        return _metrics_agree(detail, None, callable_snps)
    segments = detail["segments"]
    if not isinstance(segments, list):
        return False
    if not all(
        isinstance(segment, dict)
        and all(check(segment.get(field)) for field, check in _SEGMENT_FIELD_CHECKS.items())
        and segment["end"] >= segment["start"]
        for segment in segments
    ):
        return False
    if not _segments_clear_emission_thresholds(segments):
        return False
    return _metrics_agree(detail, segments, callable_snps)


def evaluability_from_detail(
    detail: dict[str, Any] | None, sample_engine: sa.Engine | None = None
) -> tuple[bool, int, str | None]:
    """Return ``(evaluable, autosomal_snps_used, indeterminate_reason)`` for a stored row.

    Findings persisted before the evaluability gate carry no ``evaluable`` key.
    Given ``sample_engine`` the structural rule is recomputed from the sample's
    own markers, so a legacy row is judged exactly as a fresh scan would be;
    without it, only the recorded count is available and the weaker count floor
    applies — sound, but blind to markers that clear the count while sitting in
    no region a run could occupy.

    Missing or malformed detail is indeterminate, never evaluable: a row whose
    state cannot be read is not a row that can be vouched for.

    Every read path shares this one implementation: the rule must not be
    reimplemented per consumer, or the paths drift apart and a sample reads as
    indeterminate in one place and "typical" in another.
    """
    if not isinstance(detail, dict):
        return False, 0, DETAIL_UNAVAILABLE

    # An absent count is not a count of zero. Defaulting it would manufacture
    # the claim "0 callable autosomal SNP(s)" for a blob that simply never
    # recorded coverage — asserting a measurement nothing performed.
    count_present = "autosomal_snps_used" in detail
    raw_used = detail.get("autosomal_snps_used")
    counted = _is_count(raw_used)
    snps_used = raw_used if counted else 0

    # Missing coverage is a supported legacy projection and can be recomputed.
    # A present malformed value is different: it proves the persisted result is
    # not a shape this writer emitted. Never let today's sample scan "rescue"
    # that stale/corrupt row, but report the observed count when one is available
    # so the dedicated and generic read paths expose the same safe metadata.
    if count_present and not counted:
        if sample_engine is not None:
            observed, _eligible = _sample_coverage(sample_engine)
            return False, observed, DETAIL_UNAVAILABLE
        return False, 0, DETAIL_UNAVAILABLE

    raw_froh = detail.get("froh")
    # FROH is the fraction of the autosomal genome in long homozygous runs, so
    # it is bounded by [0, 1] by construction. Type-checking it without bounding
    # it would vouch for a number no scan could have produced: a drifted blob
    # holding -3 or 12 would pass every gate below, reach the evaluable exit,
    # and be served as a measured FROH. That is the same substitution this
    # function exists to prevent, one field over — and it is how every other
    # field here is already treated (`autosomal_snps_used` must be >= 0,
    # `evaluable` a real bool, `indeterminate_reason` a known reason).
    # A NaN also fails this comparison, which is the correct outcome.
    has_metric = (
        isinstance(raw_froh, int | float)
        and not isinstance(raw_froh, bool)
        and 0.0 <= raw_froh <= 1.0
    )

    observed = snps_used
    stored = detail.get("evaluable")
    if "evaluable" in detail:
        # Must be an actual boolean. A schema-drifted `"evaluable": "false"` is
        # a truthy string, while an explicit null is not an absent legacy
        # verdict. Reading either as usable would vouch for a row on the
        # strength of a value this writer cannot emit.
        if not isinstance(stored, bool):
            return False, snps_used, DETAIL_UNAVAILABLE
        if not stored:
            # Only a known reason is honoured. ``unevaluable_text`` branches on
            # this value, so an unrecognised one would fall through to the
            # marker-region wording and state a cause the data never supported.
            reason = detail.get("indeterminate_reason")
            # Type-check before membership: JSON arrays and objects are valid
            # stored values but unhashable, so asking whether either is in the
            # reason set would raise instead of withholding the drifted row.
            if not isinstance(reason, str) or reason not in _KNOWN_INDETERMINATE_REASONS:
                return False, snps_used, DETAIL_UNAVAILABLE
            # The coverage-dependent reasons quote the marker count in their
            # narrative, so honouring one without a recorded count would assert
            # "0 callable autosomal SNP(s)" for a sample nobody counted. Read
            # the sample, or fall back to a reason that claims nothing.
            #
            # Every exit routes the resolved reason through
            # `_reason_supported_by`, so a stored reason is honoured only if the
            # evidence it will be narrated with can actually justify it. The
            # sample is read when the reason makes a claim only the sample can
            # refute: `no_segment_eligible_region` asserts a structural absence,
            # which a re-imported sample can disprove, and the count alone
            # cannot. `insufficient_autosomal_markers` needs no scan -- its
            # claim is about the count, which is already in hand.
            if reason != DETAIL_UNAVAILABLE and not counted:
                if sample_engine is None:
                    return False, 0, DETAIL_UNAVAILABLE
                observed, eligible = _sample_coverage(sample_engine)
                return False, observed, _reason_supported_by(observed, str(reason), eligible)
            if reason == NO_SEGMENT_ELIGIBLE_REGION and sample_engine is not None:
                observed, eligible = _sample_coverage(sample_engine)
                return False, observed, _reason_supported_by(observed, str(reason), eligible)
            return False, snps_used, _reason_supported_by(snps_used, str(reason))
        # An explicit `true` is not self-certifying either: it falls through to
        # the same coverage gates below. Our own writer computes the verdict
        # structurally, so a fresh row re-passes them — but a row whose sample
        # changed underneath it, or that some other writer produced, would
        # otherwise have its stored zero honoured on its own say-so.
    # A reason belongs only to an explicit false verdict: the writer sets it
    # exactly when FROH is unavailable. This also covers legacy rows that omit
    # `evaluable`; otherwise their orphaned reason survived the generic mapper
    # while the dedicated mapper silently blanked it. With a live sample, use
    # the observed count in both corrected representations.
    if detail.get("indeterminate_reason") is not None:
        if sample_engine is not None:
            observed, _eligible = _sample_coverage(sample_engine)
            return False, observed, DETAIL_UNAVAILABLE
        return False, snps_used, DETAIL_UNAVAILABLE
    if not counted:
        # Legacy row recording no coverage at all: read the sample or say the
        # state is unavailable — never split the difference.
        if sample_engine is None:
            return False, 0, DETAIL_UNAVAILABLE
        observed, eligible = _sample_coverage(sample_engine)
        if not eligible:
            return False, observed, NO_SEGMENT_ELIGIBLE_REGION
    elif sample_engine is not None:
        # `_sample_coverage` was already being called here, but only its
        # eligibility flag was kept and the stored count was served as though it
        # were an observation. A drifted blob claiming 600000 markers over a
        # 100-marker sample was therefore reported as 600000 callable markers on
        # every path. Both values are used now, at no extra scan cost.
        #
        # The observed count gates and is reported; the stored count still has
        # to clear the floor on its own. Letting the sample's markers *rescue* a
        # row that records too few would vouch for a stored FROH computed from a
        # marker set that is not this sample's — reinstating #2177 from the
        # other side. So each floor withholds independently, and the count each
        # one quotes is the count that explains it: a narrative reading
        # "insufficient markers" beside the 361 the sample does hold would state
        # a cause its own number contradicts.
        observed, eligible = _sample_coverage(sample_engine)
        if observed < MIN_EVALUABLE_AUTOSOMAL_SNPS:
            return False, observed, INSUFFICIENT_AUTOSOMAL_MARKERS
        if snps_used < MIN_EVALUABLE_AUTOSOMAL_SNPS:
            return False, snps_used, INSUFFICIENT_AUTOSOMAL_MARKERS
        if not eligible:
            return False, observed, NO_SEGMENT_ELIGIBLE_REGION
        # Once both snapshots clear the structural floor they still have to be
        # the same snapshot. Otherwise the stored FROH/segments were measured
        # against a different marker set and cannot be combined with today's
        # count. Marker identity is not persisted, so exact count equality is
        # necessary rather than sufficient; a mismatch is definitive evidence
        # to withhold until the analysis is rerun.
        if snps_used != observed:
            return False, observed, DETAIL_UNAVAILABLE
    elif snps_used < MIN_EVALUABLE_AUTOSOMAL_SNPS:
        return False, snps_used, INSUFFICIENT_AUTOSOMAL_MARKERS

    # The only exit that vouches for a row, so the invariant is stated once: a
    # verdict is usable only when a result accompanies it. Neither an explicit
    # stored `evaluable: true` nor a re-read of the sample's coverage can
    # reconstruct a measurement that was never recorded — establishing that a
    # scan *could* have run is not the same as having its result.
    # With a live sample, a recorded count mismatch has already been withheld.
    # Without one, the recorded count is the only available bound. For a legacy
    # row with no recorded count, the current observation supplies that bound.
    callable_snps = min(snps_used, observed) if counted else observed
    if not has_metric or not _companion_metrics_readable(detail, callable_snps):
        return False, observed, DETAIL_UNAVAILABLE
    return True, observed, None


def normalize_legacy_finding_text(
    module: str | None,
    category: str | None,
    finding_text: str | None,
    detail: dict[str, Any] | None,
    sample_engine: sa.Engine | None = None,
) -> str | None:
    """Correct a stored ROH narrative that predates the evaluability gate.

    The unified findings API and the report generator render the persisted
    ``finding_text`` verbatim, so without this a pre-gate row keeps presenting
    "No long runs of homozygosity were detected ... This is the typical result"
    for a sample the scan could never have assessed. Rows from other modules,
    and evaluable ROH rows, pass through untouched. Pass ``sample_engine``
    wherever it is available so legacy rows get the full structural rule.
    """
    if module != MODULE or category != CATEGORY:
        return finding_text
    evaluable, snps_used, reason = evaluability_from_detail(detail, sample_engine)
    if evaluable:
        return finding_text
    return unevaluable_text(snps_used, reason or INSUFFICIENT_AUTOSOMAL_MARKERS)


def _withheld_detail(
    detail: dict[str, Any] | None, snps_used: int, reason: str | None
) -> dict[str, Any] | None:
    """Blank every measured quantity in a stored ROH ``detail`` blob.

    Correcting only the narrative would hand clients a withheld conclusion
    alongside the exact ``froh: 0.0`` it withholds — one payload asserting both.
    Returns a copy so the caller's parsed blob is not mutated.
    """
    if detail is None:
        return None
    # Valid JSON that is not an object (`[]`, `5`, `"text"`) is unreadable, not
    # absent, so it cannot be handed back verbatim: `FindingResponse.detail` is
    # `dict | None`, and returning a list/int/str made `/api/analysis/findings`
    # raise a Pydantic error and 500 on exactly the blobs the dedicated ROH
    # route already withholds — it normalises at its parse site (`roh.py`). The
    # fix belongs here rather than at a second parse site so every consumer of
    # the shared rule agrees; withheld state is built from empty rather than
    # from the unreadable value.
    corrected = dict(detail) if isinstance(detail, dict) else {}
    # Every measured quantity is withheld, not just FROH: "0 kb total, 0
    # segments" beside a withheld FROH is the same measured absence in another
    # field. The marker count stays because it is observed, not derived.
    for key in ("froh", "total_roh_kb", "longest_kb", "n_segments"):
        corrected[key] = None
    # The segment list is the strongest assertion in the blob — concrete
    # chromosomes and coordinates — so it goes too. Withholding the summary
    # while shipping the detail states the finding in another form.
    corrected["segments"] = []
    corrected["segments_truncated"] = False
    corrected["evaluable"] = False
    corrected["indeterminate_reason"] = reason or INSUFFICIENT_AUTOSOMAL_MARKERS
    corrected["autosomal_snps_used"] = snps_used
    return corrected


def normalize_legacy_row(
    module: str | None,
    category: str | None,
    finding_text: str | None,
    detail: dict[str, Any] | None,
    sample_engine: sa.Engine | None = None,
) -> tuple[str | None, dict[str, Any] | None]:
    """Correct narrative and detail together, from a single evaluability read.

    A consumer needing both must not call the two single-purpose helpers: each
    evaluates independently, and for a legacy row that means two full autosomal
    scans per request on exactly the dense samples this module targets. Doing
    both here makes the double scan unexpressible rather than merely avoided.
    """
    if module != MODULE or category != CATEGORY:
        return finding_text, detail
    evaluable, snps_used, reason = evaluability_from_detail(detail, sample_engine)
    if evaluable:
        return finding_text, detail
    return (
        unevaluable_text(snps_used, reason or INSUFFICIENT_AUTOSOMAL_MARKERS),
        _withheld_detail(detail, snps_used, reason),
    )


def _finding_text(result: RohResult) -> str:
    if result.indeterminate_reason is not None:
        return unevaluable_text(result.autosomal_snps_used, result.indeterminate_reason)
    if not result.segments:
        return (
            "No long runs of homozygosity were detected (FROH ≈ 0). This is the "
            "typical result and is reported here only as a genomic-ancestry metric. "
            "FROH is a genome-wide estimate of autozygosity — it is not a diagnosis "
            "and says nothing about whether your parents are related."
        )
    return (
        f"Runs of homozygosity: {len(result.segments)} autosomal segment(s) totalling "
        f"{result.total_roh_kb:.0f} kb (longest {result.longest_kb:.0f} kb), giving an "
        f"FROH autozygosity estimate of {result.froh:.4f}. FROH is a genome-wide "
        f"*estimate* of the fraction of the genome in long homozygous runs — a "
        f"population-genetics metric, not a diagnosis. Long runs have many benign "
        f"causes (population history, genuine ancestral isolation, large low-"
        f"recombination blocks); this result is not a statement about whether your "
        f"parents are related."
    )


def store_roh_findings(result: RohResult, sample_engine: sa.Engine) -> int:
    """Persist a single ROH summary finding (idempotent)."""
    longest = sorted(result.segments, key=lambda s: s.length_kb, reverse=True)
    # An unevaluable scan measured nothing, so no derived quantity is asserted:
    # persisting 0 kb / 0 segments beside a withheld FROH would state the same
    # absence in another field. The observed marker count is retained for audit.
    measured = result.evaluable
    detail: dict[str, Any] = {
        "froh": result.froh,
        "total_roh_kb": result.total_roh_kb if measured else None,
        "longest_kb": result.longest_kb if measured else None,
        "n_segments": len(result.segments) if measured else None,
        "autosomal_snps_used": result.autosomal_snps_used,
        "evaluable": result.evaluable,
        "indeterminate_reason": result.indeterminate_reason,
        "params": {
            "min_roh_kb": MIN_ROH_KB,
            "min_roh_snps": MIN_ROH_SNPS,
            "max_gap_kb": MAX_GAP_KB,
            "het_window_snps": HET_WINDOW_SNPS,
            "het_window_tolerance": HET_WINDOW_TOLERANCE,
            "froh_denominator_kb": AUTOSOMAL_GENOME_KB,
            "min_evaluable_autosomal_snps": MIN_EVALUABLE_AUTOSOMAL_SNPS,
        },
        "segments": [
            {
                "chrom": s.chrom,
                "start": s.start,
                "end": s.end,
                "length_kb": s.length_kb,
                "n_snps": s.n_snps,
            }
            for s in longest[:_MAX_PERSISTED_SEGMENTS]
        ],
        "segments_truncated": len(result.segments) > _MAX_PERSISTED_SEGMENTS,
    }

    row = {
        "module": MODULE,
        "category": CATEGORY,
        "evidence_level": 1,  # a genomic metric, never a clinical high-confidence finding
        "finding_text": _finding_text(result),
        "conditions": "Autozygosity (FROH) estimate",
        "clinvar_significance": None,
        "detail_json": json.dumps(detail),
    }

    with sample_engine.begin() as conn:
        conn.execute(
            sa.delete(findings).where(findings.c.module == MODULE, findings.c.category == CATEGORY)
        )
        conn.execute(sa.insert(findings), [row])
    return 1

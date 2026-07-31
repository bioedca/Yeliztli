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
    raw_used = detail.get("autosomal_snps_used")
    counted = isinstance(raw_used, int) and not isinstance(raw_used, bool) and raw_used >= 0
    snps_used = raw_used if counted else 0

    raw_froh = detail.get("froh")
    has_metric = isinstance(raw_froh, int | float) and not isinstance(raw_froh, bool)

    observed = snps_used
    stored = detail.get("evaluable")
    if stored is not None:
        # Must be an actual boolean. A schema-drifted `"evaluable": "false"` is
        # a truthy string, and reading it as a true verdict would skip both the
        # count floor and the structural check — vouching for a row on the
        # strength of a value that says the opposite.
        if not isinstance(stored, bool):
            return False, snps_used, DETAIL_UNAVAILABLE
        if not stored:
            # Only a known reason is honoured. ``unevaluable_text`` branches on
            # this value, so an unrecognised one would fall through to the
            # marker-region wording and state a cause the data never supported.
            reason = detail.get("indeterminate_reason")
            if reason not in _KNOWN_INDETERMINATE_REASONS:
                return False, snps_used, DETAIL_UNAVAILABLE
            # The coverage-dependent reasons quote the marker count in their
            # narrative, so honouring one without a recorded count would assert
            # "0 callable autosomal SNP(s)" for a sample nobody counted. Read
            # the sample, or fall back to a reason that claims nothing.
            if reason != DETAIL_UNAVAILABLE and not counted:
                if sample_engine is None:
                    return False, 0, DETAIL_UNAVAILABLE
                observed, _eligible = _sample_coverage(sample_engine)
                return False, observed, str(reason)
            return False, snps_used, str(reason)
        # An explicit `true` is not self-certifying either: it falls through to
        # the same coverage gates below. Our own writer computes the verdict
        # structurally, so a fresh row re-passes them — but a row whose sample
        # changed underneath it, or that some other writer produced, would
        # otherwise have its stored zero honoured on its own say-so.
    if not counted:
        # Legacy row recording no coverage at all: read the sample or say the
        # state is unavailable — never split the difference.
        if sample_engine is None:
            return False, 0, DETAIL_UNAVAILABLE
        observed, eligible = _sample_coverage(sample_engine)
        if not eligible:
            return False, observed, NO_SEGMENT_ELIGIBLE_REGION
    else:
        if snps_used < MIN_EVALUABLE_AUTOSOMAL_SNPS:
            return False, snps_used, INSUFFICIENT_AUTOSOMAL_MARKERS
        if sample_engine is not None and not _sample_coverage(sample_engine)[1]:
            return False, snps_used, NO_SEGMENT_ELIGIBLE_REGION

    # The only exit that vouches for a row, so the invariant is stated once: a
    # verdict is usable only when a result accompanies it. Neither an explicit
    # stored `evaluable: true` nor a re-read of the sample's coverage can
    # reconstruct a measurement that was never recorded — establishing that a
    # scan *could* have run is not the same as having its result.
    if not has_metric:
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
    if not isinstance(detail, dict):
        return detail
    corrected = dict(detail)
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

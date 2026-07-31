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
  - Before any of that, the sample must be *evaluable*: a scan over fewer than
    ``MIN_EVALUABLE_AUTOSOMAL_SNPS`` callable autosomal SNPs cannot emit a
    segment under any genome, so its empty result is withheld as indeterminate
    instead of being reported as ``FROH = 0``. See that constant for why this
    is a provable floor rather than a coverage-adequacy threshold.

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

# Below this many *callable* autosomal SNPs the scan cannot emit a segment at
# all: every reported segment must contain >= MIN_ROH_SNPS homozygous calls
# (see ``_scan_chromosome``), and a segment's homozygous calls are necessarily a
# subset of the sample's callable autosomal calls. "No ROH found" is therefore
# forced by construction for such an input and says nothing about the genome —
# a zero here is an artifact of the detector, not an observation.
#
# This is deliberately only a floor on what is *provably* uninformative. It is
# NOT a claim that any larger count constitutes adequate coverage: a validated,
# platform-aware density/distribution gate is a separate, calibration-dependent
# question (issue #2220) and must not be inferred from this constant.
MIN_EVALUABLE_AUTOSOMAL_SNPS = MIN_ROH_SNPS

# ``RohResult.indeterminate_reason`` vocabulary.
INSUFFICIENT_AUTOSOMAL_MARKERS = "insufficient_autosomal_markers"

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

    # Evaluability is decided before any segment interpretation: with too few
    # callable autosomal markers the scan below is structurally incapable of
    # emitting a segment, so its empty result would be a foregone conclusion
    # rather than evidence that the genome carries no long homozygous runs.
    if autosomal_snps < MIN_EVALUABLE_AUTOSOMAL_SNPS:
        logger.info(
            "roh_not_evaluable",
            autosomal_snps=autosomal_snps,
            required=MIN_EVALUABLE_AUTOSOMAL_SNPS,
            reason=INSUFFICIENT_AUTOSOMAL_MARKERS,
        )
        return RohResult(
            segments=[],
            froh=None,
            total_roh_kb=0.0,
            longest_kb=0.0,
            autosomal_snps_used=autosomal_snps,
            indeterminate_reason=INSUFFICIENT_AUTOSOMAL_MARKERS,
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


def unevaluable_text(autosomal_snps_used: int) -> str:
    """Narrative for a sample whose autozygosity could not be assessed.

    Kept separate and public so the API can serve it for findings persisted
    before the evaluability gate existed, whose stored text asserts the very
    negative this gate withholds.
    """
    return (
        f"Runs of homozygosity were not assessed: this sample has "
        f"{autosomal_snps_used} callable autosomal SNP(s), and any single "
        f"reported run must itself contain at least {MIN_ROH_SNPS} homozygous "
        f"SNPs, so no run could have been reported whatever the genome "
        f"contains. No FROH estimate is given, because none can be measured "
        f"from this input. This reflects the coverage of the data analysed, "
        f"not a finding about your genome — it does not indicate that long "
        f"runs of homozygosity are absent."
    )


def _finding_text(result: RohResult) -> str:
    if result.indeterminate_reason is not None:
        return unevaluable_text(result.autosomal_snps_used)
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
    detail: dict[str, Any] = {
        "froh": result.froh,
        "total_roh_kb": result.total_roh_kb,
        "longest_kb": result.longest_kb,
        "n_segments": len(result.segments),
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

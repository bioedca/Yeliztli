"""Imputation feasibility / reachability report (SW-C4 / roadmap #47).

Wave C reporting layer. Imputation (SW-C1/C2) can only recover an untyped locus
when the locus sits on a chromosome the reference panel covers **and** the
genotyping array provides enough flanking directly-typed markers (the LD backbone
imputation leans on). This module reports a sample's imputation *reachability*
from those two factual signals — no contestable threshold, no reference-panel
binary required:

* **Panel coverage (structural).** The shipped 1000G Phase 3 v5a panel covers
  autosomes 1–22 + X on GRCh37 (:data:`backend.annotation.imputation_panel.PANEL_CHROMOSOMES`).
  Typed markers on any other chromosome (Y, MT, contigs) are *structurally
  unreachable* — imputation can never extend them.
* **Backbone density (descriptive).** Per panel chromosome the report gives the
  typed-marker count and the median spacing between adjacent typed markers. Denser
  backbones impute more reliably; this is reported as a plain descriptive
  statistic the caller can judge, not a pass/fail the module asserts.
* **Realized reachability.** When a sample has been imputed, the count of
  firewall-cleared imputed sites (``imputed_variants``) is the reachability the run
  actually achieved (already gated by the SW-C3 firewall).

**Graceful degradation.** A sample with no imputation (no ``imputed_variants``
table, or an empty one) reports ``imputation_run = False`` and zero realized
reachability — the structural / density signals still report from the typed data.
A sample with no typed variants reports all-zero counts.

This is a pure reporting module: it reads the sample DB and computes counts; it
surfaces no findings and changes no calls. Per-locus, LD-aware reachability against
the actual panel sites is a later refinement (validate its shape on a cluster run
once the ~8.5 GB panel is provisioned)."""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field

import sqlalchemy as sa
import structlog

from backend.annotation.imputation_panel import (
    PANEL_BUILD,
    PANEL_CHROMOSOMES,
    PANEL_VERSION,
)
from backend.db.tables import annotated_variants, imputed_variants

logger = structlog.get_logger(__name__)


def _norm_chrom(chrom: str | None) -> str | None:
    """Normalize a chromosome label (strip ``chr``, upper) for panel matching.

    Mirrors :func:`backend.analysis.prs._norm_chrom` so sample chromosome labels
    compare equal to the panel's regardless of a ``chr`` prefix.
    """
    if chrom is None:
        return None
    c = str(chrom).strip()
    if c[:3].lower() == "chr":
        c = c[3:]
    return c.upper()


# Panel-covered chromosomes, normalized once for membership tests.
_PANEL_CHROMS: frozenset[str] = frozenset(c.upper() for c in PANEL_CHROMOSOMES)


def panel_covers(chrom: str | None) -> bool:
    """Whether the imputation panel covers ``chrom`` (autosomes 1–22 + X, GRCh37).

    A locus on an uncovered chromosome (Y, MT, unplaced contigs) is structurally
    unreachable — imputation can never extend it regardless of backbone density.
    """
    norm = _norm_chrom(chrom)
    return norm is not None and norm in _PANEL_CHROMS


@dataclass(frozen=True)
class ChromReachability:
    """Typed-backbone density on one panel-covered chromosome."""

    chrom: str  # normalized panel chromosome ("1".."22", "X")
    typed_markers: int  # unique directly-typed loci on this chromosome
    median_gap_bp: int | None  # median spacing between adjacent typed loci (None if < 2)


@dataclass
class ReachabilitySummary:
    """A sample's imputation reachability/feasibility report."""

    panel_version: str
    panel_build: str
    panel_chromosomes: list[str]
    typed_total: int  # unique directly-typed loci (chrom, pos)
    typed_on_panel: int  # typed loci on panel-covered chromosomes (imputable backbone)
    typed_off_panel: int  # typed loci on uncovered chromosomes (structurally unreachable)
    imputation_run: bool  # whether firewall-cleared imputed variants are persisted
    imputed_reachable: int  # firewall-cleared imputed sites recovered (realized reachability)
    per_chromosome: list[ChromReachability] = field(default_factory=list)


def _median_gap_bp(positions: list[int]) -> int | None:
    """Median spacing (bp) between adjacent typed markers; ``None`` for fewer than two."""
    if len(positions) < 2:
        return None
    ordered = sorted(positions)
    gaps = [b - a for a, b in zip(ordered, ordered[1:], strict=False)]
    return int(round(statistics.median(gaps)))


def _count_imputed_reachable(sample_engine: sa.Engine) -> int:
    """Count firewall-cleared imputed sites (realized reachability).

    Graceful degradation: ``0`` when the ``imputed_variants`` table is absent (a
    sample DB predating Wave C) or empty (no imputation persisted).
    """
    with sample_engine.connect() as conn:
        if not sa.inspect(conn).has_table(imputed_variants.name):
            return 0
        return conn.execute(sa.select(sa.func.count()).select_from(imputed_variants)).scalar_one()


def summarize_sample_reachability(sample_engine: sa.Engine) -> ReachabilitySummary:
    """Report a sample's imputation reachability from panel coverage + backbone density.

    Reads the typed markers (``annotated_variants``) and any persisted imputed
    variants (``imputed_variants``); see the module docstring for the signals and
    their graceful-degradation behavior.
    """
    with sample_engine.connect() as conn:
        rows = conn.execute(
            sa.select(annotated_variants.c.chrom, annotated_variants.c.pos).distinct()
        ).fetchall()

    # Count unique (chrom, pos) loci, not rows: annotated_variants is rsid-unique, so a
    # single locus can carry multiple rows (multi-allelic / merged rsIDs). Reachability
    # and backbone density are locus properties — two rows at one position are one
    # backbone marker (and a zero gap would skew the density), so dedupe on the locus.
    typed_loci: set[tuple[str | None, int]] = {(_norm_chrom(r.chrom), r.pos) for r in rows}

    on_panel_positions: dict[str, list[int]] = {}
    typed_off_panel = 0
    for norm, pos in typed_loci:
        if norm is not None and norm in _PANEL_CHROMS:
            on_panel_positions.setdefault(norm, []).append(pos)
        else:
            typed_off_panel += 1

    typed_total = len(typed_loci)
    typed_on_panel = typed_total - typed_off_panel

    # Per panel chromosome, in the panel's own chromosome order, for chromosomes the
    # sample actually has typed markers on.
    per_chromosome = [
        ChromReachability(
            chrom=chrom,
            typed_markers=len(on_panel_positions[chrom]),
            median_gap_bp=_median_gap_bp(on_panel_positions[chrom]),
        )
        for chrom in (c.upper() for c in PANEL_CHROMOSOMES)
        if chrom in on_panel_positions
    ]

    imputed_reachable = _count_imputed_reachable(sample_engine)

    summary = ReachabilitySummary(
        panel_version=PANEL_VERSION,
        panel_build=PANEL_BUILD,
        panel_chromosomes=[c.upper() for c in PANEL_CHROMOSOMES],
        typed_total=typed_total,
        typed_on_panel=typed_on_panel,
        typed_off_panel=typed_off_panel,
        imputation_run=imputed_reachable > 0,
        imputed_reachable=imputed_reachable,
        per_chromosome=per_chromosome,
    )
    logger.info(
        "imputation_reachability_summarized",
        typed_total=typed_total,
        typed_on_panel=typed_on_panel,
        typed_off_panel=typed_off_panel,
        imputed_reachable=imputed_reachable,
    )
    return summary

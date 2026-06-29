"""Persist firewall-cleared imputed variants for a sample (Wave C glue).

Ties the Wave C pieces into one operation: prepare a sample's per-chromosome
input VCFs (:mod:`backend.analysis.imputation_input`), impute each against the
1000G panel with Beagle (:mod:`backend.analysis.imputation_runner`), apply the
SW-C3 MAF/r² firewall (:mod:`backend.analysis.imputation_firewall`), and store the
imputed variants that **clear** the firewall into the sample DB's
``imputed_variants`` table — the only imputed variants allowed to back a
P/LP/carrier/monogenic finding downstream (SW-C5/C6).

Quarantined imputed variants are never persisted: a row in ``imputed_variants``
means "imputed *and* reportable", so a future finding generator can trust any row
it reads. Genotyped (directly typed) markers are not stored here either — they
already live in ``annotated_variants``.

**Validated on real data (2026-06-27).** A real chr22 sample imputed against the
shipped v5a bref3 panel parses cleanly through this path; note that **single-sample
imputation yields conservative (often bimodal) DR2** — DR2 is a population-level
metric, so a lone sample's estimate skews low and the firewall keeps only the
high-confidence common markers. That is the intended safe direction for a clinical
gate (it withholds more, never less).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import sqlalchemy as sa
import structlog

from backend.analysis.imputation_firewall import (
    FirewallSummary,
    assess_variant,
    summarize_firewall,
)
from backend.analysis.imputation_input import (
    DEFAULT_INPUT_CHROMOSOMES,
    write_imputation_input_vcfs,
)
from backend.analysis.imputation_runner import (
    DEFAULT_JAVA_MEM,
    DEFAULT_TIMEOUT,
    ImputationChromResult,
    ImputationRunner,
    ImputationSummary,
    ImputedVariant,
    parse_imputed_vcf,
    summarize_dr2,
)
from backend.db.tables import imputed_variants

logger = structlog.get_logger(__name__)


def persist_imputed_variants(
    sample_engine: sa.Engine,
    variants: list[ImputedVariant],
    *,
    replace: bool = True,
) -> int:
    """Write the firewall-cleared imputed variants to ``imputed_variants``.

    Keeps only imputed markers that pass the SW-C3 firewall (well-imputed **and**
    common); genotyped markers and quarantined imputed markers are dropped.
    Deduplicates on the ``(chrom, pos, alt)`` primary key (last write wins). With
    ``replace`` (default) the table is cleared first, so a re-run reflects the
    latest imputation rather than accumulating stale rows. Returns the row count
    written.
    """
    rows_by_key: dict[tuple[str, int, str], dict] = {}
    for v in variants:
        if not v.imputed or not assess_variant(v).reportable:
            continue
        rows_by_key[(v.chrom, v.pos, v.alt)] = {
            "chrom": v.chrom,
            "pos": v.pos,
            "ref": v.ref,
            "alt": v.alt,
            "dr2": v.dr2,
            "af": v.af,
            "dosage": v.dosage,
            "best_guess_copies": v.best_guess_copies,
        }
    rows = list(rows_by_key.values())
    with sample_engine.begin() as conn:
        if replace:
            conn.execute(sa.delete(imputed_variants))
        if rows:
            conn.execute(sa.insert(imputed_variants), rows)
    logger.info("imputed_variants_persisted", n_written=len(rows), replaced=replace)
    return len(rows)


@dataclass
class ImputePersistResult:
    """Outcome of imputing a sample and persisting its firewall-cleared variants."""

    n_input_sites: int  # typed markers prepared as Beagle input
    n_imputed: int  # imputed markers parsed across all chromosomes
    n_persisted: int  # firewall-cleared imputed variants written
    firewall: FirewallSummary
    quality: ImputationSummary
    chrom_results: list[ImputationChromResult] = field(default_factory=list)


def impute_and_persist_sample(
    sample_engine: sa.Engine,
    work_dir: Path,
    *,
    panel_dir: Path,
    beagle_jar: Path,
    chromosomes: tuple[str, ...] = DEFAULT_INPUT_CHROMOSOMES,
    biological_sex: str | None = None,
    java_mem: str = DEFAULT_JAVA_MEM,
    nthreads: int | None = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> ImputePersistResult:
    """Prepare → impute → firewall → persist a sample's imputed variants.

    Writes input VCFs under ``work_dir/input``, imputes each input unit against
    the panel into ``work_dir/imputed``, then persists the firewall-cleared
    imputed variants to the sample DB. Chromosomes/regions whose input produced
    no usable sites, or whose Beagle run failed, are skipped (their failure is
    recorded in ``chrom_results``). Returns counts plus the DR2/firewall summaries.
    Chromosome X requires ``biological_sex`` resolved to ``XX`` or ``XY`` so its
    PAR/non-PAR ploidy can be encoded correctly.

    **Partial-failure safety.** ``persist_imputed_variants`` replaces the whole
    table, so it runs **only when every attempted chromosome succeeded** — if any
    Beagle run failed, persistence is skipped (``n_persisted == 0``) so a partial
    run can never overwrite a complete prior snapshot with a partial one.
    """
    work_dir = Path(work_dir)
    input_result = write_imputation_input_vcfs(
        sample_engine,
        work_dir / "input",
        chromosomes=chromosomes,
        biological_sex=biological_sex,
    )

    runner = ImputationRunner(panel_dir, beagle_jar, java_mem=java_mem, nthreads=nthreads)
    out_dir = work_dir / "imputed"
    all_variants: list[ImputedVariant] = []
    chrom_results: list[ImputationChromResult] = []
    for unit in input_result.units:
        res = runner.impute_chromosome(
            unit.chrom,
            unit.path,
            out_dir,
            region=unit.beagle_region,
            output_label=unit.key,
            timeout=timeout,
        )
        chrom_results.append(res)
        if res.return_ok and res.output_vcf is not None:
            all_variants.extend(parse_imputed_vcf(res.output_vcf))

    quality = summarize_dr2(all_variants)
    quality.chrom_runtimes = {r.chrom: r.runtime_seconds for r in chrom_results if r.return_ok}
    firewall = summarize_firewall(all_variants)

    failed_chroms = [r.chrom for r in chrom_results if not r.return_ok]
    if failed_chroms:
        # A full-table replace with only the surviving chromosomes' variants would
        # silently overwrite a complete prior snapshot — refuse and preserve it.
        logger.warning(
            "impute_partial_failure_persist_skipped",
            failed_chroms=failed_chroms,
            n_input_sites=input_result.n_emitted,
        )
        n_persisted = 0
    else:
        n_persisted = persist_imputed_variants(sample_engine, all_variants)

    logger.info(
        "impute_and_persist_complete",
        n_input_sites=input_result.n_emitted,
        chromosomes=len(input_result.vcf_paths),
        n_imputed=quality.n_imputed,
        n_persisted=n_persisted,
        failed_chroms=failed_chroms,
    )
    return ImputePersistResult(
        n_input_sites=input_result.n_emitted,
        n_imputed=quality.n_imputed,
        n_persisted=n_persisted,
        firewall=firewall,
        quality=quality,
        chrom_results=chrom_results,
    )

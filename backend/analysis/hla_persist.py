"""Predict + persist a sample's HIBAG HLA calls (Wave D glue).

Ties the Wave D pieces into one operation: prepare a sample's classical-HLA-region
PLINK fileset (:mod:`backend.analysis.hla_input`), call HLA alleles with HIBAG
(:mod:`backend.analysis.hibag_runner`), and store the per-locus calls into the
sample DB's ``hla_calls`` table — the source the Wave D SW-D2–D5 report layers read
via :mod:`backend.analysis.hla_resolver`.

This mirrors the Wave C imputation persist-driver
(:mod:`backend.analysis.imputation_persist`): idempotent full-table replace on
re-run, and a driver that degrades **gracefully** at every step (mirroring the
SW-C2 / SW-D1 never-fatal contract) rather than raising — HLA imputation needs an
operator-installed R + Bioconductor HIBAG runtime plus a BYO ancestry model, so a
default install has no runtime and simply stores no calls (the clinical layers then
show nothing, exactly like the imputed-findings path with no imputation persisted).

Real HLA-call validation is deferred to a run with the runtime + a model
provisioned (the SW-D1 deferred-runtime note); this driver is unit-tested with the
``HibagRunner`` mocked.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import sqlalchemy as sa
import structlog

from backend.analysis.hibag_runner import (
    DEFAULT_TIMEOUT,
    HLA_LOCI,
    RECOMMENDED_PROB_THRESHOLD,
    HibagRunner,
    HLACall,
    detect_rscript,
    resolve_model,
)
from backend.analysis.hla_input import XMHC_GRCH37, MHCRegion, write_hibag_plink_input
from backend.db.tables import hla_calls

logger = structlog.get_logger(__name__)

# Predict+persist outcome states (never raised — reported).
STATUS_OK = "ok"  # HLA calls produced and persisted
STATUS_UNAVAILABLE = "unavailable"  # no Rscript / no model for the ancestry
STATUS_NO_INPUT = "no_input"  # sample had no usable HLA-region SNP
STATUS_FAILED = "failed"  # the HIBAG run itself failed (non-zero / parse / empty)


def persist_hla_calls(
    sample_engine: sa.Engine,
    calls: list[HLACall],
    *,
    ancestry_model: str | None = None,
    source: str = "hibag",
    replace: bool = True,
) -> int:
    """Write the per-locus HLA calls to ``hla_calls`` and return the row count.

    Deduplicates on ``locus`` (one genotype per locus; last write wins). With
    ``replace`` (default) the table is cleared first, so a re-run reflects the
    latest prediction rather than accumulating stale rows.
    """
    rows_by_locus: dict[str, dict] = {}
    for c in calls:
        rows_by_locus[c.locus] = {
            "locus": c.locus,
            "allele1": c.allele1,
            "allele2": c.allele2,
            "prob": c.prob,
            "matching": c.matching,
            "low_confidence": 1 if c.low_confidence else 0,
            "ancestry_model": ancestry_model,
            "source": source,
        }
    rows = list(rows_by_locus.values())
    with sample_engine.begin() as conn:
        if replace:
            conn.execute(sa.delete(hla_calls))
        if rows:
            conn.execute(sa.insert(hla_calls), rows)
    logger.info("hla_calls_persisted", n_written=len(rows), replaced=replace, source=source)
    return len(rows)


@dataclass
class HlaPredictPersistResult:
    """Outcome of predicting a sample's HLA alleles and persisting them."""

    status: str  # one of STATUS_*
    ancestry: str | None = None
    n_input_snps: int = 0  # HLA-region SNPs prepared for HIBAG
    n_calls: int = 0  # loci called
    n_persisted: int = 0  # rows written to hla_calls
    runtime_seconds: float = 0.0
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.status == STATUS_OK


def predict_and_persist_hla_calls(
    sample_engine: sa.Engine,
    work_dir: Path,
    *,
    rscript: Path | None,
    model_dir: Path | None,
    ancestry: str,
    region: MHCRegion = XMHC_GRCH37,
    loci=HLA_LOCI,
    sample_name: str = "SAMPLE",
    prob_threshold: float = RECOMMENDED_PROB_THRESHOLD,
    timeout: float = DEFAULT_TIMEOUT,
    runner: HibagRunner | None = None,
) -> HlaPredictPersistResult:
    """Prepare → predict → persist a sample's classical HLA calls.

    Writes the PLINK input under ``work_dir/input`` and the HIBAG output under
    ``work_dir/out``, then persists the per-locus calls. **Never raises** — every
    outcome is reported via ``status`` so a route/driver caller does not crash:
    ``unavailable`` (no Rscript, no ``{ancestry}-HLA4.RData``, or the bundled R
    script is missing), ``no_input`` (no usable HLA-region SNP in the sample),
    ``failed`` (the HIBAG run errored, or an I/O / database error while preparing
    input or persisting), or ``ok``. On any non-``ok`` outcome the ``hla_calls``
    table is left untouched (a partial/failed run never overwrites a prior good
    snapshot).

    ``runner`` is injectable for testing; in production it is built from
    ``rscript``/``model_dir`` only once availability is confirmed.
    """
    # 1. Availability guard (never construct HibagRunner if the runtime is absent).
    if runner is None:
        if detect_rscript(rscript) is None:
            return HlaPredictPersistResult(
                status=STATUS_UNAVAILABLE,
                ancestry=ancestry,
                detail="Rscript not found (install R + Bioconductor HIBAG, set hibag_rscript).",
            )
        if resolve_model(model_dir, ancestry) is None:
            return HlaPredictPersistResult(
                status=STATUS_UNAVAILABLE,
                ancestry=ancestry,
                detail=f"no HIBAG model for ancestry {ancestry!r} in {model_dir} (BYO model).",
            )

    # 2. Prepare the classical-HLA-region PLINK input (an I/O / DB error here is a
    #    genuine failure, distinct from the legitimate "no usable SNP" empty result).
    work_dir = Path(work_dir)
    try:
        input_result = write_hibag_plink_input(
            sample_engine, work_dir / "input" / "sample", region=region, sample_name=sample_name
        )
    except (OSError, sa.exc.SQLAlchemyError) as exc:
        logger.error("hla_input_prep_failed", error_type=type(exc).__name__)
        return HlaPredictPersistResult(
            status=STATUS_FAILED,
            ancestry=ancestry,
            detail=f"failed to prepare HLA-region PLINK input: {type(exc).__name__}",
        )
    if input_result.plink_prefix is None:
        return HlaPredictPersistResult(
            status=STATUS_NO_INPUT,
            ancestry=ancestry,
            detail=f"no HLA-region SNP in {region.chrom}:{region.start}-{region.end}.",
        )

    # 3. Run HIBAG. Building HibagRunner can still raise FileNotFoundError (e.g. the
    #    bundled R script missing, or Rscript vanished after the guard) — treat that
    #    as an availability problem rather than letting it escape.
    if runner is not None:
        active_runner = runner
    else:
        try:
            active_runner = HibagRunner(rscript=rscript, model_dir=model_dir)
        except FileNotFoundError as exc:
            return HlaPredictPersistResult(
                status=STATUS_UNAVAILABLE,
                ancestry=ancestry,
                detail=str(exc),
            )
    result = active_runner.predict_for_ancestry(
        input_result.plink_prefix,
        ancestry,
        work_dir / "out",
        loci=loci,
        prob_threshold=prob_threshold,
        timeout=timeout,
    )
    if not result.return_ok:
        return HlaPredictPersistResult(
            status=STATUS_FAILED,
            ancestry=ancestry,
            n_input_snps=input_result.n_emitted,
            runtime_seconds=result.runtime_seconds,
            detail=result.stderr_tail or "HIBAG run failed",
        )

    # 4. Persist (a DB error here must not escape the graceful contract).
    try:
        n_persisted = persist_hla_calls(sample_engine, result.calls, ancestry_model=ancestry)
    except sa.exc.SQLAlchemyError as exc:
        logger.error("hla_calls_persist_failed", error_type=type(exc).__name__)
        return HlaPredictPersistResult(
            status=STATUS_FAILED,
            ancestry=ancestry,
            n_input_snps=input_result.n_emitted,
            n_calls=len(result.calls),
            runtime_seconds=result.runtime_seconds,
            detail=f"failed to persist HLA calls: {type(exc).__name__}",
        )
    logger.info(
        "hla_predict_persist_complete",
        ancestry=ancestry,
        n_input_snps=input_result.n_emitted,
        n_calls=len(result.calls),
        n_persisted=n_persisted,
    )
    return HlaPredictPersistResult(
        status=STATUS_OK,
        ancestry=ancestry,
        n_input_snps=input_result.n_emitted,
        n_calls=len(result.calls),
        n_persisted=n_persisted,
        runtime_seconds=result.runtime_seconds,
    )

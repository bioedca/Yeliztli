"""LAI runner — Local Ancestry Inference pipeline for Yeliztli.

Orchestrates the full LAI pipeline:
  1. Read genotypes from sample DB (raw_variants table)
  2. Translate rsIDs to GRCh38 coordinates via liftover lookup
  3. Write per-chromosome unphased VCFs (using pysam)
  4. Phase with Beagle against the shipped reference panel
  5. Run Gnomix inference using re-exported models (numpy + xgboost)
  6. Aggregate into global ancestry proportions + chromosome painting

Subprocess calls to bcftools/bgzip/tabix are replaced by pysam; Gnomix
inference is handled by ``gnomix_inference.py`` instead of calling the
gnomix.py script.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import time
from collections import defaultdict
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, TypedDict

import numpy as np
import structlog

from backend.analysis.lai_liftover import (
    LIFTOVER_FILENAMES,
    canonical_lai_autosome,
    load_lai_rsid_lookup,
    resolve_lai_liftover_path,
)
from backend.analysis.zygosity import is_no_call

if TYPE_CHECKING:
    from backend.analysis.gnomix_inference import ChromosomeResult

logger = structlog.get_logger(__name__)

# Threshold below which MID ancestry estimates are flagged as lower-precision
MID_LOW_PRECISION_THRESHOLD = 0.15

# Drop-rate threshold above which the LAI coverage banner is shown (Plan §6.6)
LAI_DROP_RATE_WARNING_THRESHOLD = 0.15

# Version of the machine-readable LAI coverage payload stored in result metadata.
LAI_COVERAGE_METRICS_SCHEMA_VERSION = 1

# Source labels for merged samples (Plan §6.6, §10.2)
_MERGED_SOURCE_KEYS = ("S1", "S2", "both")

POPULATIONS: dict[str, dict[str, str]] = {
    "AFR": {"display": "African", "color": "#E8A838"},
    "AMR": {"display": "Indigenous American", "color": "#EE6677"},
    "CSA": {"display": "Central/South Asian", "color": "#AA3377"},
    "EAS": {"display": "East Asian", "color": "#66CCEE"},
    "EUR": {"display": "European", "color": "#4477AA"},
    "MID": {"display": "Middle Eastern", "color": "#228833"},
    "OCE": {"display": "Oceanian", "color": "#CCBB44"},
}


class SourceCoverageCounts(TypedDict):
    """Input-marker counts for one source/vendor."""

    hits: int
    drops: int


class ModelMarkerCounts(TypedDict):
    """Model-marker alignment counts for one scope."""

    matched: int
    total: int
    allele_mismatch: int
    match_rate: float


class EmittedMarkerMetrics(TypedDict):
    """Markers emitted to Beagle input VCFs."""

    total: int
    by_autosome: dict[str, int]


class ModelMarkerMetrics(TypedDict):
    """Model-marker alignment counts across and within autosomes."""

    aggregate: ModelMarkerCounts
    by_autosome: dict[str, ModelMarkerCounts]


class ModelDenominatorStatus(TypedDict):
    """Whether all model-marker and window denominators were readable."""

    complete: bool
    unreadable_autosomes: list[int]


class AutosomeSetMetrics(TypedDict):
    """Count and stable numeric identities for an autosome set."""

    count: int
    identities: list[int]


class HaplotypeWindowMetrics(TypedDict):
    """Expected and ancestry-assigned haplotype-window counts."""

    expected: int
    valid_assigned: int
    assignment_rate: float
    expected_by_autosome: dict[str, int]
    valid_assigned_by_autosome: dict[str, int]


class LAICoverageMetrics(TypedDict):
    """Versioned coverage metrics for calibration and later policy gates."""

    schema_version: int
    emitted_markers: EmittedMarkerMetrics
    model_markers: ModelMarkerMetrics
    model_denominators: ModelDenominatorStatus
    phased_autosomes: AutosomeSetMetrics
    analyzed_autosomes: AutosomeSetMetrics
    haplotype_windows: HaplotypeWindowMetrics
    per_source: dict[str, SourceCoverageCounts]


@dataclass(frozen=True)
class PhasedVCFParseResult:
    """Haplotypes plus the model-marker alignment telemetry that produced them.

    Iteration intentionally yields only the two haplotypes so legacy private
    callers that unpacked ``hap0, hap1 = _parse_phased_vcf(...)`` keep working.
    New diagnostic callers should read :attr:`model_marker_counts` directly.
    """

    hap0: np.ndarray
    hap1: np.ndarray
    model_marker_counts: ModelMarkerCounts

    def __iter__(self) -> Iterator[np.ndarray]:
        yield self.hap0
        yield self.hap1


@dataclass
class LAIRunnerResult:
    """Result from a full LAI pipeline run."""

    global_ancestry: dict[str, dict]
    chromosome_painting: dict[str, list[dict]]
    metadata: dict
    coverage_telemetry: dict[str, SourceCoverageCounts] = field(default_factory=dict)


class LAIRunner:
    """Orchestrates local ancestry inference from sample DB genotypes."""

    def __init__(self, bundle_path: str | Path, java_mem: str = "4g") -> None:
        self.bundle = Path(bundle_path)
        self.java_mem = java_mem
        self._validate_bundle()
        self.rsid_lookup = self._load_rsid_lookup()
        logger.info("lai_runner_init", rsid_count=len(self.rsid_lookup))

    def _validate_bundle(self) -> None:
        """Check that all required bundle components exist."""
        required = [
            self.bundle / "beagle" / "beagle.jar",
        ]
        for chr_num in range(1, 23):
            required.extend(
                [
                    self.bundle / "phasing_panel" / f"ref_panel_chr{chr_num}.vcf.gz",
                    self.bundle / "gnomix_models" / f"chr{chr_num}" / "metadata.npz",
                    self.bundle / "gnomix_models" / f"chr{chr_num}" / "base_coefs.npz",
                    self.bundle / "gnomix_models" / f"chr{chr_num}" / "smoother.json",
                    self.bundle / "genetic_maps" / f"plink.chrchr{chr_num}.GRCh38.map",
                ]
            )

        missing = [str(p) for p in required if not p.exists()]
        # The rsID->GRCh38 liftover lookup may be named either way (see
        # LIFTOVER_FILENAMES); require that at least one exists.
        if self._liftover_path() is None:
            missing.append(str(self.bundle / "liftover" / LIFTOVER_FILENAMES[0]))
        if missing:
            raise FileNotFoundError(
                "LAI bundle incomplete. Missing:\n"
                + "\n".join(missing[:10])
                + (f"\n... and {len(missing) - 10} more" if len(missing) > 10 else "")
            )
        logger.info("lai_bundle_validated", path=str(self.bundle))

    def _liftover_path(self) -> Path | None:
        """Resolve the rsID->GRCh38 liftover table, tolerating the v2.0.0 rename.

        Returns the first existing candidate from :data:`LIFTOVER_FILENAMES`
        (``array_site_mapping.tsv`` for v2.0.0, ``rsid_to_grch38.tsv`` for
        v1.1), or ``None`` if neither is present.
        """
        return resolve_lai_liftover_path(self.bundle)

    def _load_rsid_lookup(self) -> dict[str, tuple[str, int]]:
        """Load rsID -> (chrom, pos_grch38) lookup table."""
        path = self._liftover_path()
        if path is None:
            raise FileNotFoundError(
                f"LAI bundle liftover table not found in {self.bundle / 'liftover'}"
            )
        return load_lai_rsid_lookup(path)

    def run(
        self,
        genotypes: list[dict[str, str | int]],
        output_dir: str | Path,
        progress_callback: Callable[[str, float], None] | None = None,
        cleanup: bool = True,
        file_format: str = "",
        *,
        diagnostic_metrics_callback: Callable[[LAICoverageMetrics], None] | None = None,
        allow_below_minimum_for_diagnostics: bool = False,
    ) -> LAIRunnerResult:
        """Run the full LAI pipeline.

        Args:
            genotypes: List of dicts with keys: rsid, chrom, pos, genotype, and
                optionally ``source`` (empty string on pre-Phase-3 sample DBs,
                ``S1``/``S2``/``both`` on merged samples — Plan §6.6).
            output_dir: Directory for intermediate and output files.
            progress_callback: Optional function(message, fraction) for updates.
            cleanup: Remove intermediate files after completion.
            file_format: ``sample_metadata.file_format`` for the sample (e.g.
                ``"23andme_v5"``, ``"ancestrydna_v2.0"``, ``"merged_v1"``).
                Drives single-key vs. three-key dispatch when every genotype
                has ``source=""`` (Plan §6.6).
            diagnostic_metrics_callback: Optional calibration-only callback.
                Receives fresh coverage snapshots after each pipeline stage so
                diagnostics retain partial metrics even when a later stage fails.
            allow_below_minimum_for_diagnostics: Calibration-only escape hatch
                for observing model-marker match rates below the production 5%
                hard failure. The default preserves production behavior.

        Returns:
            LAIRunnerResult with global_ancestry, chromosome_painting, metadata,
            and ``coverage_telemetry`` payload keyed by source/vendor.
        """
        start_time = time.time()
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)

        (
            model_marker_totals,
            expected_haplotype_windows,
            unreadable_model_metadata_autosomes,
        ) = self._read_model_coverage_denominators()
        emitted_markers_by_autosome: dict[int, int] = {}
        model_marker_counts_by_autosome: dict[int, ModelMarkerCounts] = {
            chrom: {
                "matched": 0,
                "total": model_marker_totals.get(chrom, 0),
                "allele_mismatch": 0,
                "match_rate": 0.0,
            }
            for chrom in range(1, 23)
        }
        coverage_telemetry: dict[str, SourceCoverageCounts] = {}
        phased_paths: dict[int, Path] = {}
        chrom_results: dict[int, ChromosomeResult] = {}
        matched = 0

        def coverage_metrics() -> LAICoverageMetrics:
            return self._build_lai_coverage_metrics(
                emitted_total=matched,
                emitted_by_autosome=emitted_markers_by_autosome,
                model_markers_by_autosome=model_marker_counts_by_autosome,
                phased_autosomes=phased_paths.keys(),
                chrom_results=chrom_results,
                expected_haplotype_windows_by_autosome=expected_haplotype_windows,
                unreadable_model_metadata_autosomes=unreadable_model_metadata_autosomes,
                per_source=coverage_telemetry,
            )

        def emit_diagnostic_metrics() -> None:
            if diagnostic_metrics_callback is not None:
                diagnostic_metrics_callback(coverage_metrics())

        def report(msg: str, frac: float) -> None:
            logger.info("lai_progress", message=msg, fraction=frac)
            if progress_callback:
                progress_callback(msg, frac)

        # Step 1: Filter to autosomal diploid genotypes
        report("Preparing genotypes...", 0.0)
        filtered = self._filter_genotypes(genotypes)
        report(f"Filtered {len(filtered)} autosomal diploid genotypes", 0.05)

        # Step 2: Translate to GRCh38 and write per-chromosome VCFs
        report("Writing per-chromosome VCFs...", 0.05)
        vcf_paths, matched, per_source_counts = self._write_per_chrom_vcfs(
            filtered,
            out,
            emitted_markers_by_autosome=emitted_markers_by_autosome,
        )
        report(
            f"Wrote {matched} variants to VCF across {len(vcf_paths)} chromosomes",
            0.10,
        )
        coverage_telemetry = self._build_coverage_telemetry(per_source_counts, file_format)
        self._emit_coverage_telemetry(
            total_genotypes=len(genotypes),
            filtered=len(filtered),
            matched=matched,
            per_source=coverage_telemetry,
            file_format=file_format,
        )
        emit_diagnostic_metrics()
        if matched == 0:
            raise RuntimeError(
                "Insufficient data for local ancestry inference: no usable markers "
                "remained after autosomal filtering, bundle mapping, and genotype "
                f"encoding (filtered={len(filtered)}, matched=0)"
            )

        # Step 3: Phase with Beagle
        for i, chr_num in enumerate(range(1, 23), 1):
            chrom = f"chr{chr_num}"
            frac = 0.10 + (i / 22) * 0.60
            report(f"Phasing {chrom}... ({i}/22)", frac)

            if chrom not in vcf_paths:
                logger.debug("lai_no_variants", chrom=chrom)
                continue

            phased = self._phase_chromosome(chr_num, vcf_paths[chrom], out)
            if phased:
                phased_paths[chr_num] = phased

        report(f"Phasing complete: {len(phased_paths)} chromosomes", 0.70)
        emit_diagnostic_metrics()
        if not phased_paths:
            raise RuntimeError(
                "Insufficient data for local ancestry inference: no chromosome was "
                "successfully phased from the usable markers"
            )

        # Step 4: Run Gnomix inference
        from backend.analysis.gnomix_inference import (
            load_gnomix_model,
            run_inference,
        )

        failed_chroms: list[int] = []
        for i, chr_num in enumerate(sorted(phased_paths.keys()), 1):
            frac = 0.70 + (i / 22) * 0.20
            report(f"Inferring ancestry chr{chr_num}... ({i}/22)", frac)

            model_dir = self.bundle / "gnomix_models" / f"chr{chr_num}"
            try:
                model = load_gnomix_model(model_dir)
            except Exception:
                logger.exception("gnomix_inference_failed", chrom=chr_num)
                failed_chroms.append(chr_num)
                emit_diagnostic_metrics()
                continue

            try:
                parsed = self._parse_phased_vcf(
                    phased_paths[chr_num],
                    model.snp_pos,
                    model.snp_ref,
                    model.snp_alt,
                    allow_below_minimum_for_diagnostics=(allow_below_minimum_for_diagnostics),
                )
                hap0, hap1 = parsed
            except Exception:
                emit_diagnostic_metrics()
                raise

            if isinstance(parsed, PhasedVCFParseResult):
                counts = parsed.model_marker_counts
                model_marker_counts_by_autosome[chr_num] = {
                    "matched": counts["matched"],
                    "total": counts["total"],
                    "allele_mismatch": counts["allele_mismatch"],
                    "match_rate": counts["match_rate"],
                }
            emit_diagnostic_metrics()

            try:
                result = run_inference(model, hap0, hap1)
            except Exception:
                logger.exception("gnomix_inference_failed", chrom=chr_num)
                failed_chroms.append(chr_num)
                emit_diagnostic_metrics()
                continue

            chrom_results[chr_num] = result
            emit_diagnostic_metrics()

        if failed_chroms and len(failed_chroms) > len(phased_paths) // 2:
            raise RuntimeError(f"Too many chromosomes failed inference: {failed_chroms}")

        report("Ancestry inference complete", 0.90)

        # Step 5: Aggregate results
        report("Aggregating results...", 0.90)
        global_ancestry = self._compute_global_ancestry(chrom_results)
        chromosome_painting = self._build_chromosome_painting(chrom_results)
        if not global_ancestry:
            raise RuntimeError(
                "Insufficient data for local ancestry inference: no ancestry-assigned "
                "windows were produced"
            )

        # Step 6: Metadata
        elapsed = time.time() - start_time
        drop_rate = ((len(filtered) - matched) / len(filtered)) if filtered else 0.0
        lai_coverage_metrics = coverage_metrics()
        metadata = {
            "total_genotypes": len(genotypes),
            "filtered_genotypes": len(filtered),
            "mapped_to_grch38": matched,
            "chromosomes_phased": len(phased_paths),
            "chromosomes_analyzed": len(chrom_results),
            "chromosomes_failed": failed_chroms,
            "runtime_seconds": round(elapsed, 1),
            "populations": list(POPULATIONS.keys()),
            "coverage_telemetry": coverage_telemetry,
            "lai_coverage_metrics": lai_coverage_metrics,
            "drop_rate": round(drop_rate, 4),
            "drop_rate_warning": drop_rate > LAI_DROP_RATE_WARNING_THRESHOLD,
        }

        lai_result = LAIRunnerResult(
            global_ancestry=global_ancestry,
            chromosome_painting=chromosome_painting,
            metadata=metadata,
            coverage_telemetry=coverage_telemetry,
        )

        # Save results
        results_path = out / "lai_results.json"
        with open(results_path, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "global_ancestry": lai_result.global_ancestry,
                    "chromosome_painting": lai_result.chromosome_painting,
                    "metadata": lai_result.metadata,
                },
                f,
                indent=2,
                default=str,
            )
        report(f"Results saved to {results_path}", 0.95)

        if cleanup:
            self._cleanup(out)

        report("LAI analysis complete", 1.0)
        return lai_result

    def _filter_genotypes(self, genotypes: list[dict]) -> list[dict]:
        """Filter to autosomal diploid SNP genotypes.

        Carries ``source`` through to the filtered dicts so the per-source
        telemetry accumulator in ``_write_per_chrom_vcfs`` can read it.
        Pre-Phase-3 sample DBs that don't carry a ``source`` column fall
        through with ``source=""`` (Plan §6.6).
        """
        autosomal_chroms = {str(i) for i in range(1, 23)}
        filtered = []
        for gt in genotypes:
            chrom = str(gt["chrom"])
            genotype = str(gt["genotype"])
            if chrom not in autosomal_chroms:
                continue
            if is_no_call(genotype) or len(genotype) != 2:
                continue
            a1, a2 = genotype[0], genotype[1]
            if a1 not in "ACGT" or a2 not in "ACGT":
                continue
            filtered.append(
                {
                    "rsid": gt["rsid"],
                    "chrom": chrom,
                    "allele1": a1,
                    "allele2": a2,
                    "source": gt.get("source", "") or "",
                }
            )
        return filtered

    @staticmethod
    def _build_coverage_telemetry(
        per_source: dict[str, dict[str, int]],
        file_format: str,
    ) -> dict[str, SourceCoverageCounts]:
        """Shape raw per-source counts into the Plan §6.6 telemetry payload.

        Dispatch is ``source``-driven: any non-empty ``source`` key (or a
        ``merged_v1`` file_format) collapses to the three-key ``S1/S2/both``
        path. Otherwise emit a single-key ``{vendor: counts}`` where
        ``vendor = file_format.split("_", 1)[0].lower()``.
        """
        has_nonempty_source = any(key for key in per_source)
        if has_nonempty_source or file_format == "merged_v1":
            return {
                key: {
                    "hits": int(per_source.get(key, {}).get("hits", 0)),
                    "drops": int(per_source.get(key, {}).get("drops", 0)),
                }
                for key in _MERGED_SOURCE_KEYS
            }

        vendor = file_format.split("_", 1)[0].lower() if file_format else ""
        if not vendor:
            vendor = "unknown"
        counts = per_source.get("", {"hits": 0, "drops": 0})
        return {
            vendor: {
                "hits": int(counts.get("hits", 0)),
                "drops": int(counts.get("drops", 0)),
            }
        }

    @staticmethod
    def _emit_coverage_telemetry(
        *,
        total_genotypes: int,
        filtered: int,
        matched: int,
        per_source: dict[str, SourceCoverageCounts],
        file_format: str,
    ) -> None:
        """Log the per-source LAI dropout telemetry line (Plan §6.6)."""
        dropped = filtered - matched
        drop_rate = (dropped / filtered) if filtered else 0.0
        logger.info(
            "lai_coverage_telemetry",
            total_variants=total_genotypes,
            filtered=filtered,
            mapped=matched,
            dropped=dropped,
            drop_rate=round(drop_rate, 4),
            drop_rate_warning=drop_rate > LAI_DROP_RATE_WARNING_THRESHOLD,
            file_format=file_format or None,
            per_source=per_source,
        )

    def _read_model_coverage_denominators(
        self,
    ) -> tuple[dict[int, int], dict[int, int], list[int]]:
        """Read marker and haplotype-window denominators for all 22 models.

        Coverage diagnostics must not silently drop a chromosome merely because
        no input VCF was emitted or inference failed. Reading the tiny model
        metadata files up front gives every autosome an explicit denominator.
        A malformed metadata file is logged and represented as zero while its
        autosome is returned in a machine-readable error list. Consumers must
        not treat coverage metrics as calibration evidence unless that list is
        empty.
        """
        marker_totals: dict[int, int] = {}
        haplotype_windows: dict[int, int] = {}
        unreadable_autosomes: list[int] = []
        for chrom in range(1, 23):
            metadata_path = self.bundle / "gnomix_models" / f"chr{chrom}" / "metadata.npz"
            try:
                with np.load(metadata_path, allow_pickle=False) as metadata:
                    marker_total = int(np.asarray(metadata["snp_pos"]).size)
                    haplotype_window_total = 2 * int(np.asarray(metadata["W"]).item())
                    if marker_total <= 0 or haplotype_window_total <= 0:
                        raise ValueError("model coverage denominators must be positive")
                    marker_totals[chrom] = marker_total
                    haplotype_windows[chrom] = haplotype_window_total
            except (OSError, EOFError, KeyError, TypeError, ValueError):
                logger.warning(
                    "lai_model_coverage_metadata_unreadable",
                    chrom=chrom,
                    path=str(metadata_path),
                )
                marker_totals[chrom] = 0
                haplotype_windows[chrom] = 0
                unreadable_autosomes.append(chrom)
        return marker_totals, haplotype_windows, unreadable_autosomes

    @staticmethod
    def _build_lai_coverage_metrics(
        *,
        emitted_total: int,
        emitted_by_autosome: dict[int, int],
        model_markers_by_autosome: dict[int, ModelMarkerCounts],
        phased_autosomes: Iterable[int],
        chrom_results: dict[int, ChromosomeResult],
        expected_haplotype_windows_by_autosome: dict[int, int],
        unreadable_model_metadata_autosomes: Iterable[int],
        per_source: dict[str, SourceCoverageCounts],
    ) -> LAICoverageMetrics:
        """Build a fresh, JSON-safe schema-v1 LAI coverage snapshot."""
        marker_by_autosome: dict[str, ModelMarkerCounts] = {}
        for chrom in range(1, 23):
            raw = model_markers_by_autosome.get(chrom)
            total = int(raw["total"]) if raw is not None else 0
            matched = int(raw["matched"]) if raw is not None else 0
            allele_mismatch = int(raw["allele_mismatch"]) if raw is not None else 0
            marker_by_autosome[str(chrom)] = {
                "matched": matched,
                "total": total,
                "allele_mismatch": allele_mismatch,
                "match_rate": round(matched / total, 6) if total else 0.0,
            }

        aggregate_total = sum(counts["total"] for counts in marker_by_autosome.values())
        aggregate_matched = sum(counts["matched"] for counts in marker_by_autosome.values())
        aggregate_mismatch = sum(
            counts["allele_mismatch"] for counts in marker_by_autosome.values()
        )

        expected_by_autosome = {
            str(chrom): int(expected_haplotype_windows_by_autosome.get(chrom, 0))
            for chrom in range(1, 23)
        }
        valid_by_autosome = {str(chrom): 0 for chrom in range(1, 23)}
        n_populations = len(POPULATIONS)
        for chrom, result in chrom_results.items():
            valid = 0
            for assignments in (result.hap0_ancestry, result.hap1_ancestry):
                values = np.asarray(assignments).reshape(-1)[: result.n_windows]
                valid += int(np.count_nonzero((values >= 0) & (values < n_populations)))
            if 1 <= chrom <= 22:
                valid_by_autosome[str(chrom)] = valid

        expected_windows = sum(expected_by_autosome.values())
        valid_windows = sum(valid_by_autosome.values())
        phased_ids = sorted(chrom for chrom in phased_autosomes if 1 <= chrom <= 22)
        analyzed_ids = sorted(chrom for chrom in chrom_results if 1 <= chrom <= 22)
        unreadable_ids = sorted(
            {chrom for chrom in unreadable_model_metadata_autosomes if 1 <= chrom <= 22}
        )

        return {
            "schema_version": LAI_COVERAGE_METRICS_SCHEMA_VERSION,
            "emitted_markers": {
                "total": int(emitted_total),
                "by_autosome": {
                    str(chrom): int(emitted_by_autosome.get(chrom, 0)) for chrom in range(1, 23)
                },
            },
            "model_markers": {
                "aggregate": {
                    "matched": aggregate_matched,
                    "total": aggregate_total,
                    "allele_mismatch": aggregate_mismatch,
                    "match_rate": (
                        round(aggregate_matched / aggregate_total, 6) if aggregate_total else 0.0
                    ),
                },
                "by_autosome": marker_by_autosome,
            },
            "model_denominators": {
                "complete": not unreadable_ids,
                "unreadable_autosomes": unreadable_ids,
            },
            "phased_autosomes": {"count": len(phased_ids), "identities": phased_ids},
            "analyzed_autosomes": {"count": len(analyzed_ids), "identities": analyzed_ids},
            "haplotype_windows": {
                "expected": expected_windows,
                "valid_assigned": valid_windows,
                "assignment_rate": (
                    round(valid_windows / expected_windows, 6) if expected_windows else 0.0
                ),
                "expected_by_autosome": expected_by_autosome,
                "valid_assigned_by_autosome": valid_by_autosome,
            },
            "per_source": {
                source: {"hits": int(counts["hits"]), "drops": int(counts["drops"])}
                for source, counts in per_source.items()
            },
        }

    def _write_per_chrom_vcfs(
        self,
        genotypes: list[dict],
        out: Path,
        *,
        emitted_markers_by_autosome: dict[int, int] | None = None,
    ) -> tuple[dict[str, Path], int, dict[str, dict[str, int]]]:
        """Translate rsIDs to GRCh38 and write per-chromosome VCFs using pysam.

        Returns a ``(vcf_paths, total_sites, per_source_counts)`` triple. The
        third element accumulates per-source ``{"hits", "drops"}`` counts over
        the input ``genotypes`` — a hit is a site that is actually emitted into
        a VCF after liftover, REF/ALT lookup, and genotype encoding; everything
        else is a drop (Plan §6.6).
        """
        vcf_dir = out / "unphased_vcfs"
        vcf_dir.mkdir(exist_ok=True)

        chrom_genotypes: dict[str, list[dict]] = defaultdict(list)
        per_source: dict[str, dict[str, int]] = {}
        for gt in genotypes:
            rsid = gt["rsid"]
            src = gt.get("source", "") or ""
            counts = per_source.setdefault(src, {"hits": 0, "drops": 0})
            if rsid not in self.rsid_lookup:
                counts["drops"] += 1
                continue
            raw_chrom, pos38 = self.rsid_lookup[rsid]
            chrom = canonical_lai_autosome(raw_chrom)
            if chrom is None:
                counts["drops"] += 1
                continue
            chrom_genotypes[chrom].append(
                {
                    "chrom": chrom,
                    "pos": pos38,
                    "rsid": rsid,
                    "allele1": gt["allele1"],
                    "allele2": gt["allele2"],
                    "source": src,
                }
            )

        vcf_paths: dict[str, Path] = {}
        total_sites = 0
        for chrom in sorted(chrom_genotypes.keys(), key=lambda x: int(x.removeprefix("chr"))):
            sites = sorted(chrom_genotypes[chrom], key=lambda x: x["pos"])
            vcf_path = vcf_dir / f"user_{chrom}.vcf.gz"
            write_counts = self._write_single_vcf(chrom, sites, vcf_path)
            emitted_sites = 0
            for src, counts in write_counts.items():
                target = per_source.setdefault(src, {"hits": 0, "drops": 0})
                target["hits"] += counts["hits"]
                target["drops"] += counts["drops"]
                emitted_sites += counts["hits"]

            if emitted_sites:
                vcf_paths[chrom] = vcf_path
                total_sites += emitted_sites
            if emitted_markers_by_autosome is not None:
                chrom_num = int(chrom.removeprefix("chr"))
                emitted_markers_by_autosome[chrom_num] = emitted_sites

        return vcf_paths, total_sites, per_source

    def _write_single_vcf(
        self, chrom: str, sites: list[dict], vcf_path: Path
    ) -> dict[str, dict[str, int]]:
        """Write one chromosome VCF and return emitted/drop counts by source."""
        import pysam

        ref_alleles = self._get_ref_alleles_pysam(chrom)
        per_source: dict[str, dict[str, int]] = {}

        # Build VCF text in memory, then compress with pysam
        lines: list[str] = [
            "##fileformat=VCFv4.2\n",
            f"##contig=<ID={chrom}>\n",
            '##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">\n',
            "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tSAMPLE\n",
        ]

        for site in sites:
            pos = site["pos"]
            rsid = site["rsid"]
            a1, a2 = site["allele1"], site["allele2"]
            src = site.get("source", "") or ""
            counts = per_source.setdefault(src, {"hits": 0, "drops": 0})

            if pos not in ref_alleles:
                counts["drops"] += 1
                continue
            ref = ref_alleles[pos]["ref"]
            alt = ref_alleles[pos]["alt"]

            gt = self._encode_genotype(a1, a2, ref, alt)
            if gt is None:
                counts["drops"] += 1
                continue

            lines.append(f"{chrom}\t{pos}\t{rsid}\t{ref}\t{alt}\t.\tPASS\t.\tGT\t{gt}\n")
            counts["hits"] += 1

        if not any(counts["hits"] for counts in per_source.values()):
            return per_source

        # Write compressed VCF using pysam's BGZFile
        with pysam.BGZFile(str(vcf_path), "wb") as bgz:
            bgz.write("".join(lines).encode())

        # Index with tabix
        pysam.tabix_index(str(vcf_path), preset="vcf", force=True)
        return per_source

    def _get_ref_alleles_pysam(self, chrom: str) -> dict[int, dict[str, str]]:
        """Extract REF/ALT alleles from the reference panel using pysam."""
        import pysam

        chr_num = chrom.replace("chr", "")
        ref_vcf_path = self.bundle / "phasing_panel" / f"ref_panel_chr{chr_num}.vcf.gz"

        alleles: dict[int, dict[str, str]] = {}
        try:
            with pysam.VariantFile(str(ref_vcf_path)) as vcf:
                for rec in vcf:
                    if rec.alts:
                        alleles[rec.pos] = {"ref": rec.ref, "alt": rec.alts[0]}
        except Exception:
            logger.exception("ref_allele_read_failed", chrom=chrom)
            raise

        return alleles

    @staticmethod
    def _encode_genotype(a1: str, a2: str, ref: str, alt: str) -> str | None:
        """Encode a diploid genotype as VCF GT field.

        Returns "0/0", "0/1", "1/1", or None if alleles don't match.
        """
        alleles = {ref: "0", alt: "1"}
        g1 = alleles.get(a1)
        g2 = alleles.get(a2)
        if g1 is None or g2 is None:
            return None
        gt_vals = sorted([g1, g2])
        return f"{gt_vals[0]}/{gt_vals[1]}"

    def _phase_chromosome(self, chr_num: int, vcf_path: Path, out: Path) -> Path | None:
        """Phase a single chromosome using Beagle."""
        beagle_jar = self.bundle / "beagle" / "beagle.jar"
        ref_panel = self.bundle / "phasing_panel" / f"ref_panel_chr{chr_num}.vcf.gz"
        gen_map = self.bundle / "genetic_maps" / f"plink.chrchr{chr_num}.GRCh38.map"
        out_prefix = out / "phased" / f"phased_chr{chr_num}"

        (out / "phased").mkdir(exist_ok=True)

        cmd = [
            "java",
            f"-Xmx{self.java_mem}",
            "-jar",
            str(beagle_jar),
            f"gt={vcf_path}",
            f"ref={ref_panel}",
            f"map={gen_map}",
            f"out={out_prefix}",
        ]

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
            if result.returncode != 0:
                logger.error("beagle_failed", chrom=chr_num, stderr=result.stderr[-500:])
                return None
        except subprocess.TimeoutExpired:
            logger.error("beagle_timeout", chrom=chr_num)
            return None

        phased_vcf = Path(f"{out_prefix}.vcf.gz")
        if not (phased_vcf.exists() and phased_vcf.stat().st_size > 0):
            logger.error("beagle_no_output", chrom=chr_num)
            return None

        return self._postprocess_phased_vcf(phased_vcf, f"chr{chr_num}")

    def _postprocess_phased_vcf(self, phased_vcf: Path, chrom: str) -> Path:
        """Ensure phased VCF has a contig header and a tabix index.

        Beagle 5's output omits ``##contig`` lines, which makes pysam emit
        "contig not defined in header" warnings on every record. We rewrite
        the file with the contig declared and then tabix-index it so pysam
        can use random access cleanly.
        """
        import pysam

        with pysam.VariantFile(str(phased_vcf)) as vin:
            header = vin.header
            if chrom not in header.contigs:
                header.contigs.add(chrom)
            fixed = phased_vcf.with_suffix(".fixed.vcf.gz")
            with pysam.VariantFile(str(fixed), "wz", header=header) as vout:
                for rec in vin:
                    vout.write(rec)

        fixed.replace(phased_vcf)
        pysam.tabix_index(str(phased_vcf), preset="vcf", force=True)
        return phased_vcf

    def _parse_phased_vcf(
        self,
        vcf_path: Path,
        snp_pos: np.ndarray,
        snp_ref: np.ndarray,
        snp_alt: np.ndarray,
        *,
        allow_below_minimum_for_diagnostics: bool = False,
    ) -> PhasedVCFParseResult:
        """Parse a phased VCF and extract haplotype vectors aligned to model SNPs.

        Returns two haplotype arrays (hap0, hap1) of shape (n_snps,) plus
        model-marker alignment telemetry. Missing sites are encoded as 0
        (reference).

        ``allow_below_minimum_for_diagnostics`` exists only so calibration can
        measure the failure region. Its default preserves the production hard
        failure below 5% model-marker matching.
        """
        import pysam

        n_snps = len(snp_pos)
        hap0 = np.zeros(n_snps, dtype=np.int8)
        hap1 = np.zeros(n_snps, dtype=np.int8)

        # Build position lookup for fast matching
        pos_to_idx: dict[int, int] = {}
        for i, pos in enumerate(snp_pos):
            pos_to_idx[int(pos)] = i

        matched = 0
        allele_mismatch = 0
        try:
            with pysam.VariantFile(str(vcf_path)) as vcf:
                for rec in vcf:
                    idx = pos_to_idx.get(rec.pos)
                    if idx is None:
                        continue
                    if not rec.alts:
                        continue
                    if rec.ref != str(snp_ref[idx]) or rec.alts[0] != str(snp_alt[idx]):
                        allele_mismatch += 1
                        continue

                    sample = rec.samples[0]
                    gt = sample["GT"]
                    if gt is None or len(gt) < 2:
                        continue
                    hap0[idx] = int(gt[0]) if gt[0] is not None else 0
                    hap1[idx] = int(gt[1]) if gt[1] is not None else 0
                    matched += 1
        except Exception:
            logger.exception("phased_vcf_parse_failed", path=str(vcf_path))
            raise

        match_rate = matched / n_snps if n_snps else 0.0
        log = logger.warning if match_rate < 0.5 else logger.info
        log(
            "phased_vcf_parsed",
            path=str(vcf_path),
            matched=matched,
            n_snps=n_snps,
            match_rate=round(match_rate, 4),
            allele_mismatch=allele_mismatch,
        )
        model_marker_counts: ModelMarkerCounts = {
            "matched": matched,
            "total": n_snps,
            "allele_mismatch": allele_mismatch,
            "match_rate": round(match_rate, 6),
        }
        if match_rate < 0.05 and not allow_below_minimum_for_diagnostics:
            raise RuntimeError(
                f"Phased VCF {vcf_path.name} matched only {matched}/{n_snps} "
                f"({match_rate:.1%}) model markers — inference would be meaningless. "
                "Check that Beagle imputation against the reference panel is enabled."
            )

        return PhasedVCFParseResult(
            hap0=hap0,
            hap1=hap1,
            model_marker_counts=model_marker_counts,
        )

    def _compute_global_ancestry(
        self, chrom_results: dict[int, ChromosomeResult]
    ) -> dict[str, dict]:
        """Compute genome-wide ancestry proportions from chromosome results.

        Also computes per-population confidence as the mean softmax
        probability for windows assigned to each population, and flags
        MID with a warning when its proportion is below 15%.
        """
        from backend.analysis.gnomix_inference import CANONICAL_POPULATIONS

        pop_windows: dict[str, int] = defaultdict(int)
        # Accumulate softmax probabilities for confidence calculation
        pop_prob_sums: dict[str, float] = defaultdict(float)
        total_windows = 0

        n_pops = len(CANONICAL_POPULATIONS)
        for chr_num, result in chrom_results.items():
            for w in range(result.n_windows):
                h0_idx = int(result.hap0_ancestry[w])
                h1_idx = int(result.hap1_ancestry[w])
                if not (0 <= h0_idx < n_pops) or not (0 <= h1_idx < n_pops):
                    logger.warning(
                        "invalid_ancestry_index",
                        chrom=chr_num,
                        window=w,
                        h0=h0_idx,
                        h1=h1_idx,
                    )
                    continue
                h0_pop = CANONICAL_POPULATIONS[h0_idx]
                h1_pop = CANONICAL_POPULATIONS[h1_idx]
                pop_windows[h0_pop] += 1
                pop_windows[h1_pop] += 1
                # Sum the softmax probability for the assigned population
                pop_prob_sums[h0_pop] += float(result.hap0_probs[w, h0_idx])
                pop_prob_sums[h1_pop] += float(result.hap1_probs[w, h1_idx])
                total_windows += 2

        if total_windows == 0:
            return {}

        ancestry: dict[str, dict] = {}
        for pop in sorted(POPULATIONS.keys()):
            n_wins = pop_windows.get(pop, 0)
            frac = n_wins / total_windows
            # Per-population confidence: mean softmax probability across
            # windows assigned to this population (0–1 scale).
            confidence = pop_prob_sums.get(pop, 0.0) / n_wins if n_wins > 0 else 0.0
            entry: dict = {
                "fraction": round(frac, 4),
                "percentage": round(frac * 100, 1),
                "display_name": POPULATIONS[pop]["display"],
                "color": POPULATIONS[pop]["color"],
                "confidence": round(confidence, 4),
            }
            # Flag MID with lower-precision warning when proportion is low
            if pop == "MID" and frac < MID_LOW_PRECISION_THRESHOLD:
                entry["warning"] = (
                    "Middle Eastern ancestry estimates have lower precision "
                    "with current reference panel"
                )
            ancestry[pop] = entry

        return ancestry

    def _build_chromosome_painting(
        self, chrom_results: dict[int, ChromosomeResult]
    ) -> dict[str, list[dict]]:
        """Build chromosome painting data structure for visualization."""
        from backend.analysis.gnomix_inference import CANONICAL_POPULATIONS

        painting: dict[str, list[dict]] = {}

        for chr_num in sorted(chrom_results.keys()):
            result = chrom_results[chr_num]
            segments: list[dict] = []

            n_pops = len(CANONICAL_POPULATIONS)
            for w in range(result.n_windows):
                h0_idx = int(result.hap0_ancestry[w])
                h1_idx = int(result.hap1_ancestry[w])
                if not (0 <= h0_idx < n_pops) or not (0 <= h1_idx < n_pops):
                    continue
                h0_pop = CANONICAL_POPULATIONS[h0_idx]
                h1_pop = CANONICAL_POPULATIONS[h1_idx]
                start_pos, end_pos = result.window_positions[w]

                segments.append(
                    {
                        "start": start_pos,
                        "end": end_pos,
                        "n_snps": 0,  # not tracked per-window in this implementation
                        "hap0": h0_pop,
                        "hap1": h1_pop,
                        "hap0_color": POPULATIONS.get(h0_pop, {}).get("color", "#999"),
                        "hap1_color": POPULATIONS.get(h1_pop, {}).get("color", "#999"),
                    }
                )

            painting[f"chr{chr_num}"] = segments

        return painting

    def _cleanup(self, out: Path) -> None:
        """Remove intermediate files, keep only final results."""
        for subdir in ["unphased_vcfs", "phased"]:
            path = out / subdir
            if path.exists():
                shutil.rmtree(path)
                logger.info("lai_cleanup", path=str(path))

"""Focused tests for versioned LAI runtime coverage instrumentation."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from backend.analysis.gnomix_inference import CANONICAL_POPULATIONS, ChromosomeResult
from backend.analysis.lai_runner import (
    LAI_COVERAGE_METRICS_SCHEMA_VERSION,
    LAIRunner,
    PhasedVCFParseResult,
)


def _bare_runner(bundle: Path) -> LAIRunner:
    runner = LAIRunner.__new__(LAIRunner)
    runner.bundle = bundle
    runner.java_mem = "1g"
    runner.rsid_lookup = {}
    return runner


def _write_phased_vcf(path: Path, records: list[str]) -> None:
    path.write_text(
        "".join(
            [
                "##fileformat=VCFv4.2\n",
                "##contig=<ID=chr1>\n",
                '##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">\n',
                "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tSAMPLE\n",
                *records,
            ]
        ),
        encoding="utf-8",
    )


class TestPhasedVCFCoverageTelemetry:
    def test_returns_alignment_counts_and_preserves_two_value_unpacking(
        self, tmp_path: Path
    ) -> None:
        vcf_path = tmp_path / "phased_chr1.vcf"
        _write_phased_vcf(
            vcf_path,
            [
                "chr1\t100\trs1\tA\tG\t.\tPASS\t.\tGT\t0|1\n",
                "chr1\t200\trs2\tA\tT\t.\tPASS\t.\tGT\t1|0\n",
                "chr1\t999\trs999\tC\tT\t.\tPASS\t.\tGT\t0|1\n",
            ],
        )
        runner = _bare_runner(tmp_path)

        parsed = runner._parse_phased_vcf(
            vcf_path,
            np.array([100, 200, 300, 400], dtype=np.int64),
            np.array(["A", "C", "G", "T"]),
            np.array(["G", "T", "A", "C"]),
        )

        assert parsed.model_marker_counts == {
            "matched": 1,
            "total": 4,
            "allele_mismatch": 1,
            "match_rate": 0.25,
        }
        hap0, hap1 = parsed
        np.testing.assert_array_equal(hap0, [0, 0, 0, 0])
        np.testing.assert_array_equal(hap1, [1, 0, 0, 0])

    def test_below_five_percent_still_fails_unless_diagnostic_switch_is_set(
        self, tmp_path: Path
    ) -> None:
        vcf_path = tmp_path / "phased_chr1.vcf"
        _write_phased_vcf(
            vcf_path,
            ["chr1\t100\trs1\tA\tG\t.\tPASS\t.\tGT\t0|1\n"],
        )
        runner = _bare_runner(tmp_path)
        positions = np.arange(100, 121, dtype=np.int64)
        refs = np.full(21, "A")
        alts = np.full(21, "G")

        with pytest.raises(RuntimeError, match=r"matched only 1/21 \(4\.8%\)"):
            runner._parse_phased_vcf(vcf_path, positions, refs, alts)

        parsed = runner._parse_phased_vcf(
            vcf_path,
            positions,
            refs,
            alts,
            allow_below_minimum_for_diagnostics=True,
        )
        assert parsed.model_marker_counts["matched"] == 1
        assert parsed.model_marker_counts["total"] == 21
        assert parsed.model_marker_counts["match_rate"] == round(1 / 21, 6)


class TestLAICoverageMetricSchema:
    def test_reads_expected_denominators_for_all_bundle_autosomes(self, tmp_path: Path) -> None:
        runner = _bare_runner(tmp_path)
        for chrom in range(1, 23):
            model_dir = tmp_path / "gnomix_models" / f"chr{chrom}"
            model_dir.mkdir(parents=True)
            np.savez(
                model_dir / "metadata.npz",
                snp_pos=np.arange(chrom + 2, dtype=np.int64),
                W=np.array(chrom),
            )

        marker_totals, expected_windows = runner._read_model_coverage_denominators()

        assert set(marker_totals) == set(range(1, 23))
        assert marker_totals[1] == 3
        assert marker_totals[22] == 24
        assert expected_windows[1] == 2
        assert expected_windows[22] == 44

    def test_unreadable_placeholder_metadata_uses_zero_denominators(self, tmp_path: Path) -> None:
        """Legacy test/stub bundles may carry empty metadata placeholders."""
        runner = _bare_runner(tmp_path)
        model_dir = tmp_path / "gnomix_models" / "chr1"
        model_dir.mkdir(parents=True)
        (model_dir / "metadata.npz").touch()

        marker_totals, expected_windows = runner._read_model_coverage_denominators()

        assert marker_totals == {chrom: 0 for chrom in range(1, 23)}
        assert expected_windows == {chrom: 0 for chrom in range(1, 23)}

    def test_schema_counts_missing_chromosomes_in_window_denominator(self) -> None:
        result = ChromosomeResult(
            chrom=1,
            n_windows=3,
            hap0_ancestry=np.array([0, 1, 99], dtype=np.int32),
            hap1_ancestry=np.array([2, -1, 3], dtype=np.int32),
            hap0_probs=np.zeros((3, len(CANONICAL_POPULATIONS))),
            hap1_probs=np.zeros((3, len(CANONICAL_POPULATIONS))),
            window_positions=[(1, 2), (3, 4), (5, 6)],
        )
        model_counts = {
            chrom: {
                "matched": 5 if chrom == 1 else (2 if chrom == 2 else 0),
                "total": 10 if chrom == 1 else (20 if chrom == 2 else 0),
                "allele_mismatch": 1 if chrom == 1 else (3 if chrom == 2 else 0),
                "match_rate": 0.0,
            }
            for chrom in range(1, 23)
        }

        metrics = LAIRunner._build_lai_coverage_metrics(
            emitted_total=12,
            emitted_by_autosome={1: 7, 2: 5},
            model_markers_by_autosome=model_counts,
            phased_autosomes=[1, 2],
            chrom_results={1: result},
            expected_haplotype_windows_by_autosome={chrom: 10 for chrom in range(1, 23)},
            per_source={"ancestrydna": {"hits": 12, "drops": 4}},
        )

        assert metrics["schema_version"] == LAI_COVERAGE_METRICS_SCHEMA_VERSION
        assert metrics["emitted_markers"]["total"] == 12
        assert metrics["emitted_markers"]["by_autosome"]["1"] == 7
        assert metrics["emitted_markers"]["by_autosome"]["22"] == 0
        assert metrics["model_markers"]["aggregate"] == {
            "matched": 7,
            "total": 30,
            "allele_mismatch": 4,
            "match_rate": round(7 / 30, 6),
        }
        assert metrics["phased_autosomes"] == {"count": 2, "identities": [1, 2]}
        assert metrics["analyzed_autosomes"] == {"count": 1, "identities": [1]}
        windows = metrics["haplotype_windows"]
        assert windows["expected"] == 220
        assert windows["expected_by_autosome"]["22"] == 10
        assert windows["valid_assigned"] == 4
        assert windows["valid_assigned_by_autosome"]["1"] == 4
        assert windows["valid_assigned_by_autosome"]["2"] == 0
        assert windows["assignment_rate"] == round(4 / 220, 6)
        assert metrics["per_source"] == {"ancestrydna": {"hits": 12, "drops": 4}}

    def test_progressive_callback_retains_metrics_before_early_failure(
        self, tmp_path: Path
    ) -> None:
        runner = _bare_runner(tmp_path)
        snapshots: list[dict] = []

        with (
            patch.object(
                LAIRunner,
                "_read_model_coverage_denominators",
                return_value=(
                    {chrom: 10 for chrom in range(1, 23)},
                    {chrom: 4 for chrom in range(1, 23)},
                ),
            ),
            patch.object(
                LAIRunner,
                "_write_per_chrom_vcfs",
                return_value=({}, 0, {"": {"hits": 0, "drops": 1}}),
            ),
            pytest.raises(RuntimeError, match="no usable markers remained"),
        ):
            runner.run(
                genotypes=[{"rsid": "rs1", "chrom": "1", "genotype": "AA"}],
                output_dir=tmp_path,
                cleanup=False,
                file_format="23andme_v5",
                diagnostic_metrics_callback=snapshots.append,
            )

        assert len(snapshots) == 1
        assert snapshots[0]["schema_version"] == LAI_COVERAGE_METRICS_SCHEMA_VERSION
        assert snapshots[0]["emitted_markers"]["total"] == 0
        assert snapshots[0]["haplotype_windows"]["expected"] == 88
        assert snapshots[0]["per_source"] == {"23andme": {"hits": 0, "drops": 1}}

    def test_progressive_callback_retains_parse_metrics_when_inference_fails(
        self, tmp_path: Path
    ) -> None:
        runner = _bare_runner(tmp_path)
        model = SimpleNamespace(
            snp_pos=np.array([100, 200], dtype=np.int64),
            snp_ref=np.array(["A", "C"]),
            snp_alt=np.array(["G", "T"]),
        )
        parsed = PhasedVCFParseResult(
            hap0=np.array([0, 1], dtype=np.int8),
            hap1=np.array([1, 0], dtype=np.int8),
            model_marker_counts={
                "matched": 2,
                "total": 2,
                "allele_mismatch": 0,
                "match_rate": 1.0,
            },
        )
        snapshots: list[dict] = []

        with (
            patch.object(
                LAIRunner,
                "_read_model_coverage_denominators",
                return_value=(
                    {chrom: 2 for chrom in range(1, 23)},
                    {chrom: 4 for chrom in range(1, 23)},
                ),
            ),
            patch.object(
                LAIRunner,
                "_write_per_chrom_vcfs",
                return_value=(
                    {"chr1": tmp_path / "user_chr1.vcf.gz"},
                    2,
                    {"": {"hits": 2, "drops": 0}},
                ),
            ),
            patch.object(
                LAIRunner,
                "_phase_chromosome",
                return_value=tmp_path / "phased_chr1.vcf.gz",
            ),
            patch.object(LAIRunner, "_parse_phased_vcf", return_value=parsed),
            patch("backend.analysis.gnomix_inference.load_gnomix_model", return_value=model),
            patch(
                "backend.analysis.gnomix_inference.run_inference",
                side_effect=RuntimeError("diagnostic inference failure"),
            ),
            pytest.raises(RuntimeError, match="Too many chromosomes failed inference"),
        ):
            runner.run(
                genotypes=[{"rsid": "rs1", "chrom": "1", "genotype": "AA"}],
                output_dir=tmp_path,
                cleanup=False,
                file_format="23andme_v5",
                diagnostic_metrics_callback=snapshots.append,
            )

        final_snapshot = snapshots[-1]
        assert final_snapshot["model_markers"]["by_autosome"]["1"]["matched"] == 2
        assert final_snapshot["phased_autosomes"] == {"count": 1, "identities": [1]}
        assert final_snapshot["analyzed_autosomes"] == {"count": 0, "identities": []}
        assert final_snapshot["haplotype_windows"]["valid_assigned"] == 0

    def test_successful_run_persists_final_metrics_in_metadata_and_json(
        self, tmp_path: Path
    ) -> None:
        runner = _bare_runner(tmp_path)
        model = SimpleNamespace(
            snp_pos=np.array([100, 200], dtype=np.int64),
            snp_ref=np.array(["A", "C"]),
            snp_alt=np.array(["G", "T"]),
        )
        chrom_result = ChromosomeResult(
            chrom=1,
            n_windows=2,
            hap0_ancestry=np.array([0, 1], dtype=np.int32),
            hap1_ancestry=np.array([2, 3], dtype=np.int32),
            hap0_probs=np.ones((2, len(CANONICAL_POPULATIONS))),
            hap1_probs=np.ones((2, len(CANONICAL_POPULATIONS))),
            window_positions=[(100, 150), (151, 200)],
        )

        def fake_write(*_args: object, **kwargs: object):
            emitted = kwargs["emitted_markers_by_autosome"]
            assert isinstance(emitted, dict)
            emitted[1] = 2
            return {"chr1": tmp_path / "user_chr1.vcf.gz"}, 2, {"": {"hits": 2, "drops": 1}}

        parsed = PhasedVCFParseResult(
            hap0=np.array([0, 1], dtype=np.int8),
            hap1=np.array([1, 0], dtype=np.int8),
            model_marker_counts={
                "matched": 2,
                "total": 2,
                "allele_mismatch": 0,
                "match_rate": 1.0,
            },
        )
        snapshots: list[dict] = []

        with (
            patch.object(
                LAIRunner,
                "_read_model_coverage_denominators",
                return_value=(
                    {chrom: 2 for chrom in range(1, 23)},
                    {chrom: 4 for chrom in range(1, 23)},
                ),
            ),
            patch.object(LAIRunner, "_write_per_chrom_vcfs", side_effect=fake_write),
            patch.object(
                LAIRunner,
                "_phase_chromosome",
                return_value=tmp_path / "phased_chr1.vcf.gz",
            ),
            patch.object(LAIRunner, "_parse_phased_vcf", return_value=parsed),
            patch("backend.analysis.gnomix_inference.load_gnomix_model", return_value=model),
            patch(
                "backend.analysis.gnomix_inference.run_inference",
                return_value=chrom_result,
            ),
        ):
            result = runner.run(
                genotypes=[{"rsid": "rs1", "chrom": "1", "genotype": "AA"}],
                output_dir=tmp_path,
                cleanup=False,
                file_format="23andme_v5",
                diagnostic_metrics_callback=snapshots.append,
            )

        metrics = result.metadata["lai_coverage_metrics"]
        assert metrics["emitted_markers"]["by_autosome"]["1"] == 2
        assert metrics["model_markers"]["by_autosome"]["1"]["matched"] == 2
        assert metrics["phased_autosomes"] == {"count": 1, "identities": [1]}
        assert metrics["analyzed_autosomes"] == {"count": 1, "identities": [1]}
        assert metrics["haplotype_windows"]["expected"] == 88
        assert metrics["haplotype_windows"]["valid_assigned"] == 4
        assert snapshots[-1] == metrics
        stored = json.loads((tmp_path / "lai_results.json").read_text(encoding="utf-8"))
        assert stored["metadata"]["lai_coverage_metrics"] == metrics


def test_run_lai_analysis_forwards_diagnostic_options(tmp_path: Path) -> None:
    """The public entry point exposes the calibration runner options by keyword."""
    from backend.analysis.lai import run_lai_analysis

    runner_result = SimpleNamespace(
        global_ancestry={"EUR": {"fraction": 1.0}},
        chromosome_painting={},
        metadata={"lai_coverage_metrics": {"schema_version": 1}},
    )
    runner = MagicMock()
    runner.run.return_value = runner_result
    callback = MagicMock()
    settings = SimpleNamespace(
        resolved_lai_bundle_path=tmp_path,
        data_dir=tmp_path,
        lai_java_mem="1g",
    )

    with (
        patch("backend.analysis.lai.get_settings", return_value=settings),
        patch("backend.analysis.lai.validate_lai_bundle", return_value=True),
        patch("backend.analysis.lai.detect_java", return_value=True),
        patch("backend.analysis.lai._ensure_lai_tables"),
        patch("backend.analysis.lai._read_sample_file_format", return_value="23andme_v5"),
        patch(
            "backend.analysis.lai._read_sample_genotypes",
            return_value=[{"rsid": "rs1", "chrom": "1", "genotype": "AA"}],
        ),
        patch("backend.analysis.lai._store_lai_results"),
        patch("backend.analysis.lai_runner.LAIRunner", return_value=runner),
    ):
        result = run_lai_analysis(
            sample_id=7,
            sample_engine=MagicMock(),
            diagnostic_metrics_callback=callback,
            allow_below_minimum_for_diagnostics=True,
        )

    assert result.metadata == runner_result.metadata
    runner.run.assert_called_once_with(
        genotypes=[{"rsid": "rs1", "chrom": "1", "genotype": "AA"}],
        output_dir=str(tmp_path / "lai_work" / "sample_7"),
        progress_callback=None,
        cleanup=True,
        file_format="23andme_v5",
        diagnostic_metrics_callback=callback,
        allow_below_minimum_for_diagnostics=True,
    )

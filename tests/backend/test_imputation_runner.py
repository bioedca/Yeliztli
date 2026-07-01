"""Local Beagle phase+impute runtime (SW-C2).

Validates the Beagle command construction, the DR2/IMP parser (against the real
Beagle 5.5 output format verified by a live run), the quality summary, and the
impute_chromosome lifecycle (success / Beagle-failure / timeout / missing panel),
with subprocess.run mocked so no real Beagle/JVM is invoked.
"""

from __future__ import annotations

import gzip
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

import backend.analysis.imputation_runner as ir_mod
from backend.analysis.imputation_runner import (
    WELL_IMPUTED_DR2,
    ImputationRunner,
    _normalize_chrom,
    beagle_jar_path,
    parse_imputed_vcf,
    summarize_dr2,
)

# Real Beagle 5.5 output shape (verified via a live imputation run 2026-06-26):
# DR2 / target-sample AF are Number=A (per-ALT); IMP flags ref-only (imputed)
# markers; FORMAT GT:DS. Target-sample AF is intentionally not parsed into the
# population-AF field used by the firewall.
_IMPUTED_VCF = (
    "##fileformat=VCFv4.2\n"
    '##INFO=<ID=AF,Number=A,Type=Float,Description="Estimated ALT Allele Frequencies">\n'
    '##INFO=<ID=DR2,Number=A,Type=Float,Description="Dosage R-Squared">\n'
    '##INFO=<ID=IMP,Number=0,Type=Flag,Description="Imputed marker">\n'
    '##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">\n'
    '##FORMAT=<ID=DS,Number=A,Type=Float,Description="estimated ALT dose">\n'
    "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tS1\n"
    "22\t20000146\trs1\tG\tA\t.\tPASS\tDR2=0.95;AF=0.01;IMP\tGT:DS\t0|0:0\n"
    "22\t20000428\trs2\tG\tT\t.\tPASS\tDR2=1.00;AF=0.12\tGT:DS\t0|1:1\n"
    "22\t20000500\trs3\tC\tA,T\t.\tPASS\tDR2=0.70,0.30;AF=0.05,0.02;IMP\tGT:DS\t1|2:1,1\n"
    "22\t20000600\trs4\tA\tG\t.\tPASS\tDR2=0.50;AF=0.20;IMP\tGT:DS\t0|0:0\n"
)


def _write_gz(path: Path, text: str) -> Path:
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        fh.write(text)
    return path


def _stub_runner(tmp_path: Path, *, chrom: str = "22") -> ImputationRunner:
    """A runner with a stub Beagle JAR + stub panel files for ``chrom``."""
    jar = tmp_path / "lai" / "beagle" / "beagle.jar"
    jar.parent.mkdir(parents=True)
    jar.touch()
    panel = tmp_path / "panel"
    panel.mkdir()
    (panel / f"chr{chrom}.1kg.phase3.v5a.b37.bref3").touch()
    (panel / f"plink.chr{chrom}.GRCh37.map").touch()
    return ImputationRunner(panel, jar, java_mem="6g")


class TestParse:
    def test_parses_dr2_imp_and_ignores_target_af(self, tmp_path: Path) -> None:
        vcf = _write_gz(tmp_path / "imp.vcf.gz", _IMPUTED_VCF)
        variants = list(parse_imputed_vcf(vcf))
        # rs1, rs2, rs3(A), rs3(T), rs4 → 5 records (rs3 is multi-allelic).
        assert len(variants) == 5
        by_id = {(v.pos, v.alt): v for v in variants}
        assert by_id[(20000146, "A")].imputed is True
        assert by_id[(20000146, "A")].dr2 == 0.95
        assert by_id[(20000146, "A")].af is None
        assert by_id[(20000428, "T")].imputed is False  # typed marker (no IMP)
        # Multi-allelic: per-ALT DR2 aligns to each ALT; target AF stays ignored.
        assert by_id[(20000500, "A")].dr2 == 0.70
        assert by_id[(20000500, "T")].dr2 == 0.30
        assert by_id[(20000500, "A")].af is None
        assert by_id[(20000500, "T")].af is None
        assert by_id[(20000500, "T")].imputed is True

    def test_parses_per_alt_ds_dosage(self, tmp_path: Path) -> None:
        vcf = _write_gz(tmp_path / "imp.vcf.gz", _IMPUTED_VCF)
        by_id = {(v.pos, v.alt): v for v in parse_imputed_vcf(vcf)}
        # Sample col DS (GT:DS): rs1 "0|0:0" → 0; rs2 "0|1:1" → 1.
        assert by_id[(20000146, "A")].dosage == 0.0
        assert by_id[(20000428, "T")].dosage == 1.0
        # Multi-allelic rs3 "1|2:1,1" → DS aligns per ALT.
        assert by_id[(20000500, "A")].dosage == 1.0
        assert by_id[(20000500, "T")].dosage == 1.0

    def test_parses_per_alt_gt_best_guess_copies(self, tmp_path: Path) -> None:
        vcf = _write_gz(tmp_path / "imp.vcf.gz", _IMPUTED_VCF)
        by_id = {(v.pos, v.alt): v for v in parse_imputed_vcf(vcf)}
        assert by_id[(20000146, "A")].best_guess_copies == 0
        assert by_id[(20000428, "T")].best_guess_copies == 1
        # Multi-allelic rs3 "1|2" carries one copy of each ALT.
        assert by_id[(20000500, "A")].best_guess_copies == 1
        assert by_id[(20000500, "T")].best_guess_copies == 1

    def test_dosage_none_when_no_ds_or_out_of_range(self, tmp_path: Path) -> None:
        vcf = (
            "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tS1\n"
            "22\t100\trsx\tA\tG\t.\tPASS\tDR2=0.9;AF=0.2;IMP\tGT\t0|1\n"  # no DS field
            "22\t200\trsy\tA\tG\t.\tPASS\tDR2=0.9;AF=0.2;IMP\tGT:DS\t0|1:2.5\n"  # DS>2 dropped
        )
        by_pos = {v.pos: v for v in parse_imputed_vcf(_write_gz(tmp_path / "nf.vcf.gz", vcf))}
        assert by_pos[100].dosage is None  # FORMAT has no DS
        assert by_pos[200].dosage is None  # 2.5 out of [0, 2]


class TestSummary:
    def test_counts_and_well_imputed(self, tmp_path: Path) -> None:
        vcf = _write_gz(tmp_path / "imp.vcf.gz", _IMPUTED_VCF)
        s = summarize_dr2(parse_imputed_vcf(vcf))
        assert s.n_total == 5
        assert s.n_imputed == 4  # rs1, rs3(A), rs3(T), rs4 — rs2 is typed
        # Only rs1 (0.95) clears DR2>=0.8 among the imputed.
        assert WELL_IMPUTED_DR2 == 0.8
        assert s.n_well_imputed == 1
        assert s.frac_well_imputed == pytest.approx(0.25)
        assert s.mean_imputed_dr2 == pytest.approx((0.95 + 0.70 + 0.30 + 0.50) / 4)

    def test_frac_none_when_no_imputed(self) -> None:
        assert summarize_dr2([]).frac_well_imputed is None


class TestCommand:
    def test_build_command_has_gt_ref_map_out(self, tmp_path: Path) -> None:
        runner = _stub_runner(tmp_path)
        runner.nthreads = 4
        cmd = runner._build_command("22", Path("/in/chr22.vcf.gz"), tmp_path / "imputed_chr22")
        assert cmd[0] == "java" and "-Xmx6g" in cmd and "-jar" in cmd
        joined = " ".join(cmd)
        assert "gt=/in/chr22.vcf.gz" in joined
        assert "chr22.1kg.phase3.v5a.b37.bref3" in joined  # ref= panel bref3
        assert "plink.chr22.GRCh37.map" in joined  # map=
        assert "out=" in joined and "nthreads=4" in joined

    def test_build_command_accepts_x_region_interval(self, tmp_path: Path) -> None:
        runner = _stub_runner(tmp_path, chrom="X")
        cmd = runner._build_command(
            "X",
            Path("/in/chrX_PAR1.vcf.gz"),
            tmp_path / "imputed_chrX_PAR1",
            region="X:60001-2699520",
        )
        assert "chrX.1kg.phase3.v5a.b37.bref3" in " ".join(cmd)
        assert "chrom=X:60001-2699520" in cmd

    def test_missing_jar_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError, match="Beagle JAR"):
            ImputationRunner(tmp_path, tmp_path / "nope" / "beagle.jar")


class TestImputeChromosome:
    def _fake_run_writing(self, vcf_text: str, returncode: int = 0):
        def fake(cmd, **_kw):  # noqa: ANN001, ANN202
            out_prefix = next(a.split("=", 1)[1] for a in cmd if a.startswith("out="))
            if returncode == 0:
                _write_gz(Path(out_prefix + ".vcf.gz"), vcf_text)
            return SimpleNamespace(returncode=returncode, stdout="", stderr="boom")

        return fake

    def test_success_times_and_parses(self, tmp_path: Path, monkeypatch) -> None:
        runner = _stub_runner(tmp_path)
        monkeypatch.setattr(ir_mod.subprocess, "run", self._fake_run_writing(_IMPUTED_VCF))
        res = runner.impute_chromosome("22", tmp_path / "in.vcf.gz", tmp_path / "out")
        assert res.return_ok is True
        assert res.output_vcf is not None and res.output_vcf.exists()
        assert res.n_total == 5 and res.n_imputed == 4
        assert res.runtime_seconds >= 0.0

    def test_x_region_success_uses_region_and_output_label(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        seen_cmds: list[list[str]] = []

        def fake(cmd, **_kw):  # noqa: ANN001, ANN202
            seen_cmds.append(cmd)
            out_prefix = next(a.split("=", 1)[1] for a in cmd if a.startswith("out="))
            _write_gz(Path(out_prefix + ".vcf.gz"), _IMPUTED_VCF.replace("22\t", "X\t"))
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        runner = _stub_runner(tmp_path, chrom="X")
        monkeypatch.setattr(ir_mod.subprocess, "run", fake)

        res = runner.impute_chromosome(
            "X",
            tmp_path / "chrX_PAR1.vcf.gz",
            tmp_path / "out",
            region="X:60001-2699520",
            output_label="X_PAR1",
        )

        assert res.return_ok is True
        assert res.chrom == "X_PAR1"
        assert res.output_vcf is not None
        assert res.output_vcf.name == "imputed_chrX_PAR1.vcf.gz"
        assert "chrom=X:60001-2699520" in seen_cmds[0]

    def test_missing_panel_raises(self, tmp_path: Path) -> None:
        runner = _stub_runner(tmp_path)  # only chr22 panel files exist
        with pytest.raises(FileNotFoundError, match="panel files missing"):
            runner.impute_chromosome("21", tmp_path / "in.vcf.gz", tmp_path / "out")

    def test_beagle_failure_returns_not_ok(self, tmp_path: Path, monkeypatch) -> None:
        runner = _stub_runner(tmp_path)
        monkeypatch.setattr(ir_mod.subprocess, "run", self._fake_run_writing("", returncode=1))
        res = runner.impute_chromosome("22", tmp_path / "in.vcf.gz", tmp_path / "out")
        assert res.return_ok is False
        assert res.output_vcf is None
        assert "boom" in res.stderr_tail

    def test_no_output_returns_not_ok(self, tmp_path: Path, monkeypatch) -> None:
        # returncode 0 but no VCF written → still not ok.
        def fake(cmd, **_kw):  # noqa: ANN001, ANN202
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        runner = _stub_runner(tmp_path)
        monkeypatch.setattr(ir_mod.subprocess, "run", fake)
        res = runner.impute_chromosome("22", tmp_path / "in.vcf.gz", tmp_path / "out")
        assert res.return_ok is False

    def test_timeout_returns_not_ok(self, tmp_path: Path, monkeypatch) -> None:
        def fake(cmd, **_kw):  # noqa: ANN001, ANN202
            raise subprocess.TimeoutExpired(cmd, 1.0)

        runner = _stub_runner(tmp_path)
        monkeypatch.setattr(ir_mod.subprocess, "run", fake)
        res = runner.impute_chromosome("22", tmp_path / "in.vcf.gz", tmp_path / "out", timeout=1.0)
        assert res.return_ok is False
        assert res.stderr_tail == "timeout"


class TestJarPath:
    def test_beagle_jar_path(self, tmp_path: Path) -> None:
        assert beagle_jar_path(tmp_path) == tmp_path / "beagle" / "beagle.jar"


class TestChromValidation:
    def test_normalizes_valid_tokens(self) -> None:
        assert _normalize_chrom("chr22") == "22"
        assert _normalize_chrom("X") == "X"
        assert _normalize_chrom("chrx") == "X"
        assert _normalize_chrom(" 7 ") == "7"

    @pytest.mark.parametrize("bad", ["../etc", "22/x", "Y", "MT", "23", "0", ""])
    def test_rejects_unsupported_or_unsafe(self, bad: str) -> None:
        with pytest.raises(ValueError, match="unsupported chromosome"):
            _normalize_chrom(bad)

    def test_impute_chromosome_rejects_path_traversal(self, tmp_path: Path) -> None:
        # A chrom carrying a path separator must never reach panel/output paths.
        runner = _stub_runner(tmp_path)
        with pytest.raises(ValueError, match="unsupported chromosome"):
            runner.impute_chromosome("../../etc", tmp_path / "in.vcf.gz", tmp_path / "out")


class TestMalformedFloats:
    def test_nan_inf_dr2_becomes_none_and_target_af_ignored(self, tmp_path: Path) -> None:
        vcf = (
            "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"
            "22\t100\trsx\tA\tG\t.\tPASS\tDR2=nan;AF=inf;IMP\n"
        )
        [v] = list(parse_imputed_vcf(_write_gz(tmp_path / "nf.vcf.gz", vcf)))
        assert v.dr2 is None  # nan dropped
        assert v.af is None  # Beagle target-sample AF is ignored regardless of value.
        # A non-finite DR2 must not be counted as well-imputed.
        assert summarize_dr2([v]).n_well_imputed == 0

    def test_out_of_range_dr2_becomes_none_and_target_af_ignored(self, tmp_path: Path) -> None:
        # Finite but out-of-range DR2 must be dropped so a malformed value can't
        # inflate the summary or wrongly clear the well-imputed cutoff.
        vcf = (
            "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"
            "22\t100\trsx\tA\tG\t.\tPASS\tDR2=1.2;AF=-0.1;IMP\n"
        )
        [v] = list(parse_imputed_vcf(_write_gz(tmp_path / "oor.vcf.gz", vcf)))
        assert v.dr2 is None  # 1.2 > 1 dropped
        assert v.af is None  # Beagle target-sample AF is ignored regardless of value.
        assert summarize_dr2([v]).n_well_imputed == 0

"""GLIMPSE2 imputation engine seam (SW-C7).

Validates binary resolution / availability (graceful when absent), the chunk-file
parser, the chunk → phase → ligate orchestration (with subprocess mocked so no
real GLIMPSE2 is invoked), the failure / timeout / missing-output paths, and that
the parser maps INFO→quality and RAF→AF.
"""

from __future__ import annotations

import gzip
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

import backend.analysis.glimpse_runner as gr_mod
from backend.analysis.glimpse_runner import (
    REQUIRED_BINARIES,
    GlimpseRunner,
    glimpse_available,
    missing_binaries,
    parse_chunk_file,
    parse_glimpse_vcf,
    resolve_binary,
)
from backend.analysis.imputation_firewall import FirewallReason, assess_variant

# A 2-chunk chunk file (tab-separated: index, contig, input-region, output-region).
_CHUNKS = (
    "0\t22\t22:1-2500000\t22:1-2000000\t...\n1\t22\t22:1900000-5000000\t22:2000001-5000000\t...\n"
)

_LIGATED_VCF = (
    "##fileformat=VCFv4.2\n"
    '##INFO=<ID=RAF,Number=A,Type=Float,Description="ref panel AF">\n'
    '##INFO=<ID=AF,Number=A,Type=Float,Description="target AF">\n'
    '##INFO=<ID=INFO,Number=A,Type=Float,Description="IMPUTE info">\n'
    "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tS1\n"
    "22\t1000\trs1\tG\tA\t.\tPASS\tRAF=0.30;AF=0.50;INFO=0.95\tGT:DS:GP\t0|1:1:0,1,0\n"
    "22\t2000\trs2\tC\tT\t.\tPASS\tRAF=0.002;AF=0.50;INFO=0.90\tGT:DS:GP\t1|0:1:0,1,0\n"
    "22\t3000\trs3\tA\tG\t.\tPASS\tRAF=0.20;AF=0.0;INFO=0.40\tGT:DS:GP\t0|0:0:1,0,0\n"
)


def _stub_bin_dir(tmp_path: Path) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name in REQUIRED_BINARIES:
        p = bin_dir / name
        p.touch()
        p.chmod(0o755)  # resolution requires X_OK, not just existence
    return bin_dir


def _write_gz(path: Path, text: str) -> Path:
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        fh.write(text)
    return path


def _inputs(tmp_path: Path) -> tuple[Path, Path, Path]:
    gl = tmp_path / "sample.gl.vcf.gz"
    ref = tmp_path / "ref.vcf.gz"
    gmap = tmp_path / "chr22.gmap.gz"
    for p in (gl, ref, gmap):
        p.touch()
    return gl, ref, gmap


def _flag_values(cmd: list[str]) -> dict[str, str]:
    return {token: cmd[i + 1] for i, token in enumerate(cmd[:-1]) if token.startswith("--")}


class TestAvailability:
    def test_missing_when_absent(self, tmp_path: Path) -> None:
        empty = tmp_path / "empty"
        empty.mkdir()
        assert glimpse_available(empty) is False
        assert sorted(missing_binaries(empty)) == sorted(REQUIRED_BINARIES)

    def test_available_when_present(self, tmp_path: Path) -> None:
        bin_dir = _stub_bin_dir(tmp_path)
        assert glimpse_available(bin_dir) is True
        assert missing_binaries(bin_dir) == []

    def test_resolve_prefers_bin_dir(self, tmp_path: Path) -> None:
        bin_dir = _stub_bin_dir(tmp_path)
        assert resolve_binary("GLIMPSE2_phase", bin_dir) == bin_dir / "GLIMPSE2_phase"

    def test_non_executable_not_resolved(self, tmp_path: Path) -> None:
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        (bin_dir / "GLIMPSE2_phase").touch()  # exists but not executable
        # Not on PATH either → unresolved (a non-executable placeholder is ignored).
        assert resolve_binary("GLIMPSE2_phase", bin_dir) is None

    def test_constructor_raises_when_missing(self, tmp_path: Path) -> None:
        empty = tmp_path / "empty"
        empty.mkdir()
        with pytest.raises(FileNotFoundError, match="GLIMPSE2 binaries not found"):
            GlimpseRunner(bin_dir=empty)


class TestChunkParser:
    def test_parses_input_output_regions(self, tmp_path: Path) -> None:
        chunks = parse_chunk_file(_write_text(tmp_path / "c.txt", _CHUNKS))
        assert len(chunks) == 2
        assert chunks[0].input_region == "22:1-2500000"
        assert chunks[0].output_region == "22:1-2000000"
        assert chunks[1].output_region == "22:2000001-5000000"

    def test_skips_blank_and_comment(self, tmp_path: Path) -> None:
        text = "# header\n\n" + _CHUNKS
        assert len(parse_chunk_file(_write_text(tmp_path / "c.txt", text))) == 2

    def test_raises_on_short_line(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="malformed GLIMPSE2 chunk line"):
            parse_chunk_file(_write_text(tmp_path / "c.txt", "0\t22\t22:1-2\n"))


class TestParseGlimpseVcf:
    def test_info_to_quality_and_raf_to_af(self, tmp_path: Path) -> None:
        variants = list(parse_glimpse_vcf(_write_gz(tmp_path / "out.vcf.gz", _LIGATED_VCF)))
        assert len(variants) == 3
        by_pos = {v.pos: v for v in variants}
        assert by_pos[1000].dr2 == 0.95 and by_pos[1000].af == 0.30  # RAF, not AF=0.50
        assert all(v.imputed for v in variants)

    def test_firewall_consumes_glimpse_records(self, tmp_path: Path) -> None:
        by_pos = {
            v.pos: v for v in parse_glimpse_vcf(_write_gz(tmp_path / "o.vcf.gz", _LIGATED_VCF))
        }
        # rs1: info 0.95 >= 0.8 and MAF(0.30) >= 0.01 → passes.
        assert assess_variant(by_pos[1000]).reason == FirewallReason.IMPUTED_PASS
        # rs2: well-imputed but RAF MAF 0.002 < 0.01 → quarantined rare.
        assert assess_variant(by_pos[2000]).reason == FirewallReason.IMPUTED_RARE
        # rs3: info 0.40 < 0.8 → quarantined low quality.
        assert assess_variant(by_pos[3000]).reason == FirewallReason.LOW_DR2


class TestOrchestration:
    def _fake_run(self, *, fail_binary=None, timeout_binary=None, no_output_binary=None):
        def fake(cmd, **kw):  # noqa: ANN001, ANN202
            binary = Path(cmd[0]).name
            if timeout_binary and binary == timeout_binary:
                raise subprocess.TimeoutExpired(cmd, kw.get("timeout", 1.0))
            out = cmd[cmd.index("--output") + 1]
            rc = 1 if fail_binary == binary else 0
            if rc == 0 and no_output_binary != binary:
                if binary == "GLIMPSE2_chunk":
                    Path(out).write_text(_CHUNKS, encoding="utf-8")
                elif binary == "GLIMPSE2_phase":
                    Path(out).write_text("bcf", encoding="utf-8")
                elif binary == "GLIMPSE2_ligate":
                    _write_gz(Path(out), _LIGATED_VCF)
            return SimpleNamespace(returncode=rc, stdout="", stderr="boom")

        return fake

    def test_success_runs_all_three_steps(self, tmp_path: Path, monkeypatch) -> None:
        runner = GlimpseRunner(bin_dir=_stub_bin_dir(tmp_path), nthreads=4)
        gl, ref, gmap = _inputs(tmp_path)
        monkeypatch.setattr(gr_mod.subprocess, "run", self._fake_run())
        res = runner.impute_chromosome("chr22", gl, ref, gmap, tmp_path / "out")
        assert res.return_ok is True
        assert res.output_vcf is not None and res.output_vcf.exists()
        assert res.n_chunks == 2
        assert res.n_total == 3 and res.n_imputed == 3  # every marker imputed
        assert res.runtime_seconds >= 0.0

    def test_phase_command_has_expected_args(self, tmp_path: Path, monkeypatch) -> None:
        runner = GlimpseRunner(bin_dir=_stub_bin_dir(tmp_path), nthreads=2)
        gl, ref, gmap = _inputs(tmp_path)
        seen: list[list[str]] = []
        fake = self._fake_run()

        def capturing(cmd, **kw):  # noqa: ANN001, ANN202
            seen.append(cmd)
            return fake(cmd, **kw)

        monkeypatch.setattr(gr_mod.subprocess, "run", capturing)
        runner.impute_chromosome("22", gl, ref, gmap, tmp_path / "out")
        phase_cmds = [c for c in seen if Path(c[0]).name == "GLIMPSE2_phase"]
        assert len(phase_cmds) == 2  # one per chunk
        args = _flag_values(phase_cmds[0])
        assert args["--input-gl"] == str(gl)
        assert args["--reference"] == str(ref)
        assert args["--map"] == str(gmap)
        assert args["--input-region"] == "22:1-2500000"
        assert args["--output-region"] == "22:1-2000000"
        assert args["--threads"] == "2"

    def test_chunk_failure_returns_not_ok(self, tmp_path: Path, monkeypatch) -> None:
        runner = GlimpseRunner(bin_dir=_stub_bin_dir(tmp_path))
        gl, ref, gmap = _inputs(tmp_path)
        monkeypatch.setattr(gr_mod.subprocess, "run", self._fake_run(fail_binary="GLIMPSE2_chunk"))
        res = runner.impute_chromosome("22", gl, ref, gmap, tmp_path / "out")
        assert res.return_ok is False and res.output_vcf is None

    def test_phase_failure_returns_not_ok(self, tmp_path: Path, monkeypatch) -> None:
        runner = GlimpseRunner(bin_dir=_stub_bin_dir(tmp_path))
        gl, ref, gmap = _inputs(tmp_path)
        monkeypatch.setattr(gr_mod.subprocess, "run", self._fake_run(fail_binary="GLIMPSE2_phase"))
        res = runner.impute_chromosome("22", gl, ref, gmap, tmp_path / "out")
        assert res.return_ok is False
        assert res.n_chunks == 2  # chunked, then phase failed

    def test_ligate_timeout_returns_not_ok(self, tmp_path: Path, monkeypatch) -> None:
        runner = GlimpseRunner(bin_dir=_stub_bin_dir(tmp_path))
        gl, ref, gmap = _inputs(tmp_path)
        monkeypatch.setattr(
            gr_mod.subprocess, "run", self._fake_run(timeout_binary="GLIMPSE2_ligate")
        )
        res = runner.impute_chromosome("22", gl, ref, gmap, tmp_path / "out", timeout=1.0)
        assert res.return_ok is False and res.stderr_tail == "timeout"

    def test_ligate_no_output_returns_not_ok(self, tmp_path: Path, monkeypatch) -> None:
        runner = GlimpseRunner(bin_dir=_stub_bin_dir(tmp_path))
        gl, ref, gmap = _inputs(tmp_path)
        monkeypatch.setattr(
            gr_mod.subprocess, "run", self._fake_run(no_output_binary="GLIMPSE2_ligate")
        )
        res = runner.impute_chromosome("22", gl, ref, gmap, tmp_path / "out")
        assert res.return_ok is False

    def test_exec_oserror_returns_not_ok(self, tmp_path: Path, monkeypatch) -> None:
        runner = GlimpseRunner(bin_dir=_stub_bin_dir(tmp_path))
        gl, ref, gmap = _inputs(tmp_path)

        def boom(cmd, **kw):  # noqa: ANN001, ANN202
            raise PermissionError("cannot exec")

        monkeypatch.setattr(gr_mod.subprocess, "run", boom)
        res = runner.impute_chromosome("22", gl, ref, gmap, tmp_path / "out")
        assert res.return_ok is False and res.stderr_tail == "PermissionError"

    def test_unparseable_ligated_vcf_returns_not_ok(self, tmp_path: Path, monkeypatch) -> None:
        # ligate exits 0 but emits a multi-sample VCF → parse raises → failed run.
        multi_sample = (
            "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tS1\tS2\n"
            "22\t1\trs\tA\tG\t.\tPASS\tINFO=0.9;RAF=0.2\tGT:DS\t0|0:0\t0|1:1\n"
        )

        def fake(cmd, **kw):  # noqa: ANN001, ANN202
            binary = Path(cmd[0]).name
            out = cmd[cmd.index("--output") + 1]
            if binary == "GLIMPSE2_chunk":
                Path(out).write_text(_CHUNKS, encoding="utf-8")
            elif binary == "GLIMPSE2_phase":
                Path(out).write_text("bcf", encoding="utf-8")
            elif binary == "GLIMPSE2_ligate":
                _write_gz(Path(out), multi_sample)
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        runner = GlimpseRunner(bin_dir=_stub_bin_dir(tmp_path))
        gl, ref, gmap = _inputs(tmp_path)
        monkeypatch.setattr(gr_mod.subprocess, "run", fake)
        res = runner.impute_chromosome("22", gl, ref, gmap, tmp_path / "out")
        assert res.return_ok is False
        assert "failed to parse ligated VCF" in res.stderr_tail

    def test_missing_input_raises(self, tmp_path: Path) -> None:
        runner = GlimpseRunner(bin_dir=_stub_bin_dir(tmp_path))
        with pytest.raises(FileNotFoundError, match="GLIMPSE2 inputs missing"):
            runner.impute_chromosome(
                "22",
                tmp_path / "nope.vcf.gz",
                tmp_path / "no.ref",
                tmp_path / "no.map",
                tmp_path / "o",
            )

    def test_invalid_chrom_raises(self, tmp_path: Path) -> None:
        runner = GlimpseRunner(bin_dir=_stub_bin_dir(tmp_path))
        gl, ref, gmap = _inputs(tmp_path)
        with pytest.raises(ValueError, match="unsupported chromosome"):
            runner.impute_chromosome("../etc", gl, ref, gmap, tmp_path / "out")


def _write_text(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path

"""IMPUTE5 imputation engine seam (SW-C7).

Validates availability (graceful when absent), the INFO/AF parser, command
construction, region validation, and the success / failure / timeout / launch-
error / unparseable-output paths (with subprocess mocked so no real IMPUTE5 runs).
"""

from __future__ import annotations

import gzip
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

import backend.analysis.impute5_runner as i5_mod
from backend.analysis.imputation_firewall import FirewallReason, assess_variant
from backend.analysis.impute5_runner import (
    Impute5Runner,
    impute5_available,
    missing_binaries,
    parse_impute5_vcf,
)

_OUT_VCF = (
    "##fileformat=VCFv4.2\n"
    '##INFO=<ID=AF,Number=A,Type=Float,Description="ALT freq">\n'
    '##INFO=<ID=INFO,Number=A,Type=Float,Description="IMPUTE info">\n'
    "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tS1\n"
    "22\t1000\trs1\tG\tA\t.\tPASS\tAF=0.30;INFO=0.95\tGT:DS\t0|1:1\n"
    "22\t2000\trs2\tC\tT\t.\tPASS\tAF=0.002;INFO=0.90\tGT:DS\t1|0:1\n"
    "22\t3000\trs3\tA\tG\t.\tPASS\tAF=0.20;INFO=0.40\tGT:DS\t0|0:0\n"
)


def _exe(path: Path) -> Path:
    path.touch()
    path.chmod(0o755)
    return path


def _stub_bin_dir(tmp_path: Path) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _exe(bin_dir / "impute5")
    return bin_dir


def _write_gz(path: Path, text: str) -> Path:
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        fh.write(text)
    return path


def _inputs(tmp_path: Path) -> tuple[Path, Path, Path]:
    target = tmp_path / "target.phased.vcf.gz"
    ref = tmp_path / "ref.imp5"
    gmap = tmp_path / "chr22.gmap.gz"
    for p in (target, ref, gmap):
        p.touch()
    return target, ref, gmap


class TestAvailability:
    def test_unavailable_when_absent(self, tmp_path: Path) -> None:
        empty = tmp_path / "empty"
        empty.mkdir()
        assert impute5_available(empty) is False
        assert missing_binaries(empty) == ["impute5"]

    def test_available_when_present(self, tmp_path: Path) -> None:
        assert impute5_available(_stub_bin_dir(tmp_path)) is True

    def test_constructor_raises_when_missing(self, tmp_path: Path) -> None:
        empty = tmp_path / "empty"
        empty.mkdir()
        with pytest.raises(FileNotFoundError, match="IMPUTE5 binary not found"):
            Impute5Runner(bin_dir=empty)


class TestParse:
    def test_info_to_quality_and_af_all_imputed(self, tmp_path: Path) -> None:
        variants = list(parse_impute5_vcf(_write_gz(tmp_path / "o.vcf.gz", _OUT_VCF)))
        assert len(variants) == 3
        by_pos = {v.pos: v for v in variants}
        assert by_pos[1000].dr2 == 0.95 and by_pos[1000].af == 0.30
        assert all(v.imputed for v in variants)

    def test_firewall_consumes_impute5_records(self, tmp_path: Path) -> None:
        by_pos = {v.pos: v for v in parse_impute5_vcf(_write_gz(tmp_path / "o.vcf.gz", _OUT_VCF))}
        assert assess_variant(by_pos[1000]).reason == FirewallReason.IMPUTED_PASS
        assert assess_variant(by_pos[2000]).reason == FirewallReason.IMPUTED_RARE
        assert assess_variant(by_pos[3000]).reason == FirewallReason.LOW_DR2


class TestImputeRegion:
    def _fake_run(self, *, returncode=0, write=True, raise_exc=None):
        def fake(cmd, **kw):  # noqa: ANN001, ANN202
            if raise_exc is not None:
                raise raise_exc
            out = cmd[cmd.index("--o") + 1]
            if returncode == 0 and write:
                _write_gz(Path(out), _OUT_VCF)
            return SimpleNamespace(returncode=returncode, stdout="", stderr="boom")

        return fake

    def test_success_parses_and_counts(self, tmp_path: Path, monkeypatch) -> None:
        runner = Impute5Runner(bin_dir=_stub_bin_dir(tmp_path), nthreads=4)
        target, ref, gmap = _inputs(tmp_path)
        monkeypatch.setattr(i5_mod.subprocess, "run", self._fake_run())
        res = runner.impute_region("chr22", target, ref, gmap, tmp_path / "out")
        assert res.return_ok is True
        assert res.output_vcf is not None and res.output_vcf.exists()
        assert res.region == "22" and res.n_total == 3 and res.n_imputed == 3

    def test_command_args(self, tmp_path: Path, monkeypatch) -> None:
        runner = Impute5Runner(bin_dir=_stub_bin_dir(tmp_path), nthreads=2)
        target, ref, gmap = _inputs(tmp_path)
        seen: list[list[str]] = []
        fake = self._fake_run()

        def capturing(cmd, **kw):  # noqa: ANN001, ANN202
            seen.append(cmd)
            return fake(cmd, **kw)

        monkeypatch.setattr(i5_mod.subprocess, "run", capturing)
        runner.impute_region(
            "22",
            target,
            ref,
            gmap,
            tmp_path / "out",
            region="22:1-1000000",
            buffer_kb=250,
            out_gp_field=True,
        )
        joined = " ".join(seen[0])
        assert seen[0][0].endswith("impute5")
        assert "--h" in joined and "--g" in joined and "--m" in joined
        assert "--r 22:1-1000000" in joined
        assert "--b 250" in joined and "--out-gp-field" in joined and "--threads 2" in joined

    def test_buffer_region_precedence(self, tmp_path: Path, monkeypatch) -> None:
        runner = Impute5Runner(bin_dir=_stub_bin_dir(tmp_path))
        target, ref, gmap = _inputs(tmp_path)
        seen: list[list[str]] = []
        fake = self._fake_run()
        monkeypatch.setattr(
            i5_mod.subprocess,
            "run",
            lambda cmd, **kw: (seen.append(cmd), fake(cmd, **kw))[1],
        )
        runner.impute_region(
            "22",
            target,
            ref,
            gmap,
            tmp_path / "out",
            buffer_region="22:1-2000000",
            buffer_kb=250,
        )
        joined = " ".join(seen[0])
        assert "--buffer-region 22:1-2000000" in joined
        assert "--b " not in joined  # buffer_region wins over buffer_kb

    def test_failure_returns_not_ok(self, tmp_path: Path, monkeypatch) -> None:
        runner = Impute5Runner(bin_dir=_stub_bin_dir(tmp_path))
        target, ref, gmap = _inputs(tmp_path)
        monkeypatch.setattr(i5_mod.subprocess, "run", self._fake_run(returncode=1))
        res = runner.impute_region("22", target, ref, gmap, tmp_path / "out")
        assert res.return_ok is False and "boom" in res.stderr_tail

    def test_no_output_returns_not_ok(self, tmp_path: Path, monkeypatch) -> None:
        runner = Impute5Runner(bin_dir=_stub_bin_dir(tmp_path))
        target, ref, gmap = _inputs(tmp_path)
        monkeypatch.setattr(i5_mod.subprocess, "run", self._fake_run(write=False))
        res = runner.impute_region("22", target, ref, gmap, tmp_path / "out")
        assert res.return_ok is False

    def test_timeout_returns_not_ok(self, tmp_path: Path, monkeypatch) -> None:
        runner = Impute5Runner(bin_dir=_stub_bin_dir(tmp_path))
        target, ref, gmap = _inputs(tmp_path)
        monkeypatch.setattr(
            i5_mod.subprocess,
            "run",
            self._fake_run(raise_exc=subprocess.TimeoutExpired("impute5", 1.0)),
        )
        res = runner.impute_region("22", target, ref, gmap, tmp_path / "out", timeout=1.0)
        assert res.return_ok is False and res.stderr_tail == "timeout"

    def test_oserror_returns_not_ok(self, tmp_path: Path, monkeypatch) -> None:
        runner = Impute5Runner(bin_dir=_stub_bin_dir(tmp_path))
        target, ref, gmap = _inputs(tmp_path)
        monkeypatch.setattr(
            i5_mod.subprocess, "run", self._fake_run(raise_exc=PermissionError("x"))
        )
        res = runner.impute_region("22", target, ref, gmap, tmp_path / "out")
        assert res.return_ok is False and res.stderr_tail == "PermissionError"

    def test_unparseable_output_returns_not_ok(self, tmp_path: Path, monkeypatch) -> None:
        multi_sample = (
            "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tS1\tS2\n"
            "22\t1\trs\tA\tG\t.\tPASS\tAF=0.2;INFO=0.9\tGT:DS\t0|0:0\t0|1:1\n"
        )

        def fake(cmd, **kw):  # noqa: ANN001, ANN202
            _write_gz(Path(cmd[cmd.index("--o") + 1]), multi_sample)
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        runner = Impute5Runner(bin_dir=_stub_bin_dir(tmp_path))
        target, ref, gmap = _inputs(tmp_path)
        monkeypatch.setattr(i5_mod.subprocess, "run", fake)
        res = runner.impute_region("22", target, ref, gmap, tmp_path / "out")
        assert res.return_ok is False and "failed to parse" in res.stderr_tail

    def test_missing_input_raises(self, tmp_path: Path) -> None:
        runner = Impute5Runner(bin_dir=_stub_bin_dir(tmp_path))
        with pytest.raises(FileNotFoundError, match="IMPUTE5 inputs missing"):
            runner.impute_region(
                "22",
                tmp_path / "no.vcf.gz",
                tmp_path / "no.imp5",
                tmp_path / "no.map",
                tmp_path / "o",
            )

    def test_invalid_chrom_raises(self, tmp_path: Path) -> None:
        runner = Impute5Runner(bin_dir=_stub_bin_dir(tmp_path))
        target, ref, gmap = _inputs(tmp_path)
        with pytest.raises(ValueError, match="unsupported chromosome"):
            runner.impute_region("../etc", target, ref, gmap, tmp_path / "out")

    def test_invalid_region_raises(self, tmp_path: Path) -> None:
        runner = Impute5Runner(bin_dir=_stub_bin_dir(tmp_path))
        target, ref, gmap = _inputs(tmp_path)
        with pytest.raises(ValueError, match="invalid imputation region"):
            runner.impute_region("22", target, ref, gmap, tmp_path / "out", region="22; rm -rf /")

    def test_reversed_interval_raises(self, tmp_path: Path) -> None:
        runner = Impute5Runner(bin_dir=_stub_bin_dir(tmp_path))
        target, ref, gmap = _inputs(tmp_path)
        with pytest.raises(ValueError, match="end < start"):
            runner.impute_region("22", target, ref, gmap, tmp_path / "out", region="22:200-100")

    def test_region_contig_mismatch_raises(self, tmp_path: Path) -> None:
        runner = Impute5Runner(bin_dir=_stub_bin_dir(tmp_path))
        target, ref, gmap = _inputs(tmp_path)
        with pytest.raises(ValueError, match="not on chr22"):
            runner.impute_region("22", target, ref, gmap, tmp_path / "out", region="3:1-100")

    def test_region_runs_get_distinct_output_files(self, tmp_path: Path, monkeypatch) -> None:
        runner = Impute5Runner(bin_dir=_stub_bin_dir(tmp_path))
        target, ref, gmap = _inputs(tmp_path)
        monkeypatch.setattr(i5_mod.subprocess, "run", self._fake_run())
        out = tmp_path / "out"
        r1 = runner.impute_region("22", target, ref, gmap, out, region="22:1-1000000")
        r2 = runner.impute_region("22", target, ref, gmap, out, region="22:1000001-2000000")
        assert r1.output_vcf != r2.output_vcf  # region-keyed → no overwrite
        assert r1.output_vcf is not None and r1.output_vcf.exists()
        assert r2.output_vcf is not None and r2.output_vcf.exists()

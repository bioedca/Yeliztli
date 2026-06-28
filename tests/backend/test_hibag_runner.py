"""HIBAG HLA-imputation engine seam (Wave D / SW-D1).

Validates Rscript detection, model resolution, runtime-status reporting, the
TSV parser (+ low-confidence threshold), command construction, and the success /
failure / timeout / launch-error / unparseable / missing-input paths — with the
Rscript subprocess mocked so no real R/HIBAG is invoked.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

import backend.analysis.hibag_runner as hb_mod
from backend.analysis.hibag_runner import (
    RECOMMENDED_PROB_THRESHOLD,
    HibagRunner,
    available_ancestry_models,
    detect_rscript,
    hibag_available,
    hibag_runtime_status,
    parse_hibag_tsv,
    resolve_model,
)
from backend.config import Settings


def test_blank_hibag_settings_become_none(tmp_path: Path) -> None:
    # Empty env/config values must disable the engine, not resolve to Path('.').
    settings = Settings(data_dir=tmp_path, hibag_rscript="", hibag_model_dir="  ")
    assert settings.hibag_rscript is None
    assert settings.hibag_model_dir is None


_TSV = (
    "locus\tsample.id\tallele1\tallele2\tprob\tmatching\n"
    "A\tS1\t02:01\t01:01\t0.99\t0.85\n"
    "B\tS1\t07:02\t08:01\t0.40\t0.70\n"
)


def _exe(path: Path) -> Path:
    path.touch()
    path.chmod(0o755)
    return path


def _rscript(tmp_path: Path) -> Path:
    return _exe(tmp_path / "Rscript")


def _model_dir(tmp_path: Path, *ancestries: str) -> Path:
    d = tmp_path / "models"
    d.mkdir()
    for a in ancestries:
        (d / f"{a}-HLA4.RData").touch()
    return d


def _plink(tmp_path: Path, name: str = "sample") -> Path:
    prefix = tmp_path / name
    for ext in (".bed", ".bim", ".fam"):
        Path(f"{prefix}{ext}").touch()  # append (a prefix may contain dots)
    return prefix


class TestDetectRscript:
    def test_explicit_file(self, tmp_path: Path) -> None:
        rs = _rscript(tmp_path)
        assert detect_rscript(rs) == rs

    def test_directory(self, tmp_path: Path) -> None:
        rs = _rscript(tmp_path)
        assert detect_rscript(tmp_path) == rs

    def test_non_executable_is_none(self, tmp_path: Path) -> None:
        (tmp_path / "Rscript").touch()  # not executable
        assert detect_rscript(tmp_path / "Rscript") is None

    def test_path_fallback(self, tmp_path: Path, monkeypatch) -> None:
        path_dir = tmp_path / "p"
        path_dir.mkdir()
        _exe(path_dir / "Rscript")
        monkeypatch.setenv("PATH", str(path_dir))
        assert detect_rscript(None) == path_dir / "Rscript"


class TestModelResolution:
    def test_available_ancestry_models_in_known_order(self, tmp_path: Path) -> None:
        d = _model_dir(tmp_path, "African", "European")  # created out of order
        # Returned in KNOWN_ANCESTRIES order (European before African), only present ones.
        assert available_ancestry_models(d) == ["European", "African"]

    def test_available_models_none_dir(self) -> None:
        assert available_ancestry_models(None) == []

    def test_resolve_model_present_and_absent(self, tmp_path: Path) -> None:
        d = _model_dir(tmp_path, "European")
        assert resolve_model(d, "European") == d / "European-HLA4.RData"
        assert resolve_model(d, "Asian") is None

    def test_resolve_model_rejects_unknown_ancestry(self, tmp_path: Path) -> None:
        d = _model_dir(tmp_path, "European")
        # A free-form / traversal value is rejected before touching the filesystem.
        assert resolve_model(d, "../../etc/passwd") is None
        assert resolve_model(d, "Klingon") is None


class TestRuntimeStatus:
    def test_available_requires_rscript_and_model(self, tmp_path: Path) -> None:
        rs = _rscript(tmp_path)
        d = _model_dir(tmp_path, "European")
        st = hibag_runtime_status(rs, d)
        assert st.rscript_available is True
        assert st.ancestry_models == ["European"]
        assert st.available is True
        assert hibag_available(rs, d) is True

    def test_unavailable_without_model(self, tmp_path: Path) -> None:
        rs = _rscript(tmp_path)
        st = hibag_runtime_status(rs, None)
        assert st.rscript_available is True and st.available is False

    def test_unavailable_without_rscript(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setenv("PATH", "")
        d = _model_dir(tmp_path, "European")
        assert hibag_available(None, d) is False


class TestParseTsv:
    def test_parses_calls_and_low_confidence(self, tmp_path: Path) -> None:
        tsv = tmp_path / "calls.tsv"
        tsv.write_text(_TSV, encoding="utf-8")
        calls = parse_hibag_tsv(tsv)
        assert len(calls) == 2
        by_locus = {c.locus: c for c in calls}
        assert by_locus["A"].allele1 == "02:01" and by_locus["A"].prob == 0.99
        assert by_locus["A"].matching == 0.85
        assert by_locus["A"].low_confidence is False  # 0.99 >= 0.5
        assert by_locus["B"].low_confidence is True  # 0.40 < 0.5

    def test_missing_prob_is_low_confidence(self, tmp_path: Path) -> None:
        tsv = tmp_path / "c.tsv"
        tsv.write_text(
            "locus\tsample.id\tallele1\tallele2\tprob\tmatching\nA\tS1\t02:01\t01:01\tNA\tNA\n",
            encoding="utf-8",
        )
        [call] = parse_hibag_tsv(tsv)
        assert call.prob is None and call.matching is None and call.low_confidence is True

    def test_threshold_is_0_5(self) -> None:
        assert RECOMMENDED_PROB_THRESHOLD == 0.5

    def test_missing_column_raises(self, tmp_path: Path) -> None:
        tsv = tmp_path / "bad.tsv"
        # Header lacks allele2/prob/matching → broken contract → fail closed.
        tsv.write_text("locus\tsample.id\tallele1\nA\tS1\t02:01\n", encoding="utf-8")
        with pytest.raises(ValueError, match="missing columns"):
            parse_hibag_tsv(tsv)


class TestConstructor:
    def test_raises_without_rscript(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setenv("PATH", "")
        with pytest.raises(FileNotFoundError, match="Rscript not found"):
            HibagRunner(rscript=None)

    def test_raises_when_r_script_missing(self, tmp_path: Path) -> None:
        rs = _rscript(tmp_path)
        with pytest.raises(FileNotFoundError, match="HIBAG R script not found"):
            HibagRunner(rscript=rs, r_script_path=tmp_path / "nope.R")


class TestPredict:
    def _fake_run(self, *, returncode=0, write=True, raise_exc=None):
        def fake(cmd, **kw):  # noqa: ANN001, ANN202
            if raise_exc is not None:
                raise raise_exc
            out = cmd[cmd.index("--out") + 1]
            if returncode == 0 and write:
                Path(out).write_text(_TSV, encoding="utf-8")
            return SimpleNamespace(returncode=returncode, stdout="", stderr="boom")

        return fake

    def _runner(self, tmp_path: Path) -> HibagRunner:
        return HibagRunner(rscript=_rscript(tmp_path))

    def test_success(self, tmp_path: Path, monkeypatch) -> None:
        runner = self._runner(tmp_path)
        prefix = _plink(tmp_path)
        model = tmp_path / "European-HLA4.RData"
        model.touch()
        monkeypatch.setattr(hb_mod.subprocess, "run", self._fake_run())
        res = runner.predict(prefix, model, tmp_path / "out", loci=["A", "B"])
        assert res.return_ok is True and len(res.calls) == 2
        assert res.output_tsv is not None and res.output_tsv.exists()

    def test_command_args(self, tmp_path: Path, monkeypatch) -> None:
        runner = self._runner(tmp_path)
        prefix = _plink(tmp_path)
        model = tmp_path / "m.RData"
        model.touch()
        seen: list[list[str]] = []
        fake = self._fake_run()
        monkeypatch.setattr(
            hb_mod.subprocess, "run", lambda cmd, **kw: (seen.append(cmd), fake(cmd, **kw))[1]
        )
        runner.predict(prefix, model, tmp_path / "out", loci=["A", "DRB1"])
        joined = " ".join(seen[0])
        assert seen[0][0].endswith("Rscript")
        assert "hibag_predict.R" in joined
        assert "--plink" in joined and "--model" in joined
        assert "--loci A,DRB1" in joined and "--out" in joined

    def test_invalid_locus_raises(self, tmp_path: Path) -> None:
        runner = self._runner(tmp_path)
        prefix = _plink(tmp_path)
        model = tmp_path / "m.RData"
        model.touch()
        with pytest.raises(ValueError, match="invalid HLA locus"):
            runner.predict(prefix, model, tmp_path / "out", loci=["A; rm -rf /"])

    def test_missing_input_raises(self, tmp_path: Path) -> None:
        runner = self._runner(tmp_path)
        with pytest.raises(FileNotFoundError, match="HIBAG inputs missing"):
            runner.predict(tmp_path / "absent", tmp_path / "absent.RData", tmp_path / "out")

    def test_failure_returns_not_ok(self, tmp_path: Path, monkeypatch) -> None:
        runner = self._runner(tmp_path)
        prefix = _plink(tmp_path)
        model = tmp_path / "m.RData"
        model.touch()
        monkeypatch.setattr(hb_mod.subprocess, "run", self._fake_run(returncode=1))
        res = runner.predict(prefix, model, tmp_path / "out", loci=["A"])
        assert res.return_ok is False and "boom" in res.stderr_tail

    def test_timeout_returns_not_ok(self, tmp_path: Path, monkeypatch) -> None:
        runner = self._runner(tmp_path)
        prefix = _plink(tmp_path)
        model = tmp_path / "m.RData"
        model.touch()
        monkeypatch.setattr(
            hb_mod.subprocess,
            "run",
            self._fake_run(raise_exc=subprocess.TimeoutExpired("Rscript", 1.0)),
        )
        res = runner.predict(prefix, model, tmp_path / "out", loci=["A"], timeout=1.0)
        assert res.return_ok is False and res.stderr_tail == "timeout"

    def test_oserror_returns_not_ok(self, tmp_path: Path, monkeypatch) -> None:
        runner = self._runner(tmp_path)
        prefix = _plink(tmp_path)
        model = tmp_path / "m.RData"
        model.touch()
        monkeypatch.setattr(
            hb_mod.subprocess, "run", self._fake_run(raise_exc=PermissionError("x"))
        )
        res = runner.predict(prefix, model, tmp_path / "out", loci=["A"])
        assert res.return_ok is False and res.stderr_tail == "PermissionError"

    def test_no_output_returns_not_ok(self, tmp_path: Path, monkeypatch) -> None:
        runner = self._runner(tmp_path)
        prefix = _plink(tmp_path)
        model = tmp_path / "m.RData"
        model.touch()
        monkeypatch.setattr(hb_mod.subprocess, "run", self._fake_run(write=False))
        res = runner.predict(prefix, model, tmp_path / "out", loci=["A"])
        assert res.return_ok is False

    def test_empty_calls_returns_not_ok(self, tmp_path: Path, monkeypatch) -> None:
        runner = self._runner(tmp_path)
        prefix = _plink(tmp_path)
        model = tmp_path / "m.RData"
        model.touch()
        header_only = "locus\tsample.id\tallele1\tallele2\tprob\tmatching\n"

        def fake(cmd, **kw):  # noqa: ANN001, ANN202
            Path(cmd[cmd.index("--out") + 1]).write_text(header_only, encoding="utf-8")
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        monkeypatch.setattr(hb_mod.subprocess, "run", fake)
        res = runner.predict(prefix, model, tmp_path / "out", loci=["A"])
        assert res.return_ok is False and "no HLA calls" in res.stderr_tail

    def test_dotted_prefix_found(self, tmp_path: Path, monkeypatch) -> None:
        # A prefix containing dots must resolve <prefix>.bed/.bim/.fam (append),
        # not suffix-replace — regression guard for the with_suffix bug.
        runner = self._runner(tmp_path)
        prefix = _plink(tmp_path, name="co.hort.v2")
        model = tmp_path / "m.RData"
        model.touch()
        monkeypatch.setattr(hb_mod.subprocess, "run", self._fake_run())
        res = runner.predict(prefix, model, tmp_path / "out", loci=["A"])
        assert res.return_ok is True

    def test_predict_for_ancestry_resolves_model(self, tmp_path: Path, monkeypatch) -> None:
        model_dir = _model_dir(tmp_path, "European")
        runner = HibagRunner(rscript=_rscript(tmp_path), model_dir=model_dir)
        prefix = _plink(tmp_path)
        monkeypatch.setattr(hb_mod.subprocess, "run", self._fake_run())
        res = runner.predict_for_ancestry(prefix, "European", tmp_path / "out", loci=["A"])
        assert res.return_ok is True

    def test_predict_for_ancestry_missing_model_raises(self, tmp_path: Path) -> None:
        runner = HibagRunner(
            rscript=_rscript(tmp_path), model_dir=_model_dir(tmp_path, "European")
        )
        prefix = _plink(tmp_path)
        with pytest.raises(FileNotFoundError, match="no HIBAG model for ancestry"):
            runner.predict_for_ancestry(prefix, "Asian", tmp_path / "out")

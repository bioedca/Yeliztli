"""Regression tests for the frontend Vitest warning gate."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
FRONTEND_DIR = REPO_ROOT / "frontend"
SCRIPT = FRONTEND_DIR / "scripts" / "run-vitest-strict.sh"


def _run_with_fake_vitest(
    tmp_path: Path,
    fake_body: str,
    *script_args: str,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_vitest = bin_dir / "vitest"
    fake_vitest.write_text(f"#!/usr/bin/env bash\n{fake_body}", encoding="utf-8")
    fake_vitest.chmod(0o755)

    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"
    env["VITEST_STRICT_LOG"] = str(tmp_path / "vitest.log")
    if extra_env:
        env.update(extra_env)

    return subprocess.run(
        ["bash", str(SCRIPT), *script_args],
        cwd=FRONTEND_DIR,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_strict_vitest_fails_on_react_act_warning(tmp_path: Path) -> None:
    result = _run_with_fake_vitest(
        tmp_path,
        "printf '%s\n' 'Warning: not wrapped in act(...)'\n",
    )

    assert result.returncode == 1
    assert "React act warning detected" in result.stderr


def test_strict_vitest_preserves_vitest_failure_without_warning(tmp_path: Path) -> None:
    result = _run_with_fake_vitest(
        tmp_path,
        "printf '%s\n' 'ordinary failure'\nexit 7\n",
    )

    assert result.returncode == 7
    assert "ordinary failure" in result.stdout


def test_strict_vitest_passes_clean_output(tmp_path: Path) -> None:
    result = _run_with_fake_vitest(tmp_path, "printf '%s\n' 'all good'\n")

    assert result.returncode == 0


def test_strict_vitest_forwards_args_to_vitest_run(tmp_path: Path) -> None:
    args_out = tmp_path / "args.txt"
    result = _run_with_fake_vitest(
        tmp_path,
        "printf '%s\n' \"$@\" > \"$VITEST_ARGS_OUT\"\nprintf '%s\n' 'all good'\n",
        "--coverage",
        extra_env={"VITEST_ARGS_OUT": str(args_out)},
    )

    assert result.returncode == 0
    assert args_out.read_text(encoding="utf-8").splitlines() == ["run", "--coverage"]

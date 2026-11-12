"""The literal browser lifecycle CI command must retain first-attempt failures."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]


def _step(name: str) -> str:
    workflow = (_ROOT / ".github/workflows/ci.yml").read_text()
    job = workflow.split("  test-e2e:\n", 1)[1]
    job = re.split(r"^  [A-Za-z0-9_-]+:\s*$", job, maxsplit=1, flags=re.MULTILINE)[0]
    marker = f"      - name: {name}\n"
    assert marker in job, f"missing CI step: {name}"
    step = job.split(marker, 1)[1]
    return re.split(r"^      - ", step, maxsplit=1, flags=re.MULTILINE)[0]


def _script() -> str:
    step = _step("Run browser lifecycle regression")
    lines = step.split("        run: |\n", 1)[1].splitlines()
    shell = []
    for line in lines:
        if line and not line.startswith("          "):
            break
        shell.append(line[10:] if line else "")
    assert shell
    return "\n".join(shell) + "\n"


@pytest.mark.parametrize("browser", ["chromium", "firefox", "webkit"])
@pytest.mark.parametrize("exit_code", [0, 37])
def test_lifecycle_command_preserves_exit_and_process_logs(
    tmp_path: Path, browser: str, exit_code: int
) -> None:
    tools = tmp_path / "probe tools"
    tools.mkdir()
    (tools / "python").symlink_to(sys.executable)
    node = tools / "node"
    node.write_text(
        "#!/usr/bin/env python\n"
        + textwrap.dedent(
            """
            import json, os, sys
            from pathlib import Path
            Path(os.environ['PROBE_CALL']).write_text(json.dumps({
                'args': sys.argv[1:], 'debug': os.environ.get('DEBUG'),
                'data': os.environ.get('YELIZTLI_DATA_DIR'),
            }))
            print('synthetic browser stdout', flush=True)
            code = int(os.environ['PROBE_EXIT'])
            print(f'synthetic process exit: {code}', file=sys.stderr, flush=True)
            raise SystemExit(code)
            """
        )
    )
    node.chmod(0o700)
    diagnostics = tmp_path / "output/playwright/lifecycle probe"
    call = tmp_path / "call.json"
    step = _step("Run browser lifecycle regression")
    debug = re.search(r"^          DEBUG:\s*[\"']?([^\n\"']+)", step, re.MULTILINE)
    assert debug and debug[1] == "pw:browser"
    env = {
        **os.environ,
        "PATH": str(tools) + os.pathsep + os.environ["PATH"],
        "DEBUG": debug[1],
        "YELIZTLI_BROWSER_DIAGNOSTICS": str(diagnostics),
        "PROBE_CALL": str(call),
        "PROBE_EXIT": str(exit_code),
    }
    result = subprocess.run(
        ["bash", "-c", _script().replace("${{ matrix.browser }}", browser)],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == exit_code, result.stdout + result.stderr
    invocation = json.loads(call.read_text())
    assert invocation["args"] == [
        "node_modules/@playwright/test/cli.js",
        "test",
        "--config=playwright.lifecycle.config.ts",
        f"--project={browser}",
        "--workers=1",
        "--retries=0",
    ]
    assert invocation["debug"] == "pw:browser"
    assert Path(invocation["data"]) == diagnostics / "data"
    assert (diagnostics / "browser.log").read_text().splitlines() == [
        "synthetic browser stdout",
        f"synthetic process exit: {exit_code}",
    ]


def test_lifecycle_diagnostics_upload_on_failure_without_application_data() -> None:
    step = _step("Upload browser lifecycle diagnostics")
    assert re.search(r"^        if: always\(\)$", step, re.MULTILINE)
    name = re.search(r"^          name:\s*(.+)$", step, re.MULTILINE)
    assert name
    assert "${{ matrix.browser }}" in name[1]
    assert "${{ github.run_id }}" in name[1]
    assert "${{ github.run_attempt }}" in name[1]
    assert "output/playwright/lifecycle-*/browser.log" in step
    assert "output/playwright/lifecycle-*/browser-results.json" in step
    assert "output/playwright/lifecycle-*/test-results/" in step
    assert "data/" not in step


def test_lifecycle_command_preserves_an_existing_attempt(tmp_path: Path) -> None:
    diagnostics = tmp_path / "output/playwright/existing-attempt"
    diagnostics.mkdir(parents=True)
    log = diagnostics / "browser.log"
    log.write_text("retained failed attempt\n")
    tools = tmp_path / "tools"
    tools.mkdir()
    node = tools / "node"
    node.write_text('#!/bin/sh\n: > "$PROBE_CALL"\nexit 0\n')
    node.chmod(0o700)
    call = tmp_path / "unexpected-call"
    result = subprocess.run(
        ["bash", "-c", _script().replace("${{ matrix.browser }}", "webkit")],
        cwd=tmp_path,
        env={
            **os.environ,
            "PATH": str(tools) + os.pathsep + os.environ["PATH"],
            "YELIZTLI_BROWSER_DIAGNOSTICS": str(diagnostics),
            "PROBE_CALL": str(call),
        },
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode != 0
    assert not call.exists(), "a prior attempt must stop the command before browser launch"
    assert log.read_text() == "retained failed attempt\n"

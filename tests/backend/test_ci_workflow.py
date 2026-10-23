"""CI workflow guardrails.

The backend suite's job timeout is a headroom budget, not a hang detector.
Nothing else in the repository observes it: a too-tight limit does not fail a
test, it kills a *passing* run and reports `cancelled`, which propagates as
`CI Required = failure` and reads exactly like a benign superseding-push
cancellation. That is why #2213 went undiagnosed through repeated re-runs.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_CI_WORKFLOW = _PROJECT_ROOT / ".github" / "workflows" / "ci.yml"
_JOB_HEADER = re.compile(r"^  [A-Za-z0-9_-]+:\s*$", re.MULTILINE)

# Longest honest backend leg observed to date is 30:15 (#2244, py3.13, killed at
# the old 30-minute cap) against a 26:49 sibling on the same commit. A limit at
# or below that is not headroom at all.
_MIN_BACKEND_TIMEOUT_MINUTES = 40


def _job_block(job_name: str) -> str:
    content = _CI_WORKFLOW.read_text(encoding="utf-8")
    marker = f"  {job_name}:\n"
    start = content.index(marker)
    next_job = _JOB_HEADER.search(content, start + len(marker))
    end = next_job.start() if next_job else len(content)
    return content[start:end]


def _step_block(job: str, name: str) -> str:
    marker = f"      - name: {name}\n"
    assert marker in job, f"missing CI step: {name}"
    start = job.index(marker)
    next_step = re.search(r"^      - ", job[start + len(marker) :], re.MULTILINE)
    end = start + len(marker) + next_step.start() if next_step else len(job)
    return job[start:end]


def test_e2e_installer_budget_keeps_measured_headroom_and_all_browsers() -> None:
    """A successful 33m09s install needs headroom, plus a finite outer backstop (#2600)."""
    job = _job_block("test-e2e")
    job_timeout = re.search(r"^    timeout-minutes:\s*(\d+)\s*$", job, re.MULTILINE)
    assert job_timeout, "E2E must declare an explicit overall job budget"
    install = _step_block(job, "Install Playwright browsers")
    step_timeout = re.search(r"^        timeout-minutes:\s*(\d+)\s*$", install, re.MULTILINE)
    budget = re.search(
        r"^          PLAYWRIGHT_INSTALL_TIMEOUT_SECONDS:\s*['\"]?(\d+)['\"]?\s*$",
        install,
        re.MULTILINE,
    )
    grace = re.search(
        r"^          PLAYWRIGHT_INSTALL_KILL_GRACE_SECONDS:\s*['\"]?(\d+)['\"]?\s*$",
        install,
        re.MULTILINE,
    )
    assert grace and int(grace[1]) > 0, "cleanup escalation must remain enabled"
    assert step_timeout, "installer needs an outer step backstop"
    assert budget, "installer needs a declared command budget"
    assert int(budget[1]) >= 45 * 60, "retain headroom over the successful 33m09s baseline"
    assert int(step_timeout[1]) * 60 >= int(budget[1]) + int(grace[1]) + 30, (
        "allow timeout cleanup/reporting"
    )
    assert int(job_timeout[1]) >= int(step_timeout[1]) + 30, "leave room for setup and E2E"
    matrix = re.search(r"^        browser:\s*\[([^]]+)\]", job, re.MULTILINE)
    assert matrix, "keep an explicit browser matrix"
    browsers = [value.strip().strip("'\"") for value in matrix[1].split(",")]
    assert set(browsers) == {"chromium", "firefox", "webkit"}
    assert len(browsers) == 3
    assert "continue-on-error" not in job, "installer failures must still fail the gate"


def test_e2e_installer_diagnostics_survive_failure_and_reruns() -> None:
    """Keep installation evidence independently of whether browser tests start (#2600)."""
    job = _job_block("test-e2e")
    diagnostics = _step_block(job, "Upload browser installation diagnostics")
    assert re.search(r"^        if: always\(\)$", diagnostics, re.MULTILINE)
    assert "uses: actions/upload-artifact@" in diagnostics
    names = re.findall(r"^          name:\s*(.+)$", diagnostics, re.MULTILINE)
    assert len(names) == 1
    assert "${{ matrix.browser }}" in names[0]
    assert "${{ github.run_attempt }}" in names[0], "reruns must retain earlier diagnostics"
    assert "output/playwright/" in diagnostics


def _installer_shell() -> str:
    """Execute the literal production step, rather than a test-only wrapper."""
    step = _step_block(_job_block("test-e2e"), "Install Playwright browsers")
    marker = "        run: |\n"
    assert marker in step, "installer must retain its executable diagnostic wrapper"
    lines = []
    for line in step.split(marker, 1)[1].splitlines():
        if line and not line.startswith("          "):
            break
        lines.append(line[10:] if line else "")
    assert lines
    return "\n".join(lines) + "\n"


@pytest.fixture
def installer_probe(tmp_path: Path):
    """Replace only npx; use real Bash, tee and GNU timeout for the Ubuntu CI step."""
    timeout = shutil.which("timeout") or shutil.which("gtimeout")
    if os.name != "posix" or timeout is None:
        pytest.skip("the Ubuntu CI shell probe requires POSIX and GNU timeout")
    tools = tmp_path / "probe tools"
    tools.mkdir()
    (tools / "timeout").symlink_to(timeout)
    (tools / "python").symlink_to(sys.executable)
    npx = tools / "npx"
    npx.write_text(
        "#!/usr/bin/env python\n"
        + textwrap.dedent(
            """
            import os
            import subprocess
            import sys
            import time
            from pathlib import Path

            expected = ["playwright", "install", "--with-deps"]
            expected.append(os.environ["INSTALL_PROBE_BROWSER"])
            assert sys.argv[1:] == expected
            print("installer stdout", flush=True)
            print("installer stderr", file=sys.stderr, flush=True)
            mode = os.environ["INSTALL_PROBE_MODE"]
            if mode == "failure":
                raise SystemExit(42)
            if mode == "kill_status":
                raise SystemExit(137)
            if mode in ("child_timeout", "stubborn_child"):
                child = (
                    "import os,signal,time; from pathlib import Path; "
                    "signal.signal(signal.SIGTERM, lambda *_: "
                    "(Path(os.environ['INSTALL_PROBE_TERMINATED']).write_text('terminated'), "
                    "exit(0))); "
                    "Path(os.environ['INSTALL_PROBE_READY']).write_text('ready'); "
                    "time.sleep(120)"
                )
                if mode == "stubborn_child":
                    child = (
                        "import os,signal,time; from pathlib import Path; "
                        "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
                        "Path(os.environ['INSTALL_PROBE_READY']).write_text('ready'); "
                        "time.sleep(5); "
                        "Path(os.environ['INSTALL_PROBE_ESCAPED']).write_text('escaped')"
                    )
                subprocess.Popen([sys.executable, "-c", child])
            if mode in ("timeout", "child_timeout", "stubborn_child"):
                time.sleep(120)
            """
        ),
        encoding="utf-8",
    )
    npx.chmod(0o755)

    def run(mode: str, budget: str = "30", browser: str = "webkit", grace: str = "1"):
        env = {
            **os.environ,
            "PATH": str(tools) + os.pathsep + os.environ["PATH"],
            "PLAYWRIGHT_BROWSER": browser,
            "PLAYWRIGHT_INSTALL_KILL_GRACE_SECONDS": grace,
            "INSTALL_PROBE_BROWSER": browser,
            "INSTALL_PROBE_ESCAPED": str(tmp_path / "child.escaped"),
            "PLAYWRIGHT_INSTALL_TIMEOUT_SECONDS": budget,
            "INSTALL_PROBE_MODE": mode,
            "INSTALL_PROBE_READY": str(tmp_path / "child.ready"),
            "INSTALL_PROBE_TERMINATED": str(tmp_path / "child.terminated"),
        }
        result = subprocess.run(
            ["bash", "-c", _installer_shell()],
            cwd=tmp_path,
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        output = tmp_path / "output" / "playwright"
        status = json.loads((output / f"install-{browser}.json").read_text(encoding="utf-8"))
        log = (output / f"install-{browser}.log").read_text(encoding="utf-8")
        return result, status, log

    return run


@pytest.mark.parametrize(
    ("mode", "code", "outcome", "browser"),
    [("success", 0, "succeeded", browser) for browser in ("chromium", "firefox", "webkit")]
    + [("failure", 42, "failed", "webkit"), ("kill_status", 137, "kill_status", "webkit")],
)
def test_installer_retains_output_and_propagates_phase_status(
    installer_probe, mode, code, outcome, browser
):
    result, status, log = installer_probe(mode, browser=browser)
    assert result.returncode == code, result.stdout + result.stderr
    assert status["exit_code"] == code
    assert status["outcome"] == outcome
    assert status["timeout_seconds"] == 30
    assert status["elapsed_seconds"] >= 0
    assert status["termination_observed"] is False
    assert "installer stdout" in log and "installer stderr" in log
    assert "installer stdout" in result.stdout and "installer stderr" in result.stdout
    if code:
        assert f"::error title=Playwright installation {outcome}::" in log
    else:
        assert "::error" not in result.stdout


def test_installer_timeout_fails_with_retained_diagnostics(installer_probe):
    result, status, log = installer_probe("timeout", "1")
    assert result.returncode == 137, result.stdout + result.stderr
    assert status["outcome"] == "timeout_status"
    assert status["exit_code"] == 137
    assert status["timeout_seconds"] == 1
    assert status["elapsed_seconds"] >= 2
    assert status["termination_observed"] is True
    assert status["kill_grace_seconds"] == 1
    assert "installer stdout" in log and "installer stderr" in log
    assert "::error title=Playwright installation timeout_status::" in log


def test_installer_deadline_signals_descendants(installer_probe, tmp_path: Path):
    result, status, _log = installer_probe("child_timeout", "3")
    assert result.returncode == 137, result.stdout + result.stderr
    assert status["outcome"] == "timeout_status"
    assert (tmp_path / "child.ready").read_text() == "ready"
    terminated = tmp_path / "child.terminated"
    deadline = time.monotonic() + 2
    while not terminated.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert terminated.read_text() == "terminated"


def test_installer_deadline_kills_a_term_ignoring_descendant(installer_probe, tmp_path: Path):
    result, status, _log = installer_probe("stubborn_child", "2")
    assert (tmp_path / "child.ready").read_text() == "ready"
    # The child tries to write at t=5s; the deadline plus kill grace is 3s.
    # Waiting past its finite fallback also leaves no synthetic process behind.
    time.sleep(5)
    assert not (tmp_path / "child.escaped").exists(), "installer child survived cleanup"
    assert result.returncode == 137, result.stdout + result.stderr
    assert status["outcome"] == "timeout_status"
    assert status["termination_observed"] is True


@pytest.mark.parametrize("budget", ["0", "-1", "invalid"])
def test_installer_rejects_an_unbounded_or_invalid_budget(installer_probe, budget):
    result, status, log = installer_probe("success", budget)
    assert result.returncode == 2, result.stdout + result.stderr
    assert status["outcome"] == "invalid_budget"
    assert status["exit_code"] == 2
    assert "installer stdout" not in result.stdout + log
    assert "::error title=Invalid Playwright installation budget::" in log


@pytest.mark.parametrize("grace", ["0", "-1", "invalid"])
def test_installer_rejects_disabled_or_invalid_kill_grace(installer_probe, grace):
    result, status, log = installer_probe("success", grace=grace)
    assert result.returncode == 2, result.stdout + result.stderr
    assert status["outcome"] == "invalid_budget"
    assert "installer stdout" not in result.stdout + log


def test_backend_job_timeout_clears_an_honest_slow_run() -> None:
    """The Tier-1 backend legs keep real headroom over their observed runtime."""
    block = _job_block("test-backend")
    declared = re.findall(r"^    timeout-minutes:\s*(\d+)\s*$", block, re.MULTILINE)

    assert declared, "test-backend must declare timeout-minutes explicitly"
    assert len(declared) == 1, f"expected one timeout-minutes, found {declared}"
    assert int(declared[0]) >= _MIN_BACKEND_TIMEOUT_MINUTES, (
        f"test-backend timeout-minutes is {declared[0]}, below the "
        f"{_MIN_BACKEND_TIMEOUT_MINUTES}-minute floor. The suite has been observed at "
        "30:15; a cap near that kills passing runs and reports 'cancelled' (#2213). "
        "Split the suite rather than lowering this."
    )


def test_backend_matrix_and_shard_count_agree() -> None:
    """A matrix shorter than ``--num-shards`` silently stops running tests.

    That is the one sharding mistake nothing else would catch: legs 1..k run
    their slices, the tests assigned to the missing legs run nowhere, and every
    job still reports success. The opposite mistake fails closed on its own —
    a leg whose ``--shard-id`` exceeds ``--num-shards`` aborts with a
    ``UsageError`` (``tests/backend/test_ci_backend_shard.py``).
    """
    block = _job_block("test-backend")

    matrix = re.search(r"^        shard:\s*\[([0-9,\s]+)\]\s*$", block, re.MULTILINE)
    assert matrix, "test-backend must declare an explicit `shard:` matrix"
    shards = [int(value) for value in matrix.group(1).split(",")]

    declared = re.search(r"--num-shards\s+(\d+)", block)
    assert declared, "the pytest invocation must pass --num-shards"
    num_shards = int(declared.group(1))

    assert num_shards >= 2, "a one-shard split is not a split"
    assert shards == list(range(1, num_shards + 1)), (
        f"the shard matrix {shards} does not cover 1..{num_shards}; tests assigned to "
        "the missing legs would never run, and every leg would still be green"
    )
    assert "--shard-id ${{ matrix.shard }}" in block, (
        "each leg must run the shard its matrix entry names"
    )
    assert f"shard ${{{{ matrix.shard }}}}/{num_shards}" in block, (
        "the job name must show the same shard count it runs"
    )


def test_backend_shards_still_target_the_whole_suite() -> None:
    """Sharding must narrow the *selection*, never the collected path.

    Passing a subdirectory here would look like a shard and behave like a
    permanent deletion, since the partition can only redistribute what pytest
    collected in the first place.
    """
    block = _job_block("test-backend")

    assert re.search(r"python -m pytest tests/backend/ -v --tb=short -m \"not slow\"", block), (
        "every shard must still collect all of tests/backend/ before partitioning"
    )


def test_cross_os_backend_leg_runs_the_unsharded_suite() -> None:
    """The macOS leg is the backstop that would notice a partition losing tests.

    It is Tier-2, so it only runs on merge — but it runs the suite whole, which
    means a sharding bug that drops modules cannot stay invisible on every leg
    at once.
    """
    block = _job_block("test-backend-cross-os")

    assert "--num-shards" not in block and "--shard-id" not in block, (
        "the cross-OS leg must stay unsharded"
    )
    assert 'python -m pytest tests/backend/ -v --tb=short -m "not slow"' in block


def test_backend_job_keeps_the_bounded_startup_witness() -> None:
    """A raised timeout must not become the thing that catches a lifespan hang.

    The witness is what makes a hang fail fast with a named step; without it a
    genuine hang would now burn the full, larger budget before anyone sees it.
    """
    block = _job_block("test-backend")
    assert "scripts/ci_startup_witness.py" in block
    assert re.search(r"run:\s*timeout\s+\d+\s+python scripts/ci_startup_witness\.py", block), (
        "the startup witness must stay bounded by its own `timeout`"
    )


def test_ci_required_names_the_job_and_cause_behind_its_verdict() -> None:
    """The single required check explains a red result instead of a bare 'cancelled' (#2289).

    ``join(needs.*.result, ' ')`` dropped the job name, so a time-limit kill, a
    superseding push and a broken test all read the same. The summary script
    keeps the verdict on ``needs`` and reads only the run's jobs and annotations.
    """
    block = _job_block("ci-required")

    assert re.search(r"^    if: always\(\)$", block, re.MULTILINE)
    assert "join(needs.*.result" not in block
    assert "NEEDS_JSON: ${{ toJSON(needs) }}" in block
    assert "run: python3 scripts/ci_required_summary.py" in block
    assert "sparse-checkout: scripts/ci_required_summary.py" in block
    assert "persist-credentials: false" in block

    permissions = re.search(r"^    permissions:\n((?:      .*\n)+)", block, re.MULTILINE)
    assert permissions is not None, "ci-required must scope its own token"
    scopes = dict(
        line.strip().split(": ", 1)
        for line in permissions.group(1).splitlines()
        if not line.strip().startswith("#")
    )
    assert scopes == {"contents": "read", "actions": "read", "checks": "read"}

"""The nightly workflow reports a job that times out, not only one that fails (#2270).

A job that reaches its ``timeout-minutes`` is recorded as ``cancelled``. The old
reporter ran inside the slow-tier job and was gated on ``failure()``, so when the
600k benchmark grew past the job's 120 minutes, the slow tier ran into its cap
every night from 2026-06-28 and filed nothing. These checks keep the structure that
closes that gap: one ``report`` job that runs after every test job and counts a
cancelled one, a pytest budget inside each job's budget so the summary still
prints, and the benchmark in a job of its own.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

_NIGHTLY = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "nightly.yml"
_BENCHMARK = "tests/backend/test_benchmark.py"


def _jobs() -> dict[str, dict]:
    return yaml.safe_load(_NIGHTLY.read_text(encoding="utf-8"))["jobs"]


def _pytest_runs(job: dict) -> list[str]:
    return [
        str(step["run"]) for step in job.get("steps") or [] if "pytest" in str(step.get("run", ""))
    ]


def _test_jobs() -> dict[str, dict]:
    return {name: job for name, job in _jobs().items() if _pytest_runs(job)}


def test_every_test_job_feeds_the_report_job() -> None:
    test_jobs = _test_jobs()
    assert {"slow-tests", "slow-benchmark", "cross-os-backstop"} <= set(test_jobs)
    assert set(_jobs()["report"]["needs"]) == set(test_jobs)


def test_report_runs_after_a_failed_or_cancelled_job() -> None:
    condition = re.sub(r"\s+", " ", _jobs()["report"]["if"])
    assert condition.startswith("always()"), condition
    assert "contains(needs.*.result, 'failure')" in condition
    assert "contains(needs.*.result, 'cancelled')" in condition


def test_report_counts_a_cancelled_job_as_a_regression() -> None:
    script = _jobs()["report"]["steps"][0]["with"]["script"]
    predicate = re.search(r"const regressed = \(result\) => ([^;]+);", script)
    assert predicate, "the report script no longer defines `regressed`"
    outcomes = set(re.findall(r"result === '(\w+)'", predicate.group(1)))
    assert outcomes == {"failure", "cancelled"}, predicate.group(1)
    assert "if (!jobs.some(([, job]) => regressed(job.result))) continue;" in script


def test_every_reported_job_maps_to_a_label() -> None:
    script = _jobs()["report"]["steps"][0]["with"]["script"]
    prefixes = re.findall(r"prefix: '([^']+)'", script)
    labels = re.findall(r"label: '([^']+)'", script)
    assert labels == ["slow-tier-regression", "cross-os-regression"]
    unmapped = [
        job for job in _jobs()["report"]["needs"] if not any(job.startswith(p) for p in prefixes)
    ]
    assert not unmapped, f"report needs {unmapped}, but no group prefix in its script matches them"


def test_only_the_report_job_can_file_issues() -> None:
    workflow = yaml.safe_load(_NIGHTLY.read_text(encoding="utf-8"))
    assert "issues" not in (workflow.get("permissions") or {})
    for name, job in workflow["jobs"].items():
        filing = [
            step for step in job.get("steps") or [] if "github-script" in str(step.get("uses", ""))
        ]
        if name == "report":
            assert (job.get("permissions") or {}).get("issues") == "write"
            assert len(filing) == 1
        else:
            assert not filing, f"{name} files issues itself; only the report job sees a timeout"
            assert "issues" not in (job.get("permissions") or {})


def test_pytest_is_interrupted_inside_its_job_budget() -> None:
    for name, job in _test_jobs().items():
        if name == "cross-os-backstop":
            continue  # PR-tier suite, ~30 of its 60 min; the report job still sees a timeout
        for run in _pytest_runs(job):
            budget = re.search(r"timeout --signal=INT --kill-after=(\d+)m (\d+)m", run)
            assert budget, (
                f"{name}: run pytest under `timeout --signal=INT` so it prints its summary"
            )
            kill_after, minutes = (int(value) for value in budget.groups())
            assert minutes + kill_after < job["timeout-minutes"], name
            assert '"$status" -eq 124' in run, f"{name}: name the budget overrun in the log"


def test_the_benchmark_runs_alone_and_nothing_slow_is_dropped() -> None:
    (slow,) = _pytest_runs(_jobs()["slow-tests"])
    (benchmark,) = _pytest_runs(_jobs()["slow-benchmark"])
    assert "tests/backend/ " in slow and "-m slow" in slow
    assert f"--ignore={_BENCHMARK}" in slow
    assert f"pytest {_BENCHMARK} " in benchmark and "-m slow" in benchmark
    assert Path(__file__).resolve().parents[2].joinpath(_BENCHMARK).is_file()

"""Release workflow guardrails."""

from __future__ import annotations

import re
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_RELEASE_WORKFLOW = _PROJECT_ROOT / ".github" / "workflows" / "release.yml"
_JOB_HEADER = re.compile(r"^  [A-Za-z0-9_-]+:\s*$", re.MULTILINE)
_BENCHMARK_COMMAND = (
    'run: python -m pytest tests/backend/test_benchmark.py -v --tb=short -m "slow or benchmark"'
)


def _release_job_block(job_name: str) -> str:
    content = _RELEASE_WORKFLOW.read_text(encoding="utf-8")
    marker = f"  {job_name}:\n"
    start = content.index(marker)
    next_job = _JOB_HEADER.search(content, start + len(marker))
    end = next_job.start() if next_job else len(content)
    return content[start:end]


def test_release_benchmark_pytest_failures_are_blocking() -> None:
    benchmark_job = _release_job_block("benchmark")
    benchmark_run_lines = [
        line.strip()
        for line in benchmark_job.splitlines()
        if "tests/backend/test_benchmark.py" in line
    ]

    assert benchmark_run_lines == [_BENCHMARK_COMMAND]
    assert "|| true" not in benchmark_run_lines[0]
    assert "continue-on-error: true" not in benchmark_job

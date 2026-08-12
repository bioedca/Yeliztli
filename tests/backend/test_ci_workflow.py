"""CI workflow guardrails.

The backend suite's job timeout is a headroom budget, not a hang detector.
Nothing else in the repository observes it: a too-tight limit does not fail a
test, it kills a *passing* run and reports `cancelled`, which propagates as
`CI Required = failure` and reads exactly like a benign superseding-push
cancellation. That is why #2213 went undiagnosed through repeated re-runs.
"""

from __future__ import annotations

import re
from pathlib import Path

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

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

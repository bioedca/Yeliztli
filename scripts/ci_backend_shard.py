"""Deterministic collection-time sharding for the backend pytest suite.

The Tier-1 backend leg is a headroom problem, not a hang problem: #2213 raised
the cap from 30 to 45 minutes and #2326 hit *that* ceiling on a slow runner with
the suite still passing at 92%. The comment on ``test-backend`` in ``ci.yml``
already recorded the intended answer — "a leg approaching this figure should be
**split**, not raised again" — and this module is that split.

Why partition at collection time rather than by passing file lists to pytest:

* **Coverage is guaranteed by construction.** The shard set is derived from the
  items pytest actually collected, so a newly added test file cannot land in
  zero shards. A hand-maintained manifest, or a glob split in the workflow,
  fails exactly that way — silently, and looking green while it does it.
* **Balance tracks the suite.** Weights are the real collected test counts, so
  adding 300 parametrised cases to one module re-balances on the next run with
  no manifest to update in the PR that added them.
* **Isolation is unchanged.** Each shard is a separate pytest process running a
  subset of modules, exactly the isolation the single unsharded process had.
  This is deliberately *not* in-process parallelism (pytest-xdist): every
  fixture in ``tests/backend/conftest.py`` is function-scoped, but the suite
  also reaches real filesystem state (``HOME``, the data dir, sample
  databases), which concurrent workers in one job would share.

Modules — not individual tests — are the unit of assignment, so a module's
tests always run together in one process and one interpreter, and any ordering
relationship inside a file is preserved.

Test *count* is a proxy for runtime, not a measurement of it. It does not have
to be a good one: three shards take the leg from ~29 minutes to ~10, so even a
badly mis-weighted shard sits far below the 45-minute cap. Wire it to real
durations only if a shard is ever observed approaching the budget.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping

import pytest

# ── Pure partition ──────────────────────────────────────────────────────


def assign_modules_to_shards(weights: Mapping[str, int], num_shards: int) -> dict[str, int]:
    """Map every module in ``weights`` to a 0-based shard index.

    Greedy longest-processing-time-first: heaviest module first, each one to the
    lightest shard so far. Ties break on the module id and then the lower shard
    index, so the assignment depends only on ``weights`` and ``num_shards`` —
    every leg of the matrix computes the same partition from the same tree
    without exchanging any state.

    The result is a total, disjoint partition: every key appears exactly once.
    """
    if num_shards < 1:
        raise ValueError(f"num_shards must be >= 1, got {num_shards}")

    loads = [0] * num_shards
    assignment: dict[str, int] = {}
    for module in sorted(weights, key=lambda name: (-weights[name], name)):
        target = min(range(num_shards), key=lambda index: (loads[index], index))
        assignment[module] = target
        loads[target] += weights[module]
    return assignment


def module_id(item: pytest.Item) -> str:
    """Return the collection file a test item belongs to.

    ``nodeid`` is ``path/to/test_x.py::TestClass::test_y[param]``; everything
    before the first ``::`` is the module, and a module-level item (rare, e.g. a
    collection error) has no ``::`` at all.
    """
    return item.nodeid.split("::", 1)[0]


# ── pytest plugin ───────────────────────────────────────────────────────


class BackendShardPlugin:
    """Keep only the collected items belonging to this shard.

    Registered by ``tests/conftest.py`` only when ``--num-shards`` is greater
    than 1, so an unsharded run — every local invocation, the macOS Tier-2 leg,
    and the nightly slow tier — behaves exactly as it did before.
    """

    def __init__(self, shard_id: int, num_shards: int) -> None:
        self.shard_id = shard_id
        self.num_shards = num_shards

    def pytest_report_header(self) -> str:
        """Put the shard on the first page of the log, next to the versions."""
        return f"backend shard: {self.shard_id} of {self.num_shards}"

    # trylast so this runs *after* pytest's own marker deselection (``-m "not
    # slow"``) and after the prerequisite skips in tests/conftest.py: the
    # weights then describe the tests this leg would really have run.
    @pytest.hookimpl(trylast=True)
    def pytest_collection_modifyitems(
        self, config: pytest.Config, items: list[pytest.Item]
    ) -> None:
        weights = Counter(module_id(item) for item in items)
        assignment = assign_modules_to_shards(weights, self.num_shards)
        mine = self.shard_id - 1

        selected: list[pytest.Item] = []
        deselected: list[pytest.Item] = []
        for item in items:
            (selected if assignment[module_id(item)] == mine else deselected).append(item)

        if deselected:
            config.hook.pytest_deselected(items=deselected)
        items[:] = selected

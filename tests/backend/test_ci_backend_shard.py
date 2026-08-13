"""Guardrails for the backend suite's CI sharding (#2326).

Sharding trades one long job for three short ones, and it does that by *removing
tests from the run*. That makes its failure mode uniquely nasty: a partition that
drops a module does not fail anything — every leg goes green having quietly run
less. So the properties asserted here are totality and disjointness first, and
balance only as a distant third.

Two layers, deliberately:

* the pure partition and the plugin's item routing, checked directly; and
* a real ``python -m pytest`` invocation over a generated mini-suite that loads
  ``tests/conftest.py``'s own ``pytest_addoption`` / ``_configure_sharding``, so
  the command line CI actually types is proven to reach the plugin. Unit tests
  over the partition alone would pass just as happily if the wiring were dead.
"""

from __future__ import annotations

import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

import pytest

from scripts.ci_backend_shard import (
    BackendShardPlugin,
    assign_modules_to_shards,
    module_id,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]


# ── Test doubles ────────────────────────────────────────────────────────


class _FakeItem:
    """The plugin reads exactly one attribute off a collected item."""

    def __init__(self, nodeid: str) -> None:
        self.nodeid = nodeid


class _RecordingHook:
    def __init__(self) -> None:
        self.deselected: list[_FakeItem] = []

    def pytest_deselected(self, items: list[_FakeItem]) -> None:
        self.deselected.extend(items)


class _FakeConfig:
    def __init__(self) -> None:
        self.hook = _RecordingHook()


def _items(spec: dict[str, int]) -> list[_FakeItem]:
    """Build collected items as ``{module: number_of_tests}``."""
    return [
        _FakeItem(f"{module}::test_{index}")
        for module, count in spec.items()
        for index in range(count)
    ]


_SUITE = {
    "tests/backend/test_alpha.py": 120,
    "tests/backend/test_beta.py": 7,
    "tests/backend/test_gamma.py": 64,
    "tests/backend/test_delta.py": 1,
    "tests/backend/test_epsilon.py": 39,
    "tests/backend/annotation_validation/test_zeta.py": 18,
}


# ── The pure partition ──────────────────────────────────────────────────


def test_partition_is_total_and_disjoint() -> None:
    weights = {module: count for module, count in _SUITE.items()}
    assignment = assign_modules_to_shards(weights, 3)

    assert set(assignment) == set(weights), "every module must be assigned"
    assert set(assignment.values()) <= {0, 1, 2}
    # A dict cannot hold a module twice, so disjointness is really the claim that
    # each module maps to exactly one shard index — an int, not a collection.
    assert all(isinstance(shard, int) for shard in assignment.values())


def test_partition_is_independent_of_input_ordering() -> None:
    """Every matrix leg computes this alone; they must all agree."""
    forward = assign_modules_to_shards(dict(_SUITE), 3)
    reversed_order = assign_modules_to_shards(dict(reversed(list(_SUITE.items()))), 3)

    assert forward == reversed_order


def test_partition_balances_by_weight() -> None:
    """Not exact balance — just enough that no shard carries the whole suite."""
    assignment = assign_modules_to_shards(dict(_SUITE), 3)
    loads = Counter()
    for module, shard in assignment.items():
        loads[shard] += _SUITE[module]

    total = sum(_SUITE.values())
    assert len(loads) == 3, "no shard may end up empty for a suite this size"
    assert max(loads.values()) <= total * 0.55, (
        f"shard loads {dict(loads)} are too skewed against a total of {total}"
    )


def test_single_shard_keeps_everything_together() -> None:
    assignment = assign_modules_to_shards(dict(_SUITE), 1)

    assert set(assignment.values()) == {0}


@pytest.mark.parametrize("num_shards", [0, -1])
def test_partition_rejects_a_nonsense_shard_count(num_shards: int) -> None:
    with pytest.raises(ValueError, match="num_shards must be >= 1"):
        assign_modules_to_shards(dict(_SUITE), num_shards)


@pytest.mark.parametrize(
    ("nodeid", "expected"),
    [
        ("tests/backend/test_x.py::test_y", "tests/backend/test_x.py"),
        ("tests/backend/test_x.py::TestC::test_y[a::b]", "tests/backend/test_x.py"),
        ("tests/backend/test_x.py", "tests/backend/test_x.py"),
    ],
)
def test_module_id_splits_on_the_first_separator(nodeid: str, expected: str) -> None:
    assert module_id(_FakeItem(nodeid)) == expected


# ── Plugin routing ──────────────────────────────────────────────────────


def _run_plugin(shard_id: int, num_shards: int) -> tuple[list[str], list[str]]:
    """Return ``(kept, deselected)`` node ids for one shard of ``_SUITE``."""
    items = _items(_SUITE)
    config = _FakeConfig()
    BackendShardPlugin(shard_id, num_shards).pytest_collection_modifyitems(config, items)  # type: ignore[arg-type]
    return [item.nodeid for item in items], [item.nodeid for item in config.hook.deselected]


def test_every_test_runs_in_exactly_one_shard() -> None:
    """The property the whole mechanism exists to preserve."""
    everything = {item.nodeid for item in _items(_SUITE)}

    seen: Counter[str] = Counter()
    for shard_id in (1, 2, 3):
        kept, _ = _run_plugin(shard_id, 3)
        seen.update(kept)

    assert set(seen) == everything, "the shards must cover the whole suite"
    duplicated = [nodeid for nodeid, count in seen.items() if count != 1]
    assert not duplicated, f"these tests would run more than once: {duplicated[:5]}"


def test_a_shard_deselects_exactly_what_it_did_not_keep() -> None:
    """Deselection is reported, so the log's counts stay honest."""
    everything = {item.nodeid for item in _items(_SUITE)}
    kept, deselected = _run_plugin(2, 3)

    assert set(kept) | set(deselected) == everything
    assert not set(kept) & set(deselected)
    assert deselected, "a 3-way split of this suite cannot keep everything"


def test_a_modules_tests_are_never_split_across_shards() -> None:
    """Module cohesion is what preserves in-file ordering and shared setup."""
    for shard_id in (1, 2, 3):
        kept, deselected = _run_plugin(shard_id, 3)
        kept_modules = {nodeid.split("::", 1)[0] for nodeid in kept}
        dropped_modules = {nodeid.split("::", 1)[0] for nodeid in deselected}
        assert not kept_modules & dropped_modules


def test_report_header_names_the_shard() -> None:
    assert BackendShardPlugin(2, 3).pytest_report_header() == "backend shard: 2 of 3"


# ── The real command line ───────────────────────────────────────────────

# The mini-suite is generated rather than pointed at real modules: collecting
# even one real package here costs ~11 seconds, and this file is itself part of
# the suite it is protecting.
_MINI_CONFTEST = """\
import sys

sys.path.insert(0, {repo_root!r})

from tests.conftest import _configure_sharding, pytest_addoption  # noqa: F401


def pytest_configure(config):
    _configure_sharding(config)
"""

_MINI_MODULE_TEST_COUNTS = (11, 2, 7, 1, 5)
_NODEID = re.compile(r"^test_mod\d+\.py::test_\d+$")


def _write_mini_suite(root: Path) -> set[str]:
    (root / "conftest.py").write_text(
        _MINI_CONFTEST.format(repo_root=str(_REPO_ROOT)), encoding="utf-8"
    )
    expected: set[str] = set()
    for index, count in enumerate(_MINI_MODULE_TEST_COUNTS):
        name = f"test_mod{index}.py"
        body = "\n".join(f"def test_{n}():\n    assert True\n" for n in range(count))
        (root / name).write_text(body, encoding="utf-8")
        expected.update(f"{name}::test_{n}" for n in range(count))
    return expected


def _collect(root: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            str(root),
            "-q",
            "--collect-only",
            "-p",
            "no:cacheprovider",
            *extra,
        ],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def _nodeids(result: subprocess.CompletedProcess[str]) -> set[str]:
    return {line.strip() for line in result.stdout.splitlines() if _NODEID.match(line.strip())}


def test_the_ci_command_line_partitions_a_real_pytest_run(tmp_path: Path) -> None:
    """End-to-end: the flags ci.yml passes reach the plugin and split the run."""
    expected = _write_mini_suite(tmp_path)

    baseline = _collect(tmp_path)
    assert baseline.returncode == 0, baseline.stdout + baseline.stderr
    assert _nodeids(baseline) == expected, "the unsharded baseline must collect everything"

    per_shard = {}
    for shard_id in (1, 2, 3):
        result = _collect(tmp_path, "--num-shards", "3", "--shard-id", str(shard_id))
        assert result.returncode == 0, result.stdout + result.stderr
        per_shard[shard_id] = _nodeids(result)

    union: set[str] = set()
    for shard_id, collected in per_shard.items():
        assert collected, f"shard {shard_id} collected nothing"
        assert not union & collected, f"shard {shard_id} repeats tests from an earlier shard"
        union |= collected

    assert union == expected, "sharding must not lose a single test"


def test_the_log_names_which_shard_ran(tmp_path: Path) -> None:
    """Without this line a green log gives no clue that 2/3 of the suite is elsewhere.

    ``-q`` suppresses the report header, so this is the one collection that runs
    at the workflow's own verbosity.
    """
    _write_mini_suite(tmp_path)

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            str(tmp_path),
            "--collect-only",
            "-p",
            "no:cacheprovider",
            "--num-shards",
            "3",
            "--shard-id",
            "2",
        ],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "backend shard: 2 of 3" in result.stdout


def test_a_shard_id_outside_the_matrix_fails_the_run(tmp_path: Path) -> None:
    """Fail closed: an unrunnable selector must stop, not silently run nothing."""
    _write_mini_suite(tmp_path)

    result = _collect(tmp_path, "--num-shards", "3", "--shard-id", "4")

    assert result.returncode != 0
    assert "--shard-id must be between 1 and --num-shards (3), got 4" in (
        result.stdout + result.stderr
    )


def test_a_zero_shard_count_fails_the_run(tmp_path: Path) -> None:
    _write_mini_suite(tmp_path)

    result = _collect(tmp_path, "--num-shards", "0")

    assert result.returncode != 0
    assert "--num-shards must be >= 1, got 0" in (result.stdout + result.stderr)

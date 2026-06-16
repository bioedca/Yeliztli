"""Guard for the vulture dead-code gate (#579).

ruff (E/F/I/UP) flags unused locals and imports but NOT unused module-level
constants/functions/methods, so that class of dead code shipped CI-invisible
(the #527/#535/#553/#561 cluster). The CI ``Vulture (dead code)`` lint step
closes the gap; these tests prove the wiring is intact and that the configured
detector actually flags the target class, so the gate can't silently rot into a
no-op (e.g. a min_confidence bumped so high nothing is reported).
"""

from __future__ import annotations

import subprocess
import sys
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_vulture_config_is_wired() -> None:
    """[tool.vulture] exists, targets backend at the confidence that catches
    unused constants/functions, and includes the baseline whitelist as a path."""
    cfg = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    vulture = cfg["tool"]["vulture"]

    # Unused module-level constants/functions/methods are 60%-confidence findings
    # in vulture; a higher floor would silently stop catching the target class.
    assert vulture["min_confidence"] <= 60
    assert "backend" in vulture["paths"]
    # tests/scripts are analysed as usage sources so a backend symbol referenced
    # only by a test isn't a false positive.
    assert "tests" in vulture["paths"]
    # The reviewed baseline must be on the path so the current tree reports clean.
    assert "vulture_whitelist.py" in vulture["paths"]
    assert (REPO_ROOT / "vulture_whitelist.py").exists(), "baseline whitelist missing"


def test_vulture_detects_unused_module_symbols(tmp_path: Path) -> None:
    """The configured detector flags an unused module-level constant AND function
    — the exact class ruff misses — at the gate's confidence floor."""
    module = tmp_path / "dead_module.py"
    module.write_text(
        "UNUSED_CONSTANT = 123\n\n\ndef unused_function():\n    return 1\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [sys.executable, "-m", "vulture", str(module), "--min-confidence", "60"],
        capture_output=True,
        text=True,
        check=False,
    )
    # vulture exits non-zero when it finds dead code.
    assert result.returncode != 0, f"vulture found nothing:\n{result.stdout}\n{result.stderr}"
    assert "unused_function" in result.stdout
    assert "UNUSED_CONSTANT" in result.stdout


def test_vulture_config_keeps_pytest_decorator_exclusion() -> None:
    """[tool.vulture].ignore_decorators must keep a ``@pytest`` exclusion (#715).

    pytest fixtures/marks are invoked by the framework, not called directly, so
    vulture reports them as unused functions/methods. The blocking gate reddened
    main *twice* when this exclusion was missing (#684; independently re-derived
    in the closed duplicate #706). This config assertion fails if a future edit
    narrows the decorator list or drops the glob, so the gate can't rot a third
    time.
    """
    cfg = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    ignore_decorators = cfg["tool"]["vulture"]["ignore_decorators"]
    assert any(d.startswith("@pytest") for d in ignore_decorators), (
        f"vulture ignore_decorators lost its @pytest exclusion: {ignore_decorators}"
    )


def test_configured_ignore_decorators_suppress_pytest_fixtures(tmp_path: Path) -> None:
    """The repo's *actual* configured ``ignore_decorators`` must suppress a pytest
    fixture, including an ``autouse=True`` one — the exact false positive that broke
    the gate (#684, #715).

    Discriminating both ways: with no ignore, vulture flags the fixtures (positive
    control); the configured globs must silence them. A config narrowed to patterns
    that no longer cover ``@pytest`` fixtures fails this test before it can redden
    the blocking gate on main.
    """
    module = tmp_path / "conftest_like.py"
    module.write_text(
        "import pytest\n\n\n"
        "@pytest.fixture\n"
        "def some_fixture():\n"
        "    return 1\n\n\n"
        "@pytest.fixture(autouse=True)\n"
        "def _setup_complete():\n"
        "    yield\n",
        encoding="utf-8",
    )

    def _run(*extra: str) -> subprocess.CompletedProcess[str]:
        # cwd=tmp_path so vulture does NOT auto-load the repo's [tool.vulture]
        # config; the ignore set is supplied explicitly via --ignore-decorators.
        return subprocess.run(
            [
                sys.executable,
                "-m",
                "vulture",
                "conftest_like.py",
                "--min-confidence",
                "60",
                *extra,
            ],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            check=False,
        )

    # Positive control: with no ignore, vulture DOES flag the fixtures as unused.
    bare = _run()
    assert "some_fixture" in bare.stdout, (
        f"expected vulture to flag an un-ignored pytest fixture:\n{bare.stdout}\n{bare.stderr}"
    )

    # The repo's actual configured ignore_decorators must suppress them.
    cfg = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    ignore_decorators = ",".join(cfg["tool"]["vulture"]["ignore_decorators"])
    guarded = _run("--ignore-decorators", ignore_decorators)
    assert guarded.returncode == 0, (
        "configured ignore_decorators failed to suppress pytest fixtures:\n"
        f"{guarded.stdout}\n{guarded.stderr}"
    )
    assert "some_fixture" not in guarded.stdout
    assert "_setup_complete" not in guarded.stdout

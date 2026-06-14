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

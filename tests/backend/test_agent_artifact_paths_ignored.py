"""Guard: the artifact directories the agent contract names must actually be ignored.

``CLAUDE.md`` and ``AGENTS.md`` tell agents to keep sanitized Playwright output under
"ignored ``output/playwright/``", and the Playwright MCP plugin and CLI write
``.playwright-mcp/`` and ``.playwright-cli/`` into the repository root on every run.
Before #2279 none of those paths were in ``.gitignore``, so the contract's claim was
false against the code and ~9 MB of console logs and page snapshots accumulated as
untracked noise that a ``git add -A`` could sweep into a commit.

This asserts the *behavior* (``git check-ignore``) rather than the text of
``.gitignore``, so it stays true however the ignore rule is expressed — and it fails
on any tree where one of these paths stops being ignored.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# Representative path under each directory the agent contract or tooling writes to.
# A directory-level ignore rule is only useful if it covers files *inside* it, which is
# what these probe.
MUST_BE_IGNORED = (
    "output/playwright/flow-screenshot.png",
    "output/playwright/trace.zip",
    ".playwright-mcp/console-2026-01-01T00-00-00-000Z.log",
    ".playwright-cli/page-2026-01-01T00-00-00-000Z.yml",
)


def _is_ignored(path: str) -> bool:
    """True iff git would ignore ``path``. Exit 0 = ignored, 1 = not, >1 = error."""
    result = subprocess.run(
        ["git", "check-ignore", "-q", "--no-index", path],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    if result.returncode > 1:
        raise RuntimeError(f"git check-ignore failed for {path}: {result.stderr}")
    return result.returncode == 0


def test_agent_artifact_paths_are_gitignored() -> None:
    """Every directory the contract routes agent artifacts into is ignored."""
    not_ignored = [path for path in MUST_BE_IGNORED if not _is_ignored(path)]
    assert not not_ignored, (
        "These agent-artifact paths are NOT gitignored, so verification runs leave "
        "committable noise in the tree (see #2279):\n  " + "\n  ".join(not_ignored)
    )


def test_ignore_rules_do_not_shadow_tracked_files() -> None:
    """The ignore rules must not cover a file that is actually tracked.

    ``git check-ignore`` reports a match even for a tracked file, and a rule that
    shadows tracked content silently hides real edits from ``git status``. Both sides
    matter: the collection is asserted non-empty first so a failed listing cannot pass
    this test vacuously.
    """
    tracked = subprocess.run(
        ["git", "ls-files"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()
    assert tracked, "git ls-files returned nothing — the guard would pass vacuously"

    prefixes = ("output/", ".playwright-mcp/", ".playwright-cli/")
    shadowed = [path for path in tracked if path.startswith(prefixes)]
    assert not shadowed, (
        "The new ignore rules shadow tracked files, which would hide edits to them:\n  "
        + "\n  ".join(shadowed)
    )

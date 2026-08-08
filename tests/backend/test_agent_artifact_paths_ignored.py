"""Guard: the artifact directories the agent contract names must actually be ignored.

``CLAUDE.md`` and ``AGENTS.md`` tell agents to keep sanitized Playwright output under
"ignored ``output/playwright/``", and the Playwright MCP plugin and CLI write
``.playwright-mcp/`` and ``.playwright-cli/`` into the repository root on every run.
Before #2279 none of those paths were in ``.gitignore``, so the contract's claim was
false against the code and ~9 MB of console logs and page snapshots accumulated as
untracked noise that a ``git add -A`` could sweep into a commit.

These assert the *behavior* — git's own ignore matcher — rather than the text of
``.gitignore``, so they stay true however the rules are later spelled.

Two subtleties the matcher forces, both pinned by ``test_ignore_report_*`` below:

* Ignore matching also consults ``core.excludesFile`` and ``$GIT_DIR/info/exclude``, so
  a bare "is it ignored" probe passes on a tree where the committed rule was deleted but
  the developer or runner carries it locally. Requiring the match to come from
  ``.gitignore`` closes that: a ``.gitignore`` pattern outranks both other sources.
* ``git check-ignore -v`` also prints a record when the deciding pattern is a *negation*
  (``!``), which means the path is explicitly **not** ignored. Attribution therefore
  cannot be read from ``-v`` alone; the authoritative ignored set comes from plain
  ``check-ignore``, and ``-v`` is used only to say which pattern did it.
"""

from __future__ import annotations

import subprocess
from pathlib import Path, PurePosixPath

REPO_ROOT = Path(__file__).resolve().parents[2]

# A path under each directory the agent contract or tooling writes to. A directory-level
# ignore rule is only useful if it covers files *inside* it, which is what these probe.
#
# Deliberately EXTENSIONLESS. The real artifacts are `console-*.log`, `page-*.yml`,
# screenshots and traces, but probing those would let an unrelated `*.log` / `*.png` rule
# keep this guard green after the directory rules were deleted — leaving siblings such as
# `.playwright-mcp/snapshot.json` committable while the test still passed. An
# extensionless sentinel can only be matched by a rule that covers the directory itself,
# which is the behaviour being protected. The nested entry additionally pins that the rule
# reaches below the directory's top level.
MUST_BE_IGNORED = (
    "output/playwright/probe",
    "output/playwright/nested/probe",
    ".playwright-mcp/probe",
    ".playwright-cli/probe",
)

# Directory *names*, deliberately not ``.gitignore`` pattern text: git reports a pattern
# verbatim as written, so keying on the spelling would make the guard below silently
# vacuous the moment someone rewrote ``output/`` as ``/output/`` or ``output/**``.
ARTIFACT_DIRS = ("output", ".playwright-mcp", ".playwright-cli")

IGNORE_SOURCE = ".gitignore"


def _git(
    args: list[str], root: Path, stdin: str | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=root, input=stdin, capture_output=True, text=True)


def _ignore_report(
    paths: tuple[str, ...] | list[str], root: Path = REPO_ROOT
) -> dict[str, tuple[str, str]]:
    """Map each genuinely-ignored path to the ``(source, pattern)`` responsible.

    Paths git does not ignore are absent. ``-z`` is used so paths containing spaces or
    quotes are not mangled by git's path quoting.
    """
    if not paths:
        return {}
    payload = "\0".join(paths) + "\0"

    # Authoritative verdict: plain check-ignore lists only paths that ARE ignored.
    # exit 0 = some path matched, 1 = none did, >1 = real error.
    verdict = _git(["check-ignore", "-z", "--no-index", "--stdin"], root, payload)
    if verdict.returncode > 1:
        raise RuntimeError(f"git check-ignore failed: {verdict.stderr}")
    ignored = set(verdict.stdout.split("\0")[:-1])
    if not ignored:
        return {}

    # Attribution only. ``-v`` also reports negated matches (which mean NOT ignored),
    # so it is intersected with the authoritative set above rather than trusted alone.
    detail = _git(["check-ignore", "-z", "-v", "--no-index", "--stdin"], root, payload)
    if detail.returncode > 1:
        raise RuntimeError(f"git check-ignore -v failed: {detail.stderr}")
    fields = detail.stdout.split("\0")[:-1]
    if len(fields) % 4:
        raise RuntimeError(f"unexpected git check-ignore -z -v output: {detail.stdout!r}")

    return {
        fields[i + 3]: (fields[i], fields[i + 2])
        for i in range(0, len(fields), 4)
        if fields[i + 3] in ignored
    }


def _tracked_paths(root: Path = REPO_ROOT) -> list[str]:
    listing = _git(["ls-files", "-z"], root)
    if listing.returncode:
        raise RuntimeError(f"git ls-files failed: {listing.stderr}")
    return listing.stdout.split("\0")[:-1]


def test_agent_artifact_paths_are_gitignored() -> None:
    """Every directory the contract routes agent artifacts into is ignored by .gitignore."""
    report = _ignore_report(MUST_BE_IGNORED)

    problems: list[str] = []
    for path in MUST_BE_IGNORED:
        match = report.get(path)
        if match is None:
            problems.append(f"{path}: not ignored")
            continue
        source, pattern = match
        if source != IGNORE_SOURCE:
            problems.append(
                f"{path}: ignored by {source} (pattern {pattern!r}), not the committed "
                f"{IGNORE_SOURCE} — this machine's local excludes are masking a missing rule"
            )

    assert not problems, (
        "These agent-artifact paths are NOT gitignored by the committed .gitignore, so "
        "verification runs leave committable noise in the tree (see #2279):\n  "
        + "\n  ".join(problems)
    )


def test_artifact_dirs_hold_no_tracked_files() -> None:
    """No tracked file may live under an artifact directory these rules ignore.

    The harm is *not* that ignoring hides edits to an already-tracked file — it does not;
    such a file still shows up in ``git status`` and is still staged by ``git add -A``.
    The harm is for its neighbours: once the directory is ignored, a new file added
    beside it is silently skipped by ``git add -A`` and never listed as untracked. Keeping
    these directories free of tracked content stops that trap from ever arming.

    Membership is decided by git's matcher plus the directory *name*, so it holds for any
    spelling of the rule and catches nested cases — none of these patterns contains a
    slash except a trailing one, so ``output/`` also covers ``frontend/output/schema.json``.
    """
    tracked = _tracked_paths()
    assert tracked, "git ls-files returned nothing — the guard would pass vacuously"

    offenders = [
        f"{path} (ignored by {pattern!r})"
        for path, (source, pattern) in _ignore_report(tracked).items()
        if source == IGNORE_SOURCE
        and any(part in ARTIFACT_DIRS for part in PurePosixPath(path).parent.parts)
    ]

    assert not offenders, (
        "Tracked files live under the artifact directories #2279 ignores, so new files "
        "added beside them would be silently skipped by `git add -A`:\n  " + "\n  ".join(offenders)
    )


def _init_repo(root: Path, gitignore: str) -> None:
    subprocess.run(["git", "init", "-q", str(root)], check=True, capture_output=True)
    (root / ".gitignore").write_text(gitignore)


def test_ignore_report_excludes_negated_matches(tmp_path: Path) -> None:
    """A path re-included by a ``!`` rule must not be reported as ignored.

    ``git check-ignore -v`` prints a record for a negated match too — the path matched a
    pattern, that pattern just happens to un-ignore it. Reading ``-v`` alone therefore
    counts explicitly-committable paths as ignored, which would let
    :func:`test_agent_artifact_paths_are_gitignored` pass on a ``.gitignore`` that
    re-included every probe path.
    """
    _init_repo(tmp_path, "*.log\n!keep.log\n")

    report = _ignore_report(["drop.log", "keep.log"], root=tmp_path)

    assert "drop.log" in report, "a plainly ignored path must be reported"
    assert report["drop.log"] == (IGNORE_SOURCE, "*.log")
    assert "keep.log" not in report, (
        "a path re-included by a negation is NOT ignored, but was reported as ignored: "
        f"{report.get('keep.log')!r}"
    )


def test_ignore_report_attributes_the_source(tmp_path: Path) -> None:
    """A rule supplied only by local excludes must not be credited to .gitignore.

    This is the masking vector: a developer or self-hosted runner carrying ``output/`` in
    ``$GIT_DIR/info/exclude`` would otherwise make the guard pass on a tree where the
    committed rule had been deleted.
    """
    _init_repo(tmp_path, "# no rules here\n")
    exclude = tmp_path / ".git" / "info" / "exclude"
    exclude.parent.mkdir(parents=True, exist_ok=True)
    exclude.write_text("output/\n")

    report = _ignore_report(["output/playwright/x.png"], root=tmp_path)

    source, _pattern = report["output/playwright/x.png"]
    assert source != IGNORE_SOURCE, (
        "a rule coming from .git/info/exclude was credited to the committed .gitignore"
    )

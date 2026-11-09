"""Check literal dated science-evidence references in Git's index, offline.

Run after staging the intended change. Untracked/local-only evidence cannot
satisfy a reference; neither can a tracked sibling of a missing payload.
Only references outside data/ are scanned. This validates availability, not
scientific accuracy, licensing, or the authenticity of a claimed access date.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

EVIDENCE_ROOT = "data/science-evidence/"
_REFERENCE = re.compile(re.escape(EVIDENCE_ROOT) + r"[0-9]{4}-[0-9]{2}-[0-9]{2}-[A-Za-z0-9._/-]+")
# Exact source + target exceptions, never a source-file or packet-prefix bypass.
# The issue form demonstrates an evidence location, rather than citing a packet.
# The ignore-rule tests deliberately probe both committable and forbidden paths.
_EXAMPLES = {
    ".github/ISSUE_TEMPLATE/scientific_validity.yml": {EVIDENCE_ROOT + "2026-07-17-dpyd-c557ag"},
    "tests/backend/test_agent_artifact_paths_ignored.py": {
        EVIDENCE_ROOT + "2026-01-01-slug/" + leaf
        for leaf in (
            "README.md",
            "raw/payload.json",
            "raw/payload.jsonl.gz",
            "sample.db",
            "sample.db-wal",
            "sample.db-shm",
            ".env",
            ".env.local",
            "credentials.json",
        )
    },
}


def _git(root: Path, *args: str) -> bytes:
    return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True).stdout


def check_references(root: Path) -> tuple[int, list[str]]:
    """Return checked reference count and failures; Git errors fail closed."""
    files: set[str] = set()
    for entry in _git(root, "ls-files", "--stage", "-z").split(b"\0"):
        if not entry:
            continue
        metadata, name = entry.split(b"\t", 1)
        mode, _, stage = metadata.split()
        if stage != b"0":
            raise ValueError("Resolve the index's merge conflicts before validation")
        if mode in {b"100644", b"100755"}:
            files.add(name.decode("utf-8"))
    search = subprocess.run(
        [
            "git",
            "grep",
            "--cached",
            "--no-textconv",
            "-I",
            "-l",
            "-z",
            "-F",
            "-e",
            EVIDENCE_ROOT,
            "--",
            ".",
            ":(top,exclude)data/",
        ],
        cwd=root,
        capture_output=True,
    )
    if search.returncode not in {0, 1}:
        search.check_returncode()
    failures: list[str] = []
    checked = 0
    for name in search.stdout.split(b"\0"):
        if not name:
            continue
        source = name.decode("utf-8")
        content = _git(root, "show", ":" + source).decode("utf-8")
        for line_number, line in enumerate(content.splitlines(), 1):
            for match in _REFERENCE.finditer(line):
                # A terminal full stop is prose punctuation; a terminal slash
                # denotes a directory. Interior dots/slashes are kept intact.
                target = match.group().rstrip(".").rstrip("/")
                if target in _EXAMPLES.get(source, set()):
                    continue
                checked += 1
                traversal = ".." in match.group().split("/")
                present = target in files or any(path.startswith(target + "/") for path in files)
                if traversal or not present:
                    failures.append(f"{source}:{line_number}: untracked evidence {target}")
    return checked, failures


def main() -> int:
    try:
        count, failures = check_references(Path.cwd())
    except (subprocess.CalledProcessError, UnicodeError, ValueError) as error:
        print(f"Evidence-path validation failed: {error}", file=sys.stderr)
        return 2
    if failures:
        print("\n".join(failures), file=sys.stderr)
        return 1
    print(f"Checked {count} dated evidence references against Git's index")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

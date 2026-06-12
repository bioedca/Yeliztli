"""Guard against leaking private build-host pointers into the repository."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


def _bytes(*parts: str) -> bytes:
    return "".join(parts).encode()


def _literal(*parts: str) -> bytes:
    return re.escape(_bytes(*parts))


PRIVATE_BUILD_POINTERS: tuple[tuple[str, re.Pattern[bytes]], ...] = (
    ("private shared filesystem root", re.compile(_literal("/exports", "/people"))),
    ("private lab/account namespace", re.compile(_literal("mondragon", "lab"))),
    ("private cluster username", re.compile(_literal("ecc", "1695"))),
    (
        "private SSH host alias invocation",
        re.compile(rb"\bssh\s+" + _literal("tw", "o") + rb"\b"),
    ),
    (
        "private rsync/scp host target",
        re.compile(rb"\b" + _literal("tw", "o") + rb":(?=[~/])"),
    ),
    ("private cluster host FQDN", re.compile(_literal("tw", "o", ".", "am", "lab"))),
    (
        "private gateway host FQDN",
        re.compile(_literal("ze", "ro", ".", "bio", "chem")),
    ),
    ("private SLURM node mapping", re.compile(_literal("one", ",", "two"))),
    (
        "private gateway partition mapping",
        re.compile(_literal("compute") + rb"\s*=\s*" + _literal("ze", "ro")),
    ),
)


def _tracked_files() -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=REPO_ROOT,
        capture_output=True,
        check=True,
    )
    return [
        REPO_ROOT / raw.decode()
        for raw in result.stdout.split(b"\0")
        if raw
    ]


def _line_number(data: bytes, offset: int) -> int:
    return data.count(b"\n", 0, offset) + 1


def test_no_private_build_host_pointers_in_tracked_files() -> None:
    violations: list[str] = []

    for path in _tracked_files():
        rel = path.relative_to(REPO_ROOT)
        data = path.read_bytes()
        for label, pattern in PRIVATE_BUILD_POINTERS:
            for match in pattern.finditer(data):
                line = _line_number(data, match.start())
                violations.append(f"{rel}:{line}: {label}")

    if violations:
        pytest.fail(
            "Private build-host pointers must stay out of tracked files. "
            "Use operator-provided environment variables, placeholders, or "
            "gitignored local config instead.\n"
            + "\n".join(violations)
        )

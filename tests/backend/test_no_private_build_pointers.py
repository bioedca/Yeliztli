"""Guard against leaking private build-host pointers into the repository."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


PRIVATE_BUILD_POINTERS: tuple[tuple[str, re.Pattern[bytes]], ...] = (
    ("private shared filesystem root", re.compile(rb"/exports(?:/|$)")),
    (
        "literal SSH build host",
        re.compile(rb"\bssh\s+(?!alias\b)(?!['\"\$<])[A-Za-z0-9_.][-A-Za-z0-9_.]*(?=\s|$)"),
    ),
    (
        "literal remote copy target",
        re.compile(rb"\b(?:rsync|scp)\b[^\n]*\s[-A-Za-z0-9_.]+:(?=[~/])"),
    ),
)


def _tracked_files() -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=REPO_ROOT,
        capture_output=True,
        check=True,
    )
    return [REPO_ROOT / raw.decode() for raw in result.stdout.split(b"\0") if raw]


def _line_number(data: bytes, offset: int) -> int:
    return data.count(b"\n", 0, offset) + 1


def _sample(*parts: str) -> bytes:
    return "".join(parts).encode()


@pytest.mark.parametrize(
    ("label", "sample"),
    [
        ("private shared filesystem root", _sample("WORKDIR=/ex", "ports/private/build\n")),
        ("literal SSH build host", _sample("s", "sh build-host\n")),
        (
            "literal remote copy target",
            _sample("rs", "ync -av scripts/ build-host", ":~/scripts/\n"),
        ),
    ],
)
def test_private_build_pointer_patterns_catch_representative_leaks(
    label: str,
    sample: bytes,
) -> None:
    pattern = dict(PRIVATE_BUILD_POINTERS)[label]
    assert pattern.search(sample)


@pytest.mark.parametrize(
    "sample",
    [
        b'ssh "$LAI_BUILD_HOST"\n',
        b'rsync -av scripts/ "${LAI_BUILD_HOST}:${LAI_WORKDIR%/}/scripts/"\n',
    ],
)
def test_private_build_pointer_patterns_allow_operator_variables(sample: bytes) -> None:
    assert not any(pattern.search(sample) for _, pattern in PRIVATE_BUILD_POINTERS)


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
            "gitignored local config instead.\n" + "\n".join(violations)
        )

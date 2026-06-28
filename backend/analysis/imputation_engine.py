"""Shared binary resolution for the SW-C7 advanced imputation engines.

GLIMPSE2 (:mod:`backend.analysis.glimpse_runner`) and IMPUTE5
(:mod:`backend.analysis.impute5_runner`) are both **operator-installed external
tools** invoked via ``subprocess`` (never imported). This module centralises how
their binaries are located — from a configured directory or ``PATH`` — and how an
engine reports itself **available / unavailable** (never fatal when absent), so
both engines share one resolution policy (including the executability check).
"""

from __future__ import annotations

import os
import shutil
from collections.abc import Iterable
from pathlib import Path


def resolve_binary(name: str, bin_dir: Path | None = None) -> Path | None:
    """Resolve an engine binary by ``name`` from ``bin_dir`` then ``PATH``.

    Returns the executable's path, or ``None`` when it cannot be found (so the
    caller can report the engine unavailable rather than crash). Executability is
    required, not just existence — a non-executable placeholder in ``bin_dir`` is
    ignored so it can't shadow a real binary on ``PATH`` (``shutil.which`` already
    enforces ``X_OK``).
    """
    if bin_dir is not None:
        candidate = Path(bin_dir) / name
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate
    found = shutil.which(name)
    return Path(found) if found else None


def missing_binaries(required: Iterable[str], bin_dir: Path | None = None) -> list[str]:
    """Names of the ``required`` engine binaries that cannot be resolved."""
    return [n for n in required if resolve_binary(n, bin_dir) is None]


def engine_available(required: Iterable[str], bin_dir: Path | None = None) -> bool:
    """True iff every ``required`` engine binary resolves (never raises)."""
    return not missing_binaries(required, bin_dir)

#!/usr/bin/env python3
"""Bounded FastAPI TestClient startup witness for CI (#1965).

Enters the app's lifespan through Starlette's ``TestClient`` (the AnyIO portal
path, distinct from uvicorn's) and confirms ``/api/health`` answers 200, then
exits. It runs *before* the full backend suite on each interpreter in the
Python 3.12 / 3.13 matrix so a genuine startup hang surfaces as a fast, named
failure instead of a 20-minute suite timeout with no signal.

Context: #1892 traced a reported TestClient hang to the Codex restricted sandbox
(an ``AF_UNIX`` self-pipe ``EPERM``), not to Python 3.13 — the same witness
passed on 3.13.11 outside the sandbox. This script is that witness, wired into
required CI so the interpreter is continuously verified. Per #1965 it adds NO
application workaround for the sandbox restriction; it only exercises startup.

The caller is expected to impose the time bound (CI wraps this in ``timeout``),
because a true C-level hang would not honour an in-process ``signal.alarm``.
Exit 0 on success; non-zero (or killed by the outer bound) on failure.
"""

from __future__ import annotations

import os
import sys
import tempfile
from importlib.metadata import PackageNotFoundError, version


def _log_environment() -> None:
    """Record the interpreter and the resolved async-stack versions (#1965).

    ``flush=True`` because this must reach the CI log *before* the startup call
    below — if that call hangs and the outer ``timeout`` SIGKILLs the process,
    buffered stdout (CI is not a TTY) would otherwise be lost, discarding the
    version diagnostics for the exact failure this witness exists to catch.
    """
    print(f"python: {sys.version.split()[0]}", flush=True)
    for pkg in ("fastapi", "starlette", "anyio", "httpx", "pytest", "pytest-asyncio"):
        try:
            print(f"{pkg}: {version(pkg)}", flush=True)
        except PackageNotFoundError:
            print(f"{pkg}: <not installed>", flush=True)


def main() -> int:
    _log_environment()

    # Point the whole data root at a throwaway dir BEFORE importing the app, so
    # the witness never reads or writes the caller's real ~/.yeliztli. The app's
    # DEFAULT_DATA_DIR (and the data-dir pointer / config.toml paths) derive from
    # Path.home(), so overriding HOME isolates all of them. Startup writes a
    # reference.db, huey.db, and samples/ here — in CI the witness shares its
    # runner HOME with the pytest step that follows, and a leaked data dir breaks
    # require_fresh_sample tests (both interpreter legs failed on exactly that,
    # while the local run passed only because witness and suite used separate
    # temp HOMEs). ignore_cleanup_errors: a still-open sqlite handle must not turn
    # a passing witness into a teardown error.
    with tempfile.TemporaryDirectory(
        prefix="yeliztli-witness-", ignore_cleanup_errors=True
    ) as isolated_home:
        os.environ["HOME"] = isolated_home

        # Imported lazily (after HOME is set) so the version log still prints if
        # an import fails, and so DEFAULT_DATA_DIR resolves into the isolated dir.
        from fastapi.testclient import TestClient

        from backend.main import app

        with TestClient(app) as client:  # __enter__ drives the lifespan startup
            response = client.get("/api/health")

    if response.status_code != 200:
        print(f"FAIL: /api/health returned {response.status_code}", file=sys.stderr)
        return 1

    print("OK: FastAPI TestClient startup witness passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

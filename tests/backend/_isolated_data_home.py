"""Isolation of the default data home for the backend suite (#2079).

``backend.config`` resolves every default path from the user's home:
``Settings.data_dir`` defaults to ``~/.yeliztli``, and the data-dir pointer and
``config.toml`` are read from the live ``DEFAULT_DATA_DIR``. Tests isolate by
building ``Settings(data_dir=tmp)`` and patching ``get_registry``/``get_settings``
per route module, so any code path a test did not patch fell through to the real
``~/.yeliztli``. ``Settings(data_dir=tmp)`` also still read every other field
from the real ``config.toml``: an ``auth_enabled`` install turned API tests into
401s. ``YELIZTLI_*`` settings variables in a developer's shell and the project
``.env`` leak the same way, and ``YELIZTLI_DATA_DIR`` outranks everything below.
The suite was green only while that directory was absent and the shell was clean.

Two layers, both driven by ``tests/backend/conftest.py``:

* :func:`start_session` runs when that conftest is imported, before any test
  module is. Importing ``backend.main`` imports ``backend.tasks.huey_tasks``,
  which calls ``get_settings()`` and binds the global Huey to
  ``data_dir/huey.db`` at import time, so isolation has to exist by then.
* :func:`isolate_data_home` then gives every test its own empty home, so one
  test's leftovers are not the next one's state.

``data_dir`` is redirected with a data-dir pointer, not ``YELIZTLI_DATA_DIR``:
the env var outranks the pointer, so it would mask every test that exercises the
pointer or a relocated install. Those tests patch ``DEFAULT_DATA_DIR`` to their
own home, so they never see this pointer and keep working unchanged.

Tests that must read the real home (``REAL_DATA_MARKERS``) get it back for their
whole module via :func:`restore_real_data_home`; see the conftest.
"""

from __future__ import annotations

import itertools
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

import pytest

# Tests that must read the real data home. The nightly slow tier stages the
# production VEP/LAI bundles into ``~/.yeliztli`` and these tests locate them
# through ``get_settings()``; isolating them would make them skip as "bundle
# missing" instead of running.
REAL_DATA_MARKERS = frozenset({"requires_real_bundle"})

# The committed bundle manifest. Without YELIZTLI_MANIFEST_PATH, every
# get_bundle_info() call downloaded the live one from GitHub's main branch, so
# results depended on the network and on what was published there (#2538). This
# is the same file the live URL serves from main, and a branch is now tested
# against its own copy.
REPO_MANIFEST = Path(__file__).resolve().parents[2] / "bundles" / "manifest.json"

_session: dict[str, Any] = {}
_test_numbers = itertools.count()


def uses_real_data_home(node: Any) -> bool:
    """True when ``node`` carries a marker that needs the real data home."""
    return any(node.get_closest_marker(marker) for marker in REAL_DATA_MARKERS)


def settings_env_names() -> frozenset[str]:
    """Upper-cased env names ``Settings`` reads (``case_sensitive`` is off).

    Only these are cleared. Other ``YELIZTLI_*`` variables are harness or
    module controls read straight from the environment, such as
    ``YELIZTLI_UPDATE_GOLDEN``, which regenerates the golden snapshot.
    """
    from backend import config

    names = {config.ENV_PREFIX + field for field in config.Settings.model_fields}
    names.add(config.NCBI_API_KEY_ENV_ALIAS)
    return frozenset(name.upper() for name in names)


def isolate_data_home(monkeypatch: pytest.MonkeyPatch, home: Path) -> None:
    """Resolve default data paths into ``home`` until ``monkeypatch`` is undone.

    Ambient settings variables (:func:`settings_env_names`) and the project
    ``.env`` are masked; a test that needs either sets it itself. Patching
    ``DEFAULT_DATA_DIR`` moves the pointer and ``config.toml`` sources and the
    ``data_dir`` default, which is read from it when settings are built (#2533).
    A pointer at ``home`` records the same directory for code that reads it.

    The pointer is written before ``DEFAULT_DATA_DIR`` moves, so a thread an
    earlier test left running never sees a home without its pointer. While the
    default was frozen to the real home, such a home resolved ``data_dir``
    there (#2529). The cached settings and the ``get_registry()`` singleton,
    which keeps the ``data_dir`` it was built with, are dropped last, so
    neither a real nor an earlier value is reused.
    """
    from backend import config
    from backend.db.connection import reset_registry

    names = settings_env_names()
    for key in [key for key in os.environ if key.upper() in names]:
        monkeypatch.delenv(key)
    monkeypatch.setitem(config.Settings.model_config, "env_file", None)
    home.mkdir(parents=True, exist_ok=True)
    (home / config.DATA_DIR_POINTER_NAME).write_text(str(home), encoding="utf-8")
    monkeypatch.setattr(config, "DEFAULT_DATA_DIR", home)
    config.get_settings.cache_clear()
    reset_registry()


def session_root() -> Path:
    """Directory holding the session home and every per-test home."""
    return _session["root"]


def new_test_home() -> Path:
    """A fresh home under :func:`session_root`, without rescanning siblings."""
    return session_root() / f"test-{next(_test_numbers)}" / ".yeliztli"


def start_session() -> None:
    """Isolate the whole session, capturing the real state first."""
    if _session:
        return
    from backend import config

    names = settings_env_names()
    _session["real"] = {
        "home": config.DEFAULT_DATA_DIR,
        "env": {key: value for key, value in os.environ.items() if key.upper() in names},
        "env_file": config.Settings.model_config.get("env_file"),
    }
    _session["root"] = Path(tempfile.mkdtemp(prefix="yeliztli-test-homes-"))
    _session["monkeypatch"] = monkeypatch = pytest.MonkeyPatch()
    isolate_data_home(monkeypatch, session_root() / "session" / ".yeliztli")
    from backend.db import manifest

    monkeypatch.setenv(manifest.MANIFEST_PATH_ENV, str(REPO_MANIFEST))


def finish_session() -> None:
    """Undo :func:`start_session` and delete every throwaway home."""
    if not _session:
        return
    _session["monkeypatch"].undo()
    shutil.rmtree(_session["root"], ignore_errors=True)
    _session.clear()
    from backend import config

    config.get_settings.cache_clear()


def restore_real_data_home(monkeypatch: pytest.MonkeyPatch) -> None:
    """Point default data paths back at the real home until undone."""
    from backend import config

    real = _session["real"]
    names = settings_env_names()
    for key in [key for key in os.environ if key.upper() in names]:
        monkeypatch.delenv(key)
    for key, value in real["env"].items():
        monkeypatch.setenv(key, value)
    monkeypatch.setitem(config.Settings.model_config, "env_file", real["env_file"])
    monkeypatch.setattr(config, "DEFAULT_DATA_DIR", real["home"])
    config.get_settings.cache_clear()
    from backend.db.connection import reset_registry

    reset_registry()

"""Per-test isolation of the default data home (#2079).

``backend.config`` resolves every default path from the user's home:
``Settings.data_dir`` defaults to ``~/.yeliztli``, and the data-dir pointer and
``config.toml`` are read from the live ``DEFAULT_DATA_DIR``. Tests isolate by
building ``Settings(data_dir=tmp)`` and patching ``get_registry``/``get_settings``
per route module, so any code path a test did not patch fell through to the real
``~/.yeliztli``. ``Settings(data_dir=tmp)`` also still read every other field
from the real ``config.toml``: an ``auth_enabled`` install turned API tests into
401s. ``YELIZTLI_*`` variables in a developer's shell leak the same way, and
``YELIZTLI_DATA_DIR`` outranks everything below. The suite was green only while
that directory was absent and the shell was clean.

:func:`isolate_data_home` points all three at a throwaway directory so an
unpatched path resolves somewhere empty and disposable. The per-test patches stay
as defense in depth rather than the only isolation.

It redirects ``data_dir`` with a data-dir pointer, not ``YELIZTLI_DATA_DIR``:
the env var outranks the pointer, so it would mask every test that exercises the
pointer or a relocated install. Those tests patch ``DEFAULT_DATA_DIR`` to their
own home, so they never see this pointer and keep working unchanged.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

# Tests that must read the real data home. The nightly slow tier stages the
# production VEP/LAI bundles into ``~/.yeliztli`` and these tests locate them
# through ``get_settings()``; isolating them would make them skip as "bundle
# missing" instead of running.
REAL_DATA_MARKERS = frozenset({"requires_real_bundle"})


def uses_real_data_home(node: Any) -> bool:
    """True when ``node`` carries a marker that needs the real data home."""
    return any(node.get_closest_marker(marker) for marker in REAL_DATA_MARKERS)


def isolate_data_home(monkeypatch: pytest.MonkeyPatch, home: Path) -> None:
    """Resolve default data paths into ``home`` for the rest of the test.

    Ambient ``YELIZTLI_*`` variables are removed first; a test that needs one
    sets it itself. Patching ``DEFAULT_DATA_DIR`` moves the pointer and
    ``config.toml`` sources, and a pointer at ``home`` then overrides the
    ``data_dir`` field default, which was bound to the real home when
    ``Settings`` was defined. The cached settings are cleared so neither a real
    nor an earlier test's value is reused.
    """
    from backend import config

    for name in [key for key in os.environ if key.startswith(config.ENV_PREFIX)]:
        monkeypatch.delenv(name)
    monkeypatch.setattr(config, "DEFAULT_DATA_DIR", home)
    config.write_data_dir_pointer(home)
    config.get_settings.cache_clear()

"""Root-level pytest conftest — project-wide fixtures and markers."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from backend.config import Settings


def _java_available() -> bool:
    """Return True if a Java runtime is on PATH."""
    return shutil.which("java") is not None


def _real_lai_bundle_available() -> bool:
    """Return True if the production LAI bundle is present and validates locally.

    Used to auto-skip ``@pytest.mark.requires_real_bundle`` tests on dev
    machines and PR-blocking CI. The nightly slow-tier workflow (step 42)
    downloads the bundle into ``data_dir/lai_bundle/`` before invoking
    ``pytest -m slow``, at which point this returns True and the dormant
    tests execute.
    """
    try:
        from backend.config import get_settings
        from backend.db.database_registry import validate_lai_bundle
    except Exception:
        return False
    try:
        bundle_path = get_settings().resolved_lai_bundle_path
    except Exception:
        return False
    return validate_lai_bundle(bundle_path)


# Heuristic floor for "is this the real production VEP bundle, not a mini fixture?"
# Production releases are hundreds of MB; anything smaller than 100 MB is treated
# as a development stub and the slow-tier test stays dormant.
_REAL_VEP_BUNDLE_MIN_BYTES = 100_000_000


def _real_vep_bundle_available() -> bool:
    """Return True if the production VEP bundle is present at the expected path.

    Mirrors :func:`_real_lai_bundle_available` for the VEP-only nightly
    real-bundle test (step 42). Dev machines and PR-blocking CI typically have
    no bundle (or only the ~12 MB mini bundle baked into ``tests/fixtures/``);
    the nightly workflow downloads the real release asset into
    ``data_dir/vep_bundle.db`` before invoking ``pytest -m slow``, at which
    point this returns True and the dormant tests execute.
    """
    try:
        from backend.config import get_settings
    except Exception:
        return False
    try:
        bundle_path = get_settings().vep_bundle_db_path
    except Exception:
        return False
    try:
        return bundle_path.is_file() and bundle_path.stat().st_size >= _REAL_VEP_BUNDLE_MIN_BYTES
    except OSError:
        return False


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Auto-skip tests whose runtime prerequisites are not present locally.

    - ``requires_java``: skipped when no Java runtime is on PATH.
    - ``requires_real_bundle``: skipped when *neither* the production LAI
      bundle *nor* the production VEP bundle is present locally — so the
      slow-tier tests stay dormant on every PR-blocking run and only
      activate inside the nightly workflow (step 42). Tests still
      individually ``pytest.skip()`` when their specific bundle is the one
      that's missing, which lets a developer who only has one of the two
      bundles still exercise the matching test class.
    """
    java_ok = _java_available()
    real_bundle_ok = _real_lai_bundle_available() or _real_vep_bundle_available()
    skip_java = pytest.mark.skip(reason="Java runtime not available")
    skip_real_bundle = pytest.mark.skip(
        reason="Real production bundle not available (slow-tier nightly only)"
    )
    for item in items:
        if "requires_java" in item.keywords and not java_ok:
            item.add_marker(skip_java)
        if "requires_real_bundle" in item.keywords and not real_bundle_ok:
            item.add_marker(skip_real_bundle)


# ── Custom Markers ───────────────────────────────────────────────────


def pytest_configure(config: pytest.Config) -> None:
    """Register custom markers to avoid 'unknown marker' warnings."""
    config.addinivalue_line(
        "markers",
        "slow: marks tests as slow (deselect with '-m not slow')",
    )
    config.addinivalue_line("markers", "e2e: marks end-to-end tests")
    config.addinivalue_line("markers", "integration: marks integration tests")
    config.addinivalue_line(
        "markers",
        "requires_java: marks tests that need a real Java runtime (skipped when unavailable)",
    )
    config.addinivalue_line(
        "markers",
        "requires_real_bundle: marks tests that need the real production LAI/VEP "
        "bundle on disk (skipped when absent; consumed by the nightly slow-tier workflow)",
    )


# ── Project-wide Fixtures ────────────────────────────────────────────


@pytest.fixture
def tmp_data_dir(tmp_path: Path) -> Path:
    """Create a temporary data directory mimicking ~/.yeliztli layout.

    Creates the standard subdirectories (samples, downloads, logs) so that
    Settings and DBRegistry can operate without error.
    """
    (tmp_path / "samples").mkdir()
    (tmp_path / "downloads").mkdir()
    (tmp_path / "logs").mkdir()
    return tmp_path


@pytest.fixture
def test_settings(tmp_data_dir: Path) -> Settings:
    """Return a Settings instance pointing at the temporary data directory.

    WAL mode is disabled for in-memory / temp-file SQLite to avoid
    PRAGMA errors that do not apply to ephemeral databases.
    """
    return Settings(data_dir=tmp_data_dir, wal_mode=False)

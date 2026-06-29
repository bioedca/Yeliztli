"""Tests for ``backend.services.staleness`` (Plan §7.4 step 3, ADNA-00c part 4).

Locks the staleness service contract:

* ``is_sample_stale`` returns ``True`` iff the per-sample
  ``annotation_state.vep_bundle_version`` has a strictly lower **major**
  ``packaging.version.Version`` than the installed
  ``database_versions['vep_bundle']``.
* Minor / patch differences are NOT stale.
* Missing per-sample state (table absent, row absent, malformed value)
  is treated as ``v1.0.0`` and emits a structured
  ``annotation_state_missing`` warning.
* The helper never raises on a malformed per-sample DB.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import pytest
import sqlalchemy as sa
from structlog.testing import capture_logs

from backend.config import Settings
from backend.db.connection import reset_registry
from backend.db.sample_schema import create_sample_tables
from backend.db.tables import (
    annotation_state,
    database_versions,
    reference_metadata,
    samples,
)
from backend.services.staleness import _parse_reference_versions, is_sample_stale

# ── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
def staleness_env(tmp_data_dir: Path):
    """Reference DB seeded with one sample row; no per-sample DB yet."""
    settings = Settings(data_dir=tmp_data_dir, wal_mode=False)

    ref_path = settings.reference_db_path
    ref_engine = sa.create_engine(f"sqlite:///{ref_path}")
    reference_metadata.create_all(ref_engine)
    with ref_engine.begin() as conn:
        conn.execute(
            samples.insert().values(
                id=1,
                name="Sample 1",
                db_path="samples/sample_1.db",
                file_format="23andme_v5",
                file_hash="abc",
            )
        )
    ref_engine.dispose()

    with patch("backend.db.connection.get_settings", return_value=settings):
        reset_registry()
        yield {"settings": settings, "sample_id": 1}
        reset_registry()


def _seed_installed_bundle(settings: Settings, version: str) -> None:
    engine = sa.create_engine(f"sqlite:///{settings.reference_db_path}")
    try:
        with engine.begin() as conn:
            conn.execute(
                database_versions.insert().values(
                    db_name="vep_bundle",
                    version=version,
                    downloaded_at=datetime.now(UTC),
                )
            )
    finally:
        engine.dispose()


def _make_sample_db(
    settings: Settings,
    *,
    create_state_table: bool = True,
    seed_version: str | None = None,
) -> None:
    """Materialise ``samples/sample_1.db`` in three shapes:

    * ``create_state_table=True`` + ``seed_version=None`` → table present,
      no ``vep_bundle_version`` row.
    * ``create_state_table=True`` + ``seed_version="v1.0.0"`` → fully
      seeded.
    * ``create_state_table=False`` → empty SQLite file with a placeholder
      table only; ``annotation_state`` query raises ``OperationalError``.
    """
    sample_db_path = settings.data_dir / "samples" / "sample_1.db"
    sample_db_path.parent.mkdir(parents=True, exist_ok=True)
    engine = sa.create_engine(f"sqlite:///{sample_db_path}")
    try:
        if create_state_table:
            create_sample_tables(engine)
            if seed_version is not None:
                with engine.begin() as conn:
                    conn.execute(
                        annotation_state.insert().values(
                            key="vep_bundle_version",
                            value=seed_version,
                        )
                    )
        else:
            # Forces a valid SQLite file on disk so the engine can open
            # it, but ``annotation_state`` is absent.
            with engine.begin() as conn:
                conn.execute(sa.text("CREATE TABLE _placeholder (id INTEGER)"))
    finally:
        engine.dispose()


def _has_event(cap_logs: list[dict], event: str) -> bool:
    return any(entry.get("event") == event for entry in cap_logs)


# ── Tests ─────────────────────────────────────────────────────────────


class TestIsSampleStale:
    def test_fresh_sample_not_stale(self, staleness_env):
        _seed_installed_bundle(staleness_env["settings"], "v2.0.0")
        _make_sample_db(staleness_env["settings"], seed_version="v2.0.0")

        assert is_sample_stale(staleness_env["sample_id"]) is False

    def test_minor_patch_difference_not_stale(self, staleness_env):
        """Same major across installed + sample → fresh, regardless of minor/patch."""
        _seed_installed_bundle(staleness_env["settings"], "v2.3.1")
        _make_sample_db(staleness_env["settings"], seed_version="v2.0.0")

        assert is_sample_stale(staleness_env["sample_id"]) is False

    def test_stale_when_sample_major_lower(self, staleness_env):
        _seed_installed_bundle(staleness_env["settings"], "v2.0.0")
        _make_sample_db(staleness_env["settings"], seed_version="v1.0.0")

        assert is_sample_stale(staleness_env["sample_id"]) is True

    def test_missing_annotation_state_table_treats_as_v1(self, staleness_env):
        """Plan §7.4 fallback — missing table → treat as v1.0.0; gate fires."""
        _seed_installed_bundle(staleness_env["settings"], "v2.0.0")
        _make_sample_db(staleness_env["settings"], create_state_table=False)

        with capture_logs() as cap_logs:
            result = is_sample_stale(staleness_env["sample_id"])

        assert result is True
        assert _has_event(cap_logs, "annotation_state_missing")

    def test_missing_table_not_stale_against_v1(self, staleness_env):
        """Fallback v1.0.0 matches installed v1.0.0 → not stale."""
        _seed_installed_bundle(staleness_env["settings"], "v1.0.0")
        _make_sample_db(staleness_env["settings"], create_state_table=False)

        with capture_logs() as cap_logs:
            result = is_sample_stale(staleness_env["sample_id"])

        assert result is False
        # Warning still fires — the *missing* state is what we log; the
        # gate decision is downstream.
        assert _has_event(cap_logs, "annotation_state_missing")

    def test_missing_vep_bundle_version_row_treats_as_v1(self, staleness_env):
        _seed_installed_bundle(staleness_env["settings"], "v2.0.0")
        _make_sample_db(staleness_env["settings"], seed_version=None)

        with capture_logs() as cap_logs:
            result = is_sample_stale(staleness_env["sample_id"])

        assert result is True
        assert _has_event(cap_logs, "annotation_state_missing")

    def test_malformed_recorded_version_treats_as_v1(self, staleness_env):
        """A recorded value that ``Version`` can't parse falls back to v1.0.0."""
        _seed_installed_bundle(staleness_env["settings"], "v2.0.0")
        _make_sample_db(staleness_env["settings"], seed_version="not-a-version")

        with capture_logs() as cap_logs:
            result = is_sample_stale(staleness_env["sample_id"])

        assert result is True
        assert _has_event(cap_logs, "annotation_state_missing")

    def test_does_not_raise_on_malformed_db(self, staleness_env):
        """Contract: never raises — Plan §7.4."""
        _seed_installed_bundle(staleness_env["settings"], "v2.0.0")
        _make_sample_db(staleness_env["settings"], create_state_table=False)

        # No exception even though the per-sample DB has no
        # annotation_state table.
        is_sample_stale(staleness_env["sample_id"])

    def test_missing_installed_version_returns_false(self, staleness_env):
        """When the installed bundle version is missing, decline to gate."""
        _make_sample_db(staleness_env["settings"], seed_version="v1.0.0")
        # No database_versions row seeded for vep_bundle.

        with capture_logs() as cap_logs:
            result = is_sample_stale(staleness_env["sample_id"])

        assert result is False
        assert _has_event(cap_logs, "vep_bundle_version_unreadable")

    def test_missing_sample_row_treats_as_v1(self, staleness_env):
        """A sample_id with no row in ``samples`` is treated as fallback v1.0.0."""
        _seed_installed_bundle(staleness_env["settings"], "v2.0.0")
        # No per-sample DB / sample row for id=999.

        with capture_logs() as cap_logs:
            result = is_sample_stale(999)

        assert result is True
        events = [e for e in cap_logs if e.get("event") == "annotation_state_missing"]
        assert events, "expected annotation_state_missing warning"
        assert events[0].get("reason") == "sample_row_missing"


# ── G1: vep_bundle re-annotation bump ─────────────────────────────────


def _repo_manifest_vep_version() -> str:
    """Read the vep_bundle ``version`` straight from the repo manifest."""
    repo_manifest = Path(__file__).resolve().parents[2] / "bundles" / "manifest.json"
    data = json.loads(repo_manifest.read_text(encoding="utf-8"))
    return data["bundles"]["vep_bundle"]["version"]


class TestG1ReannotationBump:
    """G1: the manifest vep_bundle version was bumped so ``is_sample_stale``
    re-flags every pre-existing sample for re-annotation through the corrected
    (carriage/zygosity + F25/F15) engine. The catalog is unchanged — only the
    version leads the asset tag.
    """

    # What pre-existing samples were annotated against before the bump.
    _PRIOR_MAJOR_VERSION = "v2.0.0"

    def test_manifest_major_exceeds_prior_so_v2_samples_reflag(self):
        """The bump must raise the major above the prior, or nothing re-flags."""
        from packaging.version import Version

        installed_major = Version(_repo_manifest_vep_version().lstrip("v")).major
        prior_major = Version(self._PRIOR_MAJOR_VERSION.lstrip("v")).major
        assert installed_major > prior_major

    def test_prior_major_sample_is_stale_after_bump(self, staleness_env):
        """A sample annotated at the prior major is stale once the bumped
        manifest version is the installed version → the re-annotation banner
        fires and a live re-run repopulates zygosity + the new columns."""
        installed = _repo_manifest_vep_version()
        _seed_installed_bundle(staleness_env["settings"], installed)
        _make_sample_db(staleness_env["settings"], seed_version=self._PRIOR_MAJOR_VERSION)

        assert is_sample_stale(staleness_env["sample_id"]) is True

    def test_sample_at_bumped_version_not_stale(self, staleness_env):
        """A sample re-annotated against the bumped version is no longer stale."""
        installed = _repo_manifest_vep_version()
        _seed_installed_bundle(staleness_env["settings"], installed)
        _make_sample_db(staleness_env["settings"], seed_version=installed)

        assert is_sample_stale(staleness_env["sample_id"]) is False


class TestReferenceVersionSnapshotParsing:
    def test_reference_snapshot_parses_compact_and_provenance_shapes(self):
        assert _parse_reference_versions(
            json.dumps(
                {
                    "gnomad": "4.1.0",
                    "clinvar": {"version": "20260601", "source": "metadata"},
                }
            )
        ) == {"gnomad": "4.1.0", "clinvar": "20260601"}

    def test_reference_snapshot_rejects_unsupported_nonempty_shapes(self):
        assert _parse_reference_versions(json.dumps({"gnomad": {"source": "metadata"}})) is None
        assert _parse_reference_versions(json.dumps({"gnomad": 41})) is None

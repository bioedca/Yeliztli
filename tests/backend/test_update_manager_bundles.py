"""Tests for manifest-driven bundle update checks + the CHECK_FNS dispatch.

Step 18 of docs/setup-update-steps.md adds ``check_lai_bundle_update`` and
``check_ancestry_pca_update``, plus the ``CHECK_FNS`` dispatch dict used by
the scheduler refactor in later steps. Each bundle check reads from
``bundles/manifest.json`` (via the ``YELIZTLI_MANIFEST_PATH`` override
for tests) and compares the manifest version against the
``database_versions`` row.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

from backend.db import manifest as manifest_mod
from backend.db.manifest import reset_cache
from backend.db.tables import database_versions
from backend.db.update_manager import (
    CHECK_FNS,
    VersionInfo,
    check_ancestry_pca_update,
    check_clinvar_update,
    check_gnomad_bundle_update,
    check_lai_bundle_update,
    check_vep_bundle_update,
)

SAMPLE_MANIFEST: dict = {
    "schema_version": 1,
    "generated_at": "2026-05-08T00:00:00Z",
    "bundles": {
        "lai_bundle": {
            "version": "v1.1",
            "build_date": "2026-04-07",
            "url": "https://example.com/lai.tar.gz",
            "sha256": "959ed0fd9ebe2ad8fa542776a59ce73072d928c7ce59839ea81d0f1e78a5c18e",
            "size_bytes": 523_801_111,
        },
        "ancestry_pca": {
            "version": "v1.0",
            "build_date": "2026-04-07",
            "url": "",
            "sha256": "3593c24dd32f67e87fa4d58653621a5b5c6635bcce569ead909386c3139ddf58",
            "size_bytes": 414_432,
        },
        "gnomad": {
            "version": "v1.0.0",
            "build_date": "2026-06-06",
            "url": "https://example.com/gnomad_af.db",
            "sha256": "11" * 32,
            "size_bytes": 2_000_000_123,
        },
        "vep_bundle": {
            "version": "v3.0.0",
            "build_date": "2026-06-01",
            "url": "https://example.com/vep_bundle.db",
            "sha256": "22" * 32,
            "size_bytes": 358_752_256,
        },
    },
    "pipeline_pins": {
        "clinvar": {
            "url": "https://ftp.ncbi.nlm.nih.gov/pub/clinvar/vcf_GRCh37/clinvar.vcf.gz",
            "last_known_version": "",
        },
    },
}


@pytest.fixture(autouse=True)
def _clear_manifest_cache_and_env(monkeypatch):
    """Each test starts with an empty manifest cache and no env override."""
    monkeypatch.delenv(manifest_mod.MANIFEST_PATH_ENV, raising=False)
    reset_cache()
    yield
    reset_cache()


def _write_manifest(tmp_path: Path, payload: dict) -> Path:
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _record_version_row(engine, db_name: str, version: str) -> None:
    with engine.begin() as conn:
        conn.execute(database_versions.insert().values(db_name=db_name, version=version))


# ── check_lai_bundle_update ───────────────────────────────────────────


class TestCheckLaiBundleUpdate:
    def test_manifest_newer_than_recorded_returns_version_info(
        self, tmp_path: Path, monkeypatch, reference_engine
    ):
        path = _write_manifest(tmp_path, SAMPLE_MANIFEST)
        monkeypatch.setenv(manifest_mod.MANIFEST_PATH_ENV, str(path))
        _record_version_row(reference_engine, "lai_bundle", "v1.0")

        result = check_lai_bundle_update(reference_engine)

        assert result is not None
        assert isinstance(result, VersionInfo)
        assert result.db_name == "lai_bundle"
        assert result.latest_version == "v1.1"
        assert result.download_url == "https://example.com/lai.tar.gz"
        assert result.download_size_bytes == 523_801_111
        assert result.release_date == "2026-04-07"

    def test_manifest_matches_recorded_returns_none(
        self, tmp_path: Path, monkeypatch, reference_engine
    ):
        path = _write_manifest(tmp_path, SAMPLE_MANIFEST)
        monkeypatch.setenv(manifest_mod.MANIFEST_PATH_ENV, str(path))
        _record_version_row(reference_engine, "lai_bundle", "v1.1")

        assert check_lai_bundle_update(reference_engine) is None

    def test_no_recorded_version_returns_version_info(
        self, tmp_path: Path, monkeypatch, reference_engine
    ):
        """Fresh install / unknown-pre-manifest backfill should be offered the update."""
        path = _write_manifest(tmp_path, SAMPLE_MANIFEST)
        monkeypatch.setenv(manifest_mod.MANIFEST_PATH_ENV, str(path))

        result = check_lai_bundle_update(reference_engine)

        assert result is not None
        assert result.latest_version == "v1.1"

    def test_unknown_pre_manifest_recorded_returns_version_info(
        self, tmp_path: Path, monkeypatch, reference_engine
    ):
        """Backfilled rows with 'unknown-pre-manifest' must surface as an update."""
        path = _write_manifest(tmp_path, SAMPLE_MANIFEST)
        monkeypatch.setenv(manifest_mod.MANIFEST_PATH_ENV, str(path))
        _record_version_row(reference_engine, "lai_bundle", "unknown-pre-manifest")

        result = check_lai_bundle_update(reference_engine)

        assert result is not None
        assert result.latest_version == "v1.1"

    def test_manifest_missing_bundle_entry_returns_none(
        self, tmp_path: Path, monkeypatch, reference_engine
    ):
        payload = json.loads(json.dumps(SAMPLE_MANIFEST))
        del payload["bundles"]["lai_bundle"]
        path = _write_manifest(tmp_path, payload)
        monkeypatch.setenv(manifest_mod.MANIFEST_PATH_ENV, str(path))

        assert check_lai_bundle_update(reference_engine) is None

    def test_manifest_unreachable_returns_none(self, reference_engine):
        """Manifest fetch failures degrade gracefully — no spurious update offers."""
        with patch(
            "backend.db.manifest.httpx.get",
            side_effect=httpx.ConnectError("nope"),
        ):
            assert check_lai_bundle_update(reference_engine) is None

    def test_settings_argument_accepted_and_ignored(
        self, tmp_path: Path, monkeypatch, reference_engine
    ):
        """Signature parity: settings is accepted but not required."""
        path = _write_manifest(tmp_path, SAMPLE_MANIFEST)
        monkeypatch.setenv(manifest_mod.MANIFEST_PATH_ENV, str(path))
        _record_version_row(reference_engine, "lai_bundle", "v1.1")

        # Both call shapes return None (up to date).
        assert check_lai_bundle_update(reference_engine, None) is None
        assert check_lai_bundle_update(reference_engine, settings=object()) is None


# ── check_ancestry_pca_update ─────────────────────────────────────────


class TestCheckAncestryPcaUpdate:
    def test_no_url_returns_none_even_if_version_differs(
        self, tmp_path: Path, monkeypatch, reference_engine
    ):
        """ancestry_pca ships out-of-band (manifest ``url=""``): there is nothing
        to download, so no update is surfaced even when the recorded version
        trails the manifest. Surfacing it would dead-end — the trigger endpoint
        cannot apply a no-URL bundle (the bug this guards against)."""
        path = _write_manifest(tmp_path, SAMPLE_MANIFEST)
        monkeypatch.setenv(manifest_mod.MANIFEST_PATH_ENV, str(path))
        _record_version_row(reference_engine, "ancestry_pca", "v0.9")

        assert check_ancestry_pca_update(reference_engine) is None

    def test_manifest_matches_recorded_returns_none(
        self, tmp_path: Path, monkeypatch, reference_engine
    ):
        path = _write_manifest(tmp_path, SAMPLE_MANIFEST)
        monkeypatch.setenv(manifest_mod.MANIFEST_PATH_ENV, str(path))
        _record_version_row(reference_engine, "ancestry_pca", "v1.0")

        assert check_ancestry_pca_update(reference_engine) is None

    def test_no_url_no_recorded_version_returns_none(
        self, tmp_path: Path, monkeypatch, reference_engine
    ):
        """Even a fresh install (no recorded version) is not offered a no-URL
        update — installation flows through the committed-fixture path, not the
        Update Manager."""
        path = _write_manifest(tmp_path, SAMPLE_MANIFEST)
        monkeypatch.setenv(manifest_mod.MANIFEST_PATH_ENV, str(path))

        assert check_ancestry_pca_update(reference_engine) is None

    def test_with_url_surfaces_update_when_version_differs(
        self, tmp_path: Path, monkeypatch, reference_engine
    ):
        """The no-URL guard only suppresses out-of-band bundles: once a hosted
        release URL exists, a version mismatch is surfaced normally."""
        payload = json.loads(json.dumps(SAMPLE_MANIFEST))
        payload["bundles"]["ancestry_pca"]["url"] = "https://example.com/pca.npz"
        path = _write_manifest(tmp_path, payload)
        monkeypatch.setenv(manifest_mod.MANIFEST_PATH_ENV, str(path))
        _record_version_row(reference_engine, "ancestry_pca", "v0.9")

        result = check_ancestry_pca_update(reference_engine)

        assert result is not None
        assert result.db_name == "ancestry_pca"
        assert result.latest_version == "v1.0"
        assert result.download_url == "https://example.com/pca.npz"

    def test_manifest_missing_bundle_entry_returns_none(
        self, tmp_path: Path, monkeypatch, reference_engine
    ):
        payload = json.loads(json.dumps(SAMPLE_MANIFEST))
        del payload["bundles"]["ancestry_pca"]
        path = _write_manifest(tmp_path, payload)
        monkeypatch.setenv(manifest_mod.MANIFEST_PATH_ENV, str(path))

        assert check_ancestry_pca_update(reference_engine) is None

    def test_manifest_unreachable_returns_none(self, reference_engine):
        with patch(
            "backend.db.manifest.httpx.get",
            side_effect=httpx.ConnectError("nope"),
        ):
            assert check_ancestry_pca_update(reference_engine) is None


# ── check_gnomad_bundle_update ────────────────────────────────────────


class TestCheckGnomadBundleUpdate:
    def test_manifest_newer_than_recorded_returns_version_info(
        self, tmp_path: Path, monkeypatch, reference_engine
    ):
        path = _write_manifest(tmp_path, SAMPLE_MANIFEST)
        monkeypatch.setenv(manifest_mod.MANIFEST_PATH_ENV, str(path))
        _record_version_row(reference_engine, "gnomad", "v0.9.0")

        result = check_gnomad_bundle_update(reference_engine)

        assert result is not None
        assert isinstance(result, VersionInfo)
        assert result.db_name == "gnomad"
        assert result.latest_version == "v1.0.0"
        assert result.download_url == "https://example.com/gnomad_af.db"
        assert result.download_size_bytes == 2_000_000_123
        assert result.release_date == "2026-06-06"

    def test_manifest_matches_recorded_returns_none(
        self, tmp_path: Path, monkeypatch, reference_engine
    ):
        path = _write_manifest(tmp_path, SAMPLE_MANIFEST)
        monkeypatch.setenv(manifest_mod.MANIFEST_PATH_ENV, str(path))
        _record_version_row(reference_engine, "gnomad", "v1.0.0")

        assert check_gnomad_bundle_update(reference_engine) is None

    def test_no_recorded_version_returns_version_info(
        self, tmp_path: Path, monkeypatch, reference_engine
    ):
        """Fresh install (no database_versions row) → offer the bundle."""
        path = _write_manifest(tmp_path, SAMPLE_MANIFEST)
        monkeypatch.setenv(manifest_mod.MANIFEST_PATH_ENV, str(path))

        result = check_gnomad_bundle_update(reference_engine)

        assert result is not None
        assert result.latest_version == "v1.0.0"

    def test_manifest_missing_bundle_entry_returns_none(
        self, tmp_path: Path, monkeypatch, reference_engine
    ):
        """Deferred state: bundles['gnomad'] absent → no update offered."""
        payload = json.loads(json.dumps(SAMPLE_MANIFEST))
        del payload["bundles"]["gnomad"]
        path = _write_manifest(tmp_path, payload)
        monkeypatch.setenv(manifest_mod.MANIFEST_PATH_ENV, str(path))

        assert check_gnomad_bundle_update(reference_engine) is None

    def test_no_url_returns_none(self, tmp_path: Path, monkeypatch, reference_engine):
        """An entry with an empty url is treated as nothing-to-download."""
        payload = json.loads(json.dumps(SAMPLE_MANIFEST))
        payload["bundles"]["gnomad"]["url"] = ""
        path = _write_manifest(tmp_path, payload)
        monkeypatch.setenv(manifest_mod.MANIFEST_PATH_ENV, str(path))
        _record_version_row(reference_engine, "gnomad", "v0.9.0")

        assert check_gnomad_bundle_update(reference_engine) is None

    def test_manifest_unreachable_returns_none(self, reference_engine):
        with patch(
            "backend.db.manifest.httpx.get",
            side_effect=httpx.ConnectError("nope"),
        ):
            assert check_gnomad_bundle_update(reference_engine) is None

    def test_settings_argument_accepted_and_ignored(
        self, tmp_path: Path, monkeypatch, reference_engine
    ):
        """Signature parity: settings is accepted but not required."""
        path = _write_manifest(tmp_path, SAMPLE_MANIFEST)
        monkeypatch.setenv(manifest_mod.MANIFEST_PATH_ENV, str(path))
        _record_version_row(reference_engine, "gnomad", "v1.0.0")

        # Both call shapes return None (up to date).
        assert check_gnomad_bundle_update(reference_engine, None) is None
        assert check_gnomad_bundle_update(reference_engine, settings=object()) is None


# ── check_vep_bundle_update ───────────────────────────────────────────


class TestCheckVepBundleUpdate:
    """VEP bundle is checked against the manifest like the other bundles.

    Regression guard for the old GitHub-commit-date heuristic, which compared
    the bundle's frozen ``build_date`` against the latest commit on
    ``bundles/vep_bundle.db`` and so reported an update on every run (the file is
    re-committed over time), even on a fresh install.
    """

    def test_manifest_newer_than_recorded_returns_version_info(
        self, tmp_path: Path, monkeypatch, reference_engine
    ):
        path = _write_manifest(tmp_path, SAMPLE_MANIFEST)
        monkeypatch.setenv(manifest_mod.MANIFEST_PATH_ENV, str(path))
        _record_version_row(reference_engine, "vep_bundle", "v2.0.0")

        result = check_vep_bundle_update(reference_engine)

        assert result is not None
        assert isinstance(result, VersionInfo)
        assert result.db_name == "vep_bundle"
        assert result.latest_version == "v3.0.0"
        assert result.download_url == "https://example.com/vep_bundle.db"
        assert result.download_size_bytes == 358_752_256
        assert result.release_date == "2026-06-01"

    def test_manifest_matches_recorded_returns_none(
        self, tmp_path: Path, monkeypatch, reference_engine
    ):
        """A freshly-installed bundle (recorded == manifest) is up to date."""
        path = _write_manifest(tmp_path, SAMPLE_MANIFEST)
        monkeypatch.setenv(manifest_mod.MANIFEST_PATH_ENV, str(path))
        _record_version_row(reference_engine, "vep_bundle", "v3.0.0")

        assert check_vep_bundle_update(reference_engine) is None

    def test_no_recorded_version_returns_version_info(
        self, tmp_path: Path, monkeypatch, reference_engine
    ):
        """No recorded row → offer the manifest release (still satisfiable: has a url)."""
        path = _write_manifest(tmp_path, SAMPLE_MANIFEST)
        monkeypatch.setenv(manifest_mod.MANIFEST_PATH_ENV, str(path))

        result = check_vep_bundle_update(reference_engine)

        assert result is not None
        assert result.latest_version == "v3.0.0"

    def test_stale_committed_fixture_recorded_returns_version_info(
        self, tmp_path: Path, monkeypatch, reference_engine
    ):
        """The offline-fallback fixture records 'v1.0.0' → real v3.0.0 is offered."""
        path = _write_manifest(tmp_path, SAMPLE_MANIFEST)
        monkeypatch.setenv(manifest_mod.MANIFEST_PATH_ENV, str(path))
        _record_version_row(reference_engine, "vep_bundle", "v1.0.0")

        result = check_vep_bundle_update(reference_engine)

        assert result is not None
        assert result.latest_version == "v3.0.0"

    def test_manifest_missing_bundle_entry_returns_none(
        self, tmp_path: Path, monkeypatch, reference_engine
    ):
        payload = json.loads(json.dumps(SAMPLE_MANIFEST))
        del payload["bundles"]["vep_bundle"]
        path = _write_manifest(tmp_path, payload)
        monkeypatch.setenv(manifest_mod.MANIFEST_PATH_ENV, str(path))

        assert check_vep_bundle_update(reference_engine) is None

    def test_no_url_returns_none(self, tmp_path: Path, monkeypatch, reference_engine):
        """Without a download URL there is nothing to offer (no dead-end update)."""
        payload = json.loads(json.dumps(SAMPLE_MANIFEST))
        payload["bundles"]["vep_bundle"]["url"] = ""
        path = _write_manifest(tmp_path, payload)
        monkeypatch.setenv(manifest_mod.MANIFEST_PATH_ENV, str(path))
        _record_version_row(reference_engine, "vep_bundle", "v1.0.0")

        assert check_vep_bundle_update(reference_engine) is None

    def test_manifest_unreachable_returns_none(self, reference_engine):
        """Manifest fetch failures degrade gracefully — no spurious update offers."""
        with patch(
            "backend.db.manifest.httpx.get",
            side_effect=httpx.ConnectError("nope"),
        ):
            assert check_vep_bundle_update(reference_engine) is None

    def test_settings_argument_accepted_and_ignored(
        self, tmp_path: Path, monkeypatch, reference_engine
    ):
        """Signature parity: settings is accepted but not required."""
        path = _write_manifest(tmp_path, SAMPLE_MANIFEST)
        monkeypatch.setenv(manifest_mod.MANIFEST_PATH_ENV, str(path))
        _record_version_row(reference_engine, "vep_bundle", "v3.0.0")

        assert check_vep_bundle_update(reference_engine, None) is None
        assert check_vep_bundle_update(reference_engine, settings=object()) is None


# ── CHECK_FNS dispatch dict ───────────────────────────────────────────


class TestCheckFnsDispatch:
    def test_contains_currently_implemented_keys(self):
        # Step 18 wires the bundle + clinvar + vep_bundle entries.
        # Pipeline-DB entries are added in Steps 19–24. gnomad joined the bundle
        # set when it flipped to build_mode="bundled".
        assert set(CHECK_FNS) >= {
            "clinvar",
            "vep_bundle",
            "lai_bundle",
            "ancestry_pca",
            "gnomad",
        }

    def test_each_value_is_callable(self):
        for db_name, fn in CHECK_FNS.items():
            assert callable(fn), f"CHECK_FNS[{db_name!r}] is not callable"

    def test_bindings_point_at_expected_functions(self):
        assert CHECK_FNS["clinvar"] is check_clinvar_update
        assert CHECK_FNS["vep_bundle"] is check_vep_bundle_update
        assert CHECK_FNS["lai_bundle"] is check_lai_bundle_update
        assert CHECK_FNS["ancestry_pca"] is check_ancestry_pca_update
        assert CHECK_FNS["gnomad"] is check_gnomad_bundle_update

    def test_bundle_dispatch_via_check_fns(self, tmp_path: Path, monkeypatch, reference_engine):
        """Round-trip the dispatch path used by Step 25's scheduler refactor."""
        path = _write_manifest(tmp_path, SAMPLE_MANIFEST)
        monkeypatch.setenv(manifest_mod.MANIFEST_PATH_ENV, str(path))
        _record_version_row(reference_engine, "lai_bundle", "v1.0")

        result = CHECK_FNS["lai_bundle"](reference_engine, None)

        assert result is not None
        assert result.db_name == "lai_bundle"
        assert result.latest_version == "v1.1"

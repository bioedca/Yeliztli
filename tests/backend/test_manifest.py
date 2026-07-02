"""Tests for backend.db.manifest — bundle manifest loader + cache."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest
import structlog
from structlog.testing import capture_logs

from backend.db import manifest as manifest_mod
from backend.db.manifest import (
    BundleManifestEntry,
    Manifest,
    ManifestFetchError,
    PipelinePinEntry,
    fetch_manifest,
    get_bundle_info,
    get_pipeline_pin,
    reset_cache,
)

# Historical v2 fixture values used by V2_PAYLOAD below.
V2_VEP_BUNDLE_SHA256 = "9f645b2c6963e2a83e69c0b1e5bea777cb1bf20566d7c051cfda9b0fef6393bc"
V2_VEP_BUNDLE_SIZE_BYTES = 358_752_256

# The real VEP bundle v4.0.0 SHA-256 and exact size of the published
# vep_bundle.db (see docs/bundle-release-runbook.md §3). The v4 asset was
# rebuilt with VEP --canonical so GRCh37 CANONICAL rows populate mane_select.
# Naming them keeps the test assertions exact and pins bundles/manifest.json
# against accidental drift.
VEP_BUNDLE_SHA256 = "c8e5f162e1872ecd0ff94408c66ce0874b79dea5f4f645c758d2c45c3b6cc4d3"
VEP_BUNDLE_SIZE_BYTES = 352_251_904

# The real LAI bundle v2.0.0 SHA-256 and exact size of the published
# genomeinsight_lai_bundle_v2.0.0.tar.gz. Filled in by PR-0c (Phase D, Step 32)
# once the cluster rebuild produced the tarball and the draft release was
# confirmed reachable; before that the manifest carried the v1.1 asset values.
# Pins bundles/manifest.json against accidental rollback/drift and must
# byte-match DATABASES["lai_bundle"].sha256 (Plan §9 Done criterion #4).
LAI_BUNDLE_SHA256 = "36abb5f2ed95011aff1227c894f52597ef5c31adb5a132fafdf0830eabf14bff"
LAI_BUNDLE_SIZE_BYTES = 1_723_731_810

# The real gnomAD bundle v1.0.0 SHA-256 and exact size of the published
# gnomad_af.db. Filled in by the asset-publish follow-up to PR #312 once the
# gnomAD r2.1.1 exomes (GRCh37) bundle was built (16,761,252 VCF records parsed,
# 14,774,008 unique rsIDs) and the draft release was confirmed reachable. Before
# that, gnomAD stayed in pipeline_pins (deferred state). Pins bundles/manifest.json
# against accidental rollback/drift and must byte-match the size carried by
# DATABASES["gnomad"].expected_size_bytes.
GNOMAD_BUNDLE_SHA256 = "8518eb9ff105646b088e761c405e993f26127bcf05fd93d47c5eeef40579ad79"
GNOMAD_BUNDLE_SIZE_BYTES = 1_952_698_368

SAMPLE_PAYLOAD: dict = {
    "schema_version": 1,
    "generated_at": "2026-05-08T00:00:00Z",
    "bundles": {
        "lai_bundle": {
            "version": "v1.1.0",
            "build_date": "2026-04-07",
            "url": "https://example.com/lai.tar.gz",
            "sha256": "959ed0fd9ebe2ad8fa542776a59ce73072d928c7ce59839ea81d0f1e78a5c18e",
            "size_bytes": 523801111,
        },
        "vep_bundle": {
            "version": "v1.0.0",
            "build_date": "2026-04-10",
            "url": "https://example.com/vep.db",
            "sha256": "1786b5bc1a6f5a0440239f40d5f5ac69d15ce213015a9cbf11affa05bbedfff0",
            "size_bytes": 11374592,
        },
    },
    "pipeline_pins": {
        "clinvar": {
            "url": "https://ftp.ncbi.nlm.nih.gov/pub/clinvar/vcf_GRCh37/clinvar.vcf.gz",
            "last_known_version": "",
        },
        "dbnsfp": {
            "url": "https://dist.genos.us/academic/e55b09/dbNSFP5.3.1a.zip",
            "last_known_version": "5.3.1a",
        },
    },
}

V2_PAYLOAD: dict = {
    "schema_version": 1,
    "generated_at": "2026-05-18T00:00:00Z",
    "bundles": {
        "lai_bundle": {
            "version": "v1.1.0",
            "build_date": "2026-04-07",
            "url": "https://example.com/lai.tar.gz",
            "sha256": "959ed0fd9ebe2ad8fa542776a59ce73072d928c7ce59839ea81d0f1e78a5c18e",
            "size_bytes": 523801111,
        },
        "vep_bundle": {
            "version": "v2.0.0",
            "build_date": "2026-06-01",
            "url": "https://github.com/bioedca/Yeliztli/releases/download/bundle-v2.0.0/vep_bundle.db",
            "sha256": V2_VEP_BUNDLE_SHA256,
            "size_bytes": V2_VEP_BUNDLE_SIZE_BYTES,
            "min_app_version": "0.2.0",
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
def _clear_cache_and_env(monkeypatch):
    """Each test starts with an empty cache and no env override."""
    monkeypatch.delenv(manifest_mod.MANIFEST_PATH_ENV, raising=False)
    reset_cache()
    yield
    reset_cache()


def _write_manifest(path: Path, payload: dict) -> Path:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _make_response(json_data: dict, status: int = 200) -> MagicMock:
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status
    resp.json.return_value = json_data
    resp.raise_for_status = MagicMock()
    return resp


# ── dataclass surface ─────────────────────────────────────────────


class TestDataclasses:
    def test_bundle_entry_is_frozen(self):
        entry = BundleManifestEntry(
            version="v1", build_date="2026-01-01", url="u", sha256="abc", size_bytes=10
        )
        with pytest.raises((AttributeError, TypeError)):
            entry.version = "v2"  # type: ignore[misc]

    def test_pipeline_pin_is_frozen(self):
        pin = PipelinePinEntry(url="u", last_known_version="1.0")
        with pytest.raises((AttributeError, TypeError)):
            pin.url = "x"  # type: ignore[misc]

    def test_manifest_is_frozen(self):
        m = Manifest(schema_version=1, generated_at="now", bundles={}, pipeline_pins={})
        with pytest.raises((AttributeError, TypeError)):
            m.schema_version = 2  # type: ignore[misc]


# ── env-var local override ───────────────────────────────────────


class TestLocalOverride:
    def test_loads_from_env_path(self, tmp_path: Path, monkeypatch):
        path = _write_manifest(tmp_path / "manifest.json", SAMPLE_PAYLOAD)
        monkeypatch.setenv(manifest_mod.MANIFEST_PATH_ENV, str(path))

        m = fetch_manifest()
        assert m.schema_version == 1
        assert m.generated_at == "2026-05-08T00:00:00Z"
        assert "lai_bundle" in m.bundles
        assert m.bundles["lai_bundle"].version == "v1.1.0"
        assert m.bundles["lai_bundle"].size_bytes == 523_801_111
        assert m.pipeline_pins["dbnsfp"].last_known_version == "5.3.1a"

    def test_local_override_skips_network(self, tmp_path: Path, monkeypatch):
        path = _write_manifest(tmp_path / "manifest.json", SAMPLE_PAYLOAD)
        monkeypatch.setenv(manifest_mod.MANIFEST_PATH_ENV, str(path))

        with patch("backend.db.manifest.httpx.get") as http_get:
            fetch_manifest()
            http_get.assert_not_called()

    def test_local_override_not_cached(self, tmp_path: Path, monkeypatch):
        """Env override re-reads each call so tests can swap files."""
        path = tmp_path / "manifest.json"
        _write_manifest(path, SAMPLE_PAYLOAD)
        monkeypatch.setenv(manifest_mod.MANIFEST_PATH_ENV, str(path))

        first = fetch_manifest()
        assert first.bundles["lai_bundle"].version == "v1.1.0"

        updated = json.loads(json.dumps(SAMPLE_PAYLOAD))
        updated["bundles"]["lai_bundle"]["version"] = "v1.2.0"
        _write_manifest(path, updated)

        second = fetch_manifest()
        assert second.bundles["lai_bundle"].version == "v1.2.0"

    def test_missing_local_file_raises(self, tmp_path: Path, monkeypatch):
        monkeypatch.setenv(manifest_mod.MANIFEST_PATH_ENV, str(tmp_path / "nope.json"))
        with pytest.raises(ManifestFetchError):
            fetch_manifest()

    def test_malformed_local_file_raises(self, tmp_path: Path, monkeypatch):
        bad = tmp_path / "manifest.json"
        bad.write_text("{not json", encoding="utf-8")
        monkeypatch.setenv(manifest_mod.MANIFEST_PATH_ENV, str(bad))
        with pytest.raises(ManifestFetchError):
            fetch_manifest()


# ── remote fetch + caching ───────────────────────────────────────


class TestRemoteFetch:
    def test_fetch_returns_parsed_manifest(self):
        resp = _make_response(SAMPLE_PAYLOAD)
        with patch("backend.db.manifest.httpx.get", return_value=resp) as http_get:
            m = fetch_manifest()

        http_get.assert_called_once()
        called_url = http_get.call_args.args[0]
        assert called_url == manifest_mod.MANIFEST_URL
        assert m.bundles["vep_bundle"].sha256.startswith("1786b5bc")

    def test_cache_ttl_skips_second_network_call(self):
        resp = _make_response(SAMPLE_PAYLOAD)
        with patch("backend.db.manifest.httpx.get", return_value=resp) as http_get:
            fetch_manifest()
            fetch_manifest()
        assert http_get.call_count == 1

    def test_cache_expires_after_ttl(self):
        resp = _make_response(SAMPLE_PAYLOAD)
        with (
            patch("backend.db.manifest.httpx.get", return_value=resp) as http_get,
            patch("backend.db.manifest.time.monotonic") as mono,
        ):
            mono.return_value = 1000.0
            fetch_manifest()
            mono.return_value = 1000.0 + manifest_mod.CACHE_TTL_SECONDS + 1
            fetch_manifest()
        assert http_get.call_count == 2

    def test_force_refresh_bypasses_cache(self):
        resp = _make_response(SAMPLE_PAYLOAD)
        with patch("backend.db.manifest.httpx.get", return_value=resp) as http_get:
            fetch_manifest()
            fetch_manifest(force_refresh=True)
        assert http_get.call_count == 2

    def test_network_error_raises_typed(self):
        with patch(
            "backend.db.manifest.httpx.get",
            side_effect=httpx.ConnectError("refused"),
        ):
            with pytest.raises(ManifestFetchError):
                fetch_manifest()

    def test_timeout_raises_typed(self):
        with patch(
            "backend.db.manifest.httpx.get",
            side_effect=httpx.TimeoutException("slow"),
        ):
            with pytest.raises(ManifestFetchError):
                fetch_manifest()

    def test_http_error_status_raises_typed(self):
        resp = MagicMock(spec=httpx.Response)
        resp.status_code = 500
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "server error", request=MagicMock(), response=resp
        )
        with patch("backend.db.manifest.httpx.get", return_value=resp):
            with pytest.raises(ManifestFetchError):
                fetch_manifest()

    def test_invalid_json_raises_typed(self):
        resp = MagicMock(spec=httpx.Response)
        resp.status_code = 200
        resp.raise_for_status = MagicMock()
        resp.json.side_effect = ValueError("not json")
        with patch("backend.db.manifest.httpx.get", return_value=resp):
            with pytest.raises(ManifestFetchError):
                fetch_manifest()

    def test_malformed_payload_raises_typed(self):
        bad = {"schema_version": 1, "bundles": {"x": {"version": "v1"}}}
        resp = _make_response(bad)
        with patch("backend.db.manifest.httpx.get", return_value=resp):
            with pytest.raises(ManifestFetchError):
                fetch_manifest()

    def test_null_required_field_raises_typed(self):
        """Null sha256 (or other required strings) must fail loudly, not str-coerce to 'None'."""
        bad = json.loads(json.dumps(SAMPLE_PAYLOAD))
        bad["bundles"]["lai_bundle"]["sha256"] = None
        resp = _make_response(bad)
        with patch("backend.db.manifest.httpx.get", return_value=resp):
            with pytest.raises(ManifestFetchError, match="sha256"):
                fetch_manifest()

    def test_invalid_sha256_format_raises_typed(self):
        """sha256 must be 64 hex chars."""
        bad = json.loads(json.dumps(SAMPLE_PAYLOAD))
        bad["bundles"]["lai_bundle"]["sha256"] = "not-a-real-hash"
        resp = _make_response(bad)
        with patch("backend.db.manifest.httpx.get", return_value=resp):
            with pytest.raises(ManifestFetchError, match="sha256"):
                fetch_manifest()

    def test_zero_size_bundle_raises_typed(self):
        """size_bytes must be > 0."""
        bad = json.loads(json.dumps(SAMPLE_PAYLOAD))
        bad["bundles"]["lai_bundle"]["size_bytes"] = 0
        resp = _make_response(bad)
        with patch("backend.db.manifest.httpx.get", return_value=resp):
            with pytest.raises(ManifestFetchError, match="size_bytes"):
                fetch_manifest()

    def test_missing_top_level_sections_raises_typed(self):
        """Missing bundles or pipeline_pins must fail loudly, not silently become empty."""
        for missing in ("bundles", "pipeline_pins"):
            bad = json.loads(json.dumps(SAMPLE_PAYLOAD))
            del bad[missing]
            resp = _make_response(bad)
            with patch("backend.db.manifest.httpx.get", return_value=resp):
                with pytest.raises(ManifestFetchError, match="required"):
                    fetch_manifest()
            reset_cache()

    def test_non_object_payload_raises_typed(self):
        resp = _make_response([])  # type: ignore[arg-type]
        with patch("backend.db.manifest.httpx.get", return_value=resp):
            with pytest.raises(ManifestFetchError):
                fetch_manifest()

    def test_failure_does_not_pollute_cache(self):
        resp_bad = MagicMock(spec=httpx.Response)
        resp_bad.status_code = 200
        resp_bad.raise_for_status = MagicMock()
        resp_bad.json.side_effect = ValueError("bad")
        resp_good = _make_response(SAMPLE_PAYLOAD)

        with patch(
            "backend.db.manifest.httpx.get",
            side_effect=[resp_bad, resp_good],
        ) as http_get:
            with pytest.raises(ManifestFetchError):
                fetch_manifest()
            m = fetch_manifest()

        assert http_get.call_count == 2
        assert m.bundles["lai_bundle"].version == "v1.1.0"


# ── accessor helpers ─────────────────────────────────────────────


class TestAccessors:
    def test_get_bundle_info_returns_entry(self, tmp_path: Path, monkeypatch):
        path = _write_manifest(tmp_path / "manifest.json", SAMPLE_PAYLOAD)
        monkeypatch.setenv(manifest_mod.MANIFEST_PATH_ENV, str(path))

        entry = get_bundle_info("lai_bundle")
        assert entry is not None
        assert entry.version == "v1.1.0"
        assert entry.size_bytes == 523_801_111

    def test_get_bundle_info_unknown_returns_none(self, tmp_path: Path, monkeypatch):
        path = _write_manifest(tmp_path / "manifest.json", SAMPLE_PAYLOAD)
        monkeypatch.setenv(manifest_mod.MANIFEST_PATH_ENV, str(path))

        assert get_bundle_info("not_a_bundle") is None

    def test_get_bundle_info_swallows_fetch_error(self):
        with patch(
            "backend.db.manifest.httpx.get",
            side_effect=httpx.ConnectError("nope"),
        ):
            assert get_bundle_info("lai_bundle") is None

    def test_get_pipeline_pin_returns_entry(self, tmp_path: Path, monkeypatch):
        path = _write_manifest(tmp_path / "manifest.json", SAMPLE_PAYLOAD)
        monkeypatch.setenv(manifest_mod.MANIFEST_PATH_ENV, str(path))

        pin = get_pipeline_pin("dbnsfp")
        assert pin is not None
        assert pin.last_known_version == "5.3.1a"
        assert pin.url.endswith("dbNSFP5.3.1a.zip")

    def test_get_pipeline_pin_unknown_returns_none(self, tmp_path: Path, monkeypatch):
        path = _write_manifest(tmp_path / "manifest.json", SAMPLE_PAYLOAD)
        monkeypatch.setenv(manifest_mod.MANIFEST_PATH_ENV, str(path))

        assert get_pipeline_pin("nope") is None

    def test_get_pipeline_pin_swallows_fetch_error(self):
        with patch(
            "backend.db.manifest.httpx.get",
            side_effect=httpx.ConnectError("nope"),
        ):
            assert get_pipeline_pin("clinvar") is None


# ── v2.0.0 bundle fixture ────────────────────────────────────────


class TestBundleV2:
    """The v2.0.0 vep_bundle fixture loads with all current fields and the
    additive ``min_app_version`` JSON key round-trips through the parser
    (step 5)."""

    def test_v2_manifest_loads_with_all_fields(self, tmp_path: Path, monkeypatch):
        path = _write_manifest(tmp_path / "manifest.json", V2_PAYLOAD)
        monkeypatch.setenv(manifest_mod.MANIFEST_PATH_ENV, str(path))

        m = fetch_manifest()
        entry = m.bundles["vep_bundle"]
        assert entry.version == "v2.0.0"
        assert entry.build_date == "2026-06-01"
        assert entry.url.endswith("/bundle-v2.0.0/vep_bundle.db")
        assert entry.sha256 == V2_VEP_BUNDLE_SHA256
        assert entry.size_bytes == V2_VEP_BUNDLE_SIZE_BYTES

    def test_v2_min_app_version_round_trips(self, tmp_path: Path, monkeypatch):
        """`min_app_version` is parsed from the JSON entry and exposed on the dataclass.

        Supersedes the placeholder-only check from Step 4 that just asserted
        the additive key existed in V2_PAYLOAD — this one proves the parser
        actually reads it onto BundleManifestEntry.
        """
        path = _write_manifest(tmp_path / "manifest.json", V2_PAYLOAD)
        monkeypatch.setenv(manifest_mod.MANIFEST_PATH_ENV, str(path))
        monkeypatch.setattr(manifest_mod, "_current_app_version", lambda: "0.2.0")

        m = fetch_manifest()
        assert m.bundles["vep_bundle"].min_app_version == "0.2.0"
        # The lai_bundle entry in the v2 fixture has no min_app_version → field is None.
        assert m.bundles["lai_bundle"].min_app_version is None

    def test_legacy_v1_version_normalized_to_v1_0_0(self):
        """Prior ``v1.0`` was normalized to ``v1.0.0`` for clean semver compare."""
        assert SAMPLE_PAYLOAD["bundles"]["vep_bundle"]["version"] == "v1.0.0"


# ── min_app_version advisory warning (step 5) ──────────────────────


class TestMinAppVersionField:
    def test_dataclass_default_is_none(self):
        """Backward-compat: existing callers that construct entries without the
        new field still work; the additive default is ``None``."""
        entry = BundleManifestEntry(
            version="v1", build_date="2026-01-01", url="u", sha256="abc", size_bytes=10
        )
        assert entry.min_app_version is None

    def test_dataclass_accepts_explicit_value(self):
        entry = BundleManifestEntry(
            version="v2.0.0",
            build_date="2026-05-18",
            url="u",
            sha256="abc",
            size_bytes=10,
            min_app_version="0.2.0",
        )
        assert entry.min_app_version == "0.2.0"

    def test_parser_treats_empty_string_as_none(self, tmp_path: Path, monkeypatch):
        """Empty / null values collapse to ``None`` so the advisory check skips them."""
        payload = json.loads(json.dumps(V2_PAYLOAD))
        payload["bundles"]["vep_bundle"]["min_app_version"] = ""
        path = _write_manifest(tmp_path / "manifest.json", payload)
        monkeypatch.setenv(manifest_mod.MANIFEST_PATH_ENV, str(path))
        monkeypatch.setattr(manifest_mod, "_current_app_version", lambda: "0.1.0")

        m = fetch_manifest()
        assert m.bundles["vep_bundle"].min_app_version is None


class TestMinAppVersionAdvisoryWarning:
    """Plan §2.2 / §5.5: when the running app version is below a bundle's
    advisory ``min_app_version``, ``manifest.py`` logs a structured warning
    and continues. It never refuses to load."""

    EVENT_NAME = "manifest_min_app_version_below_threshold"

    def _events_named(self, cap_logs, name):
        return [e for e in cap_logs if e.get("event") == name]

    def test_warning_emits_when_below_threshold(self, tmp_path: Path, monkeypatch):
        path = _write_manifest(tmp_path / "manifest.json", V2_PAYLOAD)
        monkeypatch.setenv(manifest_mod.MANIFEST_PATH_ENV, str(path))
        monkeypatch.setattr(manifest_mod, "_current_app_version", lambda: "0.1.0")

        with capture_logs() as cap_logs:
            m = fetch_manifest()

        # Manifest still loads — never refuses.
        assert m.bundles["vep_bundle"].version == "v2.0.0"
        events = self._events_named(cap_logs, self.EVENT_NAME)
        assert len(events) == 1
        warn = events[0]
        assert warn["log_level"] == "warning"
        assert warn["bundle"] == "vep_bundle"
        assert warn["installed_app_version"] == "0.1.0"
        assert warn["required_app_version"] == "0.2.0"

    def test_no_warning_when_app_at_threshold(self, tmp_path: Path, monkeypatch):
        path = _write_manifest(tmp_path / "manifest.json", V2_PAYLOAD)
        monkeypatch.setenv(manifest_mod.MANIFEST_PATH_ENV, str(path))
        monkeypatch.setattr(manifest_mod, "_current_app_version", lambda: "0.2.0")

        with capture_logs() as cap_logs:
            fetch_manifest()

        assert self._events_named(cap_logs, self.EVENT_NAME) == []

    def test_no_warning_when_app_above_threshold(self, tmp_path: Path, monkeypatch):
        path = _write_manifest(tmp_path / "manifest.json", V2_PAYLOAD)
        monkeypatch.setenv(manifest_mod.MANIFEST_PATH_ENV, str(path))
        monkeypatch.setattr(manifest_mod, "_current_app_version", lambda: "0.3.0")

        with capture_logs() as cap_logs:
            fetch_manifest()

        assert self._events_named(cap_logs, self.EVENT_NAME) == []

    def test_no_warning_when_field_absent(self, tmp_path: Path, monkeypatch):
        """SAMPLE_PAYLOAD has no ``min_app_version`` on any bundle — silent."""
        path = _write_manifest(tmp_path / "manifest.json", SAMPLE_PAYLOAD)
        monkeypatch.setenv(manifest_mod.MANIFEST_PATH_ENV, str(path))
        monkeypatch.setattr(manifest_mod, "_current_app_version", lambda: "0.1.0")

        with capture_logs() as cap_logs:
            fetch_manifest()

        assert self._events_named(cap_logs, self.EVENT_NAME) == []

    def test_malformed_min_app_version_does_not_raise(self, tmp_path: Path, monkeypatch):
        """An unparseable ``min_app_version`` must never raise or block loading."""
        payload = json.loads(json.dumps(V2_PAYLOAD))
        payload["bundles"]["vep_bundle"]["min_app_version"] = "not-a-version"
        path = _write_manifest(tmp_path / "manifest.json", payload)
        monkeypatch.setenv(manifest_mod.MANIFEST_PATH_ENV, str(path))
        monkeypatch.setattr(manifest_mod, "_current_app_version", lambda: "0.1.0")

        with capture_logs() as cap_logs:
            m = fetch_manifest()

        assert m.bundles["vep_bundle"].min_app_version == "not-a-version"
        # No advisory warning emitted (compare skipped on InvalidVersion).
        assert self._events_named(cap_logs, self.EVENT_NAME) == []

    def test_malformed_app_version_does_not_raise(self, tmp_path: Path, monkeypatch):
        """An unparseable running-app version must never raise or block loading."""
        path = _write_manifest(tmp_path / "manifest.json", V2_PAYLOAD)
        monkeypatch.setenv(manifest_mod.MANIFEST_PATH_ENV, str(path))
        monkeypatch.setattr(manifest_mod, "_current_app_version", lambda: "garbage")

        with capture_logs() as cap_logs:
            m = fetch_manifest()

        assert m.bundles["vep_bundle"].version == "v2.0.0"
        assert self._events_named(cap_logs, self.EVENT_NAME) == []

    def test_leading_v_tolerated_on_both_sides(self, tmp_path: Path, monkeypatch):
        """Both the manifest's ``v0.2.0`` form and a ``v0.1.0`` running app
        version are accepted; semver compare strips a single leading ``v``."""
        payload = json.loads(json.dumps(V2_PAYLOAD))
        payload["bundles"]["vep_bundle"]["min_app_version"] = "v0.2.0"
        path = _write_manifest(tmp_path / "manifest.json", payload)
        monkeypatch.setenv(manifest_mod.MANIFEST_PATH_ENV, str(path))
        monkeypatch.setattr(manifest_mod, "_current_app_version", lambda: "v0.1.0")

        with capture_logs() as cap_logs:
            fetch_manifest()

        events = self._events_named(cap_logs, self.EVENT_NAME)
        assert len(events) == 1
        assert events[0]["installed_app_version"] == "0.1.0"
        assert events[0]["required_app_version"] == "0.2.0"

    def test_current_app_version_resolves_backend_main(self, monkeypatch):
        """Default resolver returns ``backend.main.VERSION`` (lazy-imported)."""
        from backend.main import VERSION

        assert manifest_mod._current_app_version() == VERSION
        # And the structlog logger module is wired up.
        assert isinstance(manifest_mod._structlog, structlog.stdlib.BoundLogger) or hasattr(
            manifest_mod._structlog, "warning"
        )


# ── repo manifest sanity (defensive — catches drift in bundles/manifest.json) ──


class TestRepoManifest:
    def test_repo_manifest_loads_and_has_required_bundles(self, monkeypatch):
        repo_root = Path(__file__).resolve().parents[2]
        path = repo_root / "bundles" / "manifest.json"
        if not path.is_file():
            pytest.skip("bundles/manifest.json not present in this checkout")
        monkeypatch.setenv(manifest_mod.MANIFEST_PATH_ENV, str(path))

        m = fetch_manifest()
        assert m.schema_version == 1
        # gnomAD moved from pipeline_pins to bundles in the asset-publish follow-up
        # to PR #312 (build_mode="bundled"); it now ships as a prebuilt download.
        for required in ("lai_bundle", "vep_bundle", "ancestry_pca", "gnomad"):
            assert required in m.bundles, f"missing bundle entry: {required}"
        required_pins = (
            "clinvar",
            "dbnsfp",
            "cpic",
            "gwas_catalog",
            "dbsnp",
            "mondo_hpo",
        )
        for required in required_pins:
            assert required in m.pipeline_pins, f"missing pipeline pin: {required}"
        # gnomAD is no longer a pipeline pin now that it ships as a bundle.
        assert "gnomad" not in m.pipeline_pins

    def test_repo_manifest_vep_bundle_is_v4_0_0(self, monkeypatch):
        """Pins the manifest's vep_bundle entry against accidental rollback/drift.

        v4.0.0 is a real rebuilt asset, not a manifest-only version lead: it was
        generated with VEP ``--canonical`` so canonical rows populate
        ``mane_select`` and v3.0.0 samples re-annotate against the corrected
        preferred-transcript tiebreak.
        """
        repo_root = Path(__file__).resolve().parents[2]
        path = repo_root / "bundles" / "manifest.json"
        if not path.is_file():
            pytest.skip("bundles/manifest.json not present in this checkout")
        monkeypatch.setenv(manifest_mod.MANIFEST_PATH_ENV, str(path))

        m = fetch_manifest()
        vep = m.bundles["vep_bundle"]
        assert vep.version == "v4.0.0"
        assert vep.build_date == "2026-07-02"
        assert vep.url.endswith("/bundle-v4.0.0/vep_bundle.db")
        assert vep.size_bytes == VEP_BUNDLE_SIZE_BYTES
        assert vep.sha256 == VEP_BUNDLE_SHA256
        raw = json.loads(path.read_text(encoding="utf-8"))
        assert raw["bundles"]["vep_bundle"]["min_app_version"] == "0.2.0"

    def test_repo_manifest_lai_bundle_is_v2_0_0(self, monkeypatch):
        """Pins Step 32's PR-0c manifest update against accidental rollback."""
        repo_root = Path(__file__).resolve().parents[2]
        path = repo_root / "bundles" / "manifest.json"
        if not path.is_file():
            pytest.skip("bundles/manifest.json not present in this checkout")
        monkeypatch.setenv(manifest_mod.MANIFEST_PATH_ENV, str(path))

        m = fetch_manifest()
        lai = m.bundles["lai_bundle"]
        assert lai.version == "v2.0.0"
        assert lai.build_date == "2026-06-05"
        assert lai.url.endswith("/lai-bundle-v2.0.0/genomeinsight_lai_bundle_v2.0.0.tar.gz")
        # Exact-equality vs the real published asset values (PR-0c, Phase D);
        # pins bundles/manifest.json against accidental rollback or drift.
        assert lai.size_bytes == LAI_BUNDLE_SIZE_BYTES
        assert lai.sha256 == LAI_BUNDLE_SHA256
        # Both v2.0.0 bundles gate on app v0.2.0 (AncestryDNA support); the
        # Phase E1 smoke (Plan §E1) asserts lai.min_app_version == "0.2.0".
        assert lai.min_app_version == "0.2.0"

    def test_repo_manifest_gnomad_is_v1_0_0(self, monkeypatch):
        """Pins the gnomAD bundle asset-publish manifest entry against rollback.

        Asset-publish follow-up to PR #312: bundles["gnomad"] was added with the
        real gnomad-bundle-v1.0.0 asset values and pipeline_pins["gnomad"] removed.
        Mirrors test_repo_manifest_vep_bundle_is_v4_0_0.
        """
        repo_root = Path(__file__).resolve().parents[2]
        path = repo_root / "bundles" / "manifest.json"
        if not path.is_file():
            pytest.skip("bundles/manifest.json not present in this checkout")
        monkeypatch.setenv(manifest_mod.MANIFEST_PATH_ENV, str(path))

        m = fetch_manifest()
        gnomad = m.bundles["gnomad"]
        assert gnomad.version == "v1.0.0"
        assert gnomad.build_date == "2026-06-06"
        assert gnomad.url.endswith("/gnomad-bundle-v1.0.0/gnomad_af.db")
        # Exact-equality vs the real published asset values; pins
        # bundles/manifest.json against accidental rollback or drift.
        assert gnomad.size_bytes == GNOMAD_BUNDLE_SIZE_BYTES
        assert gnomad.sha256 == GNOMAD_BUNDLE_SHA256
        assert gnomad.min_app_version == "0.2.0"
        # gnomAD is no longer a pipeline pin (it ships as a bundle now).
        assert "gnomad" not in m.pipeline_pins


# ── committed v2 manifest fixture (Step 18 / Plan §16.1) ──────────────


class TestManifestV2Fixture:
    """Phase 0 closure: `tests/fixtures/manifest_v2.json` loads cleanly.

    The committed fixture is the shared test artifact for Phase 0 — keeps
    other test modules from re-declaring V2_PAYLOAD inline and gives the
    AncestryDNA fixture chain a single source of truth.
    """

    FIXTURE_PATH = Path(__file__).resolve().parents[1] / "fixtures" / "manifest_v2.json"

    def test_fixture_exists(self):
        assert self.FIXTURE_PATH.is_file()

    def test_fixture_loads_with_v2_0_0_fields(self, monkeypatch):
        monkeypatch.setenv(manifest_mod.MANIFEST_PATH_ENV, str(self.FIXTURE_PATH))
        m = fetch_manifest()

        vep = m.bundles["vep_bundle"]
        assert vep.version == "v2.0.0"
        assert vep.url.endswith("/bundle-v2.0.0/vep_bundle.db")
        assert vep.size_bytes == 600_000_000
        assert vep.min_app_version == "0.2.0"

        # LAI v1.1.0 normalized (Plan §12.2 LAI-00a).
        lai = m.bundles["lai_bundle"]
        assert lai.version == "v1.1.0"
        assert lai.min_app_version == "0.2.0"

    def test_fixture_passes_min_app_version_check_at_threshold(self, monkeypatch):
        """An app at 0.2.0 sees no advisory warning against the v2 fixture."""
        from structlog.testing import capture_logs

        monkeypatch.setenv(manifest_mod.MANIFEST_PATH_ENV, str(self.FIXTURE_PATH))
        monkeypatch.setattr(manifest_mod, "_current_app_version", lambda: "0.2.0")

        with capture_logs() as cap_logs:
            fetch_manifest()

        events = [
            e for e in cap_logs if e.get("event") == "manifest_min_app_version_below_threshold"
        ]
        assert events == []

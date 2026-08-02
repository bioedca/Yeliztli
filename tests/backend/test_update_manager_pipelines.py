"""Tests for manifest-driven pipeline-DB update checks (Steps 19–24).

Each ``check_<db>_update`` function reads its URL + ``last_known_version``
from ``bundles/manifest.json`` via the ``YELIZTLI_MANIFEST_PATH``
override, then performs an HTTP HEAD on the pinned URL to confirm
reachability and pull a download-size estimate. The result is compared
against ``database_versions`` — newer manifest pin → :class:`VersionInfo`,
same/newer recorded version → ``None``, network error → ``None``.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import httpx
import pytest

from backend.annotation.cpic import check_cpic_update
from backend.annotation.dbnsfp import check_dbnsfp_update
from backend.annotation.dbsnp import check_dbsnp_update
from backend.annotation.gwas import check_gwas_update
from backend.annotation.mondo_hpo import (
    HPO_GENES_TO_PHENOTYPE_URL,
    MONDO_HPO_INGESTION_REVISION,
    MONDO_SSSOM_URL,
    check_mondo_hpo_update,
)
from backend.db import manifest as manifest_mod
from backend.db.manifest import reset_cache
from backend.db.tables import database_versions, gene_phenotype
from backend.db.update_manager import (
    CHECK_FNS,
    VersionInfo,
)

GNOMAD_URL = (
    "https://storage.googleapis.com/gcp-public-data--gnomad/"
    "release/2.1.1/vcf/exomes/gnomad.exomes.r2.1.1.sites.vcf.bgz"
)

DBNSFP_URL = "https://dist.genos.us/academic/e55b09/dbNSFP5.3.1a.zip"

CPIC_RELEASE_API_URL = "https://api.github.com/repos/cpicpgx/cpic-data/releases/latest"
CPIC_ASSET_URL = (
    "https://github.com/cpicpgx/cpic-data/releases/download/v1.30.0/cpic-data-v1.30.0.zip"
)

GWAS_URL = (
    "https://ftp.ebi.ac.uk/pub/databases/gwas/releases/latest/"
    "gwas-catalog-associations_ontology-annotated-full.zip"
)

DBSNP_URL = (
    "https://ftp.ncbi.nlm.nih.gov/snp/organisms/"
    "human_9606_b151_GRCh38p7/database/organism_data/RsMergeArch.bcp.gz"
)

MONDO_HPO_URL = (
    "https://data.monarchinitiative.org/monarch-kg/latest/tsv/"
    "gene_associations/gene_disease.9606.tsv.gz"
)

SAMPLE_MANIFEST: dict = {
    "schema_version": 1,
    "generated_at": "2026-05-08T00:00:00Z",
    "bundles": {},
    "pipeline_pins": {
        "gnomad": {
            "url": GNOMAD_URL,
            "last_known_version": "r2.1.1",
        },
        "dbnsfp": {
            "url": DBNSFP_URL,
            "last_known_version": "5.3.1a",
        },
        "cpic": {
            "url": CPIC_RELEASE_API_URL,
            "last_known_version": "bundled",
        },
        "gwas_catalog": {
            "url": GWAS_URL,
            "last_known_version": "",
        },
        "dbsnp": {
            "url": DBSNP_URL,
            "last_known_version": "b151",
        },
        "mondo_hpo": {
            "url": MONDO_HPO_URL,
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


def _mock_head_client(
    content_length: str | None = "987654321",
    last_modified: str | None = None,
):
    """Build a context-manager mock that replaces ``httpx.Client``.

    Returns a ``MagicMock`` whose ``.head()`` succeeds with stubbed
    ``Content-Length`` (and optionally ``Last-Modified``) response headers.
    The caller can override ``head.side_effect`` for failure paths.
    """
    headers = {}
    if content_length is not None:
        headers["Content-Length"] = content_length
    if last_modified is not None:
        headers["Last-Modified"] = last_modified

    mock_resp = MagicMock()
    mock_resp.headers = headers
    mock_resp.raise_for_status = MagicMock()

    mock_client = MagicMock()
    mock_client.return_value.__enter__ = MagicMock(return_value=mock_client.return_value)
    mock_client.return_value.__exit__ = MagicMock(return_value=False)
    mock_client.return_value.head.return_value = mock_resp
    return mock_client, mock_resp


def _cpic_release_payload(
    *,
    tag_name: str = "v1.30.0",
    asset_url: str | None = CPIC_ASSET_URL,
    asset_size: int | None = 12_345_678,
    published_at: str | None = "2026-04-15T12:34:56Z",
) -> dict:
    """Build a stub GitHub releases-API JSON payload for CPIC."""
    payload: dict = {"tag_name": tag_name}
    if published_at is not None:
        payload["published_at"] = published_at
    if asset_url is not None or asset_size is not None:
        asset: dict = {}
        if asset_url is not None:
            asset["browser_download_url"] = asset_url
        if asset_size is not None:
            asset["size"] = asset_size
        payload["assets"] = [asset]
    return payload


def _mock_get_client(payload: dict | None = None):
    """Build a context-manager mock that replaces ``httpx.Client`` for GET calls.

    Returns a ``MagicMock`` whose ``.get()`` succeeds with the given JSON
    payload (defaults to a typical CPIC release response). The caller can
    override ``get.side_effect`` for failure paths.
    """
    if payload is None:
        payload = _cpic_release_payload()

    mock_resp = MagicMock()
    mock_resp.json.return_value = payload
    mock_resp.raise_for_status = MagicMock()

    mock_client = MagicMock()
    mock_client.return_value.__enter__ = MagicMock(return_value=mock_client.return_value)
    mock_client.return_value.__exit__ = MagicMock(return_value=False)
    mock_client.return_value.get.return_value = mock_resp
    return mock_client, mock_resp


# ── check_dbnsfp_update ────────────────────────────────────────────────


class TestCheckDbnsfpUpdate:
    def test_older_recorded_returns_version_info(
        self, tmp_path: Path, monkeypatch, reference_engine
    ):
        """Recorded version older than manifest pin → VersionInfo."""
        path = _write_manifest(tmp_path, SAMPLE_MANIFEST)
        monkeypatch.setenv(manifest_mod.MANIFEST_PATH_ENV, str(path))
        _record_version_row(reference_engine, "dbnsfp", "5.3.0")

        mock_client, _ = _mock_head_client(content_length="50000000000")
        with patch("backend.annotation.dbnsfp.httpx.Client", mock_client):
            result = check_dbnsfp_update(reference_engine)

        assert result is not None
        assert isinstance(result, VersionInfo)
        assert result.db_name == "dbnsfp"
        assert result.latest_version == "5.3.1a"
        assert result.download_url == DBNSFP_URL
        assert result.download_size_bytes == 50_000_000_000
        mock_client.return_value.head.assert_called_once_with(DBNSFP_URL)

    def test_newer_recorded_returns_none(self, tmp_path: Path, monkeypatch, reference_engine):
        """Recorded version newer than manifest pin → no downgrade offered."""
        path = _write_manifest(tmp_path, SAMPLE_MANIFEST)
        monkeypatch.setenv(manifest_mod.MANIFEST_PATH_ENV, str(path))
        _record_version_row(reference_engine, "dbnsfp", "5.3.2")

        mock_client, _ = _mock_head_client()
        with patch("backend.annotation.dbnsfp.httpx.Client", mock_client):
            result = check_dbnsfp_update(reference_engine)

        assert result is None
        mock_client.return_value.head.assert_not_called()

    def test_matching_recorded_returns_none(self, tmp_path: Path, monkeypatch, reference_engine):
        """Recorded version equals manifest pin → already up to date."""
        path = _write_manifest(tmp_path, SAMPLE_MANIFEST)
        monkeypatch.setenv(manifest_mod.MANIFEST_PATH_ENV, str(path))
        _record_version_row(reference_engine, "dbnsfp", "5.3.1a")

        mock_client, _ = _mock_head_client()
        with patch("backend.annotation.dbnsfp.httpx.Client", mock_client):
            assert check_dbnsfp_update(reference_engine) is None
        mock_client.return_value.head.assert_not_called()

    def test_no_recorded_version_returns_version_info(
        self, tmp_path: Path, monkeypatch, reference_engine
    ):
        """Fresh install (no database_versions row) → offer the pinned version."""
        path = _write_manifest(tmp_path, SAMPLE_MANIFEST)
        monkeypatch.setenv(manifest_mod.MANIFEST_PATH_ENV, str(path))

        mock_client, _ = _mock_head_client(content_length="50000000000")
        with patch("backend.annotation.dbnsfp.httpx.Client", mock_client):
            result = check_dbnsfp_update(reference_engine)

        assert result is not None
        assert result.latest_version == "5.3.1a"
        assert result.download_size_bytes == 50_000_000_000

    def test_head_missing_content_length_returns_zero_size(
        self, tmp_path: Path, monkeypatch, reference_engine
    ):
        """HEAD succeeds without Content-Length → size defaults to 0."""
        path = _write_manifest(tmp_path, SAMPLE_MANIFEST)
        monkeypatch.setenv(manifest_mod.MANIFEST_PATH_ENV, str(path))

        mock_client, _ = _mock_head_client(content_length=None)
        with patch("backend.annotation.dbnsfp.httpx.Client", mock_client):
            result = check_dbnsfp_update(reference_engine)

        assert result is not None
        assert result.download_size_bytes == 0

    def test_head_network_error_returns_none(self, tmp_path: Path, monkeypatch, reference_engine):
        """HEAD raises → graceful None (no spurious update banner)."""
        path = _write_manifest(tmp_path, SAMPLE_MANIFEST)
        monkeypatch.setenv(manifest_mod.MANIFEST_PATH_ENV, str(path))

        mock_client, _ = _mock_head_client()
        mock_client.return_value.head.side_effect = httpx.ConnectError("nope")
        with patch("backend.annotation.dbnsfp.httpx.Client", mock_client):
            assert check_dbnsfp_update(reference_engine) is None

    def test_head_http_error_returns_none(self, tmp_path: Path, monkeypatch, reference_engine):
        """HEAD returns non-2xx → raise_for_status raises → None."""
        path = _write_manifest(tmp_path, SAMPLE_MANIFEST)
        monkeypatch.setenv(manifest_mod.MANIFEST_PATH_ENV, str(path))

        mock_client, mock_resp = _mock_head_client()
        mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "404", request=MagicMock(), response=MagicMock(status_code=404)
        )
        with patch("backend.annotation.dbnsfp.httpx.Client", mock_client):
            assert check_dbnsfp_update(reference_engine) is None

    def test_manifest_unreachable_returns_none(self, reference_engine):
        """Manifest fetch failure → None without attempting HEAD."""
        with patch(
            "backend.db.manifest.httpx.get",
            side_effect=httpx.ConnectError("nope"),
        ):
            assert check_dbnsfp_update(reference_engine) is None

    def test_manifest_missing_pipeline_pin_returns_none(
        self, tmp_path: Path, monkeypatch, reference_engine
    ):
        """No ``pipeline_pins["dbnsfp"]`` entry → None."""
        payload = json.loads(json.dumps(SAMPLE_MANIFEST))
        del payload["pipeline_pins"]["dbnsfp"]
        path = _write_manifest(tmp_path, payload)
        monkeypatch.setenv(manifest_mod.MANIFEST_PATH_ENV, str(path))

        assert check_dbnsfp_update(reference_engine) is None

    def test_manifest_pin_empty_version_returns_none(
        self, tmp_path: Path, monkeypatch, reference_engine
    ):
        """Pipeline pin with empty ``last_known_version`` → nothing to compare → None."""
        payload = json.loads(json.dumps(SAMPLE_MANIFEST))
        payload["pipeline_pins"]["dbnsfp"]["last_known_version"] = ""
        path = _write_manifest(tmp_path, payload)
        monkeypatch.setenv(manifest_mod.MANIFEST_PATH_ENV, str(path))

        assert check_dbnsfp_update(reference_engine) is None

    def test_settings_argument_accepted_and_ignored(
        self, tmp_path: Path, monkeypatch, reference_engine
    ):
        """Signature parity: ``settings`` is accepted positionally / by keyword."""
        path = _write_manifest(tmp_path, SAMPLE_MANIFEST)
        monkeypatch.setenv(manifest_mod.MANIFEST_PATH_ENV, str(path))
        _record_version_row(reference_engine, "dbnsfp", "5.3.1a")

        # Both call shapes return None (recorded matches pin).
        assert check_dbnsfp_update(reference_engine, None) is None
        assert check_dbnsfp_update(reference_engine, settings=object()) is None


# ── check_cpic_update ──────────────────────────────────────────────────


class TestCheckCpicUpdate:
    def test_different_recorded_returns_version_info(
        self, tmp_path: Path, monkeypatch, reference_engine
    ):
        """Recorded tag differs from GitHub-latest tag → VersionInfo with asset details."""
        path = _write_manifest(tmp_path, SAMPLE_MANIFEST)
        monkeypatch.setenv(manifest_mod.MANIFEST_PATH_ENV, str(path))
        _record_version_row(reference_engine, "cpic", "v1.29.0")

        mock_client, _ = _mock_get_client()
        with patch("backend.annotation.cpic.httpx.Client", mock_client):
            result = check_cpic_update(reference_engine)

        assert result is not None
        assert isinstance(result, VersionInfo)
        assert result.db_name == "cpic"
        assert result.latest_version == "v1.30.0"
        assert result.download_url == CPIC_ASSET_URL
        assert result.download_size_bytes == 12_345_678
        assert result.release_date == "2026-04-15"
        # GET issued against the manifest-pinned releases-API URL.
        mock_client.return_value.get.assert_called_once()
        args, _kwargs = mock_client.return_value.get.call_args
        assert args[0] == CPIC_RELEASE_API_URL

    def test_matching_recorded_returns_none(self, tmp_path: Path, monkeypatch, reference_engine):
        """Recorded tag equals GitHub-latest tag → already up to date."""
        path = _write_manifest(tmp_path, SAMPLE_MANIFEST)
        monkeypatch.setenv(manifest_mod.MANIFEST_PATH_ENV, str(path))
        _record_version_row(reference_engine, "cpic", "v1.30.0")

        mock_client, _ = _mock_get_client()
        with patch("backend.annotation.cpic.httpx.Client", mock_client):
            assert check_cpic_update(reference_engine) is None

    def test_no_recorded_version_returns_version_info(
        self, tmp_path: Path, monkeypatch, reference_engine
    ):
        """Fresh install (no database_versions row) → offer the latest GitHub tag."""
        path = _write_manifest(tmp_path, SAMPLE_MANIFEST)
        monkeypatch.setenv(manifest_mod.MANIFEST_PATH_ENV, str(path))

        mock_client, _ = _mock_get_client()
        with patch("backend.annotation.cpic.httpx.Client", mock_client):
            result = check_cpic_update(reference_engine)

        assert result is not None
        assert result.latest_version == "v1.30.0"
        assert result.download_size_bytes == 12_345_678

    def test_no_assets_falls_back_to_manifest_url(
        self, tmp_path: Path, monkeypatch, reference_engine
    ):
        """Release without assets → download_url falls back to the manifest URL, size 0."""
        path = _write_manifest(tmp_path, SAMPLE_MANIFEST)
        monkeypatch.setenv(manifest_mod.MANIFEST_PATH_ENV, str(path))

        payload = _cpic_release_payload(asset_url=None, asset_size=None)
        payload.pop("assets", None)

        mock_client, _ = _mock_get_client(payload=payload)
        with patch("backend.annotation.cpic.httpx.Client", mock_client):
            result = check_cpic_update(reference_engine)

        assert result is not None
        assert result.download_url == CPIC_RELEASE_API_URL
        assert result.download_size_bytes == 0

    def test_missing_published_at_leaves_release_date_none(
        self, tmp_path: Path, monkeypatch, reference_engine
    ):
        """Release without ``published_at`` → release_date is None."""
        path = _write_manifest(tmp_path, SAMPLE_MANIFEST)
        monkeypatch.setenv(manifest_mod.MANIFEST_PATH_ENV, str(path))

        payload = _cpic_release_payload(published_at=None)

        mock_client, _ = _mock_get_client(payload=payload)
        with patch("backend.annotation.cpic.httpx.Client", mock_client):
            result = check_cpic_update(reference_engine)

        assert result is not None
        assert result.release_date is None

    def test_missing_tag_name_returns_none(self, tmp_path: Path, monkeypatch, reference_engine):
        """Payload without ``tag_name`` → cannot compare → None."""
        path = _write_manifest(tmp_path, SAMPLE_MANIFEST)
        monkeypatch.setenv(manifest_mod.MANIFEST_PATH_ENV, str(path))

        mock_client, _ = _mock_get_client(payload={"published_at": "2026-04-15T00:00:00Z"})
        with patch("backend.annotation.cpic.httpx.Client", mock_client):
            assert check_cpic_update(reference_engine) is None

    def test_non_dict_payload_returns_none(self, tmp_path: Path, monkeypatch, reference_engine):
        """GitHub returns a list (e.g. error wrapper) → None."""
        path = _write_manifest(tmp_path, SAMPLE_MANIFEST)
        monkeypatch.setenv(manifest_mod.MANIFEST_PATH_ENV, str(path))

        mock_client, _ = _mock_get_client(payload=[])
        with patch("backend.annotation.cpic.httpx.Client", mock_client):
            assert check_cpic_update(reference_engine) is None

    def test_get_network_error_returns_none(self, tmp_path: Path, monkeypatch, reference_engine):
        """GET raises → graceful None (no spurious update banner)."""
        path = _write_manifest(tmp_path, SAMPLE_MANIFEST)
        monkeypatch.setenv(manifest_mod.MANIFEST_PATH_ENV, str(path))

        mock_client, _ = _mock_get_client()
        mock_client.return_value.get.side_effect = httpx.ConnectError("nope")
        with patch("backend.annotation.cpic.httpx.Client", mock_client):
            assert check_cpic_update(reference_engine) is None

    def test_get_http_error_returns_none(self, tmp_path: Path, monkeypatch, reference_engine):
        """GET returns non-2xx → raise_for_status raises → None."""
        path = _write_manifest(tmp_path, SAMPLE_MANIFEST)
        monkeypatch.setenv(manifest_mod.MANIFEST_PATH_ENV, str(path))

        mock_client, mock_resp = _mock_get_client()
        mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "404", request=MagicMock(), response=MagicMock(status_code=404)
        )
        with patch("backend.annotation.cpic.httpx.Client", mock_client):
            assert check_cpic_update(reference_engine) is None

    def test_manifest_unreachable_returns_none(self, reference_engine):
        """Manifest fetch failure → None without attempting GET."""
        with patch(
            "backend.db.manifest.httpx.get",
            side_effect=httpx.ConnectError("nope"),
        ):
            assert check_cpic_update(reference_engine) is None

    def test_manifest_missing_pipeline_pin_returns_none(
        self, tmp_path: Path, monkeypatch, reference_engine
    ):
        """No ``pipeline_pins["cpic"]`` entry → None."""
        payload = json.loads(json.dumps(SAMPLE_MANIFEST))
        del payload["pipeline_pins"]["cpic"]
        path = _write_manifest(tmp_path, payload)
        monkeypatch.setenv(manifest_mod.MANIFEST_PATH_ENV, str(path))

        assert check_cpic_update(reference_engine) is None

    def test_manifest_pin_empty_url_returns_none(
        self, tmp_path: Path, monkeypatch, reference_engine
    ):
        """Pipeline pin with empty ``url`` → nothing to GET → None."""
        payload = json.loads(json.dumps(SAMPLE_MANIFEST))
        payload["pipeline_pins"]["cpic"]["url"] = ""
        path = _write_manifest(tmp_path, payload)
        monkeypatch.setenv(manifest_mod.MANIFEST_PATH_ENV, str(path))

        assert check_cpic_update(reference_engine) is None

    def test_settings_argument_accepted_and_ignored(
        self, tmp_path: Path, monkeypatch, reference_engine
    ):
        """Signature parity: ``settings`` is accepted positionally / by keyword."""
        path = _write_manifest(tmp_path, SAMPLE_MANIFEST)
        monkeypatch.setenv(manifest_mod.MANIFEST_PATH_ENV, str(path))
        _record_version_row(reference_engine, "cpic", "v1.30.0")

        mock_client, _ = _mock_get_client()
        with patch("backend.annotation.cpic.httpx.Client", mock_client):
            assert check_cpic_update(reference_engine, None) is None
            assert check_cpic_update(reference_engine, settings=object()) is None


# ── check_gwas_update ──────────────────────────────────────────────────


# Tue, 15 Apr 2026 12:34:56 GMT → 20260415
GWAS_LAST_MODIFIED_NEW = "Wed, 15 Apr 2026 12:34:56 GMT"
GWAS_LAST_MODIFIED_NEW_VERSION = "20260415"
GWAS_LAST_MODIFIED_OLD = "Sun, 01 Mar 2026 00:00:00 GMT"
GWAS_LAST_MODIFIED_OLD_VERSION = "20260301"


class TestCheckGwasUpdate:
    def test_older_recorded_returns_version_info(
        self, tmp_path: Path, monkeypatch, reference_engine
    ):
        """Recorded date older than remote Last-Modified → VersionInfo."""
        path = _write_manifest(tmp_path, SAMPLE_MANIFEST)
        monkeypatch.setenv(manifest_mod.MANIFEST_PATH_ENV, str(path))
        _record_version_row(reference_engine, "gwas_catalog", GWAS_LAST_MODIFIED_OLD_VERSION)

        mock_client, _ = _mock_head_client(
            content_length="123456789",
            last_modified=GWAS_LAST_MODIFIED_NEW,
        )
        with patch("backend.annotation.gwas.httpx.Client", mock_client):
            result = check_gwas_update(reference_engine)

        assert result is not None
        assert isinstance(result, VersionInfo)
        assert result.db_name == "gwas_catalog"
        assert result.latest_version == GWAS_LAST_MODIFIED_NEW_VERSION
        assert result.download_url == GWAS_URL
        assert result.download_size_bytes == 123_456_789
        assert result.release_date == GWAS_LAST_MODIFIED_NEW_VERSION
        mock_client.return_value.head.assert_called_once_with(GWAS_URL)

    def test_newer_recorded_returns_none(self, tmp_path: Path, monkeypatch, reference_engine):
        """Recorded date newer than remote Last-Modified → no downgrade offered."""
        path = _write_manifest(tmp_path, SAMPLE_MANIFEST)
        monkeypatch.setenv(manifest_mod.MANIFEST_PATH_ENV, str(path))
        _record_version_row(reference_engine, "gwas_catalog", "20270101")

        mock_client, _ = _mock_head_client(last_modified=GWAS_LAST_MODIFIED_NEW)
        with patch("backend.annotation.gwas.httpx.Client", mock_client):
            assert check_gwas_update(reference_engine) is None

    def test_matching_recorded_returns_none(self, tmp_path: Path, monkeypatch, reference_engine):
        """Recorded date equals remote Last-Modified → already up to date."""
        path = _write_manifest(tmp_path, SAMPLE_MANIFEST)
        monkeypatch.setenv(manifest_mod.MANIFEST_PATH_ENV, str(path))
        _record_version_row(reference_engine, "gwas_catalog", GWAS_LAST_MODIFIED_NEW_VERSION)

        mock_client, _ = _mock_head_client(last_modified=GWAS_LAST_MODIFIED_NEW)
        with patch("backend.annotation.gwas.httpx.Client", mock_client):
            assert check_gwas_update(reference_engine) is None

    def test_no_recorded_version_returns_version_info(
        self, tmp_path: Path, monkeypatch, reference_engine
    ):
        """Fresh install (no database_versions row) → offer the remote release."""
        path = _write_manifest(tmp_path, SAMPLE_MANIFEST)
        monkeypatch.setenv(manifest_mod.MANIFEST_PATH_ENV, str(path))

        mock_client, _ = _mock_head_client(
            content_length="123456789",
            last_modified=GWAS_LAST_MODIFIED_NEW,
        )
        with patch("backend.annotation.gwas.httpx.Client", mock_client):
            result = check_gwas_update(reference_engine)

        assert result is not None
        assert result.latest_version == GWAS_LAST_MODIFIED_NEW_VERSION
        assert result.download_size_bytes == 123_456_789

    def test_head_missing_content_length_returns_zero_size(
        self, tmp_path: Path, monkeypatch, reference_engine
    ):
        """HEAD succeeds without Content-Length → size defaults to 0."""
        path = _write_manifest(tmp_path, SAMPLE_MANIFEST)
        monkeypatch.setenv(manifest_mod.MANIFEST_PATH_ENV, str(path))

        mock_client, _ = _mock_head_client(
            content_length=None,
            last_modified=GWAS_LAST_MODIFIED_NEW,
        )
        with patch("backend.annotation.gwas.httpx.Client", mock_client):
            result = check_gwas_update(reference_engine)

        assert result is not None
        assert result.download_size_bytes == 0

    def test_head_unparseable_content_length_returns_zero_size(
        self, tmp_path: Path, monkeypatch, reference_engine
    ):
        """Non-integer Content-Length header → size defaults to 0 (no crash)."""
        path = _write_manifest(tmp_path, SAMPLE_MANIFEST)
        monkeypatch.setenv(manifest_mod.MANIFEST_PATH_ENV, str(path))

        mock_client, _ = _mock_head_client(
            content_length="not-a-number",
            last_modified=GWAS_LAST_MODIFIED_NEW,
        )
        with patch("backend.annotation.gwas.httpx.Client", mock_client):
            result = check_gwas_update(reference_engine)

        assert result is not None
        assert result.download_size_bytes == 0

    def test_head_missing_last_modified_returns_none(
        self, tmp_path: Path, monkeypatch, reference_engine
    ):
        """HEAD succeeds without Last-Modified → cannot derive remote version → None."""
        path = _write_manifest(tmp_path, SAMPLE_MANIFEST)
        monkeypatch.setenv(manifest_mod.MANIFEST_PATH_ENV, str(path))

        # No last_modified key in headers.
        mock_client, _ = _mock_head_client(content_length="123456789")
        with patch("backend.annotation.gwas.httpx.Client", mock_client):
            assert check_gwas_update(reference_engine) is None

    def test_head_unparseable_last_modified_returns_none(
        self, tmp_path: Path, monkeypatch, reference_engine
    ):
        """Last-Modified that ``email.utils.parsedate_to_datetime`` can't parse → None."""
        path = _write_manifest(tmp_path, SAMPLE_MANIFEST)
        monkeypatch.setenv(manifest_mod.MANIFEST_PATH_ENV, str(path))

        mock_client, _ = _mock_head_client(last_modified="not-a-date")
        with patch("backend.annotation.gwas.httpx.Client", mock_client):
            assert check_gwas_update(reference_engine) is None

    def test_head_network_error_returns_none(self, tmp_path: Path, monkeypatch, reference_engine):
        """HEAD raises → graceful None (no spurious update banner)."""
        path = _write_manifest(tmp_path, SAMPLE_MANIFEST)
        monkeypatch.setenv(manifest_mod.MANIFEST_PATH_ENV, str(path))

        mock_client, _ = _mock_head_client(last_modified=GWAS_LAST_MODIFIED_NEW)
        mock_client.return_value.head.side_effect = httpx.ConnectError("nope")
        with patch("backend.annotation.gwas.httpx.Client", mock_client):
            assert check_gwas_update(reference_engine) is None

    def test_head_http_error_returns_none(self, tmp_path: Path, monkeypatch, reference_engine):
        """HEAD returns non-2xx → raise_for_status raises → None."""
        path = _write_manifest(tmp_path, SAMPLE_MANIFEST)
        monkeypatch.setenv(manifest_mod.MANIFEST_PATH_ENV, str(path))

        mock_client, mock_resp = _mock_head_client(last_modified=GWAS_LAST_MODIFIED_NEW)
        mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "404", request=MagicMock(), response=MagicMock(status_code=404)
        )
        with patch("backend.annotation.gwas.httpx.Client", mock_client):
            assert check_gwas_update(reference_engine) is None

    def test_manifest_unreachable_returns_none(self, reference_engine):
        """Manifest fetch failure → None without attempting HEAD."""
        with patch(
            "backend.db.manifest.httpx.get",
            side_effect=httpx.ConnectError("nope"),
        ):
            assert check_gwas_update(reference_engine) is None

    def test_manifest_missing_pipeline_pin_returns_none(
        self, tmp_path: Path, monkeypatch, reference_engine
    ):
        """No ``pipeline_pins["gwas_catalog"]`` entry → None."""
        payload = json.loads(json.dumps(SAMPLE_MANIFEST))
        del payload["pipeline_pins"]["gwas_catalog"]
        path = _write_manifest(tmp_path, payload)
        monkeypatch.setenv(manifest_mod.MANIFEST_PATH_ENV, str(path))

        assert check_gwas_update(reference_engine) is None

    def test_manifest_pin_empty_url_returns_none(
        self, tmp_path: Path, monkeypatch, reference_engine
    ):
        """Pipeline pin with empty ``url`` is rejected by the manifest parser → None.

        The shared manifest parser requires a non-empty URL on every pipeline
        pin, so an empty URL surfaces as ``ManifestFetchError`` → ``None``.
        """
        payload = json.loads(json.dumps(SAMPLE_MANIFEST))
        payload["pipeline_pins"]["gwas_catalog"]["url"] = ""
        path = _write_manifest(tmp_path, payload)
        monkeypatch.setenv(manifest_mod.MANIFEST_PATH_ENV, str(path))

        assert check_gwas_update(reference_engine) is None

    def test_settings_argument_accepted_and_ignored(
        self, tmp_path: Path, monkeypatch, reference_engine
    ):
        """Signature parity: ``settings`` is accepted positionally / by keyword."""
        path = _write_manifest(tmp_path, SAMPLE_MANIFEST)
        monkeypatch.setenv(manifest_mod.MANIFEST_PATH_ENV, str(path))
        _record_version_row(reference_engine, "gwas_catalog", GWAS_LAST_MODIFIED_NEW_VERSION)

        mock_client, _ = _mock_head_client(last_modified=GWAS_LAST_MODIFIED_NEW)
        with patch("backend.annotation.gwas.httpx.Client", mock_client):
            assert check_gwas_update(reference_engine, None) is None
            assert check_gwas_update(reference_engine, settings=object()) is None


# ── check_dbsnp_update ─────────────────────────────────────────────────


# Tue, 15 Apr 2026 12:34:56 GMT → 20260415
DBSNP_LAST_MODIFIED_NEW = "Wed, 15 Apr 2026 12:34:56 GMT"
DBSNP_LAST_MODIFIED_NEW_VERSION = "20260415"
DBSNP_LAST_MODIFIED_OLD = "Sun, 01 Mar 2026 00:00:00 GMT"
DBSNP_LAST_MODIFIED_OLD_VERSION = "20260301"


class TestCheckDbsnpUpdate:
    def test_older_recorded_returns_version_info(
        self, tmp_path: Path, monkeypatch, reference_engine
    ):
        """Recorded date older than remote Last-Modified → VersionInfo."""
        path = _write_manifest(tmp_path, SAMPLE_MANIFEST)
        monkeypatch.setenv(manifest_mod.MANIFEST_PATH_ENV, str(path))
        _record_version_row(reference_engine, "dbsnp", DBSNP_LAST_MODIFIED_OLD_VERSION)

        mock_client, _ = _mock_head_client(
            content_length="567890123",
            last_modified=DBSNP_LAST_MODIFIED_NEW,
        )
        with patch("backend.annotation.dbsnp.httpx.Client", mock_client):
            result = check_dbsnp_update(reference_engine)

        assert result is not None
        assert isinstance(result, VersionInfo)
        assert result.db_name == "dbsnp"
        assert result.latest_version == DBSNP_LAST_MODIFIED_NEW_VERSION
        assert result.download_url == DBSNP_URL
        assert result.download_size_bytes == 567_890_123
        assert result.release_date == DBSNP_LAST_MODIFIED_NEW_VERSION
        mock_client.return_value.head.assert_called_once_with(DBSNP_URL)

    def test_newer_recorded_returns_none(self, tmp_path: Path, monkeypatch, reference_engine):
        """Recorded date newer than remote Last-Modified → no downgrade offered."""
        path = _write_manifest(tmp_path, SAMPLE_MANIFEST)
        monkeypatch.setenv(manifest_mod.MANIFEST_PATH_ENV, str(path))
        _record_version_row(reference_engine, "dbsnp", "20270101")

        mock_client, _ = _mock_head_client(last_modified=DBSNP_LAST_MODIFIED_NEW)
        with patch("backend.annotation.dbsnp.httpx.Client", mock_client):
            assert check_dbsnp_update(reference_engine) is None

    def test_matching_recorded_returns_none(self, tmp_path: Path, monkeypatch, reference_engine):
        """Recorded date equals remote Last-Modified → already up to date."""
        path = _write_manifest(tmp_path, SAMPLE_MANIFEST)
        monkeypatch.setenv(manifest_mod.MANIFEST_PATH_ENV, str(path))
        _record_version_row(reference_engine, "dbsnp", DBSNP_LAST_MODIFIED_NEW_VERSION)

        mock_client, _ = _mock_head_client(last_modified=DBSNP_LAST_MODIFIED_NEW)
        with patch("backend.annotation.dbsnp.httpx.Client", mock_client):
            assert check_dbsnp_update(reference_engine) is None

    def test_no_recorded_version_returns_version_info(
        self, tmp_path: Path, monkeypatch, reference_engine
    ):
        """Fresh install (no database_versions row) → offer the remote release."""
        path = _write_manifest(tmp_path, SAMPLE_MANIFEST)
        monkeypatch.setenv(manifest_mod.MANIFEST_PATH_ENV, str(path))

        mock_client, _ = _mock_head_client(
            content_length="567890123",
            last_modified=DBSNP_LAST_MODIFIED_NEW,
        )
        with patch("backend.annotation.dbsnp.httpx.Client", mock_client):
            result = check_dbsnp_update(reference_engine)

        assert result is not None
        assert result.latest_version == DBSNP_LAST_MODIFIED_NEW_VERSION
        assert result.download_size_bytes == 567_890_123

    def test_head_missing_content_length_returns_zero_size(
        self, tmp_path: Path, monkeypatch, reference_engine
    ):
        """HEAD succeeds without Content-Length → size defaults to 0."""
        path = _write_manifest(tmp_path, SAMPLE_MANIFEST)
        monkeypatch.setenv(manifest_mod.MANIFEST_PATH_ENV, str(path))

        mock_client, _ = _mock_head_client(
            content_length=None,
            last_modified=DBSNP_LAST_MODIFIED_NEW,
        )
        with patch("backend.annotation.dbsnp.httpx.Client", mock_client):
            result = check_dbsnp_update(reference_engine)

        assert result is not None
        assert result.download_size_bytes == 0

    def test_head_unparseable_content_length_returns_zero_size(
        self, tmp_path: Path, monkeypatch, reference_engine
    ):
        """Non-integer Content-Length header → size defaults to 0 (no crash)."""
        path = _write_manifest(tmp_path, SAMPLE_MANIFEST)
        monkeypatch.setenv(manifest_mod.MANIFEST_PATH_ENV, str(path))

        mock_client, _ = _mock_head_client(
            content_length="not-a-number",
            last_modified=DBSNP_LAST_MODIFIED_NEW,
        )
        with patch("backend.annotation.dbsnp.httpx.Client", mock_client):
            result = check_dbsnp_update(reference_engine)

        assert result is not None
        assert result.download_size_bytes == 0

    def test_head_missing_last_modified_returns_none(
        self, tmp_path: Path, monkeypatch, reference_engine
    ):
        """HEAD succeeds without Last-Modified → cannot derive remote version → None."""
        path = _write_manifest(tmp_path, SAMPLE_MANIFEST)
        monkeypatch.setenv(manifest_mod.MANIFEST_PATH_ENV, str(path))

        mock_client, _ = _mock_head_client(content_length="567890123")
        with patch("backend.annotation.dbsnp.httpx.Client", mock_client):
            assert check_dbsnp_update(reference_engine) is None

    def test_head_unparseable_last_modified_returns_none(
        self, tmp_path: Path, monkeypatch, reference_engine
    ):
        """Last-Modified that ``email.utils.parsedate_to_datetime`` can't parse → None."""
        path = _write_manifest(tmp_path, SAMPLE_MANIFEST)
        monkeypatch.setenv(manifest_mod.MANIFEST_PATH_ENV, str(path))

        mock_client, _ = _mock_head_client(last_modified="not-a-date")
        with patch("backend.annotation.dbsnp.httpx.Client", mock_client):
            assert check_dbsnp_update(reference_engine) is None

    def test_head_network_error_returns_none(self, tmp_path: Path, monkeypatch, reference_engine):
        """HEAD raises → graceful None (no spurious update banner)."""
        path = _write_manifest(tmp_path, SAMPLE_MANIFEST)
        monkeypatch.setenv(manifest_mod.MANIFEST_PATH_ENV, str(path))

        mock_client, _ = _mock_head_client(last_modified=DBSNP_LAST_MODIFIED_NEW)
        mock_client.return_value.head.side_effect = httpx.ConnectError("nope")
        with patch("backend.annotation.dbsnp.httpx.Client", mock_client):
            assert check_dbsnp_update(reference_engine) is None

    def test_head_http_error_returns_none(self, tmp_path: Path, monkeypatch, reference_engine):
        """HEAD returns non-2xx → raise_for_status raises → None."""
        path = _write_manifest(tmp_path, SAMPLE_MANIFEST)
        monkeypatch.setenv(manifest_mod.MANIFEST_PATH_ENV, str(path))

        mock_client, mock_resp = _mock_head_client(last_modified=DBSNP_LAST_MODIFIED_NEW)
        mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "404", request=MagicMock(), response=MagicMock(status_code=404)
        )
        with patch("backend.annotation.dbsnp.httpx.Client", mock_client):
            assert check_dbsnp_update(reference_engine) is None

    def test_manifest_unreachable_returns_none(self, reference_engine):
        """Manifest fetch failure → None without attempting HEAD."""
        with patch(
            "backend.db.manifest.httpx.get",
            side_effect=httpx.ConnectError("nope"),
        ):
            assert check_dbsnp_update(reference_engine) is None

    def test_manifest_missing_pipeline_pin_returns_none(
        self, tmp_path: Path, monkeypatch, reference_engine
    ):
        """No ``pipeline_pins["dbsnp"]`` entry → None."""
        payload = json.loads(json.dumps(SAMPLE_MANIFEST))
        del payload["pipeline_pins"]["dbsnp"]
        path = _write_manifest(tmp_path, payload)
        monkeypatch.setenv(manifest_mod.MANIFEST_PATH_ENV, str(path))

        assert check_dbsnp_update(reference_engine) is None

    def test_manifest_pin_empty_url_returns_none(
        self, tmp_path: Path, monkeypatch, reference_engine
    ):
        """Pipeline pin with empty ``url`` is rejected by the manifest parser → None.

        The shared manifest parser requires a non-empty URL on every pipeline
        pin, so an empty URL surfaces as ``ManifestFetchError`` → ``None``.
        """
        payload = json.loads(json.dumps(SAMPLE_MANIFEST))
        payload["pipeline_pins"]["dbsnp"]["url"] = ""
        path = _write_manifest(tmp_path, payload)
        monkeypatch.setenv(manifest_mod.MANIFEST_PATH_ENV, str(path))

        assert check_dbsnp_update(reference_engine) is None

    def test_settings_argument_accepted_and_ignored(
        self, tmp_path: Path, monkeypatch, reference_engine
    ):
        """Signature parity: ``settings`` is accepted positionally / by keyword."""
        path = _write_manifest(tmp_path, SAMPLE_MANIFEST)
        monkeypatch.setenv(manifest_mod.MANIFEST_PATH_ENV, str(path))
        _record_version_row(reference_engine, "dbsnp", DBSNP_LAST_MODIFIED_NEW_VERSION)

        mock_client, _ = _mock_head_client(last_modified=DBSNP_LAST_MODIFIED_NEW)
        with patch("backend.annotation.dbsnp.httpx.Client", mock_client):
            assert check_dbsnp_update(reference_engine, None) is None
            assert check_dbsnp_update(reference_engine, settings=object()) is None


# ── check_mondo_hpo_update ─────────────────────────────────────────────


# Tue, 15 Apr 2026 12:34:56 GMT → 20260415
MONDO_HPO_LAST_MODIFIED_NEW = "Wed, 15 Apr 2026 12:34:56 GMT"
MONDO_HPO_LAST_MODIFIED_NEW_VERSION = "20260415"
MONDO_HPO_LAST_MODIFIED_OLD = "Sun, 01 Mar 2026 00:00:00 GMT"
MONDO_HPO_LAST_MODIFIED_OLD_VERSION = "20260301"


def _current_mondo_hpo_version(
    hpo_validator: str = MONDO_HPO_LAST_MODIFIED_NEW,
    sssom_validator: str = MONDO_HPO_LAST_MODIFIED_NEW,
) -> str:
    """Build the installed v2 source-validator value used by update-check tests."""
    hpo_fingerprint = hashlib.sha256(hpo_validator.encode()).hexdigest()[:16]
    sssom_fingerprint = hashlib.sha256(sssom_validator.encode()).hexdigest()[:16]
    return (
        f"{MONDO_HPO_LAST_MODIFIED_NEW_VERSION}+{MONDO_HPO_INGESTION_REVISION}"
        f"+hpo-{hpo_fingerprint}+mondo-sssom-{sssom_fingerprint}"
    )


class TestCheckMondoHpoUpdate:
    def test_older_recorded_returns_version_info(
        self, tmp_path: Path, monkeypatch, reference_engine
    ):
        """Recorded date older than remote Last-Modified → VersionInfo."""
        path = _write_manifest(tmp_path, SAMPLE_MANIFEST)
        monkeypatch.setenv(manifest_mod.MANIFEST_PATH_ENV, str(path))
        _record_version_row(reference_engine, "mondo_hpo", MONDO_HPO_LAST_MODIFIED_OLD_VERSION)

        mock_client, primary = _mock_head_client(
            content_length="98765432",
            last_modified=MONDO_HPO_LAST_MODIFIED_NEW,
        )
        hpo = MagicMock(headers={"Content-Length": "20", "ETag": "hpo-current"})
        hpo.raise_for_status = MagicMock()
        sssom = MagicMock(headers={"Content-Length": "13", "ETag": "sssom-current"})
        sssom.raise_for_status = MagicMock()
        mock_client.return_value.head.side_effect = [primary, hpo, sssom]
        with patch("backend.annotation.mondo_hpo.httpx.Client", mock_client):
            result = check_mondo_hpo_update(reference_engine)

        assert result is not None
        assert isinstance(result, VersionInfo)
        assert result.db_name == "mondo_hpo"
        assert result.latest_version == MONDO_HPO_LAST_MODIFIED_NEW_VERSION
        assert result.download_url == MONDO_HPO_URL
        assert result.download_size_bytes == 98_765_465
        assert result.release_date == MONDO_HPO_LAST_MODIFIED_NEW_VERSION
        assert mock_client.return_value.head.call_args_list == [
            call(MONDO_HPO_URL),
            call(HPO_GENES_TO_PHENOTYPE_URL),
            call(MONDO_SSSOM_URL),
        ]

    def test_newer_recorded_returns_none(self, tmp_path: Path, monkeypatch, reference_engine):
        """Recorded date newer than remote Last-Modified → no downgrade offered."""
        path = _write_manifest(tmp_path, SAMPLE_MANIFEST)
        monkeypatch.setenv(manifest_mod.MANIFEST_PATH_ENV, str(path))
        _record_version_row(reference_engine, "mondo_hpo", "20270101")

        mock_client, _ = _mock_head_client(last_modified=MONDO_HPO_LAST_MODIFIED_NEW)
        with patch("backend.annotation.mondo_hpo.httpx.Client", mock_client):
            assert check_mondo_hpo_update(reference_engine) is None

    def test_matching_version_with_legacy_terms_offers_label_refresh(
        self, tmp_path: Path, monkeypatch, reference_engine
    ):
        """Same-date ID-only rows receive the one-time labelled-data rebuild."""
        path = _write_manifest(tmp_path, SAMPLE_MANIFEST)
        monkeypatch.setenv(manifest_mod.MANIFEST_PATH_ENV, str(path))
        _record_version_row(reference_engine, "mondo_hpo", MONDO_HPO_LAST_MODIFIED_NEW_VERSION)
        with reference_engine.begin() as conn:
            conn.execute(
                gene_phenotype.insert().values(
                    gene_symbol="BRCA1",
                    disease_name="Hereditary breast cancer",
                    disease_id="MONDO:0005012",
                    hpo_terms='["HP:0003002"]',
                    source="mondo_hpo",
                )
            )

        mock_client, _ = _mock_head_client(last_modified=MONDO_HPO_LAST_MODIFIED_NEW)
        with patch("backend.annotation.mondo_hpo.httpx.Client", mock_client):
            result = check_mondo_hpo_update(reference_engine)

        assert result is not None
        assert result.latest_version == MONDO_HPO_LAST_MODIFIED_NEW_VERSION

    def test_matching_version_with_current_revision_and_labelled_terms_returns_none(
        self, tmp_path: Path, monkeypatch, reference_engine
    ):
        """Current labelled, disease-scoped data does not offer a rebuild."""
        path = _write_manifest(tmp_path, SAMPLE_MANIFEST)
        monkeypatch.setenv(manifest_mod.MANIFEST_PATH_ENV, str(path))
        _record_version_row(
            reference_engine,
            "mondo_hpo",
            _current_mondo_hpo_version(),
        )
        with reference_engine.begin() as conn:
            conn.execute(
                gene_phenotype.insert().values(
                    gene_symbol="BRCA1",
                    disease_name="Hereditary breast cancer",
                    disease_id="MONDO:0005012",
                    hpo_terms=('[{"id": "HP:0003002", "name": "Breast carcinoma"}]'),
                    source="mondo_hpo",
                )
            )

        mock_client, _ = _mock_head_client(last_modified=MONDO_HPO_LAST_MODIFIED_NEW)
        with patch("backend.annotation.mondo_hpo.httpx.Client", mock_client):
            assert check_mondo_hpo_update(reference_engine) is None

    def test_matching_version_with_mixed_term_shapes_offers_refresh(
        self, tmp_path: Path, monkeypatch, reference_engine
    ):
        """Any legacy MONDO/HPO payload triggers the one-time rebuild."""
        path = _write_manifest(tmp_path, SAMPLE_MANIFEST)
        monkeypatch.setenv(manifest_mod.MANIFEST_PATH_ENV, str(path))
        _record_version_row(reference_engine, "mondo_hpo", MONDO_HPO_LAST_MODIFIED_NEW_VERSION)
        with reference_engine.begin() as conn:
            conn.execute(
                gene_phenotype.insert(),
                [
                    {
                        "gene_symbol": "BRCA1",
                        "disease_name": "Hereditary breast cancer",
                        "disease_id": "MONDO:0005012",
                        "hpo_terms": ('[{"id": "HP:0003002", "name": "Breast carcinoma"}]'),
                        "source": "mondo_hpo",
                    },
                    {
                        "gene_symbol": "CFTR",
                        "disease_name": "Cystic fibrosis",
                        "disease_id": "MONDO:0009061",
                        "hpo_terms": '["HP:0002110"]',
                        "source": "mondo_hpo",
                    },
                ],
            )

        mock_client, _ = _mock_head_client(last_modified=MONDO_HPO_LAST_MODIFIED_NEW)
        with patch("backend.annotation.mondo_hpo.httpx.Client", mock_client):
            result = check_mondo_hpo_update(reference_engine)

        assert result is not None
        assert result.latest_version == MONDO_HPO_LAST_MODIFIED_NEW_VERSION

    def test_matching_labelled_version_without_scope_revision_offers_refresh(
        self, tmp_path: Path, monkeypatch, reference_engine
    ):
        """A formerly labelled but gene-wide install is rebuilt once for #2163."""
        path = _write_manifest(tmp_path, SAMPLE_MANIFEST)
        monkeypatch.setenv(manifest_mod.MANIFEST_PATH_ENV, str(path))
        _record_version_row(reference_engine, "mondo_hpo", MONDO_HPO_LAST_MODIFIED_NEW_VERSION)
        with reference_engine.begin() as conn:
            conn.execute(
                gene_phenotype.insert().values(
                    gene_symbol="BRCA1",
                    disease_name="Hereditary breast cancer",
                    disease_id="MONDO:0005012",
                    hpo_terms='[{"id": "HP:0003002", "name": "Breast carcinoma"}]',
                    source="mondo_hpo",
                )
            )

        mock_client, _ = _mock_head_client(last_modified=MONDO_HPO_LAST_MODIFIED_NEW)
        with patch("backend.annotation.mondo_hpo.httpx.Client", mock_client):
            result = check_mondo_hpo_update(reference_engine)

        assert isinstance(result, VersionInfo)
        assert result.db_name == "mondo_hpo"
        assert result.latest_version == MONDO_HPO_LAST_MODIFIED_NEW_VERSION
        assert result.download_url == MONDO_HPO_URL
        assert result.release_date == MONDO_HPO_LAST_MODIFIED_NEW_VERSION

    def test_newer_version_with_legacy_terms_never_downgrades(
        self, tmp_path: Path, monkeypatch, reference_engine
    ):
        """A content migration must not replace a newer installed snapshot."""
        path = _write_manifest(tmp_path, SAMPLE_MANIFEST)
        monkeypatch.setenv(manifest_mod.MANIFEST_PATH_ENV, str(path))
        _record_version_row(reference_engine, "mondo_hpo", "20270101")
        with reference_engine.begin() as conn:
            conn.execute(
                gene_phenotype.insert().values(
                    gene_symbol="BRCA1",
                    disease_name="Hereditary breast cancer",
                    disease_id="MONDO:0005012",
                    hpo_terms='["HP:0003002"]',
                    source="mondo_hpo",
                )
            )

        mock_client, _ = _mock_head_client(last_modified=MONDO_HPO_LAST_MODIFIED_NEW)
        with patch("backend.annotation.mondo_hpo.httpx.Client", mock_client):
            assert check_mondo_hpo_update(reference_engine) is None

    def test_matching_current_revision_returns_none(
        self, tmp_path: Path, monkeypatch, reference_engine
    ):
        """Matching primary source and loader revision are already up to date."""
        path = _write_manifest(tmp_path, SAMPLE_MANIFEST)
        monkeypatch.setenv(manifest_mod.MANIFEST_PATH_ENV, str(path))
        _record_version_row(
            reference_engine,
            "mondo_hpo",
            _current_mondo_hpo_version(),
        )

        mock_client, _ = _mock_head_client(last_modified=MONDO_HPO_LAST_MODIFIED_NEW)
        with patch("backend.annotation.mondo_hpo.httpx.Client", mock_client):
            assert check_mondo_hpo_update(reference_engine) is None

    def test_changed_secondary_validator_offers_refresh(
        self, tmp_path: Path, monkeypatch, reference_engine
    ):
        """An HPO or SSSOM update is not hidden by an unchanged primary date."""
        path = _write_manifest(tmp_path, SAMPLE_MANIFEST)
        monkeypatch.setenv(manifest_mod.MANIFEST_PATH_ENV, str(path))
        _record_version_row(
            reference_engine,
            "mondo_hpo",
            _current_mondo_hpo_version("hpo-old", "sssom-current"),
        )

        primary = MagicMock(headers={"Last-Modified": MONDO_HPO_LAST_MODIFIED_NEW})
        primary.raise_for_status = MagicMock()
        hpo = MagicMock(headers={"ETag": "hpo-new"})
        hpo.raise_for_status = MagicMock()
        sssom = MagicMock(headers={"ETag": "sssom-current"})
        sssom.raise_for_status = MagicMock()
        mock_client, _ = _mock_head_client(last_modified=MONDO_HPO_LAST_MODIFIED_NEW)
        mock_client.return_value.head.side_effect = [primary, hpo, sssom]

        with patch("backend.annotation.mondo_hpo.httpx.Client", mock_client):
            result = check_mondo_hpo_update(reference_engine)

        assert isinstance(result, VersionInfo)
        assert result.download_url == MONDO_HPO_URL
        assert mock_client.return_value.head.call_args_list == [
            call(MONDO_HPO_URL),
            call(HPO_GENES_TO_PHENOTYPE_URL),
            call(MONDO_SSSOM_URL),
        ]

    @pytest.mark.parametrize("installed_version", ["vNext", "2026-04-x"])
    def test_noncanonical_installed_version_is_not_compared_lexically(
        self, tmp_path: Path, monkeypatch, reference_engine, installed_version: str
    ):
        """An unparseable primary version must not trigger a possible downgrade."""
        path = _write_manifest(tmp_path, SAMPLE_MANIFEST)
        monkeypatch.setenv(manifest_mod.MANIFEST_PATH_ENV, str(path))
        _record_version_row(reference_engine, "mondo_hpo", installed_version)

        mock_client, _ = _mock_head_client(last_modified=MONDO_HPO_LAST_MODIFIED_NEW)
        with patch("backend.annotation.mondo_hpo.httpx.Client", mock_client):
            assert check_mondo_hpo_update(reference_engine) is None

        mock_client.return_value.head.assert_called_once_with(MONDO_HPO_URL)

    def test_no_recorded_version_returns_version_info(
        self, tmp_path: Path, monkeypatch, reference_engine
    ):
        """Fresh install (no database_versions row) → offer the remote release."""
        path = _write_manifest(tmp_path, SAMPLE_MANIFEST)
        monkeypatch.setenv(manifest_mod.MANIFEST_PATH_ENV, str(path))

        mock_client, _ = _mock_head_client(
            content_length="98765432",
            last_modified=MONDO_HPO_LAST_MODIFIED_NEW,
        )
        with patch("backend.annotation.mondo_hpo.httpx.Client", mock_client):
            result = check_mondo_hpo_update(reference_engine)

        assert result is not None
        assert result.latest_version == MONDO_HPO_LAST_MODIFIED_NEW_VERSION
        assert result.download_size_bytes == 296_296_296

    def test_head_missing_content_length_returns_zero_size(
        self, tmp_path: Path, monkeypatch, reference_engine
    ):
        """HEAD succeeds without Content-Length → size defaults to 0."""
        path = _write_manifest(tmp_path, SAMPLE_MANIFEST)
        monkeypatch.setenv(manifest_mod.MANIFEST_PATH_ENV, str(path))

        mock_client, _ = _mock_head_client(
            content_length=None,
            last_modified=MONDO_HPO_LAST_MODIFIED_NEW,
        )
        with patch("backend.annotation.mondo_hpo.httpx.Client", mock_client):
            result = check_mondo_hpo_update(reference_engine)

        assert result is not None
        assert result.download_size_bytes == 0

    def test_head_unparseable_content_length_returns_zero_size(
        self, tmp_path: Path, monkeypatch, reference_engine
    ):
        """Non-integer Content-Length header → size defaults to 0 (no crash)."""
        path = _write_manifest(tmp_path, SAMPLE_MANIFEST)
        monkeypatch.setenv(manifest_mod.MANIFEST_PATH_ENV, str(path))

        mock_client, _ = _mock_head_client(
            content_length="not-a-number",
            last_modified=MONDO_HPO_LAST_MODIFIED_NEW,
        )
        with patch("backend.annotation.mondo_hpo.httpx.Client", mock_client):
            result = check_mondo_hpo_update(reference_engine)

        assert result is not None
        assert result.download_size_bytes == 0

    def test_head_missing_last_modified_returns_none(
        self, tmp_path: Path, monkeypatch, reference_engine
    ):
        """HEAD succeeds without Last-Modified → cannot derive remote version → None."""
        path = _write_manifest(tmp_path, SAMPLE_MANIFEST)
        monkeypatch.setenv(manifest_mod.MANIFEST_PATH_ENV, str(path))

        mock_client, _ = _mock_head_client(content_length="98765432")
        with patch("backend.annotation.mondo_hpo.httpx.Client", mock_client):
            assert check_mondo_hpo_update(reference_engine) is None

    def test_head_unparseable_last_modified_returns_none(
        self, tmp_path: Path, monkeypatch, reference_engine
    ):
        """Last-Modified that ``email.utils.parsedate_to_datetime`` can't parse → None."""
        path = _write_manifest(tmp_path, SAMPLE_MANIFEST)
        monkeypatch.setenv(manifest_mod.MANIFEST_PATH_ENV, str(path))

        mock_client, _ = _mock_head_client(last_modified="not-a-date")
        with patch("backend.annotation.mondo_hpo.httpx.Client", mock_client):
            assert check_mondo_hpo_update(reference_engine) is None

    def test_head_network_error_returns_none(self, tmp_path: Path, monkeypatch, reference_engine):
        """HEAD raises → graceful None (no spurious update banner)."""
        path = _write_manifest(tmp_path, SAMPLE_MANIFEST)
        monkeypatch.setenv(manifest_mod.MANIFEST_PATH_ENV, str(path))

        mock_client, _ = _mock_head_client(last_modified=MONDO_HPO_LAST_MODIFIED_NEW)
        mock_client.return_value.head.side_effect = httpx.ConnectError("nope")
        with patch("backend.annotation.mondo_hpo.httpx.Client", mock_client):
            assert check_mondo_hpo_update(reference_engine) is None

    def test_head_http_error_returns_none(self, tmp_path: Path, monkeypatch, reference_engine):
        """HEAD returns non-2xx → raise_for_status raises → None."""
        path = _write_manifest(tmp_path, SAMPLE_MANIFEST)
        monkeypatch.setenv(manifest_mod.MANIFEST_PATH_ENV, str(path))

        mock_client, mock_resp = _mock_head_client(last_modified=MONDO_HPO_LAST_MODIFIED_NEW)
        mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "404", request=MagicMock(), response=MagicMock(status_code=404)
        )
        with patch("backend.annotation.mondo_hpo.httpx.Client", mock_client):
            assert check_mondo_hpo_update(reference_engine) is None

    def test_manifest_unreachable_returns_none(self, reference_engine):
        """Manifest fetch failure → None without attempting HEAD."""
        with patch(
            "backend.db.manifest.httpx.get",
            side_effect=httpx.ConnectError("nope"),
        ):
            assert check_mondo_hpo_update(reference_engine) is None

    def test_manifest_missing_pipeline_pin_returns_none(
        self, tmp_path: Path, monkeypatch, reference_engine
    ):
        """No ``pipeline_pins["mondo_hpo"]`` entry → None."""
        payload = json.loads(json.dumps(SAMPLE_MANIFEST))
        del payload["pipeline_pins"]["mondo_hpo"]
        path = _write_manifest(tmp_path, payload)
        monkeypatch.setenv(manifest_mod.MANIFEST_PATH_ENV, str(path))

        assert check_mondo_hpo_update(reference_engine) is None

    def test_manifest_pin_empty_url_returns_none(
        self, tmp_path: Path, monkeypatch, reference_engine
    ):
        """Pipeline pin with empty ``url`` is rejected by the manifest parser → None.

        The shared manifest parser requires a non-empty URL on every pipeline
        pin, so an empty URL surfaces as ``ManifestFetchError`` → ``None``.
        """
        payload = json.loads(json.dumps(SAMPLE_MANIFEST))
        payload["pipeline_pins"]["mondo_hpo"]["url"] = ""
        path = _write_manifest(tmp_path, payload)
        monkeypatch.setenv(manifest_mod.MANIFEST_PATH_ENV, str(path))

        assert check_mondo_hpo_update(reference_engine) is None

    def test_settings_argument_accepted_and_ignored(
        self, tmp_path: Path, monkeypatch, reference_engine
    ):
        """Signature parity: ``settings`` is accepted positionally / by keyword."""
        path = _write_manifest(tmp_path, SAMPLE_MANIFEST)
        monkeypatch.setenv(manifest_mod.MANIFEST_PATH_ENV, str(path))
        _record_version_row(
            reference_engine,
            "mondo_hpo",
            _current_mondo_hpo_version(),
        )

        mock_client, _ = _mock_head_client(last_modified=MONDO_HPO_LAST_MODIFIED_NEW)
        with patch("backend.annotation.mondo_hpo.httpx.Client", mock_client):
            assert check_mondo_hpo_update(reference_engine, None) is None
            assert check_mondo_hpo_update(reference_engine, settings=object()) is None


# ── CHECK_FNS registration ────────────────────────────────────────────


class TestCheckFnsRegistration:
    # gnomad's CHECK_FN is now check_gnomad_bundle_update (it ships as a bundle);
    # that binding + round-trip are covered in test_update_manager_bundles.py.
    def test_dbnsfp_registered(self):
        assert "dbnsfp" in CHECK_FNS
        assert CHECK_FNS["dbnsfp"] is check_dbnsfp_update

    def test_cpic_registered(self):
        assert "cpic" in CHECK_FNS
        assert CHECK_FNS["cpic"] is check_cpic_update

    def test_gwas_catalog_registered(self):
        assert "gwas_catalog" in CHECK_FNS
        assert CHECK_FNS["gwas_catalog"] is check_gwas_update

    def test_dbsnp_registered(self):
        assert "dbsnp" in CHECK_FNS
        assert CHECK_FNS["dbsnp"] is check_dbsnp_update

    def test_mondo_hpo_registered(self):
        assert "mondo_hpo" in CHECK_FNS
        assert CHECK_FNS["mondo_hpo"] is check_mondo_hpo_update

    def test_dbnsfp_dispatch_via_check_fns(self, tmp_path: Path, monkeypatch, reference_engine):
        """Same dispatch path, exercising the dbNSFP entry."""
        path = _write_manifest(tmp_path, SAMPLE_MANIFEST)
        monkeypatch.setenv(manifest_mod.MANIFEST_PATH_ENV, str(path))
        _record_version_row(reference_engine, "dbnsfp", "5.3.0")

        mock_client, _ = _mock_head_client(content_length="654321")
        with patch("backend.annotation.dbnsfp.httpx.Client", mock_client):
            result = CHECK_FNS["dbnsfp"](reference_engine, None)

        assert result is not None
        assert result.db_name == "dbnsfp"
        assert result.latest_version == "5.3.1a"
        assert result.download_size_bytes == 654_321

    def test_cpic_dispatch_via_check_fns(self, tmp_path: Path, monkeypatch, reference_engine):
        """Same dispatch path, exercising the CPIC entry."""
        path = _write_manifest(tmp_path, SAMPLE_MANIFEST)
        monkeypatch.setenv(manifest_mod.MANIFEST_PATH_ENV, str(path))
        _record_version_row(reference_engine, "cpic", "v1.29.0")

        mock_client, _ = _mock_get_client()
        with patch("backend.annotation.cpic.httpx.Client", mock_client):
            result = CHECK_FNS["cpic"](reference_engine, None)

        assert result is not None
        assert result.db_name == "cpic"
        assert result.latest_version == "v1.30.0"
        assert result.download_size_bytes == 12_345_678

    def test_gwas_catalog_dispatch_via_check_fns(
        self, tmp_path: Path, monkeypatch, reference_engine
    ):
        """Same dispatch path, exercising the GWAS Catalog entry."""
        path = _write_manifest(tmp_path, SAMPLE_MANIFEST)
        monkeypatch.setenv(manifest_mod.MANIFEST_PATH_ENV, str(path))
        _record_version_row(reference_engine, "gwas_catalog", GWAS_LAST_MODIFIED_OLD_VERSION)

        mock_client, _ = _mock_head_client(
            content_length="222333",
            last_modified=GWAS_LAST_MODIFIED_NEW,
        )
        with patch("backend.annotation.gwas.httpx.Client", mock_client):
            result = CHECK_FNS["gwas_catalog"](reference_engine, None)

        assert result is not None
        assert result.db_name == "gwas_catalog"
        assert result.latest_version == GWAS_LAST_MODIFIED_NEW_VERSION
        assert result.download_size_bytes == 222_333

    def test_dbsnp_dispatch_via_check_fns(self, tmp_path: Path, monkeypatch, reference_engine):
        """Same dispatch path, exercising the dbSNP entry."""
        path = _write_manifest(tmp_path, SAMPLE_MANIFEST)
        monkeypatch.setenv(manifest_mod.MANIFEST_PATH_ENV, str(path))
        _record_version_row(reference_engine, "dbsnp", DBSNP_LAST_MODIFIED_OLD_VERSION)

        mock_client, _ = _mock_head_client(
            content_length="444555",
            last_modified=DBSNP_LAST_MODIFIED_NEW,
        )
        with patch("backend.annotation.dbsnp.httpx.Client", mock_client):
            result = CHECK_FNS["dbsnp"](reference_engine, None)

        assert result is not None
        assert result.db_name == "dbsnp"
        assert result.latest_version == DBSNP_LAST_MODIFIED_NEW_VERSION
        assert result.download_size_bytes == 444_555

    def test_mondo_hpo_dispatch_via_check_fns(self, tmp_path: Path, monkeypatch, reference_engine):
        """Same dispatch path, exercising the MONDO/HPO entry."""
        path = _write_manifest(tmp_path, SAMPLE_MANIFEST)
        monkeypatch.setenv(manifest_mod.MANIFEST_PATH_ENV, str(path))
        _record_version_row(reference_engine, "mondo_hpo", MONDO_HPO_LAST_MODIFIED_OLD_VERSION)

        mock_client, _ = _mock_head_client(
            content_length="666777",
            last_modified=MONDO_HPO_LAST_MODIFIED_NEW,
        )
        with patch("backend.annotation.mondo_hpo.httpx.Client", mock_client):
            result = CHECK_FNS["mondo_hpo"](reference_engine, None)

        assert result is not None
        assert result.db_name == "mondo_hpo"
        assert result.latest_version == MONDO_HPO_LAST_MODIFIED_NEW_VERSION
        assert result.download_size_bytes == 2_000_331

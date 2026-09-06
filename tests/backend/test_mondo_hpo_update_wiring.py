"""End-to-end regression coverage for MONDO/HPO update URL wiring (#2315)."""

from __future__ import annotations

import asyncio
import errno
import gzip
import hashlib
import json
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import sqlalchemy as sa
from fastapi import HTTPException

from backend.annotation.mondo_hpo import (
    HPO_GENES_TO_PHENOTYPE_URL,
    MONDO_HPO_INGESTION_REVISION,
    MONDO_SSSOM_URL,
    download_and_load_mondo_hpo,
)
from backend.api.routes.updates import (
    TriggerUpdateRequest,
    check_for_updates,
    trigger_update,
)
from backend.config import Settings
from backend.db import manifest as manifest_mod
from backend.db.manifest import reset_cache
from backend.db.tables import database_versions, gene_phenotype
from backend.db.update_manager import (
    MondoHpoSourceBindingError,
    UpdateCheckResult,
    VersionInfo,
    _load_mondo_hpo_source_binding_key,
    capture_mondo_hpo_source_binding,
    check_mondo_hpo_update,
    decode_mondo_hpo_source_binding,
)
from backend.tasks.huey_tasks import _execute_database_update

PINNED_MONDO_URL = "https://updates.example.test/mondo/pinned-gene-disease.tsv.gz"
PINNED_LAST_MODIFIED = "Wed, 15 Apr 2026 12:34:56 GMT"
PINNED_VERSION = "20260415"
SOURCE_BINDING_KEY = b"source-binding-test-key-32-byte!"


def _manifest(path: Path, *, mondo_url: str = PINNED_MONDO_URL) -> Path:
    manifest_path = path / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "generated_at": "2026-08-12T00:00:00Z",
                "bundles": {},
                "pipeline_pins": {
                    "mondo_hpo": {
                        "url": mondo_url,
                        "last_known_version": "",
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    return manifest_path


def _head_response(url: str, **headers: str) -> MagicMock:
    response = MagicMock(headers=headers)
    response.url = url
    response.raise_for_status.return_value = None
    return response


def _head_client(
    *,
    cycles: int = 1,
    mondo_etag: str = '"mondo-pinned"',
    hpo_etag: str = '"hpo-pinned"',
    sssom_etag: str = '"sssom-pinned"',
    mondo_url: str = PINNED_MONDO_URL,
    hpo_url: str = HPO_GENES_TO_PHENOTYPE_URL,
    sssom_url: str = MONDO_SSSOM_URL,
) -> MagicMock:
    client = MagicMock()
    client.__enter__.return_value = client
    client.__exit__.return_value = False
    responses = [
        _head_response(
            mondo_url,
            **{
                "Content-Length": "101",
                "Last-Modified": PINNED_LAST_MODIFIED,
                "ETag": mondo_etag,
            },
        ),
        _head_response(hpo_url, **{"Content-Length": "103", "ETag": hpo_etag}),
        _head_response(sssom_url, **{"Content-Length": "107", "ETag": sssom_etag}),
    ]
    client.head.side_effect = responses * cycles
    return client


@pytest.mark.parametrize(
    ("changed_source", "changed_kwargs"),
    [
        ("MONDO", {"mondo_etag": '"mondo-changed"'}),
        ("HPO", {"hpo_etag": '"hpo-changed"'}),
        ("SSSOM", {"sssom_etag": '"sssom-changed"'}),
    ],
)
def test_source_binding_changes_with_each_checked_validator(
    changed_source: str,
    changed_kwargs: dict[str, str],
) -> None:
    """Every source validator participates in the opaque worker binding."""
    offered_client = _head_client()
    with patch(
        "backend.db.update_manager.httpx.Client",
        return_value=offered_client,
    ):
        offered_binding = capture_mondo_hpo_source_binding(
            PINNED_MONDO_URL, source_binding_key=SOURCE_BINDING_KEY
        )
    assert all(
        request.kwargs == {"headers": {"Accept-Encoding": "identity"}}
        for request in offered_client.head.call_args_list
    )
    assert PINNED_MONDO_URL not in offered_binding
    expectations = decode_mondo_hpo_source_binding(
        offered_binding,
        PINNED_MONDO_URL,
        source_binding_key=SOURCE_BINDING_KEY,
    )
    assert set(expectations) == {
        PINNED_MONDO_URL,
        HPO_GENES_TO_PHENOTYPE_URL,
        MONDO_SSSOM_URL,
    }
    assert expectations[PINNED_MONDO_URL].last_modified == PINNED_LAST_MODIFIED
    assert expectations[HPO_GENES_TO_PHENOTYPE_URL].last_modified is None
    assert expectations[MONDO_SSSOM_URL].last_modified is None
    same_origin_url = "https://operator:changed@updates.example.test/private/changed?token=x"
    with pytest.raises(MondoHpoSourceBindingError, match="manifest source changed"):
        decode_mondo_hpo_source_binding(
            offered_binding,
            same_origin_url,
            source_binding_key=SOURCE_BINDING_KEY,
        )
    with pytest.raises(MondoHpoSourceBindingError, match="manifest source changed"):
        decode_mondo_hpo_source_binding(
            offered_binding,
            "https://other.example.test/mondo/pinned-gene-disease.tsv.gz",
            source_binding_key=SOURCE_BINDING_KEY,
        )

    with patch(
        "backend.db.update_manager.httpx.Client",
        return_value=_head_client(**changed_kwargs),
    ):
        changed_binding = capture_mondo_hpo_source_binding(
            PINNED_MONDO_URL, source_binding_key=SOURCE_BINDING_KEY
        )

    assert changed_binding != offered_binding, changed_source


def test_source_binding_hides_credentials_without_an_offline_digest_oracle() -> None:
    """Durable queue state binds the path but cannot test guesses without the key."""
    first_url = "https://operator:first@updates.example.test/token/alpha/mondo.tsv?secret=one"
    second_url = "https://operator:second@updates.example.test/token/beta/mondo.tsv?secret=two"

    with patch(
        "backend.db.update_manager.httpx.Client",
        return_value=_head_client(mondo_url=first_url),
    ):
        first_binding = capture_mondo_hpo_source_binding(
            first_url, source_binding_key=SOURCE_BINDING_KEY
        )
    with patch(
        "backend.db.update_manager.httpx.Client",
        return_value=_head_client(mondo_url=second_url),
    ):
        second_binding = capture_mondo_hpo_source_binding(
            second_url, source_binding_key=SOURCE_BINDING_KEY
        )

    assert first_binding != second_binding
    assert "first" not in first_binding
    assert "alpha" not in first_binding
    assert "secret" not in first_binding
    unsalted_guess = hashlib.sha256(
        b"yeliztli-mondo-hpo-url-v1\0" + first_url.encode()
    ).hexdigest()
    assert unsalted_guess not in first_binding
    assert SOURCE_BINDING_KEY.hex() not in first_binding
    assert json.loads(first_binding)["schema"] == 3


def test_source_binding_key_is_private_stable_and_shared_by_api_worker(
    tmp_path: Path,
) -> None:
    """API and worker share one private key that is never serialized into Huey."""
    shared_data = tmp_path / "shared-data"
    api_settings = Settings(data_dir=shared_data)
    worker_settings = Settings(data_dir=shared_data)

    with patch(
        "backend.db.update_manager.os.link",
        side_effect=OSError(errno.EOPNOTSUPP, "hard links unavailable"),
    ):
        first = _load_mondo_hpo_source_binding_key(api_settings)
        second = _load_mondo_hpo_source_binding_key(worker_settings)
    key_path = shared_data / ".source-binding.key"
    lock_path = shared_data / ".source-binding.key.lock"

    assert first == second
    assert len(first) == 32
    assert key_path.read_bytes() == first
    assert key_path.stat().st_mode & 0o077 == 0
    assert lock_path.is_file()
    assert lock_path.stat().st_mode & 0o077 == 0


def test_source_binding_rejects_a_lock_readable_by_other_users(tmp_path: Path) -> None:
    """Another local user must not be able to open and indefinitely hold the lock."""
    shared_data = tmp_path / "shared-data"
    shared_data.mkdir()
    lock_path = shared_data / ".source-binding.key.lock"
    lock_path.touch(mode=0o644)
    lock_path.chmod(0o644)

    with pytest.raises(MondoHpoSourceBindingError, match="owner-controlled regular file"):
        _load_mondo_hpo_source_binding_key(Settings(data_dir=shared_data))


def test_source_binding_normalizes_an_unsupported_advisory_lock(tmp_path: Path) -> None:
    """A mount without advisory locks fails closed without leaking raw OS errors."""
    settings = Settings(data_dir=tmp_path / "shared-data")

    with (
        patch(
            "backend.db.update_manager.fcntl.flock",
            side_effect=OSError(errno.ENOTSUP, "advisory locks unavailable"),
        ) as flock,
        pytest.raises(MondoHpoSourceBindingError, match="key is unavailable"),
    ):
        _load_mondo_hpo_source_binding_key(settings)

    assert flock.call_count == 1


@pytest.mark.parametrize("bad_etag", ["", 'W/"weak"', "unquoted"])
def test_source_binding_requires_a_strong_quoted_etag(
    bad_etag: str,
) -> None:
    with (
        patch(
            "backend.db.update_manager.httpx.Client",
            return_value=_head_client(hpo_etag=bad_etag),
        ),
        pytest.raises(MondoHpoSourceBindingError, match="no strong ETag"),
    ):
        capture_mondo_hpo_source_binding(PINNED_MONDO_URL, source_binding_key=SOURCE_BINDING_KEY)


def test_checked_offer_uses_identity_encoded_snapshot_size() -> None:
    """The bound identity representation, not an encoded first pass, sizes the offer."""
    first_pass = VersionInfo(
        db_name="mondo_hpo",
        latest_version=PINNED_VERSION,
        download_url=PINNED_MONDO_URL,
        download_size_bytes=999,
        release_date=PINNED_VERSION,
    )
    snapshot_client = _head_client()

    with (
        patch("backend.db.update_manager._check_mondo_hpo_update", return_value=first_pass),
        patch("backend.db.update_manager.httpx.Client", return_value=snapshot_client),
    ):
        offered = check_mondo_hpo_update(MagicMock(), source_binding_key=SOURCE_BINDING_KEY)

    assert offered is first_pass
    assert offered.download_size_bytes == 101 + 103 + 107
    assert offered._source_binding is not None
    assert all(
        request.kwargs == {"headers": {"Accept-Encoding": "identity"}}
        for request in snapshot_client.head.call_args_list
    )


class _SourceResponse:
    def __init__(self, url: str, body: bytes, etag: str, *, status: int = 200) -> None:
        self.url = url
        self.status_code = status
        self.headers = httpx.Headers(
            {
                "Content-Length": str(len(body)),
                "ETag": etag,
                "Last-Modified": PINNED_LAST_MODIFIED,
            }
        )
        self._body = body

    def __enter__(self) -> _SourceResponse:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                "source request failed",
                request=httpx.Request("GET", self.url),
                response=httpx.Response(self.status_code),
            )

    def iter_raw(self, chunk_size: int) -> Iterator[bytes]:
        for offset in range(0, len(self._body), chunk_size):
            yield self._body[offset : offset + chunk_size]


class _PinnedSourceClient:
    def __init__(self, sources: dict[str, tuple[bytes, str]]) -> None:
        self.sources = sources
        self.get_requests: list[tuple[str, str | None]] = []

    def __enter__(self) -> _PinnedSourceClient:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def head(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
    ) -> _SourceResponse:
        if headers is not None:
            assert headers == {"Accept-Encoding": "identity"}
        body, etag = self.sources[url]
        return _SourceResponse(url, body, etag)

    def stream(self, method: str, url: str, *, headers: dict[str, str]) -> _SourceResponse:
        assert method == "GET"
        body, etag = self.sources[url]
        if_match = headers.get("If-Match")
        self.get_requests.append((url, if_match))
        status = 200 if if_match == etag else 412
        return _SourceResponse(url, body, etag, status=status)


def _pinned_source_payloads() -> dict[str, tuple[bytes, str]]:
    mondo_text = (
        "subject\tsubject_label\tpredicate\tobject\tobject_label\tqualifier\n"
        "HGNC:1100\tBRCA1\tbiolink:gene_associated_with_condition\t"
        "MONDO:0011450\tHereditary breast cancer\t\n"
    )
    hpo_text = (
        "ncbi_gene_id\tgene_symbol\thpo_id\thpo_name\tfrequency\tdisease_id\n"
        "672\tBRCA1\tHP:0003002\tBreast carcinoma\t\tOMIM:604370\n"
    )
    sssom_text = (
        "subject_id\tsubject_label\tpredicate_id\tobject_id\tobject_label\t"
        "mapping_justification\n"
        "MONDO:0011450\tHereditary breast cancer\tskos:exactMatch\tOMIM:604370\t"
        "Breast-ovarian cancer\tsemapv:UnspecifiedMatching\n"
    )
    return {
        PINNED_MONDO_URL: (gzip.compress(mondo_text.encode(), mtime=0), '"mondo-pinned"'),
        HPO_GENES_TO_PHENOTYPE_URL: (hpo_text.encode(), '"hpo-pinned"'),
        MONDO_SSSOM_URL: (sssom_text.encode(), '"sssom-pinned"'),
    }


def test_pinned_offer_is_installed_and_not_offered_again(
    reference_engine: sa.Engine,
    tmp_path: Path,
    monkeypatch,
) -> None:
    """A divergent manifest pin controls both approval and the resulting install."""
    manifest_path = _manifest(tmp_path)
    monkeypatch.setenv(manifest_mod.MANIFEST_PATH_ENV, str(manifest_path))
    reset_cache()
    monkeypatch.setattr("backend.annotation.mondo_hpo.MINIMUM_MONDO_GENE_DISEASE_ARCHIVE_BYTES", 1)
    monkeypatch.setattr("backend.annotation.mondo_hpo.MINIMUM_HPO_GENES_TO_PHENOTYPE_BYTES", 1)
    monkeypatch.setattr("backend.annotation.mondo_hpo.MINIMUM_UNAMBIGUOUS_MONDO_XREFS", 1)

    source_client = _PinnedSourceClient(_pinned_source_payloads())
    with patch("backend.annotation.mondo_hpo.httpx.Client", return_value=source_client):
        offered = check_mondo_hpo_update(reference_engine, source_binding_key=SOURCE_BINDING_KEY)

    assert offered is not None
    assert offered.download_url == PINNED_MONDO_URL
    assert offered.latest_version == PINNED_VERSION
    assert offered._source_binding is not None

    settings = Settings(data_dir=tmp_path / "data", wal_mode=False)
    settings.downloads_dir.mkdir(parents=True, mode=0o700)
    registry = SimpleNamespace(reference_engine=reference_engine, settings=settings)

    with (
        patch("backend.api.routes.updates.get_registry", return_value=registry),
        patch("backend.api.routes.updates.is_cross_process_build_claimed", return_value=False),
        patch(
            "backend.api.routes.updates.check_mondo_hpo_update",
        ) as recheck,
        patch(
            "backend.api.routes.updates.asyncio.to_thread",
            new=AsyncMock(return_value=offered),
        ) as to_thread,
        patch(
            "backend.tasks.huey_tasks.create_database_update_job",
            return_value="job-mondo-hpo",
        ),
        patch("backend.tasks.huey_tasks.run_database_update_task") as queued_task,
    ):
        response = asyncio.run(trigger_update(TriggerUpdateRequest(db_name="mondo_hpo")))

    assert response.job_id == "job-mondo-hpo"
    to_thread.assert_awaited_once_with(recheck, reference_engine, settings=settings)
    recheck.assert_not_called()
    queued_task.assert_called_once_with(
        "job-mondo-hpo",
        "mondo_hpo",
        offered._source_binding,
    )
    assert offered.download_url not in repr(queued_task.call_args)

    with (
        patch("backend.db.connection.get_registry", return_value=registry),
        patch(
            "backend.db.database_registry.get_build_fn", return_value=download_and_load_mondo_hpo
        ),
        patch("backend.annotation.mondo_hpo.httpx.Client", return_value=source_client),
        patch(
            "backend.db.update_manager._load_mondo_hpo_source_binding_key",
            return_value=SOURCE_BINDING_KEY,
        ),
        patch("backend.db.update_manager.run_precheck_all_samples"),
        patch("backend.tasks.huey_tasks._update_job"),
    ):
        _execute_database_update(*queued_task.call_args.args)

    with reference_engine.connect() as connection:
        installed_version = connection.execute(
            sa.select(database_versions.c.version).where(
                database_versions.c.db_name == "mondo_hpo"
            )
        ).scalar_one()
        installed_row = connection.execute(
            sa.select(gene_phenotype).where(gene_phenotype.c.gene_symbol == "BRCA1")
        ).one()
    assert installed_version.startswith(f"{offered.latest_version}+{MONDO_HPO_INGESTION_REVISION}")
    assert installed_row.disease_id == "MONDO:0011450"
    assert "HP:0003002" in installed_row.hpo_terms
    assert source_client.get_requests == [
        (PINNED_MONDO_URL, '"mondo-pinned"'),
        (HPO_GENES_TO_PHENOTYPE_URL, '"hpo-pinned"'),
        (MONDO_SSSOM_URL, '"sssom-pinned"'),
    ]

    with patch("backend.annotation.mondo_hpo.httpx.Client", return_value=source_client):
        assert (
            check_mondo_hpo_update(
                reference_engine,
                source_binding_key=SOURCE_BINDING_KEY,
            )
            is None
        )

    reset_cache()


def test_manual_trigger_refuses_when_recheck_has_no_offer(tmp_path: Path) -> None:
    """A stale dashboard offer cannot queue an unapproved MONDO/HPO artifact."""
    settings = Settings(data_dir=tmp_path / "data", wal_mode=False)
    registry = SimpleNamespace(reference_engine=MagicMock(), settings=settings)

    with (
        patch("backend.api.routes.updates.get_registry", return_value=registry),
        patch("backend.api.routes.updates.check_mondo_hpo_update") as recheck,
        patch(
            "backend.api.routes.updates.asyncio.to_thread",
            new=AsyncMock(return_value=None),
        ) as to_thread,
        patch("backend.tasks.huey_tasks.create_database_update_job") as create_job,
        patch("backend.tasks.huey_tasks.run_database_update_task") as queued_task,
    ):
        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(trigger_update(TriggerUpdateRequest(db_name="mondo_hpo")))

    assert exc_info.value.status_code == 409
    assert "currently available" in str(exc_info.value.detail)
    to_thread.assert_awaited_once_with(
        recheck,
        registry.reference_engine,
        settings=settings,
    )
    recheck.assert_not_called()
    create_job.assert_not_called()
    queued_task.assert_not_called()


def test_dashboard_check_offloads_blocking_source_validation(tmp_path: Path) -> None:
    """The async API does not run the multi-source HEAD checks on its event loop."""
    settings = Settings(
        data_dir=tmp_path / "data",
        wal_mode=False,
        update_check_interval="daily",
    )
    registry = SimpleNamespace(reference_engine=MagicMock(), settings=settings)
    result = UpdateCheckResult(up_to_date=["mondo_hpo"])

    with (
        patch("backend.api.routes.updates.get_registry", return_value=registry),
        patch("backend.api.routes.updates.check_all_updates") as check_all,
        patch(
            "backend.api.routes.updates.asyncio.to_thread",
            new=AsyncMock(return_value=result),
        ) as to_thread,
    ):
        response = asyncio.run(check_for_updates())

    to_thread.assert_awaited_once_with(
        check_all,
        registry.reference_engine,
        settings=settings,
    )
    check_all.assert_not_called()
    assert response.up_to_date == ["mondo_hpo"]

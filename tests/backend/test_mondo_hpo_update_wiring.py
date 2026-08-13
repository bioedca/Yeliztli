"""End-to-end regression coverage for MONDO/HPO update URL wiring (#2315)."""

from __future__ import annotations

import asyncio
import gzip
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import sqlalchemy as sa
from fastapi import HTTPException

from backend.annotation.mondo_hpo import (
    HPO_GENES_TO_PHENOTYPE_URL,
    MONDO_HPO_INGESTION_REVISION,
    MONDO_SSSOM_URL,
    check_mondo_hpo_update,
    download_and_load_mondo_hpo,
)
from backend.api.routes.updates import TriggerUpdateRequest, trigger_update
from backend.config import Settings
from backend.db import manifest as manifest_mod
from backend.db.manifest import reset_cache
from backend.db.tables import database_versions
from backend.db.update_manager import bind_source_url
from backend.tasks.huey_tasks import _execute_database_update

PINNED_MONDO_URL = "https://updates.example.test/mondo/pinned-gene-disease.tsv.gz"
PINNED_LAST_MODIFIED = "Wed, 15 Apr 2026 12:34:56 GMT"
PINNED_VERSION = "20260415"


def _manifest(path: Path) -> Path:
    manifest_path = path / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "generated_at": "2026-08-12T00:00:00Z",
                "bundles": {},
                "pipeline_pins": {
                    "mondo_hpo": {
                        "url": PINNED_MONDO_URL,
                        "last_known_version": "",
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    return manifest_path


def _head_response(**headers: str) -> MagicMock:
    response = MagicMock(headers=headers)
    response.raise_for_status.return_value = None
    return response


def _head_client() -> MagicMock:
    client = MagicMock()
    client.__enter__.return_value = client
    client.__exit__.return_value = False
    client.head.side_effect = [
        _head_response(**{"Content-Length": "101", "Last-Modified": PINNED_LAST_MODIFIED}),
        _head_response(**{"Content-Length": "103", "ETag": '"hpo-pinned"'}),
        _head_response(**{"Content-Length": "107", "ETag": '"sssom-pinned"'}),
    ]
    return client


def _write_pinned_sources(url: str, dest_dir: Path, filename: str, **kwargs: object) -> Path:
    target = dest_dir / filename
    target.parent.mkdir(parents=True, exist_ok=True)
    metadata = kwargs.get("meta")

    if filename == "gene_disease.9606.tsv.gz":
        assert url == PINNED_MONDO_URL
        content = (
            "subject\tsubject_label\tpredicate\tobject\tobject_label\tqualifier\n"
            "HGNC:1100\tBRCA1\tbiolink:gene_associated_with_condition\t"
            "MONDO:0011450\tHereditary breast cancer\t\n"
        )
        with gzip.open(target, "wt", encoding="utf-8") as handle:
            handle.write(content)
        assert isinstance(metadata, dict)
        metadata.update(
            {
                "etag": '"mondo-pinned"',
                "last_modified": PINNED_LAST_MODIFIED,
                "version": PINNED_VERSION,
            }
        )
    elif filename == "genes_to_phenotype.txt":
        assert url == HPO_GENES_TO_PHENOTYPE_URL
        target.write_text(
            "ncbi_gene_id\tgene_symbol\thpo_id\thpo_name\tfrequency\tdisease_id\n"
            "672\tBRCA1\tHP:0003002\tBreast carcinoma\t\tOMIM:604370\n",
            encoding="utf-8",
        )
        assert isinstance(metadata, dict)
        metadata.update({"etag": '"hpo-pinned"', "version": PINNED_VERSION})
    else:
        assert filename == "mondo.sssom.tsv"
        assert url == MONDO_SSSOM_URL
        target.write_text(
            "subject_id\tsubject_label\tpredicate_id\tobject_id\tobject_label\t"
            "mapping_justification\n"
            "MONDO:0011450\tHereditary breast cancer\tskos:exactMatch\tOMIM:604370\t"
            "Breast-ovarian cancer\tsemapv:UnspecifiedMatching\n",
            encoding="utf-8",
        )
        assert isinstance(metadata, dict)
        metadata.update({"etag": '"sssom-pinned"', "version": PINNED_VERSION})
    return target


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

    check_client = _head_client()
    with patch("backend.annotation.mondo_hpo.httpx.Client", return_value=check_client):
        offered = check_mondo_hpo_update(reference_engine)

    assert offered is not None
    assert offered.download_url == PINNED_MONDO_URL
    assert offered.latest_version == PINNED_VERSION

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
        bind_source_url(offered.download_url),
    )
    assert offered.download_url not in repr(queued_task.call_args)

    with (
        patch("backend.db.connection.get_registry", return_value=registry),
        patch(
            "backend.db.database_registry.get_build_fn", return_value=download_and_load_mondo_hpo
        ),
        patch("backend.annotation.mondo_hpo.download_file", side_effect=_write_pinned_sources),
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
    assert installed_version.startswith(f"{offered.latest_version}+{MONDO_HPO_INGESTION_REVISION}")

    recheck_client = _head_client()
    with patch("backend.annotation.mondo_hpo.httpx.Client", return_value=recheck_client):
        assert check_mondo_hpo_update(reference_engine) is None

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

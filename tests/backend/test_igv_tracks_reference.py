"""Tests for Genome Browser local reference endpoint handlers."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import HTTPException
from fastapi.responses import FileResponse

from backend.api.routes.igv_tracks import (
    genome_browser_reference_fasta,
    genome_browser_reference_fasta_index,
    genome_browser_reference_refseq_track,
    genome_browser_reference_status,
)
from backend.config import Settings


def _write_local_reference_bundle(data_dir: Path) -> None:
    (data_dir / "grch37.fa").write_text(">chr1\nACGTACGT\n", encoding="ascii")
    (data_dir / "grch37.fa.fai").write_text("chr1\t8\t6\t8\t9\n", encoding="ascii")
    (data_dir / "grch37_refseq.bed").write_text(
        "chr1\t0\t8\tGENE1\t0\t+\t0\t8\t0\t1\t8,\t0,\n",
        encoding="ascii",
    )


@pytest.fixture
def patch_route_settings(tmp_path: Path):
    settings = Settings(data_dir=tmp_path, wal_mode=False)
    with patch("backend.api.routes.igv_tracks.get_settings", return_value=settings):
        yield settings


async def test_reference_status_reports_remote_fallback_when_bundle_missing(
    patch_route_settings: Settings,
) -> None:
    status = await genome_browser_reference_status()

    payload = status.model_dump()
    assert payload["available"] is False
    assert payload["mode"] == "remote"
    assert payload["reference"] is None
    assert payload["tracks"] == []
    assert "GRCh37 FASTA (grch37.fa)" in payload["missing"]
    assert "RefSeq BED track (grch37_refseq.bed)" in payload["missing"]


async def test_reference_status_returns_igv_local_reference_config(
    patch_route_settings: Settings,
) -> None:
    _write_local_reference_bundle(patch_route_settings.data_dir)

    status = await genome_browser_reference_status()

    payload = status.model_dump()
    assert payload["available"] is True
    assert payload["mode"] == "local"
    assert payload["missing"] == []
    assert payload["reference"] == {
        "id": "hg19-local",
        "name": "GRCh37/hg19 (local)",
        "fastaURL": "/api/igv-tracks/reference/fasta",
        "indexURL": "/api/igv-tracks/reference/fasta.fai",
    }
    assert payload["tracks"][0]["name"] == "RefSeq Genes"
    assert payload["tracks"][0]["format"] == "bed"
    assert payload["tracks"][0]["url"] == "/api/igv-tracks/reference/refseq.bed"


async def test_reference_file_handlers_return_file_responses(
    patch_route_settings: Settings,
) -> None:
    data_dir = patch_route_settings.data_dir
    _write_local_reference_bundle(data_dir)

    fasta = await genome_browser_reference_fasta()
    fai = await genome_browser_reference_fasta_index()
    refseq = await genome_browser_reference_refseq_track()

    assert isinstance(fasta, FileResponse)
    assert Path(fasta.path) == data_dir / "grch37.fa"
    assert isinstance(fai, FileResponse)
    assert Path(fai.path) == data_dir / "grch37.fa.fai"
    assert isinstance(refseq, FileResponse)
    assert Path(refseq.path) == data_dir / "grch37_refseq.bed"


async def test_reference_file_handlers_404_when_bundle_missing(
    patch_route_settings: Settings,
) -> None:
    with pytest.raises(HTTPException) as exc:
        await genome_browser_reference_fasta()

    assert exc.value.status_code == 404
    assert exc.value.detail == "GRCh37 FASTA is not installed."

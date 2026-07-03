"""Tests for Genome Browser local reference endpoint handlers."""

from __future__ import annotations

import json
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

UCSC_HG19_FASTA_URL = "https://hgdownload.soe.ucsc.edu/goldenPath/hg19/bigZips/hg19.fa.gz"
UCSC_HG19_REFGENE_URL = "https://hgdownload.soe.ucsc.edu/goldenPath/hg19/database/refGene.txt.gz"


def _write_toy_reference_bundle(data_dir: Path) -> None:
    (data_dir / "grch37.fa").write_text(">chr1\nACGTACGT\n", encoding="ascii")
    (data_dir / "grch37.fa.fai").write_text("chr1\t8\t6\t8\t9\n", encoding="ascii")
    (data_dir / "grch37_refseq.bed").write_text(
        "chr1\t0\t8\tGENE1\t0\t+\t0\t8\t0\t1\t8,\t0,\n",
        encoding="ascii",
    )


def _write_local_reference_bundle(data_dir: Path) -> None:
    (data_dir / "grch37.fa").write_text(">chr1\nACGTACGT\n", encoding="ascii")
    (data_dir / "grch37.fa.fai").write_text(
        "\n".join(
            [
                "chr1\t249250621\t6\t50\t51",
                "chr2\t243199373\t6\t50\t51",
                "chr10\t135534747\t6\t50\t51",
                "chrX\t155270560\t6\t50\t51",
                "chrY\t59373566\t6\t50\t51",
                "chrM\t16571\t6\t50\t51",
            ]
        )
        + "\n",
        encoding="ascii",
    )
    (data_dir / "grch37_refseq.bed").write_text(
        "chr1\t11873\t14409\tDDX11L1\t0\t+\t11873\t11873\t51,65,85\t3\t354,109,1189\t0,739,1347\n",
        encoding="ascii",
    )
    (data_dir / "genome_browser_reference_manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "name": "genome_browser_reference_grch37_hg19",
                "runtime_files": ["grch37.fa", "grch37.fa.fai", "grch37_refseq.bed"],
                "sources": {
                    "fasta": {"url": UCSC_HG19_FASTA_URL},
                    "refgene": {"url": UCSC_HG19_REFGENE_URL},
                },
                "outputs": {
                    "fasta": {"path": "grch37.fa"},
                    "fasta_index": {"path": "grch37.fa.fai"},
                    "refseq_bed": {"path": "grch37_refseq.bed"},
                },
            }
        )
        + "\n",
        encoding="utf-8",
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


async def test_reference_status_rejects_toy_reference_without_manifest(
    patch_route_settings: Settings,
) -> None:
    _write_toy_reference_bundle(patch_route_settings.data_dir)

    status = await genome_browser_reference_status()

    payload = status.model_dump()
    assert payload["available"] is False
    assert payload["mode"] == "remote"
    assert payload["reference"] is None
    assert any("GRCh37 reference manifest" in item for item in payload["missing"])
    assert any("sentinel contig lengths" in item for item in payload["missing"])


async def test_reference_status_rejects_wrong_assembly_sentinel_lengths(
    patch_route_settings: Settings,
) -> None:
    _write_local_reference_bundle(patch_route_settings.data_dir)
    (patch_route_settings.data_dir / "grch37.fa.fai").write_text(
        "\n".join(
            [
                "chr1\t248956422\t6\t50\t51",
                "chr2\t242193529\t6\t50\t51",
                "chr10\t133797422\t6\t50\t51",
                "chrX\t156040895\t6\t50\t51",
                "chrY\t57227415\t6\t50\t51",
                "chrM\t16569\t6\t50\t51",
            ]
        )
        + "\n",
        encoding="ascii",
    )

    status = await genome_browser_reference_status()

    payload = status.model_dump()
    assert payload["available"] is False
    assert payload["mode"] == "remote"
    assert any("sentinel contig lengths" in item for item in payload["missing"])


async def test_reference_status_rejects_malformed_manifest_runtime_files(
    patch_route_settings: Settings,
) -> None:
    _write_local_reference_bundle(patch_route_settings.data_dir)
    manifest_path = patch_route_settings.data_dir / "genome_browser_reference_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["runtime_files"] = ["grch37.fa", None, "grch37_refseq.bed"]
    manifest_path.write_text(json.dumps(manifest) + "\n", encoding="utf-8")

    status = await genome_browser_reference_status()

    payload = status.model_dump()
    assert payload["available"] is False
    assert payload["mode"] == "remote"
    assert any("runtime_files must list" in item for item in payload["missing"])


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


async def test_reference_file_handlers_404_when_bundle_invalid(
    patch_route_settings: Settings,
) -> None:
    _write_toy_reference_bundle(patch_route_settings.data_dir)

    with pytest.raises(HTTPException) as exc:
        await genome_browser_reference_fasta()

    assert exc.value.status_code == 404
    assert exc.value.detail == "GRCh37 FASTA is not installed."

"""Tests for the Genome Browser reference bundle builder."""

from __future__ import annotations

import gzip
import json
import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "scripts"))
from build_genome_browser_reference import (
    BED_RGB,
    SourceConfig,
    build_reference_bundle,
    convert_refgene_to_bed,
    write_fasta_and_fai,
)


def _write_gzip(path: Path, text: str) -> None:
    with gzip.open(path, "wt", encoding="utf-8", newline="\n") as handle:
        handle.write(text)


def test_write_fasta_and_fai_from_ucsc_style_gzip(tmp_path: Path) -> None:
    source = tmp_path / "hg19.fa.gz"
    fasta = tmp_path / "grch37.fa"
    fai = tmp_path / "grch37.fa.fai"
    _write_gzip(source, ">chr1\nACGT\nTGCA\n>chr2 description\nNN\n")

    stats = write_fasta_and_fai(source, fasta, fai)

    assert stats.sequences == 2
    assert stats.bases == 10
    assert fasta.read_text(encoding="ascii") == ">chr1\nACGT\nTGCA\n>chr2 description\nNN\n"
    assert fai.read_text(encoding="ascii").splitlines() == [
        "chr1\t8\t6\t4\t5",
        "chr2\t2\t34\t2\t3",
    ]


def test_convert_refgene_to_bed12(tmp_path: Path) -> None:
    source = tmp_path / "refGene.txt.gz"
    bed = tmp_path / "grch37_refseq.bed"
    _write_gzip(
        source,
        "585\tNM_000000\tchr1\t+\t10\t30\t12\t25\t2\t10,20,\t15,30,\t0\tGENE1\tcmpl\tcmpl\t0,1,\n",
    )

    stats = convert_refgene_to_bed(source, bed)

    assert stats.transcripts == 1
    assert stats.genes == 1
    assert stats.skipped_malformed == 0
    assert bed.read_text(encoding="utf-8").strip() == "\t".join(
        [
            "chr1",
            "10",
            "30",
            "GENE1",
            "0",
            "+",
            "12",
            "25",
            BED_RGB,
            "2",
            "5,10",
            "0,10",
        ]
    )


def test_build_reference_bundle_writes_manifest_and_runtime_files(tmp_path: Path) -> None:
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    fasta_source = source_dir / "hg19.fa.gz"
    refgene_source = source_dir / "refGene.txt.gz"
    _write_gzip(fasta_source, ">chr1\nACGT\n")
    _write_gzip(
        refgene_source,
        "585\tNM_000001\tchr1\t-\t0\t4\t0\t4\t1\t0,\t4,\t0\tGENE2\tcmpl\tcmpl\t0,\n",
    )

    output_dir = tmp_path / "bundle"
    download_cache = tmp_path / "download-cache"
    download_cache.mkdir()
    cache_marker = download_cache / "keep-me.txt"
    cache_marker.write_text("operator cache metadata", encoding="utf-8")
    result = build_reference_bundle(
        output_dir,
        source_dir=download_cache,
        source_config=SourceConfig(
            fasta_url=fasta_source.as_uri(),
            refgene_url=refgene_source.as_uri(),
        ),
        accessed_date=date(2026, 7, 1),
    )

    assert (output_dir / "grch37.fa").is_file()
    assert (output_dir / "grch37.fa.fai").is_file()
    assert (output_dir / "grch37_refseq.bed").is_file()
    assert cache_marker.read_text(encoding="utf-8") == "operator cache metadata"
    assert not (download_cache / "hg19.fa.gz").exists()
    assert not (download_cache / "refGene.txt.gz").exists()
    assert result.fasta_stats.sequences == 1
    assert result.refgene_stats.transcripts == 1

    manifest = json.loads((output_dir / "genome_browser_reference_manifest.json").read_text())
    assert manifest["accessed_date"] == "2026-07-01"
    assert manifest["sources"]["fasta"]["url"] == fasta_source.as_uri()
    assert manifest["sources"]["refgene"]["url"] == refgene_source.as_uri()
    assert "directory_url" not in manifest["sources"]["fasta"]
    assert "directory_url" not in manifest["sources"]["refgene"]
    assert "url" not in manifest["license"]
    assert manifest["license"]["label"] == "Custom source license not recorded"
    assert "Custom source URLs were provided" in manifest["license"]["summary"]
    assert "UCSC" not in manifest["license"]["summary"]
    assert manifest["outputs"]["fasta"]["path"] == "grch37.fa"
    assert manifest["outputs"]["fasta_index"]["path"] == "grch37.fa.fai"
    assert manifest["outputs"]["refseq_bed"]["path"] == "grch37_refseq.bed"


def test_build_reference_bundle_rejects_overlapping_source_and_output_dirs(
    tmp_path: Path,
) -> None:
    output_dir = tmp_path / "bundle"

    with pytest.raises(ValueError, match="--source-dir must not overlap --output-dir"):
        build_reference_bundle(output_dir, source_dir=output_dir / "_sources")

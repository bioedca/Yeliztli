#!/usr/bin/env python3
"""Build the local Genome Browser GRCh37 FASTA + RefSeq track bundle.

The app serves three files when the optional local browser reference is
installed in the Yeliztli data directory:

* ``grch37.fa``
* ``grch37.fa.fai``
* ``grch37_refseq.bed``

This script downloads the UCSC hg19 FASTA and ``refGene`` table, writes those
runtime files, and records source/output checksums in a manifest. It uses only
the Python standard library so it can run inside a minimal SLURM job.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import shutil
import sys
import urllib.request
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import BinaryIO

DEFAULT_FASTA_URL = "https://hgdownload.soe.ucsc.edu/goldenPath/hg19/bigZips/hg19.fa.gz"
DEFAULT_REFGENE_URL = "https://hgdownload.soe.ucsc.edu/goldenPath/hg19/database/refGene.txt.gz"
DEFAULT_LICENSE_URL = "https://genome.ucsc.edu/license/"
DEFAULT_BIGZIPS_URL = "https://hgdownload.soe.ucsc.edu/goldenPath/hg19/bigZips/"
DEFAULT_DATABASE_URL = "https://hgdownload.soe.ucsc.edu/goldenPath/hg19/database/"

FASTA_OUTPUT_NAME = "grch37.fa"
REFSEQ_OUTPUT_NAME = "grch37_refseq.bed"
MANIFEST_OUTPUT_NAME = "genome_browser_reference_manifest.json"
BED_RGB = "51,65,85"


@dataclass(frozen=True)
class FileInfo:
    path: str
    sha256: str
    size_bytes: int


@dataclass(frozen=True)
class FastaStats:
    sequences: int
    bases: int


@dataclass(frozen=True)
class RefGeneStats:
    transcripts: int
    genes: int
    skipped_malformed: int


@dataclass(frozen=True)
class SourceConfig:
    fasta_url: str = DEFAULT_FASTA_URL
    refgene_url: str = DEFAULT_REFGENE_URL
    license_url: str | None = DEFAULT_LICENSE_URL
    bigzips_url: str | None = DEFAULT_BIGZIPS_URL
    database_url: str | None = DEFAULT_DATABASE_URL


@dataclass(frozen=True)
class BuildResult:
    output_dir: Path
    fasta: FileInfo
    fasta_index: FileInfo
    refseq_bed: FileInfo
    manifest: FileInfo
    fasta_stats: FastaStats
    refgene_stats: RefGeneStats


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def file_info(path: Path, *, relative_to: Path | None = None) -> FileInfo:
    display_path = path.relative_to(relative_to).as_posix() if relative_to else path.as_posix()
    return FileInfo(
        path=display_path,
        sha256=sha256_file(path),
        size_bytes=path.stat().st_size,
    )


def download_file(url: str, destination: Path, *, force: bool = False) -> FileInfo:
    if destination.exists() and not force:
        return file_info(destination)

    destination.parent.mkdir(parents=True, exist_ok=True)
    tmp_destination = destination.with_suffix(destination.suffix + ".tmp")
    digest = hashlib.sha256()
    size_bytes = 0

    with urllib.request.urlopen(url) as response, tmp_destination.open("wb") as output:
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            output.write(chunk)
            digest.update(chunk)
            size_bytes += len(chunk)

    tmp_destination.replace(destination)
    return FileInfo(
        path=destination.as_posix(),
        sha256=digest.hexdigest(),
        size_bytes=size_bytes,
    )


def _fasta_name(header_line: bytes) -> str:
    name = header_line[1:].strip().split(maxsplit=1)[0]
    if not name:
        raise ValueError("FASTA header is missing a sequence name")
    return name.decode("ascii")


def _finish_fasta_record(
    records: list[tuple[str, int, int, int, int]],
    name: str | None,
    length: int,
    base_offset: int | None,
    line_bases: int,
    line_width: int,
) -> None:
    if name is None:
        return
    if base_offset is None or line_bases <= 0 or line_width <= 0:
        raise ValueError(f"FASTA sequence {name!r} has no bases")
    records.append((name, length, base_offset, line_bases, line_width))


def _write_fasta_and_fai_from_stream(
    source: BinaryIO, fasta_path: Path, fai_path: Path
) -> FastaStats:
    records: list[tuple[str, int, int, int, int]] = []
    current_name: str | None = None
    current_length = 0
    current_base_offset: int | None = None
    current_line_bases = 0
    current_line_width = 0
    saw_terminal_short_line = False
    output_offset = 0

    fasta_path.parent.mkdir(parents=True, exist_ok=True)
    with fasta_path.open("wb") as output:
        for line in source:
            line_offset = output_offset
            output.write(line)
            output_offset += len(line)

            if line.startswith(b">"):
                _finish_fasta_record(
                    records,
                    current_name,
                    current_length,
                    current_base_offset,
                    current_line_bases,
                    current_line_width,
                )
                current_name = _fasta_name(line)
                current_length = 0
                current_base_offset = None
                current_line_bases = 0
                current_line_width = 0
                saw_terminal_short_line = False
                continue

            if current_name is None:
                raise ValueError("FASTA sequence data appeared before the first header")

            bases = line.rstrip(b"\r\n")
            if not bases:
                continue
            if current_base_offset is None:
                current_base_offset = line_offset

            bases_on_line = len(bases)
            if current_line_bases == 0:
                current_line_bases = bases_on_line
                current_line_width = len(line)
            elif bases_on_line != current_line_bases:
                if bases_on_line > current_line_bases or saw_terminal_short_line:
                    raise ValueError(
                        f"FASTA sequence {current_name!r} has inconsistent line lengths"
                    )
                saw_terminal_short_line = True
            current_length += bases_on_line

    _finish_fasta_record(
        records,
        current_name,
        current_length,
        current_base_offset,
        current_line_bases,
        current_line_width,
    )

    if not records:
        raise ValueError("FASTA source contained no sequences")

    with fai_path.open("w", encoding="ascii", newline="\n") as fai:
        for name, length, offset, line_bases, line_width in records:
            fai.write(f"{name}\t{length}\t{offset}\t{line_bases}\t{line_width}\n")

    return FastaStats(
        sequences=len(records),
        bases=sum(record[1] for record in records),
    )


def write_fasta_and_fai(fasta_gz_path: Path, fasta_path: Path, fai_path: Path) -> FastaStats:
    with gzip.open(fasta_gz_path, "rb") as source:
        return _write_fasta_and_fai_from_stream(source, fasta_path, fai_path)


def _split_int_list(value: str) -> list[int]:
    return [int(item) for item in value.rstrip(",").split(",") if item]


def _parse_refgene_row(
    fields: list[str],
) -> tuple[str, str, str, int, int, int, int, list[int], list[int], str]:
    if len(fields) >= 16 and fields[3] in {"+", "-"}:
        offset = 1
    elif len(fields) >= 15 and fields[2] in {"+", "-"}:
        offset = 0
    else:
        raise ValueError("row does not match refGene genePred columns")

    accession = fields[offset]
    chrom = fields[offset + 1]
    strand = fields[offset + 2]
    tx_start = int(fields[offset + 3])
    tx_end = int(fields[offset + 4])
    cds_start = int(fields[offset + 5])
    cds_end = int(fields[offset + 6])
    exon_count = int(fields[offset + 7])
    exon_starts = _split_int_list(fields[offset + 8])
    exon_ends = _split_int_list(fields[offset + 9])
    gene_symbol = fields[offset + 11] or accession

    if len(exon_starts) != exon_count or len(exon_ends) != exon_count:
        raise ValueError("exon count does not match exon starts/ends")

    return (
        accession,
        chrom,
        strand,
        tx_start,
        tx_end,
        cds_start,
        cds_end,
        exon_starts,
        exon_ends,
        gene_symbol,
    )


def convert_refgene_to_bed(refgene_gz_path: Path, bed_path: Path) -> RefGeneStats:
    transcripts = 0
    skipped_malformed = 0
    genes: set[str] = set()

    bed_path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(refgene_gz_path, "rt", encoding="utf-8", newline="") as source:
        with bed_path.open("w", encoding="utf-8", newline="\n") as bed:
            for line in source:
                if not line.strip() or line.startswith("#"):
                    continue
                fields = line.rstrip("\n").split("\t")
                try:
                    (
                        _accession,
                        chrom,
                        strand,
                        tx_start,
                        tx_end,
                        cds_start,
                        cds_end,
                        exon_starts,
                        exon_ends,
                        gene_symbol,
                    ) = _parse_refgene_row(fields)
                except (IndexError, TypeError, ValueError):
                    skipped_malformed += 1
                    continue

                block_sizes = [
                    end - start for start, end in zip(exon_starts, exon_ends, strict=True)
                ]
                block_starts = [start - tx_start for start in exon_starts]
                if any(size <= 0 for size in block_sizes) or any(
                    start < 0 for start in block_starts
                ):
                    skipped_malformed += 1
                    continue

                bed.write(
                    "\t".join(
                        [
                            chrom,
                            str(tx_start),
                            str(tx_end),
                            gene_symbol,
                            "0",
                            strand,
                            str(cds_start),
                            str(cds_end),
                            BED_RGB,
                            str(len(block_sizes)),
                            ",".join(str(size) for size in block_sizes),
                            ",".join(str(start) for start in block_starts),
                        ]
                    )
                    + "\n"
                )
                transcripts += 1
                genes.add(gene_symbol)

    if transcripts == 0:
        raise ValueError("refGene source produced no BED records")

    return RefGeneStats(
        transcripts=transcripts,
        genes=len(genes),
        skipped_malformed=skipped_malformed,
    )


def write_manifest(
    output_dir: Path,
    *,
    source_config: SourceConfig,
    source_files: dict[str, FileInfo],
    output_files: dict[str, FileInfo],
    fasta_stats: FastaStats,
    refgene_stats: RefGeneStats,
    accessed_date: date,
) -> FileInfo:
    manifest_path = output_dir / MANIFEST_OUTPUT_NAME
    uses_default_sources = (
        source_config.fasta_url == DEFAULT_FASTA_URL
        and source_config.refgene_url == DEFAULT_REFGENE_URL
    )
    if uses_default_sources and source_config.license_url == DEFAULT_LICENSE_URL:
        license_payload = {
            "label": "UCSC Genome Browser data files",
            "url": source_config.license_url,
            "summary": (
                "UCSC states that raw data files and database table dumps used by the "
                "Genome Browser are freely available for public and commercial use; "
                "source databases may impose separate terms."
            ),
        }
    elif source_config.license_url:
        license_payload = {
            "label": "Operator-supplied source license",
            "url": source_config.license_url,
            "summary": (
                "Custom source URLs were provided. Verify the supplied license URL and "
                "source terms before using or sharing the generated artifact."
            ),
        }
    else:
        license_payload = {
            "label": "Custom source license not recorded",
            "summary": (
                "Custom source URLs were provided without a license URL. Verify source "
                "terms separately before using or sharing the generated artifact."
            ),
        }

    fasta_source_payload = {
        "url": source_config.fasta_url,
        **asdict(source_files["fasta"]),
    }
    if source_config.bigzips_url:
        fasta_source_payload["directory_url"] = source_config.bigzips_url

    refgene_source_payload = {
        "url": source_config.refgene_url,
        **asdict(source_files["refgene"]),
    }
    if source_config.database_url:
        refgene_source_payload["directory_url"] = source_config.database_url

    payload = {
        "schema_version": 1,
        "name": "genome_browser_reference_grch37_hg19",
        "created_at": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "accessed_date": accessed_date.isoformat(),
        "runtime_files": [FASTA_OUTPUT_NAME, f"{FASTA_OUTPUT_NAME}.fai", REFSEQ_OUTPUT_NAME],
        "license": license_payload,
        "sources": {
            "fasta": fasta_source_payload,
            "refgene": refgene_source_payload,
        },
        "outputs": {
            key: asdict(value)
            for key, value in sorted(output_files.items(), key=lambda item: item[0])
        },
        "fasta": asdict(fasta_stats),
        "refseq": asdict(refgene_stats),
    }
    manifest_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return file_info(manifest_path, relative_to=output_dir)


def _paths_overlap(first: Path, second: Path) -> bool:
    first_resolved = first.expanduser().resolve()
    second_resolved = second.expanduser().resolve()
    return (
        first_resolved == second_resolved
        or first_resolved in second_resolved.parents
        or second_resolved in first_resolved.parents
    )


def _normalized_source_config(source_config: SourceConfig) -> SourceConfig:
    bigzips_url = source_config.bigzips_url
    if source_config.fasta_url != DEFAULT_FASTA_URL and bigzips_url == DEFAULT_BIGZIPS_URL:
        bigzips_url = None

    database_url = source_config.database_url
    if source_config.refgene_url != DEFAULT_REFGENE_URL and database_url == DEFAULT_DATABASE_URL:
        database_url = None

    license_url = source_config.license_url
    if (
        source_config.fasta_url != DEFAULT_FASTA_URL
        or source_config.refgene_url != DEFAULT_REFGENE_URL
    ) and license_url == DEFAULT_LICENSE_URL:
        license_url = None

    return SourceConfig(
        fasta_url=source_config.fasta_url,
        refgene_url=source_config.refgene_url,
        license_url=license_url,
        bigzips_url=bigzips_url,
        database_url=database_url,
    )


def _remove_source_files(paths: list[Path]) -> None:
    for path in paths:
        path.unlink(missing_ok=True)
        path.with_suffix(path.suffix + ".tmp").unlink(missing_ok=True)


def build_reference_bundle(
    output_dir: Path,
    *,
    source_dir: Path | None = None,
    source_config: SourceConfig = SourceConfig(),
    accessed_date: date | None = None,
    force: bool = False,
    keep_sources: bool = False,
) -> BuildResult:
    accessed_date = accessed_date or datetime.now(UTC).date()
    source_dir_was_provided = source_dir is not None
    source_config = _normalized_source_config(source_config)
    output_dir.mkdir(parents=True, exist_ok=True)
    source_dir = source_dir or output_dir / "_sources"
    if source_dir_was_provided and _paths_overlap(output_dir, source_dir):
        raise ValueError("--source-dir must not overlap --output-dir")
    source_dir.mkdir(parents=True, exist_ok=True)

    fasta_source = source_dir / "hg19.fa.gz"
    refgene_source = source_dir / "refGene.txt.gz"
    downloaded_fasta_source = download_file(source_config.fasta_url, fasta_source, force=force)
    downloaded_refgene_source = download_file(
        source_config.refgene_url, refgene_source, force=force
    )
    fasta_source_info = FileInfo(
        path=fasta_source.name,
        sha256=downloaded_fasta_source.sha256,
        size_bytes=downloaded_fasta_source.size_bytes,
    )
    refgene_source_info = FileInfo(
        path=refgene_source.name,
        sha256=downloaded_refgene_source.sha256,
        size_bytes=downloaded_refgene_source.size_bytes,
    )

    fasta_path = output_dir / FASTA_OUTPUT_NAME
    fai_path = output_dir / f"{FASTA_OUTPUT_NAME}.fai"
    refseq_path = output_dir / REFSEQ_OUTPUT_NAME

    fasta_stats = write_fasta_and_fai(fasta_source, fasta_path, fai_path)
    refgene_stats = convert_refgene_to_bed(refgene_source, refseq_path)

    output_files = {
        "fasta": file_info(fasta_path, relative_to=output_dir),
        "fasta_index": file_info(fai_path, relative_to=output_dir),
        "refseq_bed": file_info(refseq_path, relative_to=output_dir),
    }
    manifest_info = write_manifest(
        output_dir,
        source_config=source_config,
        source_files={"fasta": fasta_source_info, "refgene": refgene_source_info},
        output_files=output_files,
        fasta_stats=fasta_stats,
        refgene_stats=refgene_stats,
        accessed_date=accessed_date,
    )

    if not keep_sources:
        if source_dir_was_provided:
            _remove_source_files([fasta_source, refgene_source])
        else:
            shutil.rmtree(source_dir)

    return BuildResult(
        output_dir=output_dir,
        fasta=output_files["fasta"],
        fasta_index=output_files["fasta_index"],
        refseq_bed=output_files["refseq_bed"],
        manifest=manifest_info,
        fasta_stats=fasta_stats,
        refgene_stats=refgene_stats,
    )


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build the local Genome Browser GRCh37 FASTA + RefSeq BED bundle."
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("data/genome-browser-reference"),
        help=(
            "Directory that receives grch37.fa, grch37.fa.fai, "
            "grch37_refseq.bed, and the manifest."
        ),
    )
    parser.add_argument(
        "--source-dir",
        type=Path,
        default=None,
        help="Temporary/cache directory for downloaded UCSC source files.",
    )
    parser.add_argument("--fasta-url", default=DEFAULT_FASTA_URL, help="UCSC hg19 FASTA URL.")
    parser.add_argument("--refgene-url", default=DEFAULT_REFGENE_URL, help="UCSC refGene URL.")
    parser.add_argument(
        "--fasta-directory-url",
        default=None,
        help="Directory URL recorded for the FASTA source; inferred for the default UCSC URL.",
    )
    parser.add_argument(
        "--refgene-directory-url",
        default=None,
        help="Directory URL recorded for the refGene source; inferred for the default UCSC URL.",
    )
    parser.add_argument(
        "--license-url",
        default=None,
        help="License URL recorded in the manifest; inferred for the default UCSC sources.",
    )
    parser.add_argument(
        "--accessed-date",
        type=date.fromisoformat,
        default=None,
        help="ISO date recorded in the manifest; defaults to today's date.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-download sources when matching files already exist in --source-dir.",
    )
    parser.add_argument(
        "--keep-sources",
        action="store_true",
        help="Keep downloaded source gzip files after a successful build.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    source_config = SourceConfig(
        fasta_url=args.fasta_url,
        refgene_url=args.refgene_url,
        bigzips_url=args.fasta_directory_url or DEFAULT_BIGZIPS_URL,
        database_url=args.refgene_directory_url or DEFAULT_DATABASE_URL,
        license_url=args.license_url or DEFAULT_LICENSE_URL,
    )
    result = build_reference_bundle(
        args.output_dir,
        source_dir=args.source_dir,
        source_config=source_config,
        accessed_date=args.accessed_date,
        force=args.force,
        keep_sources=args.keep_sources,
    )
    print(
        json.dumps(
            {
                "output_dir": result.output_dir.as_posix(),
                "fasta": asdict(result.fasta),
                "fasta_index": asdict(result.fasta_index),
                "refseq_bed": asdict(result.refseq_bed),
                "manifest": asdict(result.manifest),
                "fasta_stats": asdict(result.fasta_stats),
                "refseq_stats": asdict(result.refgene_stats),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Derive Gnomix TSV maps from the downloaded Beagle/PLINK GRCh38 maps."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import tempfile
from pathlib import Path

SOURCE_TEMPLATE = "plink.chrchr{chrom}.GRCh38.map"
OUTPUT_TEMPLATE = "chr{chrom}.map"


class MapFormatError(ValueError):
    """Raised when a source map cannot be converted safely."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _normalize_chromosome(value: str) -> str:
    normalized = value[3:] if value.lower().startswith("chr") else value
    if not normalized.isdigit() or not 1 <= int(normalized) <= 22:
        raise ValueError(f"unsupported autosome: {value!r}")
    return str(int(normalized))


def _requested_chromosomes(values: list[str]) -> list[str]:
    chromosomes = [_normalize_chromosome(value) for value in values]
    if len(chromosomes) != len(set(chromosomes)):
        raise ValueError("chromosome list contains duplicates")
    return sorted(chromosomes, key=int)


def _convert_map(source: Path, destination: Path, chromosome: str) -> dict[str, object]:
    expected_label = f"chr{chromosome}"
    previous_position: int | None = None
    previous_cm: float | None = None
    row_count = 0

    with (
        source.open(encoding="ascii") as source_handle,
        destination.open("w", encoding="ascii", newline="\n") as output_handle,
    ):
        for line_number, raw_line in enumerate(source_handle, start=1):
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue

            fields = line.split()
            if len(fields) != 4:
                raise MapFormatError(
                    f"{source}:{line_number}: expected 4 PLINK columns, found {len(fields)}"
                )

            source_chromosome, _marker, cm_text, position_text = fields
            if source_chromosome != expected_label:
                raise MapFormatError(
                    f"{source}:{line_number}: expected {expected_label}, "
                    f"found {source_chromosome!r}"
                )

            try:
                position = int(position_text)
            except ValueError as exc:
                raise MapFormatError(
                    f"{source}:{line_number}: physical position must be an integer"
                ) from exc
            if position <= 0:
                raise MapFormatError(f"{source}:{line_number}: physical position must be positive")
            if previous_position is not None and position <= previous_position:
                raise MapFormatError(
                    f"{source}:{line_number}: physical positions must be strictly increasing"
                )

            try:
                position_cm = float(cm_text)
            except ValueError as exc:
                raise MapFormatError(
                    f"{source}:{line_number}: genetic position must be numeric"
                ) from exc
            if not math.isfinite(position_cm) or position_cm < 0:
                raise MapFormatError(
                    f"{source}:{line_number}: genetic position must be finite and non-negative"
                )
            if previous_cm is not None and position_cm < previous_cm:
                raise MapFormatError(
                    f"{source}:{line_number}: genetic positions must be non-decreasing"
                )

            output_handle.write(f"{expected_label}\t{position}\t{cm_text}\n")
            previous_position = position
            previous_cm = position_cm
            row_count += 1

    if row_count == 0:
        raise MapFormatError(f"{source}: map contains no data rows")

    return {
        "chromosome": expected_label,
        "source_file": f"chr_in_chrom_field/{source.name}",
        "source_sha256": _sha256(source),
        "derived_file": destination.name,
        "derived_sha256": _sha256(destination),
        "row_count": row_count,
    }


def _write_manifest(path: Path, payload: dict[str, object]) -> None:
    text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="ascii",
        newline="\n",
        dir=path.parent,
        prefix=f".{path.name}.",
        delete=False,
    ) as handle:
        handle.write(text)
        temporary_path = Path(handle.name)
    temporary_path.replace(path)


def verify_maps(output_dir: Path, chromosomes: list[str]) -> Path:
    """Verify requested derived maps against the published provenance marker."""
    chromosomes = _requested_chromosomes(chromosomes)
    manifest_path = output_dir / "provenance.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="ascii"))
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"missing provenance marker: {manifest_path}") from exc
    except json.JSONDecodeError as exc:
        raise MapFormatError(f"invalid provenance marker: {manifest_path}: {exc}") from exc

    if not isinstance(manifest, dict) or manifest.get("schema_version") != 1:
        raise MapFormatError(f"{manifest_path}: unsupported provenance schema")
    entries = manifest.get("maps")
    if not isinstance(entries, list):
        raise MapFormatError(f"{manifest_path}: maps must be a list")

    entries_by_chromosome: dict[str, dict[str, object]] = {}
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("chromosome"), str):
            raise MapFormatError(f"{manifest_path}: malformed map entry")
        chromosome = entry["chromosome"]
        if chromosome in entries_by_chromosome:
            raise MapFormatError(f"{manifest_path}: duplicate entry for {chromosome}")
        entries_by_chromosome[chromosome] = entry

    for chromosome in chromosomes:
        label = f"chr{chromosome}"
        entry = entries_by_chromosome.get(label)
        if entry is None:
            raise MapFormatError(f"{manifest_path}: missing entry for {label}")
        expected_name = OUTPUT_TEMPLATE.format(chrom=chromosome)
        if entry.get("derived_file") != expected_name:
            raise MapFormatError(f"{manifest_path}: unexpected derived filename for {label}")
        expected_sha256 = entry.get("derived_sha256")
        if not isinstance(expected_sha256, str) or len(expected_sha256) != 64:
            raise MapFormatError(f"{manifest_path}: invalid derived checksum for {label}")
        derived_path = output_dir / expected_name
        if not derived_path.is_file() or derived_path.stat().st_size == 0:
            raise FileNotFoundError(f"missing derived map: {derived_path}")
        if _sha256(derived_path) != expected_sha256:
            raise MapFormatError(f"{derived_path}: checksum does not match provenance marker")

    return manifest_path


def derive_maps(
    source_dir: Path,
    output_dir: Path,
    chromosomes: list[str],
    source_url: str,
) -> Path:
    """Validate and derive all requested maps before publishing any of them."""
    chromosomes = _requested_chromosomes(chromosomes)
    sources = {
        chromosome: source_dir / SOURCE_TEMPLATE.format(chrom=chromosome)
        for chromosome in chromosomes
    }
    missing = [
        str(path) for path in sources.values() if not path.is_file() or path.stat().st_size == 0
    ]
    if missing:
        raise FileNotFoundError("missing source map(s): " + ", ".join(missing))

    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = output_dir / "provenance.json"
    entries: list[dict[str, object]] = []
    with tempfile.TemporaryDirectory(prefix=".gnomix-maps.", dir=output_dir) as staging:
        staging_dir = Path(staging)
        for chromosome in chromosomes:
            derived_name = OUTPUT_TEMPLATE.format(chrom=chromosome)
            entries.append(
                _convert_map(sources[chromosome], staging_dir / derived_name, chromosome)
            )

        manifest = {
            "schema_version": 1,
            "genome_build": "GRCh38",
            "converter": "scripts/lai_bundle_v2/01_convert_gnomix_maps.py",
            "source": {
                "url": source_url,
                "format": "PLINK whitespace-delimited chromosome, marker, cM, bp",
                "directory": "genetic_maps_grch38/chr_in_chrom_field",
            },
            "derived_format": "Gnomix tab-delimited chromosome, bp, cM; no header",
            "transformation": "PLINK columns 1,4,3 with chrN labels retained",
            "maps": entries,
        }

        # The manifest is the generation commit marker. Once it is absent,
        # Phase 5 refuses to consume a partially replaced map set.
        manifest_path.unlink(missing_ok=True)
        for entry in entries:
            derived_name = str(entry["derived_file"])
            (staging_dir / derived_name).replace(output_dir / derived_name)
        _write_manifest(manifest_path, manifest)

    return manifest_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--chromosomes", nargs="+", required=True)
    parser.add_argument("--source-url")
    parser.add_argument(
        "--verify",
        action="store_true",
        help="verify derived maps against output-dir/provenance.json",
    )
    args = parser.parse_args()

    try:
        if args.verify:
            manifest_path = verify_maps(args.output_dir, args.chromosomes)
            print(f"verified {len(_requested_chromosomes(args.chromosomes))} Gnomix maps")
            return 0
        if args.source_dir is None or args.source_url is None:
            parser.error("--source-dir and --source-url are required unless --verify is used")
        manifest_path = derive_maps(
            args.source_dir, args.output_dir, args.chromosomes, args.source_url
        )
    except (FileNotFoundError, MapFormatError, OSError, ValueError) as exc:
        parser.exit(1, f"error: {exc}\n")

    payload = json.loads(manifest_path.read_text(encoding="ascii"))
    total_rows = sum(int(entry["row_count"]) for entry in payload["maps"])
    print(f"derived {len(payload['maps'])} Gnomix maps ({total_rows} rows): {manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

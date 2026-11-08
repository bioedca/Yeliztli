"""Validate the PCA bundle's complete, source-backed GRCh37 allele mapping."""

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

COMPLEMENT = str.maketrans("ACGT", "TGCA")
MAPPING_VERSION = "grch37-pca-v1"
_COLUMNS = (
    "rsid",
    "chrom",
    "grch38_position",
    "grch38_ref",
    "grch38_alt",
    "grch37_position",
    "grch37_strand",
    "grch37_ref",
    "grch37_alt",
    "evidence",
)


def load_grch37_mapping(
    bundle_path: Path,
    markers: list[tuple[str, str, int, str, str]],
) -> dict[str, tuple[int, int]]:
    """Return GRCh37 position/strand for every training marker, or fail closed.

    The sibling JSON pins both the NPZ and TSV by SHA-256. Rows must match the
    training marker order, coordinates and ordered alleles exactly. Explicit
    strand determines the ordered GRCh37 pair, including palindromic SNPs;
    allele membership alone cannot establish that orientation.
    """
    metadata_path = bundle_path.with_suffix(".grch37.json")
    table_path = bundle_path.with_suffix(".grch37.tsv")
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    table_bytes = table_path.read_bytes()
    expected = {
        "schema_version": 1,
        "mapping_version": MAPPING_VERSION,
        "training_build": "GRCh38",
        "sample_build": "GRCh37",
        "sample_strand": "+",
        "marker_count": len(markers),
        "bundle_sha256": hashlib.sha256(bundle_path.read_bytes()).hexdigest(),
        "mapping_sha256": hashlib.sha256(table_bytes).hexdigest(),
    }
    if not isinstance(metadata, dict) or any(metadata.get(k) != v for k, v in expected.items()):
        raise ValueError("Ancestry GRCh37 mapping metadata does not match the PCA bundle")
    rows = list(csv.DictReader(table_bytes.decode("utf-8").splitlines(), delimiter="\t"))
    if not rows or tuple(rows[0]) != _COLUMNS or len(rows) != len(markers):
        raise ValueError("Ancestry GRCh37 mapping must cover every AIM with the expected columns")
    result = {}
    for row, (rsid, chrom, position, ref, alt) in zip(rows, markers, strict=True):
        try:
            sample_position = int(row["grch37_position"])
            strand = int(row["grch37_strand"])
            training_position = int(row["grch38_position"])
        except (ValueError, TypeError) as exc:
            raise ValueError(f"Invalid ancestry mapping coordinates/strand for {rsid}") from exc
        sample_ref = ref.translate(COMPLEMENT) if strand == -1 else ref
        sample_alt = alt.translate(COMPLEMENT) if strand == -1 else alt
        if (
            len(row) != len(_COLUMNS)
            or row["rsid"] != rsid
            or rsid in result
            or row["chrom"] != chrom
            or training_position != position
            or position <= 0
            or sample_position <= 0
            or strand not in (-1, 1)
            or ref not in {"A", "C", "G", "T"}
            or alt not in {"A", "C", "G", "T"}
            or ref == alt
            or row["grch38_ref"] != ref
            or row["grch38_alt"] != alt
            or row["grch37_ref"] != sample_ref
            or row["grch37_alt"] != sample_alt
            or not row["evidence"]
        ):
            raise ValueError(f"Invalid ancestry GRCh37 mapping for {rsid}")
        result[rsid] = (sample_position, strand)
    return result

#!/usr/bin/env python3
"""Audit the checksum-pinned Illumina GSA-24v3 manifest archive.

The public manifest is large and contains Illumina probe designs, so it is not
redistributed with Yeliztli.  Download the URL recorded in the typeability
artifact to an operator-controlled path, then run this verifier::

    python scripts/audit_gsa_24v3_manifest.py /path/to/GSA-24v3-0-A1-manifest-file-csv.zip

The verifier checks the archive and embedded CSV hashes recorded in
``gsa_24v3_typeability.json``, parses the CSV by section, and emits a compact,
deterministic metadata report.  In particular, it counts records between
``[Assay]`` and ``[Controls]`` as loci; it never substitutes the CSV's total
physical line count for Illumina's ``Loci Count``.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import sys
import zipfile
from collections import Counter
from pathlib import Path
from typing import BinaryIO

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PROVENANCE_PATH = (
    REPO_ROOT / "backend" / "data" / "array_manifests" / "gsa_24v3_typeability.json"
)
DEFAULT_AUDIT_REPORT_RELATIVE = Path("backend/data/array_manifests/gsa_24v3_manifest_audit.json")
OFFICIAL_MANIFEST_URL = (
    "https://www.illumina.com/content/dam/illumina-support/documents/downloads/"
    "productfiles/global-screening-array-24/v3-0/GSA-24v3-0-A1-manifest-file-csv.zip"
)

EXPECTED_ASSAY_COLUMNS = (
    "IlmnID",
    "Name",
    "IlmnStrand",
    "SNP",
    "AddressA_ID",
    "AlleleA_ProbeSeq",
    "AddressB_ID",
    "AlleleB_ProbeSeq",
    "GenomeBuild",
    "Chr",
    "MapInfo",
    "Ploidy",
    "Species",
    "Source",
    "SourceVersion",
    "SourceStrand",
    "SourceSeq",
    "TopGenomicSeq",
    "BeadSetID",
    "Exp_Clusters",
    "Intensity_Only",
    "RefStrand",
)


class ManifestAuditError(ValueError):
    """The archive, CSV structure, or recorded provenance is inconsistent."""


def sha256_file(path: Path) -> str:
    """Return the streaming SHA-256 digest of *path*."""

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_stream(handle: BinaryIO) -> str:
    digest = hashlib.sha256()
    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
        digest.update(chunk)
    return digest.hexdigest()


def parse_manifest_csv(handle: BinaryIO) -> dict[str, object]:
    """Parse one Illumina manifest CSV stream without retaining assay records."""

    reader = csv.reader(io.TextIOWrapper(handle, encoding="utf-8-sig", newline=""))
    metadata: dict[str, str] = {}
    control_categories: Counter[str] = Counter()
    state = "preamble"
    assay_rows = 0
    control_records = 0
    control_marker_lines = 0
    heading_and_assay_header_lines = 0
    physical_lines = 0

    for row in reader:
        physical_lines = reader.line_num
        marker = row[0].strip() if len(row) == 1 else None

        if state == "preamble":
            if marker == "[Heading]":
                state = "heading"
            elif reader.line_num != 1:
                raise ManifestAuditError(f"expected [Heading] on CSV line 2, found {row!r}")
            continue

        if state == "heading":
            if marker == "[Assay]":
                state = "assay_header"
                continue
            if len(row) != 2:
                raise ManifestAuditError(
                    f"expected two-column heading metadata on CSV line {reader.line_num}"
                )
            metadata[row[0].strip()] = row[1].strip()
            continue

        if state == "assay_header":
            if tuple(row) != EXPECTED_ASSAY_COLUMNS:
                raise ManifestAuditError(
                    f"unexpected [Assay] header on CSV line {reader.line_num}"
                )
            heading_and_assay_header_lines = reader.line_num
            state = "assay"
            continue

        if state == "assay":
            if marker == "[Controls]":
                control_marker_lines += 1
                state = "controls"
                continue
            if len(row) != len(EXPECTED_ASSAY_COLUMNS):
                raise ManifestAuditError(
                    f"assay row on CSV line {reader.line_num} has {len(row)} columns; "
                    f"expected {len(EXPECTED_ASSAY_COLUMNS)}"
                )
            assay_rows += 1
            continue

        if len(row) != 4:
            raise ManifestAuditError(
                f"control record on CSV line {reader.line_num} has {len(row)} columns; expected 4"
            )
        control_records += 1
        control_categories[row[1].strip()] += 1

    if state != "controls":
        raise ManifestAuditError("manifest is missing a complete [Assay]/[Controls] structure")

    required_metadata = (
        "Descriptor File Name",
        "Assay Format",
        "Date Manufactured",
        "Loci Count",
    )
    missing = [key for key in required_metadata if not metadata.get(key)]
    if missing:
        raise ManifestAuditError(f"manifest heading is missing: {', '.join(missing)}")

    try:
        heading_loci_count = int(metadata["Loci Count"])
    except ValueError as exc:
        raise ManifestAuditError("manifest Loci Count is not an integer") from exc
    if heading_loci_count != assay_rows:
        raise ManifestAuditError(
            "manifest Loci Count does not match [Assay] records: "
            f"heading={heading_loci_count}, assay_rows={assay_rows}"
        )
    non_assay_physical_lines = physical_lines - assay_rows
    decomposed_non_assay_lines = (
        heading_and_assay_header_lines + control_marker_lines + control_records
    )
    if non_assay_physical_lines != decomposed_non_assay_lines:
        raise ManifestAuditError(
            "non-assay physical lines do not match headers + section marker + controls: "
            f"observed={non_assay_physical_lines}, decomposed={decomposed_non_assay_lines}"
        )

    return {
        "heading": {
            "assay_format": metadata["Assay Format"],
            "date_manufactured": metadata["Date Manufactured"],
            "descriptor_file_name": metadata["Descriptor File Name"],
            "loci_count": heading_loci_count,
        },
        "sections": {
            "assay_header_columns": len(EXPECTED_ASSAY_COLUMNS),
            "assay_rows": assay_rows,
            "control_categories": dict(sorted(control_categories.items())),
            "control_marker_lines": control_marker_lines,
            "control_records": control_records,
            "heading_and_assay_header_lines": heading_and_assay_header_lines,
            "non_assay_physical_lines": non_assay_physical_lines,
            "physical_lines": physical_lines,
        },
    }


def audit_manifest_archive(
    archive_path: Path, *, expected_archive_sha256: str | None = None
) -> dict[str, object]:
    """Return deterministic source, heading, and section metadata for *archive_path*."""

    archive_sha256 = sha256_file(archive_path)
    if expected_archive_sha256 is not None and archive_sha256 != expected_archive_sha256:
        raise ManifestAuditError(
            "manifest_zip_sha256 mismatch: "
            f"recorded={expected_archive_sha256!r}, observed={archive_sha256!r}"
        )
    with zipfile.ZipFile(archive_path) as archive:
        members = [info for info in archive.infolist() if not info.is_dir()]
        if len(members) != 1:
            raise ManifestAuditError(
                f"expected exactly one CSV member, found {len(members)} non-directory members"
            )
        member = members[0]
        if not member.filename.lower().endswith(".csv"):
            raise ManifestAuditError(f"archive member is not a CSV: {member.filename}")
        with archive.open(member) as handle:
            csv_sha256 = _sha256_stream(handle)
        with archive.open(member) as handle:
            parsed = parse_manifest_csv(handle)

    return {
        "schema_version": 1,
        "source": {
            "archive_sha256": archive_sha256,
            "archive_size_bytes": archive_path.stat().st_size,
            "csv_member": member.filename,
            "csv_sha256": csv_sha256,
            "csv_size_bytes": member.file_size,
            "url": OFFICIAL_MANIFEST_URL,
        },
        **parsed,
    }


def load_provenance(path: Path) -> dict[str, object]:
    """Load the typeability artifact's provenance mapping."""

    payload = json.loads(path.read_text(encoding="utf-8"))
    provenance = payload.get("_provenance")
    if not isinstance(provenance, dict):
        raise ManifestAuditError(f"{path} has no _provenance object")
    return provenance


def verify_report(report: dict[str, object], provenance: dict[str, object]) -> None:
    """Fail unless parsed *report* exactly matches the committed provenance pins."""

    source = report["source"]
    heading = report["heading"]
    sections = report["sections"]
    assert isinstance(source, dict)
    assert isinstance(heading, dict)
    assert isinstance(sections, dict)

    expected = {
        "url": source["url"],
        "manifest_zip_sha256": source["archive_sha256"],
        "manifest_csv_member": source["csv_member"],
        "manifest_csv_sha256": source["csv_sha256"],
        "manifest_descriptor": heading["descriptor_file_name"],
        "manifest_date_manufactured": heading["date_manufactured"],
        "manifest_loci_count": sections["assay_rows"],
        "manifest_control_record_count": sections["control_records"],
        "manifest_non_assay_line_count": sections["non_assay_physical_lines"],
        "manifest_csv_physical_line_count": sections["physical_lines"],
    }
    for key, observed in expected.items():
        recorded = provenance.get(key)
        if recorded != observed:
            raise ManifestAuditError(
                f"provenance {key} mismatch: recorded={recorded!r}, observed={observed!r}"
            )


def verify_audit_snapshot(report: dict[str, object], provenance: dict[str, object]) -> None:
    """Fail unless *report* exactly reproduces the committed audit snapshot."""

    relative_path = provenance.get("manifest_audit")
    if relative_path != DEFAULT_AUDIT_REPORT_RELATIVE.as_posix():
        raise ManifestAuditError(
            "provenance manifest_audit mismatch: "
            f"recorded={relative_path!r}, expected={DEFAULT_AUDIT_REPORT_RELATIVE.as_posix()!r}"
        )
    audit_path = REPO_ROOT / DEFAULT_AUDIT_REPORT_RELATIVE
    expected_report = json.loads(audit_path.read_text(encoding="utf-8"))
    if report != expected_report:
        raise ManifestAuditError(
            f"parsed report does not reproduce committed audit snapshot {audit_path}"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path, help="locally downloaded official manifest ZIP")
    parser.add_argument(
        "--provenance",
        type=Path,
        default=DEFAULT_PROVENANCE_PATH,
        help="typeability JSON whose _provenance pins the expected artifact",
    )
    args = parser.parse_args(argv)

    try:
        provenance = load_provenance(args.provenance)
        expected_archive_sha256 = provenance.get("manifest_zip_sha256")
        if not isinstance(expected_archive_sha256, str):
            raise ManifestAuditError("provenance manifest_zip_sha256 is missing or invalid")
        report = audit_manifest_archive(
            args.archive,
            expected_archive_sha256=expected_archive_sha256,
        )
        verify_report(report, provenance)
        verify_audit_snapshot(report, provenance)
    except (
        OSError,
        UnicodeDecodeError,
        csv.Error,
        json.JSONDecodeError,
        zipfile.BadZipFile,
        ManifestAuditError,
    ) as exc:
        print(f"GSA-24v3 manifest audit failed: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Tests for the offline, section-aware GSA-24v3 manifest verifier."""

from __future__ import annotations

import csv
import importlib.util
import io
import json
import zipfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "audit_gsa_24v3_manifest.py"


def _load_script_module():
    spec = importlib.util.spec_from_file_location("audit_gsa_24v3_manifest", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


audit = _load_script_module()


def _csv_bytes(
    *,
    assay_rows: int = 2,
    heading_loci_count: int | None = None,
    assay_header: list[str] | None = None,
) -> bytes:
    heading_loci_count = assay_rows if heading_loci_count is None else heading_loci_count
    rows = [
        ["Illumina", " Inc."],
        ["[Heading]"],
        ["Descriptor File Name", "GSA-24v3-0_A1.bpm"],
        ["Assay Format", "Infinium HTS"],
        ["Date Manufactured", "8/7/2019"],
        ["Loci Count ", str(heading_loci_count)],
        ["[Assay]"],
        assay_header or list(audit.EXPECTED_ASSAY_COLUMNS),
    ]
    rows.extend([[f"assay-{i}-{column}" for column in range(22)] for i in range(assay_rows)])
    rows.extend(
        [
            ["[Controls]"],
            ["control-1", "Staining", "value-1", "value-2"],
            ["control-2", "Extension", "value-1", "value-2"],
        ]
    )

    buffer = io.StringIO(newline="")
    csv.writer(buffer, lineterminator="\r\n").writerows(rows)
    return buffer.getvalue().encode()


def _write_archive(
    path: Path,
    *,
    member: str = "GSA-24v3-0_A1.csv",
    content: bytes | None = None,
    extra_member: bool = False,
) -> Path:
    info = zipfile.ZipInfo(member, date_time=(2019, 8, 7, 16, 17, 0))
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            info,
            content if content is not None else _csv_bytes(),
            compress_type=zipfile.ZIP_DEFLATED,
        )
        if extra_member:
            archive.writestr("unexpected.txt", "extra")
    return path


def _matching_provenance(report: dict[str, object]) -> dict[str, object]:
    source = report["source"]
    heading = report["heading"]
    sections = report["sections"]
    assert isinstance(source, dict)
    assert isinstance(heading, dict)
    assert isinstance(sections, dict)
    return {
        "manifest_audit": audit.DEFAULT_AUDIT_REPORT_RELATIVE.as_posix(),
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


def test_audit_snapshot_must_exactly_reproduce_parser_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    report = audit.audit_manifest_archive(_write_archive(tmp_path / "manifest.zip"))
    snapshot_path = tmp_path / "audit.json"
    snapshot_path.write_text(json.dumps(report), encoding="utf-8")
    monkeypatch.setattr(audit, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(audit, "DEFAULT_AUDIT_REPORT_RELATIVE", Path("audit.json"))
    provenance = {"manifest_audit": "audit.json"}

    audit.verify_audit_snapshot(report, provenance)

    stale_report = {**report, "schema_version": 2}
    snapshot_path.write_text(json.dumps(stale_report), encoding="utf-8")
    with pytest.raises(audit.ManifestAuditError, match="does not reproduce"):
        audit.verify_audit_snapshot(report, provenance)


def test_section_aware_count_excludes_controls_and_headers(tmp_path: Path) -> None:
    archive = _write_archive(tmp_path / "manifest.zip")

    report = audit.audit_manifest_archive(archive)
    audit.verify_report(report, _matching_provenance(report))

    assert report["heading"]["loci_count"] == 2
    assert report["sections"] == {
        "assay_header_columns": 22,
        "assay_rows": 2,
        "control_categories": {"Extension": 1, "Staining": 1},
        "control_marker_lines": 1,
        "control_records": 2,
        "heading_and_assay_header_lines": 8,
        "non_assay_physical_lines": 11,
        "physical_lines": 13,
    }


def test_rejects_bad_checksum_before_opening_zip(tmp_path: Path) -> None:
    not_a_zip = tmp_path / "not-a-zip.bin"
    not_a_zip.write_bytes(b"invalid archive")

    with pytest.raises(audit.ManifestAuditError, match="manifest_zip_sha256 mismatch"):
        audit.audit_manifest_archive(
            not_a_zip,
            expected_archive_sha256="0" * 64,
        )


def test_rejects_provenance_checksum_mismatch(tmp_path: Path) -> None:
    report = audit.audit_manifest_archive(_write_archive(tmp_path / "manifest.zip"))
    provenance = _matching_provenance(report)
    provenance["manifest_zip_sha256"] = "0" * 64

    with pytest.raises(audit.ManifestAuditError, match="manifest_zip_sha256 mismatch"):
        audit.verify_report(report, provenance)


def test_rejects_wrong_pinned_member_name(tmp_path: Path) -> None:
    report = audit.audit_manifest_archive(
        _write_archive(tmp_path / "manifest.zip", member="renamed.csv")
    )
    provenance = _matching_provenance(report)
    provenance["manifest_csv_member"] = "GSA-24v3-0_A1.csv"

    with pytest.raises(audit.ManifestAuditError, match="manifest_csv_member mismatch"):
        audit.verify_report(report, provenance)


def test_rejects_archive_with_extra_member(tmp_path: Path) -> None:
    archive = _write_archive(tmp_path / "manifest.zip", extra_member=True)

    with pytest.raises(audit.ManifestAuditError, match="exactly one CSV member"):
        audit.audit_manifest_archive(archive)


def test_cli_reports_malformed_csv_without_traceback(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    archive = _write_archive(tmp_path / "manifest.zip", content=b"\xff\xfe")
    provenance_path = tmp_path / "provenance.json"
    provenance_path.write_text(
        json.dumps(
            {
                "_provenance": {
                    "manifest_zip_sha256": audit.sha256_file(archive),
                }
            }
        ),
        encoding="utf-8",
    )

    assert audit.main([str(archive), "--provenance", str(provenance_path)]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith("GSA-24v3 manifest audit failed:")
    assert "Traceback" not in captured.err


def test_rejects_changed_assay_header(tmp_path: Path) -> None:
    header = list(audit.EXPECTED_ASSAY_COLUMNS)
    header[0] = "UnexpectedID"
    archive = _write_archive(tmp_path / "manifest.zip", content=_csv_bytes(assay_header=header))

    with pytest.raises(audit.ManifestAuditError, match=r"unexpected \[Assay\] header"):
        audit.audit_manifest_archive(archive)


def test_rejects_loci_count_that_includes_non_assay_lines(tmp_path: Path) -> None:
    archive = _write_archive(
        tmp_path / "manifest.zip",
        content=_csv_bytes(assay_rows=2, heading_loci_count=13),
    )

    with pytest.raises(audit.ManifestAuditError, match=r"Loci Count.*assay_rows=2"):
        audit.audit_manifest_archive(archive)

"""Docs↔code consistency guard for the upload file-format claims (#1284/#1338).

The upload route accepts plain vendor ``.txt`` exports and single-member vendor
``.zip`` downloads, then rejects ambiguous/unsafe archives. The onboarding docs
must advertise the same contract because 23andMe and AncestryDNA both ship raw
data as a ``.zip`` by default.
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest

from backend.api.routes.ingest import _normalize_upload_bytes

REPO_ROOT = Path(__file__).resolve().parents[2]
_UPLOAD_DOCS = (
    REPO_ROOT / "docs" / "getting-started" / "upload-your-dna.md",
    REPO_ROOT / "docs" / "install" / "setup-wizard.md",
)
_RAW_23ANDME = b"# rsid\tchromosome\tposition\tgenotype\nrs1\t1\t12345\tAA\n"


def _zip_bytes(name: str, data: bytes) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(name, data)
    return buf.getvalue()


def test_app_extracts_single_member_vendor_zip() -> None:
    """The contract the docs must match: a single-member vendor ZIP is accepted."""
    extracted, filename = _normalize_upload_bytes(
        _zip_bytes("raw_data.txt", _RAW_23ANDME),
        "raw_data.zip",
    )
    assert extracted == _RAW_23ANDME
    assert filename == "raw_data.txt"


@pytest.mark.parametrize("doc", _UPLOAD_DOCS, ids=lambda p: p.name)
def test_doc_lists_zip_as_supported_upload_format(doc: Path) -> None:
    text = doc.read_text(encoding="utf-8").lower()
    assert ".txt" in text
    assert ".zip" in text
    assert "single" in text or "one" in text


@pytest.mark.parametrize("doc", _UPLOAD_DOCS, ids=lambda p: p.name)
def test_doc_does_not_claim_zip_must_be_extracted(doc: Path) -> None:
    text = doc.read_text(encoding="utf-8").lower()
    assert "does not accept the `.zip`" not in text
    assert "extract the `.txt` from it before uploading" not in text

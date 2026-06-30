"""Docs↔code consistency guard for the upload file-format claims (#1284).

The app **rejects ZIP uploads** before parsing
(``dispatcher.reject_unsupported_archive_bytes`` raises the "extract the raw
.txt first" message, called at ``ingest.py``), yet the upload docs used to
advertise "``.txt`` or ``.zip``" as supported — wrong at the highest-traffic
onboarding step, since 23andMe and AncestryDNA both ship raw data as a ``.zip``.

This locks the docs to the ``.txt``-only contract: ``.zip`` may appear in the
upload docs **only** as extract-the-``.txt`` guidance, never as an accepted
upload file type.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.ingestion.base import UnsupportedFormatError
from backend.ingestion.dispatcher import reject_unsupported_archive_bytes

REPO_ROOT = Path(__file__).resolve().parents[2]
_UPLOAD_DOCS = (
    REPO_ROOT / "docs" / "getting-started" / "upload-your-dna.md",
    REPO_ROOT / "docs" / "install" / "setup-wizard.md",
)
# A minimal local ZIP header (PK\x03\x04 magic) — what a vendor raw-data archive
# starts with; padded past the 512-byte sniff window the route reads.
_ZIP_BYTES = b"PK\x03\x04" + b"\x00" * 600


def test_app_rejects_zip_upload() -> None:
    """The contract the docs must match: a ZIP is rejected (premise guard)."""
    with pytest.raises(UnsupportedFormatError, match="ZIP archive"):
        reject_unsupported_archive_bytes(_ZIP_BYTES[:512])


@pytest.mark.parametrize("doc", _UPLOAD_DOCS, ids=lambda p: p.name)
def test_doc_mentions_zip_only_as_extract_guidance(doc: Path) -> None:
    """``.zip`` must appear only on a line that also tells the user to extract the
    ``.txt`` — never as an accepted upload format (the #1284 "``.txt`` or ``.zip``"
    claim, which the app rejects)."""
    for lineno, line in enumerate(doc.read_text(encoding="utf-8").splitlines(), 1):
        if ".zip" in line.lower():
            assert "extract" in line.lower(), (
                f"{doc.name}:{lineno} presents '.zip' without extract guidance — "
                "the app rejects ZIP uploads (#1284). Mention '.zip' only as "
                f"'extract the .txt from the .zip', never as a supported file type:\n{line}"
            )


@pytest.mark.parametrize("doc", _UPLOAD_DOCS, ids=lambda p: p.name)
def test_doc_keeps_extract_the_txt_guidance(doc: Path) -> None:
    """The extract-the-``.txt``-from-the-vendor-``.zip`` guidance must not silently
    vanish, or users are left with no path from the ``.zip`` the vendors ship."""
    text = doc.read_text(encoding="utf-8").lower()
    assert "extract" in text and ".zip" in text, (
        f"{doc.name} must keep guidance to extract the .txt from the vendor .zip (#1284)."
    )

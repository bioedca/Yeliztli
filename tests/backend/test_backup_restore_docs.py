from __future__ import annotations

from pathlib import Path

DOCS_ROOT = Path(__file__).resolve().parents[2] / "docs"
BACKUP_RESTORE_DOC = DOCS_ROOT / "install" / "backup-restore.md"
TROUBLESHOOTING_DOC = DOCS_ROOT / "install" / "troubleshooting.md"


def test_backup_restore_docs_disclose_bundle_major_restore_gate() -> None:
    doc = BACKUP_RESTORE_DOC.read_text(encoding="utf-8")
    normalized = " ".join(doc.split())

    assert "### Version compatibility" in doc
    assert "VEP consequence bundle" in doc
    assert "**major version**" in doc
    assert "mismatch in either direction stops" in doc
    assert "before files are extracted" in doc
    assert "fresh install with no recorded bundle skips this comparison" in normalized
    assert "Very old backups" in doc
    assert "treated as `v1.0.0`" in doc


def test_troubleshooting_docs_link_restore_bundle_mismatch_recovery() -> None:
    doc = TROUBLESHOOTING_DOC.read_text(encoding="utf-8")

    assert "Restore fails with a bundle-version mismatch" in doc
    assert "VEP consequence bundle major" in doc
    assert "fresh install" in doc
    assert "backup & restore" in doc

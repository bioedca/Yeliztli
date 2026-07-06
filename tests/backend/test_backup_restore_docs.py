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


def test_backup_restore_docs_disclose_config_merge_auth_bind_policy() -> None:
    doc = BACKUP_RESTORE_DOC.read_text(encoding="utf-8")
    normalized = " ".join(doc.split())

    assert "merged into this installation's live `config.toml` by key" in normalized
    assert "not the relocated data directory" in normalized
    assert "target-only keys stay in place" in normalized
    assert "auth_enabled" in doc
    assert "auth_password_hash" in doc
    assert "host" in doc
    assert "port" in doc
    assert "not imported from the backup" in normalized


def test_troubleshooting_docs_link_restore_bundle_mismatch_recovery() -> None:
    doc = TROUBLESHOOTING_DOC.read_text(encoding="utf-8")

    assert "Restore fails with a bundle-version mismatch" in doc
    assert "VEP consequence bundle major" in doc
    assert "fresh install" in doc
    assert "backup & restore" in doc

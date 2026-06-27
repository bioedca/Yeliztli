"""1000G Phase 3 v5a imputation reference panel — manifest/fetch/validate (SW-C1).

Validates the manifest parser, the SHA-256-verified fetch (resume-skip + mismatch
guard), the genetic-map zip extraction, panel validation, and provenance
recording. Uses a synthetic manifest + a monkeypatched ``stream_download`` over a
two-chromosome subset, so no real ~8.5 GB download is touched.
"""

from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

import pytest
import sqlalchemy as sa

import backend.annotation.http_download as http_dl
from backend.annotation.imputation_panel import (
    PANEL_BUILD,
    PANEL_CHROMOSOMES,
    PANEL_VERSION,
    fetch_panel,
    load_panel_manifest,
    panel_bref3_path,
    panel_files,
    panel_map_path,
    record_panel_version,
    validate_panel,
)
from backend.db.tables import database_versions, reference_metadata

# Synthetic "remote" content for the subset we actually fetch (chr21, chr22, map).
_CHR21 = b"BREF3-chr21-synthetic-content"
_CHR22 = b"BREF3-chr22-synthetic-content"
_BASE_URL = "http://example.org/b37.bref3"
_MAP_URL = "http://example.org/plink.GRCh37.map.zip"
_SUBSET = ("21", "22")


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _build_map_zip(path: Path) -> bytes:
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("plink.chr21.GRCh37.map", "21 rs1 0 1000\n")
        zf.writestr("plink.chr22.GRCh37.map", "22 rs2 0 2000\n")
    return path.read_bytes()


def _write_manifest(tmp_path: Path, *, chr21: bytes, chr22: bytes, map_bytes: bytes) -> Path:
    # Every declared chromosome must be present in `files`; the ones we don't fetch
    # carry dummy metadata (never exercised because we restrict to _SUBSET).
    files: dict[str, dict] = {
        f"chr{c}": {"sha256": "0" * 64, "size_bytes": 1} for c in PANEL_CHROMOSOMES
    }
    files["chr21"] = {"sha256": _sha(chr21), "size_bytes": len(chr21)}
    files["chr22"] = {"sha256": _sha(chr22), "size_bytes": len(chr22)}
    files["map"] = {"sha256": _sha(map_bytes), "size_bytes": len(map_bytes)}
    manifest = {
        "imputation_panel": {
            "base_url": _BASE_URL,
            "map_url": _MAP_URL,
            "build": "GRCh37",
            "version": PANEL_VERSION,
            "citation_pmid": "26432245",
            "files": files,
        }
    }
    p = tmp_path / "manifest.json"
    p.write_text(json.dumps(manifest), encoding="utf-8")
    return p


@pytest.fixture
def panel_setup(tmp_path: Path):
    map_zip_src = _build_map_zip(tmp_path / "map_src.zip")
    manifest = _write_manifest(tmp_path, chr21=_CHR21, chr22=_CHR22, map_bytes=map_zip_src)
    content_by_url = {
        f"{_BASE_URL}/chr21.1kg.phase3.v5a.b37.bref3": _CHR21,
        f"{_BASE_URL}/chr22.1kg.phase3.v5a.b37.bref3": _CHR22,
        _MAP_URL: map_zip_src,
    }
    return manifest, content_by_url


def _install_fake_download(monkeypatch, content_by_url: dict[str, bytes]) -> list[str]:
    """Replace stream_download with one that writes the mapped bytes; record URLs."""
    fetched: list[str] = []

    def fake(url, tmp_path, **_kw):  # noqa: ANN001, ANN202
        fetched.append(url)
        Path(tmp_path).write_bytes(content_by_url[url])
        return None

    monkeypatch.setattr(http_dl, "stream_download", fake)
    return fetched


class TestManifest:
    def test_panel_files_parses_all_chromosomes_plus_map(self, panel_setup) -> None:
        manifest, _ = panel_setup
        files = panel_files(manifest)
        keys = [pf.key for pf in files]
        assert keys == [*PANEL_CHROMOSOMES, "map"]  # 23 chroms + map, in order
        by_key = {pf.key: pf for pf in files}
        assert by_key["21"].url == f"{_BASE_URL}/chr21.1kg.phase3.v5a.b37.bref3"
        assert by_key["21"].sha256 == _sha(_CHR21)
        assert by_key["map"].url == _MAP_URL

    def test_missing_section_raises(self, tmp_path: Path) -> None:
        p = tmp_path / "m.json"
        p.write_text(json.dumps({"bundles": {}}), encoding="utf-8")
        with pytest.raises(KeyError, match="imputation_panel"):
            load_panel_manifest(p)


class TestFetch:
    def test_fetches_verifies_and_extracts_map(self, panel_setup, tmp_path, monkeypatch) -> None:
        manifest, content = panel_setup
        urls = _install_fake_download(monkeypatch, content)
        dest = tmp_path / "panel"

        downloaded = fetch_panel(dest, chromosomes=_SUBSET, manifest_path=manifest)

        assert set(downloaded) == {"21", "22", "map"}
        assert panel_bref3_path(dest, "21").read_bytes() == _CHR21
        # The map zip is extracted to per-chromosome .map files and not retained.
        assert panel_map_path(dest, "21").exists()
        assert panel_map_path(dest, "22").exists()
        assert not (dest / "plink.GRCh37.map.zip").exists()
        assert validate_panel(dest, chromosomes=_SUBSET, manifest_path=manifest)
        assert len(urls) == 3

    def test_resume_skips_already_valid_files(self, panel_setup, tmp_path, monkeypatch) -> None:
        manifest, content = panel_setup
        _install_fake_download(monkeypatch, content)
        dest = tmp_path / "panel"

        fetch_panel(dest, chromosomes=_SUBSET, manifest_path=manifest)
        # Second run: everything present + valid → nothing re-downloaded.
        again = fetch_panel(dest, chromosomes=_SUBSET, manifest_path=manifest)
        assert again == []

    def test_sha256_mismatch_raises_and_drops_partial(
        self, panel_setup, tmp_path, monkeypatch
    ) -> None:
        manifest, content = panel_setup
        # Corrupt the chr21 payload so its SHA-256 no longer matches the manifest.
        content[f"{_BASE_URL}/chr21.1kg.phase3.v5a.b37.bref3"] = b"corrupted"
        _install_fake_download(monkeypatch, content)
        dest = tmp_path / "panel"

        with pytest.raises(ValueError, match="SHA-256 mismatch"):
            fetch_panel(dest, chromosomes=("21",), manifest_path=manifest)
        assert not panel_bref3_path(dest, "21").exists()  # corrupt file not installed
        assert (
            not panel_bref3_path(dest, "21")
            .with_name(panel_bref3_path(dest, "21").name + ".part")
            .exists()
        )


class TestValidate:
    def test_false_when_missing(self, panel_setup, tmp_path) -> None:
        manifest, _ = panel_setup
        assert (
            validate_panel(tmp_path / "empty", chromosomes=_SUBSET, manifest_path=manifest)
            is False
        )

    def test_false_on_wrong_sha(self, panel_setup, tmp_path, monkeypatch) -> None:
        manifest, content = panel_setup
        _install_fake_download(monkeypatch, content)
        dest = tmp_path / "panel"
        fetch_panel(dest, chromosomes=_SUBSET, manifest_path=manifest)
        # Tamper with an installed file → validation fails.
        panel_bref3_path(dest, "21").write_bytes(b"tampered")
        assert validate_panel(dest, chromosomes=_SUBSET, manifest_path=manifest) is False

    def test_false_when_map_missing(self, panel_setup, tmp_path, monkeypatch) -> None:
        manifest, content = panel_setup
        _install_fake_download(monkeypatch, content)
        dest = tmp_path / "panel"
        fetch_panel(dest, chromosomes=_SUBSET, manifest_path=manifest)
        panel_map_path(dest, "21").unlink()
        assert validate_panel(dest, chromosomes=_SUBSET, manifest_path=manifest) is False


class TestProvenance:
    def test_records_grch37_version(self, tmp_path: Path) -> None:
        ref = sa.create_engine(f"sqlite:///{tmp_path}/reference.db")
        reference_metadata.create_all(ref)
        record_panel_version(ref, file_size_bytes=8500000000)
        with ref.connect() as conn:
            row = conn.execute(
                sa.select(database_versions).where(
                    database_versions.c.db_name == "imputation_panel"
                )
            ).fetchone()
        assert row is not None
        assert row.version == PANEL_VERSION
        assert row.genome_build == PANEL_BUILD == "GRCh37"


class TestRealManifest:
    """The committed bundles/manifest.json must declare a complete, well-formed panel."""

    def test_committed_manifest_has_all_chromosomes_and_map(self) -> None:
        # Uses the real repo manifest (no override) — pins that SW-C1 shipped a full,
        # SHA-256-pinned spec for every chromosome + the map.
        files = panel_files()
        assert [pf.key for pf in files] == [*PANEL_CHROMOSOMES, "map"]
        for pf in files:
            assert len(pf.sha256) == 64, f"{pf.key} sha256 not pinned"
            assert pf.size_bytes > 0, f"{pf.key} size not pinned"
            assert pf.url.startswith("http")

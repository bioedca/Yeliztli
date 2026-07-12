"""Tests for LAI bundle registry, extraction, and Java detection (Step 3.7).

T-DL-01: LAI bundle listed in registry with correct metadata
T-DL-02: LAI bundle marked as optional
T-DL-03: Bundle extraction creates expected directory structure
T-DL-04: Java detection returns True/False correctly
T-DL-05: Bundle validation checks all 22 chromosome model files
"""

from __future__ import annotations

import io
import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from backend.db import manifest as manifest_mod
from backend.db.database_registry import (
    DATABASES,
    detect_java,
    get_database,
    validate_lai_bundle,
)

# ── T-DL-01: LAI bundle listed in registry with correct metadata ─────


class TestLAIBundleRegistry:
    """Verify LAI bundle registry entry metadata."""

    def test_lai_bundle_exists_in_registry(self):
        assert "lai_bundle" in DATABASES

    def test_lai_bundle_metadata(self):
        db = DATABASES["lai_bundle"]
        assert db.name == "lai_bundle"
        assert db.display_name == "LAI Bundle (Chromosome Painting)"
        assert db.filename == "lai_bundle.tar.gz"
        # Phase D/Step 32 (PR-0c) bumped this from the v1.1 asset (523 MB) to the
        # published v2.0.0 union bundle (23andMe v5 ∪ AncestryDNA v2.0, 1,941,023
        # autosomal sites — Plan §6.4). The registry SHA-256 must byte-match
        # bundles.lai_bundle.sha256 (Plan §9 Done criterion #4).
        assert db.expected_size_bytes == 1_723_731_810
        assert db.sha256 == ("36abb5f2ed95011aff1227c894f52597ef5c31adb5a132fafdf0830eabf14bff")
        assert db.build_mode == "download"
        assert db.target_db == "standalone"
        assert db.phase == 3

    def test_lai_bundle_has_post_download(self):
        db = DATABASES["lai_bundle"]
        assert db.post_download is not None
        assert callable(db.post_download)

    def test_lai_bundle_url_set(self):
        db = DATABASES["lai_bundle"]
        # Phase D/Step 32 (PR-0c) repointed this from lai-bundle-v1.1.0 to the
        # published lai-bundle-v2.0.0 release asset.
        assert db.url == (
            "https://github.com/bioedca/Yeliztli/releases/download/"
            "lai-bundle-v2.0.0/genomeinsight_lai_bundle_v2.0.0.tar.gz"
        )

    def test_get_database_returns_lai_bundle(self):
        db = get_database("lai_bundle")
        assert db is not None
        assert db.name == "lai_bundle"


# ── Step 21: v2.0.0 manifest fixture exposes the new bundle version ──


class TestLAIBundleManifestV2:
    """Plan §12.2 LAI-00e item v: ``lai_bundle.version`` reads as ``"v2.0.0"``
    for the v2 manifest fixture, alongside ``min_app_version = "0.2.0"`` per
    runbook §10."""

    V2_PAYLOAD = {
        "schema_version": 1,
        "generated_at": "2026-05-20T00:00:00Z",
        "bundles": {
            "lai_bundle": {
                "version": "v2.0.0",
                "build_date": "2026-05-20",
                "url": (
                    "https://github.com/bioedca/Yeliztli/releases/download/"
                    "lai-bundle-v2.0.0/genomeinsight_lai_bundle_v2.0.0.tar.gz"
                ),
                "sha256": "0" * 64,
                "size_bytes": 750_000_000,
                "min_app_version": "0.2.0",
            },
        },
        "pipeline_pins": {},
    }

    @pytest.fixture(autouse=True)
    def _clear_manifest_cache(self):
        manifest_mod.reset_cache()
        yield
        manifest_mod.reset_cache()

    def _write(self, tmp_path: Path) -> Path:
        path = tmp_path / "manifest.json"
        path.write_text(json.dumps(self.V2_PAYLOAD), encoding="utf-8")
        return path

    def test_v2_manifest_exposes_lai_bundle_v2_0_0(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        path = self._write(tmp_path)
        monkeypatch.setenv(manifest_mod.MANIFEST_PATH_ENV, str(path))

        m = manifest_mod.fetch_manifest()
        entry = m.bundles["lai_bundle"]
        assert entry.version == "v2.0.0"
        assert entry.min_app_version == "0.2.0"
        assert entry.size_bytes == 750_000_000
        assert entry.url.endswith("/lai-bundle-v2.0.0/genomeinsight_lai_bundle_v2.0.0.tar.gz")

    def test_v2_manifest_sha256_placeholder_round_trips(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """Pre-publish placeholder SHA (64 zeros) is accepted by the parser —
        the real sha lands when the cluster build produces the tarball."""
        path = self._write(tmp_path)
        monkeypatch.setenv(manifest_mod.MANIFEST_PATH_ENV, str(path))

        m = manifest_mod.fetch_manifest()
        assert m.bundles["lai_bundle"].sha256 == "0" * 64


# ── T-DL-02: LAI bundle marked as optional ───────────────────────────


class TestLAIBundleOptional:
    """Verify LAI bundle is optional."""

    def test_lai_bundle_not_required(self):
        db = DATABASES["lai_bundle"]
        assert db.required is False


# ── T-DL-03: Bundle extraction creates expected directory structure ───


class TestLAIBundleExtraction:
    """Test the post-download extraction callback."""

    def test_extract_creates_model_dirs(self, tmp_path: Path):
        """Create a minimal tarball and verify extraction."""
        import tarfile

        from backend.db.database_registry import _extract_lai_bundle

        tarball = tmp_path / "test_bundle.tar.gz"
        dest_path = tmp_path / "data" / "lai_bundle.tar.gz"
        dest_path.parent.mkdir(parents=True)

        # Create a minimal tarball with expected structure
        with tarfile.open(tarball, "w:gz") as tf:
            for chrom in range(1, 23):
                for fname in ("base_coefs.npz", "metadata.npz", "smoother.json"):
                    fpath = f"gnomix_models/chr{chrom}/{fname}"
                    info = tarfile.TarInfo(name=fpath)
                    info.size = 4
                    tf.addfile(info, fileobj=io.BytesIO(b"test"))
            # Add other expected dirs
            for dirname in ("beagle", "genetic_maps", "liftover", "phasing_panel"):
                info = tarfile.TarInfo(name=f"{dirname}/")
                info.type = tarfile.DIRTYPE
                tf.addfile(info)
            mapping = b"rs1\tchr1\t100\n"
            info = tarfile.TarInfo(name="liftover/array_site_mapping.tsv")
            info.size = len(mapping)
            tf.addfile(info, fileobj=io.BytesIO(mapping))

        # Copy tarball to dest_path location (simulating download)
        import shutil

        shutil.copy2(str(tarball), str(dest_path))

        # Run extraction (uses dest_path to derive dest_dir)
        _extract_lai_bundle(dest_path, dest_path)
        assert not dest_path.exists()

        # Verify structure
        lai_dir = dest_path.parent / "lai_bundle"
        assert lai_dir.is_dir()
        for chrom in range(1, 23):
            model_dir = lai_dir / "gnomix_models" / f"chr{chrom}"
            assert model_dir.is_dir(), f"Missing chr{chrom} model dir"
            for fname in ("base_coefs.npz", "metadata.npz", "smoother.json"):
                assert (model_dir / fname).exists(), f"Missing {fname} in chr{chrom}"

    def test_extract_raises_on_incomplete_bundle(self, tmp_path: Path):
        """Extraction should raise if chromosome models are missing."""
        import tarfile

        from backend.db.database_registry import _extract_lai_bundle

        tarball = tmp_path / "incomplete.tar.gz"
        dest_path = tmp_path / "data" / "lai_bundle.tar.gz"
        dest_path.parent.mkdir(parents=True)

        # Only create chr1 — missing chr2-22
        with tarfile.open(tarball, "w:gz") as tf:
            for fname in ("base_coefs.npz", "metadata.npz", "smoother.json"):
                fpath = f"gnomix_models/chr1/{fname}"
                info = tarfile.TarInfo(name=fpath)
                info.size = 4
                tf.addfile(info, fileobj=io.BytesIO(b"test"))

        import shutil

        shutil.copy2(str(tarball), str(dest_path))

        with pytest.raises(ValueError, match="LAI bundle extraction incomplete"):
            _extract_lai_bundle(dest_path, dest_path)

    def test_extract_rejects_invalid_liftover_before_deleting_archive(self, tmp_path: Path):
        """A model-complete archive still needs a usable autosomal mapping."""
        import shutil
        import tarfile

        from backend.db.database_registry import _extract_lai_bundle

        tarball = tmp_path / "invalid-liftover.tar.gz"
        dest_path = tmp_path / "data" / "lai_bundle.tar.gz"
        dest_path.parent.mkdir(parents=True)

        with tarfile.open(tarball, "w:gz") as tf:
            for chrom in range(1, 23):
                for fname in ("base_coefs.npz", "metadata.npz", "smoother.json"):
                    payload = b"test"
                    info = tarfile.TarInfo(name=f"gnomix_models/chr{chrom}/{fname}")
                    info.size = len(payload)
                    tf.addfile(info, fileobj=io.BytesIO(payload))
            mapping = b"rs1\tchrX\t100\n"
            info = tarfile.TarInfo(name="liftover/array_site_mapping.tsv")
            info.size = len(mapping)
            tf.addfile(info, fileobj=io.BytesIO(mapping))

        shutil.copy2(tarball, dest_path)

        with pytest.raises(ValueError, match="liftover table is missing or invalid"):
            _extract_lai_bundle(dest_path, dest_path)

        assert dest_path.exists()


# ── T-DL-04: Java detection returns True/False correctly ─────────────


class TestJavaDetection:
    """Test Java runtime detection with version parsing."""

    def _mock_java(self, version_output: str, returncode: int = 0):
        """Helper to mock both shutil.which and subprocess.run for java."""
        import subprocess as sp

        mock_result = sp.CompletedProcess(
            args=["java", "-version"],
            returncode=returncode,
            stdout="",
            stderr=version_output,
        )
        return (
            patch("shutil.which", return_value="/usr/bin/java"),
            patch("subprocess.run", return_value=mock_result),
        )

    def test_detect_java_when_absent(self):
        with patch("shutil.which", return_value=None):
            assert detect_java() is False

    def test_detect_java_8(self):
        p1, p2 = self._mock_java('openjdk version "1.8.0_292"\n')
        with p1, p2:
            assert detect_java() is True

    def test_detect_java_11(self):
        p1, p2 = self._mock_java('openjdk version "11.0.11" 2021-04-20\n')
        with p1, p2:
            assert detect_java() is True

    def test_detect_java_17(self):
        p1, p2 = self._mock_java('openjdk version "17.0.1" 2021-10-19\n')
        with p1, p2:
            assert detect_java() is True

    def test_detect_java_7_too_old(self):
        p1, p2 = self._mock_java('java version "1.7.0_80"\n')
        with p1, p2:
            assert detect_java() is False

    def test_detect_java_nonzero_returncode(self):
        p1, p2 = self._mock_java("", returncode=1)
        with p1, p2:
            assert detect_java() is False


# ── T-DL-05: Bundle validation checks all 22 chromosome model files ──


class TestLAIBundleValidation:
    """Test LAI bundle directory validation."""

    @staticmethod
    def _write_complete_models(root: Path) -> None:
        for chrom in range(1, 23):
            model_dir = root / "gnomix_models" / f"chr{chrom}"
            model_dir.mkdir(parents=True)
            for fname in ("base_coefs.npz", "metadata.npz", "smoother.json"):
                (model_dir / fname).write_text("test")

    def test_validate_complete_bundle(self, tmp_path: Path):
        """A complete bundle should pass validation."""
        self._write_complete_models(tmp_path)
        mapping = tmp_path / "liftover" / "array_site_mapping.tsv"
        mapping.parent.mkdir()
        mapping.write_text("rs1\tchr1\t100\n", encoding="utf-8")
        assert validate_lai_bundle(tmp_path) is True

    def test_validate_numeric_legacy_liftover_table(self, tmp_path: Path):
        """The v1 filename and bare autosomal labels remain supported."""
        self._write_complete_models(tmp_path)
        mapping = tmp_path / "liftover" / "rsid_to_grch38.tsv"
        mapping.parent.mkdir()
        mapping.write_text("rs1\t1\t100\n", encoding="utf-8")
        assert validate_lai_bundle(tmp_path) is True

    def test_validate_missing_liftover_table(self, tmp_path: Path):
        """Model files alone are not a runnable LAI bundle."""
        self._write_complete_models(tmp_path)
        assert validate_lai_bundle(tmp_path) is False

    def test_validate_liftover_without_autosome(self, tmp_path: Path):
        """A table without any supported autosomal row is not runnable."""
        self._write_complete_models(tmp_path)
        mapping = tmp_path / "liftover" / "array_site_mapping.tsv"
        mapping.parent.mkdir()
        mapping.write_text("rs1\tchrX\t100\n", encoding="utf-8")
        assert validate_lai_bundle(tmp_path) is False

    def test_validate_allows_supplemental_alt_contigs(self, tmp_path: Path):
        """A usable primary map may include published-v2 alternate contigs."""
        self._write_complete_models(tmp_path)
        mapping = tmp_path / "liftover" / "array_site_mapping.tsv"
        mapping.parent.mkdir()
        mapping.write_text(
            "rs1\tchr1\t100\nrs_alt\tchr1_KI270766v1_alt\t200\n",
            encoding="utf-8",
        )
        assert validate_lai_bundle(tmp_path) is True

    def test_validate_duplicate_rsid_uses_final_scope(self, tmp_path: Path):
        """Validation mirrors the runtime lookup's last-write-wins semantics."""
        self._write_complete_models(tmp_path)
        mapping = tmp_path / "liftover" / "array_site_mapping.tsv"
        mapping.parent.mkdir()
        mapping.write_text(
            "rs1\tchr1\t100\nrs1\tchrX\t200\n",
            encoding="utf-8",
        )
        assert validate_lai_bundle(tmp_path) is False

    def test_validate_cache_refreshes_after_same_size_repair(self, tmp_path: Path):
        """Content replacement cannot reuse a stale metadata-keyed result."""
        self._write_complete_models(tmp_path)
        mapping = tmp_path / "liftover" / "array_site_mapping.tsv"
        mapping.parent.mkdir()
        mapping.write_text("rs1\tchr1\t100\n", encoding="utf-8")
        original = mapping.stat()
        assert validate_lai_bundle(tmp_path) is True

        replacement = mapping.with_suffix(".replacement")
        replacement.write_text("rs1\tchrX\t100\n", encoding="utf-8")
        os.utime(replacement, ns=(original.st_atime_ns, original.st_mtime_ns))
        replacement.replace(mapping)

        assert mapping.stat().st_size == original.st_size
        assert mapping.stat().st_ino != original.st_ino
        assert validate_lai_bundle(tmp_path) is False

    def test_validate_liftover_larger_than_reverse_scan_chunk(self, tmp_path: Path):
        """Large lookup tables validate across multiple 64 KiB read chunks."""
        self._write_complete_models(tmp_path)
        mapping = tmp_path / "liftover" / "array_site_mapping.tsv"
        mapping.parent.mkdir()
        mapping.write_text(
            "".join(f"rs{index}\tchr1\t{index + 1}\n" for index in range(5_000)),
            encoding="utf-8",
        )

        assert mapping.stat().st_size > 64 * 1024
        assert validate_lai_bundle(tmp_path) is True

    def test_validate_duplicate_rsid_across_reverse_scan_chunk(self, tmp_path: Path):
        """A later off-autosome duplicate wins even across a 64 KiB boundary."""
        self._write_complete_models(tmp_path)
        mapping = tmp_path / "liftover" / "array_site_mapping.tsv"
        mapping.parent.mkdir()
        filler = "".join(
            f"rs_alt_{index}\tchrUn_KI{index}\t{index + 1}\n" for index in range(3_000)
        )
        mapping.write_text(
            f"rs_duplicate\tchr1\t100\n{filler}rs_duplicate\tchrX\t200\n",
            encoding="utf-8",
        )

        assert mapping.stat().st_size > 64 * 1024
        assert validate_lai_bundle(tmp_path) is False

    def test_validate_liftover_without_trailing_newline(self, tmp_path: Path):
        """The final mapping row need not end with a newline."""
        self._write_complete_models(tmp_path)
        mapping = tmp_path / "liftover" / "array_site_mapping.tsv"
        mapping.parent.mkdir()
        mapping.write_text("rs1\t1\t100", encoding="utf-8")

        assert validate_lai_bundle(tmp_path) is True

    def test_validate_incomplete_bundle(self, tmp_path: Path):
        """A bundle missing chr22 should fail validation."""
        for chrom in range(1, 22):  # Only 1-21
            model_dir = tmp_path / "gnomix_models" / f"chr{chrom}"
            model_dir.mkdir(parents=True)
            for fname in ("base_coefs.npz", "metadata.npz", "smoother.json"):
                (model_dir / fname).write_text("test")
        assert validate_lai_bundle(tmp_path) is False

    def test_validate_missing_file_in_chrom(self, tmp_path: Path):
        """A bundle missing smoother.json in chr10 should fail."""
        for chrom in range(1, 23):
            model_dir = tmp_path / "gnomix_models" / f"chr{chrom}"
            model_dir.mkdir(parents=True)
            for fname in ("base_coefs.npz", "metadata.npz", "smoother.json"):
                if chrom == 10 and fname == "smoother.json":
                    continue
                (model_dir / fname).write_text("test")
        assert validate_lai_bundle(tmp_path) is False

    def test_validate_nonexistent_dir(self, tmp_path: Path):
        """A non-existent directory should fail validation."""
        assert validate_lai_bundle(tmp_path / "nonexistent") is False

    def test_validate_empty_dir(self, tmp_path: Path):
        """An empty directory should fail validation."""
        assert validate_lai_bundle(tmp_path) is False

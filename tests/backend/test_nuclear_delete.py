"""Tests for Nuclear Delete endpoint (P4-21).

DELETE /api/data/nuclear — wipes all data and resets to setup wizard state.
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient


class TestNuclearDelete:
    """Tests for DELETE /api/data/nuclear."""

    def test_nuclear_delete_returns_200(self, test_client: TestClient) -> None:
        resp = test_client.delete("/api/data/nuclear")
        assert resp.status_code == 200
        body = resp.json()
        assert body["deleted"] is True
        assert "setup wizard" in body["message"]

    def test_nuclear_delete_removes_sample_dbs(
        self, test_client: TestClient, tmp_data_dir: Path
    ) -> None:
        """After nuclear delete, sample database files should be gone."""
        # Create a fake sample DB
        sample_dir = tmp_data_dir / "samples"
        sample_dir.mkdir(exist_ok=True)
        fake_db = sample_dir / "sample_1.db"
        fake_db.write_text("fake")

        resp = test_client.delete("/api/data/nuclear")
        assert resp.status_code == 200
        assert not fake_db.exists()

    def test_nuclear_delete_removes_reference_db(
        self, test_client: TestClient, tmp_data_dir: Path
    ) -> None:
        """After nuclear delete, reference.db should be gone."""
        ref_db = tmp_data_dir / "reference.db"
        assert ref_db.exists()  # Created by test_client fixture

        resp = test_client.delete("/api/data/nuclear")
        assert resp.status_code == 200
        assert not ref_db.exists()

    def test_nuclear_delete_removes_downloads(
        self, test_client: TestClient, tmp_data_dir: Path
    ) -> None:
        """Cached downloads should be removed."""
        dl_dir = tmp_data_dir / "downloads"
        dl_dir.mkdir(exist_ok=True)
        (dl_dir / "clinvar.vcf.gz").write_text("fake")

        resp = test_client.delete("/api/data/nuclear")
        assert resp.status_code == 200
        assert not dl_dir.exists()

    def test_nuclear_delete_removes_logs(
        self, test_client: TestClient, tmp_data_dir: Path
    ) -> None:
        """Log files should be removed."""
        log_dir = tmp_data_dir / "logs"
        log_dir.mkdir(exist_ok=True)
        (log_dir / "yeliztli.log").write_text("log entry")

        resp = test_client.delete("/api/data/nuclear")
        assert resp.status_code == 200
        assert not log_dir.exists()

    def test_nuclear_delete_removes_config(
        self, test_client: TestClient, tmp_data_dir: Path
    ) -> None:
        """config.toml and .disclaimer_accepted should be removed."""
        (tmp_data_dir / "config.toml").write_text("[server]\nport = 8000\n")
        (tmp_data_dir / ".disclaimer_accepted").write_text("2025-01-01")

        resp = test_client.delete("/api/data/nuclear")
        assert resp.status_code == 200
        assert not (tmp_data_dir / "config.toml").exists()
        assert not (tmp_data_dir / ".disclaimer_accepted").exists()

    def test_nuclear_delete_removes_large_dbs(
        self, test_client: TestClient, tmp_data_dir: Path
    ) -> None:
        """VEP bundle, gnomAD, dbNSFP, and ENCODE DBs should be removed."""
        for name in ("vep_bundle.db", "gnomad_af.db", "dbnsfp.db", "encode_ccres.db"):
            (tmp_data_dir / name).write_text("fake")

        resp = test_client.delete("/api/data/nuclear")
        assert resp.status_code == 200
        for name in ("vep_bundle.db", "gnomad_af.db", "dbnsfp.db", "encode_ccres.db"):
            assert not (tmp_data_dir / name).exists()

    def test_nuclear_delete_preserves_data_dir(
        self, test_client: TestClient, tmp_data_dir: Path
    ) -> None:
        """The data_dir itself should still exist (empty) after wipe."""
        resp = test_client.delete("/api/data/nuclear")
        assert resp.status_code == 200
        assert tmp_data_dir.exists()
        from backend.diagnostic_cutover import RECEIPT, admit

        assert list(tmp_data_dir.iterdir()) == [tmp_data_dir / RECEIPT]
        (tmp_data_dir / ".disclaimer_accepted").touch()
        admit(tmp_data_dir)

    def test_nuclear_delete_removes_disclaimer_marker(
        self, test_client: TestClient, tmp_data_dir: Path
    ) -> None:
        """The .disclaimer_accepted marker file should be removed."""
        (tmp_data_dir / ".disclaimer_accepted").write_text("2025-01-01T00:00:00")

        resp = test_client.delete("/api/data/nuclear")
        assert resp.status_code == 200
        assert not (tmp_data_dir / ".disclaimer_accepted").exists()

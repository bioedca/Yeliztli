"""Tests for carrier status disclaimer API endpoint (P3-37).

Covers:
  - GET /api/analysis/carrier/disclaimer — Carrier disclaimer text retrieval
  - Response contains title, text, and gene_notes fields
  - Text is substantial and covers key reproductive counseling topics
  - Disclaimer matches hardcoded constants from disclaimers.py
  - Per-gene notes cover all 7 carrier panel genes
  - Reproductive framing language (not disease management)
  - T3-40 (partial): Carrier Status page disclaimer block verification
"""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path
from unittest.mock import patch

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from backend.config import Settings
from backend.db.connection import reset_registry
from backend.db.tables import reference_metadata
from backend.disclaimers import (
    CARRIER_GENE_NOTES,
    CARRIER_STATUS_DISCLAIMER_TEXT,
    CARRIER_STATUS_DISCLAIMER_TITLE,
)

# ── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture()
def tmp_data_dir(tmp_path: Path) -> Path:
    """Create a temporary data directory."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    return data_dir


@pytest.fixture()
def carrier_client(tmp_data_dir: Path) -> Generator[TestClient, None, None]:
    """FastAPI test client wired to a temporary data directory."""
    settings = Settings(data_dir=tmp_data_dir, wal_mode=False)

    # Create reference.db so the registry can initialize
    ref_path = settings.reference_db_path
    engine = sa.create_engine(f"sqlite:///{ref_path}")
    reference_metadata.create_all(engine)
    engine.dispose()

    with (
        patch("backend.main.get_settings", return_value=settings),
        patch("backend.db.connection.get_settings", return_value=settings),
    ):
        reset_registry()

        from backend.main import create_app

        app = create_app()
        with TestClient(app) as tc:
            yield tc

        reset_registry()


# ═══════════════════════════════════════════════════════════════════════
# GET /api/analysis/carrier/disclaimer
# ═══════════════════════════════════════════════════════════════════════


class TestCarrierDisclaimer:
    """Tests for the carrier disclaimer endpoint (P3-37)."""

    def test_returns_disclaimer_text(self, carrier_client: TestClient) -> None:
        """Should return the full carrier disclaimer content."""
        resp = carrier_client.get("/api/analysis/carrier/disclaimer")
        assert resp.status_code == 200
        data = resp.json()
        assert data["title"] == CARRIER_STATUS_DISCLAIMER_TITLE
        assert data["text"] == CARRIER_STATUS_DISCLAIMER_TEXT

    def test_disclaimer_text_not_empty(self, carrier_client: TestClient) -> None:
        """Disclaimer text should be substantial."""
        resp = carrier_client.get("/api/analysis/carrier/disclaimer")
        data = resp.json()
        assert len(data["text"]) > 500
        assert "carrier" in data["text"].lower()

    def test_disclaimer_covers_reproductive_context(self, carrier_client: TestClient) -> None:
        """Disclaimer should emphasize reproductive context, not personal disease."""
        resp = carrier_client.get("/api/analysis/carrier/disclaimer")
        text = resp.json()["text"].lower()
        assert "reproductive" in text
        assert "family planning" in text

    def test_disclaimer_explains_carrier_not_affected(self, carrier_client: TestClient) -> None:
        """Disclaimer should explain classic carriers and gene-specific exceptions."""
        resp = carrier_client.get("/api/analysis/carrier/disclaimer")
        text = resp.json()["text"].lower()
        assert "carrier" in text
        assert "unaffected" in text or "healthy" in text
        assert "sickle-cell trait" in text
        assert "usually asymptomatic" in text

    def test_disclaimer_covers_chip_limitations(self, carrier_client: TestClient) -> None:
        """Disclaimer should warn about genotyping chip limitations."""
        resp = carrier_client.get("/api/analysis/carrier/disclaimer")
        text = resp.json()["text"].lower()
        assert "genotyping chip" in text or "consumer" in text
        assert "clinical" in text

    def test_disclaimer_covers_brca_dual_role(self, carrier_client: TestClient) -> None:
        """Disclaimer should explain BRCA1/2 dual-role (cancer + carrier)."""
        resp = carrier_client.get("/api/analysis/carrier/disclaimer")
        text = resp.json()["text"].lower()
        assert "brca" in text
        assert "cancer" in text
        assert "dominant" in text or "dual" in text

    def test_disclaimer_recommends_genetic_counseling(self, carrier_client: TestClient) -> None:
        """Disclaimer should recommend genetic counselor consultation."""
        resp = carrier_client.get("/api/analysis/carrier/disclaimer")
        text = resp.json()["text"].lower()
        assert "genetic counselor" in text

    def test_disclaimer_includes_resource_links(self, carrier_client: TestClient) -> None:
        """Disclaimer should include professional resource URLs."""
        resp = carrier_client.get("/api/analysis/carrier/disclaimer")
        text = resp.json()["text"]
        assert "findageneticcounselor.nsgc.org" in text
        assert "acog.org" in text
        assert "medlineplus.gov" in text

    def test_disclaimer_mentions_population_specificity(self, carrier_client: TestClient) -> None:
        """Disclaimer should mention population-specific carrier frequencies."""
        resp = carrier_client.get("/api/analysis/carrier/disclaimer")
        text = resp.json()["text"].lower()
        assert "population" in text
        assert "ancestry" in text or "ancestral" in text

    def test_disclaimer_explains_25_percent_risk(self, carrier_client: TestClient) -> None:
        """Disclaimer should explain the 25% autosomal recessive risk."""
        resp = carrier_client.get("/api/analysis/carrier/disclaimer")
        text = resp.json()["text"]
        assert "25%" in text


class TestCarrierGeneNotes:
    """Tests for per-gene carrier display notes (P3-37)."""

    def test_gene_notes_returned_in_response(self, carrier_client: TestClient) -> None:
        """Disclaimer endpoint should include gene_notes field."""
        resp = carrier_client.get("/api/analysis/carrier/disclaimer")
        assert resp.status_code == 200
        data = resp.json()
        assert "gene_notes" in data
        assert isinstance(data["gene_notes"], dict)

    def test_gene_notes_match_constants(self, carrier_client: TestClient) -> None:
        """Gene notes should match the hardcoded CARRIER_GENE_NOTES."""
        resp = carrier_client.get("/api/analysis/carrier/disclaimer")
        assert resp.json()["gene_notes"] == CARRIER_GENE_NOTES

    def test_gene_notes_cover_all_panel_genes(self, carrier_client: TestClient) -> None:
        """Gene notes should cover all 7 carrier panel genes."""
        resp = carrier_client.get("/api/analysis/carrier/disclaimer")
        notes = resp.json()["gene_notes"]
        expected_genes = {"CFTR", "HBB", "GBA", "HEXA", "BRCA1", "BRCA2", "SMN1"}
        assert set(notes.keys()) == expected_genes

    def test_cftr_note_mentions_frequency(self) -> None:
        """CFTR note should mention carrier frequency."""
        assert "1 in 25" in CARRIER_GENE_NOTES["CFTR"]

    def test_hbb_note_mentions_conditions(self) -> None:
        """HBB note should distinguish sickle-cell trait from disease."""
        note = CARRIER_GENE_NOTES["HBB"].lower()
        assert "sickle-cell trait" in note
        assert "not sickle-cell disease" in note
        assert "usually asymptomatic" in note
        assert "kidney" in note
        assert "thalassemia" in note

    def test_gba_note_mentions_gaucher(self) -> None:
        """GBA note should mention Gaucher Disease."""
        assert "Gaucher" in CARRIER_GENE_NOTES["GBA"]

    def test_hexa_note_mentions_tay_sachs(self) -> None:
        """HEXA note should mention Tay-Sachs Disease."""
        assert "Tay-Sachs" in CARRIER_GENE_NOTES["HEXA"]

    def test_brca1_note_mentions_dual_role(self) -> None:
        """BRCA1 note should explain dual-role (cancer + carrier)."""
        note = CARRIER_GENE_NOTES["BRCA1"].lower()
        assert "dual-role" in note or "dual role" in note
        assert "cancer" in note

    def test_brca2_note_mentions_dual_role(self) -> None:
        """BRCA2 note should explain dual-role (cancer + carrier)."""
        note = CARRIER_GENE_NOTES["BRCA2"].lower()
        assert "dual-role" in note or "dual role" in note
        assert "cancer" in note

    def test_smn1_note_mentions_copy_number_limitation(self) -> None:
        """SMN1 note should warn about copy number detection limitations."""
        note = CARRIER_GENE_NOTES["SMN1"].lower()
        assert "copy number" in note
        assert "caution" in note


# ═══════════════════════════════════════════════════════════════════════
# Unit tests for disclaimer constants
# ═══════════════════════════════════════════════════════════════════════


class TestCarrierDisclaimerConstants:
    """Unit tests for the hardcoded carrier disclaimer text (P3-37)."""

    def test_title_not_empty(self) -> None:
        assert len(CARRIER_STATUS_DISCLAIMER_TITLE) > 0

    def test_text_substantial_length(self) -> None:
        """Carrier disclaimer should be substantial (comparable to other module disclaimers)."""
        assert len(CARRIER_STATUS_DISCLAIMER_TEXT) > 1000

    def test_text_uses_reproductive_framing_not_disease(self) -> None:
        """Text should frame as reproductive risk, NOT personal disease risk."""
        text = CARRIER_STATUS_DISCLAIMER_TEXT.lower()
        # Should mention reproductive/family planning
        assert "reproductive" in text
        assert "family planning" in text
        # Should NOT frame carriers as having disease (disease-risk language)
        assert "your risk of developing" not in text
        assert "you will develop" not in text

    def test_gene_notes_all_non_empty(self) -> None:
        """All gene notes should be non-empty strings."""
        for gene, note in CARRIER_GENE_NOTES.items():
            assert len(note) > 50, f"Gene note for {gene} is too short"

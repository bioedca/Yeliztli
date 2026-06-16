"""API tests for the array-confidence endpoint (SW-A11 / #14).

GET /api/analysis/array-confidence?sample_id=N — a Weedon-PPV reliability flag
for every ClinVar P/LP finding, joined to ``annotated_variants`` for popmax AF.
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
from backend.db.sample_schema import create_sample_tables
from backend.db.tables import annotated_variants, findings, reference_metadata, samples

# ── Fixtures ────────────────────────────────────────────────────────


@pytest.fixture
def tmp_data_dir(tmp_path: Path) -> Path:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "samples").mkdir()
    return data_dir


# P/LP findings + their annotated_variants popmax AF (None == no annotation row).
# rsid → (gene, clinvar_significance, popmax_af-or-None, expected band)
_SEED = {
    "rs1800562": ("HFE", "Pathogenic", 0.05, "high"),  # common — array-reliable
    "rs9999001": ("GENEM", "Likely pathogenic", 5e-4, "moderate"),  # rare
    "rs80357906": ("BRCA1", "Pathogenic", 1e-6, "low"),  # very rare — Weedon ~16%
    "rs9999002": ("GENEU", "Pathogenic", None, "unknown"),  # no annotation row
    # Common AF (0.15) but a locus-specific array weak spot → locus_low, not high (#636).
    "rs429358": ("APOE", "Pathogenic", 0.15, "locus_low"),
}

# Non-P/LP ClinVar codes that must be filtered out of the endpoint.
_NON_PLP_SIGNIFICANCES = (
    "Uncertain_significance",
    "Benign",
    "Likely benign",
    "Benign/Likely benign",
    "Conflicting_interpretations_of_pathogenicity",
)


@pytest.fixture
def ac_client(tmp_data_dir: Path) -> Generator[TestClient, None, None]:
    settings = Settings(data_dir=tmp_data_dir, wal_mode=False)

    ref_engine = sa.create_engine(f"sqlite:///{settings.reference_db_path}")
    reference_metadata.create_all(ref_engine)

    sample_db_path = tmp_data_dir / "samples" / "sample_1.db"
    sample_engine = sa.create_engine(f"sqlite:///{sample_db_path}")
    create_sample_tables(sample_engine)

    with ref_engine.begin() as conn:
        conn.execute(
            samples.insert().values(
                id=1,
                name="Test Sample",
                db_path="samples/sample_1.db",
                file_format="v5",
                file_hash="abc123",
            )
        )

    with sample_engine.begin() as conn:
        for rsid, (gene, sig, popmax, _band) in _SEED.items():
            conn.execute(
                findings.insert().values(
                    module="cancer",
                    category="monogenic_variant",
                    evidence_level=4,
                    gene_symbol=gene,
                    rsid=rsid,
                    finding_text=f"{gene} {rsid} — {sig}",
                    clinvar_significance=sig,
                )
            )
            if popmax is not None:
                conn.execute(
                    annotated_variants.insert().values(
                        rsid=rsid,
                        chrom="1",
                        pos=1000,
                        clinvar_significance=sig,
                        clinvar_accession="VCV000000001",
                        gnomad_af_popmax=popmax,
                    )
                )
        # Non-P/LP findings that must never receive a reliability flag — the
        # endpoint is strictly scoped to ClinVar Pathogenic/Likely-pathogenic.
        for sig in _NON_PLP_SIGNIFICANCES:
            conn.execute(
                findings.insert().values(
                    module="cancer",
                    category="monogenic_variant",
                    evidence_level=1,
                    gene_symbol="NEGCTL",
                    rsid="rs9999003",
                    finding_text=f"NEGCTL rs9999003 — {sig}",
                    clinvar_significance=sig,
                )
            )

    ref_engine.dispose()
    sample_engine.dispose()

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


# ── Tests ────────────────────────────────────────────────────────────


class TestArrayConfidenceEndpoint:
    def test_only_pathogenic_findings_flagged(self, ac_client: TestClient) -> None:
        resp = ac_client.get("/api/analysis/array-confidence?sample_id=1")
        assert resp.status_code == 200
        data = resp.json()
        # Only the four P/LP findings; every non-P/LP code is excluded.
        assert len(data) == len(_SEED)
        assert {d["rsid"] for d in data} == set(_SEED)

    def test_non_pathogenic_significances_excluded(self, ac_client: TestClient) -> None:
        # Benign / VUS / Conflicting / etc. must never appear, not just VUS.
        data = ac_client.get("/api/analysis/array-confidence?sample_id=1").json()
        returned_sigs = {d["clinvar_significance"] for d in data}
        assert returned_sigs.isdisjoint(_NON_PLP_SIGNIFICANCES)
        assert "rs9999003" not in {d["rsid"] for d in data}

    def test_reliability_bands_match_frequency(self, ac_client: TestClient) -> None:
        resp = ac_client.get("/api/analysis/array-confidence?sample_id=1")
        by_rsid = {d["rsid"]: d for d in resp.json()}
        for rsid, (_gene, _sig, _popmax, expected_band) in _SEED.items():
            assert by_rsid[rsid]["reliability"] == expected_band

    def test_high_band_does_not_recommend_confirmation(self, ac_client: TestClient) -> None:
        by_rsid = {
            d["rsid"]: d
            for d in ac_client.get("/api/analysis/array-confidence?sample_id=1").json()
        }
        assert by_rsid["rs1800562"]["confirm_in_clia_recommended"] is False
        assert by_rsid["rs80357906"]["confirm_in_clia_recommended"] is True

    def test_clinvar_pathogenic_is_never_novel(self, ac_client: TestClient) -> None:
        # A ClinVar P/LP variant is, by definition, catalogued — never "novel".
        data = ac_client.get("/api/analysis/array-confidence?sample_id=1").json()
        assert all(d["is_novel"] is False for d in data)

    def test_unknown_band_when_no_annotation_row(self, ac_client: TestClient) -> None:
        by_rsid = {
            d["rsid"]: d
            for d in ac_client.get("/api/analysis/array-confidence?sample_id=1").json()
        }
        assert by_rsid["rs9999002"]["reliability"] == "unknown"
        assert by_rsid["rs9999002"]["gnomad_af_popmax"] is None
        assert by_rsid["rs9999002"]["confirm_in_clia_recommended"] is True

    def test_badge_carries_context_only_disclosure(self, ac_client: TestClient) -> None:
        from backend.analysis.array_confidence import WEEDON_PMID

        data = ac_client.get("/api/analysis/array-confidence?sample_id=1").json()
        for d in data:
            assert d["context_only"] is True
            assert d["note"]
            assert WEEDON_PMID in d["pmid_citations"]

    def test_locus_low_override_surfaces_via_api(self, ac_client: TestClient) -> None:
        """#636: a common-AF locus-specific weak spot (APOE rs429358) comes back
        rated ``locus_low`` with CLIA confirmation recommended, the override flag,
        the frequency band it would otherwise have had, and APOE citations."""
        from backend.analysis.array_confidence import APOE_ARRAY_RELIABILITY_PMIDS

        by_rsid = {
            d["rsid"]: d
            for d in ac_client.get("/api/analysis/array-confidence?sample_id=1").json()
        }
        apoe = by_rsid["rs429358"]
        assert apoe["reliability"] == "locus_low"
        assert apoe["confirm_in_clia_recommended"] is True
        assert apoe["locus_low_reliability"] is True
        # Frequency alone (AF 0.15) would have rated it "high" — recorded for transparency.
        assert apoe["frequency_band"] == "high"
        assert apoe["is_novel"] is False
        for pmid in APOE_ARRAY_RELIABILITY_PMIDS:
            assert pmid in apoe["pmid_citations"]

    def test_invalid_sample_returns_404(self, ac_client: TestClient) -> None:
        resp = ac_client.get("/api/analysis/array-confidence?sample_id=999")
        assert resp.status_code == 404

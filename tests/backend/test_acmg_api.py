"""API tests for the DRAFT ACMG/AMP endpoint (SW-F1 / #13).

GET /api/analysis/acmg?sample_id=N
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
from backend.db.tables import (
    annotated_variants,
    clingen_gene_validity,
    gnomad_gene_constraint,
    reference_metadata,
    samples,
)


@pytest.fixture
def tmp_data_dir(tmp_path: Path) -> Path:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "samples").mkdir()
    return data_dir


# rsid → (gene, consequence, af_popmax, revel, clinvar_sig)
_VARIANTS = {
    "rs_lof": ("LOFGENE", "stop_gained", None, None, None),
    "rs_validity_lof": ("VALIDGENE", "stop_gained", None, None, None),
    "rs_mis": ("MISGENE", "missense_variant", None, 0.95, None),
    "rs_common": ("MISGENE", "missense_variant", 0.06, 0.2, "Benign"),
    "rs_benign": ("MISGENE", "missense_variant", 0.005, 0.2, "Benign"),
    "rs_syn": ("MISGENE", "synonymous_variant", None, None, None),  # not a candidate
}


@pytest.fixture
def acmg_client(tmp_data_dir: Path) -> Generator[TestClient, None, None]:
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
        # LOFGENE: LoF-constrained (pLI 0.99). MISGENE: missense-constrained (mis_z 3.5).
        conn.execute(
            gnomad_gene_constraint.insert().values(
                gene_symbol="LOFGENE", loeuf=0.2, pli=0.99, mis_z=1.0
            )
        )
        conn.execute(
            gnomad_gene_constraint.insert().values(
                gene_symbol="MISGENE", loeuf=1.5, pli=0.1, mis_z=3.5
            )
        )
        conn.execute(
            clingen_gene_validity.insert().values(
                gene_symbol="VALIDGENE",
                disease_label="Valid disease relationship",
                classification="Definitive",
            )
        )

    with sample_engine.begin() as conn:
        for rsid, (gene, csq, popmax, revel, clinvar) in _VARIANTS.items():
            conn.execute(
                annotated_variants.insert().values(
                    rsid=rsid,
                    chrom="1",
                    pos=1000,
                    gene_symbol=gene,
                    consequence=csq,
                    gnomad_af_popmax=popmax,
                    revel=revel,
                    clinvar_significance=clinvar,
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


class TestAcmgEndpoint:
    def _by_rsid(self, client: TestClient) -> dict:
        data = client.get("/api/analysis/acmg?sample_id=1").json()
        return {v["rsid"]: v for v in data["variants"]}

    def test_synonymous_excluded_from_candidates(self, acmg_client: TestClient) -> None:
        by_rsid = self._by_rsid(acmg_client)
        assert "rs_syn" not in by_rsid
        assert set(by_rsid) == {
            "rs_lof",
            "rs_validity_lof",
            "rs_mis",
            "rs_common",
            "rs_benign",
        }

    def test_lof_constraint_alone_does_not_apply_pvs1(self, acmg_client: TestClient) -> None:
        v = self._by_rsid(acmg_client)["rs_lof"]
        assert v["acmg_classification"] == "Uncertain significance"
        assert v["points"] == 1
        assert {c["code"] for c in v["criteria"]} == {"PM2"}

    def test_gene_validity_alone_does_not_apply_pvs1(self, acmg_client: TestClient) -> None:
        v = self._by_rsid(acmg_client)["rs_validity_lof"]
        assert v["acmg_classification"] == "Uncertain significance"
        assert v["points"] == 1
        assert {c["code"] for c in v["criteria"]} == {"PM2"}

    def test_high_revel_missense_is_likely_pathogenic(self, acmg_client: TestClient) -> None:
        v = self._by_rsid(acmg_client)["rs_mis"]
        assert v["acmg_classification"] == "Likely pathogenic"
        codes = {c["code"] for c in v["criteria"]}
        assert {"PP3", "PP2", "PM2"} <= codes

    def test_common_variant_drafts_benign_alongside_clinvar(self, acmg_client: TestClient) -> None:
        v = self._by_rsid(acmg_client)["rs_common"]
        assert v["acmg_classification"] == "Benign"
        assert any(c["code"] == "BA1" for c in v["criteria"])
        # The original ClinVar significance travels with the draft (never overridden).
        assert v["clinvar_significance"] == "Benign"

    def test_every_variant_is_draft_with_disclosure(self, acmg_client: TestClient) -> None:
        from backend.analysis.acmg import CITATION_PMIDS

        data = acmg_client.get("/api/analysis/acmg?sample_id=1").json()
        assert data["truncated"] is False
        assert data["total_candidates"] == 5
        assert len(data["variants"]) == 5  # guard against a vacuous loop
        for v in data["variants"]:
            assert v["is_draft"] is True
            assert v["note"]
            assert set(CITATION_PMIDS) <= set(v["pmid_citations"])

    def test_invalid_sample_returns_404(self, acmg_client: TestClient) -> None:
        assert acmg_client.get("/api/analysis/acmg?sample_id=999").status_code == 404

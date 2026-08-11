"""The Gene Health endpoints must serve the panel that is loaded (#2021).

Panel prose — a cross-module note, a per-SNP recommendation — is persisted into
``findings`` when the module is scored, and sample staleness is tied to
reference-bundle versions, so a panel correction never prompts a re-analysis.
Both endpoints therefore resolve their stored rows at read time.
"""

from __future__ import annotations

import json
from collections.abc import Generator
from pathlib import Path
from unittest.mock import patch

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from backend.config import Settings
from backend.db.connection import DBRegistry, reset_registry
from backend.db.sample_schema import create_sample_tables
from backend.db.tables import findings, reference_metadata, samples

LEGACY_NOTE = (
    "FTO rs9939609 influences appetite regulation and macronutrient metabolism. "
    "See Nutrigenomics for dietary recommendations."
)
LEGACY_RECOMMENDATION = (
    "FTO is the most replicated obesity GWAS locus. Risk allele effect on BMI is "
    "attenuated by physical activity. Carriers benefit from regular exercise and "
    "mindful eating. See the Nutrigenomics module for dietary considerations "
    "related to FTO genotype."
)
PATHWAY = "Metabolic"

LEGACY_FINDINGS = [
    {
        "module": "gene_health",
        "category": "pathway_summary",
        "evidence_level": 3,
        "finding_text": "Metabolic — Moderate consideration",
        "pathway": PATHWAY,
        "pathway_level": "Moderate",
        "pmid_citations": json.dumps([]),
        "detail_json": json.dumps(
            {
                "pathway_id": "metabolic",
                "called_snps": 2,
                "total_snps": 2,
                "missing_snps": [],
                "snp_details": [
                    {
                        # A called Standard non-carrier: storage writes no
                        # snp_finding row for it, so it has no recommendation.
                        "rsid": "rs1801133",
                        "gene": "MTHFR",
                        "variant_name": "C677T",
                        "genotype": "GG",
                        "category": "Standard",
                        "effect_summary": "No C677T variant.",
                        "evidence_level": 3,
                    },
                    {
                        "rsid": "rs9939609",
                        "gene": "FTO",
                        "variant_name": "FTO intron 1",
                        "genotype": "AT",
                        "category": "Moderate",
                        "effect_summary": "One copy of FTO risk allele.",
                        "evidence_level": 3,
                    },
                ],
            }
        ),
    },
    {
        "module": "gene_health",
        "category": "snp_finding",
        "evidence_level": 3,
        "gene_symbol": "FTO",
        "rsid": "rs9939609",
        "finding_text": "FTO FTO intron 1 (AT) — One copy of FTO risk allele.",
        "pathway": PATHWAY,
        "pathway_level": "Moderate",
        "pmid_citations": json.dumps(["17434869"]),
        "detail_json": json.dumps({"recommendation": LEGACY_RECOMMENDATION}),
    },
    {
        "module": "gene_health",
        "category": "cross_module",
        "evidence_level": 3,
        "gene_symbol": "FTO",
        "rsid": "rs9939609",
        "finding_text": f"FTO FTO intron 1 (AT) — {LEGACY_NOTE}",
        "pmid_citations": json.dumps([]),
        "detail_json": json.dumps(
            {
                "source_module": "gene_health",
                "target_module": "nutrigenomics",
                "cross_module_note": LEGACY_NOTE,
            }
        ),
    },
]


@pytest.fixture
def legacy_client(tmp_path: Path) -> Generator[tuple[TestClient, int], None, None]:
    data_dir = tmp_path / "data"
    (data_dir / "samples").mkdir(parents=True)
    settings = Settings(data_dir=data_dir, wal_mode=False)

    ref_engine = sa.create_engine(f"sqlite:///{settings.reference_db_path}")
    reference_metadata.create_all(ref_engine)
    with ref_engine.begin() as conn:
        sample_id = conn.execute(
            samples.insert().values(
                name="legacy",
                db_path="samples/sample_1.db",
                file_format="23andme_v5",
                file_hash="hash_legacy",
            )
        ).lastrowid
    ref_engine.dispose()

    sample_engine = sa.create_engine(f"sqlite:///{data_dir / 'samples' / 'sample_1.db'}")
    create_sample_tables(sample_engine)
    with sample_engine.begin() as conn:
        for finding in LEGACY_FINDINGS:
            conn.execute(findings.insert().values(**finding))
    sample_engine.dispose()

    with (
        patch("backend.main.get_settings", return_value=settings),
        patch("backend.db.connection.get_settings", return_value=settings),
    ):
        reset_registry()
        DBRegistry(settings)

        from backend.main import create_app

        with TestClient(create_app()) as client:
            yield client, sample_id

        reset_registry()


class TestLegacyGeneHealthRows:
    def test_pathways_card_is_retargeted_and_refreshed(
        self, legacy_client: tuple[TestClient, int]
    ) -> None:
        client, sample_id = legacy_client
        resp = client.get(f"/api/analysis/gene_health/pathways?sample_id={sample_id}")
        assert resp.status_code == 200
        cross = resp.json()["cross_module"]
        assert len(cross) == 1
        assert cross[0]["target_module"] == "metabolic"
        assert "See Nutrigenomics for dietary recommendations" not in cross[0]["finding_text"]

    def test_pathway_detail_recommendation_drops_the_nutrigenomics_promise(
        self, legacy_client: tuple[TestClient, int]
    ) -> None:
        """The detail panel reads the recommendation straight off the stored row."""
        client, sample_id = legacy_client
        resp = client.get(f"/api/analysis/gene_health/pathway/metabolic?sample_id={sample_id}")
        assert resp.status_code == 200
        snps = resp.json()["snp_details"]
        fto = [s for s in snps if s["rsid"] == "rs9939609"]
        assert len(fto) == 1
        recommendation = fto[0]["recommendation"] or ""
        assert "nutrigenomics" not in recommendation.lower()
        # The rest of the curated advice survives — this is a refresh, not a wipe.
        assert "attenuated by physical activity" in recommendation


class TestStandardGenotypeKeepsNoRecommendation:
    """A hom-ref result must not inherit the panel's carrier-facing advice.

    Storage writes no `snp_finding` row for a called Standard genotype, so the
    detail panel shows no recommendation. Refreshing panel prose unconditionally
    would have supplied one — carrier instructions, beside a result that says
    the variant is absent.
    """

    def test_standard_snp_has_no_recommendation(
        self, legacy_client: tuple[TestClient, int]
    ) -> None:
        client, sample_id = legacy_client
        resp = client.get(f"/api/analysis/gene_health/pathway/metabolic?sample_id={sample_id}")
        assert resp.status_code == 200
        snps = {s["rsid"]: s for s in resp.json()["snp_details"]}
        assert snps["rs1801133"]["category"] == "Standard"
        assert not snps["rs1801133"]["recommendation"]
        # ...while the carrier beside it still gets its refreshed one.
        assert snps["rs9939609"]["recommendation"]

"""Tests for Gene Allergy & Immune Sensitivities findings API (P3-60).

Covers:
  - GET /api/analysis/allergy/pathways?sample_id=N — All pathway results
  - GET /api/analysis/allergy/pathway/{id}?sample_id=N — Single pathway detail
  - POST /api/analysis/allergy/run?sample_id=N — Run scoring
  - Celiac combined assessment in pathways response
  - Histamine combined assessment in pathways response
  - HLA proxy lookup data in pathway detail
  - Cross-module findings in pathways response
  - Missing sample returns 404
  - Empty findings returns empty list
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
from backend.db.tables import (
    cpic_guidelines,
    findings,
    raw_variants,
    reference_metadata,
    samples,
)

# ── Test data ────────────────────────────────────────────────────────

HLA_B5801_NEGATIVE_PROXY_CAVEAT = (
    "No HLA-B*58:01 tag-SNP allele was detected at rs9263726, but this proxy result "
    "does not exclude the HLA allele. Proxy LD varies by ancestry; use high-resolution "
    "HLA typing for clinical allopurinol decisions."
)

PATHWAY_SUMMARY_FINDINGS = [
    {
        "module": "allergy",
        "category": "pathway_summary",
        "evidence_level": 2,
        "gene_symbol": None,
        "rsid": None,
        "finding_text": "Atopic Conditions — Elevated consideration",
        "pathway": "Atopic Conditions",
        "pathway_level": "Elevated",
        "pmid_citations": json.dumps(["17611496", "20860503", "25635124", "32841424"]),
        "detail_json": json.dumps(
            {
                "pathway_id": "atopic_conditions",
                "called_snps": 2,
                "total_snps": 3,
                "missing_snps": ["rs324011"],
                "snp_details": [
                    {
                        "rsid": "rs20541",
                        "gene": "IL13",
                        "variant_name": "R130Q",
                        "genotype": "GA",
                        "category": "Moderate",
                        "effect_summary": "One copy of the R130Q variant.",
                        "evidence_level": 2,
                        "hla_proxy": None,
                        "coverage_note": None,
                    },
                    {
                        "rsid": "rs8076131",
                        "gene": "ORMDL3",
                        "variant_name": "ORMDL3 intergenic",
                        "genotype": "AA",
                        "category": "Elevated",
                        "effect_summary": "Homozygous A risk allele.",
                        "evidence_level": 2,
                        "hla_proxy": None,
                        "coverage_note": None,
                    },
                ],
            }
        ),
    },
    {
        "module": "allergy",
        "category": "pathway_summary",
        "evidence_level": 4,
        "gene_symbol": None,
        "rsid": None,
        "finding_text": "Drug Hypersensitivity — Elevated consideration",
        "pathway": "Drug Hypersensitivity",
        "pathway_level": "Elevated",
        "pmid_citations": json.dumps(["18256392"]),
        "detail_json": json.dumps(
            {
                "pathway_id": "drug_hypersensitivity",
                "called_snps": 2,
                "total_snps": 4,
                "missing_snps": ["rs144012689", "rs1061235"],
                "snp_details": [
                    {
                        "rsid": "rs2395029",
                        "gene": "HLA-B",
                        "variant_name": "HLA-B*57:01 proxy",
                        "genotype": "TG",
                        "category": "Elevated",
                        "effect_summary": "Carrier of HLA-B*57:01 proxy allele.",
                        "evidence_level": 4,
                        "hla_proxy": {
                            "hla_allele": "HLA-B*57:01",
                            "r_squared_eur": 1.0,
                            "clinical_grade": True,
                            "confirmatory_test_required": True,
                        },
                        "hla_proxy_lookup": {
                            "hla_allele": "HLA-B*57:01",
                            "r_squared_by_pop": {"EUR": 1.0},
                            "clinical_context": "Abacavir hypersensitivity",
                        },
                        "hla_proxy_caveat": "Confirmatory HLA typing required.",
                        "coverage_note": None,
                    },
                    {
                        "rsid": "rs9263726",
                        "gene": "HLA-B",
                        "variant_name": "HLA-B*58:01 proxy",
                        "genotype": "CC",
                        "category": "Standard",
                        "effect_summary": (
                            "No rs9263726 HLA-B*58:01 proxy allele detected. "
                            "This tag-SNP result does not rule out HLA-B*58:01."
                        ),
                        "evidence_level": 4,
                        "hla_proxy": {
                            "hla_allele": "HLA-B*58:01",
                            "r_squared_by_population": {
                                "Han Chinese": 0.886,
                                "Tibetan": 0.606,
                                "Hui": 0.622,
                            },
                            "clinical_grade": False,
                            "confirmatory_test_required": True,
                        },
                        "hla_proxy_lookup": {
                            "hla_allele": "HLA-B*58:01",
                            "r_squared_by_pop": {
                                "Han Chinese": 0.886,
                                "Tibetan": 0.606,
                                "Hui": 0.622,
                            },
                            "clinical_context": "Allopurinol hypersensitivity (SJS/TEN)",
                        },
                        "hla_proxy_caveat": HLA_B5801_NEGATIVE_PROXY_CAVEAT,
                        "coverage_note": None,
                    },
                ],
                "hla_proxy_lookup": {
                    "rs2395029": {
                        "hla_allele": "HLA-B*57:01",
                        "r_squared_by_pop": {"EUR": 1.0},
                        "clinical_context": "Abacavir hypersensitivity",
                    },
                    "rs9263726": {
                        "hla_allele": "HLA-B*58:01",
                        "r_squared_by_pop": {
                            "Han Chinese": 0.886,
                            "Tibetan": 0.606,
                            "Hui": 0.622,
                        },
                        "clinical_context": "Allopurinol hypersensitivity (SJS/TEN)",
                    },
                },
            }
        ),
    },
    {
        "module": "allergy",
        "category": "pathway_summary",
        "evidence_level": 3,
        "gene_symbol": None,
        "rsid": None,
        "finding_text": "Food Sensitivity — Standard (no variants of concern)",
        "pathway": "Food Sensitivity",
        "pathway_level": "Standard",
        "pmid_citations": json.dumps([]),
        "detail_json": json.dumps(
            {
                "pathway_id": "food_sensitivity",
                "called_snps": 0,
                "total_snps": 2,
                "missing_snps": ["rs2187668", "rs7454108"],
                "snp_details": [],
            }
        ),
    },
    {
        "module": "allergy",
        "category": "pathway_summary",
        "evidence_level": 1,
        "gene_symbol": None,
        "rsid": None,
        "finding_text": "Histamine Metabolism — Standard (no variants of concern)",
        "pathway": "Histamine Metabolism",
        "pathway_level": "Standard",
        "pmid_citations": json.dumps([]),
        "detail_json": json.dumps(
            {
                "pathway_id": "histamine_metabolism",
                "called_snps": 0,
                "total_snps": 2,
                "missing_snps": ["rs10156191", "rs11558538"],
                "snp_details": [],
            }
        ),
    },
]

SNP_FINDING = {
    "module": "allergy",
    "category": "snp_finding",
    "evidence_level": 2,
    "gene_symbol": "IL13",
    "rsid": "rs20541",
    "finding_text": "IL13 R130Q (GA) — One copy of the R130Q variant.",
    "pathway": "Atopic Conditions",
    "pathway_level": "Moderate",
    "pmid_citations": json.dumps(["15711639"]),
    "detail_json": json.dumps(
        {
            "variant_name": "R130Q",
            "genotype": "GA",
            "recommendation": "IL13 R130Q is a well-replicated GWAS hit.",
        }
    ),
}

CELIAC_COMBINED_FINDING = {
    "module": "allergy",
    "category": "celiac_combined",
    "evidence_level": 3,
    "gene_symbol": None,
    "rsid": None,
    "finding_text": (
        "Celiac Disease Risk Assessment — Low Celiac Risk. Neither DQ2 nor DQ8 detected."
    ),
    "pathway": "Food Sensitivity",
    "pathway_level": None,
    "pmid_citations": json.dumps(["18311140", "18509540", "20190752"]),
    "detail_json": json.dumps(
        {
            "state": "neither",
            "label": "Low Celiac Risk",
            "dq2_genotype": "CC",
            "dq8_genotype": "CC",
        }
    ),
}

HISTAMINE_COMBINED_FINDING = {
    "module": "allergy",
    "category": "histamine_combined",
    "evidence_level": 1,
    "gene_symbol": None,
    "rsid": None,
    "finding_text": (
        "Histamine Metabolism — No panel-tracked AOC1/HNMT risk genotypes "
        "detected. This limited candidate-gene panel does not rule out reduced "
        "DAO activity or histamine intolerance, which depend on additional "
        "variants and non-genetic factors (diet, medications, clinical phenotype)."
    ),
    "pathway": "Histamine Metabolism",
    "pathway_level": None,
    "pmid_citations": json.dumps(["15046637"]),
    "detail_json": json.dumps(
        {
            "aoc1_genotype": None,
            "hnmt_genotype": None,
            "aoc1_category": "Standard",
            "hnmt_category": "Standard",
            "de_emphasize": True,
        }
    ),
}

CROSS_MODULE_FINDING = {
    "module": "allergy",
    "category": "cross_module",
    "evidence_level": 4,
    "gene_symbol": "HLA-B",
    "rsid": "rs2395029",
    "finding_text": "HLA-B*57:01 proxy (rs2395029, TG) — See PGx for prescribing guidance.",
    "pathway": None,
    "pathway_level": None,
    "pmid_citations": json.dumps(["18256392"]),
    "detail_json": json.dumps(
        {
            "source_module": "allergy",
            "target_module": "pharmacogenomics",
            "genotype": "TG",
            "cross_module_note": "See PGx for prescribing guidance.",
        }
    ),
}


# ── Fixtures ─────────────────────────────────────────────────────────


@pytest.fixture()
def _env(tmp_path: Path) -> Generator[tuple[sa.Engine, sa.Engine], None, None]:
    """Set up a temporary DB environment and FastAPI test client."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "samples").mkdir()

    # Reference DB — must exist at the path Settings will look for
    ref_db = data_dir / "reference.db"
    ref_engine = sa.create_engine(f"sqlite:///{ref_db}")
    reference_metadata.create_all(ref_engine)

    # Register a sample
    with ref_engine.begin() as conn:
        conn.execute(
            sa.insert(samples),
            [
                {
                    "name": "test_sample",
                    "db_path": "samples/sample_1.db",
                    "file_format": "23andme_v5",
                    "file_hash": "abc123",
                }
            ],
        )

    # Sample DB
    sample_db = data_dir / "samples" / "sample_1.db"
    sample_engine = sa.create_engine(f"sqlite:///{sample_db}")
    create_sample_tables(sample_engine)

    # Create settings + registry
    settings = Settings(data_dir=data_dir)
    reset_registry()
    registry = DBRegistry(settings)

    with (
        patch("backend.api.dependencies.get_registry", return_value=registry),
        patch("backend.api.routes.allergy.get_registry", return_value=registry),
        patch("backend.services.staleness.get_registry", return_value=registry),
    ):
        yield sample_engine, ref_engine

    reset_registry()


@pytest.fixture()
def client(_env: tuple[sa.Engine, sa.Engine]) -> TestClient:
    """Create a test client for the allergy API."""
    from fastapi import FastAPI

    from backend.api.routes.allergy import router

    app = FastAPI()
    app.include_router(router, prefix="/api")
    return TestClient(app)


@pytest.fixture()
def seeded_client(
    _env: tuple[sa.Engine, sa.Engine],
) -> TestClient:
    """Create a test client with pre-seeded allergy findings."""
    sample_engine, _ = _env

    all_findings = PATHWAY_SUMMARY_FINDINGS + [
        SNP_FINDING,
        CELIAC_COMBINED_FINDING,
        HISTAMINE_COMBINED_FINDING,
        CROSS_MODULE_FINDING,
    ]
    with sample_engine.begin() as conn:
        conn.execute(sa.insert(findings), all_findings)

    from fastapi import FastAPI

    from backend.api.routes.allergy import router

    app = FastAPI()
    app.include_router(router, prefix="/api")
    return TestClient(app)


# ── Endpoint tests ───────────────────────────────────────────────────


class TestListPathways:
    def test_sample_gate_uses_fixture_registry_not_global_singleton(
        self,
        seeded_client: TestClient,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A prior test's singleton must not decide this fixture's sample gate."""
        from backend.db import connection

        unrelated_data_dir = tmp_path / "unrelated_global_registry"
        unrelated_data_dir.mkdir()
        (unrelated_data_dir / "samples").mkdir()
        unrelated_engine = sa.create_engine(f"sqlite:///{unrelated_data_dir / 'reference.db'}")
        reference_metadata.create_all(unrelated_engine)
        unrelated_engine.dispose()
        unrelated_registry = DBRegistry(Settings(data_dir=unrelated_data_dir))

        try:
            monkeypatch.setattr(connection, "_registry", unrelated_registry)
            response = seeded_client.get("/api/analysis/allergy/pathways?sample_id=1")
            assert response.status_code == 200
        finally:
            unrelated_registry.dispose_all()

    def test_returns_pathways(self, seeded_client: TestClient) -> None:
        resp = seeded_client.get("/api/analysis/allergy/pathways?sample_id=1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 4
        assert len(data["items"]) == 4

    def test_pathway_fields(self, seeded_client: TestClient) -> None:
        resp = seeded_client.get("/api/analysis/allergy/pathways?sample_id=1")
        data = resp.json()
        item = next(i for i in data["items"] if i["pathway_id"] == "atopic_conditions")
        assert item["level"] == "Elevated"
        assert item["evidence_level"] == 2
        assert item["called_snps"] == 2
        assert item["total_snps"] == 3
        food = next(i for i in data["items"] if i["pathway_id"] == "food_sensitivity")
        assert food["missing_snps"] == ["rs2187668", "rs7454108"]
        assert "rs7775228" not in food["missing_snps"]

    def test_celiac_combined_in_response(self, seeded_client: TestClient) -> None:
        resp = seeded_client.get("/api/analysis/allergy/pathways?sample_id=1")
        data = resp.json()
        assert data["celiac_combined"] is not None
        assert data["celiac_combined"]["state"] == "neither"
        assert data["celiac_combined"]["label"] == "Low Celiac Risk"

    def test_histamine_combined_in_response(self, seeded_client: TestClient) -> None:
        resp = seeded_client.get("/api/analysis/allergy/pathways?sample_id=1")
        data = resp.json()
        assert data["histamine_combined"] is not None
        assert data["histamine_combined"]["de_emphasize"] is True

    def test_cross_module_in_response(self, seeded_client: TestClient) -> None:
        resp = seeded_client.get("/api/analysis/allergy/pathways?sample_id=1")
        data = resp.json()
        assert len(data["cross_module"]) >= 1
        cross = data["cross_module"][0]
        assert cross["target_module"] == "pharmacogenomics"

    def test_empty_findings_returns_empty(self, client: TestClient) -> None:
        resp = client.get("/api/analysis/allergy/pathways?sample_id=1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0
        assert data["items"] == []

    def test_missing_sample_404(self, client: TestClient) -> None:
        # Hermetic (#414/#453): require_fresh_sample now checks existence before
        # staleness, so a missing sample is 404 deterministically — independent of
        # the dev box's bundle baseline — without needing to pin is_sample_stale.
        # (#453 resolved the cross-route contract: 404-for-missing everywhere,
        # including the merge/migrate routes that previously answered 423.)
        resp = client.get("/api/analysis/allergy/pathways?sample_id=999")
        assert resp.status_code == 404

    def test_hla_proxy_lookup_in_pathway(self, seeded_client: TestClient) -> None:
        resp = seeded_client.get("/api/analysis/allergy/pathways?sample_id=1")
        data = resp.json()
        drug = next(i for i in data["items"] if i["pathway_id"] == "drug_hypersensitivity")
        assert drug["hla_proxy_lookup"] is not None
        assert "rs2395029" in drug["hla_proxy_lookup"]


class TestPathwayDetail:
    def test_pathway_detail(self, seeded_client: TestClient) -> None:
        resp = seeded_client.get("/api/analysis/allergy/pathway/atopic_conditions?sample_id=1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["pathway_id"] == "atopic_conditions"
        assert data["level"] == "Elevated"
        assert len(data["snp_details"]) == 2
        ormdl3 = next(d for d in data["snp_details"] if d["rsid"] == "rs8076131")
        assert ormdl3["genotype"] == "AA"
        assert ormdl3["category"] == "Elevated"
        assert ormdl3["effect_summary"] == "Homozygous A risk allele."
        assert "No risk allele" not in ormdl3["effect_summary"]

    def test_drug_hypersensitivity_detail(self, seeded_client: TestClient) -> None:
        resp = seeded_client.get("/api/analysis/allergy/pathway/drug_hypersensitivity?sample_id=1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["level"] == "Elevated"
        assert data["hla_proxy_lookup"] is not None
        hla_b5701_detail = next(d for d in data["snp_details"] if d["rsid"] == "rs2395029")
        assert hla_b5701_detail["hla_proxy_lookup"]["r_squared_by_pop"] == {"EUR": 1.0}
        assert hla_b5701_detail["hla_proxy_caveat"] == "Confirmatory HLA typing required."
        hla_b5801_detail = next(d for d in data["snp_details"] if d["rsid"] == "rs9263726")
        assert hla_b5801_detail["hla_proxy_caveat"] == HLA_B5801_NEGATIVE_PROXY_CAVEAT
        # Source-matched population-specific LD (Zhang 2018), not fabricated bins (#333).
        by_pop = hla_b5801_detail["hla_proxy_lookup"]["r_squared_by_pop"]
        assert by_pop["Tibetan"] == 0.606
        assert "AFR" not in by_pop and "EUR" not in by_pop

    def test_missing_pathway_404(self, seeded_client: TestClient) -> None:
        resp = seeded_client.get("/api/analysis/allergy/pathway/nonexistent?sample_id=1")
        assert resp.status_code == 404


class TestRunScoring:
    def test_run_scoring(
        self,
        _env: tuple[sa.Engine, sa.Engine],
    ) -> None:
        """POST /run triggers scoring and returns counts."""
        sample_engine, ref_engine = _env

        # Seed variants
        with sample_engine.begin() as conn:
            conn.execute(
                sa.insert(raw_variants),
                [
                    {"rsid": "rs20541", "chrom": "5", "pos": 131995964, "genotype": "GA"},
                    {"rsid": "rs2395029", "chrom": "6", "pos": 31431272, "genotype": "TG"},
                ],
            )

        from fastapi import FastAPI

        from backend.api.routes.allergy import router

        app = FastAPI()
        app.include_router(router, prefix="/api")
        client = TestClient(app)

        resp = client.post("/api/analysis/allergy/run?sample_id=1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["findings_count"] > 0
        assert data["pathways_scored"] == 4


# ── PGx handoff availability at read time (#2020) ─────────────────────


LEGACY_NOTE = (
    "Abacavir/HLA-B*57:01 finding cross-links bi-directionally with the "
    "Pharmacogenomics module. See PGx for prescribing guidance."
)


def _cross_module_finding(
    drug: str | None = "abacavir",
    note: str = "Abacavir/HLA-B*57:01 drug-safety finding.",
) -> dict:
    """An allergy cross-module drug alert, optionally naming its drug."""
    detail: dict = {
        "source_module": "allergy",
        "target_module": "pharmacogenomics",
        "genotype": "TG",
        "cross_module_note": note,
    }
    if drug is not None:
        detail["drug"] = drug
    return {
        **CROSS_MODULE_FINDING,
        "finding_text": f"HLA-B*57:01 proxy (rs2395029, TG) — {note}",
        "detail_json": json.dumps(detail),
    }


def _pgx_prescribing_alert(drug: str, gene: str = "HLA-B") -> dict:
    """A presentable Pharmacogenomics prescribing alert for ``gene``/``drug``."""
    return {
        "module": "pharmacogenomics",
        "category": "prescribing_alert",
        "evidence_level": 4,
        "gene_symbol": gene,
        "rsid": None,
        "finding_text": f"{drug} prescribing alert.",
        "diplotype": "*57:01/*57:01",
        "metabolizer_status": "Positive",
        "drug": drug,
        "pathway": None,
        "pathway_level": None,
        "pmid_citations": json.dumps(["18256392"]),
        "detail_json": json.dumps(
            {
                "recommendation": "Do not prescribe.",
                "classification": "A",
                "guideline_url": "https://example.invalid/guideline",
            }
        ),
    }


def _seed_guideline(ref_engine: sa.Engine, gene: str, drug: str) -> None:
    with ref_engine.begin() as conn:
        conn.execute(
            sa.insert(cpic_guidelines),
            {
                "gene": gene,
                "drug": drug,
                "phenotype": "Positive",
                "activity_score": None,
                "recommendation": "Do not prescribe.",
                "classification": "A",
                "guideline_url": "https://example.invalid/guideline",
            },
        )


def _client_with(sample_engine: sa.Engine, rows: list[dict]) -> TestClient:
    with sample_engine.begin() as conn:
        # One statement per row: an executemany binds the *first* mapping's keys,
        # which would silently drop `drug` from the pharmacogenomics row and make
        # the positive case unreachable for reasons unrelated to the gate.
        for row in rows:
            conn.execute(sa.insert(findings), row)

    from fastapi import FastAPI

    from backend.api.routes.allergy import router

    app = FastAPI()
    app.include_router(router, prefix="/api")
    return TestClient(app)


class TestPGxHandoffAvailability:
    """The PGx handoff is decided per request, against live capability (#2020).

    Two conditions, both evaluated now rather than when the sample was scored:
    the module must hold a guideline for this alert's *gene* and drug that it
    can actually call, and this sample must have a presentable PGx result for
    that same pair. ``drug_lookup`` renders a guideline with no sample finding
    as ``not_assessed``, so neither condition alone is enough.
    """

    def _cross_module(self, client: TestClient) -> dict:
        resp = client.get("/api/analysis/allergy/pathways?sample_id=1")
        assert resp.status_code == 200
        cross = resp.json()["cross_module"]
        assert cross, "cross-module drug alert must be present"
        return cross[0]

    def _make_hla_callable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from backend.annotation.cpic import CPIC_GENES

        monkeypatch.setattr("backend.analysis.allergy.CPIC_GENES", CPIC_GENES | {"HLA-B"})

    def test_offered_when_module_and_sample_both_cover_the_pair(
        self, _env: tuple[sa.Engine, sa.Engine], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sample_engine, ref_engine = _env
        self._make_hla_callable(monkeypatch)
        _seed_guideline(ref_engine, "HLA-B", "abacavir")
        client = _client_with(
            sample_engine,
            [
                *PATHWAY_SUMMARY_FINDINGS,
                _cross_module_finding(),
                _pgx_prescribing_alert("abacavir"),
            ],
        )
        assert self._cross_module(client)["pgx_guidance_available"] is True

    def test_withheld_when_the_module_cannot_call_the_gene(
        self, _env: tuple[sa.Engine, sa.Engine]
    ) -> None:
        """Today's state: an HLA-B guideline exists but PGx cannot call HLA-B."""
        sample_engine, ref_engine = _env
        _seed_guideline(ref_engine, "HLA-B", "abacavir")
        client = _client_with(
            sample_engine,
            [
                *PATHWAY_SUMMARY_FINDINGS,
                _cross_module_finding(),
                _pgx_prescribing_alert("abacavir"),
            ],
        )
        assert self._cross_module(client)["pgx_guidance_available"] is False

    def test_withheld_when_the_sample_has_no_pgx_result(
        self, _env: tuple[sa.Engine, sa.Engine], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Covered by the module, not assessed for this sample."""
        sample_engine, ref_engine = _env
        self._make_hla_callable(monkeypatch)
        _seed_guideline(ref_engine, "HLA-B", "abacavir")
        client = _client_with(sample_engine, [*PATHWAY_SUMMARY_FINDINGS, _cross_module_finding()])
        assert self._cross_module(client)["pgx_guidance_available"] is False

    def test_withheld_when_the_pgx_result_is_for_another_drug(
        self, _env: tuple[sa.Engine, sa.Engine], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sample_engine, ref_engine = _env
        self._make_hla_callable(monkeypatch)
        _seed_guideline(ref_engine, "HLA-B", "abacavir")
        client = _client_with(
            sample_engine,
            [
                *PATHWAY_SUMMARY_FINDINGS,
                _cross_module_finding(),
                _pgx_prescribing_alert("warfarin"),
            ],
        )
        assert self._cross_module(client)["pgx_guidance_available"] is False

    def test_withheld_when_the_pgx_result_is_for_another_gene(
        self, _env: tuple[sa.Engine, sa.Engine], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A same-drug result on an unrelated gene does not interpret this alert.

        The alert is about HLA-B*57:01; a CYP2C9/abacavir result says nothing
        about it, so the handoff stays withheld.
        """
        sample_engine, ref_engine = _env
        self._make_hla_callable(monkeypatch)
        _seed_guideline(ref_engine, "HLA-B", "abacavir")
        _seed_guideline(ref_engine, "CYP2C9", "abacavir")
        client = _client_with(
            sample_engine,
            [
                *PATHWAY_SUMMARY_FINDINGS,
                _cross_module_finding(),
                _pgx_prescribing_alert("abacavir", gene="CYP2C9"),
            ],
        )
        assert self._cross_module(client)["pgx_guidance_available"] is False

    def test_legacy_finding_without_a_drug_is_gated_the_same_way(
        self, _env: tuple[sa.Engine, sa.Engine], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A finding stored before #2020 records no drug and still gates correctly.

        The pair is read from the panel by rsid — stable metadata every
        cross-module finding already carries — so an existing sample needs no
        backfill and no Allergy re-score to follow the destination's capability.
        """
        sample_engine, ref_engine = _env
        legacy = _cross_module_finding(drug=None)
        client = _client_with(
            sample_engine,
            [*PATHWAY_SUMMARY_FINDINGS, legacy, _pgx_prescribing_alert("abacavir")],
        )
        # Today's state: PGx cannot call HLA-B, so the handoff is withheld.
        assert self._cross_module(client)["pgx_guidance_available"] is False

        # Extend PGx and land the guideline — the same legacy row now qualifies.
        self._make_hla_callable(monkeypatch)
        _seed_guideline(ref_engine, "HLA-B", "abacavir")
        assert self._cross_module(client)["pgx_guidance_available"] is True

    def test_unknown_rsid_falls_back_to_the_recorded_drug(
        self, _env: tuple[sa.Engine, sa.Engine], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A finding for an rsid the panel no longer declares uses its own record."""
        sample_engine, ref_engine = _env
        self._make_hla_callable(monkeypatch)
        _seed_guideline(ref_engine, "HLA-B", "abacavir")
        retired = {**_cross_module_finding(), "rsid": "rs00000000"}
        client = _client_with(
            sample_engine,
            [*PATHWAY_SUMMARY_FINDINGS, retired, _pgx_prescribing_alert("abacavir")],
        )
        assert self._cross_module(client)["pgx_guidance_available"] is True

    def test_capability_added_after_scoring_needs_no_allergy_rescore(
        self, _env: tuple[sa.Engine, sa.Engine], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The production sequence: score Allergy, then extend and run PGx.

        The allergy finding is stored while PGx covers nothing, so the handoff
        is withheld. A release that teaches PGx to call HLA-B, a reference
        update carrying the guideline, and a PGx run then restore the link on
        the very next request — with the stored allergy finding untouched.
        """
        sample_engine, ref_engine = _env
        client = _client_with(sample_engine, [*PATHWAY_SUMMARY_FINDINGS, _cross_module_finding()])
        assert self._cross_module(client)["pgx_guidance_available"] is False

        self._make_hla_callable(monkeypatch)
        _seed_guideline(ref_engine, "HLA-B", "abacavir")
        with sample_engine.begin() as conn:
            conn.execute(sa.insert(findings), _pgx_prescribing_alert("abacavir"))

        assert self._cross_module(client)["pgx_guidance_available"] is True


class TestLegacyHandoffProse:
    """A finding scored before #2020 must not still point the user at PGx.

    Withholding the link is not enough on its own: the stored ``finding_text``
    carries the retired "See PGx for prescribing guidance" sentence, which
    directs the user to the same unsupported destination. The note is refreshed
    from the panel at read time so an existing sample needs no re-score.
    """

    def _cross_module(self, client: TestClient) -> dict:
        resp = client.get("/api/analysis/allergy/pathways?sample_id=1")
        assert resp.status_code == 200
        cross = resp.json()["cross_module"]
        assert cross
        return cross[0]

    def test_retired_note_is_replaced_by_the_current_panel_note(
        self, _env: tuple[sa.Engine, sa.Engine]
    ) -> None:
        sample_engine, _ = _env
        client = _client_with(
            sample_engine,
            [*PATHWAY_SUMMARY_FINDINGS, _cross_module_finding(drug=None, note=LEGACY_NOTE)],
        )

        text = self._cross_module(client)["finding_text"]
        assert "See PGx for prescribing guidance" not in text
        assert "cross-links" not in text
        # The panel's current wording, and the actionable instruction with it.
        assert "Confirmatory high-resolution HLA-B*57:01 typing" in text
        # The sample-specific prefix is preserved verbatim.
        assert text.startswith("HLA-B*57:01 proxy (rs2395029, TG) — ")

    def test_current_note_is_returned_unchanged(self, _env: tuple[sa.Engine, sa.Engine]) -> None:
        """A freshly scored finding already carries the panel note."""
        sample_engine, _ = _env
        current = (
            "Abacavir/HLA-B*57:01 drug-safety finding. Confirmatory "
            "high-resolution HLA-B*57:01 typing is required before any abacavir "
            "prescribing decision."
        )
        client = _client_with(
            sample_engine, [*PATHWAY_SUMMARY_FINDINGS, _cross_module_finding(note=current)]
        )
        assert self._cross_module(client)["finding_text"].endswith(current)

    def test_unrecognised_text_is_left_alone(self, _env: tuple[sa.Engine, sa.Engine]) -> None:
        """Never rewrite text that does not end in the note the finding recorded."""
        sample_engine, _ = _env
        row = _cross_module_finding(drug=None, note=LEGACY_NOTE)
        row["finding_text"] = "Hand-edited text that ends differently."
        client = _client_with(sample_engine, [*PATHWAY_SUMMARY_FINDINGS, row])
        assert (
            self._cross_module(client)["finding_text"] == "Hand-edited text that ends differently."
        )

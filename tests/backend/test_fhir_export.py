"""Tests for FHIR R4 DiagnosticReport export (P4-12a / T4-22f).

Validates that the FHIR export endpoint produces a valid FHIR R4 Bundle
with DiagnosticReport and Observation resources.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from backend.config import Settings
from backend.db.connection import reset_registry
from backend.db.sample_schema import create_sample_tables
from backend.db.tables import annotated_variants, reference_metadata, samples

# ── Test data ────────────────────────────────────────────────────────

ANNOTATED_VARIANTS = [
    {
        "rsid": "rs429358",
        "chrom": "19",
        "pos": 44908684,
        "ref": "T",
        "alt": "C",
        "genotype": "TC",
        "zygosity": "het",
        "gene_symbol": "APOE",
        "consequence": "missense_variant",
        "hgvs_coding": "NM_000041.4:c.388T>C",
        "hgvs_protein": "NP_000032.1:p.Cys130Arg",
        "clinvar_significance": "risk_factor",
        "clinvar_review_stars": 3,
        "clinvar_accession": "VCV000017864",
        "gnomad_af_global": 0.15,
        "rare_flag": False,
        "cadd_phred": 23.5,
        "annotation_coverage": 0x1F,
        "evidence_conflict": False,
        "ensemble_pathogenic": False,
    },
    {
        "rsid": "rs80357906",
        "chrom": "17",
        "pos": 43091983,
        "ref": "CTC",
        "alt": "C",
        "genotype": "TC",
        "zygosity": "het",
        "gene_symbol": "BRCA1",
        "consequence": "frameshift_variant",
        "clinvar_significance": "Pathogenic",
        "clinvar_review_stars": 3,
        "clinvar_accession": "VCV000017661",
        "gnomad_af_global": 0.0001,
        "rare_flag": True,
        "ultra_rare_flag": True,
        "cadd_phred": 35.0,
        "revel": 0.95,
        "annotation_coverage": 0x1F,
        "evidence_conflict": False,
        "ensemble_pathogenic": True,
    },
    {
        "rsid": "rs1801133",
        "chrom": "1",
        "pos": 11856378,
        "ref": "G",
        "alt": "A",
        "genotype": "AG",
        "zygosity": "het",
        "gene_symbol": "MTHFR",
        "consequence": "missense_variant",
        "clinvar_significance": "drug_response",
        "clinvar_review_stars": 2,
        "clinvar_accession": "VCV000003520",
        "gnomad_af_global": 0.35,
        "rare_flag": False,
        "cadd_phred": 25.0,
        "annotation_coverage": 0x1F,
        "evidence_conflict": False,
        "ensemble_pathogenic": False,
    },
    {
        "rsid": "rs12913832",
        "chrom": "15",
        "pos": 28365618,
        "ref": "A",
        "alt": "G",
        "genotype": "GG",
        "zygosity": "hom_alt",
        "gene_symbol": "HERC2",
        "consequence": "intron_variant",
        "clinvar_significance": None,
        "gnomad_af_global": 0.50,
        "rare_flag": False,
        "annotation_coverage": 0x07,
        "evidence_conflict": False,
        "ensemble_pathogenic": False,
    },
]

_ALL_COLS = [col.name for col in annotated_variants.columns]


def _normalize(variant: dict) -> dict:
    """Fill missing columns with None."""
    return {k: variant.get(k) for k in _ALL_COLS}


# ── Fixtures ─────────────────────────────────────────────────────────


def _setup_client(tmp_data_dir: Path, variants: list[dict]):
    """Create a TestClient with annotated sample data."""
    settings = Settings(data_dir=tmp_data_dir, wal_mode=False)

    ref_engine = sa.create_engine(f"sqlite:///{settings.reference_db_path}")
    reference_metadata.create_all(ref_engine)
    with ref_engine.begin() as conn:
        result = conn.execute(
            samples.insert().values(
                name="Test Sample",
                db_path="samples/sample_1.db",
                file_format="23andme_v5",
                file_hash="abc123",
            )
        )
        sample_id = result.lastrowid
    ref_engine.dispose()

    sample_db_path = tmp_data_dir / "samples" / "sample_1.db"
    sample_engine = sa.create_engine(f"sqlite:///{sample_db_path}")
    create_sample_tables(sample_engine)
    if variants:
        normalized = [_normalize(v) for v in variants]
        with sample_engine.begin() as conn:
            conn.execute(annotated_variants.insert(), normalized)
    sample_engine.dispose()

    with (
        patch("backend.main.get_settings", return_value=settings),
        patch("backend.db.connection.get_settings", return_value=settings),
    ):
        reset_registry()
        from backend.main import create_app

        app = create_app()
        with TestClient(app) as tc:
            yield tc, sample_id
        reset_registry()


@pytest.fixture
def client(tmp_data_dir: Path):
    yield from _setup_client(tmp_data_dir, ANNOTATED_VARIANTS)


@pytest.fixture
def empty_client(tmp_data_dir: Path):
    yield from _setup_client(tmp_data_dir, [])


# ══════════════════════════════════════════════════════════════════════
# FHIR Bundle structure tests (T4-22f)
# ══════════════════════════════════════════════════════════════════════


class TestFhirBundleStructure:
    """POST /api/export/fhir produces a valid FHIR R4 Bundle."""

    def test_fhir_export_returns_200(self, client) -> None:
        tc, sid = client
        resp = tc.post(
            "/api/export/fhir",
            json={"sample_id": sid},
        )
        assert resp.status_code == 200

    def test_fhir_content_type(self, client) -> None:
        tc, sid = client
        resp = tc.post(
            "/api/export/fhir",
            json={"sample_id": sid},
        )
        assert "application/fhir+json" in resp.headers["content-type"]

    def test_fhir_content_disposition(self, client) -> None:
        tc, sid = client
        resp = tc.post(
            "/api/export/fhir",
            json={"sample_id": sid},
        )
        assert "attachment" in resp.headers["content-disposition"]
        assert ".fhir.json" in resp.headers["content-disposition"]

    def test_bundle_resource_type(self, client) -> None:
        tc, sid = client
        resp = tc.post("/api/export/fhir", json={"sample_id": sid})
        bundle = resp.json()
        assert bundle["resourceType"] == "Bundle"

    def test_bundle_type_is_collection(self, client) -> None:
        tc, sid = client
        resp = tc.post("/api/export/fhir", json={"sample_id": sid})
        bundle = resp.json()
        assert bundle["type"] == "collection"

    def test_bundle_has_id(self, client) -> None:
        tc, sid = client
        resp = tc.post("/api/export/fhir", json={"sample_id": sid})
        bundle = resp.json()
        assert "id" in bundle
        assert len(bundle["id"]) > 0

    def test_bundle_has_timestamp(self, client) -> None:
        tc, sid = client
        resp = tc.post("/api/export/fhir", json={"sample_id": sid})
        bundle = resp.json()
        assert "timestamp" in bundle

    def test_bundle_has_meta_profile(self, client) -> None:
        tc, sid = client
        resp = tc.post("/api/export/fhir", json={"sample_id": sid})
        bundle = resp.json()
        assert "meta" in bundle
        assert "profile" in bundle["meta"]
        assert any("genomics-reporting" in p for p in bundle["meta"]["profile"])


class TestFhirDiagnosticReport:
    """The first entry must be a DiagnosticReport resource."""

    def test_first_entry_is_diagnostic_report(self, client) -> None:
        tc, sid = client
        resp = tc.post("/api/export/fhir", json={"sample_id": sid})
        bundle = resp.json()
        first = bundle["entry"][0]["resource"]
        assert first["resourceType"] == "DiagnosticReport"

    def test_report_status_final(self, client) -> None:
        tc, sid = client
        resp = tc.post("/api/export/fhir", json={"sample_id": sid})
        report = resp.json()["entry"][0]["resource"]
        assert report["status"] == "final"

    def test_report_code_loinc(self, client) -> None:
        tc, sid = client
        resp = tc.post("/api/export/fhir", json={"sample_id": sid})
        report = resp.json()["entry"][0]["resource"]
        coding = report["code"]["coding"][0]
        assert coding["system"] == "http://loinc.org"
        assert coding["code"] == "81247-9"

    def test_report_has_subject(self, client) -> None:
        tc, sid = client
        resp = tc.post("/api/export/fhir", json={"sample_id": sid})
        report = resp.json()["entry"][0]["resource"]
        assert report["subject"]["display"] == "Test Sample"

    def test_report_has_issued(self, client) -> None:
        tc, sid = client
        resp = tc.post("/api/export/fhir", json={"sample_id": sid})
        report = resp.json()["entry"][0]["resource"]
        assert "issued" in report

    def test_report_references_observations(self, client) -> None:
        tc, sid = client
        resp = tc.post("/api/export/fhir", json={"sample_id": sid})
        bundle = resp.json()
        report = bundle["entry"][0]["resource"]
        # Should reference 4 observations (all variants)
        assert len(report["result"]) == 4
        # Each reference should match an observation fullUrl
        obs_urls = {e["fullUrl"] for e in bundle["entry"][1:]}
        for ref in report["result"]:
            assert ref["reference"] in obs_urls

    def test_report_category_genetics(self, client) -> None:
        tc, sid = client
        resp = tc.post("/api/export/fhir", json={"sample_id": sid})
        report = resp.json()["entry"][0]["resource"]
        cat = report["category"][0]["coding"][0]
        assert cat["code"] == "GE"
        assert cat["display"] == "Genetics"


class TestFhirObservations:
    """Observation resources for each variant."""

    def test_observation_count(self, client) -> None:
        tc, sid = client
        resp = tc.post("/api/export/fhir", json={"sample_id": sid})
        bundle = resp.json()
        observations = [
            e for e in bundle["entry"] if e["resource"]["resourceType"] == "Observation"
        ]
        assert len(observations) == 4

    def test_observation_structure(self, client) -> None:
        tc, sid = client
        resp = tc.post("/api/export/fhir", json={"sample_id": sid})
        bundle = resp.json()
        obs = bundle["entry"][1]["resource"]
        assert obs["resourceType"] == "Observation"
        assert obs["status"] == "final"
        assert obs["code"]["coding"][0]["code"] == "69548-6"
        assert "component" in obs

    def test_observation_has_gene(self, client) -> None:
        tc, sid = client
        resp = tc.post("/api/export/fhir", json={"sample_id": sid})
        bundle = resp.json()
        # First obs after DiagnosticReport (sorted by chrom/pos, so chr1 MTHFR first)
        obs = bundle["entry"][1]["resource"]
        gene_components = [
            c for c in obs["component"] if c["code"]["coding"][0]["code"] == "48018-6"
        ]
        assert len(gene_components) == 1

    def test_observation_has_dbsnp(self, client) -> None:
        tc, sid = client
        resp = tc.post("/api/export/fhir", json={"sample_id": sid})
        bundle = resp.json()
        obs = bundle["entry"][1]["resource"]
        dbsnp_components = [
            c for c in obs["component"] if c["code"]["coding"][0]["code"] == "81255-2"
        ]
        assert len(dbsnp_components) == 1

    def test_observation_has_position(self, client) -> None:
        tc, sid = client
        resp = tc.post("/api/export/fhir", json={"sample_id": sid})
        bundle = resp.json()
        obs = bundle["entry"][1]["resource"]
        pos_components = [
            c for c in obs["component"] if c["code"]["coding"][0]["code"] == "81254-5"
        ]
        assert len(pos_components) == 1
        assert "valueInteger" in pos_components[0]

    def test_observation_has_allelic_state(self, client) -> None:
        tc, sid = client
        resp = tc.post("/api/export/fhir", json={"sample_id": sid})
        bundle = resp.json()
        obs = bundle["entry"][1]["resource"]
        allelic = [c for c in obs["component"] if c["code"]["coding"][0]["code"] == "53034-5"]
        assert len(allelic) == 1

    def test_observation_has_ref_alt_alleles(self, client) -> None:
        tc, sid = client
        resp = tc.post("/api/export/fhir", json={"sample_id": sid})
        bundle = resp.json()
        obs = bundle["entry"][1]["resource"]
        ref_comps = [c for c in obs["component"] if c["code"]["coding"][0]["code"] == "69547-8"]
        alt_comps = [c for c in obs["component"] if c["code"]["coding"][0]["code"] == "69551-0"]
        assert len(ref_comps) == 1
        assert len(alt_comps) == 1

    def test_observation_clinvar_significance(self, client) -> None:
        tc, sid = client
        resp = tc.post("/api/export/fhir", json={"sample_id": sid})
        bundle = resp.json()
        # Find the BRCA1 observation (Pathogenic)
        brca_obs = None
        for e in bundle["entry"][1:]:
            components = e["resource"].get("component", [])
            for c in components:
                if (
                    c["code"]["coding"][0]["code"] == "81255-2"
                    and c.get("valueCodeableConcept", {}).get("coding", [{}])[0].get("code")
                    == "rs80357906"
                ):
                    brca_obs = e["resource"]
                    break
        assert brca_obs is not None
        clinvar_comps = [
            c for c in brca_obs["component"] if c["code"]["coding"][0]["code"] == "53037-8"
        ]
        assert len(clinvar_comps) == 1
        assert clinvar_comps[0]["valueCodeableConcept"]["text"] == "Pathogenic"

    def test_observation_gnomad_af(self, client) -> None:
        tc, sid = client
        resp = tc.post("/api/export/fhir", json={"sample_id": sid})
        bundle = resp.json()
        obs = bundle["entry"][1]["resource"]
        af_comps = [c for c in obs["component"] if c["code"]["coding"][0]["code"] == "81258-6"]
        assert len(af_comps) == 1
        assert "valueQuantity" in af_comps[0]

    def test_observation_hgvs_value(self, client) -> None:
        """Observation with HGVS should include valueCodeableConcept."""
        tc, sid = client
        resp = tc.post("/api/export/fhir", json={"sample_id": sid})
        bundle = resp.json()
        # APOE variant (rs429358) has HGVS annotations
        apoe_obs = None
        for e in bundle["entry"][1:]:
            components = e["resource"].get("component", [])
            for c in components:
                if (
                    c["code"]["coding"][0]["code"] == "81255-2"
                    and c.get("valueCodeableConcept", {}).get("coding", [{}])[0].get("code")
                    == "rs429358"
                ):
                    apoe_obs = e["resource"]
                    break
        assert apoe_obs is not None
        assert "valueCodeableConcept" in apoe_obs
        assert "NM_000041.4" in apoe_obs["valueCodeableConcept"]["text"]

    def test_observation_consequence(self, client) -> None:
        tc, sid = client
        resp = tc.post("/api/export/fhir", json={"sample_id": sid})
        bundle = resp.json()
        obs = bundle["entry"][1]["resource"]
        consequence_comps = [
            c for c in obs["component"] if c["code"]["coding"][0]["code"] == "48004-6"
        ]
        assert len(consequence_comps) == 1
        assert "valueCodeableConcept" in consequence_comps[0]


class TestFhirFiltering:
    """The include_all flag filters to ClinVar-annotated variants only."""

    def test_include_all_true(self, client) -> None:
        tc, sid = client
        resp = tc.post("/api/export/fhir", json={"sample_id": sid, "include_all": True})
        bundle = resp.json()
        observations = [
            e for e in bundle["entry"] if e["resource"]["resourceType"] == "Observation"
        ]
        assert len(observations) == 4

    def test_include_all_false(self, client) -> None:
        tc, sid = client
        resp = tc.post("/api/export/fhir", json={"sample_id": sid, "include_all": False})
        bundle = resp.json()
        observations = [
            e for e in bundle["entry"] if e["resource"]["resourceType"] == "Observation"
        ]
        # Only 3 variants have clinvar_significance (rs12913832 has None)
        assert len(observations) == 3
        # DiagnosticReport result refs should match
        report = bundle["entry"][0]["resource"]
        assert len(report["result"]) == 3


class TestFhirErrors:
    """Error handling for FHIR export."""

    def test_missing_sample(self, client) -> None:
        tc, _ = client
        resp = tc.post("/api/export/fhir", json={"sample_id": 999})
        # #453: a missing sample is 404 (existence checked before the export
        # runs), distinct from an existing-but-empty sample's 422
        # (test_no_annotated_variants). Previously both returned 422 because the
        # gate passed a missing sample through to build_fhir_bundle's ValueError.
        assert resp.status_code == 404

    def test_no_annotated_variants(self, empty_client) -> None:
        tc, sid = empty_client
        resp = tc.post("/api/export/fhir", json={"sample_id": sid})
        assert resp.status_code == 422
        assert "annotated variants" in resp.json()["detail"].lower()


class TestFhirBundleValidation:
    """Validate FHIR R4 Bundle constraints."""

    def test_all_entries_have_full_url(self, client) -> None:
        tc, sid = client
        resp = tc.post("/api/export/fhir", json={"sample_id": sid})
        bundle = resp.json()
        for entry in bundle["entry"]:
            assert "fullUrl" in entry
            assert entry["fullUrl"].startswith("urn:uuid:")

    def test_all_entries_have_resource(self, client) -> None:
        tc, sid = client
        resp = tc.post("/api/export/fhir", json={"sample_id": sid})
        bundle = resp.json()
        for entry in bundle["entry"]:
            assert "resource" in entry
            assert "resourceType" in entry["resource"]

    def test_all_observations_have_id(self, client) -> None:
        tc, sid = client
        resp = tc.post("/api/export/fhir", json={"sample_id": sid})
        bundle = resp.json()
        for entry in bundle["entry"][1:]:
            assert "id" in entry["resource"]

    def test_all_observations_have_category(self, client) -> None:
        tc, sid = client
        resp = tc.post("/api/export/fhir", json={"sample_id": sid})
        bundle = resp.json()
        for entry in bundle["entry"][1:]:
            obs = entry["resource"]
            assert "category" in obs
            assert obs["category"][0]["coding"][0]["code"] == "laboratory"

    def test_bundle_is_valid_json(self, client) -> None:
        tc, sid = client
        resp = tc.post("/api/export/fhir", json={"sample_id": sid})
        # Should be parseable as JSON
        bundle = json.loads(resp.text)
        assert isinstance(bundle, dict)

    def test_entry_count(self, client) -> None:
        """Bundle should have 1 DiagnosticReport + N Observations."""
        tc, sid = client
        resp = tc.post("/api/export/fhir", json={"sample_id": sid})
        bundle = resp.json()
        # 1 DiagnosticReport + 4 Observations = 5 entries
        assert len(bundle["entry"]) == 5

    def test_no_condition_resources(self, client) -> None:
        """R-17 mitigation: no Condition resources in bundle."""
        tc, sid = client
        resp = tc.post("/api/export/fhir", json={"sample_id": sid})
        bundle = resp.json()
        conditions = [e for e in bundle["entry"] if e["resource"]["resourceType"] == "Condition"]
        assert len(conditions) == 0

    def test_no_medication_resources(self, client) -> None:
        """R-17 mitigation: no MedicationStatement resources in bundle."""
        tc, sid = client
        resp = tc.post("/api/export/fhir", json={"sample_id": sid})
        bundle = resp.json()
        meds = [
            e for e in bundle["entry"] if e["resource"]["resourceType"] == "MedicationStatement"
        ]
        assert len(meds) == 0

    def test_chromosome_sorted_order(self, client) -> None:
        """Observations should be sorted by chromosome, then position."""
        tc, sid = client
        resp = tc.post("/api/export/fhir", json={"sample_id": sid})
        bundle = resp.json()
        # Extract rsids in order from observations
        rsids = []
        for entry in bundle["entry"][1:]:
            for comp in entry["resource"]["component"]:
                if comp["code"]["coding"][0]["code"] == "81255-2":
                    rsids.append(comp["valueCodeableConcept"]["coding"][0]["code"])
        # Expected order: chr1 (rs1801133), chr15 (rs12913832),
        # chr17 (rs80357906), chr19 (rs429358)
        assert rsids == ["rs1801133", "rs12913832", "rs80357906", "rs429358"]

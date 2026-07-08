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
from backend.db.tables import (
    annotated_variants,
    raw_variants,
    reference_metadata,
    samples,
)
from backend.reports.fhir_export import (
    FHIR_GENOME_BUILD,
    FHIR_MITOCHONDRIAL_REFERENCE,
    HGNC_SYSTEM,
    LOINC_ALLELIC_STATE,
    LOINC_CLINVAR_SIGNIFICANCE,
    LOINC_GENE_STUDIED,
    LOINC_GENOMIC_REF_SEQ,
    LOINC_POPULATION_AF,
    LOINC_SYSTEM,
    LOINC_VARIANT_EXACT_START,
    NCBI_NUCCORE_SYSTEM,
    _clinical_significance_value,
    _variant_to_observation,
)

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
    # #890 regression: a hom_ref (non-carrier) row carrying the locus's Pathogenic
    # ClinVar significance. The sample genotype matches the reference, so it does
    # NOT carry the variant — it must be excluded from the FHIR bundle, never
    # exported as a "Homozygous" + Pathogenic Observation. Only `zygosity` matters
    # for this regression; rs334/HBB is used as a recognizable Pathogenic locus.
    {
        "rsid": "rs334",
        "chrom": "11",
        "pos": 5248232,
        "ref": "T",
        "alt": "A",
        "genotype": "TT",
        "zygosity": "hom_ref",
        "gene_symbol": "HBB",
        "consequence": "missense_variant",
        "clinvar_significance": "Pathogenic",
        "clinvar_review_stars": 4,
        "clinvar_accession": "VCV000015333",
        "gnomad_af_global": 0.01,
        "rare_flag": False,
        "annotation_coverage": 0x1F,
        "evidence_conflict": False,
        "ensemble_pathogenic": True,
    },
]

_ALL_COLS = [col.name for col in annotated_variants.columns]


def _normalize(variant: dict) -> dict:
    """Fill missing columns with None."""
    return {k: variant.get(k) for k in _ALL_COLS}


def _components_by_code(resource: dict, code: str) -> list[dict]:
    return [c for c in resource["component"] if c["code"]["coding"][0]["code"] == code]


# Allelic-state regression helpers (#1280) ---------------------------------
# GYG2 X:2777985 — the issue's real reproduced non-PAR chrX locus (GRCh37 X PAR1
# ends at 2,699,520, so this falls outside it).
_NONPAR_X_POS = 2_777_985
_PAR1_X_POS = 1_000_000  # inside GRCh37 X PAR1 (60001–2,699,520)
_LOINC_DBSNP_ID = "81255-2"  # LOINC "dbSNP [ID]" component code


def _obs_allelic_coding(resource: dict) -> dict | None:
    """Return the single allelic-state (LOINC 53034-5) coding, or None if absent."""
    comps = _components_by_code(resource, LOINC_ALLELIC_STATE)
    if not comps:
        return None
    return comps[0]["valueCodeableConcept"]["coding"][0]


def _find_obs_by_rsid(bundle: dict, rsid: str) -> dict:
    """Find the Observation in a bundle whose dbSNP component carries ``rsid``."""
    for entry in bundle["entry"]:
        resource = entry["resource"]
        if resource.get("resourceType") != "Observation":
            continue
        for comp in _components_by_code(resource, _LOINC_DBSNP_ID):
            val = comp.get("valueCodeableConcept", {}).get("coding", [{}])[0]
            if val.get("code") == rsid:
                return resource
    raise AssertionError(f"no Observation for {rsid} in bundle")


# Male sex signal for the end-to-end hemizygous test: 120 non-PAR chrX
# hemizygous single-allele calls (het rate 0) + 60 fully-typed chrY probes clear
# the §9.4 minimum-evidence floors and infer XY.
_MALE_RAW_VARIANTS = [
    {"rsid": f"rs_x_{i}", "chrom": "X", "pos": 5_000_000 + i, "genotype": "A"} for i in range(120)
] + [
    {"rsid": f"rs_y_{i}", "chrom": "Y", "pos": 6_000_000 + i, "genotype": "TT"} for i in range(60)
]
_MALE_ANNOTATED = [
    {
        "rsid": "rs_x_hemi",
        "chrom": "X",
        "pos": _NONPAR_X_POS,
        "ref": "C",
        "alt": "T",
        "genotype": "T",
        "zygosity": "hom_alt",
        "gene_symbol": "GYG2",
    },
    {
        "rsid": "rs_mt_test",
        "chrom": "MT",
        "pos": 73,
        "ref": "A",
        "alt": "G",
        "genotype": "G",
        "zygosity": "hom_alt",
        "gene_symbol": "MT-TF",
    },
]


# ── Fixtures ─────────────────────────────────────────────────────────


def _setup_client(
    tmp_data_dir: Path,
    variants: list[dict],
    raw_variant_rows: list[dict] | None = None,
):
    """Create a TestClient with annotated sample data.

    ``raw_variant_rows`` optionally seeds ``raw_variants`` (chrX/chrY probes) so
    the FHIR export's biological-sex resolution has signal to infer from — needed
    to exercise the hemizygous allelic-state path (#1280).
    """
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
    if raw_variant_rows:
        with sample_engine.begin() as conn:
            conn.execute(sa.insert(raw_variants), raw_variant_rows)
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


@pytest.fixture
def male_client(tmp_data_dir: Path):
    """Client whose sample infers XY and carries chrX non-PAR + chrMT variants (#1280)."""
    yield from _setup_client(tmp_data_dir, _MALE_ANNOTATED, raw_variant_rows=_MALE_RAW_VARIANTS)


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

    def test_report_status_is_preliminary_not_final(self, client) -> None:
        # Unvalidated, array-derived research output must not be signalled as a
        # verified/complete clinical report (#1291).
        tc, sid = client
        resp = tc.post("/api/export/fhir", json={"sample_id": sid})
        report = resp.json()["entry"][0]["resource"]
        assert report["status"] == "preliminary"

    def test_report_carries_research_use_conclusion(self, client) -> None:
        # The research-use caveat must travel inside the bundle (#1291), so a
        # receiving EHR/clinician sees it — a docs-only caveat does not.
        from backend.reports.fhir_export import RESEARCH_USE_DISCLAIMER

        tc, sid = client
        resp = tc.post("/api/export/fhir", json={"sample_id": sid})
        report = resp.json()["entry"][0]["resource"]
        assert report["conclusion"] == RESEARCH_USE_DISCLAIMER
        conclusion_lower = report["conclusion"].lower()
        assert "not" in conclusion_lower and "clinical" in conclusion_lower
        assert "research" in conclusion_lower

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
        # Unvalidated research result, not a verified clinical observation (#1291).
        assert obs["status"] == "preliminary"
        assert obs["code"]["coding"][0]["code"] == "69548-6"
        assert "component" in obs

    def test_observation_has_gene(self, client) -> None:
        tc, sid = client
        resp = tc.post("/api/export/fhir", json={"sample_id": sid})
        bundle = resp.json()
        # First obs after DiagnosticReport (sorted by chrom/pos, so chr1 MTHFR first)
        obs = bundle["entry"][1]["resource"]
        gene_components = _components_by_code(obs, LOINC_GENE_STUDIED)
        assert len(gene_components) == 1
        assert gene_components[0]["valueString"] == "MTHFR"
        assert "valueCodeableConcept" not in gene_components[0]

    def test_observation_codes_gene_when_hgnc_id_available(self) -> None:
        row = {**ANNOTATED_VARIANTS[1], "hgnc_id": "HGNC:1100"}
        _full_url, obs = _variant_to_observation(row)
        gene_component = _components_by_code(obs, LOINC_GENE_STUDIED)[0]
        coding = gene_component["valueCodeableConcept"]["coding"][0]

        assert coding == {
            "system": HGNC_SYSTEM,
            "code": "HGNC:1100",
            "display": "BRCA1",
        }

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
        pos_components = _components_by_code(obs, LOINC_VARIANT_EXACT_START)
        assert len(pos_components) == 1
        assert "valueInteger" in pos_components[0]

    def test_coordinate_observations_carry_reference_sequence_build(self) -> None:
        for row in ANNOTATED_VARIANTS:
            _full_url, obs = _variant_to_observation(row)
            if not _components_by_code(obs, LOINC_VARIANT_EXACT_START):
                continue

            refseq_components = _components_by_code(obs, LOINC_GENOMIC_REF_SEQ)
            assert len(refseq_components) == 1
            value = refseq_components[0]["valueCodeableConcept"]
            assert value["text"].startswith(FHIR_GENOME_BUILD)
            assert value["coding"][0]["system"] == NCBI_NUCCORE_SYSTEM

    def test_observation_reference_sequence_is_chromosome_specific(self) -> None:
        _full_url, obs = _variant_to_observation(ANNOTATED_VARIANTS[2])

        value = _components_by_code(obs, LOINC_GENOMIC_REF_SEQ)[0]["valueCodeableConcept"]

        assert value["text"] == "GRCh37/hg19 chr1"
        assert value["coding"] == [
            {
                "system": NCBI_NUCCORE_SYSTEM,
                "code": "NC_000001.10",
                "display": "GRCh37/hg19 chr1",
            }
        ]

    def test_observation_reference_sequence_handles_mitochondrial_label(self) -> None:
        row = {
            "rsid": "rs_mt_test",
            "chrom": "chrM",
            "pos": 73,
            "ref": "A",
            "alt": "G",
            "genotype": "G",
            "zygosity": "hom_alt",
            "gene_symbol": "MT-TF",
        }
        _full_url, obs = _variant_to_observation(row)
        value = _components_by_code(obs, LOINC_GENOMIC_REF_SEQ)[0]["valueCodeableConcept"]

        assert value["text"] == "rCRS chrM"
        assert value["coding"] == [
            {
                "system": NCBI_NUCCORE_SYSTEM,
                "code": "NC_012920.1",
                "display": f"{FHIR_MITOCHONDRIAL_REFERENCE} chrM",
            }
        ]

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
            c
            for c in brca_obs["component"]
            if c["code"]["coding"][0]["code"] == LOINC_CLINVAR_SIGNIFICANCE
        ]
        assert len(clinvar_comps) == 1
        value = clinvar_comps[0]["valueCodeableConcept"]
        assert value["text"] == "Pathogenic"
        assert value["coding"] == [
            {
                "system": LOINC_SYSTEM,
                "code": "LA6668-3",
                "display": "Pathogenic",
            }
        ]

    def test_observation_clinvar_significance_does_not_use_accession_as_code(
        self,
    ) -> None:
        row = {**ANNOTATED_VARIANTS[1], "clinvar_accession": None}
        _full_url, obs = _variant_to_observation(row)
        clinvar_component = _components_by_code(obs, LOINC_CLINVAR_SIGNIFICANCE)[0]
        value = clinvar_component["valueCodeableConcept"]

        assert value["coding"][0]["code"] == "LA6668-3"
        assert "unknown" not in json.dumps(value)

    def test_observation_unmapped_clinvar_significance_is_text_only(self) -> None:
        _full_url, obs = _variant_to_observation(ANNOTATED_VARIANTS[2])
        clinvar_component = _components_by_code(obs, LOINC_CLINVAR_SIGNIFICANCE)[0]
        value = clinvar_component["valueCodeableConcept"]

        assert value == {"text": "drug_response"}

    @pytest.mark.parametrize(
        ("significance", "code", "display"),
        [
            ("Pathogenic", "LA6668-3", "Pathogenic"),
            ("Likely pathogenic", "LA26332-9", "Likely pathogenic"),
            ("Uncertain significance", "LA26333-7", "Uncertain significance"),
            ("Likely benign", "LA26334-5", "Likely benign"),
            ("Benign", "LA6675-8", "Benign"),
        ],
    )
    def test_clinical_significance_loinc_code(
        self, significance: str, code: str, display: str
    ) -> None:
        """Every ACMG_CLINICAL_SIGNIFICANCE_MAP entry must map to its own LOINC
        answer code in the EHR-facing bundle (#1288). The suite previously only
        exercised Pathogenic, so a wrong code for any of the other four
        (e.g. Benign → LA6668-3 Pathogenic) shipped green. Calls
        ``_clinical_significance_value`` directly to stay a fast unit test."""
        coding = _clinical_significance_value(significance)["valueCodeableConcept"]["coding"][0]
        assert coding == {"system": LOINC_SYSTEM, "code": code, "display": display}

    @pytest.mark.parametrize(
        ("significance", "expected_codings"),
        [
            (
                "Pathogenic/Likely pathogenic",
                [
                    {"system": LOINC_SYSTEM, "code": "LA6668-3", "display": "Pathogenic"},
                    {
                        "system": LOINC_SYSTEM,
                        "code": "LA26332-9",
                        "display": "Likely pathogenic",
                    },
                ],
            ),
            (
                "Benign/Likely benign",
                [
                    {"system": LOINC_SYSTEM, "code": "LA6675-8", "display": "Benign"},
                    {"system": LOINC_SYSTEM, "code": "LA26334-5", "display": "Likely benign"},
                ],
            ),
        ],
    )
    def test_combined_clinvar_significance_uses_component_loinc_codes(
        self, significance: str, expected_codings: list[dict[str, str]]
    ) -> None:
        value = _clinical_significance_value(significance)["valueCodeableConcept"]

        assert value["text"] == significance
        assert value["coding"] == expected_codings

    def test_partially_unmapped_combined_clinvar_significance_is_text_only(self) -> None:
        value = _clinical_significance_value("Pathogenic/drug_response")["valueCodeableConcept"]

        assert value == {"text": "Pathogenic/drug_response"}

    def test_observation_gnomad_af_uses_population_frequency_code(self) -> None:
        _full_url, obs = _variant_to_observation(ANNOTATED_VARIANTS[2])
        sample_af_comps = [
            c for c in obs["component"] if c["code"]["coding"][0]["code"] == "81258-6"
        ]
        population_af_comps = [
            c for c in obs["component"] if c["code"]["coding"][0]["code"] == LOINC_POPULATION_AF
        ]
        assert sample_af_comps == []
        assert len(population_af_comps) == 1
        assert population_af_comps[0]["valueQuantity"]["value"] == pytest.approx(0.35)

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
        # Of the 4 carried variants, 3 have clinvar_significance (rs12913832 is
        # None). rs334 also has Pathogenic clinvar_significance but is hom_ref, so
        # the #890 carriage gate drops it before the clinvar filter ever applies.
        assert len(observations) == 3
        # DiagnosticReport result refs should match
        report = bundle["entry"][0]["resource"]
        assert len(report["result"]) == 3


class TestFhirCarriageGate:
    """#890: hom_ref (non-carrier) positions must not be exported as Observations.

    A FHIR genetic-variant Observation asserts the variant is *present*. The
    fixture seeds rs334/HBB as hom_ref + Pathogenic; the sample matches the
    reference there and carries no variant, so it must never appear in the
    bundle — neither as a variant Observation nor as a "Homozygous" +
    Pathogenic ClinVar assertion.
    """

    def test_homref_variant_excluded_include_all(self, client) -> None:
        tc, sid = client
        resp = tc.post("/api/export/fhir", json={"sample_id": sid, "include_all": True})
        bundle = resp.json()
        blob = json.dumps(bundle)
        # The hom_ref locus identifiers must not appear anywhere in the bundle.
        assert "rs334" not in blob
        assert "VCV000015333" not in blob

    def test_homref_variant_excluded_clinvar_only(self, client) -> None:
        tc, sid = client
        resp = tc.post("/api/export/fhir", json={"sample_id": sid, "include_all": False})
        bundle = resp.json()
        assert "rs334" not in json.dumps(bundle)

    def test_homref_row_emits_no_allelic_state(self) -> None:
        # Defense in depth (#890): even if a hom_ref row reached the observation
        # builder directly, it must NOT be labelled "Homozygous" (LA6705-3, the
        # code shared with hom_alt). hom_ref is deliberately absent from
        # ALLELIC_STATE_MAP, so the allelic-state component is simply omitted.
        row = {
            "rsid": "rs334",
            "chrom": "11",
            "pos": 5248232,
            "ref": "T",
            "alt": "A",
            "genotype": "TT",
            "zygosity": "hom_ref",
            "gene_symbol": "HBB",
            "clinvar_significance": "Pathogenic",
            "clinvar_accession": "VCV000015333",
        }
        _full_url, obs = _variant_to_observation(row)
        allelic_codes = [
            comp["code"]["coding"][0]["code"]
            for comp in obs.get("component", [])
            if comp["code"]["coding"][0]["code"] == LOINC_ALLELIC_STATE
        ]
        assert allelic_codes == []


class TestFhirAllelicStatePloidy:
    """Allelic state must be ploidy/sex/chromosome-aware (#1280).

    LOINC answer list LL381-5 (verified at loinc.org 2026-06-29): a single-copy
    locus is Hemizygous (LA6707-9) or Homoplasmic (LA6704-6), never the diploid
    "Homozygous" (LA6705-3). ``zygosity.py`` collapses a haploid single-base call
    to ``hom_alt``, so without the remap a male non-PAR chrX/chrY call or any
    chrMT call would export as a two-copy homozygote — biologically wrong and
    EHR-facing.
    """

    @staticmethod
    def _row(**kw: object) -> dict:
        row: dict = {"rsid": "rsTEST", "ref": "C", "alt": "T", "gene_symbol": "TESTGENE"}
        row.update(kw)
        return row

    def test_male_nonpar_x_homalt_is_hemizygous(self) -> None:
        row = self._row(chrom="X", pos=_NONPAR_X_POS, genotype="T", zygosity="hom_alt")
        _u, obs = _variant_to_observation(row, sex="XY")
        assert _obs_allelic_coding(obs) == {
            "system": LOINC_SYSTEM,
            "code": "LA6707-9",
            "display": "Hemizygous",
        }

    def test_male_y_homalt_is_hemizygous(self) -> None:
        row = self._row(chrom="Y", pos=2_700_000, genotype="T", zygosity="hom_alt")
        _u, obs = _variant_to_observation(row, sex="XY")
        coding = _obs_allelic_coding(obs)
        assert coding["code"] == "LA6707-9"
        assert coding["display"] == "Hemizygous"

    def test_male_par_x_homalt_stays_homozygous(self) -> None:
        # PAR1/PAR2 are diploid in both sexes — must NOT be remapped to hemizygous.
        row = self._row(chrom="X", pos=_PAR1_X_POS, genotype="TT", zygosity="hom_alt")
        _u, obs = _variant_to_observation(row, sex="XY")
        coding = _obs_allelic_coding(obs)
        assert coding["code"] == "LA6705-3"
        assert coding["display"] == "Homozygous"

    def test_male_y_par2_homalt_stays_homozygous(self) -> None:
        row = self._row(chrom="Y", pos=59_198_808, genotype="TT", zygosity="hom_alt")
        _u, obs = _variant_to_observation(row, sex="XY")
        coding = _obs_allelic_coding(obs)
        assert coding["code"] == "LA6705-3"
        assert coding["display"] == "Homozygous"

    def test_female_nonpar_x_homalt_stays_homozygous(self) -> None:
        # XX is diploid on chrX — a genuine homozygote.
        row = self._row(chrom="X", pos=_NONPAR_X_POS, genotype="TT", zygosity="hom_alt")
        _u, obs = _variant_to_observation(row, sex="XX")
        assert _obs_allelic_coding(obs)["code"] == "LA6705-3"

    def test_unresolved_sex_nonpar_x_homalt_stays_homozygous(self) -> None:
        # Without a resolved male sex we cannot assert hemizygosity → stay diploid.
        for sex in (None, "unknown", "manual_review"):
            row = self._row(chrom="X", pos=_NONPAR_X_POS, genotype="T", zygosity="hom_alt")
            _u, obs = _variant_to_observation(row, sex=sex)
            assert _obs_allelic_coding(obs)["code"] == "LA6705-3", sex

    def test_male_nonpar_x_het_stays_heterozygous(self) -> None:
        # Two distinct alleles were actually observed → keep Heterozygous (#1280).
        row = self._row(chrom="X", pos=_NONPAR_X_POS, genotype="CT", zygosity="het")
        _u, obs = _variant_to_observation(row, sex="XY")
        assert _obs_allelic_coding(obs)["code"] == "LA6706-1"

    def test_mt_homalt_is_homoplasmic(self) -> None:
        row = self._row(chrom="MT", pos=73, ref="A", alt="G", genotype="G", zygosity="hom_alt")
        _u, obs = _variant_to_observation(row, sex="XY")
        assert _obs_allelic_coding(obs) == {
            "system": LOINC_SYSTEM,
            "code": "LA6704-6",
            "display": "Homoplasmic",
        }

    def test_mt_homoplasmic_is_sex_independent(self) -> None:
        # mtDNA is haploid in every sample — Homoplasmic regardless of sex.
        for sex in (None, "XX", "XY", "unknown"):
            row = self._row(chrom="MT", pos=73, ref="A", alt="G", genotype="G", zygosity="hom_alt")
            _u, obs = _variant_to_observation(row, sex=sex)
            assert _obs_allelic_coding(obs)["code"] == "LA6704-6", sex

    def test_mt_alias_chr_prefix_normalizes(self) -> None:
        # "chrM" must fold onto MT and resolve to Homoplasmic.
        row = self._row(chrom="chrM", pos=73, ref="A", alt="G", genotype="G", zygosity="hom_alt")
        _u, obs = _variant_to_observation(row)
        assert _obs_allelic_coding(obs)["code"] == "LA6704-6"

    def test_male_nonpar_x_chr_prefix_is_hemizygous(self) -> None:
        # A "chrX" label must still be recognised as chromosome X.
        row = self._row(chrom="chrX", pos=_NONPAR_X_POS, genotype="T", zygosity="hom_alt")
        _u, obs = _variant_to_observation(row, sex="XY")
        assert _obs_allelic_coding(obs)["code"] == "LA6707-9"

    def test_autosomal_homalt_stays_homozygous(self) -> None:
        row = self._row(chrom="1", pos=11_856_378, genotype="TT", zygosity="hom_alt")
        _u, obs = _variant_to_observation(row, sex="XY")
        assert _obs_allelic_coding(obs)["code"] == "LA6705-3"

    def test_export_infers_male_and_codes_hemizygous_and_homoplasmic(self, male_client) -> None:
        """End-to-end: a sample inferred XY exports its chrX non-PAR variant as
        Hemizygous and its chrMT variant as Homoplasmic."""
        tc, sid = male_client
        resp = tc.post("/api/export/fhir", json={"sample_id": sid, "include_all": True})
        assert resp.status_code == 200
        bundle = resp.json()
        assert _obs_allelic_coding(_find_obs_by_rsid(bundle, "rs_x_hemi"))["code"] == "LA6707-9"
        assert _obs_allelic_coding(_find_obs_by_rsid(bundle, "rs_mt_test"))["code"] == "LA6704-6"


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

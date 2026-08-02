"""Tests for pharmacogenomics star-allele calling (P3-02), three-state
calling model (P3-03), and prescribing alert generation (P3-04).

Covers:
  - T3-01: Star-allele calling returns correct diplotype for CYP2C19 *1/*2
  - T3-02: Metabolizer phenotype correctly assigned: CYP2C19 *1/*2 → IM
  - T3-04: CYP2D6 calling produces Partial state with structural variant caveat
  - P3-04: Prescribing alerts generated from star-allele results + CPIC guidelines
  - Additional: CYP2D6 calling, edge cases, multi-variant alleles, missing data,
    three-state confidence assignment
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
import sqlalchemy as sa

from backend.analysis.pharmacogenomics import (
    CallConfidence,
    PrescribingAlert,
    StarAlleleResult,
    _assess_call_confidence,
    _build_finding_text,
    _count_alt_alleles,
    _fetch_alleles_for_gene,
    _fetch_diplotype_phenotype,
    _fetch_guidelines_for_gene_phenotype,
    _fetch_sample_genotypes,
    call_all_star_alleles,
    call_star_alleles_for_gene,
    contains_unpresentable_prescribing_identifier,
    generate_prescribing_alerts,
    is_patient_presentable_finding_payload,
    is_patient_presentable_response_payload,
    is_prescribing_alert_withheld,
    patient_visible_finding_clause,
    store_prescribing_alerts,
    update_annotation_coverage_cpic,
)
from backend.analysis.run_all import _run_pharma
from backend.annotation.engine import CPIC_BIT
from backend.db.sample_schema import create_sample_tables
from backend.db.tables import (
    annotated_variants,
    cpic_alleles,
    cpic_diplotypes,
    cpic_guidelines,
    findings,
    raw_variants,
    reference_metadata,
)

# ═══════════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════════


@pytest.fixture
def pgx_reference_engine() -> sa.Engine:
    """Reference engine with CPIC alleles and diplotypes for PGx testing."""
    engine = sa.create_engine("sqlite://")
    reference_metadata.create_all(engine)

    alleles = [
        # CYP2C19
        {
            "gene": "CYP2C19",
            "allele_name": "*1",
            "defining_variants": "[]",
            "function": "Normal function",
            "activity_score": 1.0,
        },
        {
            "gene": "CYP2C19",
            "allele_name": "*2",
            "defining_variants": json.dumps([{"rsid": "rs4244285", "ref": "G", "alt": "A"}]),
            "function": "No function",
            "activity_score": 0.0,
        },
        {
            "gene": "CYP2C19",
            "allele_name": "*3",
            "defining_variants": json.dumps([{"rsid": "rs4986893", "ref": "G", "alt": "A"}]),
            "function": "No function",
            "activity_score": 0.0,
        },
        {
            "gene": "CYP2C19",
            "allele_name": "*17",
            "defining_variants": json.dumps([{"rsid": "rs12248560", "ref": "C", "alt": "T"}]),
            "function": "Increased function",
            "activity_score": 1.5,
        },
        # CYP2D6
        {
            "gene": "CYP2D6",
            "allele_name": "*1",
            "defining_variants": "[]",
            "function": "Normal function",
            "activity_score": 1.0,
        },
        {
            "gene": "CYP2D6",
            "allele_name": "*2",
            "defining_variants": json.dumps([{"rsid": "rs16947", "ref": "G", "alt": "A"}]),
            "function": "Normal function",
            "activity_score": 1.0,
        },
        {
            "gene": "CYP2D6",
            "allele_name": "*4",
            "defining_variants": json.dumps([{"rsid": "rs3892097", "ref": "C", "alt": "T"}]),
            "function": "No function",
            "activity_score": 0.0,
        },
        {
            "gene": "CYP2D6",
            "allele_name": "*10",
            "defining_variants": json.dumps([{"rsid": "rs1065852", "ref": "G", "alt": "A"}]),
            "function": "Decreased function",
            "activity_score": 0.25,
        },
        # TPMT (with multi-variant allele *3A)
        {
            "gene": "TPMT",
            "allele_name": "*1",
            "defining_variants": "[]",
            "function": "Normal function",
            "activity_score": 1.0,
        },
        {
            "gene": "TPMT",
            "allele_name": "*3A",
            "defining_variants": json.dumps(
                [
                    {"rsid": "rs1800460", "ref": "C", "alt": "T"},
                    {"rsid": "rs1142345", "ref": "T", "alt": "C"},
                ]
            ),
            "function": "No function",
            "activity_score": 0.0,
        },
        {
            "gene": "TPMT",
            "allele_name": "*3B",
            "defining_variants": json.dumps([{"rsid": "rs1800460", "ref": "C", "alt": "T"}]),
            "function": "No function",
            "activity_score": 0.0,
        },
        {
            "gene": "TPMT",
            "allele_name": "*3C",
            "defining_variants": json.dumps([{"rsid": "rs1142345", "ref": "T", "alt": "C"}]),
            "function": "No function",
            "activity_score": 0.0,
        },
        # SLCO1B1 (with multi-variant allele *15)
        {
            "gene": "SLCO1B1",
            "allele_name": "*1A",
            "defining_variants": "[]",
            "function": "Normal function",
            "activity_score": 1.0,
        },
        {
            "gene": "SLCO1B1",
            "allele_name": "*1B",
            "defining_variants": json.dumps([{"rsid": "rs2306283", "ref": "A", "alt": "G"}]),
            "function": "Normal function",
            "activity_score": 1.0,
        },
        {
            "gene": "SLCO1B1",
            "allele_name": "*5",
            "defining_variants": json.dumps([{"rsid": "rs4149056", "ref": "T", "alt": "C"}]),
            "function": "Decreased function",
            "activity_score": 0.5,
        },
        {
            "gene": "SLCO1B1",
            "allele_name": "*15",
            "defining_variants": json.dumps(
                [
                    {"rsid": "rs2306283", "ref": "A", "alt": "G"},
                    {"rsid": "rs4149056", "ref": "T", "alt": "C"},
                ]
            ),
            "function": "Decreased function",
            "activity_score": 0.5,
        },
    ]

    diplotypes = [
        # CYP2C19
        {
            "gene": "CYP2C19",
            "diplotype": "*1/*1",
            "phenotype": "Normal Metabolizer",
            "ehr_notation": "CYP2C19 Normal Metabolizer",
            "activity_score": 2.0,
        },
        {
            "gene": "CYP2C19",
            "diplotype": "*1/*2",
            "phenotype": "Intermediate Metabolizer",
            "ehr_notation": "CYP2C19 Intermediate Metabolizer",
            "activity_score": 1.0,
        },
        {
            "gene": "CYP2C19",
            "diplotype": "*2/*2",
            "phenotype": "Poor Metabolizer",
            "ehr_notation": "CYP2C19 Poor Metabolizer",
            "activity_score": 0.0,
        },
        {
            "gene": "CYP2C19",
            "diplotype": "*1/*17",
            "phenotype": "Rapid Metabolizer",
            "ehr_notation": "CYP2C19 Rapid Metabolizer",
            "activity_score": 2.5,
        },
        {
            "gene": "CYP2C19",
            "diplotype": "*17/*17",
            "phenotype": "Ultrarapid Metabolizer",
            "ehr_notation": "CYP2C19 Ultrarapid Metabolizer",
            "activity_score": 3.0,
        },
        {
            "gene": "CYP2C19",
            "diplotype": "*2/*17",
            "phenotype": "Intermediate Metabolizer",
            "ehr_notation": "CYP2C19 Intermediate Metabolizer",
            "activity_score": 1.5,
        },
        # CYP2D6
        {
            "gene": "CYP2D6",
            "diplotype": "*1/*1",
            "phenotype": "Normal Metabolizer",
            "ehr_notation": "CYP2D6 Normal Metabolizer",
            "activity_score": 2.0,
        },
        {
            "gene": "CYP2D6",
            "diplotype": "*1/*4",
            "phenotype": "Intermediate Metabolizer",
            "ehr_notation": "CYP2D6 Intermediate Metabolizer",
            "activity_score": 1.0,
        },
        {
            "gene": "CYP2D6",
            "diplotype": "*4/*4",
            "phenotype": "Poor Metabolizer",
            "ehr_notation": "CYP2D6 Poor Metabolizer",
            "activity_score": 0.0,
        },
        {
            "gene": "CYP2D6",
            "diplotype": "*1/*2",
            "phenotype": "Normal Metabolizer",
            "ehr_notation": "CYP2D6 Normal Metabolizer",
            "activity_score": 2.0,
        },
        {
            "gene": "CYP2D6",
            "diplotype": "*2/*4",
            "phenotype": "Intermediate Metabolizer",
            "ehr_notation": "CYP2D6 Intermediate Metabolizer",
            "activity_score": 1.0,
        },
        {
            "gene": "CYP2D6",
            "diplotype": "*1/*10",
            "phenotype": "Normal Metabolizer",
            "ehr_notation": "CYP2D6 Normal Metabolizer",
            "activity_score": 1.25,
        },
        # TPMT
        {
            "gene": "TPMT",
            "diplotype": "*1/*1",
            "phenotype": "Normal Metabolizer",
            "ehr_notation": "TPMT Normal Metabolizer",
            "activity_score": 2.0,
        },
        {
            "gene": "TPMT",
            "diplotype": "*1/*3A",
            "phenotype": "Intermediate Metabolizer",
            "ehr_notation": "TPMT Intermediate Metabolizer",
            "activity_score": 1.0,
        },
        {
            "gene": "TPMT",
            "diplotype": "*3A/*3A",
            "phenotype": "Poor Metabolizer",
            "ehr_notation": "TPMT Poor Metabolizer",
            "activity_score": 0.0,
        },
        {
            "gene": "TPMT",
            "diplotype": "*1/*3B",
            "phenotype": "Intermediate Metabolizer",
            "ehr_notation": "TPMT Intermediate Metabolizer",
            "activity_score": 1.0,
        },
        {
            "gene": "TPMT",
            "diplotype": "*1/*3C",
            "phenotype": "Intermediate Metabolizer",
            "ehr_notation": "TPMT Intermediate Metabolizer",
            "activity_score": 1.0,
        },
        # SLCO1B1
        {
            "gene": "SLCO1B1",
            "diplotype": "*1A/*1A",
            "phenotype": "Normal function",
            "ehr_notation": "SLCO1B1 Normal function",
            "activity_score": 2.0,
        },
        {
            "gene": "SLCO1B1",
            "diplotype": "*1A/*5",
            "phenotype": "Decreased function",
            "ehr_notation": "SLCO1B1 Decreased function",
            "activity_score": 1.5,
        },
        {
            "gene": "SLCO1B1",
            "diplotype": "*1A/*15",
            "phenotype": "Decreased function",
            "ehr_notation": "SLCO1B1 Decreased function",
            "activity_score": 1.5,
        },
        {
            "gene": "SLCO1B1",
            "diplotype": "*1A/*1B",
            "phenotype": "Normal function",
            "ehr_notation": "SLCO1B1 Normal function",
            "activity_score": 2.0,
        },
    ]

    guidelines = [
        # CYP2D6 codeine
        {
            "gene": "CYP2D6",
            "drug": "codeine",
            "phenotype": "Normal Metabolizer",
            "recommendation": "Use label-recommended age- or weight-specific dosing.",
            "classification": "A",
            "guideline_url": "https://cpicpgx.org/guidelines/guideline-for-codeine-and-cyp2d6/",
        },
        {
            "gene": "CYP2D6",
            "drug": "codeine",
            "phenotype": "Intermediate Metabolizer",
            "recommendation": "Use label-recommended age- or weight-specific dosing.",
            "classification": "A",
            "guideline_url": "https://cpicpgx.org/guidelines/guideline-for-codeine-and-cyp2d6/",
        },
        {
            "gene": "CYP2D6",
            "drug": "codeine",
            "phenotype": "Poor Metabolizer",
            "recommendation": "Avoid codeine use. Alternative analgesics recommended.",
            "classification": "A",
            "guideline_url": "https://cpicpgx.org/guidelines/guideline-for-codeine-and-cyp2d6/",
        },
        # CYP2D6 tramadol (classification B)
        {
            "gene": "CYP2D6",
            "drug": "tramadol",
            "phenotype": "Poor Metabolizer",
            "recommendation": "Avoid tramadol use due to lack of efficacy.",
            "classification": "B",
            "guideline_url": "https://cpicpgx.org/guidelines/guideline-for-codeine-and-cyp2d6/",
        },
        {
            "gene": "CYP2D6",
            "drug": "tramadol",
            "phenotype": "Normal Metabolizer",
            "recommendation": "Use label-recommended dosing.",
            "classification": "B",
            "guideline_url": "https://cpicpgx.org/guidelines/guideline-for-codeine-and-cyp2d6/",
        },
        # CYP2C19 clopidogrel
        {
            "gene": "CYP2C19",
            "drug": "clopidogrel",
            "phenotype": "Intermediate Metabolizer",
            "recommendation": "Consider alternative antiplatelet therapy.",
            "classification": "A",
            "guideline_url": "https://cpicpgx.org/guidelines/guideline-for-clopidogrel-and-cyp2c19/",
        },
        {
            "gene": "CYP2C19",
            "drug": "clopidogrel",
            "phenotype": "Poor Metabolizer",
            "recommendation": "Use alternative antiplatelet therapy.",
            "classification": "A",
            "guideline_url": "https://cpicpgx.org/guidelines/guideline-for-clopidogrel-and-cyp2c19/",
        },
        {
            "gene": "CYP2C19",
            "drug": "clopidogrel",
            "phenotype": "Normal Metabolizer",
            "recommendation": "Use label-recommended dosing.",
            "classification": "A",
            "guideline_url": "https://cpicpgx.org/guidelines/guideline-for-clopidogrel-and-cyp2c19/",
        },
        # CYP2C19 voriconazole — Rapid Metabolizer row guards the gap fixed in
        # issue #8 (CYP2C19 *1/*17 is callable as Rapid Metabolizer but had no
        # voriconazole guideline, so the actionable drug-gene pair was silently
        # skipped). CPIC groups Rapid and Ultrarapid metabolizers under the same
        # voriconazole recommendation. Only the Rapid row is seeded here to keep
        # the curated fixture's other alert-count assertions unchanged.
        {
            "gene": "CYP2C19",
            "drug": "voriconazole",
            "phenotype": "Rapid Metabolizer",
            "recommendation": "Use alternative antifungal agent or increase dose with TDM.",
            "classification": "A",
            "guideline_url": "https://cpicpgx.org/guidelines/guideline-for-voriconazole-and-cyp2c19/",
        },
        # TPMT mercaptopurine
        {
            "gene": "TPMT",
            "drug": "mercaptopurine",
            "phenotype": "Normal Metabolizer",
            "recommendation": "Use label-recommended dosing.",
            "classification": "A",
            "guideline_url": "https://cpicpgx.org/guidelines/guideline-for-thiopurines-and-tpmt/",
        },
        {
            "gene": "TPMT",
            "drug": "mercaptopurine",
            "phenotype": "Intermediate Metabolizer",
            "recommendation": "Start at 30-70% of target dose. Titrate based on tolerance.",
            "classification": "A",
            "guideline_url": "https://cpicpgx.org/guidelines/guideline-for-thiopurines-and-tpmt/",
        },
        {
            "gene": "TPMT",
            "drug": "mercaptopurine",
            "phenotype": "Poor Metabolizer",
            "recommendation": (
                "For malignancy: initiate therapy with drastically reduced starting doses. "
                "Reduce starting dose by 10-fold and reduce frequency to thrice weekly instead "
                "of daily (e.g. 10 mg/m2/day given 3 days/week). During therapy, adjust "
                "mercaptopurine doses based on the degree of myelosuppression and "
                "disease-specific guidelines. It usually takes at least 4-6 weeks of stable "
                "dosing to reach steady state after each dose adjustment. If myelosuppression "
                "occurs, emphasis should be on reducing mercaptopurine over other agents. For "
                "nonmalignancy: consider alternative nonthiopurine immunosuppressant therapy."
            ),
            "classification": "A",
            "guideline_url": "https://cpicpgx.org/guidelines/guideline-for-thiopurines-and-tpmt/",
        },
        # SLCO1B1 simvastatin
        {
            "gene": "SLCO1B1",
            "drug": "simvastatin",
            "phenotype": "Normal function",
            "recommendation": "Use label-recommended dosing.",
            "classification": "A",
            "guideline_url": "https://cpicpgx.org/guidelines/guideline-for-simvastatin-and-slco1b1/",
        },
        {
            "gene": "SLCO1B1",
            "drug": "simvastatin",
            "phenotype": "Decreased function",
            "recommendation": "Use lower dose or alternative statin. Avoid 80mg dose.",
            "classification": "A",
            "guideline_url": "https://cpicpgx.org/guidelines/guideline-for-simvastatin-and-slco1b1/",
        },
    ]

    with engine.begin() as conn:
        conn.execute(cpic_alleles.insert(), alleles)
        conn.execute(cpic_diplotypes.insert(), diplotypes)
        conn.execute(cpic_guidelines.insert(), guidelines)

    return engine


def _make_sample_engine(genotypes: list[dict]) -> sa.Engine:
    """Create a sample engine with given raw variants."""
    engine = sa.create_engine("sqlite://")
    create_sample_tables(engine)
    if genotypes:
        with engine.begin() as conn:
            conn.execute(raw_variants.insert(), genotypes)
    return engine


def test_patient_visible_clause_hides_held_and_unclassifiable_alerts() -> None:
    """#2019: malformed or relabeled alert rows stay audit-only in SQL surfaces."""
    sample = _make_sample_engine([])
    with sample.begin() as conn:
        conn.execute(
            findings.insert(),
            [
                {
                    "module": "pharmacogenomics",
                    "category": "prescribing_alert",
                    "gene_symbol": "CYP2D6",
                    "drug": "codeine",
                    "finding_text": "CYP2D6/codeine control alert",
                },
                {
                    "module": "medication_review",
                    "category": "legacy_note",
                    "gene_symbol": "CYP2D6",
                    "drug": "tamoxifen",
                    "finding_text": "Relabeled tamoxifen guidance must not render.",
                },
                {
                    "module": "medication_review",
                    "category": "prescribing_alert",
                    "gene_symbol": " \t",
                    "drug": "tamoxifen",
                    "finding_text": "Blank-gene guidance must not render.",
                },
                {
                    "module": "medication_review",
                    "category": "prescribing_alert",
                    "gene_symbol": "CYP2D6",
                    "drug": "\n ",
                    "finding_text": "Blank-drug guidance must not render.",
                },
            ],
        )

    with sample.connect() as conn:
        visible = (
            conn.execute(
                sa.select(findings.c.finding_text)
                .where(patient_visible_finding_clause(findings.c))
                .order_by(findings.c.id)
            )
            .scalars()
            .all()
        )

    assert visible == ["CYP2D6/codeine control alert"]


# ═══════════════════════════════════════════════════════════════════════
# _count_alt_alleles
# ═══════════════════════════════════════════════════════════════════════


class TestCountAltAlleles:
    def test_hom_ref(self):
        assert _count_alt_alleles("CC", "C", "T") == 0

    def test_het(self):
        assert _count_alt_alleles("CT", "C", "T") == 1

    def test_het_reversed(self):
        assert _count_alt_alleles("TC", "C", "T") == 1

    def test_hom_alt(self):
        assert _count_alt_alleles("TT", "C", "T") == 2

    def test_no_call_dashes(self):
        assert _count_alt_alleles("--", "C", "T") is None

    def test_no_call_zeros(self):
        assert _count_alt_alleles("00", "C", "T") is None

    def test_indel_alleles(self):
        assert _count_alt_alleles("CT", "CT", "C") is None

    def test_deletion_indel_het(self):
        assert _count_alt_alleles("DI", "AT", "A") == 1

    def test_deletion_indel_hom_ref(self):
        assert _count_alt_alleles("II", "AT", "A") == 0

    def test_deletion_indel_hom_alt(self):
        assert _count_alt_alleles("DD", "AT", "A") == 2

    def test_deletion_indel_haploid_marker_is_uncallable(self):
        assert _count_alt_alleles("D", "AT", "A") is None

    def test_insertion_indel_het(self):
        assert _count_alt_alleles("DI", "A", "AT") == 1

    def test_equal_length_repeat_alleles_stay_uncallable(self):
        assert _count_alt_alleles("DI", "TA6", "TA7") is None

    def test_empty_genotype(self):
        assert _count_alt_alleles("", "C", "T") is None

    def test_short_genotype(self):
        assert _count_alt_alleles("C", "C", "T") is None

    def test_unexpected_bases(self):
        """Genotype with bases not matching ref or alt."""
        assert _count_alt_alleles("AG", "C", "T") is None

    def test_dd_genotype(self):
        assert _count_alt_alleles("DD", "C", "T") is None

    def test_ii_genotype(self):
        assert _count_alt_alleles("II", "C", "T") is None

    # CYP2D6*6 (rs5030655) is the 1707delT frameshift deletion — GRCh37 plus-strand
    # CA>C — not a T>C SNV (#184). It must be counted only from I/D indel tokens,
    # never from base-coded genotypes.
    def test_cyp2d6_star6_deletion_hom_alt(self):
        assert _count_alt_alleles("DD", "CA", "C") == 2  # *6/*6

    def test_cyp2d6_star6_deletion_het(self):
        assert _count_alt_alleles("DI", "CA", "C") == 1  # one *6 copy
        assert _count_alt_alleles("ID", "CA", "C") == 1

    def test_cyp2d6_star6_deletion_hom_ref(self):
        assert _count_alt_alleles("II", "CA", "C") == 0  # no *6

    def test_cyp2d6_star6_base_coded_genotype_is_uncallable(self):
        # The previous placeholder T>C SNV encoding let base-coded TC/CC be
        # mis-called as *6 carriers. The deletion encoding rejects them.
        assert _count_alt_alleles("CC", "CA", "C") is None
        assert _count_alt_alleles("TC", "CA", "C") is None
        assert _count_alt_alleles("CT", "CA", "C") is None

    def test_di_genotype(self):
        assert _count_alt_alleles("DI", "C", "T") is None


# ═══════════════════════════════════════════════════════════════════════
# _fetch_sample_genotypes
# ═══════════════════════════════════════════════════════════════════════


class TestFetchSampleGenotypes:
    def test_returns_matching_rsids(self, sample_with_variants: sa.Engine):
        result = _fetch_sample_genotypes(["rs429358", "rs7412"], sample_with_variants)
        assert result == {"rs429358": "TC", "rs7412": "CC"}

    def test_missing_rsids_excluded(self, sample_with_variants: sa.Engine):
        result = _fetch_sample_genotypes(["rs429358", "rs_nonexistent"], sample_with_variants)
        assert "rs429358" in result
        assert "rs_nonexistent" not in result

    def test_empty_list(self, sample_with_variants: sa.Engine):
        assert _fetch_sample_genotypes([], sample_with_variants) == {}


# ═══════════════════════════════════════════════════════════════════════
# _fetch_alleles_for_gene
# ═══════════════════════════════════════════════════════════════════════


class TestFetchAllelesForGene:
    def test_returns_alleles(self, pgx_reference_engine: sa.Engine):
        alleles = _fetch_alleles_for_gene("CYP2C19", pgx_reference_engine)
        names = [a["allele_name"] for a in alleles]
        assert "*1" in names
        assert "*2" in names
        assert "*17" in names

    def test_defining_variants_parsed(self, pgx_reference_engine: sa.Engine):
        alleles = _fetch_alleles_for_gene("CYP2C19", pgx_reference_engine)
        star2 = next(a for a in alleles if a["allele_name"] == "*2")
        assert isinstance(star2["defining_variants"], list)
        assert star2["defining_variants"][0]["rsid"] == "rs4244285"

    def test_unknown_gene_returns_empty(self, pgx_reference_engine: sa.Engine):
        assert _fetch_alleles_for_gene("FAKEGENE", pgx_reference_engine) == []


# ═══════════════════════════════════════════════════════════════════════
# _fetch_diplotype_phenotype
# ═══════════════════════════════════════════════════════════════════════


class TestFetchDiplotypePhenotype:
    def test_known_diplotype(self, pgx_reference_engine: sa.Engine):
        result = _fetch_diplotype_phenotype("CYP2C19", "*1/*2", pgx_reference_engine)
        assert result is not None
        assert result["phenotype"] == "Intermediate Metabolizer"

    def test_unknown_diplotype_returns_none(self, pgx_reference_engine: sa.Engine):
        result = _fetch_diplotype_phenotype("CYP2C19", "*99/*99", pgx_reference_engine)
        assert result is None


# ═══════════════════════════════════════════════════════════════════════
# call_star_alleles_for_gene — CYP2C19
# ═══════════════════════════════════════════════════════════════════════


class TestCallStarAllelesCYP2C19:
    """T3-01 + T3-02: CYP2C19 star-allele calling and phenotype assignment."""

    def test_cyp2c19_star1_star2_het(self, pgx_reference_engine: sa.Engine):
        """T3-01: CYP2C19 rs4244285 GA → *1/*2.
        T3-02: CYP2C19 *1/*2 → Intermediate Metabolizer.
        """
        alleles = _fetch_alleles_for_gene("CYP2C19", pgx_reference_engine)
        genotypes = {"rs4244285": "GA"}

        result = call_star_alleles_for_gene("CYP2C19", alleles, genotypes, pgx_reference_engine)
        assert result.gene == "CYP2C19"
        assert result.diplotype == "*1/*2"
        assert result.phenotype == "Intermediate Metabolizer"
        assert "rs4244285" in result.involved_rsids

    def test_cyp2c19_star1_star1_wildtype(self, pgx_reference_engine: sa.Engine):
        """All defining rsids are ref → *1/*1 Normal Metabolizer."""
        alleles = _fetch_alleles_for_gene("CYP2C19", pgx_reference_engine)
        genotypes = {"rs4244285": "GG", "rs4986893": "GG", "rs12248560": "CC"}

        result = call_star_alleles_for_gene("CYP2C19", alleles, genotypes, pgx_reference_engine)
        assert result.diplotype == "*1/*1"
        assert result.phenotype == "Normal Metabolizer"
        assert result.involved_rsids == set()
        assert result.assessed_rsids == {"rs4244285", "rs4986893", "rs12248560"}
        assert result.coverage_assessed == 3

    def test_cyp2c19_star2_star2_hom(self, pgx_reference_engine: sa.Engine):
        """rs4244285 AA → *2/*2 → Poor Metabolizer."""
        alleles = _fetch_alleles_for_gene("CYP2C19", pgx_reference_engine)
        genotypes = {"rs4244285": "AA"}

        result = call_star_alleles_for_gene("CYP2C19", alleles, genotypes, pgx_reference_engine)
        assert result.diplotype == "*2/*2"
        assert result.phenotype == "Poor Metabolizer"

    def test_cyp2c19_star1_star17_rapid(self, pgx_reference_engine: sa.Engine):
        """rs12248560 CT → *1/*17 → Rapid Metabolizer."""
        alleles = _fetch_alleles_for_gene("CYP2C19", pgx_reference_engine)
        genotypes = {"rs4244285": "GG", "rs12248560": "CT"}

        result = call_star_alleles_for_gene("CYP2C19", alleles, genotypes, pgx_reference_engine)
        assert result.diplotype == "*1/*17"
        assert result.phenotype == "Rapid Metabolizer"

    def test_cyp2c19_star2_star17(self, pgx_reference_engine: sa.Engine):
        """rs4244285 GA + rs12248560 CT → *2/*17 → IM."""
        alleles = _fetch_alleles_for_gene("CYP2C19", pgx_reference_engine)
        genotypes = {"rs4244285": "GA", "rs12248560": "CT"}

        result = call_star_alleles_for_gene("CYP2C19", alleles, genotypes, pgx_reference_engine)
        assert result.diplotype == "*2/*17"
        assert result.phenotype == "Intermediate Metabolizer"

    def test_cyp2c19_missing_rsids(self, pgx_reference_engine: sa.Engine):
        """No genotype data → *1/*1 with missing_rsids populated."""
        alleles = _fetch_alleles_for_gene("CYP2C19", pgx_reference_engine)
        genotypes: dict[str, str] = {}

        result = call_star_alleles_for_gene("CYP2C19", alleles, genotypes, pgx_reference_engine)
        assert result.diplotype == "*1/*1"
        assert len(result.missing_rsids) > 0
        assert "rs4244285" in result.missing_rsids
        assert result.assessed_rsids == set()


# ═══════════════════════════════════════════════════════════════════════
# call_star_alleles_for_gene — CYP2D6
# ═══════════════════════════════════════════════════════════════════════


class TestCallStarAllelesCYP2D6:
    def test_cyp2d6_star1_star4(self, pgx_reference_engine: sa.Engine):
        """rs3892097 CT → *1/*4 → Intermediate Metabolizer."""
        alleles = _fetch_alleles_for_gene("CYP2D6", pgx_reference_engine)
        genotypes = {"rs16947": "GG", "rs3892097": "CT", "rs1065852": "GG"}

        result = call_star_alleles_for_gene("CYP2D6", alleles, genotypes, pgx_reference_engine)
        assert result.diplotype == "*1/*4"
        assert result.phenotype == "Intermediate Metabolizer"

    def test_cyp2d6_star4_star4(self, pgx_reference_engine: sa.Engine):
        """rs3892097 TT → *4/*4 → Poor Metabolizer."""
        alleles = _fetch_alleles_for_gene("CYP2D6", pgx_reference_engine)
        genotypes = {"rs16947": "GG", "rs3892097": "TT", "rs1065852": "GG"}

        result = call_star_alleles_for_gene("CYP2D6", alleles, genotypes, pgx_reference_engine)
        assert result.diplotype == "*4/*4"
        assert result.phenotype == "Poor Metabolizer"

    def test_cyp2d6_star2_star4(self, pgx_reference_engine: sa.Engine):
        """rs16947 CT + rs3892097 CT → *2/*4 → IM."""
        alleles = _fetch_alleles_for_gene("CYP2D6", pgx_reference_engine)
        genotypes = {"rs16947": "AG", "rs3892097": "CT", "rs1065852": "GG"}

        result = call_star_alleles_for_gene("CYP2D6", alleles, genotypes, pgx_reference_engine)
        assert result.diplotype == "*2/*4"
        assert result.phenotype == "Intermediate Metabolizer"
        assert "rs16947" in result.involved_rsids
        assert "rs3892097" in result.involved_rsids

    def test_cyp2d6_star1_star2(self, pgx_reference_engine: sa.Engine):
        """rs16947 CT, others ref → *1/*2 → Normal Metabolizer."""
        alleles = _fetch_alleles_for_gene("CYP2D6", pgx_reference_engine)
        genotypes = {"rs16947": "AG", "rs3892097": "CC", "rs1065852": "GG"}

        result = call_star_alleles_for_gene("CYP2D6", alleles, genotypes, pgx_reference_engine)
        assert result.diplotype == "*1/*2"
        assert result.phenotype == "Normal Metabolizer"

    def test_cyp2d6_wildtype(self, pgx_reference_engine: sa.Engine):
        """All ref → *1/*1."""
        alleles = _fetch_alleles_for_gene("CYP2D6", pgx_reference_engine)
        genotypes = {"rs16947": "GG", "rs3892097": "CC", "rs1065852": "GG"}

        result = call_star_alleles_for_gene("CYP2D6", alleles, genotypes, pgx_reference_engine)
        assert result.diplotype == "*1/*1"
        assert result.phenotype == "Normal Metabolizer"


# ═══════════════════════════════════════════════════════════════════════
# Multi-variant allele handling (TPMT *3A, SLCO1B1 *15)
# ═══════════════════════════════════════════════════════════════════════


class TestMultiVariantAlleles:
    def test_tpmt_star3a_het(self, pgx_reference_engine: sa.Engine):
        """Both *3A defining variants het → phase-ambiguous *1/*3A."""
        alleles = _fetch_alleles_for_gene("TPMT", pgx_reference_engine)
        genotypes = {"rs1800460": "CT", "rs1142345": "CT"}

        result = call_star_alleles_for_gene("TPMT", alleles, genotypes, pgx_reference_engine)
        # *3A is most specific (2 variants), but unphased data cannot distinguish
        # cis *1/*3A from trans *3B/*3C.
        assert result.diplotype == "*1/*3A"
        assert result.phenotype == "Intermediate Metabolizer"
        assert result.call_confidence == CallConfidence.PARTIAL
        assert "*3B/*3C" in result.confidence_note
        assert "Poor Metabolizer" in result.confidence_note

    def test_tpmt_star3b_only(self, pgx_reference_engine: sa.Engine):
        """Only rs1800460 het, rs1142345 ref → *1/*3B."""
        alleles = _fetch_alleles_for_gene("TPMT", pgx_reference_engine)
        genotypes = {"rs1800460": "CT", "rs1142345": "TT"}

        result = call_star_alleles_for_gene("TPMT", alleles, genotypes, pgx_reference_engine)
        assert result.diplotype == "*1/*3B"
        assert result.phenotype == "Intermediate Metabolizer"

    def test_tpmt_star3c_only(self, pgx_reference_engine: sa.Engine):
        """Only rs1142345 het, rs1800460 ref → *1/*3C."""
        alleles = _fetch_alleles_for_gene("TPMT", pgx_reference_engine)
        genotypes = {"rs1800460": "CC", "rs1142345": "CT"}

        result = call_star_alleles_for_gene("TPMT", alleles, genotypes, pgx_reference_engine)
        assert result.diplotype == "*1/*3C"
        assert result.phenotype == "Intermediate Metabolizer"

    def test_tpmt_wildtype(self, pgx_reference_engine: sa.Engine):
        """All ref → *1/*1."""
        alleles = _fetch_alleles_for_gene("TPMT", pgx_reference_engine)
        genotypes = {"rs1800460": "CC", "rs1142345": "TT"}

        result = call_star_alleles_for_gene("TPMT", alleles, genotypes, pgx_reference_engine)
        assert result.diplotype == "*1/*1"
        assert result.phenotype == "Normal Metabolizer"

    def test_slco1b1_star15_het(self, pgx_reference_engine: sa.Engine):
        """Both *15 defining variants het → *1A/*15 (most specific)."""
        alleles = _fetch_alleles_for_gene("SLCO1B1", pgx_reference_engine)
        genotypes = {"rs2306283": "AG", "rs4149056": "TC"}

        result = call_star_alleles_for_gene("SLCO1B1", alleles, genotypes, pgx_reference_engine)
        assert result.diplotype == "*1A/*15"
        assert result.phenotype == "Decreased function"

    def test_slco1b1_star1b_only(self, pgx_reference_engine: sa.Engine):
        """Only rs2306283 het → *1A/*1B."""
        alleles = _fetch_alleles_for_gene("SLCO1B1", pgx_reference_engine)
        genotypes = {"rs2306283": "AG", "rs4149056": "TT"}

        result = call_star_alleles_for_gene("SLCO1B1", alleles, genotypes, pgx_reference_engine)
        assert result.diplotype == "*1A/*1B"
        assert result.phenotype == "Normal function"


# ═══════════════════════════════════════════════════════════════════════
# Edge cases
# ═══════════════════════════════════════════════════════════════════════


class TestEdgeCases:
    def test_no_call_genotype(self, pgx_reference_engine: sa.Engine):
        """No-call genotype → treated as missing → uncalled_rsids populated."""
        alleles = _fetch_alleles_for_gene("CYP2C19", pgx_reference_engine)
        genotypes = {"rs4244285": "--"}

        result = call_star_alleles_for_gene("CYP2C19", alleles, genotypes, pgx_reference_engine)
        assert result.diplotype == "*1/*1"
        assert "rs4244285" in result.uncalled_rsids

    def test_empty_genotypes(self, pgx_reference_engine: sa.Engine):
        """No sample data → defaults to reference alleles."""
        alleles = _fetch_alleles_for_gene("CYP2D6", pgx_reference_engine)

        result = call_star_alleles_for_gene("CYP2D6", alleles, {}, pgx_reference_engine)
        assert result.diplotype == "*1/*1"
        assert len(result.missing_rsids) > 0

    def test_involved_rsids_correct(self, pgx_reference_engine: sa.Engine):
        """involved_rsids only includes rsids that contributed to a call."""
        alleles = _fetch_alleles_for_gene("CYP2D6", pgx_reference_engine)
        genotypes = {
            "rs16947": "GG",  # ref → not involved
            "rs3892097": "CT",  # alt → involved (*4)
            "rs1065852": "GG",  # ref → not involved
        }

        result = call_star_alleles_for_gene("CYP2D6", alleles, genotypes, pgx_reference_engine)
        assert result.involved_rsids == {"rs3892097"}

    def test_result_dataclass_fields(self, pgx_reference_engine: sa.Engine):
        """StarAlleleResult has all expected fields."""
        alleles = _fetch_alleles_for_gene("CYP2C19", pgx_reference_engine)
        result = call_star_alleles_for_gene(
            "CYP2C19", alleles, {"rs4244285": "GA"}, pgx_reference_engine
        )
        assert isinstance(result, StarAlleleResult)
        assert result.gene == "CYP2C19"
        assert result.allele1 is not None
        assert result.allele2 is not None
        assert result.ehr_notation is not None


# ═══════════════════════════════════════════════════════════════════════
# call_all_star_alleles — integration
# ═══════════════════════════════════════════════════════════════════════


class TestCallAllStarAlleles:
    def test_calls_specified_genes(self, pgx_reference_engine: sa.Engine):
        """Calling a subset of genes returns results only for those genes."""
        sample = _make_sample_engine(
            [
                {"rsid": "rs4244285", "chrom": "10", "pos": 96541616, "genotype": "GA"},
                {"rsid": "rs16947", "chrom": "22", "pos": 42522613, "genotype": "AG"},
            ]
        )

        results = call_all_star_alleles(
            pgx_reference_engine,
            sample,
            genes=frozenset({"CYP2C19", "CYP2D6"}),
        )

        gene_names = [r.gene for r in results]
        assert "CYP2C19" in gene_names
        assert "CYP2D6" in gene_names
        assert len(results) == 2

    def test_cyp2c19_star1_star2_integration(self, pgx_reference_engine: sa.Engine):
        """Full pipeline: sample with rs4244285 GA → CYP2C19 *1/*2 IM."""
        sample = _make_sample_engine(
            [
                {"rsid": "rs4244285", "chrom": "10", "pos": 96541616, "genotype": "GA"},
            ]
        )

        results = call_all_star_alleles(
            pgx_reference_engine,
            sample,
            genes=frozenset({"CYP2C19"}),
        )

        assert len(results) == 1
        r = results[0]
        assert r.gene == "CYP2C19"
        assert r.diplotype == "*1/*2"
        assert r.phenotype == "Intermediate Metabolizer"

    def test_multiple_genes(self, pgx_reference_engine: sa.Engine):
        """Multiple genes called in one pass."""
        sample = _make_sample_engine(
            [
                {"rsid": "rs4244285", "chrom": "10", "pos": 96541616, "genotype": "GA"},
                {"rsid": "rs3892097", "chrom": "22", "pos": 42524947, "genotype": "CT"},
                {"rsid": "rs16947", "chrom": "22", "pos": 42522613, "genotype": "GG"},
                {"rsid": "rs1065852", "chrom": "22", "pos": 42525772, "genotype": "GG"},
            ]
        )

        results = call_all_star_alleles(
            pgx_reference_engine,
            sample,
            genes=frozenset({"CYP2C19", "CYP2D6"}),
        )

        by_gene = {r.gene: r for r in results}
        assert by_gene["CYP2C19"].diplotype == "*1/*2"
        assert by_gene["CYP2D6"].diplotype == "*1/*4"

    def test_no_data_defaults_to_wildtype(self, pgx_reference_engine: sa.Engine):
        """Gene with no sample data → *1/*1, Normal Metabolizer."""
        sample = _make_sample_engine([])

        results = call_all_star_alleles(
            pgx_reference_engine,
            sample,
            genes=frozenset({"CYP2C19"}),
        )

        assert results[0].diplotype == "*1/*1"
        # Assert the phenotype call too — the diplotype alone doesn't prove the
        # *1/*1 → phenotype lookup ran; a broken mapping could leave it None or
        # mislabel it while the diplotype string still reads "*1/*1".
        assert results[0].phenotype == "Normal Metabolizer"

    def test_absent_data_produces_no_risk_phenotype_alerts(self, pgx_reference_engine: sa.Engine):
        """Absent pgx data must default to Normal Metabolizer, never a risk call.

        The genotype-agnostic guard for the diplotype path: a sample with no
        defining variants must resolve every gene to *1/*1 Normal Metabolizer,
        and ``generate_prescribing_alerts`` must not fabricate a Poor / Rapid /
        Intermediate / Ultrarapid metabolizer alert from that absence (such a
        miscall would drive a real prescribing change).
        """
        sample = _make_sample_engine([])

        results = call_all_star_alleles(
            pgx_reference_engine,
            sample,
            genes=frozenset({"CYP2C19", "CYP2D6"}),
        )
        for r in results:
            assert r.diplotype == "*1/*1", f"{r.gene} not wildtype: {r.diplotype}"
            assert r.phenotype == "Normal Metabolizer", f"{r.gene}: {r.phenotype}"

        alerts = generate_prescribing_alerts(results, pgx_reference_engine)
        non_normal = [(a.gene, a.phenotype) for a in alerts if a.phenotype != "Normal Metabolizer"]
        assert not non_normal, f"absent data produced risk-metabolizer alerts: {non_normal}"

    def test_results_sorted_by_gene(self, pgx_reference_engine: sa.Engine):
        """Results are sorted alphabetically by gene name."""
        sample = _make_sample_engine([])

        results = call_all_star_alleles(
            pgx_reference_engine,
            sample,
            genes=frozenset({"TPMT", "CYP2D6", "CYP2C19"}),
        )

        genes = [r.gene for r in results]
        assert genes == sorted(genes)

    def test_gene_not_in_reference_skipped(self, pgx_reference_engine: sa.Engine):
        """Gene with no allele definitions in reference is skipped."""
        sample = _make_sample_engine([])

        results = call_all_star_alleles(
            pgx_reference_engine,
            sample,
            genes=frozenset({"UGT1A1"}),  # No alleles loaded for this gene
        )

        assert len(results) == 0

    def test_with_seeded_conftest_data(
        self, seeded_reference_engine: sa.Engine, sample_with_variants: sa.Engine
    ):
        """Integration with conftest seeded data: CYP2C19 rs4244285 GA → *1/*2."""
        results = call_all_star_alleles(
            seeded_reference_engine,
            sample_with_variants,
            genes=frozenset({"CYP2C19"}),
        )

        assert len(results) == 1
        r = results[0]
        assert r.gene == "CYP2C19"
        assert r.diplotype == "*1/*2"
        assert r.phenotype == "Intermediate Metabolizer"


# ═══════════════════════════════════════════════════════════════════════
# _assess_call_confidence — unit tests (P3-03)
# ═══════════════════════════════════════════════════════════════════════


class TestAssessCallConfidence:
    """Unit tests for the three-state calling confidence logic."""

    def test_complete_all_rsids_present(self):
        """Gene with all defining rsids genotyped → Complete."""
        conf, note = _assess_call_confidence(
            "CYP2C19",
            all_defining_rsids={"rs4244285", "rs4986893", "rs12248560"},
            missing_rsids=set(),
            uncalled_rsids=set(),
        )
        assert conf == CallConfidence.COMPLETE
        assert "All defining positions" in note

    def test_partial_structural_variant_gene(self):
        """CYP2D6 with all SNP rsids present → still Partial."""
        conf, note = _assess_call_confidence(
            "CYP2D6",
            all_defining_rsids={"rs16947", "rs3892097", "rs1065852"},
            missing_rsids=set(),
            uncalled_rsids=set(),
        )
        assert conf == CallConfidence.PARTIAL
        assert "structural variant" in note
        assert "provisional" in note

    def test_partial_cyp2b6(self):
        """CYP2B6 is also a structural variant gene → Partial."""
        conf, note = _assess_call_confidence(
            "CYP2B6",
            all_defining_rsids={"rs3745274"},
            missing_rsids=set(),
            uncalled_rsids=set(),
        )
        assert conf == CallConfidence.PARTIAL

    def test_partial_some_rsids_missing(self):
        """Non-SV gene with 1/3 rsids missing (≤50%) → Partial."""
        conf, note = _assess_call_confidence(
            "CYP2C19",
            all_defining_rsids={"rs4244285", "rs4986893", "rs12248560"},
            missing_rsids={"rs4986893"},
            uncalled_rsids=set(),
        )
        assert conf == CallConfidence.PARTIAL
        assert "1/3" in note
        assert "rs4986893" in note

    def test_partial_uncalled_rsids(self):
        """Uncalled rsids (no-call genotypes) count toward unusable."""
        conf, note = _assess_call_confidence(
            "TPMT",
            all_defining_rsids={"rs1800460", "rs1142345"},
            missing_rsids=set(),
            uncalled_rsids={"rs1142345"},
        )
        assert conf == CallConfidence.PARTIAL
        assert "rs1142345" in note

    def test_insufficient_just_above_half(self):
        """Three of five unusable rsids (>50%) → Insufficient."""
        conf, note = _assess_call_confidence(
            "CYP2C19",
            all_defining_rsids={"rs1", "rs2", "rs3", "rs4", "rs5"},
            missing_rsids={"rs1", "rs2", "rs3"},
            uncalled_rsids=set(),
        )
        assert conf == CallConfidence.INSUFFICIENT
        assert "3/5" in note

    def test_insufficient_majority_missing(self):
        """More than 50% of defining rsids missing → Insufficient."""
        conf, note = _assess_call_confidence(
            "CYP2C19",
            all_defining_rsids={"rs4244285", "rs4986893", "rs12248560"},
            missing_rsids={"rs4244285", "rs4986893"},
            uncalled_rsids=set(),
        )
        assert conf == CallConfidence.INSUFFICIENT
        assert "unreliable" in note

    def test_insufficient_all_missing(self):
        """All defining rsids missing → Insufficient."""
        conf, note = _assess_call_confidence(
            "SLCO1B1",
            all_defining_rsids={"rs2306283", "rs4149056"},
            missing_rsids={"rs2306283", "rs4149056"},
            uncalled_rsids=set(),
        )
        assert conf == CallConfidence.INSUFFICIENT

    def test_insufficient_mixed_missing_and_uncalled(self):
        """Missing + uncalled together exceed 50% → Insufficient."""
        conf, note = _assess_call_confidence(
            "CYP2C19",
            all_defining_rsids={"rs4244285", "rs4986893", "rs12248560"},
            missing_rsids={"rs4244285"},
            uncalled_rsids={"rs4986893"},
            # 2/3 = 67% unusable
        )
        assert conf == CallConfidence.INSUFFICIENT

    def test_no_defining_rsids_non_sv_gene(self):
        """Gene with no defining rsids (reference-only) → Complete."""
        conf, note = _assess_call_confidence(
            "TPMT",
            all_defining_rsids=set(),
            missing_rsids=set(),
            uncalled_rsids=set(),
        )
        assert conf == CallConfidence.COMPLETE

    def test_no_defining_rsids_sv_gene(self):
        """SV gene with no defining rsids → still Partial."""
        conf, note = _assess_call_confidence(
            "CYP2D6",
            all_defining_rsids=set(),
            missing_rsids=set(),
            uncalled_rsids=set(),
        )
        assert conf == CallConfidence.PARTIAL

    def test_insufficient_note_truncates_long_list(self):
        """Notes with >5 missing rsids are truncated."""
        rsids = {f"rs{i}" for i in range(10)}
        conf, note = _assess_call_confidence(
            "FAKEGENE",
            all_defining_rsids=rsids,
            missing_rsids=rsids,
            uncalled_rsids=set(),
        )
        assert conf == CallConfidence.INSUFFICIENT
        assert "and 5 more" in note


# ═══════════════════════════════════════════════════════════════════════
# Three-state calling confidence — integrated tests (P3-03)
# ═══════════════════════════════════════════════════════════════════════


class TestCallConfidenceIntegrated:
    """Tests that call_star_alleles_for_gene correctly sets call_confidence."""

    def test_cyp2c19_complete_all_rsids(self, pgx_reference_engine: sa.Engine):
        """CYP2C19 with all defining rsids genotyped → Complete."""
        alleles = _fetch_alleles_for_gene("CYP2C19", pgx_reference_engine)
        genotypes = {"rs4244285": "GG", "rs4986893": "GG", "rs12248560": "CC"}

        result = call_star_alleles_for_gene("CYP2C19", alleles, genotypes, pgx_reference_engine)
        assert result.call_confidence == CallConfidence.COMPLETE

    def test_cyp2d6_partial_structural_variant(self, pgx_reference_engine: sa.Engine):
        """T3-04: CYP2D6 calling produces Partial state with structural variant caveat."""
        alleles = _fetch_alleles_for_gene("CYP2D6", pgx_reference_engine)
        genotypes = {"rs16947": "AG", "rs3892097": "CC", "rs1065852": "GG"}

        result = call_star_alleles_for_gene("CYP2D6", alleles, genotypes, pgx_reference_engine)
        assert result.call_confidence == CallConfidence.PARTIAL
        assert "structural variant" in result.confidence_note
        assert "provisional" in result.confidence_note
        # Diplotype still called correctly
        assert result.diplotype == "*1/*2"
        assert result.phenotype == "Normal Metabolizer"

    def test_cyp2c19_insufficient_majority_missing(self, pgx_reference_engine: sa.Engine):
        """CYP2C19 with 2/3 rsids missing → Insufficient."""
        alleles = _fetch_alleles_for_gene("CYP2C19", pgx_reference_engine)
        # Only provide 1 of 3 defining rsids
        genotypes = {"rs4244285": "GA"}
        # rs4986893, rs12248560 are missing (2/3 = 67% > 50%)

        result = call_star_alleles_for_gene("CYP2C19", alleles, genotypes, pgx_reference_engine)
        assert result.call_confidence == CallConfidence.INSUFFICIENT

    def test_cyp2c19_insufficient_all_missing(self, pgx_reference_engine: sa.Engine):
        """CYP2C19 with no data → Insufficient."""
        alleles = _fetch_alleles_for_gene("CYP2C19", pgx_reference_engine)

        result = call_star_alleles_for_gene("CYP2C19", alleles, {}, pgx_reference_engine)
        assert result.call_confidence == CallConfidence.INSUFFICIENT
        assert "unreliable" in result.confidence_note

    def test_tpmt_complete_all_rsids(self, pgx_reference_engine: sa.Engine):
        """TPMT with all defining rsids → Complete."""
        alleles = _fetch_alleles_for_gene("TPMT", pgx_reference_engine)
        genotypes = {"rs1800460": "CC", "rs1142345": "TT"}

        result = call_star_alleles_for_gene("TPMT", alleles, genotypes, pgx_reference_engine)
        assert result.call_confidence == CallConfidence.COMPLETE

    def test_tpmt_partial_one_uncalled(self, pgx_reference_engine: sa.Engine):
        """TPMT with 1/2 rsids as no-call → Partial (50% exactly)."""
        alleles = _fetch_alleles_for_gene("TPMT", pgx_reference_engine)
        genotypes = {"rs1800460": "CC", "rs1142345": "--"}  # no-call

        result = call_star_alleles_for_gene("TPMT", alleles, genotypes, pgx_reference_engine)
        assert result.call_confidence == CallConfidence.PARTIAL

    def test_slco1b1_complete(self, pgx_reference_engine: sa.Engine):
        """SLCO1B1 with all rsids genotyped → Complete."""
        alleles = _fetch_alleles_for_gene("SLCO1B1", pgx_reference_engine)
        genotypes = {"rs2306283": "AA", "rs4149056": "TT"}

        result = call_star_alleles_for_gene("SLCO1B1", alleles, genotypes, pgx_reference_engine)
        assert result.call_confidence == CallConfidence.COMPLETE

    def test_call_all_includes_confidence(self, pgx_reference_engine: sa.Engine):
        """call_all_star_alleles results include call_confidence field."""
        sample = _make_sample_engine(
            [
                {"rsid": "rs4244285", "chrom": "10", "pos": 96541616, "genotype": "GA"},
                {"rsid": "rs4986893", "chrom": "10", "pos": 96540410, "genotype": "GG"},
                {"rsid": "rs12248560", "chrom": "10", "pos": 96521657, "genotype": "CC"},
                {"rsid": "rs16947", "chrom": "22", "pos": 42522613, "genotype": "AG"},
                {"rsid": "rs3892097", "chrom": "22", "pos": 42524947, "genotype": "CC"},
                {"rsid": "rs1065852", "chrom": "22", "pos": 42525772, "genotype": "GG"},
            ]
        )

        results = call_all_star_alleles(
            pgx_reference_engine,
            sample,
            genes=frozenset({"CYP2C19", "CYP2D6"}),
        )

        by_gene = {r.gene: r for r in results}
        # CYP2C19 has all defining rsids → Complete
        assert by_gene["CYP2C19"].call_confidence == CallConfidence.COMPLETE
        # CYP2D6 is structural variant gene → always Partial
        assert by_gene["CYP2D6"].call_confidence == CallConfidence.PARTIAL


# ═══════════════════════════════════════════════════════════════════════
# CallConfidence enum tests
# ═══════════════════════════════════════════════════════════════════════


class TestCallConfidenceEnum:
    def test_values(self):
        assert CallConfidence.COMPLETE.value == "Complete"
        assert CallConfidence.PARTIAL.value == "Partial"
        assert CallConfidence.INSUFFICIENT.value == "Insufficient"

    def test_members(self):
        assert len(CallConfidence) == 3


# ═══════════════════════════════════════════════════════════════════════
# _fetch_guidelines_for_gene_phenotype (P3-04)
# ═══════════════════════════════════════════════════════════════════════


class TestFetchGuidelinesForGenePhenotype:
    def test_returns_matching_guidelines(self, pgx_reference_engine: sa.Engine):
        results = _fetch_guidelines_for_gene_phenotype(
            "CYP2D6", "Poor Metabolizer", pgx_reference_engine
        )
        assert len(results) == 2
        drugs = {r["drug"] for r in results}
        assert drugs == {"codeine", "tramadol"}

    def test_single_drug_match(self, pgx_reference_engine: sa.Engine):
        results = _fetch_guidelines_for_gene_phenotype(
            "CYP2C19", "Intermediate Metabolizer", pgx_reference_engine
        )
        assert len(results) == 1
        assert results[0]["drug"] == "clopidogrel"
        assert results[0]["classification"] == "A"

    def test_activity_score_precedence_is_per_drug_and_preserves_duplicates(
        self, pgx_reference_engine: sa.Engine
    ):
        rows = [
            {
                "gene": "TEST",
                "drug": "exact-drug",
                "phenotype": "Intermediate Metabolizer",
                "activity_score": None,
                "recommendation": "generic exact-drug fallback",
            },
            {
                "gene": "TEST",
                "drug": "exact-drug",
                "phenotype": "Intermediate Metabolizer",
                "activity_score": 1.5,
                "recommendation": "exact row one",
            },
            {
                "gene": "TEST",
                "drug": "exact-drug",
                "phenotype": "Intermediate Metabolizer",
                "activity_score": 1.5,
                "recommendation": "exact row two",
            },
            {
                "gene": "TEST",
                "drug": "generic-drug",
                "phenotype": "Intermediate Metabolizer",
                "activity_score": None,
                "recommendation": "generic other-drug fallback",
            },
        ]
        with pgx_reference_engine.begin() as conn:
            conn.execute(cpic_guidelines.insert(), rows)

        exact = _fetch_guidelines_for_gene_phenotype(
            "TEST",
            "Intermediate Metabolizer",
            pgx_reference_engine,
            activity_score=1.5,
        )
        assert [(row["drug"], row["recommendation"]) for row in exact] == [
            ("exact-drug", "exact row one"),
            ("exact-drug", "exact row two"),
            ("generic-drug", "generic other-drug fallback"),
        ]

        fallback = _fetch_guidelines_for_gene_phenotype(
            "TEST",
            "Intermediate Metabolizer",
            pgx_reference_engine,
            activity_score=1.0,
        )
        assert [(row["drug"], row["recommendation"]) for row in fallback] == [
            ("exact-drug", "generic exact-drug fallback"),
            ("generic-drug", "generic other-drug fallback"),
        ]

    def test_no_match_returns_empty(self, pgx_reference_engine: sa.Engine):
        results = _fetch_guidelines_for_gene_phenotype(
            "CYP2D6", "Nonexistent Phenotype", pgx_reference_engine
        )
        assert results == []

    def test_unknown_gene_returns_empty(self, pgx_reference_engine: sa.Engine):
        results = _fetch_guidelines_for_gene_phenotype(
            "FAKEGENE", "Normal Metabolizer", pgx_reference_engine
        )
        assert results == []


# ═══════════════════════════════════════════════════════════════════════
# generate_prescribing_alerts (P3-04)
# ═══════════════════════════════════════════════════════════════════════


class TestGeneratePrescribingAlerts:
    """Test prescribing alert generation from star-allele results."""

    def test_cyp2d6_poor_metabolizer_alerts(self, pgx_reference_engine: sa.Engine):
        """CYP2D6 Poor Metabolizer should generate codeine + tramadol alerts."""
        results = [
            StarAlleleResult(
                gene="CYP2D6",
                allele1="*4",
                allele2="*4",
                diplotype="*4/*4",
                phenotype="Poor Metabolizer",
                ehr_notation="CYP2D6 Poor Metabolizer",
                activity_score=0.0,
                call_confidence=CallConfidence.PARTIAL,
                confidence_note="Structural variant gene.",
                involved_rsids={"rs3892097"},
            ),
        ]

        alerts = generate_prescribing_alerts(results, pgx_reference_engine)
        assert len(alerts) == 2
        drugs = {a.drug for a in alerts}
        assert drugs == {"codeine", "tramadol"}

        codeine_alert = next(a for a in alerts if a.drug == "codeine")
        assert codeine_alert.gene == "CYP2D6"
        assert codeine_alert.diplotype == "*4/*4"
        assert codeine_alert.phenotype == "Poor Metabolizer"
        assert "Avoid codeine" in codeine_alert.recommendation
        assert codeine_alert.classification == "A"
        assert codeine_alert.evidence_level == 4  # CPIC A → ★★★★
        assert codeine_alert.call_confidence == CallConfidence.PARTIAL

        tramadol_alert = next(a for a in alerts if a.drug == "tramadol")
        assert tramadol_alert.classification == "B"
        assert tramadol_alert.evidence_level == 3  # CPIC B → ★★★

    def test_cyp2c19_intermediate_metabolizer(self, pgx_reference_engine: sa.Engine):
        """CYP2C19 IM should generate clopidogrel alert."""
        results = [
            StarAlleleResult(
                gene="CYP2C19",
                allele1="*1",
                allele2="*2",
                diplotype="*1/*2",
                phenotype="Intermediate Metabolizer",
                call_confidence=CallConfidence.COMPLETE,
                confidence_note="All defining positions assessed.",
                involved_rsids={"rs4244285"},
            ),
        ]

        alerts = generate_prescribing_alerts(results, pgx_reference_engine)
        assert len(alerts) == 1
        assert alerts[0].drug == "clopidogrel"
        assert "alternative antiplatelet" in alerts[0].recommendation
        assert alerts[0].call_confidence == CallConfidence.COMPLETE

    def test_cyp2c19_rapid_metabolizer_voriconazole(self, pgx_reference_engine: sa.Engine):
        """CYP2C19 *1/*17 Rapid Metabolizer should generate a voriconazole alert.

        Regression for issue #8: the rapid metabolizer phenotype is callable but
        the bundled CPIC table had no CYP2C19/voriconazole/Rapid Metabolizer row,
        so this actionable drug-gene pair was silently skipped despite voriconazole
        being primarily CYP2C19-metabolized.
        """
        results = [
            StarAlleleResult(
                gene="CYP2C19",
                allele1="*1",
                allele2="*17",
                diplotype="*1/*17",
                phenotype="Rapid Metabolizer",
                call_confidence=CallConfidence.COMPLETE,
                confidence_note="All defining positions assessed.",
                involved_rsids={"rs12248560"},
            ),
        ]

        alerts = generate_prescribing_alerts(results, pgx_reference_engine)
        vori = [a for a in alerts if a.drug == "voriconazole"]
        assert len(vori) == 1
        assert vori[0].gene == "CYP2C19"
        assert vori[0].diplotype == "*1/*17"
        assert vori[0].phenotype == "Rapid Metabolizer"
        assert "alternative antifungal" in vori[0].recommendation
        assert vori[0].classification == "A"
        assert vori[0].evidence_level == 4  # CPIC A → ★★★★

    def test_insufficient_confidence_excluded(self, pgx_reference_engine: sa.Engine):
        """Genes with Insufficient confidence produce no alerts."""
        results = [
            StarAlleleResult(
                gene="CYP2D6",
                allele1="*1",
                allele2="*1",
                diplotype="*1/*1",
                phenotype="Normal Metabolizer",
                call_confidence=CallConfidence.INSUFFICIENT,
                confidence_note="3/3 defining positions missing.",
            ),
        ]

        alerts = generate_prescribing_alerts(results, pgx_reference_engine)
        assert len(alerts) == 0

    def test_no_phenotype_excluded(self, pgx_reference_engine: sa.Engine):
        """Genes with no phenotype (None) produce no alerts."""
        results = [
            StarAlleleResult(
                gene="CYP2D6",
                allele1="*99",
                allele2="*99",
                diplotype="*99/*99",
                phenotype=None,
                call_confidence=CallConfidence.COMPLETE,
                confidence_note="All defining positions assessed.",
            ),
        ]

        alerts = generate_prescribing_alerts(results, pgx_reference_engine)
        assert len(alerts) == 0

    def test_no_guidelines_for_phenotype(self, pgx_reference_engine: sa.Engine):
        """Phenotype with no matching guidelines produces no alerts."""
        results = [
            StarAlleleResult(
                gene="CYP2C19",
                allele1="*17",
                allele2="*17",
                diplotype="*17/*17",
                phenotype="Ultrarapid Metabolizer",
                call_confidence=CallConfidence.COMPLETE,
                confidence_note="All defining positions assessed.",
            ),
        ]

        alerts = generate_prescribing_alerts(results, pgx_reference_engine)
        # No Ultrarapid Metabolizer guidelines seeded for CYP2C19 in this fixture
        assert len(alerts) == 0

    def test_multiple_genes_multiple_alerts(self, pgx_reference_engine: sa.Engine):
        """Multiple genes produce correctly grouped alerts."""
        results = [
            StarAlleleResult(
                gene="CYP2D6",
                allele1="*1",
                allele2="*1",
                diplotype="*1/*1",
                phenotype="Normal Metabolizer",
                call_confidence=CallConfidence.PARTIAL,
                confidence_note="SV gene.",
                involved_rsids=set(),
            ),
            StarAlleleResult(
                gene="CYP2C19",
                allele1="*2",
                allele2="*2",
                diplotype="*2/*2",
                phenotype="Poor Metabolizer",
                call_confidence=CallConfidence.COMPLETE,
                confidence_note="All defining positions assessed.",
                involved_rsids={"rs4244285"},
            ),
        ]

        alerts = generate_prescribing_alerts(results, pgx_reference_engine)
        # CYP2D6 NM: codeine + tramadol = 2 alerts
        # CYP2C19 PM: clopidogrel = 1 alert
        assert len(alerts) == 3
        genes = {a.gene for a in alerts}
        assert genes == {"CYP2D6", "CYP2C19"}

    def test_alerts_sorted_by_gene_drug(self, pgx_reference_engine: sa.Engine):
        """Alerts are sorted by (gene, drug)."""
        results = [
            StarAlleleResult(
                gene="TPMT",
                allele1="*1",
                allele2="*1",
                diplotype="*1/*1",
                phenotype="Normal Metabolizer",
                call_confidence=CallConfidence.COMPLETE,
                confidence_note="All defining positions assessed.",
            ),
            StarAlleleResult(
                gene="CYP2D6",
                allele1="*1",
                allele2="*1",
                diplotype="*1/*1",
                phenotype="Normal Metabolizer",
                call_confidence=CallConfidence.PARTIAL,
                confidence_note="SV gene.",
            ),
        ]

        alerts = generate_prescribing_alerts(results, pgx_reference_engine)
        gene_drug_pairs = [(a.gene, a.drug) for a in alerts]
        assert gene_drug_pairs == sorted(gene_drug_pairs)

    def test_involved_rsids_propagated(self, pgx_reference_engine: sa.Engine):
        """involved_rsids from star-allele result are carried into alerts."""
        results = [
            StarAlleleResult(
                gene="CYP2D6",
                allele1="*4",
                allele2="*4",
                diplotype="*4/*4",
                phenotype="Poor Metabolizer",
                call_confidence=CallConfidence.PARTIAL,
                confidence_note="SV gene.",
                involved_rsids={"rs3892097"},
            ),
        ]

        alerts = generate_prescribing_alerts(results, pgx_reference_engine)
        for alert in alerts:
            assert alert.involved_rsids == ["rs3892097"]

    def test_empty_results(self, pgx_reference_engine: sa.Engine):
        """Empty star-allele results produce empty alerts."""
        alerts = generate_prescribing_alerts([], pgx_reference_engine)
        assert alerts == []


# ═══════════════════════════════════════════════════════════════════════
# _build_finding_text (P3-04)
# ═══════════════════════════════════════════════════════════════════════


class TestBuildFindingText:
    def test_complete_confidence(self):
        alert = PrescribingAlert(
            gene="CYP2D6",
            drug="codeine",
            diplotype="*4/*4",
            phenotype="Poor Metabolizer",
            recommendation="Avoid codeine use.",
            classification="A",
            guideline_url=None,
            call_confidence=CallConfidence.COMPLETE,
            confidence_note="All defining positions assessed.",
            evidence_level=4,
        )
        text = _build_finding_text(alert)
        assert "CYP2D6 *4/*4" in text
        assert "Poor Metabolizer" in text
        assert "codeine" in text
        assert "Avoid codeine" in text
        assert "provisional" not in text

    def test_partial_confidence_adds_provisional_note(self):
        alert = PrescribingAlert(
            gene="CYP2D6",
            drug="codeine",
            diplotype="*4/*4",
            phenotype="Poor Metabolizer",
            recommendation="Avoid codeine use.",
            classification="A",
            guideline_url=None,
            call_confidence=CallConfidence.PARTIAL,
            confidence_note="SV gene.",
            evidence_level=4,
        )
        text = _build_finding_text(alert)
        assert "provisional" in text


# ═══════════════════════════════════════════════════════════════════════
# store_prescribing_alerts (P3-04)
# ═══════════════════════════════════════════════════════════════════════


class TestStorePrescribingAlerts:
    def test_stores_alerts_as_findings(self, pgx_reference_engine: sa.Engine):
        """Alerts are persisted as findings records with module=pharmacogenomics."""
        sample = _make_sample_engine([])

        alerts = [
            PrescribingAlert(
                gene="CYP2D6",
                drug="codeine",
                diplotype="*4/*4",
                phenotype="Poor Metabolizer",
                recommendation="Avoid codeine use. Alternative analgesics recommended.",
                classification="A",
                guideline_url="https://cpicpgx.org/guidelines/guideline-for-codeine-and-cyp2d6/",
                call_confidence=CallConfidence.PARTIAL,
                confidence_note="Structural variant gene.",
                evidence_level=4,
                activity_score=0.0,
                ehr_notation="CYP2D6 Poor Metabolizer",
                involved_rsids=["rs3892097"],
            ),
            PrescribingAlert(
                gene="CYP2C19",
                drug="clopidogrel",
                diplotype="*1/*2",
                phenotype="Intermediate Metabolizer",
                recommendation="Consider alternative antiplatelet therapy.",
                classification="A",
                guideline_url="https://cpicpgx.org/guidelines/guideline-for-clopidogrel-and-cyp2c19/",
                call_confidence=CallConfidence.COMPLETE,
                confidence_note="All defining positions assessed.",
                evidence_level=4,
                involved_rsids=["rs4244285"],
            ),
        ]

        count = store_prescribing_alerts(alerts, sample)
        assert count == 2

        # Verify rows in findings table
        with sample.connect() as conn:
            rows = conn.execute(sa.select(findings).order_by(findings.c.gene_symbol)).fetchall()

        assert len(rows) == 2

        # Check CYP2C19 row
        cyp2c19_row = rows[0]
        assert cyp2c19_row.module == "pharmacogenomics"
        assert cyp2c19_row.category == "prescribing_alert"
        assert cyp2c19_row.gene_symbol == "CYP2C19"
        assert cyp2c19_row.drug == "clopidogrel"
        assert cyp2c19_row.diplotype == "*1/*2"
        assert cyp2c19_row.metabolizer_status == "Intermediate Metabolizer"
        assert cyp2c19_row.evidence_level == 4
        assert "clopidogrel" in cyp2c19_row.finding_text

        # Check detail_json
        detail = json.loads(cyp2c19_row.detail_json)
        assert detail["classification"] == "A"
        assert detail["call_confidence"] == "Complete"
        assert detail["involved_rsids"] == ["rs4244285"]

        # Check CYP2D6 row
        cyp2d6_row = rows[1]
        assert cyp2d6_row.gene_symbol == "CYP2D6"
        assert cyp2d6_row.drug == "codeine"
        assert "provisional" in cyp2d6_row.finding_text  # Partial confidence

        cyp2d6_detail = json.loads(cyp2d6_row.detail_json)
        assert cyp2d6_detail["call_confidence"] == "Partial"
        assert cyp2d6_detail["activity_score"] == 0.0
        assert cyp2d6_detail["guideline_url"] == (
            "https://cpicpgx.org/guidelines/guideline-for-codeine-and-cyp2d6/"
        )

    def test_empty_alerts_returns_zero(self):
        """Storing an empty alerts list returns 0 (and clears any stale alerts)."""
        sample = _make_sample_engine([])
        count = store_prescribing_alerts([], sample)
        assert count == 0

    @staticmethod
    def _alert(gene="CYP2C19", drug="clopidogrel") -> PrescribingAlert:
        return PrescribingAlert(
            gene=gene,
            drug=drug,
            diplotype="*1/*2",
            phenotype="Intermediate Metabolizer",
            recommendation="Consider alternative therapy.",
            classification="A",
            guideline_url="https://cpicpgx.org/",
            call_confidence=CallConfidence.COMPLETE,
            confidence_note="All defining positions assessed.",
            evidence_level=4,
            involved_rsids=["rs4244285"],
        )

    def test_rerun_does_not_duplicate_alerts(self):
        """#481: re-storing the same alerts must not duplicate them (clear-on-rerun)."""
        sample = _make_sample_engine([])
        assert store_prescribing_alerts([self._alert()], sample) == 1
        assert store_prescribing_alerts([self._alert()], sample) == 1  # rerun
        with sample.connect() as conn:
            n = conn.execute(
                sa.select(sa.func.count())
                .select_from(findings)
                .where(findings.c.category == "prescribing_alert")
            ).scalar()
        assert n == 1, "rerun duplicated the prescribing alert"

    def test_empty_rerun_clears_stale_alerts(self):
        """#481: a rerun yielding no alerts must clear prior ones, not leave them."""
        sample = _make_sample_engine([])
        store_prescribing_alerts([self._alert("CYP2D6", "codeine")], sample)
        assert store_prescribing_alerts([], sample) == 0  # empty rerun
        with sample.connect() as conn:
            n = conn.execute(
                sa.select(sa.func.count())
                .select_from(findings)
                .where(findings.c.category == "prescribing_alert")
            ).scalar()
        assert n == 0, "empty rerun left stale prescribing alerts"

    def test_rerun_preserves_withheld_tamoxifen_audit_records(self):
        """#2019: reanalysis must not delete retained custom audit provenance."""
        sample = _make_sample_engine([])
        with sample.begin() as conn:
            conn.execute(
                findings.insert().values(
                    module="pharmacogenomics",
                    category="prescribing_alert",
                    gene_symbol="CYP2D6",
                    drug="tamoxifen",
                    finding_text="Custom retained tamoxifen audit record.",
                    detail_json=json.dumps({"local_audit_note": "retain"}),
                    provenance=json.dumps({"source": "local_clinician"}),
                )
            )

        # A normal alert is still refreshed and then cleared, while the held
        # source/custom row survives both reanalysis paths.
        assert store_prescribing_alerts([self._alert()], sample) == 1
        assert store_prescribing_alerts([], sample) == 0

        with sample.connect() as conn:
            rows = conn.execute(
                sa.select(
                    findings.c.gene_symbol,
                    findings.c.drug,
                    findings.c.finding_text,
                    findings.c.provenance,
                ).where(findings.c.category == "prescribing_alert")
            ).all()

        assert rows == [
            (
                "CYP2D6",
                "tamoxifen",
                "Custom retained tamoxifen audit record.",
                json.dumps({"source": "local_clinician"}),
            )
        ]


# ═══════════════════════════════════════════════════════════════════════
# Integration: call_all_star_alleles → generate → store (P3-04)
# ═══════════════════════════════════════════════════════════════════════


class TestPrescribingAlertIntegration:
    """Full pipeline: parse genotypes → call star alleles → generate alerts → store."""

    def test_full_pipeline_cyp2d6_poor_metabolizer(self, pgx_reference_engine: sa.Engine):
        """CYP2D6 *4/*4 → PM → codeine + tramadol alerts stored in findings."""
        sample = _make_sample_engine(
            [
                {"rsid": "rs16947", "chrom": "22", "pos": 42522613, "genotype": "GG"},
                {"rsid": "rs3892097", "chrom": "22", "pos": 42524947, "genotype": "TT"},
                {"rsid": "rs1065852", "chrom": "22", "pos": 42525772, "genotype": "GG"},
            ]
        )

        # Step 1: Call star alleles
        results = call_all_star_alleles(
            pgx_reference_engine,
            sample,
            genes=frozenset({"CYP2D6"}),
        )
        assert results[0].diplotype == "*4/*4"
        assert results[0].phenotype == "Poor Metabolizer"

        # Step 2: Generate prescribing alerts
        alerts = generate_prescribing_alerts(results, pgx_reference_engine)
        assert len(alerts) == 2

        # Step 3: Store as findings
        count = store_prescribing_alerts(alerts, sample)
        assert count == 2

        # Step 4: Verify findings in DB
        with sample.connect() as conn:
            rows = conn.execute(
                sa.select(findings)
                .where(findings.c.module == "pharmacogenomics")
                .order_by(findings.c.drug)
            ).fetchall()

        assert len(rows) == 2
        assert rows[0].drug == "codeine"
        assert rows[1].drug == "tramadol"
        assert rows[0].evidence_level == 4  # CPIC A
        assert rows[1].evidence_level == 3  # CPIC B

    def test_full_pipeline_multi_gene(self, pgx_reference_engine: sa.Engine):
        """Multi-gene pipeline: CYP2C19 IM + CYP2D6 NM → all alerts stored."""
        sample = _make_sample_engine(
            [
                # CYP2C19 *1/*2 → IM
                {"rsid": "rs4244285", "chrom": "10", "pos": 96541616, "genotype": "GA"},
                {"rsid": "rs4986893", "chrom": "10", "pos": 96540410, "genotype": "GG"},
                {"rsid": "rs12248560", "chrom": "10", "pos": 96521657, "genotype": "CC"},
                # CYP2D6 *1/*1 → NM
                {"rsid": "rs16947", "chrom": "22", "pos": 42522613, "genotype": "GG"},
                {"rsid": "rs3892097", "chrom": "22", "pos": 42524947, "genotype": "CC"},
                {"rsid": "rs1065852", "chrom": "22", "pos": 42525772, "genotype": "GG"},
            ]
        )

        results = call_all_star_alleles(
            pgx_reference_engine,
            sample,
            genes=frozenset({"CYP2C19", "CYP2D6"}),
        )

        alerts = generate_prescribing_alerts(results, pgx_reference_engine)
        count = store_prescribing_alerts(alerts, sample)

        # CYP2C19 IM → clopidogrel (1 alert)
        # CYP2D6 NM → codeine + tramadol (2 alerts)
        assert count == 3

        with sample.connect() as conn:
            rows = conn.execute(
                sa.select(findings).where(findings.c.module == "pharmacogenomics")
            ).fetchall()
        assert len(rows) == 3

    def test_insufficient_gene_excluded_from_pipeline(self, pgx_reference_engine: sa.Engine):
        """Gene with insufficient confidence does not produce findings."""
        # Empty sample → all genes will be Insufficient
        sample = _make_sample_engine([])

        results = call_all_star_alleles(
            pgx_reference_engine,
            sample,
            genes=frozenset({"CYP2C19"}),
        )
        # CYP2C19 with no data → Insufficient (3/3 defining rsids missing)
        assert results[0].call_confidence == CallConfidence.INSUFFICIENT

        alerts = generate_prescribing_alerts(results, pgx_reference_engine)
        assert len(alerts) == 0

        count = store_prescribing_alerts(alerts, sample)
        assert count == 0


# ═══════════════════════════════════════════════════════════════════════
# update_annotation_coverage_cpic (P3-04a)
# ═══════════════════════════════════════════════════════════════════════


class TestUpdateAnnotationCoverageCpic:
    """Test that CPIC bitmask bit 6 (value 64) is ORed into annotation_coverage."""

    def _make_sample_with_annotated(
        self,
        raw: list[dict],
        annotated: list[dict],
    ) -> sa.Engine:
        """Create sample engine with raw_variants and pre-populated annotated_variants."""
        engine = sa.create_engine("sqlite://")
        create_sample_tables(engine)
        if raw:
            with engine.begin() as conn:
                conn.execute(raw_variants.insert(), raw)
        if annotated:
            with engine.begin() as conn:
                conn.execute(annotated_variants.insert(), annotated)
        return engine

    def test_sets_cpic_bit_on_involved_variants(self):
        """Variants involved in star-allele calls get the CPIC coverage bit set."""

        sample = self._make_sample_with_annotated(
            raw=[
                {"rsid": "rs4244285", "chrom": "10", "pos": 96541616, "genotype": "GA"},
                {"rsid": "rs9999999", "chrom": "1", "pos": 100, "genotype": "CC"},
            ],
            annotated=[
                {
                    "rsid": "rs4244285",
                    "chrom": "10",
                    "pos": 96541616,
                    "genotype": "GA",
                    "annotation_coverage": 0b001111,
                },  # bits 0-3 set (VEP+ClinVar+gnomAD+dbNSFP)
                {
                    "rsid": "rs9999999",
                    "chrom": "1",
                    "pos": 100,
                    "genotype": "CC",
                    "annotation_coverage": 0b000011,
                },  # bits 0-1 set
            ],
        )

        results = [
            StarAlleleResult(
                gene="CYP2C19",
                allele1="*1",
                allele2="*2",
                diplotype="*1/*2",
                phenotype="Intermediate Metabolizer",
                call_confidence=CallConfidence.COMPLETE,
                confidence_note="All defining positions assessed.",
                involved_rsids={"rs4244285"},
            ),
        ]

        updated = update_annotation_coverage_cpic(results, sample)
        assert updated == 1

        with sample.connect() as conn:
            rows = {
                r.rsid: r.annotation_coverage
                for r in conn.execute(
                    sa.select(
                        annotated_variants.c.rsid,
                        annotated_variants.c.annotation_coverage,
                    )
                ).fetchall()
            }

        # rs4244285: prior bits 0-3 plus the CPIC bit
        assert rows["rs4244285"] == 0b001111 | CPIC_BIT
        # rs9999999: unchanged (not involved in CPIC)
        assert rows["rs9999999"] == 0b000011

    def test_null_annotation_coverage_gets_cpic_bit(self):
        """Variant with NULL annotation_coverage gets the CPIC bit set."""

        sample = self._make_sample_with_annotated(
            raw=[
                {"rsid": "rs4244285", "chrom": "10", "pos": 96541616, "genotype": "GA"},
            ],
            annotated=[
                {
                    "rsid": "rs4244285",
                    "chrom": "10",
                    "pos": 96541616,
                    "genotype": "GA",
                    "annotation_coverage": None,
                },
            ],
        )

        results = [
            StarAlleleResult(
                gene="CYP2C19",
                allele1="*1",
                allele2="*2",
                diplotype="*1/*2",
                involved_rsids={"rs4244285"},
                call_confidence=CallConfidence.COMPLETE,
                confidence_note="",
            ),
        ]

        updated = update_annotation_coverage_cpic(results, sample)
        assert updated == 1

        with sample.connect() as conn:
            row = conn.execute(
                sa.select(annotated_variants.c.annotation_coverage).where(
                    annotated_variants.c.rsid == "rs4244285"
                )
            ).scalar()
        assert row == CPIC_BIT  # CPIC_BIT only (its own bit; F33 moved it off GENE_PHENOTYPE_BIT)

    def test_assessed_reference_variant_gets_cpic_bit(self):
        """Reference-state defining variants used by CPIC calls get the CPIC bit."""

        sample = self._make_sample_with_annotated(
            raw=[
                {"rsid": "rs4244285", "chrom": "10", "pos": 96541616, "genotype": "GG"},
            ],
            annotated=[
                {
                    "rsid": "rs4244285",
                    "chrom": "10",
                    "pos": 96541616,
                    "genotype": "GG",
                    "annotation_coverage": 0,
                },
            ],
        )

        results = [
            StarAlleleResult(
                gene="CYP2C19",
                allele1="*1",
                allele2="*1",
                diplotype="*1/*1",
                phenotype="Normal Metabolizer",
                involved_rsids=set(),
                assessed_rsids={"rs4244285"},
                call_confidence=CallConfidence.COMPLETE,
                confidence_note="All defining positions assessed.",
            ),
        ]

        updated = update_annotation_coverage_cpic(results, sample)
        assert updated == 1

        with sample.connect() as conn:
            row = conn.execute(
                sa.select(annotated_variants.c.annotation_coverage).where(
                    annotated_variants.c.rsid == "rs4244285"
                )
            ).scalar()

        assert row == CPIC_BIT

    def test_no_involved_rsids_returns_zero(self):
        """Star-allele results with no involved rsids → 0 updates."""

        sample = self._make_sample_with_annotated(raw=[], annotated=[])

        results = [
            StarAlleleResult(
                gene="CYP2C19",
                allele1="*1",
                allele2="*1",
                diplotype="*1/*1",
                involved_rsids=set(),
                call_confidence=CallConfidence.INSUFFICIENT,
                confidence_note="All missing.",
            ),
        ]

        updated = update_annotation_coverage_cpic(results, sample)
        assert updated == 0

    def test_empty_results_returns_zero(self):
        """Empty star-allele results list → 0 updates."""

        sample = self._make_sample_with_annotated(raw=[], annotated=[])
        updated = update_annotation_coverage_cpic([], sample)
        assert updated == 0

    def test_multiple_genes_involved_rsids_combined(self):
        """Rsids from multiple genes are combined and all get the CPIC bit."""

        sample = self._make_sample_with_annotated(
            raw=[
                {"rsid": "rs4244285", "chrom": "10", "pos": 96541616, "genotype": "GA"},
                {"rsid": "rs3892097", "chrom": "22", "pos": 42524947, "genotype": "TT"},
            ],
            annotated=[
                {
                    "rsid": "rs4244285",
                    "chrom": "10",
                    "pos": 96541616,
                    "genotype": "GA",
                    "annotation_coverage": 1,
                },
                {
                    "rsid": "rs3892097",
                    "chrom": "22",
                    "pos": 42524947,
                    "genotype": "TT",
                    "annotation_coverage": 3,
                },
            ],
        )

        results = [
            StarAlleleResult(
                gene="CYP2C19",
                allele1="*1",
                allele2="*2",
                diplotype="*1/*2",
                involved_rsids={"rs4244285"},
                call_confidence=CallConfidence.COMPLETE,
                confidence_note="",
            ),
            StarAlleleResult(
                gene="CYP2D6",
                allele1="*4",
                allele2="*4",
                diplotype="*4/*4",
                involved_rsids={"rs3892097"},
                call_confidence=CallConfidence.PARTIAL,
                confidence_note="SV.",
            ),
        ]

        updated = update_annotation_coverage_cpic(results, sample)
        assert updated == 2

        with sample.connect() as conn:
            rows = {
                r.rsid: r.annotation_coverage
                for r in conn.execute(
                    sa.select(
                        annotated_variants.c.rsid,
                        annotated_variants.c.annotation_coverage,
                    )
                ).fetchall()
            }

        assert rows["rs4244285"] == 1 | CPIC_BIT
        assert rows["rs3892097"] == 3 | CPIC_BIT

    def test_idempotent_or(self):
        """ORing the CPIC bit when already set is idempotent."""

        sample = self._make_sample_with_annotated(
            raw=[
                {"rsid": "rs4244285", "chrom": "10", "pos": 96541616, "genotype": "GA"},
            ],
            annotated=[
                {
                    "rsid": "rs4244285",
                    "chrom": "10",
                    "pos": 96541616,
                    "genotype": "GA",
                    "annotation_coverage": 0b001111 | CPIC_BIT,
                },  # CPIC bit already set
            ],
        )

        results = [
            StarAlleleResult(
                gene="CYP2C19",
                allele1="*1",
                allele2="*2",
                diplotype="*1/*2",
                involved_rsids={"rs4244285"},
                call_confidence=CallConfidence.COMPLETE,
                confidence_note="",
            ),
        ]

        update_annotation_coverage_cpic(results, sample)

        with sample.connect() as conn:
            val = conn.execute(
                sa.select(annotated_variants.c.annotation_coverage).where(
                    annotated_variants.c.rsid == "rs4244285"
                )
            ).scalar()
        assert val == 0b001111 | CPIC_BIT  # unchanged (idempotent OR)

    def test_variant_not_in_annotated_table_skipped(self):
        """Rsids in involved_rsids but not in annotated_variants → not counted."""

        sample = self._make_sample_with_annotated(
            raw=[
                {"rsid": "rs4244285", "chrom": "10", "pos": 96541616, "genotype": "GA"},
            ],
            annotated=[],  # no annotated_variants rows
        )

        results = [
            StarAlleleResult(
                gene="CYP2C19",
                allele1="*1",
                allele2="*2",
                diplotype="*1/*2",
                involved_rsids={"rs4244285"},
                call_confidence=CallConfidence.COMPLETE,
                confidence_note="",
            ),
        ]

        updated = update_annotation_coverage_cpic(results, sample)
        assert updated == 0


class TestRunPharmaAnnotationCoverage:
    """Production pharmacogenomics runner updates CPIC annotation coverage."""

    def test_run_pharma_sets_cpic_bit_for_involved_variant(self, pgx_reference_engine: sa.Engine):
        sample = _make_sample_engine(
            [
                {"rsid": "rs4244285", "chrom": "10", "pos": 96541616, "genotype": "GA"},
            ]
        )
        with sample.begin() as conn:
            conn.execute(
                annotated_variants.insert(),
                [
                    {
                        "rsid": "rs4244285",
                        "chrom": "10",
                        "pos": 96541616,
                        "genotype": "GA",
                        "annotation_coverage": 0,
                    },
                ],
            )

        stored = _run_pharma(
            sample,
            SimpleNamespace(reference_engine=pgx_reference_engine),
        )

        assert stored >= 0
        with sample.connect() as conn:
            coverage = conn.execute(
                sa.select(annotated_variants.c.annotation_coverage).where(
                    annotated_variants.c.rsid == "rs4244285"
                )
            ).scalar()

        assert coverage & CPIC_BIT == CPIC_BIT

    def test_run_pharma_sets_cpic_bit_for_reference_variant(self, pgx_reference_engine: sa.Engine):
        sample = _make_sample_engine(
            [
                {"rsid": "rs4244285", "chrom": "10", "pos": 96541616, "genotype": "GG"},
                {"rsid": "rs4986893", "chrom": "10", "pos": 96540410, "genotype": "GG"},
                {"rsid": "rs12248560", "chrom": "10", "pos": 96521657, "genotype": "CC"},
            ]
        )
        with sample.begin() as conn:
            conn.execute(
                annotated_variants.insert(),
                [
                    {
                        "rsid": "rs4244285",
                        "chrom": "10",
                        "pos": 96541616,
                        "genotype": "GG",
                        "annotation_coverage": 0,
                    },
                ],
            )

        stored = _run_pharma(
            sample,
            SimpleNamespace(reference_engine=pgx_reference_engine),
        )

        assert stored >= 0
        with sample.connect() as conn:
            coverage = conn.execute(
                sa.select(annotated_variants.c.annotation_coverage).where(
                    annotated_variants.c.rsid == "rs4244285"
                )
            ).scalar()

        assert coverage & CPIC_BIT == CPIC_BIT


class TestPatientPresentableFindingPayload:
    """#2019: legacy structured payloads cannot bypass the output hold."""

    @pytest.mark.parametrize(
        "payload",
        [
            {"legacy_pair": ["CYP2D6", "tamoxifen", "Escalate dose"]},
            {
                "legacy_gene": "CYP2D6",
                "legacy_drug": "tamoxifen",
                "recommendation": "Escalate dose",
            },
            {"recommendation": "CYP2D6 tamoxifen: escalate dose"},
            {"gene": "CYP2D6", "legacy": {"drug": "tamoxifen"}},
            {
                "legacy": [
                    {"gene": "CYP2D6"},
                    {"drug": "tamoxifen", "recommendation": "Escalate dose"},
                ]
            },
            {
                "advice": {"gene": "CYP2D6"},
                "medication": {"drug": "tamoxifen", "recommendation": "Escalate dose"},
            },
            {"first": ["CYP2D6"], "second": ["tamoxifen dose guidance"]},
            {"CYP2D6": "legacy carrier", "tamoxifen": "dose guidance"},
            {
                "first": {"legacy_gene": "CYP2D6"},
                "second": {"legacy_drug": "tamoxifen dose guidance"},
            },
            {
                "legacy": [
                    {"gene": "CYP2D6", "drug": "codeine"},
                    {"drug": "tamoxifen", "recommendation": "Escalate dose"},
                ]
            },
            {
                "legacy": {
                    "gene_hint": "CYP2D6",
                    "record": {
                        "gene": "CYP2C19",
                        "drug": "tamoxifen",
                        "recommendation": "Escalate dose",
                    },
                }
            },
            {
                "effects": [
                    {
                        "gene": "CYP2C19",
                        "drug": "clopidogrel",
                        "legacy": {"gene_hint": "CYP2D6"},
                    },
                    {
                        "gene": "CYP2C19",
                        "drug": "tamoxifen",
                        "recommendation": "Escalate dose",
                    },
                ]
            },
        ],
    )
    def test_rejects_held_pair_in_legacy_payload_shapes(self, payload: dict) -> None:
        assert contains_unpresentable_prescribing_identifier(payload)

    def test_keeps_independent_safe_pair_records_presentable(self) -> None:
        payload = {
            "effects": [
                {"gene": "CYP2D6", "drug": "codeine"},
                {"gene": "CYP2C19", "drug": "tamoxifen"},
            ]
        }

        assert not contains_unpresentable_prescribing_identifier(payload)
        assert is_patient_presentable_finding_payload(
            {
                "module": "pharmacogenomics",
                "category": "prescribing_alert",
                "gene_symbol": "CYP2C19",
                "drug": "clopidogrel",
                "finding_text": "CYP2C19/clopidogrel result",
                "detail_json": payload,
            }
        )

    def test_keeps_nested_independent_safe_pair_records_presentable(self) -> None:
        payload = {
            "gene": "CYP2D6",
            "drug": "codeine",
            "related": {"gene": "CYP2C19", "drug": "tamoxifen"},
        }

        assert not contains_unpresentable_prescribing_identifier(payload)

    def test_keeps_nested_record_narrative_with_its_own_safe_pair(self) -> None:
        payload = {
            "gene": "CYP2D6",
            "drug": "codeine",
            "finding_text": "CYP2D6/codeine normal guidance",
            "related": {
                "gene": "CYP2C19",
                "drug": "tamoxifen",
                "recommendation": "Review normally.",
            },
        }

        assert not contains_unpresentable_prescribing_identifier(payload)

    def test_keeps_row_narrative_with_its_own_safe_complete_record(self) -> None:
        finding = {
            "module": "pharmacogenomics",
            "category": "prescribing_alert",
            "gene_symbol": "CYP2D6",
            "drug": "codeine",
            "finding_text": "CYP2D6/codeine result",
            "detail_json": {
                "records": [
                    {
                        "gene": "CYP2D6",
                        "drug": "codeine",
                        "recommendation": "Dose normally.",
                    },
                    {
                        "gene": "CYP2C19",
                        "drug": "tamoxifen",
                        "recommendation": "Review normally.",
                    },
                ]
            },
        }

        assert is_patient_presentable_finding_payload(finding)

    def test_keeps_compound_nonheld_prescribing_identifiers_presentable(self) -> None:
        finding = {
            "module": "pharmacogenomics",
            "category": "prescribing_alert",
            "gene_symbol": "TPMT/NUDT15",
            "drug": "azathioprine",
            "finding_text": "TPMT/NUDT15 azathioprine guidance",
            "detail_json": {"recommendation": "Start reduced dose."},
        }

        assert not is_prescribing_alert_withheld("TPMT/NUDT15", "azathioprine")
        assert is_patient_presentable_finding_payload(finding)

    def test_keeps_lone_generic_identifier_fields_as_free_evidence(self) -> None:
        assert not contains_unpresentable_prescribing_identifier(
            {"gene": "CYP2D6", "status": "routine"}
        )
        assert not contains_unpresentable_prescribing_identifier(
            {"drug": "tamoxifen", "status": "reference-only"}
        )

    def test_rejects_intercharacter_whitespace_identifier_bypass(self) -> None:
        gene = "CYP 2D6"
        drug = "tamox ifen"

        assert is_prescribing_alert_withheld(gene, drug)
        assert contains_unpresentable_prescribing_identifier(
            {"recommendation": f"{gene} {drug} dose guidance"}
        )

    def test_rejects_held_pair_in_finding_text(self) -> None:
        assert not is_patient_presentable_finding_payload(
            {
                "module": "pharmacogenomics",
                "category": "prescribing_alert",
                "gene_symbol": "CYP2C19",
                "drug": "clopidogrel",
                "finding_text": "CYP2D6/tamoxifen: escalate dose",
                "detail_json": {},
            }
        )

    @pytest.mark.parametrize(
        "field",
        ("phenotype", "conditions", "metabolizer_status", "diplotype"),
    )
    def test_rejects_held_pair_in_any_serialized_scalar_field(self, field: str) -> None:
        finding: dict[str, object] = {
            "module": "pharmacogenomics",
            "category": "prescribing_alert",
            "gene_symbol": "CYP2C19",
            "drug": "clopidogrel",
            "finding_text": "CYP2C19/clopidogrel result",
            "detail_json": {},
            field: "CYP2D6 tamoxifen dose guidance",
        }

        assert not is_patient_presentable_finding_payload(finding)

    def test_rejects_pair_split_across_serialized_scalar_fields(self) -> None:
        assert not is_patient_presentable_finding_payload(
            {
                "module": "pharmacogenomics",
                "category": "prescribing_alert",
                "gene_symbol": "CYP2C19",
                "drug": "clopidogrel",
                "finding_text": "CYP2C19/clopidogrel result",
                "phenotype": "CYP2D6",
                "conditions": "tamoxifen dose guidance",
                "detail_json": {},
            }
        )

    def test_rejects_default_ignorable_identifier_bypass(self) -> None:
        gene = "CYP\u200b2D6"
        drug = "tamox\u200bifen"

        assert is_prescribing_alert_withheld(gene, drug)
        assert contains_unpresentable_prescribing_identifier(
            {"recommendation": f"{gene} {drug} dose guidance"}
        )
        assert not is_patient_presentable_finding_payload(
            {
                "module": "pharmacogenomics",
                "category": "prescribing_alert",
                "gene_symbol": gene,
                "drug": drug,
                "finding_text": "Legacy prescribing alert",
                "detail_json": {},
            }
        )

    @pytest.mark.parametrize(
        ("pmid_citations", "expected"),
        [
            (json.dumps(["PMID:29385237"]), True),
            ("not JSON", False),
            (json.dumps({"pmid": "PMID:29385237"}), False),
            (json.dumps(["PMID:29385237", 1]), False),
            (json.dumps(["CYP2D6", "tamoxifen dose guidance"]), False),
        ],
        ids=["clean-list", "malformed", "object", "mixed-items", "held-pair-split"],
    )
    def test_pmid_citations_are_strict_patient_payloads(
        self,
        pmid_citations: str,
        expected: bool,
    ) -> None:
        assert (
            is_patient_presentable_finding_payload(
                {
                    "module": "pharmacogenomics",
                    "category": "prescribing_alert",
                    "gene_symbol": "CYP2C19",
                    "drug": "clopidogrel",
                    "finding_text": "CYP2C19/clopidogrel result",
                    "detail_json": {},
                    "pmid_citations": pmid_citations,
                }
            )
            is expected
        )

    def test_rejects_held_pair_in_provenance(self) -> None:
        assert not is_patient_presentable_finding_payload(
            {
                "module": "pharmacogenomics",
                "category": "prescribing_alert",
                "gene_symbol": "CYP2C19",
                "drug": "clopidogrel",
                "finding_text": "CYP2C19/clopidogrel result",
                "detail_json": {},
                "provenance": {
                    "legacy": {
                        "gene": "CYP2D6",
                        "drug": "tamoxifen",
                        "recommendation": "Must not be serialized as provenance.",
                    }
                },
            }
        )

    @pytest.mark.parametrize(
        "payload",
        [
            {
                "gene": "CYP2C19",
                "drug": "clopidogrel",
                "legacy_gene_hint": "CYP2D6",
                "nested": {"gene": "CYP2C19", "drug": "tamoxifen"},
            },
            {
                "gene": "CYP2C19",
                "drug": "clopidogrel",
                "legacy_drug_hint": "tamoxifen",
                "nested": {"gene": "CYP2D6", "drug": "codeine"},
            },
        ],
    )
    def test_rejects_free_fragment_crossing_a_nested_complete_record(
        self,
        payload: dict[str, object],
    ) -> None:
        assert contains_unpresentable_prescribing_identifier(payload)

    def test_keeps_safe_row_schema_gene_and_drug_as_one_complete_record(self) -> None:
        assert is_patient_presentable_finding_payload(
            {
                "module": "pharmacogenomics",
                "category": "prescribing_alert",
                "gene_symbol": "CYP2C19",
                "drug": "tamoxifen",
                "finding_text": "CYP2C19/tamoxifen result",
                "detail_json": {"recommendation": "Review normally."},
            }
        )

    @pytest.mark.parametrize(
        "finding",
        [
            {
                "module": "pharmacogenomics",
                "category": "prescribing_alert",
                "gene_symbol": "CYP2D6",
                "drug": "codeine",
                "finding_text": "CYP2D6/codeine result",
                "detail_json": {"recommendation": "tamoxifen guidance"},
            },
            {
                "module": "pharmacogenomics",
                "category": "prescribing_alert",
                "gene_symbol": "CYP2C19",
                "drug": "clopidogrel",
                "finding_text": "CYP2C19/clopidogrel result",
                "detail_json": {"recommendation": "tamoxifen guidance"},
                "provenance": {"legacy_gene": "CYP2D6"},
            },
            {
                "module": "pharmacogenomics",
                "category": "prescribing_alert",
                "gene_symbol": "CYP2C19",
                "drug": "clopidogrel",
                "finding_text": "CYP2C19/clopidogrel result",
                "detail_json": {"recommendation": "tamoxifen guidance"},
                "pmid_citations": ["CYP2D6"],
            },
        ],
    )
    def test_rejects_pair_split_across_patient_response_fields(
        self,
        finding: dict[str, object],
    ) -> None:
        assert not is_patient_presentable_finding_payload(finding)

    def test_rejects_control_combining_and_lookalike_identifier_bypasses(self) -> None:
        assert is_prescribing_alert_withheld("CYP2D\u03326", "tamox\x00ifen")
        assert contains_unpresentable_prescribing_identifier(
            {"recommendation": "CYP2D6 tamox\x00ifen dose guidance"}
        )
        assert contains_unpresentable_prescribing_identifier(
            {"recommendation": "CYP2D\u03326 tamoxifen dose guidance"}
        )
        assert not is_patient_presentable_finding_payload(
            {
                "module": "pharmacogenomics",
                "category": "prescribing_alert",
                "gene_symbol": "CYP-2D6",
                "drug": "tamoxifen",
                "finding_text": "Malformed direct identifier",
                "detail_json": {},
            }
        )
        assert not is_patient_presentable_finding_payload(
            {
                "module": "pharmacogenomics",
                "category": "prescribing_alert",
                "gene_symbol": "CYP2D6",
                "drug": "tamоxifen",
                "finding_text": "Non-ASCII lookalike identifier",
                "detail_json": {},
            }
        )

    @pytest.mark.parametrize("gene", ("CYP٢D6", "CYP২D6"))
    def test_rejects_non_ascii_digit_lookalikes_in_free_text(self, gene: str) -> None:
        assert not is_patient_presentable_finding_payload(
            {
                "module": "pharmacogenomics",
                "category": "prescribing_alert",
                "gene_symbol": "CYP2C19",
                "drug": "clopidogrel",
                "finding_text": "Routine result",
                "detail_json": {"note": f"{gene} tamoxifen dose escalation"},
            }
        )

    @pytest.mark.parametrize(
        "text",
        (
            "CYP2D6tamoxifen dose guidance",
            "CYP2D6tamoxifendoseguidance",
            "legacyCYP2D6tamoxifendoseguidance",
            "CYP2D6\u200btamoxifen dose guidance",
            "CYP2D6\u00adtamoxifen dose guidance",
        ),
    )
    def test_rejects_fused_or_ignorable_separated_held_pair_text(self, text: str) -> None:
        assert contains_unpresentable_prescribing_identifier({"note": text})

    def test_rejects_pair_assembled_in_a_complete_response(self) -> None:
        assert not is_patient_presentable_response_payload(
            {
                "population_distances": {"CYP2D6": 0.1},
                "admixture_fractions": {"tamoxifen dose escalation": 1.0},
            }
        )

    def test_keeps_nested_detail_text_with_its_own_safe_response_record(self) -> None:
        response = [
            {
                "gene_symbol": "CYP2D6",
                "drug": "codeine",
                "detail": {"note": "CYP2D6 codeine normal guidance"},
            },
            {
                "gene_symbol": "CYP2C19",
                "drug": "tamoxifen",
                "detail": {"note": "CYP2C19 tamoxifen normal guidance"},
            },
        ]

        assert is_patient_presentable_response_payload(response)

    def test_rejects_default_ignorable_blank_prescribing_alert_identifiers(self) -> None:
        assert not is_patient_presentable_finding_payload(
            {
                "module": "pharmacogenomics",
                "category": "prescribing_alert\u200b",
                "gene_symbol": "\u200b",
                "drug": "codeine",
                "finding_text": "Unclassifiable legacy prescribing alert",
                "detail_json": {},
            }
        )

    @pytest.mark.parametrize("constant", ("NaN", "Infinity", "-Infinity"))
    def test_rejects_non_json_constants_in_serialized_payloads(self, constant: str) -> None:
        assert not is_patient_presentable_finding_payload(
            {
                "module": "pharmacogenomics",
                "category": "prescribing_alert",
                "gene_symbol": "CYP2C19",
                "drug": "clopidogrel",
                "finding_text": "CYP2C19/clopidogrel result",
                "detail_json": f'{{"score": {constant}}}',
            }
        )

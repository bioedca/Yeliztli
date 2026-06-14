"""Tests for APOE genotype determination (P3-22a) and findings generation (P3-22b).

Covers:
  P3-22a:
  - All 6 standard APOE diplotypes (ε2/ε2 through ε4/ε4)
  - rs429358 TT + rs7412 CC → ε3/ε3 (T3-17 golden fixture)
  - Missing SNPs (one or both)
  - No-call genotypes ("--")
  - Genotype normalisation (TC vs CT)
  - APOEResult properties (has_e4, e4_count, has_e2, e2_count)
  - Finding storage (module='apoe', category='genotype')
  - Idempotent re-runs (clear previous findings)
  - Finding skipped when genotype not determined

  P3-22b:
  - Three findings generation (CV risk, Alzheimer's, lipid/dietary)
  - T3-18: ε4/ε4 Alzheimer's risk finding with caveats and non-actionable framing
  - Evidence levels (★★★★ for CV and Alzheimer's, ★★★☆ for lipid/dietary)
  - All 6 diplotypes produce valid findings
  - Finding text content validation per diplotype
  - Three findings storage (idempotent, independent of genotype finding)
  - PubMed citations present
  - Detail JSON structure
"""

from __future__ import annotations

import json

import pytest
import sqlalchemy as sa

import backend.analysis.apoe as apoe_module
from backend.analysis.apoe import (
    APOE_ARRAY_RELIABILITY_CAVEAT,
    APOE_FINDING_ALZHEIMERS,
    APOE_FINDING_CATEGORIES,
    APOE_FINDING_CV,
    APOE_FINDING_LIPID,
    APOE_RELIABILITY_PMIDS,
    APOE_RS7412,
    APOE_RS429358,
    APOEAllele,
    APOEResult,
    APOEStatus,
    _normalise_genotype,
    determine_apoe_genotype,
    generate_apoe_findings,
    store_apoe_finding,
    store_apoe_three_findings,
)
from backend.db.sample_schema import create_sample_tables
from backend.db.tables import findings, raw_variants

# ── Fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture()
def sample_engine() -> sa.Engine:
    """In-memory SQLite engine with all sample tables."""
    engine = sa.create_engine("sqlite://")
    create_sample_tables(engine)
    return engine


def _seed_apoe_variants(
    engine: sa.Engine,
    rs429358_genotype: str | None = None,
    rs7412_genotype: str | None = None,
) -> None:
    """Insert APOE SNPs into raw_variants."""
    rows = []
    if rs429358_genotype is not None:
        rows.append(
            {"rsid": APOE_RS429358, "chrom": "19", "pos": 44908684, "genotype": rs429358_genotype}
        )
    if rs7412_genotype is not None:
        rows.append(
            {"rsid": APOE_RS7412, "chrom": "19", "pos": 44908822, "genotype": rs7412_genotype}
        )
    if rows:
        with engine.begin() as conn:
            conn.execute(sa.insert(raw_variants), rows)


# ── Genotype normalisation ────────────────────────────────────────────────


class TestNormaliseGenotype:
    """Test genotype string normalisation."""

    def test_already_sorted(self) -> None:
        assert _normalise_genotype("CC") == "CC"

    def test_needs_sorting(self) -> None:
        assert _normalise_genotype("TC") == "CT"

    def test_same_alleles(self) -> None:
        assert _normalise_genotype("TT") == "TT"

    def test_single_char_passthrough(self) -> None:
        assert _normalise_genotype("A") == "A"

    def test_three_char_passthrough(self) -> None:
        assert _normalise_genotype("ACG") == "ACG"


# ── All 6 standard diplotypes ────────────────────────────────────────────


class TestAPOEDiplotypes:
    """Test all 6 standard APOE diplotype determinations."""

    @pytest.mark.parametrize(
        (
            "rs429358_gt",
            "rs7412_gt",
            "expected_diplotype",
            "expected_e4_count",
            "expected_e2_count",
        ),
        [
            # ε2/ε2: rs429358=TT, rs7412=TT
            ("TT", "TT", "ε2/ε2", 0, 2),
            # ε2/ε3: rs429358=TT, rs7412=CT
            ("TT", "CT", "ε2/ε3", 0, 1),
            # ε2/ε4: rs429358=CT, rs7412=CT
            ("CT", "CT", "ε2/ε4", 1, 1),
            # ε3/ε3: rs429358=TT, rs7412=CC (T3-17 golden fixture)
            ("TT", "CC", "ε3/ε3", 0, 0),
            # ε3/ε4: rs429358=CT, rs7412=CC
            ("CT", "CC", "ε3/ε4", 1, 0),
            # ε4/ε4: rs429358=CC, rs7412=CC
            ("CC", "CC", "ε4/ε4", 2, 0),
        ],
        ids=["e2/e2", "e2/e3", "e2/e4", "e3/e3-T3-17", "e3/e4", "e4/e4"],
    )
    def test_diplotype(
        self,
        sample_engine: sa.Engine,
        rs429358_gt: str,
        rs7412_gt: str,
        expected_diplotype: str,
        expected_e4_count: int,
        expected_e2_count: int,
    ) -> None:
        _seed_apoe_variants(sample_engine, rs429358_gt, rs7412_gt)
        result = determine_apoe_genotype(sample_engine)

        assert result.is_determined
        assert result.status == APOEStatus.DETERMINED
        assert result.diplotype == expected_diplotype
        assert result.e4_count == expected_e4_count
        assert result.e2_count == expected_e2_count
        assert result.has_e4 == (expected_e4_count > 0)
        assert result.has_e2 == (expected_e2_count > 0)

    def test_e3_e3_golden_fixture(self, sample_engine: sa.Engine) -> None:
        """T3-17: APOE genotype correctly determined: rs429358 TT + rs7412 CC → ε3/ε3."""
        _seed_apoe_variants(sample_engine, "TT", "CC")
        result = determine_apoe_genotype(sample_engine)

        assert result.is_determined
        assert result.diplotype == "ε3/ε3"
        assert result.allele1 == APOEAllele.E3
        assert result.allele2 == APOEAllele.E3
        assert result.rs429358_genotype == "TT"
        assert result.rs7412_genotype == "CC"
        assert not result.has_e4
        assert result.e4_count == 0
        assert not result.has_e2
        assert result.e2_count == 0

    def test_e4_e4_homozygous(self, sample_engine: sa.Engine) -> None:
        """ε4/ε4 correctly identified with both alleles."""
        _seed_apoe_variants(sample_engine, "CC", "CC")
        result = determine_apoe_genotype(sample_engine)

        assert result.is_determined
        assert result.diplotype == "ε4/ε4"
        assert result.allele1 == APOEAllele.E4
        assert result.allele2 == APOEAllele.E4
        assert result.has_e4
        assert result.e4_count == 2

    def test_e2_e4_mixed(self, sample_engine: sa.Engine) -> None:
        """ε2/ε4 correctly identified with one of each."""
        _seed_apoe_variants(sample_engine, "CT", "CT")
        result = determine_apoe_genotype(sample_engine)

        assert result.is_determined
        assert result.diplotype == "ε2/ε4"
        assert result.has_e4
        assert result.e4_count == 1
        assert result.has_e2
        assert result.e2_count == 1


# ── Biologically impossible combinations ──────────────────────────────────


class TestAPOEAmbiguousCases:
    """Test biologically impossible genotype combinations return AMBIGUOUS."""

    @pytest.mark.parametrize(
        ("rs429358_gt", "rs7412_gt"),
        [
            ("CT", "TT"),  # Would require ε1 allele
            ("CC", "CT"),  # Would require ε1 allele
            ("CC", "TT"),  # Would require ε1 allele
        ],
        ids=["CT/TT", "CC/CT", "CC/TT"],
    )
    def test_impossible_combinations_return_ambiguous(
        self,
        sample_engine: sa.Engine,
        rs429358_gt: str,
        rs7412_gt: str,
    ) -> None:
        """Biologically impossible combinations should be AMBIGUOUS."""
        _seed_apoe_variants(sample_engine, rs429358_gt, rs7412_gt)
        result = determine_apoe_genotype(sample_engine)

        assert not result.is_determined
        assert result.status == APOEStatus.AMBIGUOUS
        assert result.diplotype is None


# ── Genotype ordering ────────────────────────────────────────────────────


class TestGenotypeOrdering:
    """Test that genotype order doesn't affect result."""

    def test_reversed_rs429358(self, sample_engine: sa.Engine) -> None:
        """TC at rs429358 treated same as CT."""
        _seed_apoe_variants(sample_engine, "TC", "CC")
        result = determine_apoe_genotype(sample_engine)

        assert result.is_determined
        assert result.diplotype == "ε3/ε4"

    def test_reversed_rs7412(self, sample_engine: sa.Engine) -> None:
        """TC at rs7412 treated same as CT."""
        _seed_apoe_variants(sample_engine, "TT", "TC")
        result = determine_apoe_genotype(sample_engine)

        assert result.is_determined
        assert result.diplotype == "ε2/ε3"

    def test_both_reversed(self, sample_engine: sa.Engine) -> None:
        """Both genotypes reversed."""
        _seed_apoe_variants(sample_engine, "TC", "TC")
        result = determine_apoe_genotype(sample_engine)

        assert result.is_determined
        assert result.diplotype == "ε2/ε4"


# ── Edge cases ────────────────────────────────────────────────────────────


class TestAPOEEdgeCases:
    """Test APOE determination edge cases."""

    def test_missing_both_snps(self, sample_engine: sa.Engine) -> None:
        """No APOE SNPs in raw_variants."""
        result = determine_apoe_genotype(sample_engine)

        assert not result.is_determined
        assert result.status == APOEStatus.MISSING_SNPS
        assert result.diplotype is None
        assert result.allele1 is None
        assert result.allele2 is None
        assert result.rs429358_genotype is None
        assert result.rs7412_genotype is None

    def test_missing_rs429358(self, sample_engine: sa.Engine) -> None:
        """Only rs7412 present."""
        _seed_apoe_variants(sample_engine, rs7412_genotype="CC")
        result = determine_apoe_genotype(sample_engine)

        assert not result.is_determined
        assert result.status == APOEStatus.MISSING_SNPS
        assert result.rs429358_genotype is None
        assert result.rs7412_genotype == "CC"

    def test_missing_rs7412(self, sample_engine: sa.Engine) -> None:
        """Only rs429358 present."""
        _seed_apoe_variants(sample_engine, rs429358_genotype="TT")
        result = determine_apoe_genotype(sample_engine)

        assert not result.is_determined
        assert result.status == APOEStatus.MISSING_SNPS
        assert result.rs429358_genotype == "TT"
        assert result.rs7412_genotype is None

    def test_no_call_rs429358(self, sample_engine: sa.Engine) -> None:
        """No-call at rs429358."""
        _seed_apoe_variants(sample_engine, "--", "CC")
        result = determine_apoe_genotype(sample_engine)

        assert not result.is_determined
        assert result.status == APOEStatus.NO_CALL

    def test_no_call_rs7412(self, sample_engine: sa.Engine) -> None:
        """No-call at rs7412."""
        _seed_apoe_variants(sample_engine, "TT", "--")
        result = determine_apoe_genotype(sample_engine)

        assert not result.is_determined
        assert result.status == APOEStatus.NO_CALL

    def test_no_call_both(self, sample_engine: sa.Engine) -> None:
        """No-call at both SNPs."""
        _seed_apoe_variants(sample_engine, "--", "--")
        result = determine_apoe_genotype(sample_engine)

        assert not result.is_determined
        assert result.status == APOEStatus.NO_CALL

    def test_no_call_zero_genotype(self, sample_engine: sa.Engine) -> None:
        """00 genotype treated as no-call."""
        _seed_apoe_variants(sample_engine, "00", "CC")
        result = determine_apoe_genotype(sample_engine)

        assert not result.is_determined
        assert result.status == APOEStatus.NO_CALL


# ── APOEResult properties ────────────────────────────────────────────────


class TestAPOEResultProperties:
    """Test APOEResult computed properties."""

    def test_e4_properties_when_absent(self) -> None:
        result = APOEResult(
            status=APOEStatus.DETERMINED,
            allele1=APOEAllele.E3,
            allele2=APOEAllele.E3,
            diplotype="ε3/ε3",
        )
        assert not result.has_e4
        assert result.e4_count == 0

    def test_e4_properties_heterozygous(self) -> None:
        result = APOEResult(
            status=APOEStatus.DETERMINED,
            allele1=APOEAllele.E3,
            allele2=APOEAllele.E4,
            diplotype="ε3/ε4",
        )
        assert result.has_e4
        assert result.e4_count == 1

    def test_e4_properties_homozygous(self) -> None:
        result = APOEResult(
            status=APOEStatus.DETERMINED,
            allele1=APOEAllele.E4,
            allele2=APOEAllele.E4,
            diplotype="ε4/ε4",
        )
        assert result.has_e4
        assert result.e4_count == 2

    def test_e2_properties(self) -> None:
        result = APOEResult(
            status=APOEStatus.DETERMINED,
            allele1=APOEAllele.E2,
            allele2=APOEAllele.E3,
            diplotype="ε2/ε3",
        )
        assert result.has_e2
        assert result.e2_count == 1
        assert not result.has_e4
        assert result.e4_count == 0

    def test_undetermined_properties(self) -> None:
        result = APOEResult(status=APOEStatus.MISSING_SNPS)
        assert not result.has_e4
        assert result.e4_count == 0
        assert not result.has_e2
        assert result.e2_count == 0
        assert not result.is_determined


# ── Finding storage ──────────────────────────────────────────────────────


class TestAPOEFindingStorage:
    """Test APOE finding persistence."""

    def test_store_determined_finding(self, sample_engine: sa.Engine) -> None:
        """Determined APOE result creates one finding."""
        _seed_apoe_variants(sample_engine, "CT", "CC")
        result = determine_apoe_genotype(sample_engine)
        count = store_apoe_finding(result, sample_engine)

        assert count == 1

        with sample_engine.connect() as conn:
            rows = conn.execute(sa.select(findings).where(findings.c.module == "apoe")).fetchall()

        assert len(rows) == 1
        row = rows[0]
        assert row.module == "apoe"
        assert row.category == "genotype"
        assert row.gene_symbol == "APOE"
        assert row.evidence_level == 4
        assert row.diplotype == "ε3/ε4"
        assert "ε3/ε4" in row.finding_text
        assert "1× ε4" in row.finding_text

        detail = json.loads(row.detail_json)
        assert detail["allele1"] == "ε3"
        assert detail["allele2"] == "ε4"
        assert detail["has_e4"] is True
        assert detail["e4_count"] == 1
        assert detail["rs429358_genotype"] == "CT"
        assert detail["rs7412_genotype"] == "CC"

    def test_store_e3_e3_no_e4_text(self, sample_engine: sa.Engine) -> None:
        """ε3/ε3 finding text does not mention ε4."""
        _seed_apoe_variants(sample_engine, "TT", "CC")
        result = determine_apoe_genotype(sample_engine)
        store_apoe_finding(result, sample_engine)

        with sample_engine.connect() as conn:
            row = conn.execute(sa.select(findings).where(findings.c.module == "apoe")).fetchone()

        assert row is not None
        assert "ε4" not in row.finding_text
        assert "ε3/ε3" in row.finding_text

    def test_store_e4_e4_two_alleles(self, sample_engine: sa.Engine) -> None:
        """ε4/ε4 finding text mentions 2× ε4."""
        _seed_apoe_variants(sample_engine, "CC", "CC")
        result = determine_apoe_genotype(sample_engine)
        store_apoe_finding(result, sample_engine)

        with sample_engine.connect() as conn:
            row = conn.execute(sa.select(findings).where(findings.c.module == "apoe")).fetchone()

        assert row is not None
        assert "2× ε4" in row.finding_text

    def test_idempotent_rerun(self, sample_engine: sa.Engine) -> None:
        """Re-running store clears previous findings and inserts new one."""
        _seed_apoe_variants(sample_engine, "TT", "CC")
        result = determine_apoe_genotype(sample_engine)

        store_apoe_finding(result, sample_engine)
        store_apoe_finding(result, sample_engine)

        with sample_engine.connect() as conn:
            count = conn.execute(
                sa.select(sa.func.count())
                .select_from(findings)
                .where(
                    findings.c.module == "apoe",
                    findings.c.category == "genotype",
                )
            ).scalar()

        assert count == 1

    def test_store_skipped_when_not_determined(self, sample_engine: sa.Engine) -> None:
        """Undetermined result stores no finding."""
        result = determine_apoe_genotype(sample_engine)  # no variants seeded
        count = store_apoe_finding(result, sample_engine)

        assert count == 0

        with sample_engine.connect() as conn:
            count = conn.execute(
                sa.select(sa.func.count()).select_from(findings).where(findings.c.module == "apoe")
            ).scalar()

        assert count == 0

    def test_store_clears_previous_when_not_determined(self, sample_engine: sa.Engine) -> None:
        """If a previous finding exists but new run is undetermined, it gets cleared."""
        # First run: determined
        _seed_apoe_variants(sample_engine, "TT", "CC")
        result1 = determine_apoe_genotype(sample_engine)
        store_apoe_finding(result1, sample_engine)

        # Remove raw variants to simulate missing data on re-run
        with sample_engine.begin() as conn:
            conn.execute(sa.delete(raw_variants))

        result2 = determine_apoe_genotype(sample_engine)
        store_apoe_finding(result2, sample_engine)

        with sample_engine.connect() as conn:
            count = conn.execute(
                sa.select(sa.func.count()).select_from(findings).where(findings.c.module == "apoe")
            ).scalar()

        assert count == 0

    def test_does_not_affect_other_module_findings(self, sample_engine: sa.Engine) -> None:
        """APOE finding storage doesn't touch findings from other modules."""
        # Insert a cardiovascular finding first
        with sample_engine.begin() as conn:
            conn.execute(
                sa.insert(findings),
                [
                    {
                        "module": "cardiovascular",
                        "category": "monogenic_variant",
                        "finding_text": "Test cardiovascular finding",
                    }
                ],
            )

        _seed_apoe_variants(sample_engine, "CT", "CC")
        result = determine_apoe_genotype(sample_engine)
        store_apoe_finding(result, sample_engine)

        with sample_engine.connect() as conn:
            cv_count = conn.execute(
                sa.select(sa.func.count())
                .select_from(findings)
                .where(findings.c.module == "cardiovascular")
            ).scalar()
            apoe_count = conn.execute(
                sa.select(sa.func.count()).select_from(findings).where(findings.c.module == "apoe")
            ).scalar()

        assert cv_count == 1
        assert apoe_count == 1


# ── Allele enum ──────────────────────────────────────────────────────────


class TestAPOEAlleleEnum:
    """Test APOEAllele enum values."""

    def test_values(self) -> None:
        assert APOEAllele.E2.value == "ε2"
        assert APOEAllele.E3.value == "ε3"
        assert APOEAllele.E4.value == "ε4"

    def test_sorting(self) -> None:
        alleles = sorted([APOEAllele.E4, APOEAllele.E2, APOEAllele.E3], key=lambda a: a.value)
        assert alleles == [APOEAllele.E2, APOEAllele.E3, APOEAllele.E4]


# ══════════════════════════════════════════════════════════════════════════
# P3-22b: APOE Three Findings Generation
# ══════════════════════════════════════════════════════════════════════════


class TestAPOEFindingsGeneration:
    """Test APOE three findings generation for all diplotypes."""

    ALL_DIPLOTYPES = ["ε2/ε2", "ε2/ε3", "ε2/ε4", "ε3/ε3", "ε3/ε4", "ε4/ε4"]

    # Genotype combos to produce each diplotype
    DIPLOTYPE_GENOTYPES: dict[str, tuple[str, str]] = {
        "ε2/ε2": ("TT", "TT"),
        "ε2/ε3": ("TT", "CT"),
        "ε2/ε4": ("CT", "CT"),
        "ε3/ε3": ("TT", "CC"),
        "ε3/ε4": ("CT", "CC"),
        "ε4/ε4": ("CC", "CC"),
    }

    def _make_result(self, diplotype: str) -> APOEResult:
        """Create a determined APOEResult for a given diplotype."""
        allele_map = {"ε2": APOEAllele.E2, "ε3": APOEAllele.E3, "ε4": APOEAllele.E4}
        a1_str, a2_str = diplotype.split("/")
        a1, a2 = allele_map[a1_str], allele_map[a2_str]
        return APOEResult(
            status=APOEStatus.DETERMINED,
            allele1=a1,
            allele2=a2,
            diplotype=diplotype,
            rs429358_genotype="TT",
            rs7412_genotype="CC",
        )

    @pytest.mark.parametrize("diplotype", ALL_DIPLOTYPES, ids=ALL_DIPLOTYPES)
    def test_generates_exactly_three_findings(self, diplotype: str) -> None:
        """Every determined diplotype produces exactly 3 findings."""
        result = self._make_result(diplotype)
        findings_list = generate_apoe_findings(result)

        assert len(findings_list) == 3

    @pytest.mark.parametrize("diplotype", ALL_DIPLOTYPES, ids=ALL_DIPLOTYPES)
    def test_finding_categories(self, diplotype: str) -> None:
        """Three findings have the correct categories."""
        result = self._make_result(diplotype)
        findings_list = generate_apoe_findings(result)

        categories = [f.category for f in findings_list]
        assert categories == [APOE_FINDING_CV, APOE_FINDING_ALZHEIMERS, APOE_FINDING_LIPID]

    @pytest.mark.parametrize("diplotype", ALL_DIPLOTYPES, ids=ALL_DIPLOTYPES)
    def test_evidence_levels(self, diplotype: str) -> None:
        """CV and Alzheimer's are ★★★★, lipid/dietary is ★★★☆."""
        result = self._make_result(diplotype)
        findings_list = generate_apoe_findings(result)

        assert findings_list[0].evidence_level == 4  # CV
        assert findings_list[1].evidence_level == 4  # Alzheimer's
        assert findings_list[2].evidence_level == 3  # Lipid/dietary

    @pytest.mark.parametrize("diplotype", ALL_DIPLOTYPES, ids=ALL_DIPLOTYPES)
    def test_all_findings_have_pmid_citations(self, diplotype: str) -> None:
        """Every finding has non-empty PubMed citations."""
        result = self._make_result(diplotype)
        findings_list = generate_apoe_findings(result)

        for f in findings_list:
            assert len(f.pmid_citations) > 0
            assert all(pmid.isdigit() for pmid in f.pmid_citations)

    @pytest.mark.parametrize("diplotype", ALL_DIPLOTYPES, ids=ALL_DIPLOTYPES)
    def test_all_findings_have_nonempty_text(self, diplotype: str) -> None:
        """Every finding has non-empty finding_text, conditions, and phenotype."""
        result = self._make_result(diplotype)
        findings_list = generate_apoe_findings(result)

        for f in findings_list:
            assert len(f.finding_text) > 0
            assert len(f.conditions) > 0
            assert len(f.phenotype) > 0

    @pytest.mark.parametrize("diplotype", ALL_DIPLOTYPES, ids=ALL_DIPLOTYPES)
    def test_detail_json_contains_diplotype(self, diplotype: str) -> None:
        """Every finding's detail_json contains the diplotype."""
        result = self._make_result(diplotype)
        findings_list = generate_apoe_findings(result)

        for f in findings_list:
            assert f.detail_json["diplotype"] == diplotype

    def test_undetermined_returns_empty(self) -> None:
        """Undetermined result produces no findings."""
        result = APOEResult(status=APOEStatus.MISSING_SNPS)
        findings_list = generate_apoe_findings(result)

        assert findings_list == []


class TestAPOEFindingsContentCV:
    """Test cardiovascular risk finding content specifics."""

    def test_e2_e2_type_iii_hlp(self) -> None:
        """ε2/ε2 CV finding mentions Type III hyperlipoproteinemia."""
        result = APOEResult(
            status=APOEStatus.DETERMINED,
            allele1=APOEAllele.E2,
            allele2=APOEAllele.E2,
            diplotype="ε2/ε2",
        )
        findings_list = generate_apoe_findings(result)
        cv = findings_list[0]

        assert "Type III hyperlipoproteinemia" in cv.finding_text
        assert "Type III hyperlipoproteinemia" in cv.conditions
        assert cv.detail_json["risk_level"] == "elevated"

    def test_e3_e3_reference(self) -> None:
        """ε3/ε3 CV finding is the population reference."""
        result = APOEResult(
            status=APOEStatus.DETERMINED,
            allele1=APOEAllele.E3,
            allele2=APOEAllele.E3,
            diplotype="ε3/ε3",
        )
        findings_list = generate_apoe_findings(result)
        cv = findings_list[0]

        assert "reference" in cv.finding_text.lower()
        assert cv.detail_json["risk_level"] == "reference"

    def test_e4_e4_elevated_ldl(self) -> None:
        """ε4/ε4 CV finding mentions elevated LDL."""
        result = APOEResult(
            status=APOEStatus.DETERMINED,
            allele1=APOEAllele.E4,
            allele2=APOEAllele.E4,
            diplotype="ε4/ε4",
        )
        findings_list = generate_apoe_findings(result)
        cv = findings_list[0]

        assert "higher LDL" in cv.finding_text
        assert cv.detail_json["risk_level"] == "elevated"

    def test_cv_conditions_include_statin(self) -> None:
        """All CV findings mention statin response in conditions."""
        result = APOEResult(
            status=APOEStatus.DETERMINED,
            allele1=APOEAllele.E3,
            allele2=APOEAllele.E4,
            diplotype="ε3/ε4",
        )
        findings_list = generate_apoe_findings(result)
        cv = findings_list[0]

        assert "statin response" in cv.conditions


class TestAPOEFindingsContentAlzheimers:
    """Test Alzheimer's risk finding content specifics."""

    def test_e4_e4_alzheimers_golden_fixture_t3_18(self) -> None:
        """T3-18: ε4/ε4 Alzheimer's finding with caveats and non-actionable framing."""
        result = APOEResult(
            status=APOEStatus.DETERMINED,
            allele1=APOEAllele.E4,
            allele2=APOEAllele.E4,
            diplotype="ε4/ε4",
        )
        findings_list = generate_apoe_findings(result)
        alz = findings_list[1]

        assert alz.category == APOE_FINDING_ALZHEIMERS
        assert alz.evidence_level == 4
        assert "Alzheimer" in alz.finding_text
        assert "8–12×" in alz.finding_text
        assert "not a diagnosis" in alz.finding_text.lower()
        assert "probabilistic" in alz.finding_text.lower()
        assert alz.detail_json["non_actionable"] is True
        assert "not a diagnosis" in alz.detail_json["caveats"].lower()
        assert alz.detail_json["approximate_or"] == 11.6
        assert alz.detail_json["relative_risk"] == "substantially_elevated"

    def test_e3_e4_alzheimers_moderate_risk(self) -> None:
        """ε3/ε4 Alzheimer's finding mentions ~3.2× risk."""
        result = APOEResult(
            status=APOEStatus.DETERMINED,
            allele1=APOEAllele.E3,
            allele2=APOEAllele.E4,
            diplotype="ε3/ε4",
        )
        findings_list = generate_apoe_findings(result)
        alz = findings_list[1]

        assert "3.2×" in alz.finding_text
        assert alz.detail_json["approximate_or"] == 3.2

    def test_e3_e3_alzheimers_reference(self) -> None:
        """ε3/ε3 Alzheimer's finding is the population reference."""
        result = APOEResult(
            status=APOEStatus.DETERMINED,
            allele1=APOEAllele.E3,
            allele2=APOEAllele.E3,
            diplotype="ε3/ε3",
        )
        findings_list = generate_apoe_findings(result)
        alz = findings_list[1]

        assert "reference" in alz.finding_text.lower()
        assert alz.detail_json["approximate_or"] == 1.0

    def test_e2_e3_alzheimers_reduced_risk(self) -> None:
        """ε2/ε3 Alzheimer's finding mentions reduced risk."""
        result = APOEResult(
            status=APOEStatus.DETERMINED,
            allele1=APOEAllele.E2,
            allele2=APOEAllele.E3,
            diplotype="ε2/ε3",
        )
        findings_list = generate_apoe_findings(result)
        alz = findings_list[1]

        assert "reduced" in alz.finding_text.lower()
        assert alz.detail_json["approximate_or"] < 1.0

    @pytest.mark.parametrize("diplotype", ["ε2/ε2", "ε2/ε3", "ε2/ε4", "ε3/ε3", "ε3/ε4", "ε4/ε4"])
    def test_alzheimers_risk_estimates_include_population_context(self, diplotype: str) -> None:
        """APOE AD risk estimates must say they are not individually calibrated."""
        allele_map = {"ε2": APOEAllele.E2, "ε3": APOEAllele.E3, "ε4": APOEAllele.E4}
        allele1, allele2 = diplotype.split("/")
        result = APOEResult(
            status=APOEStatus.DETERMINED,
            allele1=allele_map[allele1],
            allele2=allele_map[allele2],
            diplotype=diplotype,
        )

        findings_list = generate_apoe_findings(result)
        alz = findings_list[1]
        text = alz.finding_text.lower()
        context = alz.detail_json["risk_estimate_context"].lower()

        assert "population-aggregate" in text
        assert "age" in text
        assert "sex" in text
        assert "race/ethnicity" in text
        assert "genetic ancestry" in text
        assert "not calibrated" in context
        assert "37930705" in alz.pmid_citations
        assert "32818802" in alz.pmid_citations

    def test_all_alzheimers_conditions_field(self) -> None:
        """All Alzheimer's findings have 'Alzheimer's disease' as conditions."""
        for diplotype in ["ε2/ε2", "ε2/ε3", "ε2/ε4", "ε3/ε3", "ε3/ε4", "ε4/ε4"]:
            allele_map = {"ε2": APOEAllele.E2, "ε3": APOEAllele.E3, "ε4": APOEAllele.E4}
            a1, a2 = diplotype.split("/")
            result = APOEResult(
                status=APOEStatus.DETERMINED,
                allele1=allele_map[a1],
                allele2=allele_map[a2],
                diplotype=diplotype,
            )
            findings_list = generate_apoe_findings(result)
            alz = findings_list[1]
            assert alz.conditions == "Alzheimer's disease"


class TestAPOEFindingsContentLipid:
    """Test lipid/dietary context finding content specifics."""

    def test_e3_e3_typical_response(self) -> None:
        """ε3/ε3 has typical dietary fat response."""
        result = APOEResult(
            status=APOEStatus.DETERMINED,
            allele1=APOEAllele.E3,
            allele2=APOEAllele.E3,
            diplotype="ε3/ε3",
        )
        findings_list = generate_apoe_findings(result)
        lipid = findings_list[2]

        assert "typical" in lipid.finding_text.lower()
        assert lipid.evidence_level == 3
        assert lipid.detail_json["dietary_response"] == "typical"

    def test_e4_e4_enhanced_response(self) -> None:
        """ε4/ε4 has markedly enhanced LDL sensitivity to saturated fat."""
        result = APOEResult(
            status=APOEStatus.DETERMINED,
            allele1=APOEAllele.E4,
            allele2=APOEAllele.E4,
            diplotype="ε4/ε4",
        )
        findings_list = generate_apoe_findings(result)
        lipid = findings_list[2]

        assert "greatest LDL increase" in lipid.finding_text
        assert lipid.detail_json["dietary_response"] == "markedly_enhanced"

    def test_e2_e2_atypical_response(self) -> None:
        """ε2/ε2 has atypical dietary fat response."""
        result = APOEResult(
            status=APOEStatus.DETERMINED,
            allele1=APOEAllele.E2,
            allele2=APOEAllele.E2,
            diplotype="ε2/ε2",
        )
        findings_list = generate_apoe_findings(result)
        lipid = findings_list[2]

        assert "atypical" in lipid.finding_text.lower()
        assert lipid.detail_json["dietary_response"] == "atypical"

    def test_lipid_conditions_saturated_fat(self) -> None:
        """All lipid findings reference saturated fat."""
        result = APOEResult(
            status=APOEStatus.DETERMINED,
            allele1=APOEAllele.E3,
            allele2=APOEAllele.E3,
            diplotype="ε3/ε3",
        )
        findings_list = generate_apoe_findings(result)
        lipid = findings_list[2]

        assert "Saturated fat" in lipid.conditions


# ── Three findings storage ──────────────────────────────────────────────


class TestAPOEThreeFindingsStorage:
    """Test APOE three findings persistence."""

    def test_stores_three_findings(self, sample_engine: sa.Engine) -> None:
        """Determined result creates exactly 3 analysis findings."""
        _seed_apoe_variants(sample_engine, "CT", "CC")  # ε3/ε4
        result = determine_apoe_genotype(sample_engine)
        count = store_apoe_three_findings(result, sample_engine)

        assert count == 3

        with sample_engine.connect() as conn:
            rows = conn.execute(
                sa.select(findings).where(
                    findings.c.module == "apoe",
                    findings.c.category.in_(APOE_FINDING_CATEGORIES),
                )
            ).fetchall()

        assert len(rows) == 3
        categories = {r.category for r in rows}
        assert categories == {APOE_FINDING_CV, APOE_FINDING_ALZHEIMERS, APOE_FINDING_LIPID}

    def test_all_rows_have_gene_symbol(self, sample_engine: sa.Engine) -> None:
        """All three findings have gene_symbol='APOE'."""
        _seed_apoe_variants(sample_engine, "TT", "CC")
        result = determine_apoe_genotype(sample_engine)
        store_apoe_three_findings(result, sample_engine)

        with sample_engine.connect() as conn:
            rows = conn.execute(
                sa.select(findings).where(
                    findings.c.module == "apoe",
                    findings.c.category.in_(APOE_FINDING_CATEGORIES),
                )
            ).fetchall()

        for row in rows:
            assert row.gene_symbol == "APOE"
            assert row.diplotype == "ε3/ε3"

    def test_pmid_citations_stored_as_json(self, sample_engine: sa.Engine) -> None:
        """PubMed citations stored as JSON arrays."""
        _seed_apoe_variants(sample_engine, "CT", "CC")
        result = determine_apoe_genotype(sample_engine)
        store_apoe_three_findings(result, sample_engine)

        with sample_engine.connect() as conn:
            rows = conn.execute(
                sa.select(findings).where(
                    findings.c.module == "apoe",
                    findings.c.category.in_(APOE_FINDING_CATEGORIES),
                )
            ).fetchall()

        for row in rows:
            pmids = json.loads(row.pmid_citations)
            assert isinstance(pmids, list)
            assert len(pmids) > 0

    def test_detail_json_stored(self, sample_engine: sa.Engine) -> None:
        """Detail JSON is valid and contains diplotype."""
        _seed_apoe_variants(sample_engine, "CC", "CC")  # ε4/ε4
        result = determine_apoe_genotype(sample_engine)
        store_apoe_three_findings(result, sample_engine)

        with sample_engine.connect() as conn:
            rows = conn.execute(
                sa.select(findings).where(
                    findings.c.module == "apoe",
                    findings.c.category.in_(APOE_FINDING_CATEGORIES),
                )
            ).fetchall()

        for row in rows:
            detail = json.loads(row.detail_json)
            assert detail["diplotype"] == "ε4/ε4"

    def test_idempotent_rerun(self, sample_engine: sa.Engine) -> None:
        """Re-running store replaces previous findings."""
        _seed_apoe_variants(sample_engine, "TT", "CC")
        result = determine_apoe_genotype(sample_engine)

        store_apoe_three_findings(result, sample_engine)
        store_apoe_three_findings(result, sample_engine)

        with sample_engine.connect() as conn:
            count = conn.execute(
                sa.select(sa.func.count())
                .select_from(findings)
                .where(
                    findings.c.module == "apoe",
                    findings.c.category.in_(APOE_FINDING_CATEGORIES),
                )
            ).scalar()

        assert count == 3

    def test_does_not_touch_genotype_finding(self, sample_engine: sa.Engine) -> None:
        """Three findings storage does not affect the genotype finding."""
        _seed_apoe_variants(sample_engine, "CT", "CC")
        result = determine_apoe_genotype(sample_engine)

        store_apoe_finding(result, sample_engine)  # genotype finding
        store_apoe_three_findings(result, sample_engine)  # three analysis findings

        with sample_engine.connect() as conn:
            genotype_count = conn.execute(
                sa.select(sa.func.count())
                .select_from(findings)
                .where(
                    findings.c.module == "apoe",
                    findings.c.category == "genotype",
                )
            ).scalar()
            analysis_count = conn.execute(
                sa.select(sa.func.count())
                .select_from(findings)
                .where(
                    findings.c.module == "apoe",
                    findings.c.category.in_(APOE_FINDING_CATEGORIES),
                )
            ).scalar()

        assert genotype_count == 1
        assert analysis_count == 3

    def test_skipped_when_not_determined(self, sample_engine: sa.Engine) -> None:
        """Undetermined result stores no analysis findings."""
        result = determine_apoe_genotype(sample_engine)
        count = store_apoe_three_findings(result, sample_engine)

        assert count == 0

    def test_clears_previous_when_not_determined(self, sample_engine: sa.Engine) -> None:
        """Re-run with undetermined result clears previous analysis findings."""
        _seed_apoe_variants(sample_engine, "CT", "CC")
        result1 = determine_apoe_genotype(sample_engine)
        store_apoe_three_findings(result1, sample_engine)

        with sample_engine.begin() as conn:
            conn.execute(sa.delete(raw_variants))

        result2 = determine_apoe_genotype(sample_engine)
        store_apoe_three_findings(result2, sample_engine)

        with sample_engine.connect() as conn:
            count = conn.execute(
                sa.select(sa.func.count())
                .select_from(findings)
                .where(
                    findings.c.module == "apoe",
                    findings.c.category.in_(APOE_FINDING_CATEGORIES),
                )
            ).scalar()

        assert count == 0

    def test_does_not_affect_other_modules(self, sample_engine: sa.Engine) -> None:
        """Three findings storage doesn't touch other module findings."""
        with sample_engine.begin() as conn:
            conn.execute(
                sa.insert(findings),
                [
                    {
                        "module": "cardiovascular",
                        "category": "monogenic_variant",
                        "finding_text": "Test CV finding",
                    }
                ],
            )

        _seed_apoe_variants(sample_engine, "CT", "CC")
        result = determine_apoe_genotype(sample_engine)
        store_apoe_three_findings(result, sample_engine)

        with sample_engine.connect() as conn:
            cv_count = conn.execute(
                sa.select(sa.func.count())
                .select_from(findings)
                .where(findings.c.module == "cardiovascular")
            ).scalar()

        assert cv_count == 1


def test_apoe_position_constants_are_grch37() -> None:
    """#476: the APOE SNP position constants must be GRCh37 (the app's primary
    build / vep_bundle), not the GRCh38 positions that were hardcoded while
    mislabeled GRCh37. Per Ensembl GRCh37 REST: rs429358 = 19:45411941,
    rs7412 = 19:45412079 (the GRCh38 positions are 44908684 / 44908822).
    """
    from backend.analysis.apoe import (
        APOE_CHROM,
        APOE_RS7412_POS,
        APOE_RS429358_POS,
    )

    assert APOE_CHROM == "19"
    assert APOE_RS429358_POS == 45411941
    assert APOE_RS7412_POS == 45412079
    # explicitly NOT the GRCh38 coordinates
    assert APOE_RS429358_POS != 44908684
    assert APOE_RS7412_POS != 44908822


class TestAPOEArrayReliabilityCaveat:
    """#557: APOE ε-status from a consumer array is not equivalent to clinical
    genotyping (rs429358/rs7412 are an array weak spot). Every APOE finding must
    carry an array-reliability caveat recommending CLIA confirmation, and the
    module must not assert unconditional "no partial-call ambiguity"."""

    ALL_DIPLOTYPES = ["ε2/ε2", "ε2/ε3", "ε2/ε4", "ε3/ε3", "ε3/ε4", "ε4/ε4"]

    def _make_result(self, diplotype: str) -> APOEResult:
        allele_map = {"ε2": APOEAllele.E2, "ε3": APOEAllele.E3, "ε4": APOEAllele.E4}
        a1_str, a2_str = diplotype.split("/")
        return APOEResult(
            status=APOEStatus.DETERMINED,
            allele1=allele_map[a1_str],
            allele2=allele_map[a2_str],
            diplotype=diplotype,
            rs429358_genotype="CT",
            rs7412_genotype="CC",
        )

    def test_module_docstring_drops_no_ambiguity_overclaim(self) -> None:
        """The false "no partial-call ambiguity" claim is gone; the honest
        array-reliability caveat is documented instead."""
        doc = apoe_module.__doc__ or ""
        assert "no partial-call ambiguity" not in doc
        assert "Array-reliability caveat" in doc

    def test_caveat_is_canonical_and_clia_framed(self) -> None:
        """The caveat recommends CLIA confirmation and names the real concern."""
        caveat = APOE_ARRAY_RELIABILITY_CAVEAT.lower()
        assert "clia" in caveat
        assert "rs429358" in caveat
        assert "confirm" in caveat
        # Carries re-verifiable reliability citations (digit PMIDs).
        assert APOE_RELIABILITY_PMIDS
        assert all(pmid.isdigit() for pmid in APOE_RELIABILITY_PMIDS)

    @pytest.mark.parametrize("diplotype", ALL_DIPLOTYPES, ids=ALL_DIPLOTYPES)
    def test_all_three_findings_carry_array_reliability_flag(self, diplotype: str) -> None:
        """Every derived finding (CV / Alzheimer's / lipid) carries the flag."""
        findings_list = generate_apoe_findings(self._make_result(diplotype))
        assert len(findings_list) == 3
        for f in findings_list:
            flag = f.detail_json.get("array_reliability")
            assert flag is not None, f"{f.category}: missing array_reliability flag"
            assert flag["confirm_in_clia_recommended"] is True
            assert "clia" in flag["caveat"].lower()
            assert flag["pmids"] == APOE_RELIABILITY_PMIDS

    @pytest.mark.parametrize("diplotype", ALL_DIPLOTYPES, ids=ALL_DIPLOTYPES)
    def test_alzheimers_caveats_text_includes_array_reliability(self, diplotype: str) -> None:
        """The most sensitive finding folds the caveat into its caveats text too."""
        findings_list = generate_apoe_findings(self._make_result(diplotype))
        alz = next(f for f in findings_list if f.category == APOE_FINDING_ALZHEIMERS)
        caveats = alz.detail_json["caveats"].lower()
        assert "clia" in caveats and "rs429358" in caveats

    def test_genotype_finding_detail_carries_flag(self) -> None:
        """The stored genotype finding also carries the array-reliability flag."""
        engine = sa.create_engine("sqlite://")
        create_sample_tables(engine)
        # rs429358=CT, rs7412=CC -> ε3/ε4 (an ε4 carrier — the sensitive case)
        with engine.begin() as conn:
            conn.execute(
                sa.insert(raw_variants),
                [
                    {"rsid": "rs429358", "chrom": "19", "pos": 45411941, "genotype": "CT"},
                    {"rsid": "rs7412", "chrom": "19", "pos": 45412079, "genotype": "CC"},
                ],
            )
        result = determine_apoe_genotype(engine)
        assert result.has_e4
        assert store_apoe_finding(result, engine) == 1
        with engine.begin() as conn:
            row = conn.execute(
                sa.select(findings.c.detail_json).where(
                    findings.c.module == "apoe", findings.c.category == "genotype"
                )
            ).fetchone()
        detail = json.loads(row.detail_json)
        assert detail["array_reliability"]["confirm_in_clia_recommended"] is True
        assert "clia" in detail["array_reliability"]["caveat"].lower()

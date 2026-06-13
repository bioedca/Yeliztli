"""G6PD deficiency X-linked context (SW-E6).

Verifies the forward-strand, sex-aware deficiency calling the route serves:
hemizygous males (single-char chrX calls) → deficient on one allele; females →
deficient when homozygous at one locus, *variable* when single-het (never a
reassuring "normal"), and *phase_indeterminate* when two different deficiency loci
are heterozygous (an array cannot phase trans compound-het vs cis). Strands are
GRCh37 plus/forward (as real 23andMe data is).
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
import sqlalchemy as sa

from backend.analysis.g6pd import (
    G6PD_376_RSID,
    G6PD_A_MINUS_RSID,
    G6PD_DEFICIENCY_VARIANTS,
    G6PD_MED_RSID,
    G6PD_PMID_CITATIONS,
    _deficiency_alleles,
    _is_palindromic,
    assess_g6pd,
    g6pd_phenotype,
)
from backend.db.sample_schema import create_sample_tables
from backend.db.tables import raw_variants


def _make_sample(genotypes: dict[str, str]) -> sa.Engine:
    engine = sa.create_engine("sqlite://")
    create_sample_tables(engine)
    rows = [
        {"rsid": rsid, "chrom": "X", "pos": 153764217 + i, "genotype": g}
        for i, (rsid, g) in enumerate(genotypes.items())
    ]
    with engine.begin() as conn:
        conn.execute(raw_variants.insert(), rows)
    return engine


class TestDeficiencyAlleles:
    def test_hemizygous_single_char(self) -> None:
        assert _deficiency_alleles("T", "C", "T") == {"deficiency": 1, "copies": 1}
        assert _deficiency_alleles("C", "C", "T") == {"deficiency": 0, "copies": 1}

    def test_diploid(self) -> None:
        assert _deficiency_alleles("CC", "C", "T") == {"deficiency": 0, "copies": 2}
        assert _deficiency_alleles("CT", "C", "T") == {"deficiency": 1, "copies": 2}
        assert _deficiency_alleles("TT", "C", "T") == {"deficiency": 2, "copies": 2}

    def test_no_call_and_invalid(self) -> None:
        assert _deficiency_alleles("--", "C", "T") is None
        assert _deficiency_alleles("", "C", "T") is None
        assert _deficiency_alleles("G", "C", "T") is None  # unexpected base
        assert _deficiency_alleles("CG", "C", "T") is None  # third allele

    def test_palindromic_homozygote_withheld(self) -> None:
        # C/G is palindromic: a homozygote/hemizygote is strand-unresolvable, so it is
        # withheld (None) — a wrong-strand report of ref "C" is indistinguishable from
        # def "G". Only the strand-invariant heterozygote is counted.
        assert _deficiency_alleles("G", "C", "G") is None  # hemizygous def — withheld
        assert _deficiency_alleles("C", "C", "G") is None  # hemizygous ref — withheld
        assert _deficiency_alleles("GG", "C", "G") is None  # homozygous — withheld
        assert _deficiency_alleles("CC", "C", "G") is None  # homozygous — withheld
        assert _deficiency_alleles("CG", "C", "G") == {"deficiency": 1, "copies": 2}

    def test_is_palindromic(self) -> None:
        assert _is_palindromic("C", "G") and _is_palindromic("A", "T")
        assert not _is_palindromic("C", "T") and not _is_palindromic("G", "A")


class TestG6pdPhenotype:
    def test_male_one_allele_is_deficient(self) -> None:
        assert g6pd_phenotype("XY", 1, True, 1)["phenotype"] == "deficient"

    def test_male_zero_is_normal(self) -> None:
        assert g6pd_phenotype("XY", 0, True, 0)["phenotype"] == "normal"

    def test_female_homozygous_single_locus_is_deficient(self) -> None:
        # Two deficiency alleles at ONE locus (max_locus == 2) → both X's affected,
        # phase-unambiguous → deficient.
        assert g6pd_phenotype("XX", 2, True, 2)["phenotype"] == "deficient"

    def test_female_two_unphased_loci_is_phase_indeterminate(self) -> None:
        # Two deficiency alleles summed across two heterozygous loci (max_locus == 1):
        # an array cannot phase trans (compound-het → deficient) vs cis (→ variable).
        v = g6pd_phenotype("XX", 2, True, 1)
        assert v["phenotype"] == "phase_indeterminate"
        assert "phase" in v["detail"].lower()
        assert "enzyme" in v["detail"].lower()

    def test_female_one_is_variable(self) -> None:
        assert g6pd_phenotype("XX", 1, True, 1)["phenotype"] == "variable"

    def test_female_zero_is_normal(self) -> None:
        assert g6pd_phenotype("XX", 0, True, 0)["phenotype"] == "normal"

    def test_unknown_sex_with_deficiency_is_indeterminate(self) -> None:
        for sex in ("unknown", "manual_review"):
            v = g6pd_phenotype(sex, 1, True, 1)
            assert v["phenotype"] == "indeterminate"
            assert "sex" in v["detail"].lower()

    def test_not_called_is_indeterminate(self) -> None:
        assert g6pd_phenotype("XX", 0, False, 0)["phenotype"] == "indeterminate"


class TestAssessG6pd:
    def _assess(self, sex: str, genotypes: dict[str, str]) -> dict:
        engine = _make_sample(genotypes)
        with patch("backend.analysis.g6pd.infer_biological_sex", return_value=sex):
            return assess_g6pd(engine)

    def test_hemizygous_male_a_minus_deficient(self) -> None:
        r = self._assess("XY", {G6PD_A_MINUS_RSID: "T"})  # single-char hemizygous
        assert r["phenotype"] == "deficient"
        assert r["at_risk"] is True
        assert "rasburicase" in r["high_risk_drugs"]
        assert r["inferred_sex"] == "XY"

    def test_hemizygous_male_normal(self) -> None:
        r = self._assess("XY", {G6PD_A_MINUS_RSID: "C"})
        assert r["phenotype"] == "normal"
        assert r["at_risk"] is False
        assert r["high_risk_drugs"] == []

    def test_female_heterozygous_is_variable(self) -> None:
        r = self._assess("XX", {G6PD_A_MINUS_RSID: "CT"})
        assert r["phenotype"] == "variable"
        assert r["at_risk"] is True  # variable still warrants caution

    def test_female_homozygous_deficient(self) -> None:
        r = self._assess("XX", {G6PD_A_MINUS_RSID: "TT"})
        assert r["phenotype"] == "deficient"

    def test_female_unphased_double_het_is_phase_indeterminate(self) -> None:
        # A- het + Mediterranean het: two deficiency alleles across two loci, but an
        # array does not phase them — trans (compound-het, deficient) and cis (variable)
        # are indistinguishable, so this is phase-indeterminate, NOT a definitive call.
        r = self._assess("XX", {G6PD_A_MINUS_RSID: "CT", G6PD_MED_RSID: "GA"})
        assert r["phenotype"] == "phase_indeterminate"
        assert r["at_risk"] is True  # variable-or-deficient still warrants caution
        assert r["high_risk_drugs"]  # drug context surfaced despite the uncertainty
        # Both deficiency loci were callable and each contributed one allele.
        by_rsid = {v["rsid"]: v for v in r["variants"]}
        assert by_rsid[G6PD_A_MINUS_RSID]["deficiency_alleles"] == 1
        assert by_rsid[G6PD_MED_RSID]["deficiency_alleles"] == 1

    def test_female_homozygous_locus_with_second_het_stays_deficient(self) -> None:
        # A homozygous locus (A- TT) proves both X's deficient regardless of the second
        # locus, so a same-sample Mediterranean het does not downgrade the call.
        r = self._assess("XX", {G6PD_A_MINUS_RSID: "TT", G6PD_MED_RSID: "GA"})
        assert r["phenotype"] == "deficient"
        assert r["at_risk"] is True

    def test_female_reference_normal(self) -> None:
        # Negative control: no deficiency allele → no risk surfaced.
        r = self._assess("XX", {G6PD_A_MINUS_RSID: "CC", G6PD_MED_RSID: "GG"})
        assert r["phenotype"] == "normal"
        assert r["at_risk"] is False
        assert r["high_risk_drugs"] == []

    def test_unknown_sex_with_deficiency_surfaces_drug_warning(self) -> None:
        r = self._assess("unknown", {G6PD_A_MINUS_RSID: "CT"})
        assert r["phenotype"] == "indeterminate"
        assert r["at_risk"] is True  # deficiency allele present → still warn
        assert r["high_risk_drugs"]

    def test_no_variant_called_is_indeterminate(self) -> None:
        r = self._assess("XY", {G6PD_A_MINUS_RSID: "--", G6PD_MED_RSID: "--"})
        assert r["any_called"] is False
        assert r["phenotype"] == "indeterminate"
        assert r["at_risk"] is False

    def test_a_plus_nondeficient_flagged_as_context(self) -> None:
        # 376G present (rs1050829 = C) with A- reference → A+ non-deficient allele.
        r = self._assess("XX", {G6PD_A_MINUS_RSID: "CC", G6PD_376_RSID: "CC"})
        assert r["a_plus_nondeficient_present"] is True
        assert r["phenotype"] == "normal"

    def test_context_only_disclosure_and_citation(self) -> None:
        r = self._assess("XY", {G6PD_A_MINUS_RSID: "C"})
        assert r["context_only"] is True
        assert r["note"]
        assert set(G6PD_PMID_CITATIONS) <= set(r["pmid_citations"])


class TestExpandedDeficiencyPanel:
    """Issue #209: the panel now types the common East/Southeast-Asian and other
    CPIC deficiency variants alongside A− and Mediterranean. Forward/plus-strand
    REF/DEF (GRCh37, confirmed on Ensembl GRCh37 REST + NCBI dbSNP) is locked per
    variant so a strand flip — which would turn a reference call into a false
    "deficient" — fails CI.
    """

    def _assess(self, sex: str, genotypes: dict[str, str]) -> dict:
        engine = _make_sample(genotypes)
        with patch("backend.analysis.g6pd.infer_biological_sex", return_value=sex):
            return assess_g6pd(engine)

    def test_panel_covers_expected_variants(self) -> None:
        # Exact lock keyed by variant NAME (rs72554665 is shared by Canton and Cosenza,
        # a multiallelic position, so an rsID key would collapse them). An accidental
        # add/drop (or rsID swap) trips this.
        by_name = {name: rsid for name, rsid, *_ in G6PD_DEFICIENCY_VARIANTS}
        assert by_name == {
            "A- (V68M)": "rs1050828",
            "Mediterranean (S188F)": "rs5030868",
            "Mahidol (G163S)": "rs137852314",
            "Canton (R459L)": "rs72554665",
            "Kaiping (R463H)": "rs72554664",
            "Viangchan (V291M)": "rs137852327",
            "Union (R454C)": "rs398123546",
            "Chinese-5 (L342F)": "rs137852342",
            "Coimbra (R198C)": "rs137852330",
            "Chatham (A335T)": "rs5030869",
            "Gaohe (H32R)": "rs137852340",
            "Seattle/Lodi (D282H)": "rs137852318",  # #321 European/Mediterranean
            "Cosenza (R459P)": "rs72554665",  # #321 — shares Canton's position
        }

    def test_table_rows_well_formed(self) -> None:
        assert G6PD_DEFICIENCY_VARIANTS, "panel must not be empty"
        names_seen: set[str] = set()
        ref_by_rsid: dict[str, str] = {}
        for name, rsid, cdna, ref, deff in G6PD_DEFICIENCY_VARIANTS:
            assert name and cdna.startswith("c.")
            assert name not in names_seen, f"duplicate variant name {name}"
            names_seen.add(name)
            assert rsid.startswith("rs")
            assert ref in {"A", "C", "G", "T"} and deff in {"A", "C", "G", "T"}
            assert ref != deff
            # A shared rsID is one multiallelic chrX position (Canton C>A + Cosenza
            # C>G): same locus ⇒ same forward reference base, distinct deficiency alts.
            assert ref_by_rsid.setdefault(rsid, ref) == ref, f"{rsid} ref mismatch"

    @pytest.mark.parametrize(
        ("name", "rsid", "ref", "deff"),
        # Non-palindromic loci only: a palindromic (C/G) hemizygote is strand-ambiguous
        # and deliberately withheld (covered by the palindrome tests below), so it would
        # not produce the confident hemizygous call this strand-direction lock asserts.
        [
            (n, rs, ref, deff)
            for n, rs, _, ref, deff in G6PD_DEFICIENCY_VARIANTS
            if not _is_palindromic(ref, deff)
        ],
    )
    def test_each_variant_strand_direction(
        self, name: str, rsid: str, ref: str, deff: str
    ) -> None:
        # Hemizygous male carrying the forward DEFICIENCY base → deficient + at-risk.
        r = self._assess("XY", {rsid: deff})
        assert r["phenotype"] == "deficient", name
        assert r["at_risk"] is True, name
        # The forward gene-NORMAL base → normal, no risk. A flipped REF/DEF would
        # invert both assertions, so each variant's strand is locked here.
        r = self._assess("XY", {rsid: ref})
        assert r["phenotype"] == "normal", name
        assert r["at_risk"] is False, name

    def test_canton_cosenza_share_position_without_cross_calling(self) -> None:
        # rs72554665 is multiallelic: Canton (C>A) and Cosenza (C>G) share the chrX
        # position as two rows. A "CG" het is the *Cosenza* heterozygote — G lies
        # outside Canton's {C,A}, so Canton stays not-called while Cosenza calls 1.
        r = self._assess("XX", {"rs72554665": "CG"})
        canton = next(v for v in r["variants"] if v["name"] == "Canton (R459L)")
        cosenza = next(v for v in r["variants"] if v["name"] == "Cosenza (R459P)")
        assert canton["called"] is False and canton["deficiency_alleles"] is None
        assert cosenza["called"] is True and cosenza["deficiency_alleles"] == 1
        assert r["phenotype"] == "variable"  # het female at one deficiency locus

    def test_canton_hemizygous_still_callable_despite_shared_position(self) -> None:
        # Canton (C>A) is non-palindromic, so a hemizygous male "A" remains a confident
        # deficiency call even though Cosenza shares the rsID; Cosenza (needs G) stays
        # not-called (A outside its {C,G}).
        r = self._assess("XY", {"rs72554665": "A"})
        canton = next(v for v in r["variants"] if v["name"] == "Canton (R459L)")
        cosenza = next(v for v in r["variants"] if v["name"] == "Cosenza (R459P)")
        assert canton["called"] is True and canton["deficiency_alleles"] == 1
        assert cosenza["called"] is False
        assert r["phenotype"] == "deficient" and r["at_risk"] is True

    def test_palindromic_hemizygous_male_is_withheld(self) -> None:
        # Seattle/Lodi (C/G palindromic): a hemizygous male "G" cannot be strand-
        # resolved (a minus-strand report of reference C is identical), so it is
        # withheld — NOT a confident "deficient" — and flagged strand_ambiguous.
        r = self._assess("XY", {"rs137852318": "G"})
        seattle = next(v for v in r["variants"] if v["name"] == "Seattle/Lodi (D282H)")
        assert seattle["called"] is False
        assert seattle["strand_ambiguous"] is True
        assert r["strand_ambiguous_loci"] == ["Seattle/Lodi (D282H)"]
        # No confident deficiency call from the palindromic hemizygote alone.
        assert r["phenotype"] == "indeterminate"
        assert r["at_risk"] is False

    def test_palindromic_homozygous_female_is_withheld(self) -> None:
        r = self._assess("XX", {"rs137852318": "GG"})
        seattle = next(v for v in r["variants"] if v["name"] == "Seattle/Lodi (D282H)")
        assert seattle["called"] is False and seattle["strand_ambiguous"] is True

    def test_palindromic_heterozygous_female_is_variable(self) -> None:
        # The heterozygote {C,G} is strand-invariant → callable → variable.
        r = self._assess("XX", {"rs137852318": "CG"})
        seattle = next(v for v in r["variants"] if v["name"] == "Seattle/Lodi (D282H)")
        assert seattle["called"] is True and seattle["deficiency_alleles"] == 1
        assert seattle["strand_ambiguous"] is False
        assert r["phenotype"] == "variable" and r["at_risk"] is True

    def test_new_variant_female_heterozygous_is_variable(self) -> None:
        r = self._assess("XX", {"rs72554664": "CT"})  # Kaiping het
        assert r["phenotype"] == "variable"
        assert r["at_risk"] is True

    def test_new_variant_female_homozygous_is_deficient(self) -> None:
        r = self._assess("XX", {"rs137852327": "TT"})  # Viangchan homozygous
        assert r["phenotype"] == "deficient"

    def test_compound_het_across_new_loci_is_phase_indeterminate(self) -> None:
        # Canton het + Kaiping het: two different deficiency loci an array cannot phase
        # → variable-or-deficient. Confirms the phase logic generalizes beyond
        # A−/Mediterranean to the expanded panel.
        r = self._assess("XX", {"rs72554665": "CA", "rs72554664": "CT"})
        assert r["phenotype"] == "phase_indeterminate"
        assert r["at_risk"] is True

    def test_han_chinese_frequency_citation_present(self) -> None:
        # He 2020 (PMID 33051526) backs the East/Southeast-Asian deficiency panel.
        assert "33051526" in G6PD_PMID_CITATIONS

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


def _covered_panel(sex: str, **overrides: str) -> dict[str, str]:
    """Every strand-resolvable curated G6PD locus at its reference base.

    A negative G6PD result now requires adequate assay coverage (#2172), so a
    fixture that supplies a single locus is deliberately *not* enough to produce
    a ``normal`` call. Tests whose subject is something else — strand direction,
    Canton/Cosenza cross-calling — start from a fully covered reference panel and
    override the locus under test, which keeps their original intent intact
    without weakening the coverage gate.

    ``overrides`` may also *remove* a locus by passing ``None``.
    """
    copies = 1 if sex == "XY" else 2
    panel = {
        rsid: ref * copies
        for _name, rsid, _cdna, ref, deficiency_allele in G6PD_DEFICIENCY_VARIANTS
        if not _is_palindromic(ref, deficiency_allele)
    }
    for rsid, genotype in overrides.items():
        if genotype is None:
            panel.pop(rsid, None)
        else:
            panel[rsid] = genotype
    return panel


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
        r = self._assess("XY", _covered_panel("XY"))
        assert r["phenotype"] == "normal"
        assert r["at_risk"] is False
        assert r["high_risk_drugs"] == []
        assert r["medication_risk"] == "no_tested_allele_detected"
        assert r["coverage_sufficient"] is True

    def test_sparse_negative_is_indeterminate_not_normal(self) -> None:
        """#2169-style regression for G6PD (#2172): one callable reference locus is
        not a negative panel.

        A single ``rs1050828=C`` call with the rest of the panel absent used to
        report ``normal`` / ``at_risk=False`` / ``high_risk_drugs=[]`` — turning
        "we did not look" into a biochemical all-clear that cleared the oxidative
        drug warning. Absence of a tested allele across 1 of 11 loci does not
        exclude deficiency.
        """
        r = self._assess("XY", {G6PD_A_MINUS_RSID: "C"})
        assert r["phenotype"] == "indeterminate"
        assert r["coverage_sufficient"] is False
        assert r["called_resolvable_records"] == 1
        assert r["resolvable_records"] > 2  # 1 of many is well under a majority
        # The result must not read as a cleared medication risk.
        assert r["medication_risk"] == "undetermined"
        assert "cannot be cleared" in r["detail"]

    def test_broad_panel_without_anchor_alleles_is_still_indeterminate(self) -> None:
        """#2172: a partial panel cannot clear risk, however broad.

        A majority-style rule would have cleared the oxidative-drug warning for
        any 6 of the 11 resolvable loci -- including when A- and Mediterranean,
        the two highest-yield deficiency alleles, were never assessed. Powell et
        al. (PMID:39607789) quantify what limited panels miss. The gate now
        requires the whole resolvable panel, so this fails on completeness.
        """
        panel = _covered_panel("XY")
        for rsid in (G6PD_A_MINUS_RSID, G6PD_MED_RSID):
            panel.pop(rsid, None)
        r = self._assess("XY", panel)
        assert r["called_resolvable_records"] * 2 > r["resolvable_records"]  # a majority...
        assert r["called_resolvable_records"] < r["resolvable_records"]  # ...but incomplete
        assert r["coverage_sufficient"] is False  # ...so still not adequate
        assert r["phenotype"] == "indeterminate"
        assert r["medication_risk"] == "undetermined"

    def test_anchor_alleles_alone_do_not_clear_coverage(self) -> None:
        """#2172: the highest-yield alleles alone are not a negative panel."""
        r = self._assess("XY", {G6PD_A_MINUS_RSID: "C", G6PD_MED_RSID: "G"})
        assert r["coverage_sufficient"] is False
        assert r["medication_risk"] == "undetermined"

    def test_one_absent_locus_blocks_a_negative(self) -> None:
        """#2172: completeness, not a fraction.

        An earlier revision used a majority threshold that no source establishes,
        justified by the claim that requiring every locus would suppress ordinary
        samples. That was wrong: Seattle/Lodi is palindromic, so a
        homozygous-reference call there is strand-ambiguous and the pre-existing
        strand branch already forces indeterminate for any sample that types it.
        Requiring the whole resolvable panel therefore costs nothing real and
        removes the unevidenced threshold.
        """
        panel = _covered_panel("XY")
        panel.pop("rs137852342", None)  # Chinese-5 absent
        r = self._assess("XY", panel)
        assert r["called_resolvable_records"] == r["resolvable_records"] - 1
        assert r["coverage_sufficient"] is False
        assert r["medication_risk"] == "undetermined"

    def test_negative_detail_reports_the_number_actually_called(self) -> None:
        """#2172: the detail must not overstate assay coverage.

        A covered negative states how many curated variants were *tested*; that
        number is the called count, never the panel size.
        """
        r = self._assess("XY", _covered_panel("XY"))
        assert f"{r['called_resolvable_records']} curated" in r["detail"]

    def test_sparse_positive_still_reports_deficiency(self) -> None:
        """#2172 discriminating control: the coverage gate is asymmetric.

        A deficiency allele seen on a sparse panel is still a real observation and
        must stay actionable — otherwise the gate would suppress true positives.
        """
        r = self._assess("XY", {G6PD_A_MINUS_RSID: "T"})
        assert r["phenotype"] == "deficient"
        assert r["at_risk"] is True
        assert r["medication_risk"] == "elevated"
        assert "rasburicase" in r["high_risk_drugs"]

    def test_covered_negative_avoids_biochemical_normal_wording(self) -> None:
        """#2172: an adequately covered negative is 'no deficiency allele detected',
        not the biochemical claim 'G6PD normal' — enzyme activity establishes that."""
        r = self._assess("XY", _covered_panel("XY"))
        assert "G6PD normal" not in r["detail"]
        assert "no deficiency allele detected" in r["detail"]
        assert "enzyme activity" in r["detail"]

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
        r = self._assess("XX", _covered_panel("XX"))
        assert r["phenotype"] == "normal"
        assert r["at_risk"] is False
        assert r["high_risk_drugs"] == []

    def test_unknown_sex_with_deficiency_surfaces_drug_warning(self) -> None:
        r = self._assess("unknown", {G6PD_A_MINUS_RSID: "CT"})
        assert r["phenotype"] == "indeterminate"
        assert r["at_risk"] is True  # deficiency allele present → still warn
        assert r["high_risk_drugs"]

    def test_no_variant_called_is_indeterminate(self) -> None:
        """Nothing callable → indeterminate, and the drug warning is NOT cleared.

        This assertion previously required ``at_risk is False``. That is the same
        defect class as #2172 one step further along: with zero callable loci the
        module cleared the oxidative-drug list entirely. An empty read cannot
        clear risk, so ``at_risk`` is now True while ``medication_risk`` stays
        ``undetermined`` — conservative on the drug list without asserting a
        deficiency signal the data does not show.
        """
        r = self._assess("XY", {G6PD_A_MINUS_RSID: "--", G6PD_MED_RSID: "--"})
        assert r["any_called"] is False
        assert r["phenotype"] == "indeterminate"
        assert r["at_risk"] is True
        assert r["medication_risk"] == "undetermined"
        assert r["high_risk_drugs"]  # withheld conservatively, not cleared

    def test_a_plus_nondeficient_flagged_as_context(self) -> None:
        # 376G present (rs1050829 = C) with A- reference → A+ non-deficient allele.
        r = self._assess("XX", _covered_panel("XX", **{G6PD_376_RSID: "CC"}))
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
        # A single deficiency call stays actionable at any coverage (#2172): absence
        # of evidence is not evidence of absence, but presence is presence.
        r = self._assess("XY", {rsid: deff})
        assert r["phenotype"] == "deficient", name
        assert r["at_risk"] is True, name
        # The forward gene-NORMAL base → normal, no risk. A flipped REF/DEF would
        # invert both assertions, so each variant's strand is locked here. The rest
        # of the panel is supplied at reference so the negative clears the #2172
        # coverage gate — this test is about strand, not about coverage.
        r = self._assess("XY", _covered_panel("XY", **{rsid: ref}))
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

    @pytest.mark.parametrize(("sex", "genotype"), [("XY", "C"), ("XX", "CC")])
    def test_canton_reference_does_not_fabricate_cosenza_warning(
        self, sex: str, genotype: str
    ) -> None:
        # rs72554665 is GSA-typed as a Canton [A/C] probe. A hemizygous "C" is the
        # shared reference base observed on that probe; it should not be reinterpreted
        # as an observed Cosenza C/G palindromic ambiguity because Cosenza's G allele is
        # not interrogated by the GSA probe.
        r = self._assess(sex, _covered_panel(sex, **{"rs72554665": genotype}))
        canton = next(v for v in r["variants"] if v["name"] == "Canton (R459L)")
        cosenza = next(v for v in r["variants"] if v["name"] == "Cosenza (R459P)")
        assert canton["called"] is True and canton["deficiency_alleles"] == 0
        assert canton["gsa_v3_typed"] is True
        assert cosenza["called"] is False
        assert cosenza["gsa_v3_typed"] is False
        assert cosenza["strand_ambiguous"] is False
        assert r["strand_ambiguous_loci"] == []
        assert r["phenotype"] == "normal"
        assert r["at_risk"] is False
        assert r["high_risk_drugs"] == []

    def test_palindromic_hemizygous_male_is_withheld_but_warns(self) -> None:
        # Seattle/Lodi (C/G palindromic): a hemizygous male "G" cannot be strand-
        # resolved (a minus-strand report of reference C is identical), so it is
        # withheld — NOT a confident "deficient" — and flagged strand_ambiguous.
        # Because the observation cannot clear deficiency risk, high-risk oxidative
        # drug context is still surfaced with an enzyme-assay confirmation prompt.
        r = self._assess("XY", {"rs137852318": "G", G6PD_A_MINUS_RSID: "C"})
        seattle = next(v for v in r["variants"] if v["name"] == "Seattle/Lodi (D282H)")
        assert seattle["called"] is False
        assert seattle["strand_ambiguous"] is True
        assert r["strand_ambiguous_loci"] == ["Seattle/Lodi (D282H)"]
        assert r["any_called"] is True
        # No confident deficiency call from the palindromic hemizygote alone.
        assert r["phenotype"] == "indeterminate"
        assert r["at_risk"] is True
        # ...and the descriptive field must not upgrade that withheld call to a
        # confident deficiency. The locus is unresolvable, not positive (#2205
        # review): "elevated" would assert what the same response declines to say.
        assert r["medication_risk"] == "undetermined"
        assert "rasburicase" in r["high_risk_drugs"]
        assert "quantitative enzyme-activity assay" in r["detail"]

    def test_palindromic_homozygous_female_is_withheld(self) -> None:
        r = self._assess("XX", {"rs137852318": "GG"})
        seattle = next(v for v in r["variants"] if v["name"] == "Seattle/Lodi (D282H)")
        assert seattle["called"] is False and seattle["strand_ambiguous"] is True
        assert r["phenotype"] == "indeterminate"
        assert r["at_risk"] is True
        assert r["medication_risk"] == "undetermined"
        assert r["high_risk_drugs"]

    def test_confirmed_deficiency_is_the_only_elevated_state(self) -> None:
        """Discriminating control for the two assertions above.

        If `medication_risk` simply never said "elevated" they would pass
        vacuously, so pin that a real deficiency call still does.
        """
        r = self._assess("XY", _covered_panel("XY", **{G6PD_A_MINUS_RSID: "T"}))
        assert r["phenotype"] == "deficient"
        assert r["medication_risk"] == "elevated"
        assert r["at_risk"] is True

    def test_unknown_sex_full_panel_keeps_the_legacy_warning(self) -> None:
        """#2205 review: a withheld phenotype must not clear the legacy fields.

        With sex unresolved, a fully covered *reference* panel yields
        `phenotype="indeterminate"`. `medication_risk` said `undetermined` while
        `at_risk`/`high_risk_drugs` said cleared -- the same contradiction #2172
        is about, one layer up. `at_risk` is now derived from `medication_risk`,
        so the pair cannot disagree.
        """
        r = self._assess("unknown", _covered_panel("XY"))
        assert r["phenotype"] == "indeterminate"
        assert r["coverage_sufficient"] is True
        assert r["medication_risk"] == "undetermined"
        assert r["at_risk"] is True
        assert r["high_risk_drugs"]

    def test_palindromic_cosenza_hemizygous_male_is_withheld_but_warns(self) -> None:
        # Cosenza is the other C/G palindromic deficiency allele. It shares rs72554665
        # with non-palindromic Canton, so this locks both the ambiguity warning and the
        # no-cross-call behavior at the multiallelic position.
        r = self._assess("XY", {"rs72554665": "G"})
        canton = next(v for v in r["variants"] if v["name"] == "Canton (R459L)")
        cosenza = next(v for v in r["variants"] if v["name"] == "Cosenza (R459P)")
        assert canton["called"] is False and canton["strand_ambiguous"] is False
        assert cosenza["called"] is False and cosenza["strand_ambiguous"] is True
        assert r["strand_ambiguous_loci"] == ["Cosenza (R459P)"]
        assert r["phenotype"] == "indeterminate"
        assert r["at_risk"] is True
        assert r["medication_risk"] == "undetermined"
        assert r["high_risk_drugs"]

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


# ── GSA-24v3 array typeability (issue #321) ───────────────────────────────


class TestGsaArrayTypeability:
    """Every CPIC G6PD deficiency variant is on the GSA-24v3 backbone (#321).

    The repo previously could only assert that A-/Mediterranean were standard GSA
    content and the rest "varied by chip". The public Illumina GSA-24v3 manifest was
    checked (by rsID and GRCh37 position) and bundled as derived membership facts in
    backend/data/array_manifests/gsa_24v3_typeability.json; all G6PD deficiency loci
    are present by position, but gsa_v3_typed is an ALLELE-level flag (#842): Cosenza
    (C>G) shares Canton's rs72554665 whose probe resolves only [A/C], so Cosenza is
    gsa_v3_typed=False while every other variant is True.
    """

    def _artifact_json(self, name: str) -> dict:
        import json
        from pathlib import Path

        path = (
            Path(__file__).resolve().parent.parent.parent
            / "backend"
            / "data"
            / "array_manifests"
            / name
        )
        return json.loads(path.read_text())

    def _artifact(self) -> dict:
        return self._artifact_json("gsa_24v3_typeability.json")

    def test_artifact_has_provenance(self) -> None:
        prov = self._artifact()["_provenance"]
        for key in (
            "source",
            "url",
            "genome_build",
            "accessed",
            "derivation",
            "count_definition",
            "manifest_audit",
            "manifest_zip_sha256",
            "manifest_csv_sha256",
        ):
            assert prov.get(key), f"provenance missing {key}"
        assert prov["genome_build"] == "GRCh37"
        assert "manifest_probe_count" not in prov

    def test_manifest_audit_locks_section_aware_loci_count(self) -> None:
        prov = self._artifact()["_provenance"]
        assert prov["manifest_audit"] == (
            "backend/data/array_manifests/gsa_24v3_manifest_audit.json"
        )
        manifest_audit = self._artifact_json("gsa_24v3_manifest_audit.json")
        source = manifest_audit["source"]
        heading = manifest_audit["heading"]
        sections = manifest_audit["sections"]

        assert source["url"] == prov["url"]
        assert source["archive_sha256"] == prov["manifest_zip_sha256"]
        assert source["csv_member"] == prov["manifest_csv_member"]
        assert source["csv_sha256"] == prov["manifest_csv_sha256"]
        assert heading["descriptor_file_name"] == prov["manifest_descriptor"]
        assert heading["date_manufactured"] == prov["manifest_date_manufactured"]
        assert heading["loci_count"] == sections["assay_rows"]
        assert sections["assay_rows"] == prov["manifest_loci_count"] == 654027
        assert sections["control_records"] == prov["manifest_control_record_count"] == 23
        assert sections["non_assay_physical_lines"] == prov["manifest_non_assay_line_count"] == 32
        assert sections["physical_lines"] == prov["manifest_csv_physical_line_count"] == 654059
        assert sections["physical_lines"] == (
            sections["assay_rows"] + sections["non_assay_physical_lines"]
        )
        assert sections["heading_and_assay_header_lines"] == 8
        assert sections["control_marker_lines"] == 1
        assert sections["non_assay_physical_lines"] == (
            sections["heading_and_assay_header_lines"]
            + sections["control_marker_lines"]
            + sections["control_records"]
        )

    def test_all_g6pd_deficiency_variants_are_gsa_typed(self) -> None:
        typed = set(self._artifact()["typed"])
        for _name, rsid, *_ in G6PD_DEFICIENCY_VARIANTS:
            assert rsid in typed, f"{rsid} not recorded as GSA-24v3 typed"

    def test_locus_calls_surface_gsa_v3_typed(self) -> None:
        # Each locus surfaces gsa_v3_typed independent of genotype (here a single
        # reference A- call; the rest are no-calls). Typeability is allele-level (#842):
        # every variant is typed EXCEPT Cosenza, whose C>G allele is outside the
        # rs72554665 manifest probe's [A/C] (Canton C>A) set.
        engine = _make_sample({G6PD_A_MINUS_RSID: "C"})
        with patch("backend.analysis.g6pd.infer_biological_sex", return_value="XY"):
            result = assess_g6pd(engine)
        # Guard against a vacuous pass: every curated variant must be present.
        assert len(result["variants"]) == len(G6PD_DEFICIENCY_VARIANTS)
        for v in result["variants"]:
            expected = v["name"] != "Cosenza (R459P)"
            assert v["gsa_v3_typed"] is expected, f"{v['name']} gsa_v3_typed should be {expected}"

    def test_cosenza_not_gsa_typed_despite_sharing_cantons_rsid(self) -> None:
        # #842: typeability is an ALLELE-level fact, not rsID membership. The GSA-24v3
        # probe at the multiallelic rs72554665 resolves [A/C] (Canton C>A), so Canton is
        # gsa_v3_typed but Cosenza (C>G) is NOT — its G allele is not on that probe, even
        # though both deficiency rows share the rsID. A Cosenza no-call is therefore not a
        # confident reference/absent call. (gsa_v3_typed is genotype-independent; seed a
        # single harmless reference call so the sample has at least one variant.)
        engine = _make_sample({G6PD_A_MINUS_RSID: "C"})
        with patch("backend.analysis.g6pd.infer_biological_sex", return_value="XY"):
            result = assess_g6pd(engine)
        canton = next(v for v in result["variants"] if v["name"] == "Canton (R459L)")
        cosenza = next(v for v in result["variants"] if v["name"] == "Cosenza (R459P)")
        assert canton["gsa_v3_typed"] is True
        assert cosenza["gsa_v3_typed"] is False
        # Seattle/Lodi is also C/G palindromic, but its probe genuinely resolves [C/G],
        # so it stays typed — the fix keys on the allele set, not palindrome-ness.
        seattle = next(v for v in result["variants"] if v["name"] == "Seattle/Lodi (D282H)")
        assert seattle["gsa_v3_typed"] is True

    def test_non_european_lct_absentees_recorded(self) -> None:
        # The two LCT variants absent from the GSA backbone are recorded as not_typed
        # (consumed by the #291 lactase-persistence work).
        not_typed = set(self._artifact()["not_typed"])
        assert {"rs145946881", "rs869051967"} <= not_typed


class TestRecordedBiologicalSex:
    """#475: an authoritative recorded individuals.biological_sex resolves the
    X-linked G6PD phenotype that array inference alone would withhold."""

    def test_recorded_sex_overrides_unknown_inference(self) -> None:
        engine = _make_sample({G6PD_A_MINUS_RSID: "T"})  # one A- deficiency allele
        with (
            patch("backend.analysis.g6pd.infer_biological_sex", return_value=None),
            patch("backend.analysis.g6pd.get_recorded_biological_sex", return_value="XY"),
        ):
            r = assess_g6pd(engine, reference_engine=engine, sample_id=1)
        assert r["inferred_sex"] == "XY"
        assert r["sex_source"] == "recorded"
        assert r["phenotype"] == "deficient"  # XY hemizygote + 1 deficiency allele

    def test_recorded_sex_overrides_conflicting_inference(self) -> None:
        # True precedence case: a confident inference (XX) DISAGREES with the recorded
        # value (XY). The user-set recorded sex is authoritative, so the phenotype must
        # follow XY (hemizygote → deficient), not the XX heterozygote reading that
        # inference alone would have produced (#475; resolve_biological_sex precedence).
        engine = _make_sample({G6PD_A_MINUS_RSID: "T"})  # one A- deficiency allele
        with (
            patch("backend.analysis.g6pd.infer_biological_sex", return_value="XX"),
            patch("backend.analysis.g6pd.get_recorded_biological_sex", return_value="XY"),
        ):
            r = assess_g6pd(engine, reference_engine=engine, sample_id=1)
        assert r["inferred_sex"] == "XY"
        assert r["sex_source"] == "recorded"
        assert r["phenotype"] == "deficient"  # follows recorded XY, not inferred XX

    def test_inferred_sex_used_when_no_recorded(self) -> None:
        engine = _make_sample({G6PD_A_MINUS_RSID: "T"})
        with (
            patch("backend.analysis.g6pd.infer_biological_sex", return_value="XY"),
            patch("backend.analysis.g6pd.get_recorded_biological_sex", return_value=None),
        ):
            r = assess_g6pd(engine, reference_engine=engine, sample_id=1)
        assert r["inferred_sex"] == "XY"
        assert r["sex_source"] == "inferred"

    def test_no_reference_engine_falls_back_to_inference(self) -> None:
        engine = _make_sample({G6PD_A_MINUS_RSID: "C"})
        with patch("backend.analysis.g6pd.infer_biological_sex", return_value="XY"):
            r = assess_g6pd(engine)  # no reference_engine / sample_id threaded
        assert r["sex_source"] == "inferred"
        assert r["inferred_sex"] == "XY"

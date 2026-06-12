"""Tests for the gout / serum-urate module (ABCG2 Q141K + SLC2A9 rs13129697).

ABCG2 Q141K (rs2231142) risk allele is T on the GRCh37 plus strand (23andMe
reports plus -> homozygous risk = "TT"); SLC2A9 rs13129697 urate-raising allele
is T. The honesty guardrails under test: ancestry-appropriate OR band (larger in
East Asian ancestry); strand-harmonized calls; common risk alleles write
clinvar_significance=NULL; and — critically — NO dietary/purine prescriptions
appear anywhere in the findings or disclaimer (denylist), every finding framed as
"risk modifier, not a diagnosis".
"""

from __future__ import annotations

import json

import pytest
import sqlalchemy as sa

from backend.analysis.gout import assess_gout, load_gout_panel, store_gout_findings
from backend.db.tables import findings, raw_variants

# Phrases that would turn an educational risk readout into an unvalidated dietary
# prescription — none may appear in any finding text, caveat, or the disclaimer.
_PRESCRIPTION_DENYLIST = (
    "avoid purine",
    "low-purine",
    "purine",
    "diet",
    "alcohol",
    "lose weight",
    "cherry",
)


@pytest.fixture()
def panel():
    return load_gout_panel()


def _seed(engine: sa.Engine, rows: list[dict]) -> None:
    if rows:
        with engine.begin() as conn:
            conn.execute(sa.insert(raw_variants), rows)


def _seed_ancestry(engine: sa.Engine, top_population: str, fraction: float = 0.85) -> None:
    detail = {"top_population": top_population, "admixture_fractions": {top_population: fraction}}
    with engine.begin() as conn:
        conn.execute(
            sa.insert(findings),
            [
                {
                    "module": "ancestry",
                    "category": "nnls_admixture",
                    "evidence_level": 1,
                    "finding_text": f"Ancestry: {top_population}",
                    "detail_json": json.dumps(detail),
                }
            ],
        )


def _abcg2(genotype: str) -> dict:  # risk T / ref G (plus strand)
    return {"rsid": "rs2231142", "chrom": "4", "pos": 89052323, "genotype": genotype}


def _slc2a9(genotype: str) -> dict:  # urate-raising T / urate-lowering G
    return {"rsid": "rs13129697", "chrom": "4", "pos": 9926967, "genotype": genotype}


class TestABCG2:
    def test_homozygous_european_band(self, panel, sample_engine: sa.Engine) -> None:
        _seed_ancestry(sample_engine, "EUR")
        _seed(sample_engine, [_abcg2("TT"), _slc2a9("GG")])
        a = assess_gout(panel, sample_engine)
        abcg2 = [c for c in a.calls if c.gene_symbol == "ABCG2"]
        assert len(abcg2) == 1
        assert "homozygous" in abcg2[0].risk_classification.lower()
        assert "2.80" in abcg2[0].finding_text  # European band
        assert abcg2[0].evidence_stars == 2

    def test_homozygous_east_asian_larger_band(self, panel, sample_engine: sa.Engine) -> None:
        _seed_ancestry(sample_engine, "EAS")
        _seed(sample_engine, [_abcg2("TT"), _slc2a9("GG")])
        a = assess_gout(panel, sample_engine)
        abcg2 = [c for c in a.calls if c.gene_symbol == "ABCG2"][0]
        assert "4.56" in abcg2.finding_text  # larger East Asian OR band

    def test_minus_strand_equivalent(self, panel, sample_engine: sa.Engine) -> None:
        # "AA" is the reverse-strand complement of plus-strand homozygous-risk "TT".
        _seed_ancestry(sample_engine, "EUR")
        _seed(sample_engine, [_abcg2("AA"), _slc2a9("GG")])
        a = assess_gout(panel, sample_engine)
        abcg2 = [c for c in a.calls if c.gene_symbol == "ABCG2"]
        assert len(abcg2) == 1
        assert "homozygous" in abcg2[0].risk_classification.lower()

    def test_heterozygous(self, panel, sample_engine: sa.Engine) -> None:
        _seed_ancestry(sample_engine, "EUR")
        _seed(sample_engine, [_abcg2("GT"), _slc2a9("GG")])
        a = assess_gout(panel, sample_engine)
        abcg2 = [c for c in a.calls if c.gene_symbol == "ABCG2"]
        assert len(abcg2) == 1
        assert "heterozygous" in abcg2[0].risk_classification.lower()

    def test_ref_ref_no_abcg2_finding(self, panel, sample_engine: sa.Engine) -> None:
        _seed_ancestry(sample_engine, "EUR")
        _seed(sample_engine, [_abcg2("GG"), _slc2a9("GG")])
        a = assess_gout(panel, sample_engine)
        assert a.calls == []


class TestSLC2A9:
    def test_urate_effect_statement(self, panel, sample_engine: sa.Engine) -> None:
        _seed_ancestry(sample_engine, "EUR")
        _seed(sample_engine, [_abcg2("GG"), _slc2a9("TT")])
        a = assess_gout(panel, sample_engine)
        slc = [c for c in a.calls if c.gene_symbol == "SLC2A9"]
        assert len(slc) == 1
        assert "urate" in slc[0].finding_text.lower()
        assert slc[0].evidence_stars == 1

    def test_homozygous_is_not_classified_heterozygous(
        self, panel, sample_engine: sa.Engine
    ) -> None:
        # TT fires the homozygous model only — the het model (dosage 1) must not.
        _seed_ancestry(sample_engine, "EUR")
        _seed(sample_engine, [_abcg2("GG"), _slc2a9("TT")])
        a = assess_gout(panel, sample_engine)
        slc = [c for c in a.calls if c.gene_symbol == "SLC2A9"]
        assert len(slc) == 1
        assert "heterozygous" not in slc[0].risk_classification.lower()
        assert slc[0].zygosity == "hom_alt"

    def test_heterozygous_one_copy_statement(self, panel, sample_engine: sa.Engine) -> None:
        # GT carriers carry one urate-raising T allele; SLC2A9 acts per-allele
        # (additively), so a modest one-copy finding must surface (issue #119).
        _seed_ancestry(sample_engine, "EUR")
        _seed(sample_engine, [_abcg2("GG"), _slc2a9("GT")])
        a = assess_gout(panel, sample_engine)
        slc = [c for c in a.calls if c.gene_symbol == "SLC2A9"]
        assert len(slc) == 1
        assert "heterozygous" in slc[0].risk_classification.lower()
        assert slc[0].zygosity == "het"
        assert slc[0].evidence_stars == 1
        assert "one copy" in slc[0].finding_text.lower()
        assert "urate" in slc[0].finding_text.lower()

    def test_heterozygous_tg_orientation(self, panel, sample_engine: sa.Engine) -> None:
        # Genotype-string order must not matter: TG is the same one-copy call as GT.
        _seed_ancestry(sample_engine, "EUR")
        _seed(sample_engine, [_abcg2("GG"), _slc2a9("TG")])
        a = assess_gout(panel, sample_engine)
        slc = [c for c in a.calls if c.gene_symbol == "SLC2A9"]
        assert len(slc) == 1
        assert slc[0].zygosity == "het"

    def test_reference_homozygous_no_slc2a9_finding(self, panel, sample_engine: sa.Engine) -> None:
        # GG carries two urate-lowering alleles → no SLC2A9 finding (no false het).
        _seed_ancestry(sample_engine, "EUR")
        _seed(sample_engine, [_abcg2("GG"), _slc2a9("GG")])
        a = assess_gout(panel, sample_engine)
        slc = [c for c in a.calls if c.gene_symbol == "SLC2A9"]
        assert slc == []

    def test_off_chip_indeterminate(self, panel, sample_engine: sa.Engine) -> None:
        _seed_ancestry(sample_engine, "EUR")
        _seed(sample_engine, [_abcg2("GT")])  # SLC2A9 absent
        a = assess_gout(panel, sample_engine)
        assert "rs13129697" in a.indeterminate_loci


class TestCollectAll:
    def test_both_findings(self, panel, sample_engine: sa.Engine) -> None:
        # collect_all -> ABCG2 het AND SLC2A9 urate statement both surface.
        _seed_ancestry(sample_engine, "EUR")
        _seed(sample_engine, [_abcg2("GT"), _slc2a9("TT")])
        a = assess_gout(panel, sample_engine)
        genes = {c.gene_symbol for c in a.calls}
        assert genes == {"ABCG2", "SLC2A9"}


class TestNoDietPrescriptionGuardrail:
    def test_findings_and_disclaimer_have_no_diet_prescription(
        self, panel, sample_engine: sa.Engine
    ) -> None:
        from backend.disclaimers import GOUT_DISCLAIMER_TEXT

        _seed_ancestry(sample_engine, "EUR")
        _seed(sample_engine, [_abcg2("TT"), _slc2a9("TT")])
        a = assess_gout(panel, sample_engine)
        assert len(a.calls) == 2
        corpus = GOUT_DISCLAIMER_TEXT.lower()
        for call in a.calls:
            corpus += " " + call.finding_text.lower()
            corpus += " " + " ".join(call.detail["caveats"]).lower()
        for banned in _PRESCRIPTION_DENYLIST:
            assert banned not in corpus, f"diet-prescription phrase leaked: {banned!r}"
        # And the risk-modifier framing must be explicit.
        assert "not a diagnosis" in corpus


class TestStorageGuardrails:
    def test_clinvar_significance_null(self, panel, sample_engine: sa.Engine) -> None:
        _seed_ancestry(sample_engine, "EUR")
        _seed(sample_engine, [_abcg2("TT"), _slc2a9("GG")])
        a = assess_gout(panel, sample_engine)
        assert store_gout_findings(a, sample_engine) == 1
        with sample_engine.connect() as conn:
            row = conn.execute(sa.select(findings).where(findings.c.module == "gout")).fetchone()
        assert row.clinvar_significance is None
        assert row.gene_symbol == "ABCG2"
        assert row.evidence_level <= 3

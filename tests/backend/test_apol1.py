"""Tests for the APOL1 kidney-risk module (G1/G2 + N264K, ancestry-contextualized, recessive).

APOL1 risk is recessive (two risk alleles across G1/G2), interpreted with
African-ancestry validation context, and modified by N264K (rs73885316). The
honesty guardrails under test: directly observed high-risk genotypes are reported
with an ancestry caveat when global ancestry is not predominantly AFR; partial
indeterminate calls are suppressed outside that ancestry context; the G2 indel
being off-chip yields a partial genotype, never a false low-risk; an unassessed
N264K caveats a high-risk call rather than overstating it; common risk alleles
write clinvar_significance=NULL.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import sqlalchemy as sa

from backend.analysis.apol1 import assess_apol1, load_apol1_panel, store_apol1_findings
from backend.analysis.risk_genotype import PROBE_TYPED, read_genotypes
from backend.db.tables import findings, raw_variants


@pytest.fixture()
def panel():
    return load_apol1_panel()


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


def _g1(genotype: str) -> dict:  # risk G / ref A
    return {"rsid": "rs73885319", "chrom": "22", "pos": 36661906, "genotype": genotype}


def _g1b(genotype: str) -> dict:  # rs60910145, risk G / ref T
    return {"rsid": "rs60910145", "chrom": "22", "pos": 36662034, "genotype": genotype}


def _g2(genotype: str) -> dict:  # indel risk D / ref I
    return {"rsid": "rs71785313", "chrom": "22", "pos": 36662042, "genotype": genotype}


def _n264k(genotype: str) -> dict:  # modifier-present A / ref C
    return {"rsid": "rs73885316", "chrom": "22", "pos": 36661674, "genotype": genotype}


class TestHighRiskAFR:
    def test_g1_homozygous_high_risk(self, panel, sample_engine: sa.Engine) -> None:
        _seed_ancestry(sample_engine, "AFR")
        _seed(sample_engine, [_g1("GG"), _g2("II"), _n264k("CC")])
        a = assess_apol1(panel, sample_engine)
        assert a.ancestry_suppressed is False
        assert len(a.calls) == 1
        call = a.calls[0]
        assert "high-risk" in call.risk_classification.lower()
        assert "10.5" in call.finding_text and "7.3" in call.finding_text
        caveats = " ".join(call.detail["caveats"]).lower()
        assert "recessive" in caveats  # recessive note

    def test_g2_homozygous_high_risk(self, panel, sample_engine: sa.Engine) -> None:
        _seed_ancestry(sample_engine, "AFR")
        _seed(sample_engine, [_g1("AA"), _g2("DD"), _n264k("CC")])
        a = assess_apol1(panel, sample_engine)
        assert len(a.calls) == 1
        assert "high-risk" in a.calls[0].risk_classification.lower()

    def test_g1_g2_compound_high_risk(self, panel, sample_engine: sa.Engine) -> None:
        _seed_ancestry(sample_engine, "AFR")
        _seed(sample_engine, [_g1("AG"), _g2("DI"), _n264k("CC")])
        a = assess_apol1(panel, sample_engine)
        assert len(a.calls) == 1
        assert "high-risk" in a.calls[0].risk_classification.lower()

    def test_single_g1_allele_low_risk_no_finding(self, panel, sample_engine: sa.Engine) -> None:
        # G2 confirmed reference -> genuinely low-risk (one allele, recessive).
        _seed_ancestry(sample_engine, "AFR")
        _seed(sample_engine, [_g1("AG"), _g2("II"), _n264k("CC")])
        a = assess_apol1(panel, sample_engine)
        assert a.calls == []  # one risk allele is not high-risk (recessive)

    def test_single_g1_allele_g2_off_chip_is_indeterminate(
        self, panel, sample_engine: sa.Engine
    ) -> None:
        # One G1 allele typed, the G2 6-bp deletion off-chip -> the recessive
        # status cannot be determined; disclose a partial genotype, never silent.
        _seed_ancestry(sample_engine, "AFR")
        _seed(sample_engine, [_g1("AG")])  # G2 (rs71785313) absent
        a = assess_apol1(panel, sample_engine)
        assert len(a.calls) == 1
        call = a.calls[0]
        assert "indeterminate" in call.risk_classification.lower()
        assert call.detail["indeterminate"] is True
        assert "rs71785313" in call.detail["untyped_loci"]
        assert "not a low-risk result" in call.finding_text.lower()

    def test_indeterminate_suppressed_for_non_african_ancestry(
        self, panel, sample_engine: sa.Engine
    ) -> None:
        _seed_ancestry(sample_engine, "EUR")
        _seed(sample_engine, [_g1("AG")])  # G2 off-chip, but EUR -> not actionable
        a = assess_apol1(panel, sample_engine)
        assert a.calls == []
        assert a.ancestry_suppressed is True


class TestAncestryGate:
    def test_eur_observed_high_risk_caveated(self, panel, sample_engine: sa.Engine) -> None:
        _seed_ancestry(sample_engine, "EUR")
        _seed(sample_engine, [_g1("AA"), _g2("DD"), _n264k("CC")])
        a = assess_apol1(panel, sample_engine)
        assert len(a.calls) == 1
        assert "high-risk" in a.calls[0].risk_classification.lower()
        assert a.ancestry_suppressed is False
        assert (
            "Top global ancestry does not prove local ancestry at APOL1" in a.calls[0].finding_text
        )
        assert any(
            "Top global ancestry does not prove local ancestry at APOL1" in caveat
            for caveat in a.calls[0].detail["caveats"]
        )

    def test_afr_below_half_observed_high_risk_caveated(
        self, panel, sample_engine: sa.Engine
    ) -> None:
        _seed_ancestry(sample_engine, "AFR", fraction=0.49)
        _seed(sample_engine, [_g1("AA"), _g2("DD"), _n264k("CC")])
        a = assess_apol1(panel, sample_engine)
        assert len(a.calls) == 1
        assert a.ancestry_suppressed is False
        assert "extra validation caution" in a.calls[0].finding_text
        assert any("extra validation caution" in caveat for caveat in a.calls[0].detail["caveats"])

    def test_no_ancestry_observed_high_risk_caveated(
        self, panel, sample_engine: sa.Engine
    ) -> None:
        # No ancestry finding seeded -> inferred ancestry unknown, but the observed
        # two-risk-allele genotype is still reported with a validation caveat.
        _seed(sample_engine, [_g1("AA"), _g2("DD"), _n264k("CC")])
        a = assess_apol1(panel, sample_engine)
        assert len(a.calls) == 1
        assert a.ancestry_suppressed is False
        assert "extra validation caution" in a.calls[0].finding_text
        assert any("extra validation caution" in caveat for caveat in a.calls[0].detail["caveats"])


class TestN264KModifier:
    def test_n264k_present_attenuates(self, panel, sample_engine: sa.Engine) -> None:
        _seed_ancestry(sample_engine, "AFR")
        _seed(sample_engine, [_g1("AA"), _g2("DD"), _n264k("CA")])  # one Lys (A) copy
        a = assess_apol1(panel, sample_engine)
        assert len(a.calls) == 1
        call = a.calls[0]
        assert "attenuat" in call.risk_classification.lower()
        assert call.evidence_stars == 1
        assert "38036523" in call.pmids
        assert all(pmid.isdigit() for pmid in call.pmids)

    def test_g1_hom_g2_and_n264k_off_chip_both_caveats(
        self, panel, sample_engine: sa.Engine
    ) -> None:
        # G1/G1 fires high-risk, but G2 (indel) and N264K are both off-chip.
        _seed_ancestry(sample_engine, "AFR")
        _seed(sample_engine, [_g1("GG")])  # G2 and N264K absent
        a = assess_apol1(panel, sample_engine)
        assert len(a.calls) == 1
        call = a.calls[0]
        assert "high-risk" in call.risk_classification.lower()  # still high-risk, not low
        caveats = " ".join(call.detail["caveats"]).lower()
        assert "partial genotype" in caveats  # G2 not typed
        assert "n264k" in caveats and "overstated" in caveats  # modifier not assessed
        assert "rs71785313" in a.indeterminate_loci


class TestStorageAndGuardrails:
    def test_clinvar_significance_null(self, panel, sample_engine: sa.Engine) -> None:
        _seed_ancestry(sample_engine, "AFR")
        _seed(sample_engine, [_g1("GG"), _g2("II"), _n264k("CC")])
        a = assess_apol1(panel, sample_engine)
        assert store_apol1_findings(a, sample_engine) == 1
        with sample_engine.connect() as conn:
            row = conn.execute(sa.select(findings).where(findings.c.module == "apol1")).fetchone()
        assert row.clinvar_significance is None
        assert row.gene_symbol == "APOL1"
        assert row.evidence_level == 3  # high-risk recessive model is 3 stars

    def test_observed_non_afr_high_risk_stores_caveated_finding(
        self, panel, sample_engine: sa.Engine
    ) -> None:
        _seed_ancestry(sample_engine, "EUR")
        _seed(sample_engine, [_g1("AA"), _g2("DD"), _n264k("CC")])
        a = assess_apol1(panel, sample_engine)
        assert store_apol1_findings(a, sample_engine) == 1
        with sample_engine.connect() as conn:
            row = conn.execute(sa.select(findings).where(findings.c.module == "apol1")).fetchone()
        assert "Top global ancestry does not prove local ancestry at APOL1" in row.finding_text
        detail = json.loads(row.detail_json)
        assert any(
            "Top global ancestry does not prove local ancestry at APOL1" in caveat
            for caveat in detail["caveats"]
        )

    def test_indeterminate_non_afr_stores_nothing(self, panel, sample_engine: sa.Engine) -> None:
        _seed_ancestry(sample_engine, "EUR")
        _seed(sample_engine, [_g1("AG")])
        a = assess_apol1(panel, sample_engine)
        assert a.calls == []
        assert a.ancestry_suppressed is True
        assert store_apol1_findings(a, sample_engine) == 0


class TestG1HaplotypeConcordance:
    """G1 is a two-SNP cis haplotype (rs73885319 tag + rs60910145, near-absolute
    LD). When the partner is typed and corroborates LESS risk than the tag, the
    G1 risk is unconfirmed and must NOT be summed into a high-risk call — it is an
    indeterminate genotyping-concordance (QC) flag instead (#160). The veto is
    one-directional: a partner showing MORE dosage than the tag is the known
    rs60910145/G2-deletion amplification artifact and must not suppress a real
    call (David et al. 2018, PMID 30596185).
    """

    def test_discordant_g1_overcall_becomes_indeterminate_afr(
        self, panel, sample_engine: sa.Engine
    ) -> None:
        # The reported bug: tag GG (2) but partner TT (0), G2 reference. Pre-fix
        # this summed to a two-risk-allele high-risk call; now the G1 dosage is
        # vetoed to indeterminate and a QC disclosure is shown (never high-risk).
        _seed_ancestry(sample_engine, "AFR")
        _seed(sample_engine, [_g1("GG"), _g1b("TT"), _g2("II"), _n264k("CC")])
        a = assess_apol1(panel, sample_engine)
        assert a.discordant_loci == ["rs73885319"]
        assert a.dosages["rs73885319"] is None  # tag vetoed
        assert len(a.calls) == 1
        call = a.calls[0]
        assert "high-risk" not in call.risk_classification.lower()
        assert "indeterminate" in call.risk_classification.lower()
        assert call.detail["indeterminate"] is True
        assert "concordance" in call.finding_text.lower()
        assert "not a low-risk result" in call.finding_text.lower()

    def test_discordant_g1_overcall_suppressed_non_afr(
        self, panel, sample_engine: sa.Engine
    ) -> None:
        # Same discordant genotype, EUR ancestry: the false high-risk is gone for
        # every ancestry (the veto precedes classification); the indeterminate QC
        # disclosure is then suppressed outside the validated ancestry.
        _seed_ancestry(sample_engine, "EUR")
        _seed(sample_engine, [_g1("GG"), _g1b("TT"), _g2("II"), _n264k("CC")])
        a = assess_apol1(panel, sample_engine)
        assert a.calls == []
        assert a.ancestry_suppressed is True
        assert a.discordant_loci == ["rs73885319"]

    def test_g1_het_discordant_partner_indeterminate(
        self, panel, sample_engine: sa.Engine
    ) -> None:
        # Tag AG (1) but partner TT (0): the single G1 allele is uncorroborated →
        # vetoed; with G2 reference the result is indeterminate, not low-risk.
        _seed_ancestry(sample_engine, "AFR")
        _seed(sample_engine, [_g1("AG"), _g1b("TT"), _g2("II"), _n264k("CC")])
        a = assess_apol1(panel, sample_engine)
        assert a.discordant_loci == ["rs73885319"]
        assert len(a.calls) == 1
        assert "indeterminate" in a.calls[0].risk_classification.lower()

    def test_concordant_g1g1_still_high_risk(self, panel, sample_engine: sa.Engine) -> None:
        # Both G1 SNPs homozygous risk (concordant) → genuine G1/G1, high-risk.
        _seed_ancestry(sample_engine, "AFR")
        _seed(sample_engine, [_g1("GG"), _g1b("GG"), _g2("II"), _n264k("CC")])
        a = assess_apol1(panel, sample_engine)
        assert a.discordant_loci == []
        assert len(a.calls) == 1
        assert "high-risk" in a.calls[0].risk_classification.lower()

    def test_partner_inflated_artifact_does_not_veto(
        self, panel, sample_engine: sa.Engine
    ) -> None:
        # G1/G2 carrier: tag AG (1), partner GG (2) — the documented rs60910145
        # over-amplification artifact (partner > tag). This must NOT veto: the
        # G1/G2 compound is genuinely high-risk.
        _seed_ancestry(sample_engine, "AFR")
        _seed(sample_engine, [_g1("AG"), _g1b("GG"), _g2("DI"), _n264k("CC")])
        a = assess_apol1(panel, sample_engine)
        assert a.discordant_loci == []
        assert len(a.calls) == 1
        assert "high-risk" in a.calls[0].risk_classification.lower()

    def test_tag_alone_off_chip_partner_still_calls(self, panel, sample_engine: sa.Engine) -> None:
        # rs60910145 off-chip (untyped): the tag SNP alone is a validated G1
        # readout (near-absolute LD), so G1/G1 still fires high-risk — an untyped
        # partner never vetoes.
        _seed_ancestry(sample_engine, "AFR")
        _seed(sample_engine, [_g1("GG"), _g2("II"), _n264k("CC")])  # rs60910145 absent
        a = assess_apol1(panel, sample_engine)
        assert a.discordant_loci == []
        assert len(a.calls) == 1
        assert "high-risk" in a.calls[0].risk_classification.lower()

    def test_discordant_g1_with_g2_homozygous_still_high_risk(
        self, panel, sample_engine: sa.Engine
    ) -> None:
        # Discordant G1 (tag GG, partner TT → vetoed) but G2/G2 present: the
        # two-risk-allele call stands on G2 alone, independent of the vetoed G1.
        _seed_ancestry(sample_engine, "AFR")
        _seed(sample_engine, [_g1("GG"), _g1b("TT"), _g2("DD"), _n264k("CC")])
        a = assess_apol1(panel, sample_engine)
        assert a.discordant_loci == ["rs73885319"]
        assert len(a.calls) == 1
        call = a.calls[0]
        assert "high-risk" in call.risk_classification.lower()
        # The vetoed tag was TYPED (GG) — it must NOT be reported as "not typed
        # on this array"; an accurate discordance note is shown instead.
        caveats = " ".join(call.detail.get("caveats", [])).lower()
        assert "not typed on this array" not in caveats
        assert "discordant with its cis partner" in caveats
        assert "rs73885319" not in call.detail.get("untyped_loci", [])

    def test_discordant_g1_het_with_g2_het_flips_to_indeterminate(
        self, panel, sample_engine: sa.Engine
    ) -> None:
        # The discriminating case: tag AG (1) + G2 het DI (1) sum to 2 PRE-fix
        # (a false high-risk); the discordant partner TT (0) vetoes the G1 tag,
        # dropping the known count to 1 → indeterminate, not high-risk.
        _seed_ancestry(sample_engine, "AFR")
        _seed(sample_engine, [_g1("AG"), _g1b("TT"), _g2("DI"), _n264k("CC")])
        a = assess_apol1(panel, sample_engine)
        assert a.discordant_loci == ["rs73885319"]
        assert len(a.calls) == 1
        assert "indeterminate" in a.calls[0].risk_classification.lower()
        assert "high-risk" not in a.calls[0].risk_classification.lower()


def _g2_alias(rsid: str, genotype: str) -> dict:
    """Seed the G2 6-bp deletion under one of its merged alias rsIDs."""
    return {"rsid": rsid, "chrom": "22", "pos": 36662042, "genotype": genotype}


class TestG2AliasResolution:
    """#262 — the APOL1 G2 deletion carries merged rsIDs (rs71785313 ≡
    rs143830837 ≡ rs1317778148). A G2 typed under an alias must be read as G2,
    not under-called as off-chip."""

    def test_panel_declares_verified_g2_aliases(self, panel) -> None:
        g2 = panel.locus("rs71785313")
        assert g2 is not None
        assert g2.alias_rsids == ("rs143830837", "rs1317778148")

    @pytest.mark.parametrize("alias", ["rs143830837", "rs1317778148"])
    def test_g2_under_alias_read_as_g2(self, panel, sample_engine: sa.Engine, alias: str) -> None:
        # G2 deletion stored under an alias rsID (canonical rs71785313 absent).
        _seed(sample_engine, [_g2_alias(alias, "DI")])
        readouts = read_genotypes(panel, sample_engine)
        g2 = readouts["rs71785313"]  # keyed back to the canonical rsid
        assert g2.status == PROBE_TYPED
        assert g2.genotype == "DI"

    def test_g1g2_high_risk_with_g2_under_alias(self, panel, sample_engine: sa.Engine) -> None:
        # The sensitivity fix: G1 het (tag AG = 1) + a real G2 het (DI = 1) typed
        # under an alias = two risk alleles → recessive high-risk. Pre-fix the
        # alias-stored G2 read as off-chip, under-calling this to indeterminate.
        _seed_ancestry(sample_engine, "AFR")
        _seed(
            sample_engine,
            [_g1("AG"), _g1b("AG"), _g2_alias("rs143830837", "DI"), _n264k("CC")],
        )
        a = assess_apol1(panel, sample_engine)
        assert len(a.calls) == 1
        assert "high-risk" in a.calls[0].risk_classification.lower()
        assert "rs71785313" not in a.indeterminate_loci

    def test_canonical_rsid_still_resolves_without_alias(
        self, panel, sample_engine: sa.Engine
    ) -> None:
        # Regression: a G2 under the canonical rsid is unaffected by the alias path.
        _seed(sample_engine, [_g2("DD")])
        readouts = read_genotypes(panel, sample_engine)
        assert readouts["rs71785313"].status == PROBE_TYPED
        assert readouts["rs71785313"].genotype == "DD"


class TestMonoallelicWording:
    """#313: APOL1 single-risk-allele wording must not claim zero risk, in light
    of monoallelic CKD/FSGS evidence (Gbadegesin et al., NEJM 2025). Two risk
    alleles remain the established high-risk genotype."""

    def test_disclaimer_does_not_claim_single_allele_zero_risk(self) -> None:
        from backend.disclaimers import APOL1_DISCLAIMER_TEXT

        text = APOL1_DISCLAIMER_TEXT.lower()
        assert "does not raise risk" not in text
        assert "monoallelic" in text
        assert "gbadegesin" in text
        # The two-risk-allele high-risk framing is retained.
        assert "two risk alleles" in text

    def test_recessive_caveat_softened(self) -> None:
        from backend.analysis.risk_genotype import CAVEAT_REGISTRY

        caveat = CAVEAT_REGISTRY["apol1_recessive"].lower()
        assert "does not raise risk" not in caveat
        assert "monoallelic" in caveat
        assert "not zero risk" in caveat
        assert "high-risk genotype" in caveat

    def test_partial_disclosure_not_called_low_risk(self) -> None:
        # The partial (one-typed-allele, second-untyped) disclosure must not call a
        # single-risk-allele genotype "low-risk", and must carry the monoallelic
        # caveat. Read the curated panel JSON and assert on the specific
        # partial_disclosure finding_text field(s) rather than a serialized blob.
        panel_path = (
            Path(__file__).resolve().parent.parent.parent
            / "backend"
            / "data"
            / "panels"
            / "apol1_panel.json"
        )
        data = json.loads(panel_path.read_text(encoding="utf-8"))
        texts = [
            gm["partial_disclosure"]["finding_text"]
            for gm in data["genotype_models"]
            if "partial_disclosure" in gm
        ]
        assert texts, "no partial_disclosure finding_text found in apol1_panel.json"
        for text in texts:
            lowered = text.lower()
            assert "low-risk if you do not" not in lowered
            assert "gbadegesin" in lowered

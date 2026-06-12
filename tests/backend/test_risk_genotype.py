"""Tests for the declarative by-rsID risk-genotype engine.

Engine-level behaviour independent of any specific module: probe-readout status,
dosage computation, model evaluation (first_match), the carriage gate, the
relative-vs-absolute and unknown-caveat loader guards, the ancestry gate, and
idempotent storage that isolates other modules' findings.
"""

from __future__ import annotations

import json

import pytest
import sqlalchemy as sa

from backend.analysis.risk_genotype import (
    ALLELE_TYPE_INDEL,
    PROBE_ABSENT,
    PROBE_NO_CALL,
    PROBE_TYPED,
    TOTAL_RISK_DOSAGE_KEY,
    GenotypeModel,
    RiskLocus,
    RiskPanel,
    _indel_dosage,
    classify,
    compute_dosages,
    load_risk_panel,
    read_genotypes,
    store_risk_findings,
)
from backend.db.tables import findings, raw_variants


def _panel(models: list[GenotypeModel], **kwargs) -> RiskPanel:
    return RiskPanel(
        module="testmod",
        version="1.0.0",
        description="",
        category="risk_genotype",
        loci=[
            RiskLocus(rsid="rsA", gene_symbol="GENEA", label="A", risk_allele="A", ref_allele="G"),
            RiskLocus(rsid="rsB", gene_symbol="GENEB", label="B", risk_allele="T", ref_allele="C"),
        ],
        genotype_models=models,
        **kwargs,
    )


def _seed(engine: sa.Engine, rows: list[dict]) -> None:
    with engine.begin() as conn:
        conn.execute(sa.insert(raw_variants), rows)


class TestReadGenotypes:
    def test_typed_no_call_absent(self, sample_engine: sa.Engine) -> None:
        _seed(
            sample_engine,
            [
                {"rsid": "rsA", "chrom": "6", "pos": 1, "genotype": "AG"},
                {"rsid": "rsB", "chrom": "6", "pos": 2, "genotype": "--"},
                # rsC absent
            ],
        )
        panel = _panel([])
        readouts = read_genotypes(panel, sample_engine)
        assert readouts["rsA"].status == PROBE_TYPED
        assert readouts["rsB"].status == PROBE_NO_CALL
        assert readouts["rsB"].rsid == "rsB"

    def test_absent_probe(self, sample_engine: sa.Engine) -> None:
        _seed(sample_engine, [{"rsid": "rsA", "chrom": "6", "pos": 1, "genotype": "AG"}])
        readouts = read_genotypes(_panel([]), sample_engine)
        assert readouts["rsB"].status == PROBE_ABSENT


class TestComputeDosages:
    def test_dosage_and_indeterminate(self, sample_engine: sa.Engine) -> None:
        _seed(
            sample_engine,
            [
                {"rsid": "rsA", "chrom": "6", "pos": 1, "genotype": "AA"},  # risk A homozygous
                {"rsid": "rsB", "chrom": "6", "pos": 2, "genotype": "--"},  # no-call
            ],
        )
        panel = _panel([])
        dosages = compute_dosages(panel, read_genotypes(panel, sample_engine))
        assert dosages["rsA"] == 2
        assert dosages["rsB"] is None  # indeterminate, never a false 0


class TestClassify:
    def test_all_reference_no_calls(self, sample_engine: sa.Engine) -> None:
        """All-reference dosages fire no model — the carriage gate."""
        _seed(
            sample_engine,
            [
                {"rsid": "rsA", "chrom": "6", "pos": 1, "genotype": "GG"},  # ref/ref
                {"rsid": "rsB", "chrom": "6", "pos": 2, "genotype": "CC"},
            ],
        )
        model = GenotypeModel(
            id="hom_a",
            match={"rsA": {"dosage": 2}},
            risk_classification="A homozygous",
            evidence_stars=3,
            finding_text="A hom {genotype}",
        )
        panel = _panel([model])
        readouts = read_genotypes(panel, sample_engine)
        dosages = compute_dosages(panel, readouts)
        assessment = classify(panel, dosages, readouts)
        assert assessment.calls == []

    def test_first_match_ordering(self, sample_engine: sa.Engine) -> None:
        """A hom-risk genotype matches the homozygous model before the het one."""
        _seed(sample_engine, [{"rsid": "rsA", "chrom": "6", "pos": 1, "genotype": "AA"}])
        hom = GenotypeModel(
            id="hom_a",
            match={"rsA": {"dosage": 2}},
            risk_classification="A homozygous",
            evidence_stars=3,
            finding_text="A hom {genotype}",
        )
        het = GenotypeModel(
            id="het_a",
            match={"rsA": {"dosage_min": 1}},
            risk_classification="A carrier",
            evidence_stars=1,
            finding_text="A het {genotype}",
        )
        panel = _panel([hom, het], evaluation="first_match")
        readouts = read_genotypes(panel, sample_engine)
        dosages = compute_dosages(panel, readouts)
        assessment = classify(panel, dosages, readouts)
        assert len(assessment.calls) == 1
        assert assessment.calls[0].model_id == "hom_a"

    def test_indeterminate_loci_reported(self, sample_engine: sa.Engine) -> None:
        _seed(sample_engine, [{"rsid": "rsA", "chrom": "6", "pos": 1, "genotype": "AA"}])
        panel = _panel([])  # rsB absent
        readouts = read_genotypes(panel, sample_engine)
        dosages = compute_dosages(panel, readouts)
        assessment = classify(panel, dosages, readouts)
        assert "rsB" in assessment.indeterminate_loci
        assert "rsA" not in assessment.indeterminate_loci


class TestAncestryGate:
    def _gated_panel(self) -> RiskPanel:
        model = GenotypeModel(
            id="high_risk",
            match={"rsA": {"dosage": 2}},
            risk_classification="High risk",
            evidence_stars=3,
            finding_text="High risk {genotype}",
        )
        return _panel(
            [model],
            ancestry_gate={"required_ancestry": "AFR", "mode": "suppress", "min_fraction": 0.5},
        )

    def test_suppressed_for_non_target_ancestry(self, sample_engine: sa.Engine) -> None:
        _seed(sample_engine, [{"rsid": "rsA", "chrom": "6", "pos": 1, "genotype": "AA"}])
        panel = self._gated_panel()
        readouts = read_genotypes(panel, sample_engine)
        dosages = compute_dosages(panel, readouts)
        assessment = classify(panel, dosages, readouts, inferred_ancestry="EUR")
        assert assessment.calls == []
        assert assessment.ancestry_suppressed is True

    def test_fires_for_target_ancestry(self, sample_engine: sa.Engine) -> None:
        _seed(sample_engine, [{"rsid": "rsA", "chrom": "6", "pos": 1, "genotype": "AA"}])
        panel = self._gated_panel()
        readouts = read_genotypes(panel, sample_engine)
        dosages = compute_dosages(panel, readouts)
        assessment = classify(
            panel, dosages, readouts, inferred_ancestry="AFR", ancestry_fraction=0.9
        )
        assert len(assessment.calls) == 1
        assert assessment.ancestry_suppressed is False

    def test_caveat_mode_keeps_off_target_call(self, sample_engine: sa.Engine) -> None:
        _seed(sample_engine, [{"rsid": "rsA", "chrom": "6", "pos": 1, "genotype": "AA"}])
        model = GenotypeModel(
            id="high_risk",
            match={"rsA": {"dosage": 2}},
            risk_classification="High risk",
            evidence_stars=3,
            finding_text="High risk {genotype}",
        )
        panel = _panel(
            [model],
            ancestry_gate={
                "required_ancestry": "AFR",
                "mode": "caveat",
                "min_fraction": 0.5,
                "note": "Off-target ancestry caveat.",
            },
        )
        readouts = read_genotypes(panel, sample_engine)
        dosages = compute_dosages(panel, readouts)
        assessment = classify(panel, dosages, readouts, inferred_ancestry="EUR")
        assert len(assessment.calls) == 1
        assert assessment.ancestry_suppressed is False
        assert "Off-target ancestry caveat." in assessment.calls[0].finding_text


class TestAncestrySpecificOdds:
    def _or_panel(self) -> RiskPanel:
        model = GenotypeModel(
            id="risk",
            match={"rsA": {"dosage_min": 1}},
            risk_classification="Risk",
            evidence_stars=2,
            finding_text="Risk: {odds_ratio}",
            odds_ratio_by_ancestry={"EAS": "OR 4.5 (East Asian)", "default": "OR 2.0"},
            absolute_risk_context="Absolute risk stays modest.",
        )
        return _panel([model])

    def test_eas_gets_larger_band(self, sample_engine: sa.Engine) -> None:
        _seed(sample_engine, [{"rsid": "rsA", "chrom": "4", "pos": 1, "genotype": "AA"}])
        panel = self._or_panel()
        readouts = read_genotypes(panel, sample_engine)
        dosages = compute_dosages(panel, readouts)
        a = classify(panel, dosages, readouts, inferred_ancestry="EAS")
        assert "4.5" in a.calls[0].finding_text
        assert a.calls[0].detail["odds_ratio"] == "OR 4.5 (East Asian)"

    def test_default_band_for_other_ancestry(self, sample_engine: sa.Engine) -> None:
        _seed(sample_engine, [{"rsid": "rsA", "chrom": "4", "pos": 1, "genotype": "AA"}])
        panel = self._or_panel()
        readouts = read_genotypes(panel, sample_engine)
        dosages = compute_dosages(panel, readouts)
        a = classify(panel, dosages, readouts, inferred_ancestry="EUR")
        assert "2.0" in a.calls[0].finding_text
        assert a.calls[0].detail["odds_ratio"] == "OR 2.0"  # default band in detail too

    def test_loader_requires_absolute_for_ancestry_odds(self, tmp_path) -> None:
        bad = {
            "module": "x",
            "version": "1.0.0",
            "loci": [{"rsid": "rsA", "gene_symbol": "G", "risk_allele": "A", "ref_allele": "G"}],
            "genotype_models": [
                {
                    "id": "m",
                    "match": {"rsA": {"dosage": 1}},
                    "risk_classification": "carrier",
                    "evidence_stars": 2,
                    "finding_text": "x",
                    "odds_ratio_by_ancestry": {"EAS": "OR 4", "default": "OR 2"},
                }
            ],
        }
        path = tmp_path / "bad_anc.json"
        path.write_text(json.dumps(bad))
        with pytest.raises(ValueError, match="absolute_risk_context"):
            load_risk_panel(path)


class TestLoadRiskPanelGuards:
    def test_rejects_empty_match(self, tmp_path) -> None:
        bad = {
            "module": "x",
            "version": "1.0.0",
            "loci": [{"rsid": "rsA", "gene_symbol": "G", "risk_allele": "A", "ref_allele": "G"}],
            "genotype_models": [
                {
                    "id": "m",
                    "match": {},
                    "risk_classification": "carrier",
                    "evidence_stars": 2,
                    "finding_text": "x",
                }
            ],
        }
        path = tmp_path / "bad_empty.json"
        path.write_text(json.dumps(bad))
        with pytest.raises(ValueError, match="empty 'match'"):
            load_risk_panel(path)

    def test_rejects_odds_ratio_without_absolute(self, tmp_path) -> None:
        bad = {
            "module": "x",
            "version": "1.0.0",
            "loci": [{"rsid": "rsA", "gene_symbol": "G", "risk_allele": "A", "ref_allele": "G"}],
            "genotype_models": [
                {
                    "id": "m",
                    "match": {"rsA": {"dosage": 1}},
                    "risk_classification": "carrier",
                    "evidence_stars": 2,
                    "finding_text": "x",
                    "odds_ratio": "OR 3.0",
                }
            ],
        }
        path = tmp_path / "bad.json"
        path.write_text(json.dumps(bad))
        with pytest.raises(ValueError, match="absolute_risk_context"):
            load_risk_panel(path)

    def test_rejects_unknown_caveat(self, tmp_path) -> None:
        bad = {
            "module": "x",
            "version": "1.0.0",
            "loci": [{"rsid": "rsA", "gene_symbol": "G", "risk_allele": "A", "ref_allele": "G"}],
            "genotype_models": [
                {
                    "id": "m",
                    "match": {"rsA": {"dosage": 1}},
                    "risk_classification": "carrier",
                    "evidence_stars": 2,
                    "finding_text": "x",
                    "caveats": ["not_a_real_caveat"],
                }
            ],
        }
        path = tmp_path / "bad2.json"
        path.write_text(json.dumps(bad))
        with pytest.raises(ValueError, match="caveat"):
            load_risk_panel(path)

    def test_rejects_non_numeric_model_pmid(self, tmp_path) -> None:
        bad = {
            "module": "x",
            "version": "1.0.0",
            "loci": [{"rsid": "rsA", "gene_symbol": "G", "risk_allele": "A", "ref_allele": "G"}],
            "genotype_models": [
                {
                    "id": "m",
                    "match": {"rsA": {"dosage": 1}},
                    "risk_classification": "carrier",
                    "evidence_stars": 2,
                    "finding_text": "x",
                    "pmids": ["PMC10689833"],
                }
            ],
        }
        path = tmp_path / "bad_pmid.json"
        path.write_text(json.dumps(bad))
        with pytest.raises(ValueError, match="non-numeric PMID"):
            load_risk_panel(path)

    def test_rejects_non_ascii_digit_pmid(self, tmp_path) -> None:
        bad = {
            "module": "x",
            "version": "1.0.0",
            "loci": [{"rsid": "rsA", "gene_symbol": "G", "risk_allele": "A", "ref_allele": "G"}],
            "genotype_models": [
                {
                    "id": "m",
                    "match": {"rsA": {"dosage": 1}},
                    "risk_classification": "carrier",
                    "evidence_stars": 2,
                    "finding_text": "x",
                    "pmids": ["１２３４５"],
                }
            ],
        }
        path = tmp_path / "bad_unicode_pmid.json"
        path.write_text(json.dumps(bad))
        with pytest.raises(ValueError, match="non-numeric PMID"):
            load_risk_panel(path)

    def test_rejects_non_numeric_modifier_pmid(self, tmp_path) -> None:
        bad = {
            "module": "x",
            "version": "1.0.0",
            "loci": [{"rsid": "rsA", "gene_symbol": "G", "risk_allele": "A", "ref_allele": "G"}],
            "genotype_models": [
                {
                    "id": "m",
                    "match": {"rsA": {"dosage": 1}},
                    "risk_classification": "carrier",
                    "evidence_stars": 2,
                    "finding_text": "x",
                    "modifier": {
                        "rsid": "rsB",
                        "unassessed_caveat": "Modifier was not assessed.",
                        "pmids": ["PMC10689833"],
                    },
                }
            ],
        }
        path = tmp_path / "bad_modifier_pmid.json"
        path.write_text(json.dumps(bad))
        with pytest.raises(ValueError, match="modifier\\.pmids"):
            load_risk_panel(path)


# ── Engine extensions for APOL1 (recessive / total_risk_dosage / indel / modifier) ──


def _recessive_panel(*, partial: bool = False, **kwargs) -> RiskPanel:
    """A 3-locus APOL1-shaped panel: G1 (snv), G2 (indel), modifier (snv)."""
    high_risk = GenotypeModel(
        id="high_risk",
        match={TOTAL_RISK_DOSAGE_KEY: {"rsids": ["rsG1", "rsG2"], "dosage_min": 2}},
        primary_rsid="rsG1",
        recessive=True,
        risk_classification="High risk (two risk alleles)",
        evidence_stars=3,
        finding_text="High risk {genotype}",
        modifier={
            "rsid": "rsMod",
            "attenuates_risk_loci": ["rsG2"],
            "present_min_dosage": 1,
            "unassessed_caveat": "Modifier not assessed — risk may be overstated.",
            "reclassify": {
                "risk_classification": "Attenuated by modifier",
                "evidence_stars": 1,
                "finding_text": "Attenuated by the modifier.",
            },
        },
        partial_disclosure=(
            {
                "risk_classification": "Indeterminate (partial genotype)",
                "evidence_stars": 1,
                "finding_text": "Indeterminate: one risk allele ({genotype}); a locus untyped.",
            }
            if partial
            else None
        ),
    )
    return RiskPanel(
        module="apol1test",
        version="1.0.0",
        description="",
        category="risk_genotype",
        loci=[
            RiskLocus(
                rsid="rsG1", gene_symbol="APOL1", label="G1", risk_allele="A", ref_allele="G"
            ),
            RiskLocus(
                rsid="rsG2",
                gene_symbol="APOL1",
                label="G2",
                risk_allele="D",
                ref_allele="I",
                allele_type=ALLELE_TYPE_INDEL,
            ),
            RiskLocus(
                rsid="rsMod", gene_symbol="APOL1", label="Mod", risk_allele="T", ref_allele="C"
            ),
        ],
        genotype_models=[high_risk],
        **kwargs,
    )


def _assess(panel: RiskPanel, sample_engine: sa.Engine, **classify_kw):
    readouts = read_genotypes(panel, sample_engine)
    dosages = compute_dosages(panel, readouts)
    return classify(panel, dosages, readouts, **classify_kw)


class TestIndelDosage:
    def test_indel_token_counts(self) -> None:
        assert _indel_dosage("DD", "D", "I") == 2
        assert _indel_dosage("DI", "D", "I") == 1
        assert _indel_dosage("ID", "D", "I") == 1
        assert _indel_dosage("II", "D", "I") == 0
        assert _indel_dosage("D", "D", "I") == 1

    def test_indel_unresolvable_is_none(self) -> None:
        assert _indel_dosage("--", "D", "I") is None
        assert _indel_dosage(None, "D", "I") is None
        assert _indel_dosage("DG", "D", "I") is None  # mixed with a non-token allele

    def test_indel_locus_not_treated_as_no_call(self, sample_engine: sa.Engine) -> None:
        """The global is_no_call discards 'DD'/'II'; an indel locus must not."""
        _seed(sample_engine, [{"rsid": "rsG2", "chrom": "22", "pos": 1, "genotype": "DD"}])
        panel = _recessive_panel()
        readouts = read_genotypes(panel, sample_engine)
        assert readouts["rsG2"].status == PROBE_TYPED
        assert compute_dosages(panel, readouts)["rsG2"] == 2


class TestTotalRiskDosageRecessive:
    def test_two_risk_alleles_fires(self, sample_engine: sa.Engine) -> None:
        # G1/G1 homozygous = 2 risk alleles.
        _seed(sample_engine, [{"rsid": "rsG1", "chrom": "22", "pos": 1, "genotype": "AA"}])
        a = _assess(_recessive_panel(), sample_engine)
        assert len(a.calls) == 1
        assert a.calls[0].model_id == "high_risk"

    def test_compound_two_alleles_fires(self, sample_engine: sa.Engine) -> None:
        # G1 het + G2 het = 2 risk alleles total.
        _seed(
            sample_engine,
            [
                {"rsid": "rsG1", "chrom": "22", "pos": 1, "genotype": "AG"},
                {"rsid": "rsG2", "chrom": "22", "pos": 2, "genotype": "DI"},
            ],
        )
        a = _assess(_recessive_panel(), sample_engine)
        assert len(a.calls) == 1

    def test_single_risk_allele_no_finding(self, sample_engine: sa.Engine) -> None:
        # G1 het only, G2 absent -> 1 known risk allele -> recessive does not fire.
        _seed(sample_engine, [{"rsid": "rsG1", "chrom": "22", "pos": 1, "genotype": "AG"}])
        a = _assess(_recessive_panel(), sample_engine)
        assert a.calls == []


class TestPartialDisclosure:
    def test_one_allele_plus_untyped_locus_is_indeterminate(
        self, sample_engine: sa.Engine
    ) -> None:
        # One confirmed risk allele + the other contributing locus off-chip ->
        # genuinely indeterminate (could be high-risk), so disclose, not silence.
        _seed(sample_engine, [{"rsid": "rsG1", "chrom": "22", "pos": 1, "genotype": "AG"}])
        a = _assess(_recessive_panel(partial=True), sample_engine)
        assert len(a.calls) == 1
        call = a.calls[0]
        assert "indeterminate" in call.risk_classification.lower()
        assert call.detail["indeterminate"] is True
        assert "rsG2" in call.detail["untyped_loci"]
        assert call.evidence_stars == 1

    def test_one_allele_with_other_typed_ref_is_low_risk_no_finding(
        self, sample_engine: sa.Engine
    ) -> None:
        # G1 het + G2 confirmed reference -> genuinely low-risk, no disclosure.
        _seed(
            sample_engine,
            [
                {"rsid": "rsG1", "chrom": "22", "pos": 1, "genotype": "AG"},
                {"rsid": "rsG2", "chrom": "22", "pos": 2, "genotype": "II"},
            ],
        )
        a = _assess(_recessive_panel(partial=True), sample_engine)
        assert a.calls == []

    def test_zero_alleles_untyped_no_disclosure(self, sample_engine: sa.Engine) -> None:
        # No confirmed risk allele at all -> not "indeterminate", just no data.
        _seed(sample_engine, [{"rsid": "rsG1", "chrom": "22", "pos": 1, "genotype": "GG"}])
        a = _assess(_recessive_panel(partial=True), sample_engine)
        assert a.calls == []

    def test_indeterminate_suppressed_off_target_ancestry(self, sample_engine: sa.Engine) -> None:
        _seed(sample_engine, [{"rsid": "rsG1", "chrom": "22", "pos": 1, "genotype": "AG"}])
        panel = _recessive_panel(
            partial=True,
            ancestry_gate={"required_ancestry": "AFR", "mode": "suppress", "min_fraction": 0.5},
        )
        a = _assess(panel, sample_engine, inferred_ancestry="EUR")
        assert a.calls == []
        assert a.ancestry_suppressed is True

    def test_caveat_mode_can_still_suppress_indeterminate(self, sample_engine: sa.Engine) -> None:
        _seed(sample_engine, [{"rsid": "rsG1", "chrom": "22", "pos": 1, "genotype": "AG"}])
        panel = _recessive_panel(
            partial=True,
            ancestry_gate={
                "required_ancestry": "AFR",
                "mode": "caveat",
                "min_fraction": 0.5,
                "suppress_indeterminate": True,
                "note": "Off-target ancestry caveat.",
            },
        )
        a = _assess(panel, sample_engine, inferred_ancestry="EUR")
        assert a.calls == []
        assert a.ancestry_suppressed is True

    def test_caveat_mode_keeps_observed_recessive_call(self, sample_engine: sa.Engine) -> None:
        _seed(sample_engine, [{"rsid": "rsG1", "chrom": "22", "pos": 1, "genotype": "AA"}])
        panel = _recessive_panel(
            partial=True,
            ancestry_gate={
                "required_ancestry": "AFR",
                "mode": "caveat",
                "min_fraction": 0.5,
                "suppress_indeterminate": True,
                "note": "Off-target ancestry caveat.",
            },
        )
        a = _assess(panel, sample_engine, inferred_ancestry="EUR")
        assert len(a.calls) == 1
        assert a.ancestry_suppressed is False
        assert a.calls[0].detail["partial_genotype"] is True
        assert "Off-target ancestry caveat." in a.calls[0].finding_text


class TestPartialGuardrail:
    def test_partial_when_contributing_locus_untyped(self, sample_engine: sa.Engine) -> None:
        # G1/G1 fires (2 alleles) but G2 indel is off-chip -> partial genotype caveat.
        _seed(sample_engine, [{"rsid": "rsG1", "chrom": "22", "pos": 1, "genotype": "AA"}])
        a = _assess(_recessive_panel(), sample_engine)
        assert len(a.calls) == 1
        detail = a.calls[0].detail
        assert detail["partial_genotype"] is True
        assert "rsG2" in detail["untyped_loci"]
        assert "partial genotype" in a.calls[0].finding_text.lower()

    def test_no_partial_when_all_typed(self, sample_engine: sa.Engine) -> None:
        _seed(
            sample_engine,
            [
                {"rsid": "rsG1", "chrom": "22", "pos": 1, "genotype": "AA"},
                {"rsid": "rsG2", "chrom": "22", "pos": 2, "genotype": "II"},
                {"rsid": "rsMod", "chrom": "22", "pos": 3, "genotype": "CC"},
            ],
        )
        a = _assess(_recessive_panel(), sample_engine)
        assert a.calls[0].detail.get("partial_genotype") is None


class TestModifier:
    def test_modifier_present_attenuates(self, sample_engine: sa.Engine) -> None:
        # G2/G2 high risk + modifier present -> reclassified to attenuated.
        _seed(
            sample_engine,
            [
                {"rsid": "rsG2", "chrom": "22", "pos": 2, "genotype": "DD"},
                {"rsid": "rsMod", "chrom": "22", "pos": 3, "genotype": "CT"},
            ],
        )
        a = _assess(_recessive_panel(), sample_engine)
        assert a.calls[0].risk_classification == "Attenuated by modifier"
        assert a.calls[0].evidence_stars == 1
        assert a.calls[0].detail["modifier_applied"] == "rsMod"

    def test_modifier_unassessed_caveats_high_risk(self, sample_engine: sa.Engine) -> None:
        # G1/G1 high risk, G2 + modifier both off-chip -> both caveats, still high-risk.
        _seed(sample_engine, [{"rsid": "rsG1", "chrom": "22", "pos": 1, "genotype": "AA"}])
        a = _assess(_recessive_panel(), sample_engine)
        call = a.calls[0]
        assert call.risk_classification == "High risk (two risk alleles)"
        caveats = " ".join(call.detail["caveats"]).lower()
        assert "modifier not assessed" in caveats  # modifier-unassessed caveat
        assert "partial genotype" in caveats  # partial-genotype caveat

    def test_modifier_confidently_absent_no_caveat(self, sample_engine: sa.Engine) -> None:
        # G1/G1 high risk, G2 typed-absent, modifier typed-absent -> confident, no caveat.
        _seed(
            sample_engine,
            [
                {"rsid": "rsG1", "chrom": "22", "pos": 1, "genotype": "AA"},
                {"rsid": "rsG2", "chrom": "22", "pos": 2, "genotype": "II"},
                {"rsid": "rsMod", "chrom": "22", "pos": 3, "genotype": "CC"},
            ],
        )
        a = _assess(_recessive_panel(), sample_engine)
        call = a.calls[0]
        assert call.risk_classification == "High risk (two risk alleles)"
        caveats = " ".join(call.detail["caveats"]).lower()
        assert "modifier not assessed" not in caveats


class TestStoreRiskFindings:
    def _assessment_with_call(self, sample_engine: sa.Engine):
        _seed(sample_engine, [{"rsid": "rsA", "chrom": "6", "pos": 1, "genotype": "AA"}])
        model = GenotypeModel(
            id="hom_a",
            match={"rsA": {"dosage": 2}},
            risk_classification="A homozygous",
            evidence_stars=3,
            finding_text="A hom {genotype}",
            zygosity="hom_alt",
        )
        panel = _panel([model])
        readouts = read_genotypes(panel, sample_engine)
        dosages = compute_dosages(panel, readouts)
        return classify(panel, dosages, readouts)

    def test_idempotent(self, sample_engine: sa.Engine) -> None:
        assessment = self._assessment_with_call(sample_engine)
        store_risk_findings(assessment, sample_engine)
        store_risk_findings(assessment, sample_engine)
        with sample_engine.connect() as conn:
            count = conn.execute(
                sa.select(sa.func.count())
                .select_from(findings)
                .where(findings.c.module == "testmod")
            ).scalar()
        assert count == 1

    def test_isolates_other_modules(self, sample_engine: sa.Engine) -> None:
        with sample_engine.begin() as conn:
            conn.execute(
                sa.insert(findings),
                [
                    {
                        "module": "cardiovascular",
                        "category": "monogenic_variant",
                        "evidence_level": 4,
                        "finding_text": "LDLR — Pathogenic",
                    }
                ],
            )
        assessment = self._assessment_with_call(sample_engine)
        store_risk_findings(assessment, sample_engine)
        with sample_engine.connect() as conn:
            cv = conn.execute(
                sa.select(sa.func.count())
                .select_from(findings)
                .where(findings.c.module == "cardiovascular")
            ).scalar()
        assert cv == 1  # untouched

    def test_clinvar_significance_null(self, sample_engine: sa.Engine) -> None:
        """Risk-genotype findings never carry a ClinVar significance."""
        assessment = self._assessment_with_call(sample_engine)
        store_risk_findings(assessment, sample_engine)
        with sample_engine.connect() as conn:
            row = conn.execute(
                sa.select(findings).where(findings.c.module == "testmod")
            ).fetchone()
        assert row.clinvar_significance is None
        detail = json.loads(row.detail_json)
        assert detail["model_id"] == "hom_a"

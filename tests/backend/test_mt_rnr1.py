"""Tests for the MT-RNR1 aminoglycoside-ototoxicity module.

MT-RNR1 m.1555A>G (rs267606617), m.1494C>T (rs267606619) and m.1095T>C
(rs267606618) are mitochondrial 12S-rRNA variants. mtDNA is haploid, so the chip
may report a homoplasmic call as a single character ("G") or doubled ("GG"); the
engine counts a present risk allele (dosage_min: 1) either way. Honesty
guardrails under test: a present m.1555A>G / m.1494C>T fires the high-evidence
(3★) CPIC-avoidance finding; m.1095T>C is preliminary (1★); reference / no-call /
off-chip never produce a false-positive or false-negative; the framing is
decision-support (maternal inheritance + heteroplasmy caveats, explicitly "not an
instruction to start or stop any medication"); and findings write
clinvar_significance=NULL.
"""

from __future__ import annotations

import pytest
import sqlalchemy as sa

from backend.analysis.mt_rnr1 import (
    assess_mt_rnr1,
    load_mt_rnr1_panel,
    store_mt_rnr1_findings,
)
from backend.db.tables import findings, raw_variants

# Phrases that would turn CPIC decision-support into a direct personal medical
# instruction — none may appear in any finding text, caveat, or the disclaimer.
_PRESCRIPTION_DENYLIST = (
    "stop taking",
    "stop your",
    "you must avoid",
    "you should stop",
    "do not take",
    "switch to",
)


@pytest.fixture()
def panel():
    return load_mt_rnr1_panel()


def _seed(engine: sa.Engine, rows: list[dict]) -> None:
    if rows:
        with engine.begin() as conn:
            conn.execute(sa.insert(raw_variants), rows)


def _mt(rsid: str, genotype: str, pos: int) -> dict:
    return {"rsid": rsid, "chrom": "MT", "pos": pos, "genotype": genotype}


def _m1555(genotype: str) -> dict:  # ref A / risk G
    return _mt("rs267606617", genotype, 1555)


def _m1494(genotype: str) -> dict:  # ref C / risk T
    return _mt("rs267606619", genotype, 1494)


def _m1095(genotype: str) -> dict:  # ref T / risk C
    return _mt("rs267606618", genotype, 1095)


class TestHighEvidenceVariants:
    def test_m1555_haploid_call_fires(self, panel, sample_engine: sa.Engine) -> None:
        _seed(sample_engine, [_m1555("G")])  # haploid single-char homoplasmic call
        a = assess_mt_rnr1(panel, sample_engine)
        calls = [c for c in a.calls if c.detail["model_id"] == "m1555ag"]
        assert len(calls) == 1
        assert calls[0].evidence_stars == 3
        assert "aminoglycoside" in calls[0].finding_text.lower()
        assert "cpic" in calls[0].finding_text.lower()

    def test_m1555_doubled_call_fires(self, panel, sample_engine: sa.Engine) -> None:
        _seed(sample_engine, [_m1555("GG")])  # reported doubled
        a = assess_mt_rnr1(panel, sample_engine)
        assert any(c.detail["model_id"] == "m1555ag" for c in a.calls)

    def test_m1555_complement_only_call_is_indeterminate(
        self, panel, sample_engine: sa.Engine
    ) -> None:
        # "C" is the Watson–Crick complement of the plus-strand risk "G", but
        # MT-RNR1 is haploid mtDNA reported on a single strand: a literal
        # plus-strand "C" at m.1555 is an A>C variant, NOT the pathogenic A>G.
        # Without reverse-strand provenance it must be indeterminate, never the
        # CPIC aminoglycoside-avoidance finding. Regression for issue #30.
        _seed(sample_engine, [_m1555("C")])
        a = assess_mt_rnr1(panel, sample_engine)
        assert not any(c.detail["model_id"] == "m1555ag" for c in a.calls)
        assert a.dosages["rs267606617"] is None
        assert "rs267606617" in a.indeterminate_loci

    def test_m1494_fires_three_star(self, panel, sample_engine: sa.Engine) -> None:
        _seed(sample_engine, [_m1494("T")])
        a = assess_mt_rnr1(panel, sample_engine)
        calls = [c for c in a.calls if c.detail["model_id"] == "m1494ct"]
        assert len(calls) == 1
        assert calls[0].evidence_stars == 3

    def test_reference_call_no_finding(self, panel, sample_engine: sa.Engine) -> None:
        _seed(sample_engine, [_m1555("A"), _m1494("C"), _m1095("T")])
        a = assess_mt_rnr1(panel, sample_engine)
        assert a.calls == []


class TestPreliminaryVariant:
    def test_m1095_is_one_star_preliminary(self, panel, sample_engine: sa.Engine) -> None:
        _seed(sample_engine, [_m1095("C")])
        a = assess_mt_rnr1(panel, sample_engine)
        calls = [c for c in a.calls if c.detail["model_id"] == "m1095tc"]
        assert len(calls) == 1
        assert calls[0].evidence_stars == 1
        assert "preliminary" in calls[0].finding_text.lower()


class TestProbeCoverage:
    def test_absent_probe_is_indeterminate(self, panel, sample_engine: sa.Engine) -> None:
        # Only m.1555 typed; the other two positions are off-chip → indeterminate.
        _seed(sample_engine, [_m1555("A")])
        a = assess_mt_rnr1(panel, sample_engine)
        assert "rs267606619" in a.indeterminate_loci
        assert "rs267606618" in a.indeterminate_loci

    def test_no_call_is_indeterminate(self, panel, sample_engine: sa.Engine) -> None:
        _seed(sample_engine, [_m1555("--")])
        a = assess_mt_rnr1(panel, sample_engine)
        assert "rs267606617" in a.indeterminate_loci
        assert a.calls == []


class TestCollectAll:
    def test_multiple_variants_each_surface(self, panel, sample_engine: sa.Engine) -> None:
        _seed(sample_engine, [_m1555("G"), _m1095("C")])
        a = assess_mt_rnr1(panel, sample_engine)
        model_ids = {c.detail["model_id"] for c in a.calls}
        assert model_ids == {"m1555ag", "m1095tc"}


class TestFramingGuardrails:
    def test_no_personal_prescription_and_maternal_heteroplasmy_present(
        self, panel, sample_engine: sa.Engine
    ) -> None:
        from backend.disclaimers import MT_RNR1_DISCLAIMER_TEXT

        _seed(sample_engine, [_m1555("G"), _m1494("T")])
        a = assess_mt_rnr1(panel, sample_engine)
        assert len(a.calls) == 2
        corpus = MT_RNR1_DISCLAIMER_TEXT.lower()
        for call in a.calls:
            corpus += " " + call.finding_text.lower()
            corpus += " " + " ".join(call.detail["caveats"]).lower()
        for banned in _PRESCRIPTION_DENYLIST:
            assert banned not in corpus, f"prescription phrase leaked: {banned!r}"
        # Decision-support framing + mitochondrial-specific caveats must be present.
        assert "not an instruction to start or stop any medication" in corpus
        assert "maternal" in corpus
        assert "heteroplasmy" in corpus


class TestStorageGuardrails:
    def test_clinvar_significance_null_and_evidence_level(
        self, panel, sample_engine: sa.Engine
    ) -> None:
        _seed(sample_engine, [_m1555("G")])
        a = assess_mt_rnr1(panel, sample_engine)
        assert store_mt_rnr1_findings(a, sample_engine) == 1
        with sample_engine.connect() as conn:
            row = conn.execute(
                sa.select(findings).where(findings.c.module == "mt_rnr1")
            ).fetchone()
        assert row.clinvar_significance is None
        assert row.gene_symbol == "MT-RNR1"
        assert row.evidence_level == 3

    def test_store_is_idempotent(self, panel, sample_engine: sa.Engine) -> None:
        _seed(sample_engine, [_m1555("G")])
        a = assess_mt_rnr1(panel, sample_engine)
        store_mt_rnr1_findings(a, sample_engine)
        store_mt_rnr1_findings(a, sample_engine)
        with sample_engine.connect() as conn:
            n = conn.execute(
                sa.select(sa.func.count())
                .select_from(findings)
                .where(findings.c.module == "mt_rnr1")
            ).scalar()
        assert n == 1

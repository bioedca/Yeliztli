"""Tests for the LHON (Leber hereditary optic neuropathy) primary-mutation module.

The three primary mtDNA mutations — MT-ND4 m.11778G>A (rs199476112), MT-ND1
m.3460G>A (rs199476118), MT-ND6 m.14484T>C (rs199476104) — are mitochondrial and
haploid, so the chip may report a homoplasmic call as one char ("A") or doubled
("AA"); the engine counts a present risk allele (dosage_min: 1) either way.
Honesty guardrails under test: a present primary mutation fires a 3★ finding that
explicitly frames incomplete, sex-biased penetrance ("not a diagnosis and not a
prediction"); reference / no-call / off-chip never produce a false-positive or
false-negative; and findings write clinvar_significance=NULL.
"""

from __future__ import annotations

import pytest
import sqlalchemy as sa

from backend.analysis.lhon import assess_lhon, load_lhon_panel, store_lhon_findings
from backend.db.tables import findings, raw_variants


@pytest.fixture()
def panel():
    return load_lhon_panel()


def _seed(engine: sa.Engine, rows: list[dict]) -> None:
    if rows:
        with engine.begin() as conn:
            conn.execute(sa.insert(raw_variants), rows)


def _mt(rsid: str, genotype: str, pos: int) -> dict:
    return {"rsid": rsid, "chrom": "MT", "pos": pos, "genotype": genotype}


def _m11778(genotype: str) -> dict:  # ref G / risk A
    return _mt("rs199476112", genotype, 11778)


def _m3460(genotype: str) -> dict:  # ref G / risk A
    return _mt("rs199476118", genotype, 3460)


def _m14484(genotype: str) -> dict:  # ref T / risk C
    return _mt("rs199476104", genotype, 14484)


class TestPrimaryMutations:
    def test_m11778_haploid_call_fires(self, panel, sample_engine: sa.Engine) -> None:
        _seed(sample_engine, [_m11778("A")])
        a = assess_lhon(panel, sample_engine)
        calls = [c for c in a.calls if c.detail["model_id"] == "m11778ga"]
        assert len(calls) == 1
        assert calls[0].evidence_stars == 3
        assert "not a diagnosis" in calls[0].finding_text.lower()
        assert "not a prediction" in calls[0].finding_text.lower()

    def test_m11778_doubled_call_fires(self, panel, sample_engine: sa.Engine) -> None:
        _seed(sample_engine, [_m11778("AA")])
        a = assess_lhon(panel, sample_engine)
        assert any(c.detail["model_id"] == "m11778ga" for c in a.calls)

    def test_m14484_plus_frame_risk_fires(self, panel, sample_engine: sa.Engine) -> None:
        # Plus/canonical-strand risk "C" is the actual m.14484T>C primary mutation.
        _seed(sample_engine, [_m14484("C")])
        a = assess_lhon(panel, sample_engine)
        assert any(c.detail["model_id"] == "m14484tc" for c in a.calls)

    def test_plus_frame_complement_bases_are_indeterminate_not_calls(
        self, panel, sample_engine: sa.Engine
    ) -> None:
        """A plus-frame base that merely *complements* the risk allele is a
        different mtDNA substitution, not a reverse-strand reading, so it must be
        indeterminate (strand provenance required) — never a false-positive LHON
        primary-mutation call (issue #31).

        These mitochondrial loci are reported on the rCRS/plus strand, so the
        complement fallback is disabled (``allow_strand_complement: false``):
          - m.11778G>A / m.3460G>A (risk A): plus-frame ``T`` complements risk A.
          - m.14484T>C (risk C): plus-frame ``G`` complements risk C.
        Before the fix, ``risk_dosage`` complemented these to the risk allele and
        fired all three findings.
        """
        _seed(sample_engine, [_m11778("T"), _m3460("T"), _m14484("G")])
        a = assess_lhon(panel, sample_engine)
        assert a.calls == []
        assert "rs199476112" in a.indeterminate_loci
        assert "rs199476118" in a.indeterminate_loci
        assert "rs199476104" in a.indeterminate_loci

    def test_m3460_fires(self, panel, sample_engine: sa.Engine) -> None:
        _seed(sample_engine, [_m3460("A")])
        a = assess_lhon(panel, sample_engine)
        assert any(c.detail["model_id"] == "m3460ga" for c in a.calls)

    def test_reference_call_no_finding(self, panel, sample_engine: sa.Engine) -> None:
        _seed(sample_engine, [_m11778("G"), _m3460("G"), _m14484("T")])
        a = assess_lhon(panel, sample_engine)
        assert a.calls == []


class TestProbeCoverage:
    def test_absent_probe_is_indeterminate(self, panel, sample_engine: sa.Engine) -> None:
        _seed(sample_engine, [_m11778("G")])  # other two off-chip
        a = assess_lhon(panel, sample_engine)
        assert "rs199476118" in a.indeterminate_loci
        assert "rs199476104" in a.indeterminate_loci

    def test_no_call_is_indeterminate(self, panel, sample_engine: sa.Engine) -> None:
        _seed(sample_engine, [_m11778("--")])
        a = assess_lhon(panel, sample_engine)
        assert "rs199476112" in a.indeterminate_loci
        assert a.calls == []


class TestCollectAll:
    def test_multiple_variants_each_surface(self, panel, sample_engine: sa.Engine) -> None:
        # A carrier of two primary mutations (rare) surfaces both.
        _seed(sample_engine, [_m11778("A"), _m14484("C")])
        a = assess_lhon(panel, sample_engine)
        model_ids = {c.detail["model_id"] for c in a.calls}
        assert model_ids == {"m11778ga", "m14484tc"}


class TestPenetranceFraming:
    def test_sex_biased_penetrance_and_maternal_present(
        self, panel, sample_engine: sa.Engine
    ) -> None:
        from backend.disclaimers import LHON_DISCLAIMER_TEXT

        _seed(sample_engine, [_m11778("A")])
        a = assess_lhon(panel, sample_engine)
        corpus = LHON_DISCLAIMER_TEXT.lower()
        for call in a.calls:
            corpus += " " + call.finding_text.lower()
            corpus += " " + " ".join(call.detail["caveats"]).lower()
        # Incomplete + sex-biased penetrance must be explicit.
        assert "penetrance" in corpus
        assert "half of male" in corpus or "male carriers" in corpus
        assert "maternal" in corpus
        assert "heteroplasmy" in corpus


class TestStorageGuardrails:
    def test_clinvar_significance_null_and_evidence_level(
        self, panel, sample_engine: sa.Engine
    ) -> None:
        _seed(sample_engine, [_m11778("A")])
        a = assess_lhon(panel, sample_engine)
        assert store_lhon_findings(a, sample_engine) == 1
        with sample_engine.connect() as conn:
            row = conn.execute(sa.select(findings).where(findings.c.module == "lhon")).fetchone()
        assert row.clinvar_significance is None
        assert row.gene_symbol == "MT-ND4"
        assert row.evidence_level == 3

    def test_store_is_idempotent(self, panel, sample_engine: sa.Engine) -> None:
        _seed(sample_engine, [_m11778("A")])
        a = assess_lhon(panel, sample_engine)
        store_lhon_findings(a, sample_engine)
        store_lhon_findings(a, sample_engine)
        with sample_engine.connect() as conn:
            n = conn.execute(
                sa.select(sa.func.count()).select_from(findings).where(findings.c.module == "lhon")
            ).scalar()
        assert n == 1

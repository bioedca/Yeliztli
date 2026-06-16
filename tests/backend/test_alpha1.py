"""Tests for the alpha-1 antitrypsin deficiency module (SERPINA1 Pi*Z / Pi*S)."""

from __future__ import annotations

import json

import pytest
import sqlalchemy as sa

from backend.analysis.alpha1 import (
    assess_alpha1,
    load_alpha1_panel,
    store_alpha1_findings,
)
from backend.db.tables import findings, raw_variants


@pytest.fixture()
def panel():
    return load_alpha1_panel()


def _seed(engine: sa.Engine, rows: list[dict]) -> None:
    with engine.begin() as conn:
        conn.execute(sa.insert(raw_variants), rows)


def _z(genotype: str) -> dict:
    return {"rsid": "rs28929474", "chrom": "14", "pos": 94847262, "genotype": genotype}


def _s(genotype: str) -> dict:
    return {"rsid": "rs17580", "chrom": "14", "pos": 94844947, "genotype": genotype}


class TestGenotypes:
    def test_pizz_severe(self, panel, sample_engine: sa.Engine) -> None:
        _seed(sample_engine, [_z("TT"), _s("TT")])
        a = assess_alpha1(panel, sample_engine)
        assert len(a.calls) == 1
        call = a.calls[0]
        assert call.risk_classification == "PiZZ (severe deficiency)"
        assert call.evidence_stars == 3
        assert "smoking" in call.finding_text.lower()
        assert "augmentation" in call.finding_text.lower()

    def test_pisz_intermediate(self, panel, sample_engine: sa.Engine) -> None:
        _seed(sample_engine, [_z("CT"), _s("AT")])
        a = assess_alpha1(panel, sample_engine)
        assert len(a.calls) == 1
        call = a.calls[0]
        assert call.risk_classification == "PiSZ (phase-inferred intermediate deficiency)"
        assert call.evidence_stars == 2
        assert call.detail["phase_inferred"] is True
        assert call.detail["call_confidence"] == "Partial"
        assert call.detail["confidence_note"] == (
            "Unphased SNP-array genotypes observe both SERPINA1 S and Z variants "
            "but cannot determine whether they are in trans; confirm before "
            "treating as definitive PiSZ."
        )
        caveats = " ".join(call.detail["caveats"]).lower()
        assert "unphased" in caveats
        assert "in trans" in caveats
        assert "serum aat" in caveats
        assert "targeted serpina1 sequencing" in caveats
        assert "phase-inferred" in call.finding_text.lower()

    def test_pimz_carrier(self, panel, sample_engine: sa.Engine) -> None:
        _seed(sample_engine, [_z("CT"), _s("TT")])
        a = assess_alpha1(panel, sample_engine)
        assert a.calls[0].risk_classification == "PiMZ (carrier)"
        assert a.calls[0].evidence_stars == 2

    def test_pims_carrier(self, panel, sample_engine: sa.Engine) -> None:
        _seed(sample_engine, [_z("CC"), _s("AT")])
        a = assess_alpha1(panel, sample_engine)
        assert a.calls[0].risk_classification == "PiMS (carrier)"
        assert a.calls[0].evidence_stars == 1

    def test_pis_homozygote_is_strand_indeterminate(self, panel, sample_engine: sa.Engine) -> None:
        # rs17580 (Pi*S) is the palindromic A/T SNP: a homozygous "AA" is equally the
        # reverse-strand representation of the normal "TT", so a single sample cannot
        # tell which. It must be reported indeterminate, NOT false-called PiSS (#844).
        _seed(sample_engine, [_z("CC"), _s("AA")])
        a = assess_alpha1(panel, sample_engine)
        assert a.calls == []
        assert a.dosages["rs17580"] is None
        assert "rs17580" in a.indeterminate_loci

    def test_normal_no_finding(self, panel, sample_engine: sa.Engine) -> None:
        # The normal-looking "TT" at the palindromic Pi*S locus is itself strand-
        # ambiguous (minus-strand "AA"), so it yields no finding and is reported
        # indeterminate rather than a confident Pi*M call (#844).
        _seed(sample_engine, [_z("CC"), _s("TT")])
        a = assess_alpha1(panel, sample_engine)
        assert a.calls == []
        assert a.dosages["rs17580"] is None
        assert "rs17580" in a.indeterminate_loci


class TestStrandAndIndeterminate:
    def test_pizz_minus_strand(self, panel, sample_engine: sa.Engine) -> None:
        # "AA" is the reverse-strand complement of homozygous-Z "TT".
        _seed(sample_engine, [_z("AA"), _s("TT")])
        a = assess_alpha1(panel, sample_engine)
        assert a.calls[0].risk_classification == "PiZZ (severe deficiency)"

    def test_off_chip_z_indeterminate(self, panel, sample_engine: sa.Engine) -> None:
        # Z absent (off-chip) → indeterminate. The seeded S "TT" is the palindromic
        # ref homozygote, itself strand-indeterminate (#844), so neither locus calls.
        _seed(sample_engine, [_s("TT")])
        a = assess_alpha1(panel, sample_engine)
        assert a.calls == []
        assert "rs28929474" in a.indeterminate_loci

    def test_off_chip_s_indeterminate(self, panel, sample_engine: sa.Engine) -> None:
        _seed(sample_engine, [_z("CC")])  # S absent, Z normal (C is the rs28929474 reference)
        a = assess_alpha1(panel, sample_engine)
        assert a.calls == []
        assert "rs17580" in a.indeterminate_loci


class TestRareNullCaveat:
    def test_caveat_mentions_rare_null(self, panel, sample_engine: sa.Engine) -> None:
        _seed(sample_engine, [_z("TT"), _s("TT")])
        a = assess_alpha1(panel, sample_engine)
        caveats = " ".join(a.calls[0].detail["caveats"]).lower()
        assert "does not exclude" in caveats
        assert "rare null" in caveats


class TestStorage:
    def test_stored(self, panel, sample_engine: sa.Engine) -> None:
        _seed(sample_engine, [_z("TT"), _s("TT")])
        a = assess_alpha1(panel, sample_engine)
        assert store_alpha1_findings(a, sample_engine) == 1
        with sample_engine.connect() as conn:
            results = conn.execute(
                sa.select(findings).where(findings.c.module == "alpha1")
            ).fetchall()
        assert len(results) == 1  # exactly one finding, no duplicates
        row = results[0]
        assert row.category == "risk_genotype"
        assert row.gene_symbol == "SERPINA1"
        detail = json.loads(row.detail_json)
        assert detail["genotype_calls"]["rs28929474"] == "TT"

"""Resolve persisted HLA calls into allele carriage (Wave D).

Pins carriage resolution — homozygous / heterozygous / typed-but-absent /
not-called(None) — the prefix-aware group query (``B*27`` matches ``27:05``),
2-field specificity, the ``HLA-`` prefix, low-confidence propagation, and the
graceful read when the ``hla_calls`` table is absent.
"""

from __future__ import annotations

import sqlalchemy as sa

from backend.analysis.hla_resolver import (
    ResolvedHLACall,
    carriage_map,
    carries_allele,
    read_hla_calls,
)
from backend.db.sample_schema import create_sample_tables
from backend.db.tables import hla_calls


def _call(
    locus, a1, a2, *, prob=0.95, low=False, source="hibag", ancestry="European"
) -> ResolvedHLACall:
    return ResolvedHLACall(
        locus=locus,
        allele1=a1,
        allele2=a2,
        prob=prob,
        low_confidence=low,
        source=source,
        ancestry_model=ancestry,
    )


class TestReadHlaCalls:
    def test_reads_persisted_rows(self) -> None:
        engine = sa.create_engine("sqlite://")
        create_sample_tables(engine)
        with engine.begin() as conn:
            conn.execute(
                sa.insert(hla_calls),
                [
                    {
                        "locus": "B",
                        "allele1": "57:01",
                        "allele2": "07:02",
                        "prob": 0.3,
                        "matching": 0.9,
                        "low_confidence": 1,
                        "ancestry_model": "European",
                        "source": "hibag",
                    },
                ],
            )
        calls = read_hla_calls(engine)
        assert len(calls) == 1
        assert calls[0].locus == "B"
        assert calls[0].low_confidence is True
        assert calls[0].source == "hibag"

    def test_missing_table_returns_empty(self) -> None:
        # A bare DB without the sample schema -> no hla_calls table -> graceful [].
        engine = sa.create_engine("sqlite://")
        assert read_hla_calls(engine) == []


class TestCarriesAllele:
    def test_homozygous(self) -> None:
        calls = [_call("B", "57:01", "57:01")]
        c = carries_allele(calls, "B*57:01")
        assert c is not None
        assert c.carried is True
        assert c.copies == 2
        assert c.zygosity == "homozygous"

    def test_heterozygous(self) -> None:
        c = carries_allele([_call("B", "57:01", "07:02")], "B*57:01")
        assert c.carried is True
        assert c.copies == 1
        assert c.zygosity == "heterozygous"

    def test_typed_but_absent_is_not_carried(self) -> None:
        c = carries_allele([_call("B", "58:01", "07:02")], "B*57:01")
        assert c is not None
        assert c.carried is False
        assert c.copies == 0
        assert c.zygosity is None

    def test_locus_not_called_returns_none(self) -> None:
        # Unknown, not a false negative — the locus was never typed.
        assert carries_allele([_call("B", "57:01", "07:02")], "C*06:02") is None

    def test_hla_prefix_and_multifield_locus(self) -> None:
        c = carries_allele([_call("DQB1", "06:02", "03:01")], "HLA-DQB1*06:02")
        assert c.carried is True
        assert c.copies == 1
        assert c.locus == "DQB1"

    def test_group_level_query_matches_two_field_members(self) -> None:
        # B*27 (susceptibility group) matches any 27:xx allele.
        c = carries_allele([_call("B", "27:05", "07:02")], "B*27")
        assert c.carried is True
        assert c.copies == 1
        c_hom = carries_allele([_call("B", "27:05", "27:02")], "B*27")
        assert c_hom.copies == 2
        assert c_hom.zygosity == "homozygous"

    def test_two_field_query_is_specific(self) -> None:
        # B*57:01 must not match a different 2-field member 57:02.
        c = carries_allele([_call("B", "57:02", "07:02")], "B*57:01")
        assert c.carried is False

    def test_query_more_specific_than_stored_does_not_match(self) -> None:
        # A 3-field query against a 2-field call never matches (no over-claiming).
        c = carries_allele([_call("B", "57:01", "07:02")], "B*57:01:01")
        assert c.carried is False

    def test_malformed_query_returns_none(self) -> None:
        assert carries_allele([_call("B", "57:01", "07:02")], "B57:01") is None

    def test_low_confidence_propagates(self) -> None:
        c = carries_allele([_call("B", "57:01", "07:02", prob=0.4, low=True)], "B*57:01")
        assert c.low_confidence is True
        assert c.prob == 0.4


class TestCarriageMap:
    def test_batch(self) -> None:
        calls = [_call("B", "57:01", "07:02"), _call("A", "31:01", "02:01")]
        m = carriage_map(calls, ["B*57:01", "A*31:01", "C*06:02"])
        assert m["B*57:01"].carried is True
        assert m["A*31:01"].carried is True
        assert m["C*06:02"] is None  # locus not called

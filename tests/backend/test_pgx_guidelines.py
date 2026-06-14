"""Cross-source pharmacogenomic evidence layer over CPIC (SW-E2).

Verifies the curated PharmGKB-LoE / DPWG / FDA data joins to the sample's CPIC
prescribing alerts, with the verified values from authoritative downloads.
"""

from __future__ import annotations

import sqlalchemy as sa

from backend.analysis.pgx_guidelines import (
    PGX_SOURCES_PMID,
    assess_sample_pgx_guidelines,
    lookup_guideline_sources,
)
from backend.db.sample_schema import create_sample_tables
from backend.db.tables import findings


def _sample_with_alerts(pairs: list[tuple[str, str]]) -> sa.Engine:
    engine = sa.create_engine("sqlite://")
    create_sample_tables(engine)
    rows = [
        {
            "module": "pharmacogenomics",
            "category": "prescribing_alert",
            "gene_symbol": g,
            "drug": d,
            "metabolizer_status": "Poor Metabolizer",
            "finding_text": f"{g}/{d} alert",
        }
        for g, d in pairs
    ]
    with engine.begin() as conn:
        conn.execute(findings.insert(), rows)
    return engine


class TestLookup:
    def test_known_pair_full_sources(self) -> None:
        s = lookup_guideline_sources("CYP2C19", "clopidogrel")
        assert s["pharmgkb_loe"] == "1A"
        assert s["dpwg_guideline"] is True
        assert s["fda_pgx_level"] == "Actionable PGx"

    def test_case_insensitive_drug(self) -> None:
        assert lookup_guideline_sources("CYP2C19", "Clopidogrel")["pharmgkb_loe"] == "1A"

    def test_ondansetron_has_no_dpwg(self) -> None:
        s = lookup_guideline_sources("CYP2D6", "ondansetron")
        assert s["dpwg_guideline"] is False
        assert s["fda_pgx_level"] == "No Clinical PGx"

    def test_nat2_isoniazid_is_1b_no_dpwg(self) -> None:
        s = lookup_guideline_sources("NAT2", "isoniazid")
        assert s["pharmgkb_loe"] == "1B"
        assert s["dpwg_guideline"] is False

    def test_atazanavir_has_no_fda_label(self) -> None:
        # FDA atazanavir label is CYP2C19, not UGT1A1 → no FDA level for this pair.
        s = lookup_guideline_sources("UGT1A1", "atazanavir")
        assert s["pharmgkb_loe"] == "1A"
        assert s["dpwg_guideline"] is True
        assert s["fda_pgx_level"] is None

    def test_uncurated_pair_returns_none(self) -> None:
        assert lookup_guideline_sources("BRCA1", "aspirin") is None
        assert lookup_guideline_sources(None, "x") is None


class TestAssessSample:
    def test_joins_alerts_to_sources(self) -> None:
        engine = _sample_with_alerts([("CYP2C19", "clopidogrel"), ("DPYD", "fluorouracil")])
        result = assess_sample_pgx_guidelines(engine)
        by_gene = {a["gene_symbol"]: a for a in result["alerts"]}
        assert by_gene["CYP2C19"]["pharmgkb_loe"] == "1A"
        assert by_gene["CYP2C19"]["has_sources"] is True
        assert by_gene["DPYD"]["fda_pgx_level"] == "Testing Required"

    def test_uncurated_alert_flagged_not_dropped(self) -> None:
        engine = _sample_with_alerts([("FOO", "bardrug")])
        result = assess_sample_pgx_guidelines(engine)
        assert len(result["alerts"]) == 1
        a = result["alerts"][0]
        assert a["has_sources"] is False
        assert a["pharmgkb_loe"] is None

    def test_only_prescribing_alerts_considered(self) -> None:
        engine = _sample_with_alerts([("CYP2C19", "clopidogrel")])
        # Add a non-prescribing finding that must be ignored.
        with engine.begin() as conn:
            conn.execute(
                findings.insert().values(
                    module="cancer", category="prs", gene_symbol="X", finding_text="not an alert"
                )
            )
        result = assess_sample_pgx_guidelines(engine)
        assert len(result["alerts"]) == 1

    def test_context_only_disclosure_and_citation(self) -> None:
        engine = _sample_with_alerts([("CYP2C19", "clopidogrel")])
        result = assess_sample_pgx_guidelines(engine)
        assert result["context_only"] is True
        assert result["note"]
        assert PGX_SOURCES_PMID in result["pmid_citations"]


class TestCuratedDataIntegrity:
    def test_all_rows_have_valid_loe(self) -> None:
        from backend.analysis.pgx_guidelines import _load_sources

        sources = _load_sources()
        assert sources  # guard against a vacuous pass on an empty/missing CSV
        for (gene, drug), row in sources.items():
            assert row["pharmgkb_loe"] in {"1A", "1B", "2A", "2B", "3", "4"}, (gene, drug)
            assert isinstance(row["dpwg_guideline"], bool)

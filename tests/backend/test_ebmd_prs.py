"""Tests for SW-B7: heel-eBMD polygenic score (BYO, not a DXA/FRAX substitute).

Covers:
  - BYO degradation: no score DB, or DB lacking the non-commercial gSOS score
    → score_ebmd_prs returns None.
  - When the gSOS score IS installed, it is selected (bundle_only=False) + scored.
  - store_ebmd_findings stores the PRS (low-coverage) / clears stale on absence.
"""

from __future__ import annotations

import json

import sqlalchemy as sa

from backend.analysis.ebmd_prs import (
    EBMD_PGS_ID,
    score_ebmd_prs,
    store_ebmd_findings,
)
from backend.annotation.pgs_catalog import (
    create_pgs_tables,
    pgs_score_metadata,
    pgs_score_weights,
)
from backend.db.tables import annotated_variants, findings


def _pgs_engine(with_gsos: bool) -> sa.Engine:
    """A score DB that optionally contains the BYO gSOS score (PGS000657)."""
    engine = sa.create_engine("sqlite://")
    create_pgs_tables(engine)
    if with_gsos:
        with engine.begin() as conn:
            conn.execute(
                sa.insert(pgs_score_metadata),
                [
                    {
                        "pgs_id": EBMD_PGS_ID,
                        "pgs_name": "gSOS",
                        "trait_reported": "eBMD",
                        "trait_efo": "EFO_0009270",
                        "genome_build": "GRCh37",
                        "variants_number": 3,
                        "weight_type": "beta",
                        "license": "CC-BY-NC-ND-4.0",
                        "license_bundle_ok": 0,
                        "citation": "Forgetta 2020",
                        "pgp_id": None,
                    }
                ],
            )
            conn.execute(
                sa.insert(pgs_score_weights),
                [
                    {
                        "pgs_id": EBMD_PGS_ID,
                        "rsid": f"rsB{i}",
                        "chrom": "1",
                        "pos": 700 + i,
                        "effect_allele": "A",
                        "other_allele": "G",
                        "effect_weight": 0.1,
                    }
                    for i in range(3)
                ],
            )
    return engine


def _seed(engine: sa.Engine) -> None:
    with engine.begin() as conn:
        conn.execute(
            sa.insert(annotated_variants),
            [
                {
                    "rsid": "rsB0",
                    "chrom": "1",
                    "pos": 700,
                    "genotype": "AA",
                    "gnomad_af_global": 0.2,
                    "annotation_coverage": 0,
                }
            ],
        )


class TestByoAvailability:
    def test_none_without_score_db(self, sample_engine: sa.Engine) -> None:
        assert score_ebmd_prs(sample_engine, None, "EUR") is None

    def test_none_when_gsos_not_installed(self, sample_engine: sa.Engine) -> None:
        # DB present but the non-commercial gSOS score was not fetched.
        assert score_ebmd_prs(sample_engine, _pgs_engine(with_gsos=False), "EUR") is None

    def test_scored_when_gsos_installed(self, sample_engine: sa.Engine) -> None:
        _seed(sample_engine)
        prs = score_ebmd_prs(sample_engine, _pgs_engine(with_gsos=True), "EUR")
        assert prs is not None
        assert prs.pgs_id == EBMD_PGS_ID
        assert prs.calibrated is False  # uncalibrated (percentile withheld)
        # The Forgetta 2020 PMID reaches the result via the PGS registry's
        # source_pmid for PGS000657 (the single source of truth), not a
        # module-level constant — a dead duplicate of it was removed in #671.
        assert prs.source_pmid == "32614825"


class TestStore:
    def test_stores_when_available(self, sample_engine: sa.Engine) -> None:
        _seed(sample_engine)
        prs = score_ebmd_prs(sample_engine, _pgs_engine(with_gsos=True), "EUR")
        assert store_ebmd_findings(prs, sample_engine) == 1
        with sample_engine.connect() as conn:
            rows = conn.execute(
                sa.select(findings).where(
                    findings.c.module == "ebmd", findings.c.category == "prs"
                )
            ).fetchall()
        assert len(rows) == 1
        # The stored finding's machine-readable citations still carry the Forgetta
        # 2020 PMID, sourced from the PGS registry (source_pmid for PGS000657) —
        # not from the dead module-level constant removed in #671. Deletion is
        # behaviour-preserving for the user-facing citation.
        assert "32614825" in json.loads(rows[0].pmid_citations)

    def test_absence_clears_stale(self, sample_engine: sa.Engine) -> None:
        _seed(sample_engine)
        # First store a real finding...
        prs = score_ebmd_prs(sample_engine, _pgs_engine(with_gsos=True), "EUR")
        store_ebmd_findings(prs, sample_engine)
        # ...then a re-run where the BYO score is gone clears it.
        assert store_ebmd_findings(None, sample_engine) == 0
        with sample_engine.connect() as conn:
            rows = conn.execute(
                sa.select(findings).where(
                    findings.c.module == "ebmd", findings.c.category == "prs"
                )
            ).fetchall()
        assert rows == []

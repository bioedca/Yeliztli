"""Tests for the HLA proxy lookup table and loader.

Covers:
- Table creation via reference_metadata
- JSON data integrity (all 6 HLA alleles present)
- Bulk loader idempotency
- Index queries by proxy_rsid and hla_allele
- Ancestry-specific r² values
"""

from __future__ import annotations

import json
from pathlib import Path

import sqlalchemy as sa
from fastapi.testclient import TestClient

from backend.data.hla_proxy_loader import load_hla_proxy_data
from backend.db.tables import hla_proxy_lookup, reference_metadata

_JSON_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "backend"
    / "data"
    / "panels"
    / "hla_proxy_lookup.json"
)


# ═══════════════════════════════════════════════════════════════════════
# JSON data integrity
# ═══════════════════════════════════════════════════════════════════════


class TestHLAProxyJSON:
    """Validate the curated hla_proxy_lookup.json bundle."""

    def test_json_loads_without_error(self) -> None:
        with open(_JSON_PATH, encoding="utf-8") as fh:
            data = json.load(fh)
        assert "entries" in data
        assert len(data["entries"]) > 0

    def test_all_required_hla_alleles_present(self) -> None:
        with open(_JSON_PATH, encoding="utf-8") as fh:
            data = json.load(fh)
        alleles = {e["hla_allele"] for e in data["entries"]}
        # HLA-B*15:02 is intentionally absent from this r²-keyed lookup: rs144012689's
        # proxy performance is sensitivity/specificity, not an LD r², so its sourced
        # metric lives in the allergy_panel coverage_note rather than a fabricated r²
        # here (#935; see test_allergy_panel.py).
        expected = {
            "HLA-B*57:01",
            "HLA-A*31:01",
            "HLA-B*58:01",
            "HLA-DQ2",
            "HLA-DQ8",
        }
        assert expected == alleles

    def test_all_entries_have_required_fields(self) -> None:
        with open(_JSON_PATH, encoding="utf-8") as fh:
            data = json.load(fh)
        required = {"hla_allele", "proxy_rsid", "r_squared", "ancestry_pop"}
        for entry in data["entries"]:
            missing = required - set(entry.keys())
            assert not missing, f"Entry missing {missing}: {entry}"

    def test_r_squared_values_in_range(self) -> None:
        with open(_JSON_PATH, encoding="utf-8") as fh:
            data = json.load(fh)
        for entry in data["entries"]:
            r2 = entry["r_squared"]
            assert 0.0 < r2 <= 1.0, f"r² out of range: {r2} for {entry}"

    def test_proxy_rsids_start_with_rs(self) -> None:
        with open(_JSON_PATH, encoding="utf-8") as fh:
            data = json.load(fh)
        for entry in data["entries"]:
            assert entry["proxy_rsid"].startswith("rs"), f"Invalid rsid: {entry['proxy_rsid']}"

    def test_version_field_present(self) -> None:
        with open(_JSON_PATH, encoding="utf-8") as fh:
            data = json.load(fh)
        assert data.get("version") == "1.0.0"


# ═══════════════════════════════════════════════════════════════════════
# Table schema
# ═══════════════════════════════════════════════════════════════════════


class TestHLAProxyTable:
    """Validate the hla_proxy_lookup SQLAlchemy Core table definition."""

    def test_table_in_reference_metadata(self) -> None:
        assert "hla_proxy_lookup" in reference_metadata.tables

    def test_required_columns_exist(self) -> None:
        cols = {c.name for c in hla_proxy_lookup.columns}
        expected = {
            "id",
            "hla_allele",
            "proxy_rsid",
            "r_squared",
            "ancestry_pop",
            "clinical_context",
            "pmid",
        }
        assert expected == cols

    def test_table_creates_in_sqlite(self, reference_engine: sa.Engine) -> None:
        with reference_engine.connect() as conn:
            result = conn.execute(
                sa.text(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='hla_proxy_lookup'"
                )
            )
            assert result.fetchone() is not None


# ═══════════════════════════════════════════════════════════════════════
# Loader
# ═══════════════════════════════════════════════════════════════════════


class TestHLAProxyLoader:
    """Validate bulk loading from JSON into the hla_proxy_lookup table."""

    def test_load_inserts_all_rows(self, reference_engine: sa.Engine) -> None:
        count = load_hla_proxy_data(reference_engine, json_path=_JSON_PATH)
        with reference_engine.connect() as conn:
            rows = conn.execute(sa.select(hla_proxy_lookup)).fetchall()
        assert len(rows) == count
        assert count > 0

    def test_load_is_idempotent(self, reference_engine: sa.Engine) -> None:
        count1 = load_hla_proxy_data(reference_engine, json_path=_JSON_PATH)
        count2 = load_hla_proxy_data(reference_engine, json_path=_JSON_PATH)
        assert count1 == count2
        with reference_engine.connect() as conn:
            rows = conn.execute(sa.select(hla_proxy_lookup)).fetchall()
        assert len(rows) == count1

    def test_load_empty_json(self, reference_engine: sa.Engine, tmp_path: Path) -> None:
        empty = tmp_path / "empty.json"
        empty.write_text('{"entries": []}', encoding="utf-8")
        count = load_hla_proxy_data(reference_engine, json_path=empty)
        assert count == 0


# ═══════════════════════════════════════════════════════════════════════
# Query patterns
# ═══════════════════════════════════════════════════════════════════════


class TestHLAProxyQueries:
    """Validate lookup queries against the loaded table."""

    def _seed(self, engine: sa.Engine) -> None:
        load_hla_proxy_data(engine, json_path=_JSON_PATH)

    def test_lookup_by_proxy_rsid(self, reference_engine: sa.Engine) -> None:
        self._seed(reference_engine)
        with reference_engine.connect() as conn:
            rows = conn.execute(
                sa.select(hla_proxy_lookup).where(hla_proxy_lookup.c.proxy_rsid == "rs2395029")
            ).fetchall()
        assert len(rows) >= 1
        alleles = {r.hla_allele for r in rows}
        assert "HLA-B*57:01" in alleles

    def test_lookup_by_hla_allele(self, reference_engine: sa.Engine) -> None:
        self._seed(reference_engine)
        with reference_engine.connect() as conn:
            rows = conn.execute(
                sa.select(hla_proxy_lookup).where(hla_proxy_lookup.c.hla_allele == "HLA-B*58:01")
            ).fetchall()
        assert len(rows) >= 1
        pops = {r.ancestry_pop for r in rows}
        # rs9263726-HLA-B*58:01 LD is recorded with source-matched population labels
        # (Zhang 2018, #333), not fabricated continental EUR/EAS/AFR bins.
        assert "Han Chinese" in pops
        assert {"EUR", "EAS", "AFR"}.isdisjoint(pops)

    def test_ancestry_specific_r_squared(self, reference_engine: sa.Engine) -> None:
        self._seed(reference_engine)
        with reference_engine.connect() as conn:
            eur_row = conn.execute(
                sa.select(hla_proxy_lookup).where(
                    sa.and_(
                        hla_proxy_lookup.c.hla_allele == "HLA-B*57:01",
                        hla_proxy_lookup.c.ancestry_pop == "EUR",
                    )
                )
            ).fetchone()
            afr_row = conn.execute(
                sa.select(hla_proxy_lookup).where(
                    sa.and_(
                        hla_proxy_lookup.c.hla_allele == "HLA-B*57:01",
                        hla_proxy_lookup.c.ancestry_pop == "AFR",
                    )
                )
            ).fetchone()
        assert eur_row is not None
        assert afr_row is not None
        # EUR r² should be higher than AFR for HLA-B*57:01
        assert eur_row.r_squared > afr_row.r_squared

    def test_celiac_dq2_dq8_present(self, reference_engine: sa.Engine) -> None:
        self._seed(reference_engine)
        with reference_engine.connect() as conn:
            dq2 = conn.execute(
                sa.select(hla_proxy_lookup).where(hla_proxy_lookup.c.hla_allele == "HLA-DQ2")
            ).fetchall()
            dq8 = conn.execute(
                sa.select(hla_proxy_lookup).where(hla_proxy_lookup.c.hla_allele == "HLA-DQ8")
            ).fetchall()
        assert len(dq2) >= 1
        assert len(dq8) >= 1
        # Verify celiac context
        assert any(r.clinical_context and "celiac" in r.clinical_context.lower() for r in dq2)
        assert any(r.clinical_context and "celiac" in r.clinical_context.lower() for r in dq8)

    def test_all_clinical_contexts_non_empty(self, reference_engine: sa.Engine) -> None:
        self._seed(reference_engine)
        with reference_engine.connect() as conn:
            rows = conn.execute(sa.select(hla_proxy_lookup)).fetchall()
        for row in rows:
            assert row.clinical_context, f"Missing clinical_context for {row.hla_allele}"

    def test_all_pmids_non_empty(self, reference_engine: sa.Engine) -> None:
        self._seed(reference_engine)
        with reference_engine.connect() as conn:
            rows = conn.execute(sa.select(hla_proxy_lookup)).fetchall()
        for row in rows:
            assert row.pmid, f"Missing pmid for {row.hla_allele}"


# ═══════════════════════════════════════════════════════════════════════
# Startup wiring (#868)
# ═══════════════════════════════════════════════════════════════════════


class TestStartupWiring:
    """The loader must run on the app lifespan, so a freshly started deployment has
    a populated table and the allergy enrichment actually resolves — not the silent
    empty-table no-op that shipped before (the table was created by migration 003
    but nothing ever called the loader; #868)."""

    def test_lifespan_populates_table(self, test_client: TestClient) -> None:
        # test_client triggers the app lifespan (create_app + TestClient startup).
        from backend.db.connection import get_registry

        with open(_JSON_PATH, encoding="utf-8") as fh:
            expected = len(json.load(fh)["entries"])

        registry = get_registry()
        with registry.reference_engine.connect() as conn:
            count = conn.execute(
                sa.select(sa.func.count()).select_from(hla_proxy_lookup)
            ).scalar_one()
        assert count == expected  # every curated entry loaded at startup, not 0

    def test_lifespan_enrichment_resolves(self, test_client: TestClient) -> None:
        # The allergy consumer returns the curated r²/ancestry record instead of the
        # production {} once the table is populated by startup.
        from backend.analysis.allergy import _fetch_hla_proxy_info
        from backend.db.connection import get_registry

        registry = get_registry()
        info = _fetch_hla_proxy_info(["rs2395029"], registry.reference_engine)
        assert "rs2395029" in info  # HLA-B*57:01 / abacavir proxy
        assert info["rs2395029"].hla_allele == "HLA-B*57:01"
        assert info["rs2395029"].r_squared_by_pop  # ancestry-keyed r² present

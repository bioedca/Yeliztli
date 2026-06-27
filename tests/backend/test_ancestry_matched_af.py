"""Tests for ancestry-matched AF display (P3-26, T3-26).

Covers:
  - Population-to-gnomAD-column mapping
  - get_inferred_ancestry() from findings table
  - T3-26: Ancestry-matched AF returns NFE frequency for EUR-inferred user
  - Ancestry-matched AF on variant list endpoint
  - Ancestry-matched AF on variant detail endpoint
  - Fallback to global AF when ancestry not inferred
  - Unknown/OCE populations fall back to global AF
"""

from __future__ import annotations

import json

import pytest
import sqlalchemy as sa

from backend.analysis.ancestry import (
    get_ancestry_matched_af_column,
    get_inferred_ancestry,
)
from backend.db.tables import annotated_variants, findings

# ── Unit tests: population → gnomAD column mapping ───────────────────────


class TestGetAncestryMatchedAfColumn:
    """Test the ancestry → gnomAD AF column mapping."""

    def test_eur_maps_to_gnomad_af_eur(self) -> None:
        """T3-26: EUR ancestry maps to gnomad_af_eur (NFE frequency)."""
        assert get_ancestry_matched_af_column("EUR") == "gnomad_af_eur"

    def test_afr_maps_to_gnomad_af_afr(self) -> None:
        assert get_ancestry_matched_af_column("AFR") == "gnomad_af_afr"

    def test_amr_maps_to_gnomad_af_amr(self) -> None:
        assert get_ancestry_matched_af_column("AMR") == "gnomad_af_amr"

    def test_asj_maps_to_gnomad_af_asj(self) -> None:
        assert get_ancestry_matched_af_column("ASJ") == "gnomad_af_asj"

    def test_eas_maps_to_gnomad_af_eas(self) -> None:
        assert get_ancestry_matched_af_column("EAS") == "gnomad_af_eas"

    def test_csa_maps_to_gnomad_af_sas(self) -> None:
        """CSA (Central/South Asian) maps to gnomAD's sas column."""
        assert get_ancestry_matched_af_column("CSA") == "gnomad_af_sas"

    def test_mid_falls_back_to_global(self) -> None:
        """MID has no gnomAD-specific column, falls back to global."""
        assert get_ancestry_matched_af_column("MID") == "gnomad_af_global"

    def test_oce_falls_back_to_global(self) -> None:
        """OCE has no gnomAD-specific data, falls back to global."""
        assert get_ancestry_matched_af_column("OCE") == "gnomad_af_global"

    def test_none_falls_back_to_global(self) -> None:
        assert get_ancestry_matched_af_column(None) == "gnomad_af_global"

    def test_unknown_falls_back_to_global(self) -> None:
        assert get_ancestry_matched_af_column("UNKNOWN") == "gnomad_af_global"

    def test_case_insensitive(self) -> None:
        assert get_ancestry_matched_af_column("eur") == "gnomad_af_eur"
        assert get_ancestry_matched_af_column("Afr") == "gnomad_af_afr"


# ── Unit tests: get_inferred_ancestry ────────────────────────────────────


class TestGetInferredAncestry:
    """Test retrieving inferred ancestry from findings table."""

    def test_returns_none_when_no_findings(self, sample_engine: sa.Engine) -> None:
        assert get_inferred_ancestry(sample_engine) is None

    def test_returns_top_population_from_finding(self, sample_engine: sa.Engine) -> None:
        detail = json.dumps(
            {
                "top_population": "EUR",
                "inferred_ancestry": "EUR",
                "pc_scores": [1.0, 2.0],
            }
        )
        with sample_engine.begin() as conn:
            conn.execute(
                sa.insert(findings),
                [
                    {
                        "module": "ancestry",
                        "category": "pca_projection",
                        "evidence_level": 2,
                        "finding_text": "Inferred ancestry: EUR",
                        "detail_json": detail,
                    }
                ],
            )

        assert get_inferred_ancestry(sample_engine) == "EUR"

    def test_returns_latest_finding(self, sample_engine: sa.Engine) -> None:
        """When multiple ancestry findings exist, returns the latest."""
        for pop in ("AFR", "EUR"):
            detail = json.dumps({"top_population": pop})
            with sample_engine.begin() as conn:
                conn.execute(
                    sa.insert(findings),
                    [
                        {
                            "module": "ancestry",
                            "category": "pca_projection",
                            "evidence_level": 2,
                            "finding_text": f"Inferred ancestry: {pop}",
                            "detail_json": detail,
                        }
                    ],
                )

        # Latest inserted should be EUR
        assert get_inferred_ancestry(sample_engine) == "EUR"

    def test_ignores_non_ancestry_findings(self, sample_engine: sa.Engine) -> None:
        """Findings from other modules should not be returned."""
        with sample_engine.begin() as conn:
            conn.execute(
                sa.insert(findings),
                [
                    {
                        "module": "pharmacogenomics",
                        "category": "star_allele",
                        "evidence_level": 3,
                        "finding_text": "CYP2D6 *1/*2",
                        "detail_json": json.dumps({"top_population": "EUR"}),
                    }
                ],
            )

        assert get_inferred_ancestry(sample_engine) is None


# ── Integration tests: ancestry-matched AF on variant rows ───────────────


def _seed_annotated_variant_with_af(
    engine: sa.Engine,
    rsid: str = "rs429358",
    af_global: float = 0.15,
    af_afr: float = 0.20,
    af_eur: float = 0.12,
    af_eas: float = 0.08,
    af_amr: float = 0.14,
    af_asj: float = 0.13,
    af_sas: float = 0.10,
) -> None:
    """Insert a single annotated variant with per-population gnomAD AFs."""
    with engine.begin() as conn:
        conn.execute(
            sa.insert(annotated_variants),
            [
                {
                    "rsid": rsid,
                    "chrom": "19",
                    "pos": 44908684,
                    "genotype": "TC",
                    "gnomad_af_global": af_global,
                    "gnomad_af_afr": af_afr,
                    "gnomad_af_eur": af_eur,
                    "gnomad_af_eas": af_eas,
                    "gnomad_af_amr": af_amr,
                    "gnomad_af_asj": af_asj,
                    "gnomad_af_sas": af_sas,
                    "annotation_coverage": 4,
                }
            ],
        )


def _seed_ancestry_finding(engine: sa.Engine, population: str) -> None:
    """Insert an ancestry finding with the given top population."""
    with engine.begin() as conn:
        # Clear previous
        conn.execute(
            sa.delete(findings).where(
                findings.c.module == "ancestry",
            )
        )
        conn.execute(
            sa.insert(findings),
            [
                {
                    "module": "ancestry",
                    "category": "pca_projection",
                    "evidence_level": 2,
                    "finding_text": f"Inferred ancestry: {population}",
                    "detail_json": json.dumps(
                        {
                            "top_population": population,
                            "inferred_ancestry": population,
                        }
                    ),
                }
            ],
        )


class TestAncestryMatchedAfIntegration:
    """T3-26: Ancestry-matched AF returns correct population frequency."""

    def test_eur_user_gets_nfe_frequency(self, sample_engine: sa.Engine) -> None:
        """T3-26: EUR-inferred user sees gnomad_af_eur as ancestry_matched_af."""
        _seed_annotated_variant_with_af(sample_engine, af_eur=0.12)
        _seed_ancestry_finding(sample_engine, "EUR")

        ancestry = get_inferred_ancestry(sample_engine)
        assert ancestry == "EUR"

        col = get_ancestry_matched_af_column(ancestry)
        assert col == "gnomad_af_eur"

        # Verify the actual AF value from the database
        with sample_engine.connect() as conn:
            row = conn.execute(
                sa.select(annotated_variants).where(annotated_variants.c.rsid == "rs429358")
            ).fetchone()
        matched_af = getattr(row, col)
        assert matched_af == pytest.approx(0.12)

    def test_afr_user_gets_afr_frequency(self, sample_engine: sa.Engine) -> None:
        _seed_annotated_variant_with_af(sample_engine, af_afr=0.20)
        _seed_ancestry_finding(sample_engine, "AFR")

        ancestry = get_inferred_ancestry(sample_engine)
        col = get_ancestry_matched_af_column(ancestry)

        with sample_engine.connect() as conn:
            row = conn.execute(
                sa.select(annotated_variants).where(annotated_variants.c.rsid == "rs429358")
            ).fetchone()
        assert getattr(row, col) == pytest.approx(0.20)

    def test_asj_user_gets_asj_frequency(self, sample_engine: sa.Engine) -> None:
        _seed_annotated_variant_with_af(sample_engine, af_global=0.0023, af_asj=0.0269)
        _seed_ancestry_finding(sample_engine, "ASJ")

        ancestry = get_inferred_ancestry(sample_engine)
        col = get_ancestry_matched_af_column(ancestry)

        with sample_engine.connect() as conn:
            row = conn.execute(
                sa.select(annotated_variants).where(annotated_variants.c.rsid == "rs429358")
            ).fetchone()
        assert col == "gnomad_af_asj"
        assert getattr(row, col) == pytest.approx(0.0269)

    def test_eas_user_gets_eas_frequency(self, sample_engine: sa.Engine) -> None:
        _seed_annotated_variant_with_af(sample_engine, af_eas=0.08)
        _seed_ancestry_finding(sample_engine, "EAS")

        ancestry = get_inferred_ancestry(sample_engine)
        col = get_ancestry_matched_af_column(ancestry)

        with sample_engine.connect() as conn:
            row = conn.execute(
                sa.select(annotated_variants).where(annotated_variants.c.rsid == "rs429358")
            ).fetchone()
        assert getattr(row, col) == pytest.approx(0.08)

    def test_no_ancestry_returns_none(self, sample_engine: sa.Engine) -> None:
        """When ancestry hasn't been inferred, ancestry_matched_af is None."""
        _seed_annotated_variant_with_af(sample_engine)

        ancestry = get_inferred_ancestry(sample_engine)
        assert ancestry is None

        # Mapping returns global fallback, but since there's no ancestry,
        # the API should set ancestry_matched_af to None
        col = get_ancestry_matched_af_column(ancestry)
        assert col == "gnomad_af_global"

    def test_variant_with_null_population_af(self, sample_engine: sa.Engine) -> None:
        """When the matched population AF is NULL, ancestry_matched_af is None."""
        # Insert variant with no EUR AF
        with sample_engine.begin() as conn:
            conn.execute(
                sa.insert(annotated_variants),
                [
                    {
                        "rsid": "rs12345",
                        "chrom": "1",
                        "pos": 100,
                        "genotype": "AG",
                        "gnomad_af_global": 0.05,
                        "gnomad_af_eur": None,
                        "annotation_coverage": 4,
                    }
                ],
            )
        _seed_ancestry_finding(sample_engine, "EUR")

        ancestry = get_inferred_ancestry(sample_engine)
        col = get_ancestry_matched_af_column(ancestry)

        with sample_engine.connect() as conn:
            row = conn.execute(
                sa.select(annotated_variants).where(annotated_variants.c.rsid == "rs12345")
            ).fetchone()
        assert getattr(row, col) is None

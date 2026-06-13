"""Tests for SW-B8: opt-in breast absolute-risk overlay + Alembic migration 012.

Covers:
  - Consent set/get round-trip (reference DB).
  - Overlay is gated: pre-consent returns only the opt-in prompt (no figures);
    post-consent returns the SEER baseline + CanRisk handoff.
  - Monogenic carriers surface published genotype-class penetrance (BRCA1/2).
  - Migration 012 creates/drops risk_overlay_consent (round-trip).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic.config import Config

from alembic import command
from backend.analysis.breast_absolute_risk import (
    FEATURE,
    build_breast_absolute_risk,
    get_consent,
    set_consent,
)
from backend.db.tables import findings, reference_metadata, risk_overlay_consent


def _ref_engine() -> sa.Engine:
    engine = sa.create_engine("sqlite://")
    reference_metadata.create_all(engine)
    return engine


def _insert_breast_monogenic(engine: sa.Engine, gene: str) -> None:
    with engine.begin() as conn:
        conn.execute(
            sa.insert(findings),
            [
                {
                    "module": "cancer",
                    "category": "monogenic_variant",
                    "gene_symbol": gene,
                    "zygosity": "het",
                    "evidence_level": 4,
                    "finding_text": f"{gene} P/LP",
                }
            ],
        )


class TestConsent:
    def test_default_not_consented(self) -> None:
        assert get_consent(_ref_engine(), 1) is False

    def test_set_and_get(self) -> None:
        eng = _ref_engine()
        set_consent(eng, 1, True)
        assert get_consent(eng, 1) is True

    def test_opt_out_after_opt_in(self) -> None:
        eng = _ref_engine()
        set_consent(eng, 1, True)
        set_consent(eng, 1, False)  # upsert, not duplicate
        assert get_consent(eng, 1) is False
        with eng.connect() as conn:
            n = conn.execute(
                sa.select(sa.func.count())
                .select_from(risk_overlay_consent)
                .where(
                    risk_overlay_consent.c.sample_id == 1,
                    risk_overlay_consent.c.feature == FEATURE,
                )
            ).scalar()
        assert n == 1  # single row, not duplicated


class TestOverlayGating:
    def test_pre_consent_no_figures(self, sample_engine: sa.Engine) -> None:
        out = build_breast_absolute_risk(sample_engine, consented=False)
        assert out["consented"] is False
        assert out["opt_in_required"] is True
        assert "population_baseline" not in out  # no risk figures pre-consent
        assert "disclaimer" in out

    def test_post_consent_population_baseline(self, sample_engine: sa.Engine) -> None:
        # XX/female context: the SEER female baseline applies.
        out = build_breast_absolute_risk(sample_engine, consented=True, inferred_sex="XX")
        assert out["consented"] is True
        assert out["sex_context"] == "female"
        assert out["population_baseline"]["lifetime_risk_pct"] == 12.9
        assert out["has_monogenic"] is False
        assert out["canrisk"]["url"] == "https://www.canrisk.org"

    def test_post_consent_brca_carrier_penetrance(self, sample_engine: sa.Engine) -> None:
        _insert_breast_monogenic(sample_engine, "BRCA1")
        out = build_breast_absolute_risk(sample_engine, consented=True, inferred_sex="XX")
        assert out["has_monogenic"] is True
        brca1 = next(m for m in out["monogenic"] if m["gene"] == "BRCA1")
        assert brca1["cumulative_risk_to_80_pct"] == 72
        assert brca1["pmid"] == "28632866"

    def test_moderate_gene_has_no_fabricated_number(self, sample_engine: sa.Engine) -> None:
        _insert_breast_monogenic(sample_engine, "ATM")
        out = build_breast_absolute_risk(sample_engine, consented=True, inferred_sex="XX")
        atm = next(m for m in out["monogenic"] if m["gene"] == "ATM")
        assert atm["cumulative_risk_to_80_pct"] is None  # no fabricated figure
        assert "note" in atm


class TestSexGating:
    """The female SEER baseline + BRCA penetrance must never reach a non-female
    sample (gh #151)."""

    def test_xy_male_suppresses_female_baseline_and_penetrance(
        self, sample_engine: sa.Engine
    ) -> None:
        _insert_breast_monogenic(sample_engine, "BRCA1")
        out = build_breast_absolute_risk(sample_engine, consented=True, inferred_sex="XY")
        assert out["sex_context"] == "male"
        # No female SEER lifetime baseline for a male sample.
        assert "population_baseline" not in out
        brca1 = next(m for m in out["monogenic"] if m["gene"] == "BRCA1")
        # The female 72% figure must NOT be shown; male framing carries the context.
        assert brca1["cumulative_risk_to_80_pct"] is None
        assert "male" in brca1["note"].lower()
        assert "prostate" in out["sex_note"].lower()

    def test_xy_male_no_carrier_still_no_female_baseline(self, sample_engine: sa.Engine) -> None:
        out = build_breast_absolute_risk(sample_engine, consented=True, inferred_sex="XY")
        assert out["sex_context"] == "male"
        assert "population_baseline" not in out
        assert out["has_monogenic"] is False
        assert "do not apply to males" in out["sex_note"]

    def test_unknown_sex_withholds_numeric_figures(self, sample_engine: sa.Engine) -> None:
        _insert_breast_monogenic(sample_engine, "BRCA2")
        out = build_breast_absolute_risk(sample_engine, consented=True, inferred_sex="unknown")
        assert out["sex_context"] == "unresolved"
        assert "population_baseline" not in out
        brca2 = next(m for m in out["monogenic"] if m["gene"] == "BRCA2")
        assert brca2["cumulative_risk_to_80_pct"] is None
        assert "not resolved" in brca2["note"].lower()
        assert "withheld" in out["sex_note"].lower()

    def test_manual_review_is_unresolved(self, sample_engine: sa.Engine) -> None:
        out = build_breast_absolute_risk(
            sample_engine, consented=True, inferred_sex="manual_review"
        )
        assert out["sex_context"] == "unresolved"
        assert "population_baseline" not in out

    def test_default_none_is_unresolved_not_female(self, sample_engine: sa.Engine) -> None:
        # Defensive default: an un-threaded sex must NOT fall back to female figures.
        out = build_breast_absolute_risk(sample_engine, consented=True)
        assert out["sex_context"] == "unresolved"
        assert "population_baseline" not in out


class TestRecordedSexPrecedence:
    """Recorded individuals.biological_sex precedence over inference (gh #254)."""

    def test_recorded_only_resolves_context(self, sample_engine: sa.Engine) -> None:
        # No inference available, but a recorded XX resolves the female context.
        out = build_breast_absolute_risk(
            sample_engine, consented=True, inferred_sex=None, recorded_sex="XX"
        )
        assert out["sex_context"] == "female"
        assert out["sex_source"] == "recorded"
        assert out["resolved_sex"] == "XX"
        assert out["population_baseline"]["lifetime_risk_pct"] == 12.9
        assert out["sex_conflict"] is False

    def test_recorded_resolves_inconclusive_inference(self, sample_engine: sa.Engine) -> None:
        # Inference is 'unknown' (would withhold), but a recorded XX resolves it.
        out = build_breast_absolute_risk(
            sample_engine, consented=True, inferred_sex="unknown", recorded_sex="XX"
        )
        assert out["sex_context"] == "female"
        assert out["sex_source"] == "recorded"
        assert "population_baseline" in out
        assert out["sex_conflict"] is False

    def test_recorded_overrides_inferred_with_conflict_note(
        self, sample_engine: sa.Engine
    ) -> None:
        # Recorded XX is authoritative even over a confident XY inference; the
        # disagreement is surfaced as a conflict note.
        out = build_breast_absolute_risk(
            sample_engine, consented=True, inferred_sex="XY", recorded_sex="XX"
        )
        assert out["sex_context"] == "female"
        assert out["sex_source"] == "recorded"
        assert out["sex_conflict"] is True
        assert "recorded biological sex differs" in out["sex_note"]

    def test_inferred_used_when_no_recorded(self, sample_engine: sa.Engine) -> None:
        # Absent a recorded value, behaviour falls back to inference (gh #151).
        out = build_breast_absolute_risk(
            sample_engine, consented=True, inferred_sex="XY", recorded_sex=None
        )
        assert out["sex_context"] == "male"
        assert out["sex_source"] == "inferred"
        assert "population_baseline" not in out
        assert out["sex_conflict"] is False

    def test_recorded_absent_inferred_unknown_stays_unresolved(
        self, sample_engine: sa.Engine
    ) -> None:
        out = build_breast_absolute_risk(
            sample_engine, consented=True, inferred_sex="unknown", recorded_sex=None
        )
        assert out["sex_context"] == "unresolved"
        assert out["sex_source"] == "inferred"
        assert "population_baseline" not in out

    def test_get_recorded_biological_sex_via_individual_link(self) -> None:
        from backend.db.tables import individuals, samples
        from backend.services.sex_inference import get_recorded_biological_sex

        eng = _ref_engine()
        with eng.begin() as conn:
            conn.execute(
                sa.insert(individuals).values(id=7, display_name="P", biological_sex="XX")
            )
            conn.execute(
                sa.insert(samples).values(id=1, name="s", db_path="samples/s.db", individual_id=7)
            )
            # A sample with no linked individual.
            conn.execute(sa.insert(samples).values(id=2, name="s2", db_path="samples/s2.db"))
        assert get_recorded_biological_sex(eng, 1) == "XX"
        assert get_recorded_biological_sex(eng, 2) is None  # no linked individual
        assert get_recorded_biological_sex(eng, 999) is None  # unknown sample


class TestMigration012:
    def _cfg(self, db_path) -> Config:
        cfg = Config()
        cfg.set_main_option("script_location", "alembic")
        cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
        return cfg

    def test_upgrade_creates_then_downgrade_drops(self, tmp_path) -> None:
        db = tmp_path / "reference.db"
        command.upgrade(self._cfg(db), "012")
        insp = sa.inspect(sa.create_engine(f"sqlite:///{db}"))
        assert "risk_overlay_consent" in insp.get_table_names()
        indexes = {i["name"] for i in insp.get_indexes("risk_overlay_consent")}
        assert "idx_risk_overlay_consent_sample_feature" in indexes

        command.downgrade(self._cfg(db), "011")
        insp2 = sa.inspect(sa.create_engine(f"sqlite:///{db}"))
        assert "risk_overlay_consent" not in insp2.get_table_names()

"""Tests for the sex-chromosome aneuploidy (XXY) screen.

A possible-XXY call requires heterozygous non-PAR chrX calls (≥2 X chromosomes)
AND a present chrY, each judged only when enough probes were typed — so a single
stray Y probe on an XX sample stays indeterminate, never a false XXY. Turner /
XYY are explicitly out of scope (no copy-number data).
"""

from __future__ import annotations

import sqlalchemy as sa

from backend.analysis.sex_aneuploidy import (
    INDETERMINATE,
    MODULE,
    NO_SIGNAL,
    POSSIBLE_XXY,
    screen_aneuploidy,
    store_aneuploidy_findings,
)
from backend.db.tables import findings, raw_variants


def _seed(engine: sa.Engine, rows: list[dict]) -> None:
    if rows:
        with engine.begin() as conn:
            conn.execute(sa.insert(raw_variants), rows)


def _x_probes(n_het: int, n_hom: int) -> list[dict]:
    """Non-PAR chrX probes (pos well outside PAR1/PAR2)."""
    rows = []
    pos = 5_000_000
    for i in range(n_het):
        rows.append({"rsid": f"x_het{i}", "chrom": "X", "pos": pos, "genotype": "AG"})
        pos += 137
    for i in range(n_hom):
        rows.append({"rsid": f"x_hom{i}", "chrom": "X", "pos": pos, "genotype": "AA"})
        pos += 137
    return rows


def _x_hemi_probes(n: int) -> list[dict]:
    """Non-PAR chrX hemizygous single-char male calls (the 23andMe representation
    of a single X copy — one allele, not a padded diploid homozygote)."""
    rows = []
    pos = 7_000_000
    for i in range(n):
        rows.append({"rsid": f"x_hemi{i}", "chrom": "X", "pos": pos, "genotype": "A"})
        pos += 137
    return rows


def _y_probes(n_typed: int, n_nocall: int = 0) -> list[dict]:
    rows = []
    pos = 6_000_000
    for i in range(n_typed):
        rows.append({"rsid": f"y_t{i}", "chrom": "Y", "pos": pos, "genotype": "GG"})
        pos += 137
    for i in range(n_nocall):
        rows.append({"rsid": f"y_n{i}", "chrom": "Y", "pos": pos, "genotype": "--"})
        pos += 137
    return rows


class TestScreen:
    def test_possible_xxy(self, sample_engine: sa.Engine) -> None:
        _seed(sample_engine, _x_probes(60, 60) + _y_probes(60))
        r = screen_aneuploidy(sample_engine)
        assert r.outcome == POSSIBLE_XXY
        assert r.x_evaluable and r.y_evaluable

    def test_typical_xx_no_signal(self, sample_engine: sa.Engine) -> None:
        # X heterozygous, but chrY evaluable and NOT present (mostly no-call).
        _seed(sample_engine, _x_probes(60, 60) + _y_probes(8, 60))
        r = screen_aneuploidy(sample_engine)
        assert r.outcome == NO_SIGNAL

    def test_typical_xy_no_signal(self, sample_engine: sa.Engine) -> None:
        # X all homozygous (one X), chrY present → no XXY signal.
        _seed(sample_engine, _x_probes(0, 120) + _y_probes(60))
        r = screen_aneuploidy(sample_engine)
        assert r.outcome == NO_SIGNAL

    def test_twentythreeandme_male_hemizygous_x_no_signal(self, sample_engine: sa.Engine) -> None:
        """issue #504 — a 23andMe male reports non-PAR chrX as hemizygous
        single-char calls. Once those are counted as typed, a normal male is
        x-evaluable and screens as NO_SIGNAL; before the fix ``x_nonpar_typed``
        was 0, so every 23andMe male fell through to INDETERMINATE and the screen
        was silently suppressed."""
        _seed(sample_engine, _x_hemi_probes(120) + _y_probes(60))
        r = screen_aneuploidy(sample_engine)
        assert r.outcome == NO_SIGNAL
        assert r.x_evaluable and r.y_evaluable
        assert r.x_nonpar_het == 0

    def test_single_stray_y_probe_is_indeterminate(self, sample_engine: sa.Engine) -> None:
        # The golden-fixture shape: XX-like X het + ONE Y probe → must NOT call XXY.
        _seed(sample_engine, _x_probes(60, 60) + _y_probes(1))
        r = screen_aneuploidy(sample_engine)
        assert r.outcome == INDETERMINATE
        assert r.y_evaluable is False

    def test_thin_x_is_indeterminate(self, sample_engine: sa.Engine) -> None:
        _seed(sample_engine, _x_probes(5, 5) + _y_probes(60))
        r = screen_aneuploidy(sample_engine)
        assert r.outcome == INDETERMINATE
        assert r.x_evaluable is False


class TestStorage:
    def test_stores_screen_finding(self, sample_engine: sa.Engine) -> None:
        _seed(sample_engine, _x_probes(60, 60) + _y_probes(60))
        r = screen_aneuploidy(sample_engine)
        assert store_aneuploidy_findings(r, sample_engine) == 1
        with sample_engine.connect() as conn:
            row = conn.execute(sa.select(findings).where(findings.c.module == MODULE)).fetchone()
        assert row.evidence_level == 1
        assert row.clinvar_significance is None
        assert row.category == "aneuploidy_screen"
        text = row.finding_text.lower()
        assert "klinefelter" in text
        assert "screen" in text and "not a diagnosis" in text

    def test_negative_screen_states_turner_xyy_limits(self, sample_engine: sa.Engine) -> None:
        _seed(sample_engine, _x_probes(0, 120) + _y_probes(60))
        r = screen_aneuploidy(sample_engine)
        store_aneuploidy_findings(r, sample_engine)
        with sample_engine.connect() as conn:
            row = conn.execute(sa.select(findings).where(findings.c.module == MODULE)).fetchone()
        text = row.finding_text.lower()
        assert "turner" in text and "xyy" in text

    def test_store_is_idempotent(self, sample_engine: sa.Engine) -> None:
        _seed(sample_engine, _x_probes(60, 60) + _y_probes(60))
        r = screen_aneuploidy(sample_engine)
        store_aneuploidy_findings(r, sample_engine)
        store_aneuploidy_findings(r, sample_engine)
        with sample_engine.connect() as conn:
            n = conn.execute(
                sa.select(sa.func.count()).select_from(findings).where(findings.c.module == MODULE)
            ).scalar()
        assert n == 1

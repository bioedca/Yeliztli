"""rCRS MT position fallback in read_genotypes (#820).

When an MT locus declares a rCRS ``pos`` and neither its rsID nor a curated
alias yields a typed call, ``read_genotypes`` matches the variant by genomic
position — so an MT call typed only under a chip-specific i-ID (no curated
alias) is still read, vendor/chip-version independently. The fallback is
build-gated: applied ONLY for rCRS-coordinate samples (23andMe v4/v5), never
for v3/hg18 MT (CRS coordinates — a position match there would read a
*different* variant). Multiple probes at one position must agree, else the
position is left un-called (never a false call). Nuclear panels (no declared
``pos``) read exactly as before.
"""

from __future__ import annotations

import sqlalchemy as sa

from backend.analysis.lhon import load_lhon_panel
from backend.analysis.mt_rnr1 import load_mt_rnr1_panel
from backend.analysis.risk_genotype import (
    PROBE_ABSENT,
    PROBE_TYPED,
    read_genotypes,
)
from backend.db.tables import raw_variants, sample_metadata_table


def _set_format(engine: sa.Engine, file_format: str | None) -> None:
    with engine.begin() as conn:
        conn.execute(
            sample_metadata_table.insert().values(id=1, name="t", file_format=file_format)
        )


def _seed(engine: sa.Engine, rows: list[dict]) -> None:
    with engine.begin() as conn:
        conn.execute(sa.insert(raw_variants), rows)


def _mt(rsid: str, pos: int, gt: str) -> dict:
    return {"rsid": rsid, "chrom": "MT", "pos": pos, "genotype": gt}


# m.11778 (rs199476112) has NO curated alias, so an i-ID at its position
# exercises the position fallback in isolation (no alias path can find it).
_M11778 = "rs199476112"
_M14484 = "rs199476104"


class TestMtPositionFallback:
    def test_unaliased_iid_typed_by_position_on_v5(self, sample_engine: sa.Engine) -> None:
        _set_format(sample_engine, "23andme_v5")
        # A carrier typed only under a novel i-ID at the rCRS m.11778 position.
        _seed(sample_engine, [_mt("i9999991", 11778, "A")])
        readouts = read_genotypes(load_lhon_panel(), sample_engine)
        assert readouts[_M11778].status == PROBE_TYPED
        assert readouts[_M11778].genotype == "A"

    def test_v4_also_enabled(self, sample_engine: sa.Engine) -> None:
        _set_format(sample_engine, "23andme_v4")
        _seed(sample_engine, [_mt("i9999991", 11778, "G")])
        assert read_genotypes(load_lhon_panel(), sample_engine)[_M11778].status == PROBE_TYPED

    def test_v3_build_gate_blocks_position_fallback(self, sample_engine: sa.Engine) -> None:
        # v3 = Build 36 (hg18/CRS MT): a position match would mis-read a different
        # variant (e.g. v3 "MT 11778" is not m.11778G>A), so it must be denied.
        _set_format(sample_engine, "23andme_v3")
        _seed(sample_engine, [_mt("i9999991", 11778, "A")])
        assert read_genotypes(load_lhon_panel(), sample_engine)[_M11778].status == PROBE_ABSENT

    def test_unverified_format_denied(self, sample_engine: sa.Engine) -> None:
        # Deny-by-default: a format not verified rCRS-MT gets no position fallback.
        _set_format(sample_engine, "ancestrydna_v2")
        _seed(sample_engine, [_mt("i9999991", 11778, "A")])
        assert read_genotypes(load_lhon_panel(), sample_engine)[_M11778].status == PROBE_ABSENT

    def test_missing_format_row_denied(self, sample_engine: sa.Engine) -> None:
        # No sample_metadata row at all → deny (safe default, no exception).
        _seed(sample_engine, [_mt("i9999991", 11778, "A")])
        assert read_genotypes(load_lhon_panel(), sample_engine)[_M11778].status == PROBE_ABSENT

    def test_multi_probe_concordant_position_typed(self, sample_engine: sa.Engine) -> None:
        # Two distinct (non-alias) probes at the m.14484 position agree → typed.
        _set_format(sample_engine, "23andme_v5")
        _seed(sample_engine, [_mt("i9990001", 14484, "C"), _mt("i9990002", 14484, "C")])
        r = read_genotypes(load_lhon_panel(), sample_engine)[_M14484]
        assert r.status == PROBE_TYPED
        assert r.genotype == "C"

    def test_multi_probe_discordant_position_not_called(self, sample_engine: sa.Engine) -> None:
        # Conflicting probes at one position → ambiguous, never a false call.
        _set_format(sample_engine, "23andme_v5")
        _seed(sample_engine, [_mt("i9990001", 14484, "C"), _mt("i9990002", 14484, "T")])
        assert read_genotypes(load_lhon_panel(), sample_engine)[_M14484].status == PROBE_ABSENT

    def test_position_fallback_overrides_canonical_no_call(self, sample_engine: sa.Engine) -> None:
        # The #677 m.14484 shape generalized: the canonical rsID is a no-call at
        # the off-by-one 14485, but a typed (non-alias) probe at rCRS 14484 wins.
        _set_format(sample_engine, "23andme_v5")
        _seed(
            sample_engine,
            [
                {"rsid": _M14484, "chrom": "MT", "pos": 14485, "genotype": "--"},
                _mt("i9990003", 14484, "T"),
            ],
        )
        r = read_genotypes(load_lhon_panel(), sample_engine)[_M14484]
        assert r.status == PROBE_TYPED
        assert r.genotype == "T"

    def test_canonical_rsid_still_wins(self, sample_engine: sa.Engine) -> None:
        # A typed canonical rsID call is used directly (position not needed).
        _set_format(sample_engine, "23andme_v5")
        _seed(sample_engine, [{"rsid": _M11778, "chrom": "MT", "pos": 11778, "genotype": "A"}])
        r = read_genotypes(load_lhon_panel(), sample_engine)[_M11778]
        assert r.status == PROBE_TYPED
        assert r.genotype == "A"

    def test_absent_position_stays_off_chip(self, sample_engine: sa.Engine) -> None:
        # The fallback never fabricates a call: no probe at the position → absent.
        _set_format(sample_engine, "23andme_v5")
        _seed(sample_engine, [_mt("i9999991", 3460, "G")])  # m.3460 typed, m.11778 not
        readouts = read_genotypes(load_lhon_panel(), sample_engine)
        assert readouts[_M11778].status == PROBE_ABSENT  # nothing at 11778
        assert readouts["rs199476118"].status == PROBE_TYPED  # m.3460 typed by position

    def test_mt_rnr1_panel_also_benefits(self, sample_engine: sa.Engine) -> None:
        # The #669 panel: m.1555 typed under a novel i-ID at its rCRS position.
        _set_format(sample_engine, "23andme_v5")
        _seed(sample_engine, [_mt("i9990009", 1555, "G")])
        assert (
            read_genotypes(load_mt_rnr1_panel(), sample_engine)["rs267606617"].status
            == PROBE_TYPED
        )

    def test_nuclear_panel_unaffected(self, sample_engine: sa.Engine) -> None:
        # A panel whose loci declare no chrom/pos issues no position query and
        # reads exactly as before (AC2). APOL1 is nuclear (chr22).
        from backend.analysis.apol1 import load_apol1_panel

        _set_format(sample_engine, "23andme_v5")
        panel = load_apol1_panel()
        assert all(loc.pos is None for loc in panel.loci)
        readouts = read_genotypes(panel, sample_engine)
        # No data seeded → every nuclear locus off-chip, unchanged by #820.
        assert all(r.status == PROBE_ABSENT for r in readouts.values())

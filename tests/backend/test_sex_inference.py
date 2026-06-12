"""Tests for ``backend.services.sex_inference`` (Plan §9.4, IND-08 part b).

Covers the four classifications (XX / XY / manual_review / unknown), the
discordant chrX/chrY evidence branch, the PAR pre-filter, and the
load-bearing threshold + PAR constants from
``docs/sex_inference_threshold_validation.md``.

See Step 53 (``docs/sex_inference_threshold_validation.md``) for the
bio-validator attestation that fixes the threshold values these tests
expect.
"""

from __future__ import annotations

import pytest
import sqlalchemy as sa

from backend.db.sample_schema import create_sample_tables
from backend.db.tables import raw_variants
from backend.services.sex_inference import (
    _PAR1,
    _PAR2,
    _THRESHOLD_PAR_NOISE,
    _THRESHOLD_XY_CONFIRM,
    Classification,
    _classify,
    infer_biological_sex,
)

# Positions well past PAR1's upper bound (2_699_520) and below PAR2's
# lower bound (154_931_044) — i.e. unambiguously non-PAR.
_NONPAR_X_BASE = 50_000_000
_PAR1_POS = 1_000_000  # inside PAR1
_PAR2_POS = 155_000_000  # inside PAR2


@pytest.fixture()
def sample_engine() -> sa.Engine:
    engine = sa.create_engine("sqlite://")
    create_sample_tables(engine)
    return engine


def _seed(engine: sa.Engine, rows: list[dict]) -> None:
    with engine.begin() as conn:
        conn.execute(sa.insert(raw_variants), rows)


def _y_rows(*, typed: int, nocall: int, base_pos: int = 1_000_000) -> list[dict]:
    """Build chrY rows: ``typed`` called rows + ``nocall`` no-call rows."""
    rows: list[dict] = []
    for i in range(typed):
        rows.append(
            {
                "rsid": f"rs_y_typed_{i}",
                "chrom": "Y",
                "pos": base_pos + i,
                "genotype": "TT",
            }
        )
    for i in range(nocall):
        rows.append(
            {
                "rsid": f"rs_y_nc_{i}",
                "chrom": "Y",
                "pos": base_pos + typed + i,
                "genotype": "--",
            }
        )
    return rows


# ── Threshold-constant attestation ──────────────────────────────────────


class TestValidatedConstants:
    """Lock the validated thresholds + PAR coordinates against the
    bio-validator attestation (``docs/sex_inference_threshold_validation.md``).
    Any drift here demands a re-attestation, not a test edit."""

    def test_xy_confirm_threshold(self) -> None:
        assert _THRESHOLD_XY_CONFIRM == 0.30

    def test_par_noise_threshold(self) -> None:
        assert _THRESHOLD_PAR_NOISE == 0.10

    def test_par1_interval_grch37(self) -> None:
        assert _PAR1 == (60001, 2_699_520)

    def test_par2_interval_grch37(self) -> None:
        assert _PAR2 == (154_931_044, 155_260_560)

    def test_par_noise_below_confirm(self) -> None:
        # Defensive: the manual-review band must be non-empty.
        assert _THRESHOLD_PAR_NOISE < _THRESHOLD_XY_CONFIRM


# ── Core classification paths ───────────────────────────────────────────


class TestClassificationBranches:
    """One canonical happy-path test per Plan §9.4 branch."""

    def test_xx_single_nonpar_het_without_chry_signal(self, sample_engine: sa.Engine) -> None:
        """A single non-PAR chrX het with no chrY evidence yields XX."""
        _seed(
            sample_engine,
            [
                {"rsid": "rs_x_het", "chrom": "X", "pos": _NONPAR_X_BASE, "genotype": "AG"},
                {"rsid": "rs_x_hom", "chrom": "X", "pos": _NONPAR_X_BASE + 1, "genotype": "GG"},
            ],
        )
        assert infer_biological_sex(sample_engine) == "XX"

    def test_xy_confirmed(self, sample_engine: sa.Engine) -> None:
        """All non-PAR chrX hom + chrY rate > 0.30 → XY."""
        _seed(
            sample_engine,
            [
                {"rsid": "rs_x1", "chrom": "X", "pos": _NONPAR_X_BASE, "genotype": "AA"},
                {"rsid": "rs_x2", "chrom": "X", "pos": _NONPAR_X_BASE + 1, "genotype": "GG"},
                # 4/5 typed = 0.80 — well above _THRESHOLD_XY_CONFIRM (0.30).
                *_y_rows(typed=4, nocall=1),
            ],
        )
        assert infer_biological_sex(sample_engine) == "XY"

    def test_manual_review_intermediate_y_rate(self, sample_engine: sa.Engine) -> None:
        """Candidate XY + chrY rate in (PAR_NOISE, XY_CONFIRM] → manual_review."""
        _seed(
            sample_engine,
            [
                {"rsid": "rs_x", "chrom": "X", "pos": _NONPAR_X_BASE, "genotype": "AA"},
                # 2/10 = 0.20 — strictly between 0.10 and 0.30.
                *_y_rows(typed=2, nocall=8),
            ],
        )
        assert infer_biological_sex(sample_engine) == "manual_review"

    def test_unknown_empty_sample(self, sample_engine: sa.Engine) -> None:
        """Empty raw_variants → unknown."""
        assert infer_biological_sex(sample_engine) == "unknown"

    def test_unknown_mt_only_data(self, sample_engine: sa.Engine) -> None:
        """mtDNA-only data → unknown (no chrX evidence)."""
        _seed(
            sample_engine,
            [
                {"rsid": "rs_mt", "chrom": "MT", "pos": 1234, "genotype": "AA"},
            ],
        )
        assert infer_biological_sex(sample_engine) == "unknown"

    def test_unknown_all_chrx_nocall(self, sample_engine: sa.Engine) -> None:
        """Every non-PAR chrX is no-call, no chrY → unknown."""
        _seed(
            sample_engine,
            [
                {"rsid": "rs_x_nc1", "chrom": "X", "pos": _NONPAR_X_BASE, "genotype": "--"},
                {"rsid": "rs_x_nc2", "chrom": "X", "pos": _NONPAR_X_BASE + 1, "genotype": "00"},
            ],
        )
        assert infer_biological_sex(sample_engine) == "unknown"

    def test_unknown_chrY_rate_below_par_noise(self, sample_engine: sa.Engine) -> None:
        """Candidate XY + chrY rate ≤ PAR_NOISE → unknown (don't auto-assign)."""
        _seed(
            sample_engine,
            [
                {"rsid": "rs_x", "chrom": "X", "pos": _NONPAR_X_BASE, "genotype": "AA"},
                # 1/20 = 0.05 — at/below 0.10.
                *_y_rows(typed=1, nocall=19),
            ],
        )
        assert infer_biological_sex(sample_engine) == "unknown"


# ── Discordant chrX/chrY evidence and PAR pre-filter ────────────────────


class TestPARPreFilter:
    """Plan §9.4 step 0 — PAR sites carry no sex signal and must be
    excluded before the chrX zygosity check."""

    def test_par1_het_alone_yields_unknown(self, sample_engine: sa.Engine) -> None:
        """Heterozygous PAR1 call without any non-PAR chrX evidence → unknown."""
        _seed(
            sample_engine,
            [
                {"rsid": "rs_par1", "chrom": "X", "pos": _PAR1_POS, "genotype": "AG"},
            ],
        )
        assert infer_biological_sex(sample_engine) == "unknown"

    def test_par2_het_alone_yields_unknown(self, sample_engine: sa.Engine) -> None:
        _seed(
            sample_engine,
            [
                {"rsid": "rs_par2", "chrom": "X", "pos": _PAR2_POS, "genotype": "AG"},
            ],
        )
        assert infer_biological_sex(sample_engine) == "unknown"

    def test_par_het_plus_nonpar_hom_yields_candidate_xy(self, sample_engine: sa.Engine) -> None:
        """PAR het is pre-filtered; the non-PAR hom alone makes the sample
        a candidate XY, then confirmed by chrY rate."""
        _seed(
            sample_engine,
            [
                {"rsid": "rs_par", "chrom": "X", "pos": _PAR1_POS, "genotype": "AG"},
                {"rsid": "rs_x_hom", "chrom": "X", "pos": _NONPAR_X_BASE, "genotype": "GG"},
                *_y_rows(typed=3, nocall=1),  # 0.75
            ],
        )
        assert infer_biological_sex(sample_engine) == "XY"


class TestXHetWithChrYSignal:
    """Non-PAR chrX heterozygosity stays XX only while chrY is at/below
    the PAR-noise floor; stronger chrY signal is discordant."""

    def test_chrY_at_par_noise_floor_does_not_override_x_het(
        self, sample_engine: sa.Engine
    ) -> None:
        _seed(
            sample_engine,
            [
                {"rsid": "rs_x_het", "chrom": "X", "pos": _NONPAR_X_BASE, "genotype": "AG"},
                # 1/10 chrY = 0.10, exactly the PAR-noise floor.
                *_y_rows(typed=1, nocall=9),
            ],
        )
        assert infer_biological_sex(sample_engine) == "XX"

    def test_chrY_noise_in_manual_review_band_flags_manual_review(
        self, sample_engine: sa.Engine
    ) -> None:
        _seed(
            sample_engine,
            [
                {"rsid": "rs_x_het", "chrom": "X", "pos": _NONPAR_X_BASE, "genotype": "AG"},
                # 2/10 chrY = 0.20, above the PAR-noise floor.
                *_y_rows(typed=2, nocall=8),
            ],
        )
        assert infer_biological_sex(sample_engine) == "manual_review"

    def test_chrY_rate_above_confirm_flags_manual_review(self, sample_engine: sa.Engine) -> None:
        """Confirm-grade chrY plus a non-PAR chrX het is discordant, not XY."""
        _seed(
            sample_engine,
            [
                {"rsid": "rs_x_het", "chrom": "X", "pos": _NONPAR_X_BASE, "genotype": "AG"},
                *_y_rows(typed=8, nocall=2),  # 0.80
            ],
        )
        assert infer_biological_sex(sample_engine) == "manual_review"

    def test_multiple_x_hets_plus_confirm_grade_chry_flags_manual_review(
        self, sample_engine: sa.Engine
    ) -> None:
        """Regression for issue #122: an XXY-like array signal is not ordinary XX."""
        _seed(
            sample_engine,
            [
                {"rsid": "rs_x_het_1", "chrom": "X", "pos": _NONPAR_X_BASE, "genotype": "AG"},
                {
                    "rsid": "rs_x_het_2",
                    "chrom": "X",
                    "pos": _NONPAR_X_BASE + 1,
                    "genotype": "CT",
                },
                *_y_rows(typed=8, nocall=2),  # 0.80
            ],
        )
        assert infer_biological_sex(sample_engine) == "manual_review"


# ── Parametric assertion: returned type lands in the Literal alphabet ──


@pytest.mark.parametrize(
    "rows,expected",
    [
        # Branch coverage parametrized: each tuple exercises one branch of
        # Plan §9.4 from the same call site, so the type checker can lock
        # the Literal alphabet on the return.
        (
            [
                {"rsid": "rs_x_het", "chrom": "X", "pos": _NONPAR_X_BASE, "genotype": "AG"},
            ],
            "XX",
        ),
        (
            [
                {"rsid": "rs_x_hom", "chrom": "X", "pos": _NONPAR_X_BASE, "genotype": "GG"},
                {"rsid": "rs_y_t", "chrom": "Y", "pos": 1_000_000, "genotype": "TT"},
                {"rsid": "rs_y_t2", "chrom": "Y", "pos": 1_000_001, "genotype": "AA"},
            ],
            "XY",
        ),
        (
            [
                {"rsid": "rs_x_hom", "chrom": "X", "pos": _NONPAR_X_BASE, "genotype": "GG"},
                {"rsid": "rs_y_t", "chrom": "Y", "pos": 1_000_000, "genotype": "TT"},
                *[
                    {"rsid": f"rs_y_nc_{i}", "chrom": "Y", "pos": 1_000_001 + i, "genotype": "--"}
                    for i in range(4)
                ],
            ],
            "manual_review",
        ),
        ([], "unknown"),
    ],
)
def test_returns_literal_alphabet(
    rows: list[dict],
    expected: Classification,
    sample_engine: sa.Engine,
) -> None:
    if rows:
        _seed(sample_engine, rows)
    assert infer_biological_sex(sample_engine) == expected


# ── IND-09b edge-case battery (Plan §14.1) ─────────────────────────────────


class TestIND09bEdgeCases:
    """Plan §14.1 IND-09b edge-case battery — hardens the boundary
    behaviors around the PAR pre-filter, the candidate-XY → chrY-confirmation
    handoff, and discordant non-PAR chrX het + chrY signal.

    Each test maps to one bullet in IND-09b:

      (i)       chrM-only data → ``unknown``
      (ii)      PAR-only het (no informative non-PAR chrX) → ``unknown``
      (ii-bis)  PAR het + non-PAR hom with no chrY → ``unknown``
                (candidate-XY without confirmation falls back)
      (iii)     chrY rate in ``(_THRESHOLD_PAR_NOISE, _THRESHOLD_XY_CONFIRM]``
                + all non-PAR chrX homozygous → ``manual_review``
      (iii-bis) Non-PAR het + chrY signal just above PAR-noise floor
                → ``manual_review`` (pinned end-to-end and by direct
                ``_classify`` assertion)
    """

    # ── (i) chrM-only ──────────────────────────────────────────────────

    def test_i_chrM_only_multiple_loci_returns_unknown(self, sample_engine: sa.Engine) -> None:
        """Multiple chrM rows + no chrX/chrY rows at all → ``unknown``.
        Strengthens the single-row ``test_unknown_mt_only_data`` case."""
        _seed(
            sample_engine,
            [
                {
                    "rsid": f"rs_mt_{i}",
                    "chrom": "MT",
                    "pos": 1000 + i,
                    "genotype": "AA",
                }
                for i in range(20)
            ],
        )
        assert infer_biological_sex(sample_engine) == "unknown"

    # ── (ii) PAR-only het, no informative non-PAR chrX ─────────────────

    def test_ii_par1_and_par2_het_only_returns_unknown(self, sample_engine: sa.Engine) -> None:
        """Het PAR1 + Het PAR2 + no non-PAR chrX + no chrY → ``unknown``.
        Both PAR rows fall under the pre-filter so ``x_nonpar_typed`` stays
        zero and neither the XX-evidence nor candidate-XY branch fires."""
        _seed(
            sample_engine,
            [
                {"rsid": "rs_par1_het", "chrom": "X", "pos": _PAR1_POS, "genotype": "AG"},
                {"rsid": "rs_par2_het", "chrom": "X", "pos": _PAR2_POS, "genotype": "CT"},
            ],
        )
        assert infer_biological_sex(sample_engine) == "unknown"

    def test_ii_par_het_plus_nonpar_nocall_only_returns_unknown(
        self, sample_engine: sa.Engine
    ) -> None:
        """PAR1 het + non-PAR chrX no-calls only → ``unknown``. The PAR
        row is pre-filtered; the non-PAR rows are all no-calls so
        ``x_nonpar_typed`` is zero — neither informative-chrX branch
        engages."""
        _seed(
            sample_engine,
            [
                {"rsid": "rs_par1_het", "chrom": "X", "pos": _PAR1_POS, "genotype": "AG"},
                {"rsid": "rs_x_nc1", "chrom": "X", "pos": _NONPAR_X_BASE, "genotype": "--"},
                {"rsid": "rs_x_nc2", "chrom": "X", "pos": _NONPAR_X_BASE + 1, "genotype": "00"},
            ],
        )
        assert infer_biological_sex(sample_engine) == "unknown"

    # ── (ii-bis) candidate-XY without chrY confirmation ────────────────

    def test_ii_bis_par_het_plus_nonpar_hom_without_chrY_returns_unknown(
        self, sample_engine: sa.Engine
    ) -> None:
        """PAR1 het + at least one homozygous non-PAR chrX call + **no
        chrY data** → ``unknown``. The PAR pre-filter strips the PAR het,
        leaving a candidate-XY pattern on non-PAR chrX; with zero chrY
        rows ``y_rate == 0.0`` which is at/below ``_THRESHOLD_PAR_NOISE``,
        so the algorithm falls back to ``unknown`` rather than auto-
        assigning XY. Pinning this guards against a future regression
        that mistakes silence on chrY for confirmation."""
        _seed(
            sample_engine,
            [
                {"rsid": "rs_par1_het", "chrom": "X", "pos": _PAR1_POS, "genotype": "AG"},
                {"rsid": "rs_x_hom", "chrom": "X", "pos": _NONPAR_X_BASE, "genotype": "GG"},
            ],
        )
        assert infer_biological_sex(sample_engine) == "unknown"

    # ── (iii) chrY rate boundaries around the manual_review band ───────

    def test_iii_chrY_rate_just_above_par_noise_floor_yields_manual_review(
        self, sample_engine: sa.Engine
    ) -> None:
        """All non-PAR chrX hom + chrY rate **just above** the PAR-noise
        floor → ``manual_review``. 2/18 ≈ 0.111 — strictly above 0.10 and
        well below 0.30 (the existing mid-band case sits at 0.20)."""
        _seed(
            sample_engine,
            [
                {"rsid": "rs_x_hom", "chrom": "X", "pos": _NONPAR_X_BASE, "genotype": "AA"},
                *_y_rows(typed=2, nocall=16),
            ],
        )
        assert infer_biological_sex(sample_engine) == "manual_review"

    def test_iii_chrY_rate_at_xy_confirm_threshold_yields_manual_review(
        self, sample_engine: sa.Engine
    ) -> None:
        """All non-PAR chrX hom + chrY rate **exactly equal to**
        ``_THRESHOLD_XY_CONFIRM`` (3/10 = 0.30) → ``manual_review``.
        Locks the strict-``>`` semantics of the confirm branch — equality
        is not enough to promote to XY."""
        _seed(
            sample_engine,
            [
                {"rsid": "rs_x_hom", "chrom": "X", "pos": _NONPAR_X_BASE, "genotype": "AA"},
                *_y_rows(typed=3, nocall=7),
            ],
        )
        assert infer_biological_sex(sample_engine) == "manual_review"

    def test_iii_chrY_rate_just_above_xy_confirm_yields_xy(self, sample_engine: sa.Engine) -> None:
        """Defensive boundary mate to the equality case above: 31/100 =
        0.31 — just above ``_THRESHOLD_XY_CONFIRM`` — promotes the
        candidate XY to confirmed ``XY``."""
        _seed(
            sample_engine,
            [
                {"rsid": "rs_x_hom", "chrom": "X", "pos": _NONPAR_X_BASE, "genotype": "AA"},
                *_y_rows(typed=31, nocall=69),
            ],
        )
        assert infer_biological_sex(sample_engine) == "XY"

    # ── (iii-bis) discordant chrX het + chrY signal ────────────────────

    def test_iii_bis_nonpar_het_plus_chrY_just_above_floor_yields_manual_review(
        self, sample_engine: sa.Engine
    ) -> None:
        """Non-PAR het + chrY rate **just above** ``_THRESHOLD_PAR_NOISE``
        (2/18 ≈ 0.111) → ``manual_review``. This pins the floor-adjacent
        boundary for discordant X/Y evidence."""
        _seed(
            sample_engine,
            [
                {"rsid": "rs_x_het", "chrom": "X", "pos": _NONPAR_X_BASE, "genotype": "AG"},
                *_y_rows(typed=2, nocall=16),
            ],
        )
        assert infer_biological_sex(sample_engine) == "manual_review"

    def test_iii_bis_classify_helper_escalates_discordant_x_het_and_y_signal(
        self,
    ) -> None:
        """Direct ``_classify`` assertion of the X-het / chrY boundary.

        With ``x_nonpar_het >= 1`` the classifier returns ``"XX"`` only
        while chrY remains at/below the PAR-noise floor. Anything above
        that floor returns ``manual_review`` instead of silently reporting
        ordinary XX."""
        for y_rate, expected in (
            (0.0, "XX"),
            (_THRESHOLD_PAR_NOISE, "XX"),
            (_THRESHOLD_PAR_NOISE + 0.01, "manual_review"),
            (0.20, "manual_review"),
            (_THRESHOLD_XY_CONFIRM, "manual_review"),
            (_THRESHOLD_XY_CONFIRM + 0.01, "manual_review"),
            (0.50, "manual_review"),
            (0.999, "manual_review"),
        ):
            assert (
                _classify(
                    x_nonpar_het=1,
                    x_nonpar_typed=1,
                    x_nonpar_hom=0,
                    y_rate=y_rate,
                )
                == expected
            ), f"_classify misclassified discordant X/Y evidence at y_rate={y_rate}"

"""Tests for ``backend.services.sex_inference`` (Plan §9.4, IND-08 part b).

Covers the four classifications (XX / XY / manual_review / unknown), the
minimum-evidence guard (issue #363), the discordant chrX/chrY evidence branch,
the PAR pre-filter, and the load-bearing threshold + PAR constants.

A confident verdict requires an aggregate denominator on both sex chromosomes
(``MIN_X_NONPAR_TYPED`` typed non-PAR chrX probes and ``MIN_Y_PROBES`` chrY
probes), so the branch tests seed evaluable densities; the sparse-input cases
that must return ``unknown`` live in :class:`TestMinimumEvidenceGuard`.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import sqlalchemy as sa

from backend.db.sample_schema import create_sample_tables
from backend.db.tables import raw_variants
from backend.services.sex_inference import (
    _PAR1,
    _PAR2,
    _THRESHOLD_PAR_NOISE,
    _THRESHOLD_X_HET_DIPLOID,
    _THRESHOLD_X_HET_HEMIZYGOUS,
    _THRESHOLD_XY_CONFIRM,
    MIN_X_NONPAR_TYPED,
    MIN_Y_PROBES,
    Classification,
    _classify,
    compute_sex_signals,
    infer_biological_sex,
)

# Positions well past PAR1's upper bound (2_699_520) and below PAR2's
# lower bound (154_931_044) — i.e. unambiguously non-PAR.
_NONPAR_X_BASE = 50_000_000
_PAR1_POS = 1_000_000  # inside PAR1
_PAR2_POS = 155_000_000  # inside PAR2

# Evaluable baselines that clear the issue-363 minimum-evidence floors
# (``MIN_X_NONPAR_TYPED`` / ``MIN_Y_PROBES``) so the §9.4 decision tree runs.
_EVAL_X = 120  # ≥ MIN_X_NONPAR_TYPED (100)
_EVAL_Y = 60  # ≥ MIN_Y_PROBES (50)


@pytest.fixture()
def sample_engine() -> sa.Engine:
    engine = sa.create_engine("sqlite://")
    create_sample_tables(engine)
    return engine


def _seed(engine: sa.Engine, rows: list[dict]) -> None:
    with engine.begin() as conn:
        conn.execute(sa.insert(raw_variants), rows)


def _x_rows(
    *, het: int = 0, hom: int = 0, hemi: int = 0, nocall: int = 0, base: int = _NONPAR_X_BASE
) -> list[dict]:
    """Build non-PAR chrX rows: ``het`` ('AG') + ``hom`` ('GG') + ``hemi`` ('A',
    single-char hemizygous male call) + ``nocall`` ('--')."""
    rows: list[dict] = []
    i = 0
    for _ in range(het):
        rows.append({"rsid": f"rs_x_het_{i}", "chrom": "X", "pos": base + i, "genotype": "AG"})
        i += 1
    for _ in range(hom):
        rows.append({"rsid": f"rs_x_hom_{i}", "chrom": "X", "pos": base + i, "genotype": "GG"})
        i += 1
    for _ in range(hemi):
        rows.append({"rsid": f"rs_x_hemi_{i}", "chrom": "X", "pos": base + i, "genotype": "A"})
        i += 1
    for _ in range(nocall):
        rows.append({"rsid": f"rs_x_nc_{i}", "chrom": "X", "pos": base + i, "genotype": "--"})
        i += 1
    return rows


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
    """Lock the validated thresholds, PAR coordinates, and minimum-evidence
    floors. Any drift here demands a re-attestation/re-calibration, not a
    test edit."""

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

    def test_x_het_rate_thresholds(self) -> None:
        # issue #519 — X dosage is decided on the non-PAR chrX het *rate*.
        assert _THRESHOLD_X_HET_HEMIZYGOUS == 0.05
        assert _THRESHOLD_X_HET_DIPLOID == 0.15

    def test_x_het_hemizygous_below_diploid(self) -> None:
        # Defensive: the ambiguous X-dosage band must be non-empty, and the
        # hemizygous (one-X) cutoff must sit below the diploid (two-X) cutoff.
        assert _THRESHOLD_X_HET_HEMIZYGOUS < _THRESHOLD_X_HET_DIPLOID

    def test_minimum_evidence_floors(self) -> None:
        # issue #363 — shared with the sex-aneuploidy screen; calibrated to real
        # consumer-array densities (thousands of non-PAR chrX, hundreds of chrY).
        assert MIN_X_NONPAR_TYPED == 100
        assert MIN_Y_PROBES == 50


# ── Core classification paths ───────────────────────────────────────────


class TestClassificationBranches:
    """One canonical happy-path test per Plan §9.4 branch, at evaluable
    densities (≥ MIN_X_NONPAR_TYPED non-PAR chrX, ≥ MIN_Y_PROBES chrY)."""

    def test_xx_evaluable_nonpar_het_without_chry_signal(self, sample_engine: sa.Engine) -> None:
        """Heterozygous non-PAR chrX over an evaluable denominator with no chrY
        signal yields XX."""
        _seed(
            sample_engine,
            [*_x_rows(het=60, hom=60), *_y_rows(typed=0, nocall=_EVAL_Y)],
        )
        assert infer_biological_sex(sample_engine) == "XX"

    def test_xy_confirmed(self, sample_engine: sa.Engine) -> None:
        """All non-PAR chrX hom + chrY rate > 0.30 → XY."""
        _seed(
            sample_engine,
            [*_x_rows(hom=_EVAL_X), *_y_rows(typed=48, nocall=12)],  # 48/60 = 0.80
        )
        assert infer_biological_sex(sample_engine) == "XY"

    def test_manual_review_intermediate_y_rate(self, sample_engine: sa.Engine) -> None:
        """Candidate XY + chrY rate in (PAR_NOISE, XY_CONFIRM] → manual_review."""
        _seed(
            sample_engine,
            [*_x_rows(hom=_EVAL_X), *_y_rows(typed=12, nocall=48)],  # 12/60 = 0.20
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
        """Candidate XY + chrY rate ≤ PAR_NOISE → unknown (don't auto-assign).

        Seeded at evaluable density so the ``unknown`` comes from the §9.4
        chrY floor, not the minimum-evidence guard."""
        _seed(
            sample_engine,
            [*_x_rows(hom=_EVAL_X), *_y_rows(typed=3, nocall=57)],  # 3/60 = 0.05 ≤ 0.10
        )
        assert infer_biological_sex(sample_engine) == "unknown"


# ── Discordant chrX/chrY evidence and PAR pre-filter ────────────────────


class TestPARPreFilter:
    """Plan §9.4 step 1 — PAR sites carry no sex signal and must be
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
        """PAR het is pre-filtered; an evaluable pool of non-PAR hom calls makes
        the sample a candidate XY, then confirmed by chrY rate."""
        _seed(
            sample_engine,
            [
                {"rsid": "rs_par", "chrom": "X", "pos": _PAR1_POS, "genotype": "AG"},
                *_x_rows(hom=_EVAL_X),
                *_y_rows(typed=48, nocall=12),  # 0.80
            ],
        )
        assert infer_biological_sex(sample_engine) == "XY"


class TestXHetWithChrYSignal:
    """Non-PAR chrX heterozygosity stays XX only while chrY is at/below
    the PAR-noise floor; stronger chrY signal is discordant. All seeded at
    evaluable density so the verdict comes from the §9.4 tree, not the gate."""

    def test_chrY_at_par_noise_floor_does_not_override_x_het(
        self, sample_engine: sa.Engine
    ) -> None:
        _seed(
            sample_engine,
            [
                *_x_rows(het=60, hom=60),
                *_y_rows(typed=6, nocall=54),  # 6/60 = 0.10, exactly the PAR-noise floor
            ],
        )
        assert infer_biological_sex(sample_engine) == "XX"

    def test_chrY_noise_in_manual_review_band_flags_manual_review(
        self, sample_engine: sa.Engine
    ) -> None:
        _seed(
            sample_engine,
            [
                *_x_rows(het=60, hom=60),
                *_y_rows(typed=12, nocall=48),  # 12/60 = 0.20, above the PAR-noise floor
            ],
        )
        assert infer_biological_sex(sample_engine) == "manual_review"

    def test_chrY_rate_above_confirm_flags_manual_review(self, sample_engine: sa.Engine) -> None:
        """Confirm-grade chrY plus a non-PAR chrX het is discordant, not XY."""
        _seed(
            sample_engine,
            [
                *_x_rows(het=60, hom=60),
                *_y_rows(typed=48, nocall=12),  # 0.80
            ],
        )
        assert infer_biological_sex(sample_engine) == "manual_review"

    def test_diploid_x_het_rate_plus_confirm_grade_chry_flags_manual_review(
        self, sample_engine: sa.Engine
    ) -> None:
        """Regression for issue #122: a true XXY array signal is not ordinary XX.

        A real XXY shows *female-level* non-PAR chrX heterozygosity (tens of
        percent), not a couple of noise calls — so this uses a diploid-X het
        rate (40/120 ≈ 0.33, above the 0.15 diploid cutoff). With a present
        chrY this is discordant → ``manual_review``. (Issue #519 corrected the
        original fixture, which used 2 het calls = male noise, not XXY.)
        """
        _seed(
            sample_engine,
            [
                *_x_rows(het=40, hom=80),  # 40/120 ≈ 0.33 — diploid-X het rate
                *_y_rows(typed=48, nocall=12),  # 0.80
            ],
        )
        assert infer_biological_sex(sample_engine) == "manual_review"


# ── X-het RATE threshold (issue #519) ───────────────────────────────────


class TestXHetRateThreshold:
    """issue #519 — X dosage is decided on the non-PAR chrX heterozygosity
    *rate*, not a binary ``x_nonpar_het >= 1`` count. A normal 46,XY male's
    array always carries a small fraction of non-PAR chrX het *noise*; the old
    rule flagged every such male ``manual_review``. The rate-based rule tolerates
    the noise (≤ 0.05 → candidate XY) while still catching diploid-X (≥ 0.15)."""

    def test_real_male_with_x_het_noise_classifies_xy(self) -> None:
        """The exact repro from #519: an AncestryDNA male with 91/27411 ≈ 0.33%
        non-PAR chrX het noise + chrY rate 0.998. Old rule → ``manual_review``;
        rate-based rule → ``XY``."""
        assert (
            _classify(
                x_nonpar_het=91,
                x_nonpar_typed=27411,
                x_nonpar_hom=27320,
                y_total=1729,
                y_rate=0.998,
            )
            == "XY"
        )

    def test_real_male_with_x_het_noise_end_to_end_xy(self, sample_engine: sa.Engine) -> None:
        """End-to-end through ``infer_biological_sex``: a diploid-X-male export —
        mostly homozygous non-PAR chrX with a few het noise calls (3/403 ≈ 0.7%,
        below the 0.05 hemizygous cutoff) + a high chrY rate — is ``XY``."""
        _seed(
            sample_engine,
            [*_x_rows(het=3, hom=400), *_y_rows(typed=400, nocall=2)],  # chrY rate ≈ 0.995
        )
        assert infer_biological_sex(sample_engine) == "XY"

    def test_x_het_rate_at_hemizygous_cutoff_is_candidate_xy(self) -> None:
        """A het rate exactly at ``_THRESHOLD_X_HET_HEMIZYGOUS`` (0.05) is still
        male-consistent (``<=``), so a confirming chrY yields ``XY``."""
        assert (
            _classify(
                x_nonpar_het=6,
                x_nonpar_typed=120,  # 6/120 = 0.05 exactly
                x_nonpar_hom=114,
                y_total=_EVAL_Y,
                y_rate=0.9,
            )
            == "XY"
        )

    def test_ambiguous_x_het_rate_yields_manual_review(self) -> None:
        """A het rate between the hemizygous and diploid cutoffs (12/120 = 0.10,
        in (0.05, 0.15)) is ambiguous X dosage → ``manual_review`` regardless of
        the chrY rate."""
        for y_rate in (0.0, 0.20, 0.998):
            assert (
                _classify(
                    x_nonpar_het=12,
                    x_nonpar_typed=120,  # 0.10 — ambiguous band
                    x_nonpar_hom=108,
                    y_total=_EVAL_Y,
                    y_rate=y_rate,
                )
                == "manual_review"
            ), f"ambiguous X-het rate should be manual_review at y_rate={y_rate}"

    def test_hemizygous_x_without_chry_is_unknown(self) -> None:
        """Hemizygous X (male-consistent het rate) with no chrY signal is
        ``unknown`` — one X and no Y cannot be confidently assigned (e.g. 45,X
        vs. a male with chrY probe dropout); deferred, not auto-called."""
        assert (
            _classify(
                x_nonpar_het=1,
                x_nonpar_typed=120,  # 0.008 hemizygous
                x_nonpar_hom=119,
                y_total=_EVAL_Y,
                y_rate=0.0,
            )
            == "unknown"
        )


# ── Minimum-evidence guard (issue #363) ─────────────────────────────────


class TestMinimumEvidenceGuard:
    """issue #363 — a confident ``XX``/``XY``/``manual_review`` verdict requires
    an aggregate denominator on **both** sex chromosomes
    (``x_nonpar_typed >= MIN_X_NONPAR_TYPED`` and ``y_total >= MIN_Y_PROBES``).
    Sparse inputs that would previously have produced a confident call now
    return ``unknown`` so they cannot gate sex-specific findings.

    Sex inference is an aggregate QC step, not a single-locus Mendelian call:
    validated tools score X-heterozygosity together with chrY missingness over
    many markers (seXY, PMID 28035028), and a lone non-PAR chrX het occurs even
    in males (Chen et al., PMID 38073250)."""

    def test_single_nonpar_het_no_chry_is_unknown(self, sample_engine: sa.Engine) -> None:
        """The headline case from the issue: one non-PAR chrX het, no chrY
        probes — too little evidence for a confident XX → ``unknown``."""
        _seed(sample_engine, _x_rows(het=1))
        assert infer_biological_sex(sample_engine) == "unknown"

    def test_single_nonpar_het_with_full_chry_is_unknown(self, sample_engine: sa.Engine) -> None:
        """Even with an evaluable chrY denominator, one typed non-PAR chrX
        probe is below the chrX floor → ``unknown``."""
        _seed(sample_engine, [*_x_rows(het=1), *_y_rows(typed=0, nocall=_EVAL_Y)])
        assert infer_biological_sex(sample_engine) == "unknown"

    def test_evaluable_x_het_without_chry_denominator_is_unknown(
        self, sample_engine: sa.Engine
    ) -> None:
        """A strong non-PAR chrX het signal but ZERO chrY probes: ``y_rate`` is
        a vacuous 0.0, not evidence chrY is absent → ``unknown``, not XX."""
        _seed(sample_engine, _x_rows(het=_EVAL_X // 2, hom=_EVAL_X // 2))
        assert infer_biological_sex(sample_engine) == "unknown"

    def test_evaluable_x_with_thin_chry_is_unknown(self, sample_engine: sa.Engine) -> None:
        """chrY denominator just below ``MIN_Y_PROBES`` → ``unknown`` even though
        the typed-rate alone (1.0) would otherwise confirm XY."""
        _seed(
            sample_engine,
            [*_x_rows(hom=_EVAL_X), *_y_rows(typed=MIN_Y_PROBES - 1, nocall=0)],
        )
        assert infer_biological_sex(sample_engine) == "unknown"

    def test_classify_at_exact_floors_runs_the_tree(self) -> None:
        # Exactly at both floors → the §9.4 tree runs (candidate XY confirmed).
        assert (
            _classify(
                x_nonpar_het=0,
                x_nonpar_typed=MIN_X_NONPAR_TYPED,
                x_nonpar_hom=MIN_X_NONPAR_TYPED,
                y_total=MIN_Y_PROBES,
                y_rate=0.9,
            )
            == "XY"
        )
        # A diploid-X het rate at the floors resolves to XX too.
        assert (
            _classify(
                x_nonpar_het=MIN_X_NONPAR_TYPED // 2,  # 50% het → diploid-X rate
                x_nonpar_typed=MIN_X_NONPAR_TYPED,
                x_nonpar_hom=MIN_X_NONPAR_TYPED - MIN_X_NONPAR_TYPED // 2,
                y_total=MIN_Y_PROBES,
                y_rate=0.0,
            )
            == "XX"
        )

    def test_classify_just_below_x_floor_is_unknown(self) -> None:
        assert (
            _classify(
                x_nonpar_het=1,
                x_nonpar_typed=MIN_X_NONPAR_TYPED - 1,
                x_nonpar_hom=MIN_X_NONPAR_TYPED - 2,
                y_total=MIN_Y_PROBES,
                y_rate=0.0,
            )
            == "unknown"
        )

    def test_classify_just_below_y_floor_is_unknown(self) -> None:
        assert (
            _classify(
                x_nonpar_het=0,
                x_nonpar_typed=MIN_X_NONPAR_TYPED,
                x_nonpar_hom=MIN_X_NONPAR_TYPED,
                y_total=MIN_Y_PROBES - 1,
                y_rate=0.9,
            )
            == "unknown"
        )


# ── Hemizygous single-char male X (23andMe representation, issue #504) ──


class TestHemizygousMaleX:
    """issue #504 — males are hemizygous on the non-PAR X (one X copy), so
    23andMe reports their non-PAR chrX calls as single-character genotypes
    (``"A"``), not the diploid homozygote (``"AA"``) AncestryDNA emits. Such
    calls must count as typed, non-heterozygous evidence; otherwise
    ``x_nonpar_typed`` stays 0 for every 23andMe male and the classifier
    short-circuits to ``unknown`` — silently disabling Y-haplogroup assignment
    and every sex-gated finding."""

    def test_compute_signals_counts_hemizygous_as_typed_nonhet(
        self, sample_engine: sa.Engine
    ) -> None:
        """Single-char non-PAR chrX calls land in ``x_nonpar_hemizygous`` and
        ``x_nonpar_typed`` — not dropped, and not miscounted as het/hom."""
        _seed(sample_engine, _x_rows(hemi=_EVAL_X))
        signals = compute_sex_signals(sample_engine)
        assert signals.x_nonpar_typed == _EVAL_X
        assert signals.x_nonpar_hemizygous == _EVAL_X
        assert signals.x_nonpar_het == 0
        assert signals.x_nonpar_hom == 0

    def test_hemizygous_male_x_with_chry_confirms_xy(self, sample_engine: sa.Engine) -> None:
        """The headline regression: hemizygous single-char non-PAR chrX over an
        evaluable denominator + a confirm-grade chrY rate → ``XY`` (was
        ``unknown`` before #504)."""
        _seed(
            sample_engine,
            [*_x_rows(hemi=_EVAL_X), *_y_rows(typed=48, nocall=12)],  # 48/60 = 0.80
        )
        assert infer_biological_sex(sample_engine) == "XY"

    def test_hemizygous_male_x_without_chry_is_unknown(self, sample_engine: sa.Engine) -> None:
        """Evaluable hemizygous chrX but ZERO chrY probes → ``unknown``: the
        candidate-XY pattern still needs chrY confirmation, and a vacuous
        ``y_rate`` from no probes is not evidence chrY is absent (issue #363)."""
        _seed(sample_engine, _x_rows(hemi=_EVAL_X))
        assert infer_biological_sex(sample_engine) == "unknown"

    def test_hemizygous_below_x_floor_is_unknown(self, sample_engine: sa.Engine) -> None:
        """Too few hemizygous non-PAR chrX probes (below ``MIN_X_NONPAR_TYPED``)
        → ``unknown`` even with a confirm-grade chrY rate."""
        _seed(
            sample_engine,
            [*_x_rows(hemi=MIN_X_NONPAR_TYPED - 1), *_y_rows(typed=48, nocall=12)],
        )
        assert infer_biological_sex(sample_engine) == "unknown"

    def test_par_hemizygous_is_prefiltered(self, sample_engine: sa.Engine) -> None:
        """A single-char call inside PAR1 carries no sex signal and must be
        pre-filtered — it does not reach the hemizygous tally."""
        _seed(
            sample_engine,
            [{"rsid": "rs_par_hemi", "chrom": "X", "pos": _PAR1_POS, "genotype": "A"}],
        )
        signals = compute_sex_signals(sample_engine)
        assert signals.x_nonpar_typed == 0
        assert signals.x_nonpar_hemizygous == 0

    def test_classify_counts_hemizygous_toward_candidate_xy(self) -> None:
        """Direct ``_classify``: hemizygous calls satisfy the candidate-XY
        denominator (``hom + hemizygous == typed``) just as diploid homozygotes
        do, so a pure-hemizygous male confirms ``XY``."""
        assert (
            _classify(
                x_nonpar_het=0,
                x_nonpar_typed=MIN_X_NONPAR_TYPED,
                x_nonpar_hom=0,
                x_nonpar_hemizygous=MIN_X_NONPAR_TYPED,
                y_total=MIN_Y_PROBES,
                y_rate=0.9,
            )
            == "XY"
        )
        # A lone noise het among hemizygous calls (1/100 = 0.01, below the
        # hemizygous cutoff) is tolerated — still XY, not flipped by noise (#519).
        assert (
            _classify(
                x_nonpar_het=1,
                x_nonpar_typed=MIN_X_NONPAR_TYPED,
                x_nonpar_hom=0,
                x_nonpar_hemizygous=MIN_X_NONPAR_TYPED - 1,
                y_total=MIN_Y_PROBES,
                y_rate=0.9,
            )
            == "XY"
        )
        # A *diploid-X* het rate (50/100 = 0.50) with chrY present is the
        # discordant XXY signal → manual_review, not XY (issue #519: only a
        # diploid het rate flips a candidate male, not noise).
        assert (
            _classify(
                x_nonpar_het=MIN_X_NONPAR_TYPED // 2,
                x_nonpar_typed=MIN_X_NONPAR_TYPED,
                x_nonpar_hom=0,
                x_nonpar_hemizygous=MIN_X_NONPAR_TYPED - MIN_X_NONPAR_TYPED // 2,
                y_total=MIN_Y_PROBES,
                y_rate=0.9,
            )
            == "manual_review"
        )


# ── Parametric assertion: returned type lands in the Literal alphabet ──


@pytest.mark.parametrize(
    "rows,expected",
    [
        # Branch coverage parametrized: each tuple exercises one branch of
        # Plan §9.4 from the same call site, so the type checker can lock
        # the Literal alphabet on the return. Seeded at evaluable densities.
        ([*_x_rows(het=60, hom=60), *_y_rows(typed=0, nocall=_EVAL_Y)], "XX"),
        ([*_x_rows(hom=_EVAL_X), *_y_rows(typed=48, nocall=12)], "XY"),
        # 23andMe male: hemizygous single-char non-PAR chrX + confirm-grade chrY.
        ([*_x_rows(hemi=_EVAL_X), *_y_rows(typed=48, nocall=12)], "XY"),
        ([*_x_rows(hom=_EVAL_X), *_y_rows(typed=12, nocall=48)], "manual_review"),
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
        """PAR1 het + an evaluable pool of homozygous non-PAR chrX calls + **no
        chrY data** → ``unknown``. The PAR pre-filter strips the PAR het,
        leaving a candidate-XY pattern on non-PAR chrX; with zero chrY rows the
        minimum-evidence guard (``y_total < MIN_Y_PROBES``) returns ``unknown``
        rather than mistaking silence on chrY for confirmation."""
        _seed(
            sample_engine,
            [
                {"rsid": "rs_par1_het", "chrom": "X", "pos": _PAR1_POS, "genotype": "AG"},
                *_x_rows(hom=_EVAL_X),
            ],
        )
        assert infer_biological_sex(sample_engine) == "unknown"

    # ── (iii) chrY rate boundaries around the manual_review band ───────

    def test_iii_chrY_rate_just_above_par_noise_floor_yields_manual_review(
        self, sample_engine: sa.Engine
    ) -> None:
        """All non-PAR chrX hom + chrY rate **just above** the PAR-noise
        floor → ``manual_review``. 7/60 ≈ 0.117 — strictly above 0.10 and
        well below 0.30."""
        _seed(
            sample_engine,
            [*_x_rows(hom=_EVAL_X), *_y_rows(typed=7, nocall=53)],
        )
        assert infer_biological_sex(sample_engine) == "manual_review"

    def test_iii_chrY_rate_at_xy_confirm_threshold_yields_manual_review(
        self, sample_engine: sa.Engine
    ) -> None:
        """All non-PAR chrX hom + chrY rate **exactly equal to**
        ``_THRESHOLD_XY_CONFIRM`` (18/60 = 0.30) → ``manual_review``.
        Locks the strict-``>`` semantics of the confirm branch — equality
        is not enough to promote to XY."""
        _seed(
            sample_engine,
            [*_x_rows(hom=_EVAL_X), *_y_rows(typed=18, nocall=42)],
        )
        assert infer_biological_sex(sample_engine) == "manual_review"

    def test_iii_chrY_rate_just_above_xy_confirm_yields_xy(self, sample_engine: sa.Engine) -> None:
        """Defensive boundary mate to the equality case above: 19/60 ≈
        0.317 — just above ``_THRESHOLD_XY_CONFIRM`` — promotes the
        candidate XY to confirmed ``XY``."""
        _seed(
            sample_engine,
            [*_x_rows(hom=_EVAL_X), *_y_rows(typed=19, nocall=41)],
        )
        assert infer_biological_sex(sample_engine) == "XY"

    # ── (iii-bis) discordant chrX het + chrY signal ────────────────────

    def test_iii_bis_hemizygous_x_plus_chrY_just_above_floor_yields_manual_review(
        self, sample_engine: sa.Engine
    ) -> None:
        """Hemizygous non-PAR chrX (1/120 ≈ 0.008 het rate, below the 0.05
        cutoff — male-consistent) + chrY rate **just above**
        ``_THRESHOLD_PAR_NOISE`` (7/60 ≈ 0.117, below the 0.30 confirm) →
        ``manual_review``. Pins the candidate-XY chrY band boundary: a male's
        X-het noise plus an unconfirmed chrY rate is reviewed, not auto-called
        (issue #519)."""
        _seed(
            sample_engine,
            [*_x_rows(het=1, hom=119), *_y_rows(typed=7, nocall=53)],
        )
        assert infer_biological_sex(sample_engine) == "manual_review"

    def test_iii_bis_classify_helper_escalates_discordant_x_het_and_y_signal(
        self,
    ) -> None:
        """Direct ``_classify`` assertion of the diploid-X / chrY boundary.

        With a *diploid-X* non-PAR chrX het rate (here 60/120 = 0.50, above the
        0.15 diploid cutoff — issue #519) the classifier returns ``"XX"`` only
        while chrY remains at/below the PAR-noise floor. Anything above that
        floor returns ``manual_review`` (the discordant two-X + chrY / XXY
        pattern) instead of silently reporting ordinary XX."""
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
                    x_nonpar_het=60,  # 60/120 = 0.50 — diploid-X het rate
                    x_nonpar_typed=_EVAL_X,
                    x_nonpar_hom=_EVAL_X - 60,
                    y_total=_EVAL_Y,
                    y_rate=y_rate,
                )
                == expected
            ), f"_classify misclassified discordant X/Y evidence at y_rate={y_rate}"


def test_threshold_validation_doc_exists_and_matches_constants() -> None:
    """#435: the referenced threshold-validation doc must exist (it was never
    committed at Step 53, leaving several code/README/CHANGELOG references dangling)
    and must stay in sync with the live constants so it can't silently drift."""
    doc = (
        Path(__file__).resolve().parents[2]
        / "docs"
        / "internal"
        / "sex_inference_threshold_validation.md"
    )
    assert doc.exists(), "referenced sex-inference threshold-validation doc is missing (#435)"
    text = doc.read_text(encoding="utf-8")
    # The doc documents the live constant values, so it cannot drift from code.
    assert "0.30" in text and str(_THRESHOLD_XY_CONFIRM) in text
    assert "0.10" in text and str(_THRESHOLD_PAR_NOISE) in text
    # issue #519 — X-het rate cutoffs must be documented too.
    assert "0.05" in text and str(_THRESHOLD_X_HET_HEMIZYGOUS) in text
    assert "0.15" in text and str(_THRESHOLD_X_HET_DIPLOID) in text
    assert str(MIN_X_NONPAR_TYPED) in text  # 100
    assert str(MIN_Y_PROBES) in text  # 50
    assert "validate_sex_thresholds.py" in text  # reproduction command
    # Lock the honest framing so it can't drift back to claiming a fresh signed run.
    assert "reconstructed provenance" in text

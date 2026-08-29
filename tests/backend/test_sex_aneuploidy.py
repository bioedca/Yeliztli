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
    MANUAL_REVIEW,
    MODULE,
    NO_SIGNAL,
    POSSIBLE_XXY,
    Y_PRESENT_RATE,
    screen_aneuploidy,
    store_aneuploidy_findings,
)
from backend.db.tables import findings, raw_variants, sample_metadata_obj
from backend.services.sex_inference import (
    THRESHOLD_X_HET_DIPLOID,
    THRESHOLD_X_HET_HEMIZYGOUS,
    THRESHOLD_Y_PAR_NOISE,
    infer_biological_sex,
)


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


def _y_dash_nocalls(n: int) -> list[dict]:
    rows = []
    pos = 8_000_000
    for i in range(n):
        rows.append({"rsid": f"y_dash_nc{i}", "chrom": "Y", "pos": pos, "genotype": "-"})
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
        _seed(sample_engine, _x_probes(60, 60) + _y_probes(6, 60))
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

    def test_normal_male_xhet_noise_no_signal(self, sample_engine: sa.Engine) -> None:
        """issue #633 — a normal 46,XY male carries a few non-PAR chrX het calls as
        genotyping noise (the real AncestryDNA male in the issue: 91 het / 27411
        typed ≈ 0.33%). His X-het *rate* is far below the diploid-X cutoff, so the
        screen must report NO_SIGNAL. Under the old ``>= 2`` count threshold this
        same sample (5 het calls) falsely screened as possible_xxy."""
        _seed(sample_engine, _x_probes(5, 500) + _y_probes(60))
        r = screen_aneuploidy(sample_engine)
        assert r.outcome == NO_SIGNAL
        assert r.x_evaluable and r.y_evaluable
        assert r.x_nonpar_het == 5  # noise present (>2), but the rate is ~0.01

    def test_two_x_decided_on_rate_not_count(self, sample_engine: sa.Engine) -> None:
        """A sizable absolute het count stays NO_SIGNAL when the *rate* is at or
        below the hemizygous (one-X) cutoff — the decision is rate-based, not a
        count. Here 15 het / 700 typed ≈ 2.1%, which is male-consistent noise
        despite 15 being far above any plausible count threshold.

        The rate deliberately sits in the *hemizygous* zone rather than the
        ambiguous band. This test used to seed 9.1%, which is the band the
        classifier refuses to resolve, and asserted a clean negative — pinning
        #2040 as correct. The rate-not-count point does not need that genotype,
        and the ambiguous band now has its own tests below.
        """
        _seed(sample_engine, _x_probes(15, 685) + _y_probes(60))
        r = screen_aneuploidy(sample_engine)
        assert r.outcome == NO_SIGNAL
        assert r.x_nonpar_het == 15
        assert r.x_nonpar_typed == 700
        # The count is large; only the rate keeps this a negative.
        assert r.x_nonpar_het / r.x_nonpar_typed <= THRESHOLD_X_HET_HEMIZYGOUS
        # ...and the classifier agrees this is resolvable, which is what makes a
        # clean negative legitimate here (contrast the ambiguous-band tests).
        assert infer_biological_sex(sample_engine) == "XY"

    def test_ambiguous_x_band_with_y_present_is_manual_review(
        self, sample_engine: sa.Engine
    ) -> None:
        """The #2040 case, and the sharpest one: a present chrY plus an X-het
        rate just under the diploid cutoff.

        Only the X-het sitting at 9.1% instead of ≥15% separates this from a
        ``possible_xxy`` call, and the screen used to answer with an affirmative
        "no XXY signature detected". Withholding is not a positive call — an
        intermediate rate can equally be array noise.
        """
        _seed(sample_engine, _x_probes(20, 200) + _y_probes(60))

        r = screen_aneuploidy(sample_engine)

        assert r.outcome == MANUAL_REVIEW
        assert r.x_evaluable and r.y_evaluable
        assert r.y_rate > Y_PRESENT_RATE
        rate = r.x_nonpar_het / r.x_nonpar_typed
        assert THRESHOLD_X_HET_HEMIZYGOUS < rate < THRESHOLD_X_HET_DIPLOID
        assert infer_biological_sex(sample_engine) == "manual_review"

    def test_ambiguous_x_band_without_a_y_stays_a_clean_negative(
        self, sample_engine: sa.Engine
    ) -> None:
        """An absent chrY rules XXY out however the X reads.

        The XXY signature is two X chromosomes *and* a Y. With chrY at or below
        the PAR-noise floor there is no Y to pair with any X count, so "no XXY
        genotype signature detected" is accurate even though sex inference still
        cannot resolve this sample's X dosage.

        This is the same reasoning that keeps a confidently hemizygous sample a
        clean negative, applied to the other chromosome — escalating here would
        make the screen's manual-review band wider than the question it answers.
        Pairs with the case above, where the only difference is the chrY signal.
        """
        _seed(sample_engine, _x_probes(20, 200) + _y_probes(0, 60))

        r = screen_aneuploidy(sample_engine)

        assert r.outcome == NO_SIGNAL
        assert r.y_rate == 0.0
        assert r.y_evaluable
        # Sex inference still declines — the screen's narrower question is what
        # makes a negative legitimate, not agreement between the two.
        assert infer_biological_sex(sample_engine) == "manual_review"

    def test_ambiguous_x_with_subthreshold_y_is_manual_review(
        self, sample_engine: sa.Engine
    ) -> None:
        """The band between the noise floor and the presence threshold (#2040).

        ``y_discordant`` is ``y_rate > THRESHOLD_Y_PAR_NOISE`` (0.10) while
        ``y_present`` is ``y_rate > Y_PRESENT_RATE`` (0.30), so a rate in
        between escalates without the Y ever counting as present. That is
        correct — a Y too weak to call is not a Y ruled out — but it is a
        distinct band from the chrY-present case above and the copy must not
        claim the Y is present.
        """
        _seed(sample_engine, _x_probes(20, 200) + _y_probes(12, 48))

        r = screen_aneuploidy(sample_engine)

        assert r.outcome == MANUAL_REVIEW
        assert THRESHOLD_Y_PAR_NOISE < r.y_rate <= Y_PRESENT_RATE
        store_aneuploidy_findings(r, sample_engine)
        with sample_engine.connect() as conn:
            text = conn.execute(
                sa.select(findings.c.finding_text).where(findings.c.module == MODULE)
            ).scalar_one()
        assert "above the background noise floor" in text
        assert "too weak to meet this screen's presence threshold" in text
        # It must not assert presence for evidence below the presence threshold.
        assert "chromosome-Y signal is also present" not in text

    def test_manual_review_text_names_the_ambiguous_x_cause(
        self, sample_engine: sa.Engine
    ) -> None:
        """The stored text must describe the cause it actually has.

        The pre-existing MANUAL_REVIEW copy names a diploid-X/discordant-Y
        pattern, which is not what happened here; a user reading it would be
        told about a signal their sample does not have.
        """
        _seed(sample_engine, _x_probes(20, 200) + _y_probes(60))
        r = screen_aneuploidy(sample_engine)
        store_aneuploidy_findings(r, sample_engine)

        with sample_engine.connect() as conn:
            text = conn.execute(
                sa.select(findings.c.finding_text).where(findings.c.module == MODULE)
            ).scalar_one()

        assert "could not be determined" in text
        assert "NOT a clean negative" in text
        assert "chromosome-Y signal above the background noise floor" in text
        # Neither a positive call nor the other branch's wording.
        assert "NOT a positive finding" in text
        assert "diploid-X signal together with a chrY signal" not in text
        assert "Klinefelter" not in text
        # The copy must not explain the biology behind an intermediate level,
        # nor how the screen measures X dosage — either is a claim this change
        # does not evidence, and the mechanism wording drifted back in twice
        # before this assertion covered it.
        for claim in (
            "mosaic",
            "isodisomic",
            "47,XXY",
            "chromosome complement",
            "heterozygosity",
            "heterozygous",
            "copy number",
        ):
            assert claim not in text, f"unevidenced mechanism claim {claim!r} in patient copy"
        # Nor recommend a specific confirmatory assay the module has never
        # supported. An earlier revision said "confirm with clinical karyotyping
        # or FISH"; FISH was new clinical advice arriving with no evidence
        # packet behind it. The copy reuses the module's existing wording
        # instead, so this is about what the app may recommend, not about FISH's
        # merits.
        assert "FISH" not in text, "unevidenced confirmatory assay recommended in patient copy"
        assert "orthogonal chromosome-copy-number evidence" in text

    def test_diploid_rate_xhet_with_y_is_possible_xxy(self, sample_engine: sa.Engine) -> None:
        """A female-level X-het rate (above the diploid-X cutoff) co-occurring with
        a present chrY is the XXY signature → possible_xxy. 40 het / 160 typed =
        25%."""
        _seed(sample_engine, _x_probes(40, 120) + _y_probes(60))
        r = screen_aneuploidy(sample_engine)
        assert r.outcome == POSSIBLE_XXY

    def test_two_x_at_y_present_boundary_stays_manual_review(
        self, sample_engine: sa.Engine
    ) -> None:
        # Exactly 0.30 is not Y-present under the strict cutoff.
        _seed(sample_engine, _x_probes(60, 60) + _y_probes(18, 42))
        r = screen_aneuploidy(sample_engine)
        assert Y_PRESENT_RATE == 0.30
        assert r.y_total == 60
        assert r.y_rate == 0.3
        assert r.outcome == MANUAL_REVIEW

    def test_two_x_just_above_y_present_boundary_is_possible_xxy(
        self, sample_engine: sa.Engine
    ) -> None:
        # 19 / 60 ≈ 0.317 is just above the Y-present cutoff.
        _seed(sample_engine, _x_probes(60, 60) + _y_probes(19, 41))
        r = screen_aneuploidy(sample_engine)
        assert r.y_total == 60
        assert r.y_rate == 0.3167
        assert r.outcome == POSSIBLE_XXY

    def test_dash_y_nocalls_do_not_create_possible_xxy(self, sample_engine: sa.Engine) -> None:
        """Diploid-X signal plus haploid 23andMe ``"-"`` chrY no-calls is not
        a present-Y signal and must not screen as possible XXY (#1717)."""
        _seed(sample_engine, _x_probes(60, 60) + _y_dash_nocalls(60))

        r = screen_aneuploidy(sample_engine)

        assert r.outcome == NO_SIGNAL
        assert r.x_evaluable and r.y_evaluable
        assert r.y_rate == 0.0
        assert infer_biological_sex(sample_engine) == "XX"

    def test_diploid_x_with_intermediate_y_signal_needs_manual_review(
        self, sample_engine: sa.Engine
    ) -> None:
        """Diploid-X plus chrY above the shared PAR-noise floor must not become a
        clean negative screen while sex inference asks for manual review (#1130)."""
        _seed(sample_engine, _x_probes(60, 60) + _y_probes(12, 48))

        r = screen_aneuploidy(sample_engine)

        assert r.outcome == MANUAL_REVIEW
        assert r.x_evaluable and r.y_evaluable
        assert r.y_rate == 0.2
        assert infer_biological_sex(sample_engine) == "manual_review"

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

    def test_manual_review_screen_does_not_read_as_negative(
        self, sample_engine: sa.Engine
    ) -> None:
        _seed(sample_engine, _x_probes(60, 60) + _y_probes(12, 48))
        r = screen_aneuploidy(sample_engine)
        store_aneuploidy_findings(r, sample_engine)
        with sample_engine.connect() as conn:
            row = conn.execute(sa.select(findings).where(findings.c.module == MODULE)).fetchone()

        text = row.finding_text.lower()
        assert row.conditions == "Sex-chromosome aneuploidy screen: manual_review"
        assert "manual review" in text
        assert "not a clean negative" in text
        assert "no xxy" not in text

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


class TestClassifierAndScreenNeverContradict:
    """The screen must not answer a clean negative where XXY is still possible.

    #1130 established this on the chrY axis and #2040 on the X axis. The
    invariant is deliberately narrower than "any ``manual_review`` forbids
    ``NO_SIGNAL``", and the scope is reasoned rather than fitted to the tests:

    The XXY signature is two X chromosomes **and** a Y. Either chromosome can
    rule it out on its own, and when one does, a clean negative is accurate even
    though sex inference still declines to resolve the sample:

    * a confidently hemizygous X (rate ≤ ``THRESHOLD_X_HET_HEMIZYGOUS``) is one
      X, so no chrY reading can make it XXY;
    * a chrY at or below ``THRESHOLD_Y_PAR_NOISE`` is no Y, so no X reading can.

    In both the classifier's uncertainty is about *sex assignment*, not about
    XXY. Forbidding a clean negative in the first would fire on ordinary 46,XY
    males whose chrY signal is merely degraded (rate 0.10–0.30) — turning a
    psychosocially gated screen into a false-alarm generator, harm in the
    opposite direction.

    What remains forbidden is the genuinely undecidable case: an X dosage the
    classifier cannot resolve while a chrY signal is present. This sweeps the
    rate space so a future threshold change that re-opens a gap fails here.
    """

    # (het, hom) pairs spanning hemizygous, ambiguous and diploid X zones, and
    # chrY typed counts spanning absent, PAR-noise, intermediate and present.
    X_SHAPES = ((0, 220), (5, 215), (15, 205), (20, 200), (28, 192), (48, 172), (90, 130))
    Y_TYPED = (0, 3, 12, 25, 50, 60)

    def _grid(self) -> list[tuple[float, float, str, str]]:
        rows = []
        for n_het, n_hom in self.X_SHAPES:
            for y_typed in self.Y_TYPED:
                engine = sa.create_engine("sqlite://")
                sample_metadata_obj.create_all(engine)
                _seed(engine, _x_probes(n_het, n_hom) + _y_probes(y_typed, 60 - y_typed))
                r = screen_aneuploidy(engine)
                rows.append(
                    (n_het / (n_het + n_hom), r.y_rate, infer_biological_sex(engine), r.outcome)
                )
        return rows

    @staticmethod
    def _xxy_still_possible(x_rate: float, y_rate: float) -> bool:
        """Neither chromosome rules XXY out on its own."""
        return x_rate > THRESHOLD_X_HET_HEMIZYGOUS and y_rate > THRESHOLD_Y_PAR_NOISE

    def test_an_undecidable_sample_is_never_a_clean_negative(self) -> None:
        grid = self._grid()
        assert len(grid) == len(self.X_SHAPES) * len(self.Y_TYPED)

        offenders = [
            f"x_het={x:.3f} y_rate={y:.3f} -> {outcome}"
            for x, y, inferred, outcome in grid
            if inferred == "manual_review"
            and self._xxy_still_possible(x, y)
            and outcome == NO_SIGNAL
        ]
        assert not offenders, (
            "sex inference could not resolve the sample and XXY was not ruled out "
            f"by either chromosome, yet the screen reported a clean negative at: {offenders}"
        )

    def test_the_sweep_actually_reaches_the_undecidable_zone(self) -> None:
        """Without this the invariant above could hold vacuously."""
        reached = [
            (x, y, outcome)
            for x, y, inferred, outcome in self._grid()
            if inferred == "manual_review" and self._xxy_still_possible(x, y)
        ]
        assert reached, "no grid point was undecidable — the invariant is vacuous"
        outcomes = {outcome for _x, _y, outcome in reached}
        # A clean negative is the forbidden answer. POSSIBLE_XXY is legitimate:
        # a diploid X with a present chrY is the screen's positive call, and the
        # classifier reports manual_review for it because the X/Y combination is
        # discordant for a binary sex assignment.
        assert NO_SIGNAL not in outcomes
        assert MANUAL_REVIEW in outcomes

    def test_every_excused_clean_negative_is_ruled_out_by_a_chromosome(self) -> None:
        """The exclusions must not quietly widen past what actually excludes XXY.

        Every grid point where a ``manual_review`` classification still yields a
        clean negative has to be one the screen can legitimately answer: one X,
        or no Y. If a future change lets some other shape through, this fails.
        """
        excused = [
            (x, y)
            for x, y, inferred, outcome in self._grid()
            if inferred == "manual_review" and outcome == NO_SIGNAL
        ]
        assert excused, "no manual_review/clean-negative pair — this exclusion is now dead"
        for x_rate, y_rate in excused:
            one_x = x_rate <= THRESHOLD_X_HET_HEMIZYGOUS
            no_y = y_rate <= THRESHOLD_Y_PAR_NOISE
            assert one_x or no_y, (
                f"excused a sample where XXY is not ruled out "
                f"(x_het={x_rate:.3f}, y_rate={y_rate:.3f})"
            )

    def test_both_exclusion_reasons_are_exercised(self) -> None:
        """Each excusing chromosome must appear, so neither arm is dead code."""
        excused = [
            (x, y)
            for x, y, inferred, outcome in self._grid()
            if inferred == "manual_review" and outcome == NO_SIGNAL
        ]
        assert any(x <= THRESHOLD_X_HET_HEMIZYGOUS for x, _y in excused), "one-X arm unexercised"
        assert any(y <= THRESHOLD_Y_PAR_NOISE for _x, y in excused), "no-Y arm unexercised"

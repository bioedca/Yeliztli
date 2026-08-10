"""Tests for the Runs-of-Homozygosity (ROH / FROH) autozygosity module.

Exercises the clean-room sliding-run detector: a clean long homozygous run is
detected as an ROH segment; FROH = segment length / fixed autosomal denominator;
scattered genotype-error hets survive under a local 50-SNP rule, while
heterozygous-rich regions and too-short runs produce nothing; a large
position-gap breaks a run; and the finding is framed as a genomic estimate
("not a diagnosis", "not a statement about whether your parents are related"),
stored at evidence_level 1.
"""

from __future__ import annotations

import json

import pytest
import sqlalchemy as sa

from backend.analysis.roh import (
    AUTOSOMAL_GENOME_KB,
    DETAIL_UNAVAILABLE,
    MEASUREMENT_OUT_OF_BOUNDS,
    MIN_ROH_SNPS,
    MODULE,
    NO_SEGMENT_ELIGIBLE_REGION,
    _genotype_state,
    detect_roh,
    evaluability_from_detail,
    store_roh_findings,
    unevaluable_text,
)
from backend.db.tables import findings, raw_variants


def _seed(engine: sa.Engine, rows: list[dict]) -> None:
    if rows:
        with engine.begin() as conn:
            conn.execute(sa.insert(raw_variants), rows)


def _run(
    chrom: str,
    start: int,
    count: int,
    *,
    spacing: int = 10_000,
    genotype: str = "AA",
    rs_prefix: str = "r",
) -> list[dict]:
    """Generate `count` evenly-spaced SNP rows on `chrom` from `start`."""
    return [
        {
            "rsid": f"{rs_prefix}{chrom}_{i}",
            "chrom": chrom,
            "pos": start + i * spacing,
            "genotype": genotype,
        }
        for i in range(count)
    ]


# Independent GRCh37/hg19 autosome lengths from the vendored UCSC liftOver
# chain at `backend/data/chains/hg19ToHg38.over.chain.gz`. The production
# detector does not contain this table: the regression uses it only to construct
# an accepted dense-coordinate witness for the numerator/denominator mismatch.
_GRCH37_AUTOSOME_LENGTHS_BP = (
    249_250_621,
    243_199_373,
    198_022_430,
    191_154_276,
    180_915_260,
    171_115_067,
    159_138_663,
    146_364_022,
    141_213_431,
    135_534_747,
    135_006_516,
    133_851_895,
    115_169_878,
    107_349_540,
    102_531_392,
    90_354_753,
    81_195_210,
    78_077_248,
    59_128_983,
    63_025_520,
    48_129_895,
    51_304_566,
)


class TestGenotypeState:
    def test_homozygous(self) -> None:
        assert _genotype_state("AA") == "hom"
        assert _genotype_state("gg") == "hom"

    def test_heterozygous(self) -> None:
        assert _genotype_state("AG") == "het"

    def test_missing_variants(self) -> None:
        for gt in ("--", "", "A", "DD", "II", "0", None):
            assert _genotype_state(gt) == "miss"


class TestFrohDenominator:
    """Pin the FROH denominator so a wrong genome-size constant fails loudly.

    ``AUTOSOMAL_GENOME_KB`` is the ~2.77 Gb ungapped autosomal (chr1–22) genome
    length used as the FROH denominator: FROH (the fraction of the autosomal
    genome in runs of homozygosity) is the module's autozygosity/consanguinity
    metric, and this constant sets the magnitude of every FROH value
    (``FROH = Σ ROH length / AUTOSOMAL_GENOME_KB``; McQuillan et al. 2008, AJHG;
    Ceballos et al. 2018, Nat Rev Genet). A digit typo — e.g. ``277_000`` (10×)
    — would scale every sample's autozygosity estimate with green CI, because
    the magnitude assertion in ``TestDetection`` used to re-divide by this same
    imported constant (it cancelled on both sides). These tests anchor the value
    to an independent literal instead.
    """

    def test_constant_pinned_to_literal(self) -> None:
        # 2.77 Gb = the ungapped autosomal sequence length (GRCh37 chr1–22 total
        # is ~2.88 Gb including N-gaps; ~2.77 Gb of called sequence). Maintained
        # by hand as an independent reference — do NOT derive it from the module.
        assert AUTOSOMAL_GENOME_KB == 2_770_000

    def test_denominator_is_gigabase_scale_autosomal_length(self) -> None:
        # Guard against an order-of-magnitude regression independent of the exact
        # literal: the autosomal genome is ~2.6–2.9 Gb of called sequence.
        assert 2_600_000 <= AUTOSOMAL_GENOME_KB <= 2_900_000


class TestStoredEvaluability:
    @pytest.mark.parametrize("reason", [[], {}])
    def test_unhashable_reason_is_withheld_without_raising(self, reason: object) -> None:
        assert evaluability_from_detail(
            {
                "evaluable": False,
                "indeterminate_reason": reason,
                "froh": None,
                "autosomal_snps_used": 361,
            }
        ) == (False, 361, DETAIL_UNAVAILABLE)

    def test_unknown_direct_reason_uses_generic_narrative(self) -> None:
        text = unevaluable_text(361, "some_future_reason").lower()

        assert "could not be read or validated" in text
        assert "no autosome carries" not in text

    def test_legacy_metric_cannot_bypass_current_coordinate_bounds(
        self, sample_engine: sa.Engine
    ) -> None:
        # This is the pre-gate shape from the final local-review witness: a
        # measured zero with no explicit verdict. Ingestion permits the 2.8-Gb
        # positions, but a fresh detector withholds them, so legacy validation
        # must reach the same result rather than serving FROH=0.
        _seed(sample_engine, _run("1", 2_800_000_000, 200, genotype="AG"))
        detail = {
            "froh": 0.0,
            "total_roh_kb": 0.0,
            "longest_kb": 0.0,
            "n_segments": 0,
            "autosomal_snps_used": 200,
            "segments": [],
        }

        assert evaluability_from_detail(detail, sample_engine) == (
            False,
            200,
            MEASUREMENT_OUT_OF_BOUNDS,
        )

    def test_stored_measurement_reason_is_revalidated_against_current_sample(
        self, sample_engine: sa.Engine
    ) -> None:
        # A re-import can replace the input beneath a persisted finding. Do not
        # keep claiming an out-of-bounds measurement after the current detector
        # proves the ordinary replacement sample is measurable.
        _seed(sample_engine, _run("1", 1_000_000, 200))
        detail = {
            "evaluable": False,
            "indeterminate_reason": MEASUREMENT_OUT_OF_BOUNDS,
            "froh": None,
            "autosomal_snps_used": 200,
        }

        assert evaluability_from_detail(detail, sample_engine) == (
            False,
            200,
            DETAIL_UNAVAILABLE,
        )

    @pytest.mark.parametrize("stored_count", [None, 30], ids=["missing", "stale"])
    def test_generic_reason_reports_current_count_after_revalidation(
        self, sample_engine: sa.Engine, stored_count: int | None
    ) -> None:
        # Once the current detector has run to check whether the generic reason
        # can be upgraded, its observed count is authoritative. Do not pair the
        # resulting current verdict with a missing-count default or stale count.
        _seed(sample_engine, _run("1", 1_000_000, 200))
        detail = {
            "evaluable": False,
            "indeterminate_reason": DETAIL_UNAVAILABLE,
            "froh": None,
        }
        if stored_count is not None:
            detail["autosomal_snps_used"] = stored_count

        assert evaluability_from_detail(detail, sample_engine) == (
            False,
            200,
            DETAIL_UNAVAILABLE,
        )

    def test_unrelated_malformed_metric_reads_current_sample_once(
        self, sample_engine: sa.Engine, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A malformed value that is not an old over-bound writer result stays
        # generic. Re-running the full detector after the coverage scan would
        # read a 594k-marker sample twice merely to rediscover that safe answer.
        import backend.analysis.roh as roh

        _seed(sample_engine, _run("1", 1_000_000, 200))
        original = roh._read_autosomal_states
        reads = 0

        def counted_read(engine: sa.Engine):
            nonlocal reads
            reads += 1
            return original(engine)

        monkeypatch.setattr(roh, "_read_autosomal_states", counted_read)

        assert evaluability_from_detail(
            {"froh": -3, "autosomal_snps_used": 200}, sample_engine
        ) == (False, 200, DETAIL_UNAVAILABLE)
        assert reads == 1


class TestDetection:
    def test_clean_long_run_detected(self, sample_engine: sa.Engine) -> None:
        # 200 hom SNPs, 10 kb spacing → ~1990 kb span, 200 SNPs (≥1500 kb, ≥100).
        _seed(sample_engine, _run("1", 1_000_000, 200))
        result = detect_roh(sample_engine)
        assert len(result.segments) == 1
        seg = result.segments[0]
        assert seg.chrom == "1"
        assert seg.n_snps == 200
        assert seg.length_kb == pytest.approx(1990.0, abs=1.0)
        # FROH magnitude is pinned to a LITERAL denominator written here, NOT the
        # imported production constant — otherwise AUTOSOMAL_GENOME_KB cancels on
        # both sides and a wrong genome-size denominator passes (see
        # TestFrohDenominator). The 1990 kb single segment over the 2.77 Gb
        # autosomal genome gives FROH = round(1990.0 / 2_770_000, 5) = 0.00072.
        assert seg.length_kb == 1990.0
        assert result.froh == pytest.approx(0.00072, abs=5e-6)

    def test_span_equal_to_denominator_remains_measured(
        self, sample_engine: sa.Engine, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Pin the strict side of the writer guard: this ordinary 1,990 kb run is
        # exactly one patched denominator, so changing `>` to `>=` would suppress
        # a valid FROH=1 boundary result.
        import backend.analysis.roh as roh

        _seed(sample_engine, _run("1", 1_000_000, 200))
        monkeypatch.setattr(roh, "AUTOSOMAL_GENOME_KB", 1_990.0)

        result = roh.detect_roh(sample_engine)

        assert result.evaluable
        assert result.total_roh_kb == 1_990.0
        assert result.froh == 1.0
        assert result.indeterminate_reason is None

    def test_coordinate_at_reader_ceiling_remains_measured(self, sample_engine: sa.Engine) -> None:
        # The persisted-coordinate validator accepts the inclusive
        # 2,770,000,000-bp ceiling. End a normal 1,990-kb run exactly there so a
        # `>=` writer guard cannot silently make the two domains disagree.
        _seed(sample_engine, _run("1", 2_768_010_000, 200))

        result = detect_roh(sample_engine)

        assert result.evaluable
        assert len(result.segments) == 1
        assert result.segments[0].start == 2_768_010_000
        assert result.segments[0].end == 2_770_000_000
        assert result.total_roh_kb == 1_990.0
        assert result.froh == 0.00072

    def test_short_run_not_detected(self, sample_engine: sa.Engine) -> None:
        # 50 hom SNPs over ~490 kb — below both segment thresholds. The sample
        # is kept evaluable by a chr2 region that *could* hold a run (100 markers
        # spanning 1980 kb) but is entirely heterozygous, so this asserts the
        # short run is rejected on its own merits rather than because the sample
        # was unevaluable.
        _seed(
            sample_engine,
            _run("1", 1_000_000, 50)
            + _run("2", 1_000_000, 100, spacing=20_000, genotype="AG", rs_prefix="h"),
        )
        result = detect_roh(sample_engine)
        assert result.evaluable
        assert result.segments == []
        assert result.froh == 0.0

    def test_heterozygous_region_not_detected(self, sample_engine: sa.Engine) -> None:
        _seed(sample_engine, _run("1", 1_000_000, 200, genotype="AG"))
        result = detect_roh(sample_engine)
        assert result.segments == []

    def test_single_het_within_tolerance_keeps_run(self, sample_engine: sa.Engine) -> None:
        rows = _run("1", 1_000_000, 200)
        rows[100]["genotype"] = "AG"  # one isolated het is valid in its local window
        _seed(sample_engine, rows)
        result = detect_roh(sample_engine)
        assert len(result.segments) == 1
        # The single het is not counted as a homozygous SNP.
        assert result.segments[0].n_snps == 199

    def test_scattered_het_errors_preserve_long_roh(self, sample_engine: sa.Engine) -> None:
        # A 12 Mb run at 5 kb spacing has 2,401 typed SNPs. Seven evenly
        # scattered error hets (~0.29%) must not fragment the true long ROH.
        rows = _run("1", 1_000_000, 2401, spacing=5_000)
        for index in range(300, 2400, 300):
            rows[index]["genotype"] = "AG"
        _seed(sample_engine, rows)

        result = detect_roh(sample_engine)

        assert len(result.segments) == 1
        segment = result.segments[0]
        assert segment.start == 1_000_000
        assert segment.end == 13_000_000
        assert segment.length_kb == 12_000.0
        assert segment.n_snps == 2394
        assert result.longest_kb == 12_000.0
        assert result.total_roh_kb == 12_000.0

    def test_dense_heterozygous_stretch_splits_roh(self, sample_engine: sa.Engine) -> None:
        # Two ~2 Mb homozygous blocks flank a 2 Mb heterozygous stretch. The
        # local allowance must keep the error tolerance from bridging that
        # genuinely non-autozygous region into one apparent 6 Mb ROH.
        rows = _run("1", 1_000_000, 1201, spacing=5_000)
        for index in range(400, 801):
            rows[index]["genotype"] = "AG"
        _seed(sample_engine, rows)

        result = detect_roh(sample_engine)

        assert len(result.segments) == 2
        assert [segment.length_kb for segment in result.segments] == [1995.0, 1995.0]
        assert result.segments[0].end < rows[400]["pos"]
        assert result.segments[1].start > rows[800]["pos"]

    def test_large_gap_breaks_run(self, sample_engine: sa.Engine) -> None:
        # Two qualifying blocks (each ~1990 kb, 200 SNPs) separated by a 2 Mb gap
        # (> MAX_GAP_KB=1000 kb), so the gap rule must split them into two ROHs.
        block_a = _run("1", 1_000_000, 200, rs_prefix="a")  # ends at 2_990_000
        block_b = _run("1", 4_990_000, 200, rs_prefix="b")  # 2 Mb gap before it
        _seed(sample_engine, block_a + block_b)
        result = detect_roh(sample_engine)
        assert len(result.segments) == 2

    def test_outbred_sample_no_roh(self, sample_engine: sa.Engine) -> None:
        # Alternating hom/het across the genome → no long runs.
        rows = []
        for i in range(400):
            rows.append(
                {
                    "rsid": f"r{i}",
                    "chrom": "2",
                    "pos": 1_000_000 + i * 10_000,
                    "genotype": "AA" if i % 2 == 0 else "AG",
                }
            )
        _seed(sample_engine, rows)
        result = detect_roh(sample_engine)
        assert result.segments == []


class TestEvaluability:
    """#2177 — an empty scan over too few markers is not evidence of no ROH.

    When no region could satisfy the per-chromosome segment rules the detector
    cannot emit anything under any genome, so ``FROH = 0`` would be an artifact
    of the scan. These lock the withheld states *and* the boundary at which a
    real negative resumes being reported.
    """

    def test_no_autosomal_markers_is_indeterminate(self, sample_engine: sa.Engine) -> None:
        result = detect_roh(sample_engine)
        assert result.autosomal_snps_used == 0
        assert result.froh is None
        assert not result.evaluable
        assert result.indeterminate_reason == NO_SEGMENT_ELIGIBLE_REGION

    def test_single_callable_marker_is_indeterminate(self, sample_engine: sa.Engine) -> None:
        _seed(sample_engine, _run("1", 1_000_000, 1))
        result = detect_roh(sample_engine)
        assert result.autosomal_snps_used == 1
        assert result.froh is None
        assert result.indeterminate_reason == NO_SEGMENT_ELIGIBLE_REGION

    def test_single_autosomal_no_call_is_indeterminate(self, sample_engine: sa.Engine) -> None:
        _seed(sample_engine, _run("1", 1_000_000, 1, genotype="--"))
        result = detect_roh(sample_engine)
        assert result.autosomal_snps_used == 0
        assert result.froh is None
        assert result.indeterminate_reason == NO_SEGMENT_ELIGIBLE_REGION

    def test_just_below_floor_is_indeterminate(self, sample_engine: sa.Engine) -> None:
        _seed(sample_engine, _run("1", 1_000_000, MIN_ROH_SNPS - 1))
        result = detect_roh(sample_engine)
        assert result.autosomal_snps_used == MIN_ROH_SNPS - 1
        assert result.froh is None
        # Pin the cause, not just the outcome. At the default 10 kb spacing this
        # fixture also spans under MIN_ROH_KB, so without asserting the reason the
        # test passes whichever floor fired and does not discriminate the
        # marker-count rule it is named for.
        assert not result.evaluable
        assert result.indeterminate_reason == NO_SEGMENT_ELIGIBLE_REGION

    def test_at_structural_minimum_reports_a_real_negative(self, sample_engine: sa.Engine) -> None:
        # The discriminating control: a region that *could* hold a run — 100
        # markers spanning >= MIN_ROH_KB with no oversized gap — is evaluable,
        # and a genuine "no ROH" negative must still be reported as FROH = 0
        # rather than swallowed by the gate. Alternating calls keep it negative.
        rows = _run("1", 1_000_000, MIN_ROH_SNPS, spacing=20_000)  # spans 1980 kb
        for index in range(1, len(rows), 2):
            rows[index]["genotype"] = "AG"
        _seed(sample_engine, rows)
        result = detect_roh(sample_engine)
        assert result.evaluable
        assert result.indeterminate_reason is None
        assert result.froh == 0.0
        assert result.segments == []

    def test_enough_markers_packed_too_tightly_is_indeterminate(
        self, sample_engine: sa.Engine
    ) -> None:
        # 100 markers clear any count-only floor but span just 99 kb, far under
        # MIN_ROH_KB, so no run could ever be reported from them.
        _seed(sample_engine, _run("1", 1_000_000, MIN_ROH_SNPS, spacing=1_000))
        result = detect_roh(sample_engine)
        assert result.autosomal_snps_used == MIN_ROH_SNPS
        assert result.froh is None
        assert result.indeterminate_reason == NO_SEGMENT_ELIGIBLE_REGION

    def test_markers_split_across_autosomes_cannot_emit(self, sample_engine: sa.Engine) -> None:
        # 60 + 60 markers clear a genome-wide count floor, but a segment lives
        # inside ONE chromosome and needs MIN_ROH_SNPS of them, so neither
        # autosome can ever produce one.
        _seed(
            sample_engine,
            _run("1", 1_000_000, 60, spacing=50_000)
            + _run("2", 1_000_000, 60, spacing=50_000, rs_prefix="b"),
        )
        result = detect_roh(sample_engine)
        assert result.autosomal_snps_used == 120
        assert result.froh is None
        assert result.indeterminate_reason == NO_SEGMENT_ELIGIBLE_REGION

    def test_oversized_gaps_prevent_an_eligible_region(self, sample_engine: sa.Engine) -> None:
        # 200 markers on one autosome, but every neighbour pair is separated by
        # more than MAX_GAP_KB, so no run can span two of them at all.
        _seed(sample_engine, _run("1", 1_000_000, 200, spacing=2_000_000))
        result = detect_roh(sample_engine)
        assert result.autosomal_snps_used == 200
        assert result.froh is None
        assert result.indeterminate_reason == NO_SEGMENT_ELIGIBLE_REGION

    def test_heterozygosity_rich_sample_stays_a_real_negative(
        self, sample_engine: sa.Engine
    ) -> None:
        # Withholding must not swallow the clearest true negative there is: a
        # densely typed, heterozygous sample has ample evidence *against*
        # autozygosity even though it contains no homozygous run at all.
        _seed(sample_engine, _run("1", 1_000_000, 400, genotype="AG"))
        result = detect_roh(sample_engine)
        assert result.evaluable
        assert result.froh == 0.0

    def test_eligibility_is_per_chromosome_not_genome_wide(self, sample_engine: sa.Engine) -> None:
        # One autosome that could hold a run makes the sample evaluable even
        # though a second autosome could not — eligibility is existential over
        # chromosomes, so a sparse chromosome never suppresses a usable one.
        _seed(
            sample_engine,
            _run("1", 1_000_000, MIN_ROH_SNPS, spacing=20_000, genotype="AG")
            + _run("2", 1_000_000, 5, spacing=20_000, genotype="AG", rs_prefix="b"),
        )
        result = detect_roh(sample_engine)
        assert result.autosomal_snps_used == MIN_ROH_SNPS + 5
        assert result.evaluable

    def test_non_autosomal_and_no_call_rows_do_not_reach_the_floor(
        self, sample_engine: sa.Engine
    ) -> None:
        # Only callable autosomal calls count, so a sample padded with X calls
        # and no-calls stays indeterminate rather than being lifted over the
        # floor by markers the scan never reads.
        _seed(
            sample_engine,
            _run("1", 1_000_000, 50)
            + _run("X", 1_000_000, 200, rs_prefix="x")
            + _run("2", 5_000_000, 200, genotype="--", rs_prefix="n"),
        )
        result = detect_roh(sample_engine)
        assert result.autosomal_snps_used == 50
        assert result.froh is None
        assert result.indeterminate_reason == NO_SEGMENT_ELIGIBLE_REGION


class TestStorage:
    def test_coordinate_above_reader_ceiling_is_withheld_end_to_end(
        self, sample_engine: sa.Engine
    ) -> None:
        # Ingestion accepts non-negative integer positions without a per-contig
        # upper bound. Before the writer guard, this ordinary-length run was
        # stored as evaluable and then rejected by its own coordinate validator.
        _seed(sample_engine, _run("1", 2_800_000_000, 200))

        result = detect_roh(sample_engine)

        assert not result.evaluable
        assert result.indeterminate_reason == MEASUREMENT_OUT_OF_BOUNDS
        assert result.autosomal_snps_used == 200
        assert result.froh is None
        assert result.segments == []

        assert store_roh_findings(result, sample_engine) == 1
        with sample_engine.connect() as conn:
            row = conn.execute(sa.select(findings).where(findings.c.module == MODULE)).one()
        detail = json.loads(row.detail_json)
        assert detail["froh"] is None
        assert detail["total_roh_kb"] is None
        assert detail["longest_kb"] is None
        assert detail["n_segments"] is None
        assert detail["segments"] == []
        assert evaluability_from_detail(detail, sample_engine) == (
            False,
            200,
            MEASUREMENT_OUT_OF_BOUNDS,
        )
        assert "expected grch37 coordinates" in row.finding_text.lower()
        assert "re-running" not in row.finding_text.lower()

    def test_coordinate_span_above_denominator_is_withheld_end_to_end(
        self, sample_engine: sa.Engine
    ) -> None:
        # Markers every 500 kb plus each chromosome endpoint reproduce the
        # accepted dense-input witness from final review. Chr21 has only 98
        # markers and cannot emit, while the other 21 all-homozygous spans sum to
        # 2,832,903.4 kb: divided by the fixed 2.77 Gb called-sequence denominator
        # the old writer published 1.02271, which its own reader then rejected.
        rows: list[dict] = []
        candidate_lengths: list[float] = []
        for chrom, chromosome_length in enumerate(_GRCH37_AUTOSOME_LENGTHS_BP, start=1):
            positions = [*range(1, chromosome_length + 1, 500_000), chromosome_length]
            rows.extend(
                {
                    "rsid": f"dense_{chrom}_{index}",
                    "chrom": str(chrom),
                    "pos": position,
                    "genotype": "AA",
                }
                for index, position in enumerate(positions)
            )
            if len(positions) >= MIN_ROH_SNPS:
                candidate_lengths.append(round((positions[-1] - positions[0]) / 1000, 1))

        assert len(rows) == 5_798
        assert len(candidate_lengths) == 21
        candidate_total = round(sum(candidate_lengths), 1)
        assert candidate_total == 2_832_903.4
        assert round(candidate_total / 2_770_000, 5) == 1.02271

        _seed(sample_engine, rows)
        # A pre-guard writer could have persisted this exact over-bound shape.
        # Its generic parse failure is upgraded only because the current scan
        # independently reproduces the bounded-measurement failure.
        assert evaluability_from_detail(
            {
                "froh": 1.02271,
                "total_roh_kb": candidate_total,
                "longest_kb": max(candidate_lengths),
                "n_segments": 21,
                "autosomal_snps_used": 5_798,
            },
            sample_engine,
        ) == (False, 5_798, MEASUREMENT_OUT_OF_BOUNDS)

        result = detect_roh(sample_engine)

        assert not result.evaluable
        assert result.indeterminate_reason == MEASUREMENT_OUT_OF_BOUNDS
        assert result.autosomal_snps_used == 5_798
        assert result.froh is None
        assert result.segments == []
        assert result.total_roh_kb == 0.0
        assert result.longest_kb == 0.0

        assert store_roh_findings(result, sample_engine) == 1
        with sample_engine.connect() as conn:
            row = conn.execute(sa.select(findings).where(findings.c.module == MODULE)).one()
        detail = json.loads(row.detail_json)
        assert detail["evaluable"] is False
        assert detail["indeterminate_reason"] == MEASUREMENT_OUT_OF_BOUNDS
        assert detail["froh"] is None
        assert detail["total_roh_kb"] is None
        assert detail["longest_kb"] is None
        assert detail["n_segments"] is None
        assert detail["segments"] == []
        assert evaluability_from_detail(detail, sample_engine) == (
            False,
            5_798,
            MEASUREMENT_OUT_OF_BOUNDS,
        )
        assert "fixed-denominator method" in row.finding_text.lower()
        assert "re-running" not in row.finding_text.lower()

    def test_stores_single_summary_finding(self, sample_engine: sa.Engine) -> None:
        _seed(sample_engine, _run("1", 1_000_000, 200))
        result = detect_roh(sample_engine)
        assert store_roh_findings(result, sample_engine) == 1
        with sample_engine.connect() as conn:
            row = conn.execute(sa.select(findings).where(findings.c.module == MODULE)).fetchone()
        assert row.evidence_level == 1
        assert row.clinvar_significance is None
        assert row.category == "autozygosity"
        params = json.loads(row.detail_json)["params"]
        assert params["het_window_snps"] == 50
        assert params["het_window_tolerance"] == 1
        assert "het_tolerance" not in params
        corpus = row.finding_text.lower()
        assert "not a diagnosis" in corpus
        assert "parents are related" in corpus

    def test_unevaluable_sample_persists_a_withheld_finding(
        self, sample_engine: sa.Engine
    ) -> None:
        # The persisted finding_text is what generated reports render, so the
        # withheld state has to be correct in the row, not only in the API.
        _seed(sample_engine, _run("1", 1_000_000, 1))
        result = detect_roh(sample_engine)
        assert store_roh_findings(result, sample_engine) == 1
        with sample_engine.connect() as conn:
            row = conn.execute(sa.select(findings).where(findings.c.module == MODULE)).fetchone()

        text = row.finding_text.lower()
        assert "not assessed" in text
        # The three claims the defect made, none of which the data support.
        assert "typical result" not in text
        assert "no long runs of homozygosity were detected" not in text
        assert "froh ≈ 0" not in text
        # It must also not read as a clean bill of health by implication.
        assert "does not indicate that long runs of homozygosity are absent" in text

        detail = json.loads(row.detail_json)
        assert detail["evaluable"] is False
        assert detail["indeterminate_reason"] == NO_SEGMENT_ELIGIBLE_REGION
        assert detail["froh"] is None
        # No derived quantity may restate the withheld absence in another field:
        # a persisted 0.0 kb or 0 segments is the same measured-absence claim
        # FROH is being withheld to avoid.
        assert detail["total_roh_kb"] is None
        assert detail["longest_kb"] is None
        assert detail["n_segments"] is None
        assert detail["segments"] == []
        # The observed count is retained for audit even though FROH is withheld.
        assert detail["autosomal_snps_used"] == 1

    def test_store_is_idempotent(self, sample_engine: sa.Engine) -> None:
        _seed(sample_engine, _run("1", 1_000_000, 200))
        result = detect_roh(sample_engine)
        store_roh_findings(result, sample_engine)
        store_roh_findings(result, sample_engine)
        with sample_engine.connect() as conn:
            n = conn.execute(
                sa.select(sa.func.count()).select_from(findings).where(findings.c.module == MODULE)
            ).scalar()
        assert n == 1

    def test_empty_result_still_stores_informational_finding(
        self, sample_engine: sa.Engine
    ) -> None:
        # An *evaluable* sample with no qualifying run: 200 alternating calls
        # are enough markers to assess, and carry no long homozygous stretch.
        rows = _run("1", 1_000_000, 200)
        for index in range(1, 200, 2):
            rows[index]["genotype"] = "AG"
        _seed(sample_engine, rows)
        result = detect_roh(sample_engine)
        assert result.evaluable
        assert result.segments == []
        assert result.froh == 0.0
        assert store_roh_findings(result, sample_engine) == 1
        with sample_engine.connect() as conn:
            row = conn.execute(sa.select(findings).where(findings.c.module == MODULE)).fetchone()
        assert "froh" in row.finding_text.lower()
        # A genuine negative keeps its reassuring framing — the gate must not
        # convert every empty result into "not assessed".
        assert "typical result" in row.finding_text.lower()

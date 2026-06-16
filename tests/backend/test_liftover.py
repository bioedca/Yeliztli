"""Tests for GRCh38 liftover integration (P4-19, T4-19).

T4-19: pyliftover converts rs1801133 GRCh37 position to correct GRCh38 position.
"""

from __future__ import annotations

import pyliftover.liftover
import pytest
from fastapi.testclient import TestClient

from backend.ingestion import liftover as liftover_module
from backend.ingestion.liftover import (
    batch_convert,
    convert_coordinate,
    lift_build36_to_grch37,
    reset_liftover,
)

# ── Unit tests: convert_coordinate ────────────────────────────────────


class TestConvertCoordinate:
    """T4-19: Single coordinate conversion from GRCh37 to GRCh38."""

    def test_rs1801133_mthfr(self) -> None:
        """T4-19 core: rs1801133 (MTHFR C677T) on chr1 GRCh37 → GRCh38.

        GRCh37 chr1:11856378 → GRCh38 chr1:11796321
        (verified via UCSC liftOver and Ensembl)
        """
        result = convert_coordinate("1", 11856378)
        assert result is not None
        chrom, pos = result
        assert chrom == "1"
        # GRCh38 position for rs1801133
        assert pos == 11796321

    def test_rs429358_apoe(self) -> None:
        """APOE rs429358 on chr19 lifts correctly."""
        result = convert_coordinate("19", 44908684)
        assert result is not None
        chrom, pos = result
        assert chrom == "19"
        # GRCh38 position for rs429358
        assert pos == 44404524

    def test_rs7412_apoe(self) -> None:
        """APOE rs7412 on chr19 lifts correctly."""
        result = convert_coordinate("19", 44908822)
        assert result is not None
        chrom, pos = result
        assert chrom == "19"
        assert pos == 44404662

    def test_x_chromosome(self) -> None:
        """X chromosome coordinates lift correctly."""
        result = convert_coordinate("X", 1000000)
        assert result is not None
        chrom, pos = result
        assert chrom == "X"
        assert pos == 1039265  # GRCh38 (1-based)

    def test_mt_chromosome_returns_none(self) -> None:
        """F34: MT/chrM must NOT lift — UCSC hg19 chrM is Yoruba, not rCRS.

        The hg19→hg38 chain would emit wrong GRCh38 coordinates for
        mitochondrial positions (rCRS ≠ UCSC-hg19-chrM), so ``convert_coordinate``
        refuses to lift them rather than silently corrupting the position.
        """
        assert convert_coordinate("MT", 7028) is None
        assert convert_coordinate("MT", 263) is None
        # The ``chrM`` spelling is short-circuited identically to ``MT``.
        assert convert_coordinate("chrM", 750) is None

    def test_autosomal_still_lifts_after_mt_guard(self) -> None:
        """The MT short-circuit must not regress autosomal/sex liftover."""
        assert convert_coordinate("1", 11856378) == ("1", 11796321)
        assert convert_coordinate("X", 1000000) == ("X", 1039265)

    def test_chr_prefix_handled(self) -> None:
        """Input with 'chr' prefix works the same as without."""
        result_no_prefix = convert_coordinate("1", 11856378)
        result_with_prefix = convert_coordinate("chr1", 11856378)
        assert result_no_prefix is not None
        assert result_with_prefix is not None
        assert result_no_prefix == result_with_prefix

    def test_returns_none_for_invalid_chrom(self) -> None:
        """Invalid chromosome returns None."""
        result = convert_coordinate("99", 100)
        assert result is None


# ── Unit tests: batch_convert ─────────────────────────────────────────


class TestBatchConvert:
    """Batch coordinate conversion."""

    def test_batch_multiple_variants(self) -> None:
        """Batch convert returns results for all variants."""
        variants = [
            ("rs1801133", "1", 11856378),
            ("rs429358", "19", 44908684),
            ("rs7412", "19", 44908822),
        ]
        results = batch_convert(variants)
        assert len(results) == 3
        assert results["rs1801133"] is not None
        assert results["rs429358"] is not None
        assert results["rs7412"] is not None

    def test_batch_empty_list(self) -> None:
        """Empty variant list returns empty dict."""
        results = batch_convert([])
        assert results == {}

    def test_batch_preserves_rsid_keys(self) -> None:
        """Result dict is keyed by rsid."""
        variants = [("rs123", "1", 100000)]
        results = batch_convert(variants)
        assert "rs123" in results


# ── Unit tests: lift_build36_to_grch37 (#562) ─────────────────────────


class TestLiftBuild36ToGrch37:
    """hg18 (NCBI build 36, 23andMe v3) → GRCh37 lift, with strand-aware alleles."""

    def test_rs7412_known_coordinate_plus_strand(self) -> None:
        """rs7412 build36 19:50103919 → GRCh37 19:45412079 (+ strand, no complement).

        Verified via UCSC liftOver; pins the vendored hg18→hg19 chain.
        """
        result = lift_build36_to_grch37("19", 50103919, "CC")
        assert result == ("19", 45412079, "CC")

    def test_plus_strand_genotype_unchanged(self) -> None:
        """On a +-strand lift the alleles are passed through verbatim."""
        result = lift_build36_to_grch37("19", 50103919, "CT")
        assert result is not None
        assert result[2] == "CT"

    def test_minus_strand_complements_snv_alleles(self) -> None:
        """On a strand-flipped (−) segment, A/C/G/T alleles are complemented so the
        stored call is plus-strand-relative on GRCh37 (hg18 chr1:2480001 lifts −).
        The result keeps the parser's canonical uppercased+SORTED-pair form: the
        complement of sorted "AG" is "TC" in place, re-sorted to canonical "CT"."""
        result = lift_build36_to_grch37("1", 2480001, "AG")
        assert result is not None
        out_chrom, out_pos, out_genotype = result
        assert out_chrom == "1"
        assert out_pos == 2494417
        assert out_genotype == "CT"  # complement(A,G)=(T,C) → sorted "CT"

    def test_minus_strand_output_is_canonical_sorted_pair(self) -> None:
        """Every minus-strand SNV lift returns a canonical sorted pair (matching a
        fresh parse), so sorted-pair lookups can't silently miss these calls."""
        for gt in ("AG", "AC", "CT", "GT", "AT", "CG"):
            out = lift_build36_to_grch37("1", 2480001, gt)
            assert out is not None
            assert out[2] == "".join(sorted(out[2])), f"{gt} → {out[2]} not sorted"

    def test_minus_strand_does_not_complement_indels(self) -> None:
        """Indel tokens I/D must NOT be complemented on a strand flip."""
        assert lift_build36_to_grch37("1", 2480001, "II")[2] == "II"
        assert lift_build36_to_grch37("1", 2480001, "DD")[2] == "DD"
        assert lift_build36_to_grch37("1", 2480001, "DI")[2] == "DI"

    def test_minus_strand_does_not_complement_nocall(self) -> None:
        """No-call sentinels pass through unchanged on a strand flip."""
        assert lift_build36_to_grch37("1", 2480001, "--")[2] == "--"

    def test_palindromic_genotype_invariant_under_complement(self) -> None:
        """A palindromic genotype (A/T or C/G) is invariant under complement, so a
        strand flip leaves its allele set unchanged."""
        # complement(A)=T, complement(T)=A → "AT" → "TA" (same allele set)
        assert sorted(lift_build36_to_grch37("1", 2480001, "AT")[2]) == ["A", "T"]
        assert sorted(lift_build36_to_grch37("1", 2480001, "CG")[2]) == ["C", "G"]

    def test_mt_declined(self) -> None:
        """Mitochondrial inputs return None (hg18 chrM is not rCRS)."""
        assert lift_build36_to_grch37("MT", 100, "A") is None
        assert lift_build36_to_grch37("chrM", 7028, "G") is None

    def test_unliftable_returns_none(self) -> None:
        """A position deleted/rearranged out of hg19 (or unknown chrom) → None."""
        assert lift_build36_to_grch37("1", 247_000_000, "AG") is None
        assert lift_build36_to_grch37("99", 100, "AG") is None

    def test_chr_prefix_accepted(self) -> None:
        """The ``chr`` prefix is optional, mirroring convert_coordinate."""
        assert lift_build36_to_grch37("chr19", 50103919, "CC") == ("19", 45412079, "CC")


# ── Reset helper ──────────────────────────────────────────────────────


class TestResetLiftover:
    """Test the reset helper for test isolation."""

    def test_reset_and_reinit(self) -> None:
        """After reset, next call re-initialises the LiftOver instance."""
        # Ensure it's loaded
        result1 = convert_coordinate("1", 11856378)
        assert result1 is not None

        # Reset and convert again
        reset_liftover()
        result2 = convert_coordinate("1", 11856378)
        assert result2 is not None
        assert result1 == result2


# ── Offline / no-network regression (CI flake fix) ────────────────────


class TestVendoredChainOffline:
    """The hg19→hg38 chain is vendored in-repo and loaded directly.

    pyliftover's ``LiftOver("hg19", "hg38")`` would download the chain from
    UCSC on first use, which made CI flaky when that fetch failed. These tests
    guard that liftover uses the bundled file and never the network.
    """

    def test_vendored_chain_file_exists(self) -> None:
        """The chain ships in the package so liftover works offline."""
        assert liftover_module._CHAIN_PATH.exists(), (
            f"vendored chain missing at {liftover_module._CHAIN_PATH}"
        )

    def test_no_network_download(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """convert_coordinate succeeds without pyliftover's UCSC download path.

        ``open_liftover_chain_file`` is only reached by the from_db/to_db web
        branch of ``LiftOver.__init__``; loading an explicit chain path skips
        it. Making it raise proves the vendored file is used.
        """

        def _fail(*args: object, **kwargs: object) -> object:
            raise AssertionError(
                "liftover attempted a UCSC chain download instead of using the vendored file"
            )

        monkeypatch.setattr(pyliftover.liftover, "open_liftover_chain_file", _fail)
        reset_liftover()
        try:
            result = convert_coordinate("1", 11856378)
            assert result == ("1", 11796321)
        finally:
            # Drop the instance loaded under the patch so later tests reinit cleanly.
            reset_liftover()


# ── Route-level: GET /api/liftover/convert (issue #530) ────────────────


class TestConvertRoute:
    """`GET /api/liftover/convert` — the single-coordinate HTTP endpoint had no
    route-level test (only `convert_coordinate` was covered directly). It is
    build-conversion-correctness-sensitive (cf. #480), so lock the route here.
    """

    def test_convert_success(self, test_client: TestClient) -> None:
        """A liftable GRCh37 coordinate returns the GRCh38 mapping + success."""
        resp = test_client.get("/api/liftover/convert", params={"chrom": "1", "pos": 11856378})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["success"] is True
        assert body["chrom_grch37"] == "1"
        assert body["pos_grch37"] == 11856378
        assert body["chrom_grch38"] == "1"
        assert body["pos_grch38"] == 11796321  # matches TestConvertCoordinate

    def test_convert_mt_returns_success_false(self, test_client: TestClient) -> None:
        """MT is deliberately not lifted (F34: hg19 chrM is Yoruba, not rCRS) —
        the route reports `success=False` rather than a wrong coordinate.

        Uses ``M``/750, a position that *would* lift to a (wrong) GRCh38
        coordinate if the ``convert_coordinate`` short-circuit were removed, so
        this genuinely locks F34 at the route boundary (the ``MT``/263 spelling
        would return None via the chain regardless — chr name absent — and so
        could not catch a regressed short-circuit)."""
        resp = test_client.get("/api/liftover/convert", params={"chrom": "M", "pos": 750})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["success"] is False
        assert body.get("pos_grch38") is None

    def test_convert_nonpositive_pos_returns_422(self, test_client: TestClient) -> None:
        """`pos` is constrained to > 0 (1-based), so 0 is rejected by validation."""
        resp = test_client.get("/api/liftover/convert", params={"chrom": "1", "pos": 0})
        assert resp.status_code == 422

    def test_convert_missing_params_returns_422(self, test_client: TestClient) -> None:
        """Both `chrom` and `pos` are required query params."""
        assert test_client.get("/api/liftover/convert").status_code == 422

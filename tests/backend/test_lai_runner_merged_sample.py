"""LAI runner: merged-sample three-key telemetry (Step 22a; Plan §6.6).

Locks the three-key uppercase dispatch path for merged samples:

- Every genotype carrying a non-empty ``source`` in ``{"S1", "S2", "both"}``
  collapses to a three-key payload regardless of ``file_format``.
- Per-source hit/drop counts sum to the matched/dropped totals from
  ``_write_per_chrom_vcfs`` — i.e. no leakage into the unmerged empty-string
  bucket.
- A ``merged_v1`` file_format with all-empty source still collapses to the
  three-key path (the Plan §6.6 escape hatch for inflight-migrated DBs).

Step 22a uses an inline payload — the dual-upload fixture and full merge
service land in Phase 3 (Plan §10).
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from backend.analysis.lai_runner import LAIRunner

# ── Fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture()
def runner() -> LAIRunner:
    """LAIRunner stub with a deterministic rsid_lookup; bundle init bypassed."""
    instance = LAIRunner.__new__(LAIRunner)
    # 6 autosomal hits, 1 chrX-mapped (drop bucket), rs_unknown deliberately
    # absent (drop bucket). Spread the rsIDs across the three merged-source
    # buckets so we can independently audit each one.
    instance.rsid_lookup = {
        "rs_s1_hit_1": ("chr1", 1001),
        "rs_s1_hit_2": ("chr1", 1002),
        "rs_s2_hit_1": ("chr2", 2001),
        "rs_s2_hit_2": ("chr2", 2002),
        "rs_both_hit_1": ("chr3", 3001),
        "rs_both_hit_2": ("chr22", 22001),
        # In-bundle but non-autosomal → drop bucket
        "rs_s2_chrx": ("chrX", 99000),
    }
    return instance


def _emit_all_sites(_runner, _chrom, sites, _vcf_path):
    """Model a VCF writer that successfully emits every candidate site."""
    counts: dict[str, dict[str, int]] = {}
    for site in sites:
        src = site.get("source", "") or ""
        counts.setdefault(src, {"hits": 0, "drops": 0})["hits"] += 1
    return counts


# ── Merged-sample payload ────────────────────────────────────────────────


_MERGED_GENOTYPES = [
    # S1-exclusive: both autosomal hits
    {"rsid": "rs_s1_hit_1", "chrom": "1", "pos": 1001, "genotype": "AG", "source": "S1"},
    {"rsid": "rs_s1_hit_2", "chrom": "1", "pos": 1002, "genotype": "AA", "source": "S1"},
    # S1: an autosomal rsid not in the lookup → S1 drops
    {"rsid": "rs_s1_off_bundle", "chrom": "5", "pos": 50000, "genotype": "GT", "source": "S1"},
    # S2: two autosomal hits + one chrX-mapped drop + one off-bundle drop
    {"rsid": "rs_s2_hit_1", "chrom": "2", "pos": 2001, "genotype": "GG", "source": "S2"},
    {"rsid": "rs_s2_hit_2", "chrom": "2", "pos": 2002, "genotype": "CT", "source": "S2"},
    {"rsid": "rs_s2_chrx", "chrom": "X", "pos": 99000, "genotype": "AG", "source": "S2"},
    {"rsid": "rs_s2_off_bundle", "chrom": "6", "pos": 60000, "genotype": "TT", "source": "S2"},
    # both: two autosomal hits (concordant calls present on each source)
    {"rsid": "rs_both_hit_1", "chrom": "3", "pos": 3001, "genotype": "AG", "source": "both"},
    {"rsid": "rs_both_hit_2", "chrom": "22", "pos": 22001, "genotype": "CT", "source": "both"},
]


# ── Tests ─────────────────────────────────────────────────────────────────


class TestMergedThreeKeyTelemetry:
    """`_build_coverage_telemetry` returns uppercase S1/S2/both keys."""

    def test_merged_v1_with_all_three_source_keys(self, runner, tmp_path):
        filtered = runner._filter_genotypes(_MERGED_GENOTYPES)
        with patch.object(LAIRunner, "_write_single_vcf", _emit_all_sites):
            _, total, per_source = runner._write_per_chrom_vcfs(filtered, tmp_path)

        # Six autosomal hits across the three source buckets; rs_s2_chrx is
        # filtered upstream (chrX) by _filter_genotypes and never reaches the
        # accumulator.
        assert total == 6

        telemetry = LAIRunner._build_coverage_telemetry(per_source, "merged_v1")
        assert set(telemetry.keys()) == {"S1", "S2", "both"}

        # Per-bucket audit
        assert telemetry["S1"] == {"hits": 2, "drops": 1}  # rs_s1_off_bundle
        assert telemetry["S2"] == {"hits": 2, "drops": 1}  # rs_s2_off_bundle
        assert telemetry["both"] == {"hits": 2, "drops": 0}

    def test_per_source_counts_sum_to_matched_and_dropped_totals(self, runner, tmp_path):
        filtered = runner._filter_genotypes(_MERGED_GENOTYPES)
        with patch.object(LAIRunner, "_write_single_vcf", _emit_all_sites):
            _, total, per_source = runner._write_per_chrom_vcfs(filtered, tmp_path)

        telemetry = LAIRunner._build_coverage_telemetry(per_source, "merged_v1")
        total_hits = sum(bucket["hits"] for bucket in telemetry.values())
        total_drops = sum(bucket["drops"] for bucket in telemetry.values())

        assert total_hits == total
        # Drops = entries that reached _write_per_chrom_vcfs but missed the
        # autosomal-lookup hit path (rs_s1_off_bundle + rs_s2_off_bundle).
        # chrX/MT/no-call/indels are filtered upstream and don't reach here.
        assert total_drops == 2

    def test_no_empty_source_bucket_leaks_into_three_key_payload(self, runner, tmp_path):
        filtered = runner._filter_genotypes(_MERGED_GENOTYPES)
        with patch.object(LAIRunner, "_write_single_vcf", _emit_all_sites):
            _, _, per_source = runner._write_per_chrom_vcfs(filtered, tmp_path)

        telemetry = LAIRunner._build_coverage_telemetry(per_source, "merged_v1")
        # Three keys exactly — no "" leakage, no lowercase variants.
        assert "" not in telemetry
        assert "s1" not in telemetry
        assert "s2" not in telemetry
        assert "both_lowercase" not in telemetry


class TestMergedDispatchOverridesFileFormat:
    """Any non-empty source key forces three-key dispatch."""

    @pytest.mark.parametrize(
        "file_format",
        ["23andme_v5", "ancestrydna_v2.0", "merged_v1", ""],
    )
    def test_nonempty_source_collapses_to_three_keys(self, file_format):
        # Build per_source manually — this is the post-write accumulator state.
        per_source = {
            "S1": {"hits": 10, "drops": 1},
            "S2": {"hits": 12, "drops": 2},
            "both": {"hits": 5, "drops": 0},
        }
        telemetry = LAIRunner._build_coverage_telemetry(per_source, file_format)
        assert set(telemetry.keys()) == {"S1", "S2", "both"}
        assert telemetry["S1"] == {"hits": 10, "drops": 1}
        assert telemetry["S2"] == {"hits": 12, "drops": 2}
        assert telemetry["both"] == {"hits": 5, "drops": 0}

    def test_merged_v1_file_format_with_only_empty_source_still_three_key(self):
        # Edge case from Plan §6.6: ``merged_v1`` file_format collapses to the
        # three-key path even when (rarely) every genotype has source="" — a
        # mid-migration sample DB that's already stamped merged but hasn't
        # backfilled the source column. Missing buckets emit zero counts.
        per_source = {"": {"hits": 7, "drops": 1}}
        telemetry = LAIRunner._build_coverage_telemetry(per_source, "merged_v1")
        assert set(telemetry.keys()) == {"S1", "S2", "both"}
        assert telemetry["S1"] == {"hits": 0, "drops": 0}
        assert telemetry["S2"] == {"hits": 0, "drops": 0}
        assert telemetry["both"] == {"hits": 0, "drops": 0}

    def test_missing_source_bucket_emits_zero_counts(self):
        # Sources S1 + both populated, S2 absent → S2 falls back to zeros.
        per_source = {
            "S1": {"hits": 3, "drops": 0},
            "both": {"hits": 1, "drops": 0},
        }
        telemetry = LAIRunner._build_coverage_telemetry(per_source, "merged_v1")
        assert telemetry["S1"] == {"hits": 3, "drops": 0}
        assert telemetry["S2"] == {"hits": 0, "drops": 0}
        assert telemetry["both"] == {"hits": 1, "drops": 0}

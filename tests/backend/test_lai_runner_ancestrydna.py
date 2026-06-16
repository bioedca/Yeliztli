"""LAI runner: AncestryDNA single-key telemetry (Step 22; Plan §6.6).

Builds a small in-memory sample DB stamped with ``file_format='ancestrydna_v2.0'``
and exercises the LAI runner's filter + per-source accumulator. Asserts:

- Non-zero variant count after filtering.
- All retained variants are autosomal.
- Telemetry collapses to single-key ``{"ancestrydna": {hits, drops}}`` (Plan §6.6).

The full Phase-0 fixture ``sample_ancestrydna_v2.txt`` is curated in step 34;
this test uses an inline payload derived from the existing v1 fixture so step 22
can ship independently.

Soft-gate (degraded_coverage) cases — Step 23 (Plan §6.7) — extend the file
below: positive case asserts AncestryDNA + pre-v2.0.0 lai_bundle yields
``degraded_coverage=True``; negative case asserts ``23andme_v5`` never carries
the flag, regardless of bundle version.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
import sqlalchemy as sa

from backend.analysis.lai import _read_sample_file_format, _read_sample_genotypes
from backend.analysis.lai_runner import LAIRunner
from backend.db.sample_schema import create_sample_tables
from backend.db.tables import raw_variants, sample_metadata_table
from backend.services.lai_coverage_gate import (
    file_format_has_ancestrydna,
    is_lai_coverage_degraded,
    lai_bundle_below_v2,
)

# A minimal AncestryDNA-shaped payload (chrom/pos/genotype only; rsids picked
# to overlap the runner stub's liftover map below). Includes mixed autosomal,
# chrY/MT, an in-bundle but chrX-mapped rsid (drop bucket), no-call, and indel
# rows so the filter and per-source counters exercise the full set of branches.
_ANCESTRYDNA_ROWS = [
    {"rsid": "rs_auto_1", "chrom": "1", "pos": 82154, "genotype": "AA"},
    {"rsid": "rs_auto_2", "chrom": "1", "pos": 752566, "genotype": "AG"},
    {"rsid": "rs_auto_3", "chrom": "2", "pos": 100200, "genotype": "GG"},
    {"rsid": "rs_auto_4", "chrom": "22", "pos": 42523610, "genotype": "AG"},
    {"rsid": "rs_off_bundle", "chrom": "5", "pos": 11856378, "genotype": "CT"},
    {"rsid": "rs_chry", "chrom": "Y", "pos": 6873643, "genotype": "C"},
    {"rsid": "rs_nocall", "chrom": "1", "pos": 800007, "genotype": "00"},
    {"rsid": "rs_indel", "chrom": "1", "pos": 11854476, "genotype": "DI"},
    {"rsid": "rs_chrx_mapped", "chrom": "1", "pos": 999000, "genotype": "AG"},
]


# ── Fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture()
def ancestrydna_sample_engine() -> sa.Engine:
    """In-memory sample DB stamped with ``file_format='ancestrydna_v2.0'``."""
    engine = sa.create_engine("sqlite://")
    create_sample_tables(engine)
    with engine.begin() as conn:
        conn.execute(
            sample_metadata_table.insert().values(
                id=1,
                name="ancestrydna_test",
                file_format="ancestrydna_v2.0",
                file_hash="testhash",
            )
        )
        conn.execute(
            raw_variants.insert(),
            _ANCESTRYDNA_ROWS,
        )
    return engine


@pytest.fixture()
def runner() -> LAIRunner:
    """LAIRunner stub with a deterministic rsid_lookup; bundle init bypassed."""
    instance = LAIRunner.__new__(LAIRunner)
    instance.rsid_lookup = {
        "rs_auto_1": ("chr1", 82154),
        "rs_auto_2": ("chr1", 752566),
        "rs_auto_3": ("chr2", 100200),
        "rs_auto_4": ("chr22", 42523610),
        # In-bundle but maps to chrX → drop bucket (Plan §6.6 LAI is autosomal)
        "rs_chrx_mapped": ("chrX", 999000),
        # rs_off_bundle deliberately absent → drop bucket
    }
    return instance


def _emit_all_sites(_runner, _chrom, sites, _vcf_path):
    """Model a VCF writer that successfully emits every candidate site."""
    counts: dict[str, dict[str, int]] = {}
    for site in sites:
        src = site.get("source", "") or ""
        counts.setdefault(src, {"hits": 0, "drops": 0})["hits"] += 1
    return counts


# ── Tests ─────────────────────────────────────────────────────────────────


class TestAncestryDNAReadPath:
    """`_read_sample_file_format` + `_read_sample_genotypes` thread vendor + source."""

    def test_reads_ancestrydna_file_format(self, ancestrydna_sample_engine):
        assert _read_sample_file_format(ancestrydna_sample_engine) == "ancestrydna_v2.0"

    def test_genotypes_default_source_to_empty_on_pre_phase3_db(self, ancestrydna_sample_engine):
        genotypes = _read_sample_genotypes(ancestrydna_sample_engine)
        assert len(genotypes) == len(_ANCESTRYDNA_ROWS)
        assert all(gt["source"] == "" for gt in genotypes)
        assert {gt["rsid"] for gt in genotypes} == {r["rsid"] for r in _ANCESTRYDNA_ROWS}


class TestAncestryDNARunnerTelemetry:
    """End-to-end: AncestryDNA sample DB → single-key telemetry."""

    def test_non_zero_autosomal_variant_count(self, runner, ancestrydna_sample_engine, tmp_path):
        genotypes = _read_sample_genotypes(ancestrydna_sample_engine)
        filtered = runner._filter_genotypes(genotypes)
        with patch.object(LAIRunner, "_write_single_vcf", _emit_all_sites):
            vcf_paths, total, _ = runner._write_per_chrom_vcfs(filtered, tmp_path)

        assert total > 0  # non-zero variant count
        # All written contigs are autosomal (chr1..chr22)
        autosomal_chroms = {f"chr{i}" for i in range(1, 23)}
        assert set(vcf_paths.keys()) <= autosomal_chroms
        # The runner already drops Y/MT/X + no-calls + indels in _filter_genotypes
        # and rs_chrx_mapped via the autosomal post-lookup check.
        assert all(
            s["chrom"] in autosomal_chroms for s in [{"chrom": chrom} for chrom in vcf_paths]
        )

    def test_single_key_ancestrydna_telemetry(self, runner, ancestrydna_sample_engine, tmp_path):
        file_format = _read_sample_file_format(ancestrydna_sample_engine)
        genotypes = _read_sample_genotypes(ancestrydna_sample_engine)
        filtered = runner._filter_genotypes(genotypes)
        with patch.object(LAIRunner, "_write_single_vcf", _emit_all_sites):
            _, _, per_source = runner._write_per_chrom_vcfs(filtered, tmp_path)

        telemetry = LAIRunner._build_coverage_telemetry(per_source, file_format)
        assert set(telemetry.keys()) == {"ancestrydna"}
        assert telemetry["ancestrydna"]["hits"] == 4  # rs_auto_1..4
        # Drops: rs_off_bundle (autosomal but missing) + rs_chrx_mapped
        # (in-bundle but non-autosomal). rs_chry / rs_nocall / rs_indel are
        # filtered upstream by _filter_genotypes and never reach the
        # lookup-based accumulator.
        assert telemetry["ancestrydna"]["drops"] == 2

    def test_lookup_drops_attribute_to_ancestrydna_source(
        self, runner, ancestrydna_sample_engine, tmp_path
    ):
        """Single empty-source bucket — no S1/S2/both leakage on unmerged DB."""
        genotypes = _read_sample_genotypes(ancestrydna_sample_engine)
        filtered = runner._filter_genotypes(genotypes)
        with patch.object(LAIRunner, "_write_single_vcf", _emit_all_sites):
            _, _, per_source = runner._write_per_chrom_vcfs(filtered, tmp_path)

        # Only the empty-source bucket should exist on a pre-Phase-3 DB
        assert set(per_source.keys()) == {""}

    def test_ancestrydna_v2_0_format_dispatch(self):
        """`_build_coverage_telemetry` correctly derives 'ancestrydna' vendor."""
        per_source = {"": {"hits": 7, "drops": 2}}
        telemetry = LAIRunner._build_coverage_telemetry(per_source, "ancestrydna_v2.0")
        assert telemetry == {"ancestrydna": {"hits": 7, "drops": 2}}


# ── Step 23 — Soft LAI staleness gate (Plan §6.7) ─────────────────────


class TestFileFormatPredicate:
    """`file_format_has_ancestrydna` recognizes the vendor prefix."""

    @pytest.mark.parametrize(
        "file_format",
        ["ancestrydna_v2.0", "AncestryDNA_v2.0", "ANCESTRYDNA_V2.0", "ancestrydna"],
    )
    def test_positive_prefix(self, file_format):
        assert file_format_has_ancestrydna(file_format) is True

    @pytest.mark.parametrize(
        "file_format",
        ["23andme_v5", "23andme", "merged_v1", "", None, "unknown"],
    )
    def test_negative_prefix(self, file_format):
        """23andMe — and every non-AncestryDNA value — never carries the flag."""
        assert file_format_has_ancestrydna(file_format) is False


class TestBundleVersionPredicate:
    """`lai_bundle_below_v2` tolerates the 'v' prefix and bad values."""

    @pytest.mark.parametrize(
        "raw",
        ["v1.0.0", "1.0.0", "v1.1.0", "v1.9.9", "0.9.0"],
    )
    def test_below_v2(self, raw):
        assert lai_bundle_below_v2(raw) is True

    @pytest.mark.parametrize(
        "raw",
        ["v2.0.0", "2.0.0", "v2.1.0", "3.0.0"],
    )
    def test_at_or_above_v2(self, raw):
        assert lai_bundle_below_v2(raw) is False

    @pytest.mark.parametrize("raw", ["", None, "unknown-pre-manifest", "garbage"])
    def test_unparseable_short_circuits_false(self, raw):
        """Unparseable values are advisory-only — never block."""
        assert lai_bundle_below_v2(raw) is False


class TestSoftGate:
    """`is_lai_coverage_degraded` is the conjunction of the two predicates."""

    def test_ancestrydna_plus_v1_bundle_is_degraded(self):
        """Positive case: AncestryDNA + v1 bundle → degraded_coverage."""
        assert is_lai_coverage_degraded("ancestrydna_v2.0", "v1.0.0") is True

    def test_ancestrydna_plus_v2_bundle_is_clear(self):
        """AncestryDNA on the matching bundle does not trigger the gate."""
        assert is_lai_coverage_degraded("ancestrydna_v2.0", "v2.0.0") is False

    @pytest.mark.parametrize(
        "bundle_version",
        ["v1.0.0", "v1.1.0", "v2.0.0", "v3.0.0", None, ""],
    )
    def test_23andme_never_carries_flag(self, bundle_version):
        """Plan §6.7 negative case: 23andMe-only samples never degrade."""
        assert is_lai_coverage_degraded("23andme_v5", bundle_version) is False

    def test_missing_file_format_is_clear(self):
        """Defensive: an empty file_format never degrades."""
        assert is_lai_coverage_degraded(None, "v1.0.0") is False
        assert is_lai_coverage_degraded("", "v1.0.0") is False

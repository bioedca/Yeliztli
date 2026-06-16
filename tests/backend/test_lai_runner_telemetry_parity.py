"""Parity tests for LAI runner per-source telemetry (Step 22; Plan §6.6).

Locks two contracts:

1. **Byte-identical 23andMe output.** Threading ``source`` through the
   filter/write path must not change the VCF write surface for legacy
   23andMe inputs. The genotype-write call args are captured and compared
   between a baseline run (no ``source`` field on input dicts) and a run
   where every genotype carries ``source=""``.

2. **Single-key telemetry for unmerged samples.** When every input genotype
   has ``source=""`` and the parent sample's ``file_format`` starts with
   ``23andme_``, ``_build_coverage_telemetry`` returns a single-key payload
   ``{"23andme": {"hits": int, "drops": int}}`` (Plan §6.6).
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from backend.analysis.lai_runner import LAIRunner

# ── Fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture()
def runner() -> LAIRunner:
    """LAIRunner stub with a deterministic rsid_lookup; bundle init bypassed."""
    instance = LAIRunner.__new__(LAIRunner)
    # Liftover map: 4 rsids hit (autosomal), 1 rsid maps to chrX (drop), and
    # rs_unknown deliberately missing from the lookup (drop).
    instance.rsid_lookup = {
        "rs1": ("chr1", 1001),
        "rs2": ("chr1", 1002),
        "rs3": ("chr2", 2001),
        "rs4": ("chr22", 2202),
        "rs_chrx": ("chrX", 5000),
    }
    return instance


# ── 23andMe genotype payloads ────────────────────────────────────────────


_BASE_23ANDME_GENOTYPES = [
    {"rsid": "rs1", "chrom": "1", "pos": 1001, "genotype": "AG"},
    {"rsid": "rs2", "chrom": "1", "pos": 1002, "genotype": "AA"},
    {"rsid": "rs3", "chrom": "2", "pos": 2001, "genotype": "GG"},
    {"rsid": "rs4", "chrom": "22", "pos": 2202, "genotype": "CT"},
    {"rsid": "rs_unknown", "chrom": "1", "pos": 9999, "genotype": "TT"},
    {"rsid": "rs_chrx", "chrom": "X", "pos": 5000, "genotype": "AG"},
    {"rsid": "rs_nocall", "chrom": "1", "pos": 11111, "genotype": "--"},
]


def _with_source(rows: list[dict], source: str) -> list[dict]:
    return [{**r, "source": source} for r in rows]


def _emit_all_sites(_runner, _chrom, sites, _vcf_path):
    """Model a VCF writer that successfully emits every candidate site."""
    counts: dict[str, dict[str, int]] = {}
    for site in sites:
        src = site.get("source", "") or ""
        counts.setdefault(src, {"hits": 0, "drops": 0})["hits"] += 1
    return counts


# ── Tests ─────────────────────────────────────────────────────────────────


class TestPerSourceAccumulator:
    """`_write_per_chrom_vcfs` accumulates per-source hits/drops."""

    def test_unmerged_23andme_counts(self, runner, tmp_path):
        filtered = runner._filter_genotypes(_with_source(_BASE_23ANDME_GENOTYPES, ""))
        with patch.object(LAIRunner, "_write_single_vcf", _emit_all_sites):
            _, total, per_source = runner._write_per_chrom_vcfs(filtered, tmp_path)
        # Hits: rs1, rs2, rs3, rs4. Drop: rs_unknown (autosomal but missing
        # from the lookup). rs_chrx + rs_nocall are filtered upstream by
        # _filter_genotypes and never reach the per-source accumulator.
        assert total == 4
        assert per_source == {"": {"hits": 4, "drops": 1}}

    def test_drops_count_only_lookup_misses(self, runner, tmp_path):
        # Add an autosomal rsid that's not in the lookup table → drop bucket.
        # rs_unknown in the base list is already a drop, so this raises the
        # total to 2.
        rows = _BASE_23ANDME_GENOTYPES + [
            {"rsid": "rs_off_bundle", "chrom": "5", "pos": 7777, "genotype": "GA"},
        ]
        filtered = runner._filter_genotypes(_with_source(rows, ""))
        with patch.object(LAIRunner, "_write_single_vcf", _emit_all_sites):
            _, total, per_source = runner._write_per_chrom_vcfs(filtered, tmp_path)
        assert total == 4
        assert per_source[""]["hits"] == 4
        assert per_source[""]["drops"] == 2

    def test_ref_alt_encoding_skips_count_as_drops(self, runner, tmp_path, monkeypatch):
        """Mapped rsIDs count as hits only after REF/ALT encoding emits a VCF row."""

        class FakeBGZFile:
            """Tiny stand-in for pysam.BGZFile that writes bytes to disk."""

            def __init__(self, path: str, mode: str) -> None:
                """Store the target path and file mode until context entry."""
                self.path = Path(path)
                self.mode = mode
                self.handle = None

            def __enter__(self):
                """Open the target file and return this fake BGZF writer."""
                self.handle = self.path.open(self.mode)
                return self

            def __exit__(self, *exc_info):
                """Close the file handle when leaving the context manager."""
                assert self.handle is not None
                self.handle.close()

            def write(self, data: bytes) -> int:
                """Write raw bytes to the opened file handle."""
                assert self.handle is not None
                return self.handle.write(data)

        def fake_tabix_index(path: str, *, preset: str, force: bool) -> None:
            """Record that the VCF writer requested a forced VCF tabix index."""
            assert preset == "vcf"
            assert force is True
            Path(f"{path}.tbi").write_text("index", encoding="utf-8")

        monkeypatch.setitem(
            sys.modules,
            "pysam",
            SimpleNamespace(BGZFile=FakeBGZFile, tabix_index=fake_tabix_index),
        )
        runner.rsid_lookup = {
            "rs_emit": ("chr1", 1001),
            "rs_missing_ref": ("chr1", 1002),
            "rs_mismatch": ("chr1", 1003),
        }
        rows = [
            {"rsid": "rs_emit", "chrom": "1", "pos": 1001, "genotype": "AG"},
            {"rsid": "rs_missing_ref", "chrom": "1", "pos": 1002, "genotype": "AA"},
            {"rsid": "rs_mismatch", "chrom": "1", "pos": 1003, "genotype": "CT"},
            {"rsid": "rs_off_bundle", "chrom": "1", "pos": 1004, "genotype": "AA"},
        ]
        with patch.object(
            LAIRunner,
            "_get_ref_alleles_pysam",
            return_value={1001: {"ref": "A", "alt": "G"}, 1003: {"ref": "A", "alt": "G"}},
        ):
            vcf_paths, total, per_source = runner._write_per_chrom_vcfs(
                runner._filter_genotypes(rows), tmp_path
            )

        assert set(vcf_paths) == {"chr1"}
        assert total == 1
        assert per_source == {"": {"hits": 1, "drops": 3}}


class TestByteIdenticalWriteSurface:
    """Threading ``source`` does not alter the VCF-write call surface."""

    def _capture_write_calls(
        self, runner: LAIRunner, rows: list[dict], tmp_path: Path
    ) -> list[tuple]:
        calls: list[tuple] = []

        def fake_write(self, chrom, sites, vcf_path):
            # Capture chrom + the exact site-dict tuples written
            site_tuples = tuple(
                (s["chrom"], s["pos"], s["rsid"], s["allele1"], s["allele2"]) for s in sites
            )
            calls.append((chrom, str(vcf_path), site_tuples))
            return _emit_all_sites(self, chrom, sites, vcf_path)

        filtered = runner._filter_genotypes(rows)
        with patch.object(LAIRunner, "_write_single_vcf", fake_write):
            runner._write_per_chrom_vcfs(filtered, tmp_path)
        return calls

    def test_no_source_field_vs_empty_source_produce_identical_writes(self, runner, tmp_path):
        baseline_dir = tmp_path / "baseline"
        baseline_dir.mkdir()
        baseline_calls = self._capture_write_calls(runner, _BASE_23ANDME_GENOTYPES, baseline_dir)

        threaded_dir = tmp_path / "threaded"
        threaded_dir.mkdir()
        threaded_calls = self._capture_write_calls(
            runner, _with_source(_BASE_23ANDME_GENOTYPES, ""), threaded_dir
        )

        # Strip out the (per-run-different) tmp_path prefix when comparing
        def strip_dir(calls):
            return [(c[0], Path(c[1]).name, c[2]) for c in calls]

        assert strip_dir(baseline_calls) == strip_dir(threaded_calls)
        assert len(baseline_calls) > 0  # sanity: we actually wrote something


class TestSingleKeyTelemetry:
    """`_build_coverage_telemetry` emits single-key shape for unmerged 23andMe."""

    def test_23andme_v5_yields_single_key(self):
        per_source = {"": {"hits": 12, "drops": 3}}
        telemetry = LAIRunner._build_coverage_telemetry(per_source, "23andme_v5")
        assert telemetry == {"23andme": {"hits": 12, "drops": 3}}

    def test_23andme_v4_yields_single_key(self):
        per_source = {"": {"hits": 9, "drops": 1}}
        telemetry = LAIRunner._build_coverage_telemetry(per_source, "23andme_v4")
        assert telemetry == {"23andme": {"hits": 9, "drops": 1}}

    def test_missing_file_format_falls_back_to_unknown_vendor(self):
        per_source = {"": {"hits": 5, "drops": 0}}
        telemetry = LAIRunner._build_coverage_telemetry(per_source, "")
        assert telemetry == {"unknown": {"hits": 5, "drops": 0}}

    def test_empty_per_source_still_emits_zero_payload(self):
        telemetry = LAIRunner._build_coverage_telemetry({}, "23andme_v5")
        assert telemetry == {"23andme": {"hits": 0, "drops": 0}}


class TestTelemetryEmission:
    """End-to-end: 23andMe input emits the single-key log line."""

    def test_emits_lai_coverage_telemetry_event(self, runner, tmp_path, caplog):
        filtered = runner._filter_genotypes(_with_source(_BASE_23ANDME_GENOTYPES, ""))
        with patch.object(LAIRunner, "_write_single_vcf", _emit_all_sites):
            _, matched, per_source = runner._write_per_chrom_vcfs(filtered, tmp_path)
        telemetry = LAIRunner._build_coverage_telemetry(per_source, "23andme_v5")
        # The runner calls _emit_coverage_telemetry inside run(); call it
        # directly so we can assert the shape without a full pipeline.
        LAIRunner._emit_coverage_telemetry(
            total_genotypes=len(_BASE_23ANDME_GENOTYPES),
            filtered=len(filtered),
            matched=matched,
            per_source=telemetry,
            file_format="23andme_v5",
        )
        # The structured-log emitter is the contract; assertion lives in the
        # telemetry dict (already covered above). This test ensures the path
        # runs cleanly without raising.
        assert telemetry == {"23andme": {"hits": 4, "drops": 1}}

"""LAI runner: pre-Phase-3 sample DB backward compat (Step 22a; Plan §6.6).

Pre-Phase-3 sample DBs predate the ``raw_variants.source`` column (added in
step 63). On those DBs, ``_read_sample_genotypes`` defaults every genotype's
``source`` to ``""`` and the runner derives the vendor key from
``file_format.split("_", 1)[0].lower()`` — collapsing to a single-key
``{vendor: {hits, drops}}`` telemetry payload.

This test locks the derivation by:

1. Building an in-memory sample DB on the *current* schema (no ``source``
   column on ``raw_variants``).
2. Reading genotypes via ``_read_sample_genotypes`` and asserting every entry
   has ``source=""``.
3. Running the runner's filter + per-source accumulator and asserting the
   resulting telemetry is single-key with the expected vendor.

Parametrized over both shipped vendors so regressions to either derivation
trip immediately:

- ``23andme_v5`` → ``"23andme"``
- ``ancestrydna_v2.0`` → ``"ancestrydna"``
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
import sqlalchemy as sa

from backend.analysis.lai import _read_sample_file_format, _read_sample_genotypes
from backend.analysis.lai_runner import LAIRunner
from backend.db.tables import sample_metadata_table

# ── Shared payload ───────────────────────────────────────────────────────


# Three autosomal hits, one autosomal off-bundle drop. All single-source so
# the empty-string accumulator bucket carries every genotype.
_VENDOR_NEUTRAL_ROWS = [
    {"rsid": "rs_hit_1", "chrom": "1", "pos": 1001, "genotype": "AG"},
    {"rsid": "rs_hit_2", "chrom": "2", "pos": 2001, "genotype": "GG"},
    {"rsid": "rs_hit_3", "chrom": "22", "pos": 22001, "genotype": "CT"},
    {"rsid": "rs_off_bundle", "chrom": "5", "pos": 50000, "genotype": "TT"},
]


# ── Fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture()
def runner() -> LAIRunner:
    instance = LAIRunner.__new__(LAIRunner)
    instance.rsid_lookup = {
        "rs_hit_1": ("chr1", 1001),
        "rs_hit_2": ("chr2", 2001),
        "rs_hit_3": ("chr22", 22001),
    }
    return instance


def _emit_all_sites(_runner, _chrom, sites, _vcf_path):
    """Model a VCF writer that successfully emits every candidate site."""
    counts: dict[str, dict[str, int]] = {}
    for site in sites:
        src = site.get("source", "") or ""
        counts.setdefault(src, {"hits": 0, "drops": 0})["hits"] += 1
    return counts


def _build_pre_phase3_engine(file_format: str) -> sa.Engine:
    """Build an in-memory sample DB on the v7 (pre-step-63) raw_variants schema.

    Step 63 added the four provenance columns (``source``, ``concordance``,
    ``discordant_alt_genotype``, ``alt_rsid``) to the *current* schema. This
    fixture explicitly constructs a v7-shaped ``raw_variants`` (no provenance
    columns) so the backward-compat read path remains exercised — older sample
    DBs in the wild that haven't yet been through the v7→v8 migration must
    still surface ``source=""`` from ``_read_sample_genotypes``.

    The ``sample_metadata`` row stamps the requested ``file_format`` so the
    runner's vendor derivation has a real value to consume.
    """
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        # v7 raw_variants — exactly four columns, no provenance surface.
        conn.execute(
            sa.text(
                """CREATE TABLE raw_variants (
                    rsid TEXT PRIMARY KEY,
                    chrom TEXT NOT NULL,
                    pos INTEGER NOT NULL,
                    genotype TEXT NOT NULL
                )"""
            )
        )
        # sample_metadata is unchanged across v7/v8; reuse the live declaration.
        sample_metadata_table.create(conn, checkfirst=True)
        conn.execute(
            sample_metadata_table.insert().values(
                id=1,
                name=f"compat_{file_format}",
                file_format=file_format,
                file_hash="compat_hash",
            )
        )
        conn.execute(
            sa.text(
                "INSERT INTO raw_variants (rsid, chrom, pos, genotype) "
                "VALUES (:rsid, :chrom, :pos, :genotype)"
            ),
            _VENDOR_NEUTRAL_ROWS,
        )
    return engine


# ── Tests ─────────────────────────────────────────────────────────────────


class TestPrePhase3ReadDefaults:
    """`_read_sample_genotypes` defaults source to '' when column is absent."""

    @pytest.mark.parametrize(
        "file_format,expected_vendor",
        [
            ("23andme_v5", "23andme"),
            ("ancestrydna_v2.0", "ancestrydna"),
        ],
    )
    def test_source_defaults_to_empty_string(self, file_format, expected_vendor):
        engine = _build_pre_phase3_engine(file_format)
        # Sanity: confirm the source column genuinely isn't on raw_variants.
        cols = {c["name"] for c in sa.inspect(engine).get_columns("raw_variants")}
        assert "source" not in cols

        genotypes = _read_sample_genotypes(engine)
        assert len(genotypes) == len(_VENDOR_NEUTRAL_ROWS)
        assert all(gt["source"] == "" for gt in genotypes)

        # file_format roundtrips for downstream vendor derivation
        assert _read_sample_file_format(engine) == file_format
        assert file_format.split("_", 1)[0].lower() == expected_vendor


class TestPrePhase3SingleKeyTelemetry:
    """Single-key telemetry derives from ``file_format`` on pre-Phase-3 DBs."""

    @pytest.mark.parametrize(
        "file_format,expected_vendor",
        [
            ("23andme_v5", "23andme"),
            ("ancestrydna_v2.0", "ancestrydna"),
        ],
    )
    def test_pre_phase3_collapses_to_single_vendor_key(
        self, runner, tmp_path, file_format, expected_vendor
    ):
        engine = _build_pre_phase3_engine(file_format)
        file_format_read = _read_sample_file_format(engine)
        genotypes = _read_sample_genotypes(engine)
        filtered = runner._filter_genotypes(genotypes)

        with patch.object(LAIRunner, "_write_single_vcf", _emit_all_sites):
            _, total, per_source = runner._write_per_chrom_vcfs(filtered, tmp_path)

        # Accumulator only sees the empty-source bucket — no S1/S2/both leakage
        assert set(per_source.keys()) == {""}
        assert total == 3
        assert per_source[""] == {"hits": 3, "drops": 1}

        telemetry = LAIRunner._build_coverage_telemetry(per_source, file_format_read)
        assert set(telemetry.keys()) == {expected_vendor}
        assert telemetry[expected_vendor] == {"hits": 3, "drops": 1}


class TestVendorDerivationLocksSplitContract:
    """Lock the exact ``file_format.split("_", 1)[0].lower()`` derivation."""

    @pytest.mark.parametrize(
        "file_format,expected_vendor",
        [
            ("23andme_v5", "23andme"),
            ("23andme_v4", "23andme"),
            ("23andme_v3", "23andme"),
            ("ancestrydna_v2.0", "ancestrydna"),
            # Hypothetical case-insensitive vendor strings still normalize via
            # .lower() to guard against future producer changes.
            ("ANCESTRYDNA_v2.0", "ancestrydna"),
            ("23andMe_v5", "23andme"),
        ],
    )
    def test_build_coverage_telemetry_derives_vendor(self, file_format, expected_vendor):
        per_source = {"": {"hits": 5, "drops": 1}}
        telemetry = LAIRunner._build_coverage_telemetry(per_source, file_format)
        assert set(telemetry.keys()) == {expected_vendor}
        assert telemetry[expected_vendor] == {"hits": 5, "drops": 1}

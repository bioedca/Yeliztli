"""Tests for scripts/regenerate_fixtures.py.

Verifies that the regeneration script produces valid SQLite databases
from the seed CSVs, matching expected schemas and row counts.
"""

from __future__ import annotations

import csv
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures"
SEED_DIR = FIXTURES_DIR / "seed_csvs"
SCRIPT = Path(__file__).resolve().parent.parent.parent / "scripts" / "regenerate_fixtures.py"
PANEL_RSID_COORDINATES = FIXTURES_DIR / "panel_rsid_coordinates.json"

THROMBOPHILIA_COORDINATE_RSIDS = ("rs6025", "rs1799963")

SEED_COORDINATE_TARGETS = {
    "clinvar_seed.csv": THROMBOPHILIA_COORDINATE_RSIDS,
    "vep_seed.csv": THROMBOPHILIA_COORDINATE_RSIDS,
    "gnomad_seed.csv": THROMBOPHILIA_COORDINATE_RSIDS,
    "gwas_seed.csv": THROMBOPHILIA_COORDINATE_RSIDS,
    "dbnsfp_seed.csv": ("rs6025",),
}

MINI_DB_COORDINATE_TARGETS = {
    "mini_reference.db": {
        "clinvar_variants": THROMBOPHILIA_COORDINATE_RSIDS,
        "gwas_associations": THROMBOPHILIA_COORDINATE_RSIDS,
    },
    "mini_vep_bundle.db": {"vep_annotations": THROMBOPHILIA_COORDINATE_RSIDS},
    "mini_gnomad_af.db": {"gnomad_af": THROMBOPHILIA_COORDINATE_RSIDS},
    "mini_dbnsfp.db": {"dbnsfp_scores": ("rs6025",)},
}


def _expected_grch37_coordinates() -> dict[str, tuple[str, int]]:
    payload = json.loads(PANEL_RSID_COORDINATES.read_text())
    variants = payload["rsids"]
    return {
        rsid: (str(variants[rsid]["chrom"]), int(variants[rsid]["start"]))
        for rsid in THROMBOPHILIA_COORDINATE_RSIDS
    }


def _run_script(tmp_path: Path) -> subprocess.CompletedProcess[str]:
    """Run regenerate_fixtures.py and assert it succeeds."""
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--output-dir", str(tmp_path)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"Script failed:\n{result.stderr}"
    return result


# ── Seed CSV existence ────────────────────────────────────────────────

EXPECTED_CSVS = [
    "clinvar_seed.csv",
    "vep_seed.csv",
    "gnomad_seed.csv",
    "dbnsfp_seed.csv",
    "cpic_alleles_seed.csv",
    "cpic_diplotypes_seed.csv",
    "cpic_guidelines_seed.csv",
    "gwas_seed.csv",
    "gene_phenotype_seed.csv",
]


class TestSeedCSVsExist:
    """All required seed CSVs must be present."""

    @pytest.mark.parametrize("csv_name", EXPECTED_CSVS)
    def test_csv_exists(self, csv_name: str) -> None:
        path = SEED_DIR / csv_name
        assert path.exists(), f"Missing seed CSV: {path}"

    @pytest.mark.parametrize("csv_name", EXPECTED_CSVS)
    def test_csv_has_header_and_data(self, csv_name: str) -> None:
        path = SEED_DIR / csv_name
        lines = path.read_text().strip().splitlines()
        assert len(lines) >= 2, f"{csv_name} must have header + at least 1 data row"


# ── Seed CSV content validation ──────────────────────────────────────


class TestSeedCSVContent:
    """Validate that seed CSVs contain required key variants."""

    def test_clinvar_contains_key_rsids(self) -> None:
        text = (SEED_DIR / "clinvar_seed.csv").read_text()
        for rsid in ["rs429358", "rs7412", "rs1801133", "rs4680", "rs80357906", "rs113993960"]:
            assert rsid in text, f"clinvar_seed.csv missing {rsid}"

    def test_gwas_contains_key_rsids(self) -> None:
        text = (SEED_DIR / "gwas_seed.csv").read_text()
        for rsid in ["rs429358", "rs1801133", "rs4680", "rs12913832", "rs7903146"]:
            assert rsid in text, f"gwas_seed.csv missing {rsid}"

    def test_vep_contains_key_rsids(self) -> None:
        text = (SEED_DIR / "vep_seed.csv").read_text()
        for rsid in ["rs429358", "rs7412", "rs1801133"]:
            assert rsid in text, f"vep_seed.csv missing {rsid}"

    def test_cpic_alleles_contains_key_genes(self) -> None:
        text = (SEED_DIR / "cpic_alleles_seed.csv").read_text()
        for gene in ["CYP2D6", "CYP2C19"]:
            assert gene in text, f"cpic_alleles_seed.csv missing {gene}"

    @pytest.mark.parametrize(("csv_name", "rsids"), SEED_COORDINATE_TARGETS.items())
    def test_thrombophilia_seed_coordinates_are_grch37(
        self, csv_name: str, rsids: tuple[str, ...]
    ) -> None:
        expected = _expected_grch37_coordinates()
        with (SEED_DIR / csv_name).open(newline="", encoding="utf-8") as fh:
            rows = {row["rsid"]: row for row in csv.DictReader(fh) if row["rsid"] in rsids}

        assert set(rows) == set(rsids), f"{csv_name} missing thrombophilia rows"
        for rsid in rsids:
            expected_chrom, expected_pos = expected[rsid]
            row = rows[rsid]
            assert (row["chrom"], int(row["pos"])) == (expected_chrom, expected_pos)


# ── Regeneration script ──────────────────────────────────────────────


class TestRegenerateFixtures:
    """Test the regenerate_fixtures.py script end-to-end."""

    def test_dry_run_does_not_create_files(self, tmp_path: Path) -> None:
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--output-dir", str(tmp_path), "--dry-run"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        db_files = list(tmp_path.glob("*.db"))
        assert len(db_files) == 0, f"Dry run should not create files, found: {db_files}"

    def test_generates_all_databases(self, tmp_path: Path) -> None:
        _run_script(tmp_path)

        expected_dbs = [
            "mini_reference.db",
            "mini_vep_bundle.db",
            "mini_gnomad_af.db",
            "mini_dbnsfp.db",
        ]
        for db_name in expected_dbs:
            assert (tmp_path / db_name).exists(), f"Missing {db_name}"

    def test_mini_reference_schema(self, tmp_path: Path) -> None:
        _run_script(tmp_path)
        with sqlite3.connect(str(tmp_path / "mini_reference.db")) as conn:
            tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master"
                    " WHERE type='table' AND name != 'sqlite_sequence'"
                ).fetchall()
            }

        required = {
            "clinvar_variants",
            "gene_phenotype",
            "cpic_alleles",
            "cpic_diplotypes",
            "cpic_guidelines",
            "gwas_associations",
            "samples",
            "jobs",
            "database_versions",
            "update_history",
            "downloads",
            "literature_cache",
            "uniprot_cache",
            "log_entries",
            "reannotation_prompts",
        }
        assert required.issubset(tables), f"Missing tables: {required - tables}"

    def test_mini_reference_row_counts(self, tmp_path: Path) -> None:
        _run_script(tmp_path)
        with sqlite3.connect(str(tmp_path / "mini_reference.db")) as conn:
            clinvar_count = conn.execute("SELECT count(*) FROM clinvar_variants").fetchone()[0]
            gene_pheno_count = conn.execute("SELECT count(*) FROM gene_phenotype").fetchone()[0]
            cpic_alleles_count = conn.execute("SELECT count(*) FROM cpic_alleles").fetchone()[0]
            gwas_count = conn.execute("SELECT count(*) FROM gwas_associations").fetchone()[0]

        assert clinvar_count >= 50, f"Expected >=50 clinvar rows, got {clinvar_count}"
        assert gene_pheno_count >= 20, f"Expected >=20 gene_phenotype rows, got {gene_pheno_count}"
        assert cpic_alleles_count >= 10, (
            f"Expected >=10 cpic_alleles rows, got {cpic_alleles_count}"
        )
        assert gwas_count >= 30, f"Expected >=30 gwas rows, got {gwas_count}"

    def test_mini_vep_bundle_has_data(self, tmp_path: Path) -> None:
        _run_script(tmp_path)
        with sqlite3.connect(str(tmp_path / "mini_vep_bundle.db")) as conn:
            count = conn.execute("SELECT count(*) FROM vep_annotations").fetchone()[0]
        assert count >= 50, f"Expected >=50 VEP rows, got {count}"

    def test_mini_vep_bundle_covers_ancestrydna_rsids(self, tmp_path: Path) -> None:
        """Step 39: mini bundle covers every rsID in ``sample_ancestrydna_v2.txt``
        except the defensive ``kgp*`` rows.

        The kgp rows are intentionally absent so step 40's ADNA-09 regression test
        exercises the coordinate-fallback path. Every other rsID — including those
        on remapped chromosomes 23/24/25/26 (X/Y/PAR→X/MT) — must round-trip
        from the fixture into ``vep_annotations``.
        """
        fixture = FIXTURES_DIR / "sample_ancestrydna_v2.txt"
        non_kgp_rsids: set[str] = set()
        kgp_rsids: set[str] = set()
        for raw in fixture.read_text().splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or line.startswith("rsid\t"):
                continue
            parts = line.split("\t")
            if len(parts) != 5:
                continue
            rsid = parts[0]
            if rsid.startswith("kgp"):
                kgp_rsids.add(rsid)
            else:
                non_kgp_rsids.add(rsid)

        assert non_kgp_rsids, "fixture parsed zero non-kgp rsIDs — check parsing"
        assert kgp_rsids, "fixture parsed zero kgp rsIDs — coord-fallback case missing"

        _run_script(tmp_path)
        with sqlite3.connect(str(tmp_path / "mini_vep_bundle.db")) as conn:
            bundle_rsids: set[str] = {
                row[0] for row in conn.execute("SELECT rsid FROM vep_annotations").fetchall()
            }

        missing = non_kgp_rsids - bundle_rsids
        assert not missing, (
            f"Mini VEP bundle missing {len(missing)} AncestryDNA rsIDs "
            f"(first 10: {sorted(missing)[:10]})"
        )
        unexpected_kgp = kgp_rsids & bundle_rsids
        assert not unexpected_kgp, (
            f"kgp* rsIDs must not be in the mini bundle (coord-fallback path): "
            f"{sorted(unexpected_kgp)}"
        )

    def test_mini_vep_bundle_carries_v2_0_0_metadata(self, tmp_path: Path) -> None:
        """Phase 0 closure (Step 18): mini bundle mirrors v2.0.0 schema.

        The production VEP bundle writes `bundle_metadata` with at minimum
        `bundle_version`, `build_date`, `schema_version`, `ensembl_version`,
        and `variant_count` (see `scripts/build_vep_bundle.py`). The mini
        fixture must align so `update_manager.run_vep_bundle_update`'s parity
        check exercises the same code path against the fixture.
        AncestryDNA rsID coverage in the seed CSV is added later in step 39.
        """
        _run_script(tmp_path)
        with sqlite3.connect(str(tmp_path / "mini_vep_bundle.db")) as conn:
            tables = {
                row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            assert "bundle_metadata" in tables

            metadata = dict(conn.execute("SELECT key, value FROM bundle_metadata"))

        for required in (
            "bundle_version",
            "build_date",
            "schema_version",
            "ensembl_version",
            "variant_count",
        ):
            assert required in metadata, f"missing bundle_metadata key: {required}"
        assert metadata["bundle_version"] == "v2.0.0"
        assert metadata["schema_version"] == "1"
        # `variant_count` matches the seed CSV row count exactly.
        assert int(metadata["variant_count"]) >= 50

    def test_mini_gnomad_has_data(self, tmp_path: Path) -> None:
        _run_script(tmp_path)
        with sqlite3.connect(str(tmp_path / "mini_gnomad_af.db")) as conn:
            count = conn.execute("SELECT count(*) FROM gnomad_af").fetchone()[0]
        assert count >= 50, f"Expected >=50 gnomAD rows, got {count}"

    def test_mini_dbnsfp_has_data(self, tmp_path: Path) -> None:
        _run_script(tmp_path)
        with sqlite3.connect(str(tmp_path / "mini_dbnsfp.db")) as conn:
            count = conn.execute("SELECT count(*) FROM dbnsfp_scores").fetchone()[0]
        assert count >= 30, f"Expected >=30 dbNSFP rows, got {count}"

    @pytest.mark.parametrize(("db_name", "tables"), MINI_DB_COORDINATE_TARGETS.items())
    def test_thrombophilia_mini_db_coordinates_are_grch37(
        self, tmp_path: Path, db_name: str, tables: dict[str, tuple[str, ...]]
    ) -> None:
        expected = _expected_grch37_coordinates()
        _run_script(tmp_path)

        with sqlite3.connect(str(tmp_path / db_name)) as conn:
            for table_name, rsids in tables.items():
                placeholders = ", ".join("?" for _ in rsids)
                observed = {
                    rsid: (str(chrom), int(pos))
                    for rsid, chrom, pos in conn.execute(
                        f"SELECT rsid, chrom, pos FROM {table_name} "
                        f"WHERE rsid IN ({placeholders})",
                        tuple(rsids),
                    )
                }
                assert set(observed) == set(rsids), f"{db_name}:{table_name} missing rows"
                for rsid in rsids:
                    assert observed[rsid] == expected[rsid]

    def test_wal_mode_enabled(self, tmp_path: Path) -> None:
        _run_script(tmp_path)
        for db_name in [
            "mini_reference.db",
            "mini_vep_bundle.db",
            "mini_gnomad_af.db",
            "mini_dbnsfp.db",
        ]:
            with sqlite3.connect(str(tmp_path / db_name)) as conn:
                mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
            assert mode == "wal", f"{db_name} should use WAL mode, got {mode}"

    def test_clinvar_data_integrity(self, tmp_path: Path) -> None:
        """Verify a known ClinVar entry is correctly loaded."""
        _run_script(tmp_path)
        with sqlite3.connect(str(tmp_path / "mini_reference.db")) as conn:
            row = conn.execute(
                "SELECT chrom, pos, significance, gene_symbol"
                " FROM clinvar_variants WHERE rsid = 'rs429358'"
            ).fetchone()
        assert row is not None, "rs429358 not found in clinvar_variants"
        assert row[0] == "19"
        assert row[1] == 44908684
        assert row[2] == "risk_factor"
        assert row[3] == "APOE"

    def test_idempotent_regeneration(self, tmp_path: Path) -> None:
        """Running the script twice produces identical row counts."""
        counts = []
        for _ in range(2):
            _run_script(tmp_path)
            with sqlite3.connect(str(tmp_path / "mini_reference.db")) as conn:
                count = conn.execute("SELECT count(*) FROM clinvar_variants").fetchone()[0]
            counts.append(count)
        assert counts[0] >= 50, f"Expected >=50 rows, got {counts[0]}"
        assert counts[0] == counts[1], f"Row count changed: {counts[0]} -> {counts[1]}"


# ── Step 41: --vendor=ancestrydna synthetic fixture ──────────────────


_TEMPLATE_PATH = FIXTURES_DIR / "synthetic_eur_23andme.txt"


def _run_vendor_script(
    tmp_path: Path,
    *,
    dry_run: bool = False,
) -> subprocess.CompletedProcess[str]:
    """Run regenerate_fixtures.py in --vendor=ancestrydna mode."""
    argv = [
        sys.executable,
        str(SCRIPT),
        "--vendor=ancestrydna",
        "--output-dir",
        str(tmp_path),
        "--template",
        str(_TEMPLATE_PATH),
    ]
    if dry_run:
        argv.append("--dry-run")
    result = subprocess.run(argv, capture_output=True, text=True)
    assert result.returncode == 0, f"Script failed:\n{result.stderr}"
    return result


def _template_variant_count() -> int:
    """Return the number of non-comment / non-header rows in the template."""
    count = 0
    for raw in _TEMPLATE_PATH.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        first = line.split("\t", 1)[0].lower()
        if first == "rsid":
            continue
        count += 1
    return count


class TestSyntheticAncestryDNAFixture:
    """``--vendor=ancestrydna`` emits a parseable synthetic AncestryDNA file.

    Plan §16.1, ADNA-09a / step 41 — drives the nightly slow-tier real-bundle
    hit-rate test. The fixture must (a) be derived only from the synthetic
    1000G EUR template, (b) carry a clear "synthetic — not real user data"
    marker in the header, and (c) round-trip through the production
    AncestryDNA parser to the expected variant count.
    """

    def test_template_fixture_exists(self) -> None:
        assert _TEMPLATE_PATH.is_file(), (
            f"23andMe template missing — required by --vendor=ancestrydna mode: {_TEMPLATE_PATH}"
        )

    def test_dry_run_does_not_write_output(self, tmp_path: Path) -> None:
        _run_vendor_script(tmp_path, dry_run=True)
        assert not (tmp_path / "synthetic_eur_ancestrydna.txt").exists()

    def test_emits_output_at_expected_path(self, tmp_path: Path) -> None:
        _run_vendor_script(tmp_path)
        output = tmp_path / "synthetic_eur_ancestrydna.txt"
        assert output.is_file(), f"Expected output at {output}"
        # Size proxies "full-size synthetic" — the template is ~5,000 rows.
        assert output.stat().st_size > 50_000

    def test_header_carries_synthetic_marker_and_vendor_signature(self, tmp_path: Path) -> None:
        _run_vendor_script(tmp_path)
        text = (tmp_path / "synthetic_eur_ancestrydna.txt").read_text()
        head = text.splitlines()[:12]
        head_text = "\n".join(head)
        # Vendor signature for the dispatcher (Plan §8.3 detector contract).
        assert "#AncestryDNA" in head_text
        # Array-version line so `detect_version` resolves to v2.0.
        assert "AncestryDNA array version: V2.0" in head_text
        # Loud "not real user data" marker — Plan §16.1 invariant.
        assert "SYNTHETIC FIXTURE" in head_text
        assert "Must never contain real user genotypes." in head_text
        # 5-column TSV header row immediately after the comment block.
        assert "rsid\tchromosome\tposition\tallele1\tallele2" in head_text

    def test_round_trips_through_ancestrydna_parser(self, tmp_path: Path) -> None:
        from backend.ingestion.base import SourceVendor
        from backend.ingestion.parser_ancestrydna import parse_ancestrydna

        _run_vendor_script(tmp_path)
        output = tmp_path / "synthetic_eur_ancestrydna.txt"

        result = parse_ancestrydna(output)
        assert result.vendor == SourceVendor.ANCESTRYDNA
        assert result.version == "v2.0"
        assert result.build == "GRCh37"
        assert len(result.variants) == _template_variant_count()

    def test_genotypes_split_into_two_allele_columns(self, tmp_path: Path) -> None:
        _run_vendor_script(tmp_path)
        output = tmp_path / "synthetic_eur_ancestrydna.txt"

        data_rows = [
            line
            for line in output.read_text().splitlines()
            if line and not line.startswith("#") and not line.startswith("rsid\t")
        ]
        # Every row is exactly 5 tab-separated columns.
        for row in data_rows[:50]:
            assert row.count("\t") == 4, f"non-5-column row: {row!r}"
            cols = row.split("\t")
            # allele1, allele2 are single characters drawn from ACGT or "0".
            assert len(cols[3]) == 1 and len(cols[4]) == 1
            assert cols[3] in "ACGT0"
            assert cols[4] in "ACGT0"

    def test_idempotent(self, tmp_path: Path) -> None:
        _run_vendor_script(tmp_path)
        first = (tmp_path / "synthetic_eur_ancestrydna.txt").read_text()
        _run_vendor_script(tmp_path)
        second = (tmp_path / "synthetic_eur_ancestrydna.txt").read_text()
        assert first == second

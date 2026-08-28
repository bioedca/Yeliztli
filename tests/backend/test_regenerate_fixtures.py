"""Tests for scripts/regenerate_fixtures.py.

Verifies that the regeneration script produces valid SQLite databases
from the seed CSVs, matching expected schemas and row counts.
"""

from __future__ import annotations

import csv
import json
import re
import sqlite3
import subprocess
import sys
from collections import Counter
from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures"
SEED_DIR = FIXTURES_DIR / "seed_csvs"
SCRIPT = Path(__file__).resolve().parent.parent.parent / "scripts" / "regenerate_fixtures.py"
PANEL_RSID_COORDINATES = FIXTURES_DIR / "panel_rsid_coordinates.json"
MINI_CLINVAR_VCF = FIXTURES_DIR / "mini_clinvar.vcf"
NOT_23ANDME_VCF = FIXTURES_DIR / "sample_not_23andme.vcf"

# Vendor raw-data fixtures, keyed by the parser that reads them. The declared
# build is never hard-coded here: it comes back from the production parser, so
# the guard follows what the ingestion path actually believes about each file.
VENDOR_23ANDME_FIXTURES = (
    "sample_23andme_v3.txt",
    "sample_23andme_v4.txt",
    "sample_23andme_v5.txt",
)
VENDOR_ANCESTRYDNA_FIXTURES = (
    "sample_ancestrydna_v2.txt",
    "sample_ancestrydna_crlf.txt",
    "sample_ancestrydna_non_utf8_byte.txt",
)
# The build-36 23andMe v3 export is a deliberate source-build exception: it
# exists to exercise build detection and hg18 -> GRCh37 conversion, so it must
# keep its build-36 coordinates. See tests/fixtures/seed_csvs/README.md.
BUILD36_APOE_COORDINATES = {
    "rs429358": ("19", 50_103_781),
    "rs7412": ("19", 50_103_919),
}

# The three APOE-region SNPs that share the ε-defining block in the vendor
# fixtures. They are not seed rsIDs, so they cannot join
# ADDITIONAL_VERIFIED_GRCH37_MAPPINGS — that dict is asserted to appear in every
# coordinate-bearing seed CSV. They are guarded here instead, because #476
# corrected them: the block was copied from a GRCh38 source, and two of the
# three had additionally been paired with the wrong position (rs440446 carried
# rs405509's GRCh38 coordinate). Ensembl GRCh37 Variation REST, accessed
# 2026-08-27; each REF confirmed against the UCSC hg19 reference base.
APOE_REGION_GRCH37_MAPPINGS: dict[str, GRCh37Mapping] = {
    "rs405509": ("19", 45_408_836, frozenset({"T", "G"})),
    "rs440446": ("19", 45_409_167, frozenset({"C", "G", "T"})),
    "rs769449": ("19", 45_410_002, frozenset({"G", "A"})),
}

# The plus-strand GRCh37 reference base at each APOE-block position, read from
# the UCSC hg19 assembly on 2026-08-27. The allele-set check above validates
# membership only, so it cannot see a REF/ALT swap — and two rows in
# sample_not_23andme.vcf carried exactly that (rs405509 as G>T, rs769449 as
# A>G). A VCF REF is the assembly base, so it can be pinned exactly.
APOE_BLOCK_GRCH37_REFERENCE_BASE = {
    "rs405509": "T",
    "rs440446": "C",
    "rs769449": "G",
    "rs429358": "T",
    "rs7412": "C",
}

GRCh37Mapping = tuple[str, int, frozenset[str]]

ADDITIONAL_VERIFIED_GRCH37_MAPPINGS: dict[str, GRCh37Mapping] = {
    # Ensembl GRCh37 Variation REST, accessed 2026-07-19. The primary mapping
    # is 7:94937446 with allele string T/A/C/G; this fixture selects T>C.
    # https://grch37.rest.ensembl.org/variation/human/rs662
    "rs662": ("7", 94_937_446, frozenset({"T", "C"})),
    # The two APOE ε-defining SNPs (#476). They are matched by rsID in
    # backend/analysis/apoe.py and therefore never entered the panel snapshot,
    # which is exactly why the seeds carried the GRCh38 pair 19:44908684 /
    # 19:44908822 under a README contract declaring GRCh37. Pinning them here
    # puts both inside the seed/oracle guard so the build cannot drift again.
    # Ensembl GRCh37 Variation REST, accessed 2026-08-27; primary mapping,
    # plus strand. Cross-checked against NCBI dbSNP eSummary
    # ``chrpos_prev_assm`` (19:45411941 / 19:45412079), which reports the same
    # GRCh38 pair the seeds had been carrying.
    # https://grch37.rest.ensembl.org/variation/human/rs429358
    "rs429358": ("19", 45_411_941, frozenset({"T", "C"})),
    # https://grch37.rest.ensembl.org/variation/human/rs7412
    "rs7412": ("19", 45_412_079, frozenset({"C", "T"})),
}

# Only seeds governed by the real GRCh37 coordinate and plus-strand allele
# oracle belong here. GWAS is synthetic rsID-membership data, while dbNSFP uses
# synthetic GRCh38 lookup keys and has its own scenario contract. Generated
# database parity remains independently covered by MINI_DB_NAMES.
COORDINATE_SEED_DB_TARGETS = (
    ("clinvar_seed.csv", "mini_reference.db", "clinvar_variants"),
    ("vep_seed.csv", "mini_vep_bundle.db", "vep_annotations"),
    ("gnomad_seed.csv", "mini_gnomad_af.db", "gnomad_af"),
)

ALLELE_SEED_CSVS = tuple(target[0] for target in COORDINATE_SEED_DB_TARGETS)
MINI_DB_NAMES = (
    "mini_reference.db",
    "mini_vep_bundle.db",
    "mini_gnomad_af.db",
    "mini_dbnsfp.db",
)


def _expected_grch37_mappings() -> dict[str, GRCh37Mapping]:
    payload = json.loads(PANEL_RSID_COORDINATES.read_text())
    variants = payload["rsids"]
    expected: dict[str, GRCh37Mapping] = {}
    for rsid, variant in variants.items():
        assert variant["assembly"] == "GRCh37", f"{rsid} oracle mapping is not GRCh37"
        assert int(variant["strand"]) == 1, f"{rsid} oracle alleles are not plus-strand"
        alleles = frozenset(str(variant["allele_string"]).split("/"))
        assert alleles and "" not in alleles, f"{rsid} oracle has no usable allele set"
        expected[rsid] = (str(variant["chrom"]), int(variant["start"]), alleles)

    for rsid, mapping in ADDITIONAL_VERIFIED_GRCH37_MAPPINGS.items():
        assert expected.get(rsid, mapping) == mapping, (
            f"{rsid} has conflicting panel and non-panel GRCh37 mapping evidence"
        )
        expected[rsid] = mapping
    return expected


def _guarded_seed_coordinates(
    csv_name: str, expected: dict[str, GRCh37Mapping]
) -> list[tuple[str, str, int]]:
    with (SEED_DIR / csv_name).open(newline="", encoding="utf-8") as fh:
        return [
            (row["rsid"], row["chrom"], int(row["pos"]))
            for row in csv.DictReader(fh)
            if row["rsid"] in expected
        ]


def _guarded_db_coordinates(
    db_path: Path, table_name: str, expected: dict[str, GRCh37Mapping]
) -> list[tuple[str, str, int]]:
    uri = f"file:{db_path.resolve()}?mode=ro&immutable=1"
    with sqlite3.connect(uri, uri=True) as conn:
        return [
            (str(rsid), str(chrom), int(pos))
            for rsid, chrom, pos in conn.execute(
                f"SELECT rsid, chrom, pos FROM {table_name}"  # noqa: S608 -- fixed table names
            )
            if rsid in expected
        ]


def _assert_grch37_coordinates(
    rows: list[tuple[str, str, int]],
    expected: dict[str, GRCh37Mapping],
    source: str,
) -> None:
    assert rows, f"{source} has no rows overlapping the GRCh37 coordinate oracle"
    for occurrence, (rsid, chrom, pos) in enumerate(rows, start=1):
        assert (chrom, pos) == expected[rsid][:2], (
            f"{source} occurrence {occurrence} ({rsid}) is at {chrom}:{pos}; "
            f"expected {expected[rsid][0]}:{expected[rsid][1]}"
        )


def _guarded_seed_alleles(
    csv_name: str, expected: dict[str, GRCh37Mapping]
) -> list[tuple[str, str, str]]:
    with (SEED_DIR / csv_name).open(newline="", encoding="utf-8") as fh:
        return [
            (row["rsid"], row["ref"], row["alt"])
            for row in csv.DictReader(fh)
            if row["rsid"] in expected
        ]


def _assert_grch37_plus_strand_alleles(
    rows: list[tuple[str, str, str]],
    expected: dict[str, GRCh37Mapping],
    source: str,
) -> None:
    assert rows, f"{source} has no rows overlapping the GRCh37 mapping oracle"
    for occurrence, (rsid, ref, alt) in enumerate(rows, start=1):
        allowed = expected[rsid][2]
        assert {ref, alt} <= allowed, (
            f"{source} occurrence {occurrence} ({rsid}) uses {ref}>{alt}; "
            f"expected plus-strand alleles from {sorted(allowed)}"
        )


def _mini_clinvar_vcf() -> tuple[str, list[tuple[str, str, int, str, str]]]:
    """Return the declared reference build and every ``RS=``-carrying record."""
    reference = ""
    records: list[tuple[str, str, int, str, str]] = []
    for line in MINI_CLINVAR_VCF.read_text(encoding="utf-8").splitlines():
        if line.startswith("##reference="):
            reference = line.split("=", 1)[1]
            continue
        if line.startswith("#") or not line.strip():
            continue
        chrom, pos, _id, ref, alt = line.split("\t")[:5]
        info = line.split("\t")[7]
        match = re.search(r"(?:^|;)RS=(\d+)(?:;|$)", info)
        if match is None:
            continue
        records.append((f"rs{match.group(1)}", chrom, int(pos), ref, alt))
    return reference, records


def _semantic_sqlite_dump(db_path: Path) -> tuple[str, ...]:
    """Return schema and data as SQL, excluding binary-file layout details."""
    uri = f"file:{db_path.resolve()}?mode=ro&immutable=1"
    with sqlite3.connect(uri, uri=True) as conn:
        return tuple(conn.iterdump())


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

    def test_gnomad_population_columns_are_not_verbatim_copies(self) -> None:
        """No gnomAD ``af_*`` column may duplicate another across every row (#1964).

        ``af_asj`` shipped as a byte-identical copy of ``af_global`` on 97/97 rows
        (PR #1120) — a degenerate oracle where an engine mis-wiring of
        ``gnomad_af_asj -> af_global`` passed CI undetected. A population column
        that equals another on *every* row carries no independent information;
        partial coincidental overlap (e.g. ``af_fin`` on a few rare rows) is fine.
        Empty cells (SQL NULL / unknown) are compared as-is, so an all-empty
        column would also trip this guard.
        """
        with (SEED_DIR / "gnomad_seed.csv").open(newline="", encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
        af_cols = [c for c in rows[0] if c.startswith("af_")]
        for i, a in enumerate(af_cols):
            for b in af_cols[i + 1 :]:
                identical = all(r[a] == r[b] for r in rows)
                assert not identical, (
                    f"gnomad_seed.csv: '{a}' is a verbatim copy of '{b}' on all "
                    f"{len(rows)} rows — a degenerate, non-discriminating oracle"
                )

    @pytest.mark.parametrize("csv_name", [target[0] for target in COORDINATE_SEED_DB_TARGETS])
    def test_guarded_seed_coordinates_are_grch37(self, csv_name: str) -> None:
        expected = _expected_grch37_mappings()
        rows = _guarded_seed_coordinates(csv_name, expected)
        observed_rsids = {rsid for rsid, _chrom, _pos in rows}
        assert ADDITIONAL_VERIFIED_GRCH37_MAPPINGS.keys() <= observed_rsids, (
            f"{csv_name} is missing a verified non-panel coordinate row"
        )
        _assert_grch37_coordinates(rows, expected, csv_name)

    @pytest.mark.parametrize("csv_name", ALLELE_SEED_CSVS)
    def test_guarded_seed_alleles_are_grch37_plus_strand(self, csv_name: str) -> None:
        expected = _expected_grch37_mappings()
        rows = _guarded_seed_alleles(csv_name, expected)
        _assert_grch37_plus_strand_alleles(rows, expected, csv_name)

    def test_synthetic_gwas_rows_leave_scientific_fields_empty(self) -> None:
        with (SEED_DIR / "gwas_seed.csv").open(newline="", encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))

        scientific_fields = (
            "chrom",
            "pos",
            "p_value",
            "odds_ratio",
            "beta",
            "risk_allele",
            "pubmed_id",
            "study",
            "sample_size",
        )

        assert rows, "gwas_seed.csv has no synthetic membership rows"
        assert all(row["trait"] == "Synthetic GWAS membership fixture" for row in rows)
        assert all(not row[field] for row in rows for field in scientific_fields)


# ── Vendor raw-data fixtures ─────────────────────────────────────────


def _vendor_apoe_rows(
    variants: object, expected: dict[str, GRCh37Mapping]
) -> list[tuple[str, str, int]]:
    return [
        (v.rsid, v.chrom, int(v.pos))
        for v in variants  # type: ignore[attr-defined]
        if v.rsid in expected
    ]


class TestVendorFixtureBuildCoordinates:
    """A vendor fixture must place APOE at the build its parser reports (#476).

    The seeds and ``mini_clinvar.vcf`` are reference data; these are ingestion
    *inputs*, and they carried the same GRCh38 APOE pair under headers that say
    build 37. That is not only mislabeled — once the reference fixtures moved to
    GRCh37, a build-38 input would no longer position-match them, which is
    exactly the join an i-prefixed array probe depends on.

    The declared build is read back from the production parser rather than
    asserted from a filename, so the guard tracks what ingestion believes.
    """

    @pytest.mark.parametrize("fixture_name", VENDOR_23ANDME_FIXTURES)
    def test_23andme_fixture_apoe_matches_its_declared_build(self, fixture_name: str) -> None:
        from backend.ingestion.parser_23andme import parse_23andme

        expected = _expected_grch37_mappings()
        result = parse_23andme(FIXTURES_DIR / fixture_name)
        if result.build != "GRCh36":
            expected |= APOE_REGION_GRCH37_MAPPINGS
        rows = _vendor_apoe_rows(result.variants, expected)
        assert {"rs429358", "rs7412"} <= {rsid for rsid, _c, _p in rows}, (
            f"{fixture_name} no longer carries both APOE ε-defining SNPs"
        )

        if result.build == "GRCh36":
            # Only APOE has a verified build-36 mapping recorded here, so the
            # build-36 branch checks exactly the pair this guard exists for.
            for rsid, chrom, pos in rows:
                if rsid not in BUILD36_APOE_COORDINATES:
                    continue
                assert (chrom, pos) == BUILD36_APOE_COORDINATES[rsid], (
                    f"{fixture_name} declares {result.build} but {rsid} is at "
                    f"{chrom}:{pos}; expected {BUILD36_APOE_COORDINATES[rsid]}"
                )
            return

        assert result.build == "GRCh37", (
            f"{fixture_name} reports an unhandled build {result.build!r}"
        )
        _assert_grch37_coordinates(rows, expected, fixture_name)

    @pytest.mark.parametrize("fixture_name", VENDOR_ANCESTRYDNA_FIXTURES)
    def test_ancestrydna_fixture_apoe_matches_its_declared_build(self, fixture_name: str) -> None:
        from backend.ingestion.parser_ancestrydna import parse_ancestrydna

        expected = _expected_grch37_mappings()
        result = parse_ancestrydna(FIXTURES_DIR / fixture_name)
        assert result.build == "GRCh37", (
            f"{fixture_name} reports an unhandled build {result.build!r}"
        )
        rows = _vendor_apoe_rows(result.variants, expected)
        assert {"rs429358", "rs7412"} <= {rsid for rsid, _c, _p in rows}, (
            f"{fixture_name} no longer carries both APOE ε-defining SNPs"
        )
        _assert_grch37_coordinates(rows, expected, fixture_name)

    def test_grch37_declaring_vcf_fixture_is_grch37(self) -> None:
        """``sample_not_23andme.vcf`` declares ``##reference=GRCh37``."""
        expected = _expected_grch37_mappings()
        expected |= APOE_REGION_GRCH37_MAPPINGS
        reference = ""
        rows: list[tuple[str, str, int]] = []
        refalt: dict[str, tuple[str, str]] = {}
        for line in NOT_23ANDME_VCF.read_text(encoding="utf-8").splitlines():
            if line.startswith("##reference="):
                reference = line.split("=", 1)[1]
                continue
            if line.startswith("#") or not line.strip():
                continue
            chrom, pos, rsid, ref, alt = line.split("\t")[:5]
            if rsid in expected:
                rows.append((rsid, chrom, int(pos)))
                refalt[rsid] = (ref, alt)

        assert reference == "GRCh37", f"fixture declares reference {reference!r}"
        assert {"rs429358", "rs7412"} <= {rsid for rsid, _c, _p in rows}
        _assert_grch37_coordinates(rows, expected, NOT_23ANDME_VCF.name)
        for rsid, (ref, alt) in refalt.items():
            assert {ref, alt} <= expected[rsid][2], (
                f"{NOT_23ANDME_VCF.name} {rsid} uses {ref}>{alt}; expected "
                f"plus-strand alleles from {sorted(expected[rsid][2])}"
            )
            if rsid in APOE_BLOCK_GRCH37_REFERENCE_BASE:
                # Membership alone cannot see a REF/ALT swap, so pin REF to the
                # assembly base for the block this issue rewrote.
                assert ref == APOE_BLOCK_GRCH37_REFERENCE_BASE[rsid], (
                    f"{NOT_23ANDME_VCF.name} {rsid} declares REF {ref}; the "
                    f"GRCh37 assembly base is "
                    f"{APOE_BLOCK_GRCH37_REFERENCE_BASE[rsid]}"
                )

    def test_build36_fixture_lifts_onto_the_grch37_oracle(self) -> None:
        """The build-36 exception is deliberate, and it agrees with the oracle.

        This is the negative control for the guard above. Without it, the suite
        could not tell a correct build-36 fixture from one somebody
        "normalized" to GRCh37 by mistake — and it independently confirms that
        the GRCh37 values adopted for every other fixture are the ones the
        production hg18 → GRCh37 chain produces.
        """
        from backend.ingestion.liftover import lift_build36_to_grch37

        expected = _expected_grch37_mappings()
        for rsid, (chrom, pos) in BUILD36_APOE_COORDINATES.items():
            lifted = lift_build36_to_grch37(chrom, pos, "AA")
            assert lifted is not None, f"{rsid} build-36 coordinate failed to lift"
            assert lifted[:2] == expected[rsid][:2], (
                f"{rsid} build-36 {chrom}:{pos} lifts to {lifted[:2]}; "
                f"the GRCh37 oracle says {expected[rsid][:2]}"
            )


# ── Hand-maintained ClinVar VCF fixture ──────────────────────────────


class TestMiniClinVarVCFCoordinates:
    """``mini_clinvar.vcf`` must honour the build it declares (#476).

    The seed/oracle guard above only reaches the coordinate-bearing *seed
    CSVs*. ``mini_clinvar.vcf`` is hand-maintained and is the input for the
    ClinVar stream-load and position-lookup tests, so nothing checked its
    build: it carried the GRCh38 APOE pair 19:44908684 / 19:44908822 under its
    own ``##reference=GRCh37`` header for exactly as long as the seeds did.
    Pointing the same oracle at it closes that gap, and the scope grows on its
    own as the oracle grows.
    """

    def test_declares_grch37(self) -> None:
        reference, _records = _mini_clinvar_vcf()
        assert reference == "GRCh37", (
            f"mini_clinvar.vcf declares reference {reference!r}; the coordinate "
            "guard below is written against GRCh37"
        )

    def test_guarded_coordinates_are_grch37(self) -> None:
        expected = _expected_grch37_mappings()
        _reference, records = _mini_clinvar_vcf()
        guarded = [record for record in records if record[0] in expected]
        observed = {rsid for rsid, *_rest in guarded}
        assert {"rs429358", "rs7412"} <= observed, (
            "mini_clinvar.vcf no longer carries both APOE ε-defining SNPs; "
            "they are the rows this guard exists for"
        )
        for rsid, chrom, pos, ref, alt in guarded:
            exp_chrom, exp_start, _alleles = expected[rsid]
            assert chrom == exp_chrom, (
                f"mini_clinvar.vcf {rsid} is on chromosome {chrom}; expected {exp_chrom}"
            )
            if len(ref) == len(alt):
                allowed = {exp_start}
            else:
                # A VCF indel carries a left anchor base and therefore may start
                # one base before the Ensembl mapping interval the oracle
                # records. See tests/fixtures/seed_csvs/README.md.
                allowed = {exp_start, exp_start - 1}
            assert pos in allowed, (
                f"mini_clinvar.vcf {rsid} is at {chrom}:{pos}; expected "
                f"{exp_chrom}:{'/'.join(str(value) for value in sorted(allowed))}"
            )


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
        assert count == 6, f"Expected the 6 reviewed synthetic dbNSFP scenarios, got {count}"

    @pytest.mark.parametrize(("csv_name", "db_name", "table_name"), COORDINATE_SEED_DB_TARGETS)
    def test_guarded_mini_db_coordinates_match_seed(
        self,
        tmp_path: Path,
        csv_name: str,
        db_name: str,
        table_name: str,
    ) -> None:
        expected = _expected_grch37_mappings()
        seed_rows = _guarded_seed_coordinates(csv_name, expected)
        _assert_grch37_coordinates(seed_rows, expected, csv_name)
        _run_script(tmp_path)

        for db_path in (tmp_path / db_name, FIXTURES_DIR / db_name):
            source = f"{db_path}:{table_name}"
            db_rows = _guarded_db_coordinates(db_path, table_name, expected)
            _assert_grch37_coordinates(db_rows, expected, source)
            assert Counter(db_rows) == Counter(seed_rows), (
                f"{source} does not preserve every guarded {csv_name} occurrence"
            )

    def test_checked_in_mini_dbs_match_fresh_regeneration(self, tmp_path: Path) -> None:
        """Checked-in databases must be the exact semantic output of the seeds."""
        _run_script(tmp_path)

        for db_name in MINI_DB_NAMES:
            regenerated_dump = _semantic_sqlite_dump(tmp_path / db_name)
            checked_in_dump = _semantic_sqlite_dump(FIXTURES_DIR / db_name)
            assert checked_in_dump == regenerated_dump, (
                f"{db_name} differs from a fresh regeneration; inspect the SQL dump "
                "delta and regenerate the checked-in fixture"
            )

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
                "SELECT chrom, pos, significance, gene_symbol, accession, variation_id"
                " FROM clinvar_variants WHERE rsid = 'rs429358'"
            ).fetchone()
        assert row is not None, "rs429358 not found in clinvar_variants"
        assert row[0] == "19"
        assert row[1] == 45411941
        assert row[2] == "Conflicting classifications of pathogenicity|other|risk factor"
        assert row[3] == "APOE"
        assert row[4:] == ("VCV000017864", 17_864)

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

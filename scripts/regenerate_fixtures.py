#!/usr/bin/env python3
"""Rebuild test SQLite fixtures from seed CSVs.

Reads seed CSV files from tests/fixtures/seed_csvs/ and builds mini SQLite
test databases in tests/fixtures/.  No external dependencies — uses only the
Python standard library (csv, sqlite3, pathlib).

Usage:
    python scripts/regenerate_fixtures.py
    python scripts/regenerate_fixtures.py --dry-run
    python scripts/regenerate_fixtures.py --output-dir /tmp/fixtures
    python scripts/regenerate_fixtures.py --seed-dir /path/to/csvs
    python scripts/regenerate_fixtures.py --vendor=ancestrydna

The ``--vendor=ancestrydna`` mode (Plan §16.1, ADNA-09a / step 41) emits a
full-size synthetic AncestryDNA-format file
(``tests/fixtures/synthetic_eur_ancestrydna.txt``) derived from the existing
1000 Genomes EUR template at ``tests/fixtures/synthetic_eur_23andme.txt``.
The real AncestryDNA export is **never** an input — output must never carry
real user genotypes.
"""

from __future__ import annotations

import argparse
import csv
import sqlite3
import sys
from pathlib import Path

# ── Table schemas ────────────────────────────────────────────────────────────
#
# Each schema is a dict mapping table_name -> list of (column_name, column_type)
# tuples.  column_type is a SQLite type affinity string.
#
# The "autoincrement id" columns are listed but are allowed to be absent from
# the CSV; SQLite will auto-assign them when the value is NULL.

# Tables that live inside mini_reference.db
REFERENCE_SEEDED_TABLES: dict[str, list[tuple[str, str]]] = {
    "clinvar_variants": [
        ("id", "INTEGER PRIMARY KEY AUTOINCREMENT"),
        ("rsid", "TEXT"),
        ("chrom", "TEXT NOT NULL"),
        ("pos", "INTEGER NOT NULL"),
        ("ref", "TEXT NOT NULL"),
        ("alt", "TEXT NOT NULL"),
        ("significance", "TEXT"),
        ("review_stars", "INTEGER"),
        ("accession", "TEXT"),
        ("conditions", "TEXT"),
        ("gene_symbol", "TEXT"),
        ("variation_id", "INTEGER"),
    ],
    "gene_phenotype": [
        ("id", "INTEGER PRIMARY KEY AUTOINCREMENT"),
        ("gene_symbol", "TEXT NOT NULL"),
        ("disease_name", "TEXT NOT NULL"),
        ("disease_id", "TEXT"),
        ("hpo_terms", "TEXT"),  # JSON array stored as TEXT
        ("source", "TEXT NOT NULL"),
        ("inheritance", "TEXT"),
    ],
    "cpic_alleles": [
        ("id", "INTEGER PRIMARY KEY AUTOINCREMENT"),
        ("gene", "TEXT NOT NULL"),
        ("allele_name", "TEXT NOT NULL"),
        ("defining_variants", "TEXT"),  # JSON array stored as TEXT
        ("function", "TEXT"),
        ("activity_score", "REAL"),
    ],
    "cpic_diplotypes": [
        ("id", "INTEGER PRIMARY KEY AUTOINCREMENT"),
        ("gene", "TEXT NOT NULL"),
        ("diplotype", "TEXT NOT NULL"),
        ("phenotype", "TEXT NOT NULL"),
        ("ehr_notation", "TEXT"),
        ("activity_score", "REAL"),
    ],
    "cpic_guidelines": [
        ("id", "INTEGER PRIMARY KEY AUTOINCREMENT"),
        ("gene", "TEXT NOT NULL"),
        ("drug", "TEXT NOT NULL"),
        ("phenotype", "TEXT NOT NULL"),
        ("recommendation", "TEXT"),
        ("classification", "TEXT"),
        ("guideline_url", "TEXT"),
    ],
    "gwas_associations": [
        ("id", "INTEGER PRIMARY KEY AUTOINCREMENT"),
        ("rsid", "TEXT NOT NULL"),
        ("chrom", "TEXT"),
        ("pos", "INTEGER"),
        ("trait", "TEXT NOT NULL"),
        ("p_value", "REAL"),
        ("odds_ratio", "REAL"),
        ("beta", "REAL"),
        ("risk_allele", "TEXT"),
        ("pubmed_id", "TEXT"),
        ("study", "TEXT"),
        ("sample_size", "INTEGER"),
    ],
}

# Empty tables that also belong in mini_reference.db (schema only, no seed data)
REFERENCE_EMPTY_TABLES: dict[str, list[tuple[str, str]]] = {
    "samples": [
        ("id", "INTEGER PRIMARY KEY AUTOINCREMENT"),
        ("name", "TEXT NOT NULL"),
        ("db_path", "TEXT NOT NULL UNIQUE"),
        ("file_format", "TEXT"),
        ("file_hash", "TEXT"),
        ("created_at", "DATETIME DEFAULT (datetime('now'))"),
        ("updated_at", "DATETIME"),
    ],
    "jobs": [
        ("job_id", "TEXT PRIMARY KEY"),
        ("sample_id", "INTEGER"),
        ("job_type", "TEXT NOT NULL"),
        ("status", "TEXT NOT NULL DEFAULT 'pending'"),
        ("progress_pct", "REAL DEFAULT 0"),
        ("message", "TEXT DEFAULT ''"),
        ("error", "TEXT"),
        ("created_at", "DATETIME DEFAULT (datetime('now'))"),
        ("updated_at", "DATETIME DEFAULT (datetime('now'))"),
    ],
    "database_versions": [
        ("db_name", "TEXT PRIMARY KEY"),
        ("version", "TEXT NOT NULL"),
        ("file_path", "TEXT"),
        ("file_size_bytes", "INTEGER"),
        ("downloaded_at", "DATETIME"),
        ("checksum_sha256", "TEXT"),
    ],
    "update_history": [
        ("id", "INTEGER PRIMARY KEY AUTOINCREMENT"),
        ("db_name", "TEXT NOT NULL"),
        ("previous_version", "TEXT"),
        ("new_version", "TEXT NOT NULL"),
        ("updated_at", "DATETIME DEFAULT (datetime('now'))"),
        ("variants_added", "INTEGER DEFAULT 0"),
        ("variants_reclassified", "INTEGER DEFAULT 0"),
        ("download_size_bytes", "INTEGER"),
        ("duration_seconds", "INTEGER"),
    ],
    "downloads": [
        ("id", "INTEGER PRIMARY KEY AUTOINCREMENT"),
        ("url", "TEXT NOT NULL"),
        ("dest_path", "TEXT NOT NULL"),
        ("total_bytes", "INTEGER"),
        ("downloaded_bytes", "INTEGER DEFAULT 0"),
        ("checksum_sha256", "TEXT"),
        ("status", "TEXT DEFAULT 'pending'"),
        ("created_at", "DATETIME DEFAULT (datetime('now'))"),
        ("updated_at", "DATETIME"),
    ],
    "literature_cache": [
        ("id", "INTEGER PRIMARY KEY AUTOINCREMENT"),
        ("pmid", "TEXT NOT NULL"),
        ("gene", "TEXT"),
        ("query", "TEXT"),
        ("title", "TEXT"),
        ("abstract", "TEXT"),
        ("authors", "TEXT"),  # JSON array
        ("journal", "TEXT"),
        ("year", "INTEGER"),
        ("fetched_at", "DATETIME DEFAULT (datetime('now'))"),
    ],
    "uniprot_cache": [
        ("accession", "TEXT PRIMARY KEY"),
        ("gene_symbol", "TEXT"),
        ("domains", "TEXT"),  # JSON array
        ("features", "TEXT"),  # JSON array
        ("sequence_length", "INTEGER"),
        ("fetched_at", "DATETIME DEFAULT (datetime('now'))"),
        ("ttl_days", "INTEGER DEFAULT 30"),
    ],
    "log_entries": [
        ("id", "INTEGER PRIMARY KEY AUTOINCREMENT"),
        ("timestamp", "DATETIME DEFAULT (datetime('now'))"),
        ("level", "TEXT NOT NULL"),
        ("logger", "TEXT"),
        ("message", "TEXT"),
        ("event_data", "TEXT"),  # JSON
    ],
    "reannotation_prompts": [
        ("id", "INTEGER PRIMARY KEY AUTOINCREMENT"),
        ("sample_id", "INTEGER NOT NULL"),
        ("db_name", "TEXT NOT NULL"),
        ("db_version", "TEXT NOT NULL"),
        ("candidate_count", "INTEGER DEFAULT 0"),
        ("dismissed", "BOOLEAN DEFAULT 0"),
        ("created_at", "DATETIME DEFAULT (datetime('now'))"),
    ],
}

# Indexes for mini_reference.db (covering both seeded and empty tables)
REFERENCE_INDEXES: list[str] = [
    "CREATE INDEX IF NOT EXISTS idx_clinvar_rsid ON clinvar_variants(rsid)",
    "CREATE INDEX IF NOT EXISTS idx_clinvar_chrom_pos ON clinvar_variants(chrom, pos)",
    "CREATE INDEX IF NOT EXISTS idx_gene_phenotype_gene ON gene_phenotype(gene_symbol)",
    "CREATE INDEX IF NOT EXISTS idx_cpic_alleles_gene ON cpic_alleles(gene)",
    "CREATE INDEX IF NOT EXISTS idx_cpic_diplotypes_gene ON cpic_diplotypes(gene)",
    "CREATE INDEX IF NOT EXISTS idx_cpic_guidelines_gene_drug ON cpic_guidelines(gene, drug)",
    "CREATE INDEX IF NOT EXISTS idx_gwas_rsid ON gwas_associations(rsid)",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_literature_gene_pmid ON literature_cache(gene, pmid)",
    "CREATE INDEX IF NOT EXISTS idx_uniprot_gene ON uniprot_cache(gene_symbol)",
    "CREATE INDEX IF NOT EXISTS idx_log_timestamp ON log_entries(timestamp)",
    "CREATE INDEX IF NOT EXISTS idx_reannotation_sample ON reannotation_prompts(sample_id)",
]

# Standalone DB schemas
VEP_SCHEMA: list[tuple[str, str]] = [
    ("rsid", "TEXT"),
    ("chrom", "TEXT"),
    ("pos", "INTEGER"),
    ("ref", "TEXT"),
    ("alt", "TEXT"),
    ("gene_symbol", "TEXT"),
    ("transcript_id", "TEXT"),
    ("consequence", "TEXT"),
    ("hgvs_coding", "TEXT"),
    ("hgvs_protein", "TEXT"),
    ("strand", "TEXT"),
    ("exon_number", "INTEGER"),
    ("intron_number", "INTEGER"),
    ("mane_select", "INTEGER"),
]

VEP_INDEXES: list[str] = [
    "CREATE INDEX IF NOT EXISTS idx_vep_rsid ON vep_annotations(rsid)",
    "CREATE INDEX IF NOT EXISTS idx_vep_chrom_pos ON vep_annotations(chrom, pos)",
]

# `bundle_metadata` mirrors the production v2.0.0 VEP bundle schema written by
# `scripts/build_vep_bundle.py`. The mini fixture must carry the same keys so
# Phase 0 readers (e.g. `update_manager.run_vep_bundle_update`'s parity check
# against `bundle_metadata.bundle_version`) work identically against the
# fixture.
MINI_VEP_BUNDLE_METADATA: dict[str, str] = {
    "ensembl_version": "112",
    "build_date": "2026-05-18",
    "variant_count": "0",
    "schema_version": "1",
    "bundle_version": "v2.0.0",
}

GNOMAD_SCHEMA: list[tuple[str, str]] = [
    ("rsid", "TEXT"),
    ("chrom", "TEXT"),
    ("pos", "INTEGER"),
    ("ref", "TEXT"),
    ("alt", "TEXT"),
    ("af_global", "REAL"),
    ("af_afr", "REAL"),
    ("af_amr", "REAL"),
    ("af_asj", "REAL"),
    ("af_eas", "REAL"),
    ("af_eur", "REAL"),
    ("af_fin", "REAL"),
    ("af_sas", "REAL"),
    ("homozygous_count", "INTEGER"),
]

GNOMAD_INDEXES: list[str] = [
    "CREATE INDEX IF NOT EXISTS idx_gnomad_rsid ON gnomad_af(rsid)",
    "CREATE INDEX IF NOT EXISTS idx_gnomad_chrom_pos ON gnomad_af(chrom, pos)",
]

DBNSFP_SCHEMA: list[tuple[str, str]] = [
    ("rsid", "TEXT"),
    ("chrom", "TEXT"),
    ("pos", "INTEGER"),
    ("ref", "TEXT"),
    ("alt", "TEXT"),
    ("cadd_phred", "REAL"),
    ("sift_score", "REAL"),
    ("sift_pred", "TEXT"),
    ("polyphen2_hsvar_score", "REAL"),
    ("polyphen2_hsvar_pred", "TEXT"),
    ("revel", "REAL"),
    ("mutpred2", "REAL"),
    ("vest4", "REAL"),
    ("metasvm", "REAL"),
    ("metalr", "REAL"),
    ("gerp_rs", "REAL"),
    ("phylop", "REAL"),
    ("mpc", "REAL"),
    ("primateai", "REAL"),
]

DBNSFP_INDEXES: list[str] = [
    "CREATE INDEX IF NOT EXISTS idx_dbnsfp_rsid ON dbnsfp_scores(rsid)",
    "CREATE INDEX IF NOT EXISTS idx_dbnsfp_chrom_pos ON dbnsfp_scores(chrom, pos)",
]

# Mapping: seed CSV filename -> (target db filename, table name, schema, indexes)
# For reference DB tables, target db is "mini_reference.db".
SEED_CSV_MAP: dict[str, tuple[str, str, list[tuple[str, str]], list[str]]] = {
    "clinvar_seed.csv": (
        "mini_reference.db",
        "clinvar_variants",
        REFERENCE_SEEDED_TABLES["clinvar_variants"],
        [],
    ),
    "gene_phenotype_seed.csv": (
        "mini_reference.db",
        "gene_phenotype",
        REFERENCE_SEEDED_TABLES["gene_phenotype"],
        [],
    ),
    "cpic_alleles_seed.csv": (
        "mini_reference.db",
        "cpic_alleles",
        REFERENCE_SEEDED_TABLES["cpic_alleles"],
        [],
    ),
    "cpic_diplotypes_seed.csv": (
        "mini_reference.db",
        "cpic_diplotypes",
        REFERENCE_SEEDED_TABLES["cpic_diplotypes"],
        [],
    ),
    "cpic_guidelines_seed.csv": (
        "mini_reference.db",
        "cpic_guidelines",
        REFERENCE_SEEDED_TABLES["cpic_guidelines"],
        [],
    ),
    "gwas_seed.csv": (
        "mini_reference.db",
        "gwas_associations",
        REFERENCE_SEEDED_TABLES["gwas_associations"],
        [],
    ),
    "vep_seed.csv": (
        "mini_vep_bundle.db",
        "vep_annotations",
        VEP_SCHEMA,
        VEP_INDEXES,
    ),
    "gnomad_seed.csv": (
        "mini_gnomad_af.db",
        "gnomad_af",
        GNOMAD_SCHEMA,
        GNOMAD_INDEXES,
    ),
    "dbnsfp_seed.csv": (
        "mini_dbnsfp.db",
        "dbnsfp_scores",
        DBNSFP_SCHEMA,
        DBNSFP_INDEXES,
    ),
}

# Integer and real column types for casting CSV strings
_INTEGER_TYPES = {"INTEGER", "INTEGER PRIMARY KEY AUTOINCREMENT"}
_REAL_TYPES = {"REAL"}


def _base_type(col_type: str) -> str:
    """Return the bare SQLite type affinity from a full column definition."""
    # e.g. "INTEGER PRIMARY KEY AUTOINCREMENT" -> "INTEGER"
    return col_type.split()[0]


def _cast_value(value: str, col_type: str) -> object:
    """Convert a CSV string to the appropriate Python type for sqlite3.

    Empty strings become None (SQL NULL).  JSON columns are passed through
    as-is (TEXT).
    """
    if value == "":
        return None

    base = _base_type(col_type)
    if base == "INTEGER":
        return int(value)
    if base == "REAL":
        return float(value)
    if base == "BOOLEAN":
        low = value.lower()
        if low in ("0", "false", "no"):
            return 0
        if low in ("1", "true", "yes"):
            return 1
        return int(value)
    # TEXT, DATETIME, etc. — pass through
    return value


def _create_table_sql(table_name: str, schema: list[tuple[str, str]]) -> str:
    """Build a CREATE TABLE IF NOT EXISTS statement."""
    cols = ", ".join(f"{name} {typ}" for name, typ in schema)
    return f"CREATE TABLE IF NOT EXISTS {table_name} ({cols})"


def _insert_sql(table_name: str, columns: list[str]) -> str:
    """Build an INSERT statement with named placeholders."""
    placeholders = ", ".join(f":{c}" for c in columns)
    col_names = ", ".join(columns)
    return f"INSERT INTO {table_name} ({col_names}) VALUES ({placeholders})"


def _column_names(schema: list[tuple[str, str]]) -> list[str]:
    return [name for name, _ in schema]


def _column_type_map(schema: list[tuple[str, str]]) -> dict[str, str]:
    return {name: typ for name, typ in schema}


def _load_csv(csv_path: Path, schema: list[tuple[str, str]]) -> list[dict]:
    """Read a seed CSV and return a list of row dicts with proper types.

    Only columns present in both the CSV header and the schema are loaded.
    The autoincrement 'id' column is omitted so SQLite assigns it.
    """
    type_map = _column_type_map(schema)
    schema_columns = set(_column_names(schema))

    rows: list[dict] = []
    with csv_path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        if reader.fieldnames is None:
            raise ValueError(f"No header in {csv_path}")
        csv_columns = [c for c in reader.fieldnames if c in schema_columns]

        for raw_row in reader:
            row: dict = {}
            for col in csv_columns:
                # Skip autoincrement id — let SQLite handle it
                if col == "id" and "AUTOINCREMENT" in type_map.get(col, ""):
                    continue
                row[col] = _cast_value(raw_row[col], type_map[col])
            rows.append(row)

    return rows


def _human_size(nbytes: int) -> str:
    for unit in ("B", "KB", "MB"):
        if nbytes < 1024:
            return f"{nbytes:.1f} {unit}"
        nbytes /= 1024  # type: ignore[assignment]
    return f"{nbytes:.1f} GB"


def build_reference_db(
    seed_dir: Path,
    output_dir: Path,
    *,
    dry_run: bool,
) -> list[str]:
    """Build mini_reference.db with all reference tables.

    Returns a list of summary lines.
    """
    db_path = output_dir / "mini_reference.db"
    summary: list[str] = []

    if dry_run:
        summary.append(f"[dry-run] Would create {db_path}")

    # Collect rows from available seed CSVs
    table_rows: dict[str, tuple[list[tuple[str, str]], list[dict]]] = {}
    for csv_name, (db_name, table_name, schema, _) in SEED_CSV_MAP.items():
        if db_name != "mini_reference.db":
            continue
        csv_path = seed_dir / csv_name
        if csv_path.exists():
            rows = _load_csv(csv_path, schema)
            table_rows[table_name] = (schema, rows)
            if dry_run:
                summary.append(f"  [dry-run] {table_name}: {len(rows)} rows from {csv_name}")
        else:
            if dry_run:
                summary.append(f"  [dry-run] {table_name}: SKIP (no {csv_name})")

    if dry_run:
        for table_name in REFERENCE_EMPTY_TABLES:
            summary.append(f"  [dry-run] {table_name}: 0 rows (schema only)")
        return summary

    # Remove old DB if present
    if db_path.exists():
        db_path.unlink()

    with sqlite3.connect(str(db_path)) as conn:
        # Enable WAL mode
        conn.execute("PRAGMA journal_mode=WAL")

        # Create seeded tables and insert data
        for table_name, schema in REFERENCE_SEEDED_TABLES.items():
            conn.execute(_create_table_sql(table_name, schema))
            if table_name in table_rows:
                _, rows = table_rows[table_name]
                if rows:
                    columns = list(rows[0].keys())
                    insert = _insert_sql(table_name, columns)
                    conn.executemany(insert, rows)
                    summary.append(f"  {table_name}: {len(rows)} rows")
                else:
                    summary.append(f"  {table_name}: 0 rows")
            else:
                summary.append(f"  {table_name}: 0 rows (no seed CSV found)")

        # Create empty tables
        for table_name, schema in REFERENCE_EMPTY_TABLES.items():
            conn.execute(_create_table_sql(table_name, schema))
            summary.append(f"  {table_name}: 0 rows (schema only)")

        # Create indexes
        for idx_sql in REFERENCE_INDEXES:
            conn.execute(idx_sql)

    size = db_path.stat().st_size
    summary.insert(0, f"Created {db_path} ({_human_size(size)})")
    return summary


def build_standalone_db(
    csv_name: str,
    db_name: str,
    table_name: str,
    schema: list[tuple[str, str]],
    indexes: list[str],
    seed_dir: Path,
    output_dir: Path,
    *,
    dry_run: bool,
) -> list[str]:
    """Build a standalone single-table DB (VEP, gnomAD, dbNSFP).

    Returns a list of summary lines.
    """
    csv_path = seed_dir / csv_name
    db_path = output_dir / db_name
    summary: list[str] = []

    if not csv_path.exists():
        summary.append(f"SKIP {db_path} (no {csv_path.name})")
        return summary

    rows = _load_csv(csv_path, schema)

    if dry_run:
        summary.append(f"[dry-run] Would create {db_path}: {table_name} with {len(rows)} rows")
        return summary

    # Remove old DB if present
    if db_path.exists():
        db_path.unlink()

    with sqlite3.connect(str(db_path)) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(_create_table_sql(table_name, schema))

        if rows:
            columns = list(rows[0].keys())
            insert = _insert_sql(table_name, columns)
            conn.executemany(insert, rows)

        for idx_sql in indexes:
            conn.execute(idx_sql)

        # Mirror the production VEP bundle's `bundle_metadata` so the mini
        # fixture aligns with Phase 0's v2.0.0 schema (see Plan §5.2, §5.5,
        # §12.1). Other standalone DBs do not carry this table.
        if db_name == "mini_vep_bundle.db":
            conn.execute(
                "CREATE TABLE IF NOT EXISTS bundle_metadata (key TEXT PRIMARY KEY, value TEXT)"
            )
            metadata = dict(MINI_VEP_BUNDLE_METADATA)
            metadata["variant_count"] = str(len(rows))
            for key, value in metadata.items():
                conn.execute(
                    "INSERT OR REPLACE INTO bundle_metadata (key, value) VALUES (?, ?)",
                    (key, value),
                )

    size = db_path.stat().st_size
    summary.append(f"Created {db_path} ({_human_size(size)}): {table_name} with {len(rows)} rows")
    return summary


# ── Synthetic vendor-file regeneration (Plan §16.1, step 41) ────────────────
#
# The ``--vendor=ancestrydna`` mode is a *separate* code path from the SQLite
# fixture rebuild above. It reads the existing 1000 Genomes EUR template
# (``tests/fixtures/synthetic_eur_23andme.txt`` — itself a hand-curated
# synthetic file) and emits the same rsid/chrom/pos rows in AncestryDNA's
# 5-column TSV shape. The real AncestryDNA export is **never** an input.

_ANCESTRYDNA_HEADER = (
    "#AncestryDNA raw data download\n"
    "#SYNTHETIC FIXTURE — NOT REAL USER DATA\n"
    "#Generated by scripts/regenerate_fixtures.py --vendor=ancestrydna\n"
    "#Derived from the 1000 Genomes EUR template at\n"
    "#tests/fixtures/synthetic_eur_23andme.txt.\n"
    "#Must never contain real user genotypes.\n"
    "#AncestryDNA array version: V2.0\n"
    "#This raw data file may be used for personal or research purposes.\n"
    "#Below is a text version of your data. Fields are TAB-separated.\n"
    "rsid\tchromosome\tposition\tallele1\tallele2\n"
)

# 23andMe genotype tokens that map to an AncestryDNA no-call (allele1 == "0",
# allele2 == "0"). Per Plan §8.6 #1–2 the AncestryDNA parser collapses these
# back to the canonical ``"--"`` sentinel on the way in.
_NO_CALL_TOKENS: frozenset[str] = frozenset({"--", "??", "-", "00", "0"})


def _split_23andme_genotype(token: str) -> tuple[str, str]:
    """Split a 23andMe genotype token into AncestryDNA (allele1, allele2).

    The template carries only 2-character ACGT genotypes today, but be
    defensive: no-call tokens collapse to ``("0", "0")``; single-character
    haploid calls duplicate; 2-character ``I``/``D`` indel codes pass through
    element-wise.
    """
    if token in _NO_CALL_TOKENS:
        return ("0", "0")
    if len(token) == 1:
        return (token, token)
    if len(token) == 2:
        return (token[0], token[1])
    raise ValueError(f"Unsupported 23andMe genotype token: {token!r}")


def emit_ancestrydna_fixture(
    template_path: Path,
    output_path: Path,
    *,
    dry_run: bool,
) -> list[str]:
    """Convert a 23andMe-format template to AncestryDNA 5-column TSV.

    Returns a list of summary lines.
    """
    summary: list[str] = []

    if not template_path.is_file():
        summary.append(f"Error: template file not found: {template_path}")
        return summary

    if dry_run:
        summary.append(f"[dry-run] Would write {output_path} from template {template_path}")
        return summary

    output_path.parent.mkdir(parents=True, exist_ok=True)

    variant_rows = 0
    with (
        template_path.open(encoding="utf-8") as fh_in,
        output_path.open("w", encoding="utf-8", newline="\n") as fh_out,
    ):
        fh_out.write(_ANCESTRYDNA_HEADER)
        for raw in fh_in:
            line = raw.rstrip("\r\n")
            if not line or line.startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) != 4:
                continue
            rsid, chrom, pos, genotype = (p.strip() for p in parts)
            # Drop the 23andMe header row if present mid-file.
            if rsid.lower() == "rsid":
                continue
            a1, a2 = _split_23andme_genotype(genotype)
            fh_out.write(f"{rsid}\t{chrom}\t{pos}\t{a1}\t{a2}\n")
            variant_rows += 1

    size = output_path.stat().st_size
    summary.append(f"Created {output_path} ({_human_size(size)}): {variant_rows} variants")
    return summary


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Rebuild test SQLite fixtures from seed CSVs.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("tests/fixtures"),
        help="Directory to write generated .db files (default: tests/fixtures/)",
    )
    parser.add_argument(
        "--seed-dir",
        type=Path,
        default=Path("tests/fixtures/seed_csvs"),
        help="Directory containing seed CSV files (default: tests/fixtures/seed_csvs/)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be generated without writing any files.",
    )
    parser.add_argument(
        "--vendor",
        choices=("ancestrydna",),
        default=None,
        help=(
            "Emit a synthetic vendor-format fixture instead of rebuilding the "
            "SQLite databases. 'ancestrydna' converts the 1000G EUR template "
            "into AncestryDNA 5-column TSV at "
            "tests/fixtures/synthetic_eur_ancestrydna.txt."
        ),
    )
    parser.add_argument(
        "--template",
        type=Path,
        default=Path("tests/fixtures/synthetic_eur_23andme.txt"),
        help=(
            "1000G EUR template input for --vendor modes "
            "(default: tests/fixtures/synthetic_eur_23andme.txt)"
        ),
    )
    args = parser.parse_args(argv)

    output_dir: Path = args.output_dir
    seed_dir: Path = args.seed_dir
    dry_run: bool = args.dry_run

    output_dir.mkdir(parents=True, exist_ok=True)

    if args.vendor == "ancestrydna":
        print("=" * 60)
        print("Yeliztli synthetic AncestryDNA fixture regeneration")
        print(f"  Template:   {args.template.resolve()}")
        print(f"  Output dir: {output_dir.resolve()}")
        if dry_run:
            print("  Mode:       DRY RUN")
        print("=" * 60)
        print()

        output_path = output_dir / "synthetic_eur_ancestrydna.txt"
        for line in emit_ancestrydna_fixture(args.template, output_path, dry_run=dry_run):
            print(line)
        print()
        print("Done.")
        return

    if not seed_dir.is_dir():
        print(f"Error: seed directory does not exist: {seed_dir}", file=sys.stderr)
        sys.exit(1)

    print("=" * 60)
    print("Yeliztli fixture regeneration")
    print(f"  Seed dir:   {seed_dir.resolve()}")
    print(f"  Output dir: {output_dir.resolve()}")
    if dry_run:
        print("  Mode:       DRY RUN")
    print("=" * 60)
    print()

    # 1. Build mini_reference.db
    print("--- mini_reference.db ---")
    for line in build_reference_db(seed_dir, output_dir, dry_run=dry_run):
        print(line)
    print()

    # 2. Build standalone DBs (VEP, gnomAD, dbNSFP)
    for csv_name, (db_name, table_name, schema, indexes) in SEED_CSV_MAP.items():
        if db_name == "mini_reference.db":
            continue
        print(f"--- {db_name} ---")
        for line in build_standalone_db(
            csv_name,
            db_name,
            table_name,
            schema,
            indexes,
            seed_dir,
            output_dir,
            dry_run=dry_run,
        ):
            print(line)
        print()

    print("Done.")


if __name__ == "__main__":
    main()

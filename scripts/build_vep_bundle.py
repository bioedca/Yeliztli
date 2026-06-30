#!/usr/bin/env python3
"""Build the VEP annotation bundle from Ensembl VEP output.

Reads VEP output (VCF format) produced by running Ensembl VEP against the
full 23andMe v5 rsid catalog (~600k SNPs) and stores results in an indexed
SQLite database (``vep_bundle.db``, ~500 MB).

The resulting bundle is a single-table SQLite file used at annotation time
for fast batch lookups — no live VEP required.

Usage::

    # From VEP VCF output (union 23andMe v5 ∪ AncestryDNA v2.0 catalog):
    python scripts/build_vep_bundle.py --vep-vcf vep_output.vcf.gz \\
        --output vep_bundle.db --ensembl-version 112 --bundle-version v2.0.0

    # From seed CSV (testing / development):
    python scripts/build_vep_bundle.py --seed-csv tests/fixtures/seed_csvs/vep_seed.csv \\
        --output vep_bundle.db --ensembl-version 112

    # Dry run:
    python scripts/build_vep_bundle.py --vep-vcf vep_output.vcf.gz --dry-run

Any user with VEP installed can regenerate the bundle against any Ensembl
release.  Pre-built bundles are hosted on GitHub Releases.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import re
import sqlite3
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

# ── Schema ───────────────────────────────────────────────────────────────
# Must match regenerate_fixtures.py VEP_SCHEMA and tables.py annotated_variants
# VEP columns.

TABLE_NAME = "vep_annotations"

CREATE_TABLE_SQL = """\
CREATE TABLE IF NOT EXISTS vep_annotations (
    rsid            TEXT,
    chrom           TEXT,
    pos             INTEGER,
    ref             TEXT,
    alt             TEXT,
    gene_symbol     TEXT,
    transcript_id   TEXT,
    consequence     TEXT,
    hgvs_coding     TEXT,
    hgvs_protein    TEXT,
    strand          TEXT,
    exon_number     INTEGER,
    intron_number   INTEGER,
    mane_select     INTEGER
)
"""

INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_vep_rsid ON vep_annotations(rsid)",
    "CREATE INDEX IF NOT EXISTS idx_vep_chrom_pos ON vep_annotations(chrom, pos)",
]

# Bundle metadata table (tracks Ensembl version)
CREATE_META_SQL = """\
CREATE TABLE IF NOT EXISTS bundle_metadata (
    key   TEXT PRIMARY KEY,
    value TEXT
)
"""

# Column names for INSERT
COLUMNS = [
    "rsid",
    "chrom",
    "pos",
    "ref",
    "alt",
    "gene_symbol",
    "transcript_id",
    "consequence",
    "hgvs_coding",
    "hgvs_protein",
    "strand",
    "exon_number",
    "intron_number",
    "mane_select",
]

INSERT_SQL = (
    f"INSERT INTO {TABLE_NAME} ({', '.join(COLUMNS)}) "
    f"VALUES ({', '.join(':' + c for c in COLUMNS)})"
)

# Batch size for bulk inserts
BATCH_SIZE = 10_000

# Chromosomes we accept (matching 23andMe scope)
VALID_CHROMS = {str(i) for i in range(1, 23)} | {"X", "Y", "MT"}

# ── Consequence severity ranking (Ensembl SO terms) ──────────────────────
# Higher index = more severe.  Used by most-severe consequence selector
# (P2-03) but defined here so the build script can pick the most-severe
# consequence per variant when multiple transcripts are annotated.

CONSEQUENCE_SEVERITY: dict[str, int] = {
    "transcript_ablation": 35,
    "splice_acceptor_variant": 34,
    "splice_donor_variant": 33,
    "stop_gained": 32,
    "frameshift_variant": 31,
    "stop_lost": 30,
    "start_lost": 29,
    "transcript_amplification": 28,
    "feature_elongation": 27,
    "feature_truncation": 26,
    "inframe_insertion": 25,
    "inframe_deletion": 24,
    "missense_variant": 23,
    "protein_altering_variant": 22,
    "splice_donor_5th_base_variant": 21,
    "splice_region_variant": 20,
    "splice_donor_region_variant": 19,
    "splice_polypyrimidine_tract_variant": 18,
    "incomplete_terminal_codon_variant": 17,
    "start_retained_variant": 16,
    "stop_retained_variant": 15,
    "synonymous_variant": 14,
    "coding_sequence_variant": 13,
    "mature_miRNA_variant": 12,
    "5_prime_UTR_variant": 11,
    "3_prime_UTR_variant": 10,
    "non_coding_transcript_exon_variant": 9,
    "intron_variant": 8,
    "NMD_transcript_variant": 7,
    "non_coding_transcript_variant": 6,
    "upstream_gene_variant": 5,
    "downstream_gene_variant": 4,
    "TFBS_ablation": 3,
    "TFBS_amplification": 2,
    "TF_binding_site_variant": 1,
    "regulatory_region_ablation": 1,
    "regulatory_region_amplification": 1,
    "regulatory_region_variant": 1,
    "intergenic_variant": 0,
}


def consequence_severity(consequence: str) -> int:
    """Return the severity score for a consequence SO term.

    If the consequence contains multiple terms (``&``-separated as in VEP
    VCF output), returns the maximum severity among them.
    """
    terms = consequence.split("&")
    return max(CONSEQUENCE_SEVERITY.get(t, 0) for t in terms)


# ── Data classes ─────────────────────────────────────────────────────────


@dataclass
class VEPRecord:
    """A single parsed VEP annotation record."""

    rsid: str
    chrom: str
    pos: int
    ref: str
    alt: str
    gene_symbol: str | None = None
    transcript_id: str | None = None
    consequence: str | None = None
    hgvs_coding: str | None = None
    hgvs_protein: str | None = None
    strand: str | None = None
    exon_number: int | None = None
    intron_number: int | None = None
    mane_select: int = 0

    def to_dict(self) -> dict:
        return {
            "rsid": self.rsid,
            "chrom": self.chrom,
            "pos": self.pos,
            "ref": self.ref,
            "alt": self.alt,
            "gene_symbol": self.gene_symbol,
            "transcript_id": self.transcript_id,
            "consequence": self.consequence,
            "hgvs_coding": self.hgvs_coding,
            "hgvs_protein": self.hgvs_protein,
            "strand": self.strand,
            "exon_number": self.exon_number,
            "intron_number": self.intron_number,
            "mane_select": self.mane_select,
        }


@dataclass
class BuildStats:
    """Statistics from a VEP bundle build."""

    total_input_lines: int = 0
    total_csq_entries: int = 0
    variants_stored: int = 0
    skipped_no_rsid: int = 0
    skipped_invalid_chrom: int = 0
    skipped_malformed: int = 0
    unique_genes: set[str] = field(default_factory=set)
    consequence_counts: dict[str, int] = field(default_factory=dict)
    mane_select_count: int = 0
    coverage_percent: float = 0.0
    elapsed_seconds: float = 0.0

    def summary(self) -> str:
        lines = [
            "VEP Bundle Build Summary",
            "=" * 50,
            f"  Input lines parsed:      {self.total_input_lines:,}",
            f"  CSQ entries processed:    {self.total_csq_entries:,}",
            f"  Variants stored:         {self.variants_stored:,}",
            f"  Unique genes:            {len(self.unique_genes):,}",
            f"  MANE Select transcripts: {self.mane_select_count:,}",
            f"  Skipped (no rsid):       {self.skipped_no_rsid:,}",
            f"  Skipped (invalid chrom): {self.skipped_invalid_chrom:,}",
            f"  Skipped (malformed):     {self.skipped_malformed:,}",
            f"  Elapsed:                 {self.elapsed_seconds:.1f}s",
        ]
        if self.consequence_counts:
            lines.append("")
            lines.append("  Top consequences:")
            sorted_csq = sorted(
                self.consequence_counts.items(),
                key=lambda x: x[1],
                reverse=True,
            )
            for csq, count in sorted_csq[:10]:
                lines.append(f"    {csq}: {count:,}")
        return "\n".join(lines)


# ── VEP VCF Parser ──────────────────────────────────────────────────────


def _normalize_chrom(chrom: str) -> str | None:
    """Normalize chromosome name. Returns None for invalid chromosomes."""
    c = chrom.removeprefix("chr").upper()
    if c in VALID_CHROMS:
        return c
    return None


def _parse_csq_header(header_line: str) -> list[str]:
    """Extract CSQ field names from the VEP VCF header line.

    VEP adds a header like:
    ``##INFO=<ID=CSQ,...,Description="...Format: Allele|Consequence|...">``.
    """
    match = re.search(r'Format:\s*([^"]+)', header_line)
    if not match:
        return []
    return match.group(1).strip().split("|")


def _parse_strand(strand_val: str | None) -> str | None:
    """Convert VEP strand encoding to +/- string."""
    if strand_val is None or strand_val == "":
        return None
    if strand_val in ("+", "-"):
        return strand_val
    if strand_val == "1":
        return "+"
    if strand_val == "-1":
        return "-"
    return strand_val


def _parse_int_or_none(val: str | None) -> int | None:
    """Parse an integer or return None for empty/non-numeric values."""
    if val is None or val == "" or val == ".":
        return None
    # VEP exon/intron format can be "4/10" — take the first number
    if "/" in val:
        val = val.split("/")[0]
    try:
        return int(val)
    except (ValueError, TypeError):
        return None


def _is_mane_select(csq_fields: dict[str, str]) -> bool:
    """Whether this transcript is the preferred (MANE Select / canonical) one.

    VEP marks MANE Select in the MANE_SELECT field, or via the FLAGS field
    containing "mane_select". But ``MANE_SELECT`` is GRCh38-only — the bundle is
    built from a GRCh37 VEP run, where it is never emitted, so without a fallback
    every transcript scores equal and ``_pick_best`` collapses to pure
    most-severe-consequence, surfacing a minor isoform's consequence/HGVS over the
    canonical transcript's (#1295). GRCh37 VEP **does** emit ``CANONICAL`` (=YES on
    the Ensembl canonical transcript), so fall back to it as the MANE-equivalent
    tiebreak. Requires the VEP run to include ``--canonical``.
    """
    mane = csq_fields.get("MANE_SELECT", "")
    if mane and mane != "" and mane != ".":
        return True
    # Also check MANE_PLUS_CLINICAL
    flags = csq_fields.get("FLAGS", "")
    if "mane_select" in flags.lower():
        return True
    # CANONICAL fallback: GRCh37 VEP omits MANE_SELECT but emits CANONICAL="YES"
    # for the Ensembl canonical transcript (#1295).
    canonical = csq_fields.get("CANONICAL", "")
    if canonical.strip().upper() in ("YES", "1"):
        return True
    return False


def parse_vep_vcf(
    vcf_path: Path,
    stats: BuildStats,
) -> list[dict]:
    """Parse a VEP-annotated VCF file and return rows for the bundle.

    For each variant, selects the most-severe consequence annotation.
    When multiple transcripts have the same consequence severity, prefers
    MANE Select transcripts.

    Args:
        vcf_path: Path to VEP output VCF (plain or gzipped).
        stats: BuildStats to update.

    Returns:
        List of dicts ready for database insertion.
    """
    csq_field_names: list[str] = []
    rows: list[dict] = []

    # Track best annotation per (rsid, alt) to pick most-severe
    best_by_variant: dict[tuple[str, str], VEPRecord] = {}
    best_severity: dict[tuple[str, str], int] = {}

    open_fn = gzip.open if vcf_path.suffix == ".gz" else open
    with open_fn(vcf_path, "rt", encoding="utf-8") as fh:  # type: ignore[call-overload]
        for line in fh:
            # Parse header for CSQ field names
            if line.startswith("##INFO=<ID=CSQ"):
                csq_field_names = _parse_csq_header(line)
                continue
            if line.startswith("#"):
                continue

            stats.total_input_lines += 1
            parts = line.rstrip("\n\r").split("\t")
            if len(parts) < 8:
                stats.skipped_malformed += 1
                continue

            chrom_raw, pos_str, var_id, ref, alt_field, _qual, _filt, info_str = parts[:8]

            # Normalize chromosome
            chrom = _normalize_chrom(chrom_raw)
            if chrom is None:
                stats.skipped_invalid_chrom += 1
                continue

            # Parse position
            try:
                pos = int(pos_str)
            except (ValueError, TypeError):
                stats.skipped_malformed += 1
                continue

            # Extract rsid
            rsid: str | None = None
            if var_id and var_id != "." and var_id.startswith("rs"):
                rsid = var_id
            else:
                # Try to find RS in INFO field
                for part in info_str.split(";"):
                    if part.startswith("RS="):
                        rsid = f"rs{part[3:]}"
                        break

            if rsid is None:
                stats.skipped_no_rsid += 1
                continue

            # Parse CSQ entries from INFO
            csq_str = ""
            for part in info_str.split(";"):
                if part.startswith("CSQ="):
                    csq_str = part[4:]
                    break

            if not csq_str or not csq_field_names:
                # No CSQ annotation — store with minimal data
                alt = alt_field.split(",")[0]
                key = (rsid, alt)
                if key not in best_by_variant:
                    best_by_variant[key] = VEPRecord(
                        rsid=rsid,
                        chrom=chrom,
                        pos=pos,
                        ref=ref,
                        alt=alt,
                    )
                    best_severity[key] = -1
                continue

            # Parse each CSQ entry (comma-separated, fields pipe-separated)
            alts = alt_field.split(",")
            for csq_entry in csq_str.split(","):
                stats.total_csq_entries += 1
                values = csq_entry.split("|")
                csq = dict(zip(csq_field_names, values, strict=False))

                # Determine which ALT allele this CSQ entry is for
                csq_allele = csq.get("Allele", "")
                alt = csq_allele if csq_allele else (alts[0] if alts else ref)

                consequence = csq.get("Consequence", "")
                severity = consequence_severity(consequence)

                gene = csq.get("SYMBOL", "") or csq.get("Gene", "")
                transcript = csq.get("Feature", "")
                hgvsc = csq.get("HGVSc", "")
                hgvsp = csq.get("HGVSp", "")
                strand = _parse_strand(csq.get("STRAND"))
                exon = _parse_int_or_none(csq.get("EXON"))
                intron = _parse_int_or_none(csq.get("INTRON"))
                mane = 1 if _is_mane_select(csq) else 0

                # Clean HGVS — strip transcript prefix (e.g., ENST...:c.123A>G → c.123A>G)
                if hgvsc and ":" in hgvsc:
                    hgvsc = hgvsc.split(":", 1)[1]
                if hgvsp and ":" in hgvsp:
                    hgvsp = hgvsp.split(":", 1)[1]

                # Replace URL-encoded % in HGVS protein (VEP uses %3D for =)
                if hgvsp:
                    hgvsp = hgvsp.replace("%3D", "=").replace("%3E", ">")

                key = (rsid, alt)
                prev = best_by_variant.get(key)

                # Prefer the MANE Select / canonical transcript over a more-severe
                # non-canonical isoform (#1295), then most-severe consequence, then
                # first-seen. The lexicographic (mane, severity) order mirrors the
                # runtime backend.annotation.vep_bundle._pick_best; the previous
                # severity-first order let a minor isoform's stop_gained beat the
                # canonical transcript's synonymous call.
                prev_score = (
                    (prev.mane_select, best_severity.get(key, -1))
                    if prev is not None
                    else (-1, -1)
                )
                is_better = (mane, severity) > prev_score

                if is_better:
                    record = VEPRecord(
                        rsid=rsid,
                        chrom=chrom,
                        pos=pos,
                        ref=ref,
                        alt=alt,
                        gene_symbol=gene or None,
                        transcript_id=transcript or None,
                        consequence=consequence or None,
                        hgvs_coding=hgvsc or None,
                        hgvs_protein=hgvsp or None,
                        strand=strand,
                        exon_number=exon,
                        intron_number=intron,
                        mane_select=mane,
                    )
                    best_by_variant[key] = record
                    best_severity[key] = severity

    # Convert best annotations to row dicts
    for record in best_by_variant.values():
        row = record.to_dict()
        rows.append(row)

        # Update stats
        if record.gene_symbol:
            stats.unique_genes.add(record.gene_symbol)
        if record.consequence:
            # Count the primary (first) consequence term
            primary = record.consequence.split("&")[0]
            stats.consequence_counts[primary] = stats.consequence_counts.get(primary, 0) + 1
        if record.mane_select:
            stats.mane_select_count += 1

    stats.variants_stored = len(rows)
    return rows


# ── CSV Loader (for seed data / testing) ─────────────────────────────────


def load_seed_csv(csv_path: Path, stats: BuildStats) -> list[dict]:
    """Load VEP annotations from a seed CSV file.

    Used for testing and development. The CSV format matches vep_seed.csv
    in tests/fixtures/seed_csvs/.
    """
    rows: list[dict] = []
    with csv_path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for raw_row in reader:
            row: dict = {}
            for col in COLUMNS:
                val = raw_row.get(col, "")
                if val == "":
                    row[col] = None
                elif col in ("pos", "exon_number", "intron_number", "mane_select"):
                    try:
                        row[col] = int(val)
                    except (ValueError, TypeError):
                        row[col] = None
                else:
                    row[col] = val

            # Update stats from parsed row
            if row.get("gene_symbol"):
                stats.unique_genes.add(row["gene_symbol"])
            if row.get("consequence"):
                primary = row["consequence"].split("&")[0]
                stats.consequence_counts[primary] = stats.consequence_counts.get(primary, 0) + 1
            if row.get("mane_select"):
                stats.mane_select_count += 1

            rows.append(row)
            stats.total_input_lines += 1

    stats.variants_stored = len(rows)
    return rows


# ── Database Builder ─────────────────────────────────────────────────────


def build_bundle_db(
    rows: list[dict],
    output_path: Path,
    *,
    ensembl_version: str,
    build_date: str | None = None,
    bundle_version: str | None = None,
) -> str:
    """Create the vep_bundle.db SQLite file from parsed rows.

    Args:
        rows: List of row dicts matching the vep_annotations schema.
        output_path: Path for the output SQLite file.
        ensembl_version: Ensembl release number (e.g., "112").
        build_date: Optional build date string (ISO format).
        bundle_version: Optional semver string (e.g., "v2.0.0") recorded under
            ``bundle_metadata.bundle_version``. The manifest's `version` is the
            authoritative semver consulted by the staleness gate; this value
            is informational/audit only (Plan §5.5).

    Returns:
        SHA-256 hex digest of the created file.
    """
    # Remove old DB if present
    if output_path.exists():
        output_path.unlink()
    # Also remove WAL/SHM files
    for suffix in (".db-wal", ".db-shm"):
        wal_path = output_path.with_suffix(suffix)
        if wal_path.exists():
            wal_path.unlink()

    output_path.parent.mkdir(parents=True, exist_ok=True)

    with sqlite3.connect(str(output_path)) as conn:
        # Enable WAL mode
        conn.execute("PRAGMA journal_mode=WAL")

        # Create tables
        conn.execute(CREATE_TABLE_SQL)
        conn.execute(CREATE_META_SQL)

        # Bulk insert in batches
        for i in range(0, len(rows), BATCH_SIZE):
            batch = rows[i : i + BATCH_SIZE]
            conn.executemany(INSERT_SQL, batch)

        # Create indexes (after insert for performance)
        for idx_sql in INDEXES:
            conn.execute(idx_sql)

        # Store metadata
        if build_date is None:
            from datetime import UTC, datetime

            build_date = datetime.now(UTC).strftime("%Y-%m-%d")

        metadata = {
            "ensembl_version": ensembl_version,
            "build_date": build_date,
            "variant_count": str(len(rows)),
            "schema_version": "1",
        }
        if bundle_version is not None:
            metadata["bundle_version"] = bundle_version
        for key, value in metadata.items():
            conn.execute(
                "INSERT OR REPLACE INTO bundle_metadata (key, value) VALUES (?, ?)",
                (key, value),
            )

    # Checkpoint WAL (outside the connection context manager)
    with sqlite3.connect(str(output_path)) as wal_conn:
        wal_conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    # Compute SHA-256
    sha256 = _compute_sha256(output_path)
    return sha256


def _compute_sha256(path: Path) -> str:
    """Compute SHA-256 hex digest of a file."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


# ── Coverage Report ──────────────────────────────────────────────────────


def _load_catalog_rsids(catalog_path: Path) -> set[str]:
    """Load rsids from a catalog file (1-column rsids OR 3-column rsid+chrom+pos TSV).

    Auto-detects format from the first non-blank, non-`#` line. rs-prefix-only
    by design — VEP cannot annotate kgp*/i*/VG* IDs; those flow through the
    runtime coord-fallback (`backend/annotation/engine.py`) at annotation
    time and are not counted in this gate (Phase B step 5 gate semantics).
    """
    rsids: set[str] = set()
    detected: int | None = None
    with catalog_path.open(encoding="utf-8") as f:
        for line in f:
            stripped = line.rstrip("\n\r")
            if not stripped or stripped.startswith("#"):
                continue
            cols = stripped.split("\t")
            if detected is None:
                if len(cols) not in (1, 3):
                    raise ValueError(
                        f"--rsid-catalog: unexpected column count {len(cols)} "
                        "(expected 1-col rsids or 3-col rsid+chrom+pos TSV)"
                    )
                detected = len(cols)
            elif len(cols) != detected:
                raise ValueError(
                    f"--rsid-catalog: row column count {len(cols)} != header "
                    f"column count {detected}"
                )
            rsid = cols[0].strip()
            if rsid.startswith("rs"):
                rsids.add(rsid)
    return rsids


def coverage_report(
    rows: list[dict],
    rsid_catalog_path: Path | None = None,
) -> dict:
    """Generate a coverage report for the VEP bundle.

    If an rsid catalog file is provided, computes the coverage percentage
    against it. The catalog may be a 1-column file (one rsid per line) or
    the 3-column union TSV (``rsid<TAB>chrom<TAB>pos``) produced by
    ``scripts/build_union_catalog.py``; the format is auto-detected by
    :func:`_load_catalog_rsids`. Coverage is computed against the rs-prefix
    slice only. Otherwise just reports basic stats.

    Returns:
        Dict with coverage statistics.
    """
    stored_rsids = {r["rsid"] for r in rows if r.get("rsid")}
    annotated = sum(1 for r in rows if r.get("consequence"))

    report = {
        "total_variants": len(rows),
        "unique_rsids": len(stored_rsids),
        "annotated_with_consequence": annotated,
        "annotation_rate": annotated / len(rows) * 100 if rows else 0,
    }

    if rsid_catalog_path and rsid_catalog_path.exists():
        catalog_rsids = _load_catalog_rsids(rsid_catalog_path)

        covered = stored_rsids & catalog_rsids
        report["catalog_size"] = len(catalog_rsids)
        report["catalog_covered"] = len(covered)
        report["coverage_percent"] = (
            len(covered) / len(catalog_rsids) * 100 if catalog_rsids else 0
        )

    return report


# ── CLI ──────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Build VEP annotation bundle from Ensembl VEP output.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Examples:
  # Build from VEP VCF output:
  %(prog)s --vep-vcf vep_output.vcf.gz --output vep_bundle.db --ensembl-version 112

  # Build from seed CSV (testing):
  %(prog)s --seed-csv tests/fixtures/seed_csvs/vep_seed.csv --output vep_bundle.db

  # Dry run:
  %(prog)s --vep-vcf vep_output.vcf.gz --dry-run
""",
    )

    input_group = parser.add_mutually_exclusive_group(required=True)
    input_group.add_argument(
        "--vep-vcf",
        type=Path,
        help="Path to VEP output VCF file (plain or gzipped).",
    )
    input_group.add_argument(
        "--seed-csv",
        type=Path,
        help="Path to seed CSV file (for testing/development).",
    )

    parser.add_argument(
        "--output",
        type=Path,
        default=Path("vep_bundle.db"),
        help="Output SQLite file path (default: vep_bundle.db).",
    )
    parser.add_argument(
        "--ensembl-version",
        default="112",
        help="Ensembl release version (default: 112).",
    )
    parser.add_argument(
        "--bundle-version",
        default=None,
        help=(
            "Bundle semver written to bundle_metadata.bundle_version "
            "(e.g., 'v2.0.0'). Informational/audit only; the authoritative "
            "version lives in bundles/manifest.json (Plan §5.5)."
        ),
    )
    parser.add_argument(
        "--rsid-catalog",
        type=Path,
        help=(
            "Optional rsid catalog file for coverage report. Accepts either a "
            "1-column file (one rsid per line) or the 3-column union TSV "
            "(rsid<TAB>chrom<TAB>pos) produced by scripts/build_union_catalog.py. "
            "Coverage is computed against the rs-prefix slice only (VEP cannot "
            "annotate non-rs IDs; see the Phase B step 5 gate semantics)."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse input and report stats without writing the database.",
    )
    parser.add_argument(
        "--write-stats",
        type=Path,
        help="Write build statistics to a JSON file.",
    )

    args = parser.parse_args(argv)

    # Validate input exists
    input_path: Path = args.vep_vcf or args.seed_csv
    if not input_path.exists():
        print(f"Error: input file not found: {input_path}", file=sys.stderr)
        sys.exit(1)

    print("=" * 60)
    print("Yeliztli VEP Bundle Builder")
    print(f"  Input:           {input_path}")
    print(f"  Output:          {args.output}")
    print(f"  Ensembl version: {args.ensembl_version}")
    if args.bundle_version:
        print(f"  Bundle version:  {args.bundle_version}")
    if args.dry_run:
        print("  Mode:            DRY RUN")
    print("=" * 60)
    print()

    # Parse input
    stats = BuildStats()
    start_time = time.monotonic()

    if args.vep_vcf:
        print("Parsing VEP VCF output...")
        rows = parse_vep_vcf(args.vep_vcf, stats)
    else:
        print("Loading seed CSV...")
        rows = load_seed_csv(args.seed_csv, stats)

    stats.elapsed_seconds = time.monotonic() - start_time

    print(f"\n{stats.summary()}\n")

    # Coverage report
    if args.rsid_catalog:
        report = coverage_report(rows, args.rsid_catalog)
        print("Coverage Report:")
        print(f"  Catalog size:      {report.get('catalog_size', 'N/A'):,}")
        print(f"  Catalog covered:   {report.get('catalog_covered', 'N/A'):,}")
        print(f"  Coverage:          {report.get('coverage_percent', 0):.1f}%")
        print()
        stats.coverage_percent = report.get("coverage_percent", 0)

    if args.dry_run:
        print("[dry-run] No database file written.")
    else:
        print(f"Building SQLite database: {args.output}")
        build_start = time.monotonic()

        sha256 = build_bundle_db(
            rows,
            args.output,
            ensembl_version=args.ensembl_version,
            bundle_version=args.bundle_version,
        )

        build_elapsed = time.monotonic() - build_start
        file_size = args.output.stat().st_size
        size_mb = file_size / (1024 * 1024)

        print(f"  File size:   {size_mb:.1f} MB")
        print(f"  SHA-256:     {sha256}")
        print(f"  Build time:  {build_elapsed:.1f}s")
        print(f"  Total time:  {stats.elapsed_seconds + build_elapsed:.1f}s")
        print()
        print(f"Bundle written to: {args.output}")

    # Write stats JSON if requested
    if args.write_stats:
        stats_dict = {
            "total_input_lines": stats.total_input_lines,
            "total_csq_entries": stats.total_csq_entries,
            "variants_stored": stats.variants_stored,
            "skipped_no_rsid": stats.skipped_no_rsid,
            "skipped_invalid_chrom": stats.skipped_invalid_chrom,
            "skipped_malformed": stats.skipped_malformed,
            "unique_genes": len(stats.unique_genes),
            "mane_select_count": stats.mane_select_count,
            "coverage_percent": stats.coverage_percent,
            "elapsed_seconds": stats.elapsed_seconds,
            "consequence_counts": stats.consequence_counts,
            "ensembl_version": args.ensembl_version,
        }
        args.write_stats.parent.mkdir(parents=True, exist_ok=True)
        with open(args.write_stats, "w", encoding="utf-8") as f:
            json.dump(stats_dict, f, indent=2)
        print(f"Stats written to: {args.write_stats}")


if __name__ == "__main__":
    main()

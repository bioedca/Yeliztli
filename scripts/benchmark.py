#!/usr/bin/env python3
"""Performance benchmark for Yeliztli annotation pipeline.

Generates a synthetic 600k-SNP dataset, populates in-memory annotation
source databases, and times the full annotation pipeline against the
performance targets defined in the PRD:

    - 23andMe parse & ingest: < 30s (target) / < 2 min (acceptable)
    - Full annotation (600k SNPs): < 2 min (target) / < 5 min (acceptable)

Usage::

    python scripts/benchmark.py
    python scripts/benchmark.py --variants 100000  # quick run
    python scripts/benchmark.py --variants 600000  # full benchmark (default)
"""

from __future__ import annotations

import argparse
import random
import sys
import time
from pathlib import Path

import sqlalchemy as sa
from sqlalchemy.pool import StaticPool

# Ensure project root is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.annotation.engine import run_annotation
from backend.db.sample_schema import create_sample_tables
from backend.db.tables import (
    clinvar_variants,
    gene_phenotype,
    raw_variants,
    reference_metadata,
)


def create_shared_memory_engine() -> sa.Engine:
    """Create an in-memory SQLite engine that shares state across connections.

    Uses StaticPool so all connections see the same in-memory database,
    which is required for ThreadPoolExecutor-based annotation lookups.
    """
    return sa.create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )


# ── Constants ─────────────────────────────────────────────────────────────

CHROMOSOMES = [str(i) for i in range(1, 23)] + ["X", "Y", "MT"]
GENOTYPES = ["AA", "AC", "AG", "AT", "CC", "CG", "CT", "GG", "GT", "TT"]
BASES = ["A", "C", "G", "T"]

CONSEQUENCE_TYPES = [
    "missense_variant",
    "synonymous_variant",
    "intron_variant",
    "upstream_gene_variant",
    "downstream_gene_variant",
    "3_prime_UTR_variant",
    "5_prime_UTR_variant",
    "intergenic_variant",
    "splice_region_variant",
    "non_coding_transcript_exon_variant",
]

GENE_SYMBOLS = [
    "BRCA1",
    "BRCA2",
    "TP53",
    "EGFR",
    "MTHFR",
    "APOE",
    "CFTR",
    "COMT",
    "CYP2D6",
    "CYP2C19",
    "LDLR",
    "PCSK9",
    "APOB",
    "HBB",
    "GBA",
    "HEXA",
    "SMN1",
    "MLH1",
    "MSH2",
    "APC",
    "PTEN",
    "ATM",
    "CHEK2",
    "PALB2",
    "MUTYH",
    "VHL",
    "RB1",
    "NF1",
    "NF2",
    "TSC1",
    "TSC2",
    "WT1",
]

SIGNIFICANCE_LEVELS = [
    "Benign",
    "Likely_benign",
    "Uncertain_significance",
    "Likely_pathogenic",
    "Pathogenic",
    "risk_factor",
    "drug_response",
]

# ── Synthetic data generation ─────────────────────────────────────────────


def generate_raw_variants(n: int, seed: int = 42) -> list[dict]:
    """Generate n synthetic raw variant rows."""
    rng = random.Random(seed)
    variants = []
    for i in range(n):
        chrom = rng.choice(CHROMOSOMES[:22])  # autosomes only for simplicity
        pos = rng.randint(10_000, 250_000_000)
        variants.append(
            {
                "rsid": f"rs{1000000 + i}",
                "chrom": chrom,
                "pos": pos,
                "genotype": rng.choice(GENOTYPES),
            }
        )
    return variants


def seed_vep_bundle(
    engine: sa.Engine, rsids: list[str], match_rate: float = 0.7, seed: int = 42
) -> None:
    """Create and populate a vep_annotations table matching a fraction of rsids."""
    rng = random.Random(seed)
    with engine.connect() as conn:
        conn.execute(
            sa.text("""
            CREATE TABLE IF NOT EXISTS vep_annotations (
                rsid TEXT, chrom TEXT, pos INTEGER, ref TEXT, alt TEXT,
                gene_symbol TEXT, transcript_id TEXT, consequence TEXT,
                hgvs_coding TEXT, hgvs_protein TEXT, strand TEXT,
                exon_number INTEGER, intron_number INTEGER, mane_select INTEGER DEFAULT 0
            )
        """)
        )
        conn.execute(sa.text("CREATE INDEX IF NOT EXISTS idx_vep_rsid ON vep_annotations (rsid)"))
        conn.commit()

    matched = rng.sample(rsids, int(len(rsids) * match_rate))
    rows = []
    for rsid in matched:
        rows.append(
            {
                "rsid": rsid,
                "chrom": rng.choice(CHROMOSOMES[:22]),
                "pos": rng.randint(10_000, 250_000_000),
                "ref": rng.choice(BASES),
                "alt": rng.choice(BASES),
                "gene_symbol": rng.choice(GENE_SYMBOLS),
                "transcript_id": f"ENST{rng.randint(10000000, 99999999)}",
                "consequence": rng.choice(CONSEQUENCE_TYPES),
                "hgvs_coding": None,
                "hgvs_protein": None,
                "strand": rng.choice(["+", "-"]),
                "exon_number": rng.choice([None, rng.randint(1, 30)]),
                "intron_number": rng.choice([None, rng.randint(1, 30)]),
                "mane_select": rng.choice([0, 1]),
            }
        )

    batch_size = 10_000
    with engine.begin() as conn:
        for i in range(0, len(rows), batch_size):
            batch = rows[i : i + batch_size]
            conn.execute(
                sa.text(
                    "INSERT INTO vep_annotations (rsid, chrom, pos, ref, alt, gene_symbol, "
                    "transcript_id, consequence, hgvs_coding, hgvs_protein, strand, "
                    "exon_number, intron_number, mane_select) VALUES "
                    "(:rsid, :chrom, :pos, :ref, :alt, :gene_symbol, :transcript_id, "
                    ":consequence, :hgvs_coding, :hgvs_protein, :strand, :exon_number, "
                    ":intron_number, :mane_select)"
                ),
                batch,
            )


def seed_clinvar(
    engine: sa.Engine, rsids: list[str], match_rate: float = 0.05, seed: int = 42
) -> None:
    """Populate clinvar_variants with a small fraction of matching rsids."""
    rng = random.Random(seed)
    matched = rng.sample(rsids, int(len(rsids) * match_rate))
    rows = []
    for i, rsid in enumerate(matched):
        rows.append(
            {
                "rsid": rsid,
                "chrom": rng.choice(CHROMOSOMES[:22]),
                "pos": rng.randint(10_000, 250_000_000),
                "ref": rng.choice(BASES),
                "alt": rng.choice(BASES),
                "significance": rng.choice(SIGNIFICANCE_LEVELS),
                "review_stars": rng.randint(0, 4),
                "accession": f"VCV{100000 + i:09d}",
                "conditions": "test condition",
                "gene_symbol": rng.choice(GENE_SYMBOLS),
                "variation_id": 100000 + i,
            }
        )

    batch_size = 10_000
    with engine.begin() as conn:
        for i in range(0, len(rows), batch_size):
            batch = rows[i : i + batch_size]
            conn.execute(clinvar_variants.insert(), batch)


def seed_gene_phenotype(engine: sa.Engine, seed: int = 42) -> None:
    """Populate gene_phenotype with entries for each gene symbol."""
    rng = random.Random(seed)
    rows = []
    for gene in GENE_SYMBOLS:
        rows.append(
            {
                "gene_symbol": gene,
                "disease_name": f"Disease associated with {gene}",
                "disease_id": f"MONDO:{rng.randint(1000, 9999):07d}",
                "hpo_terms": '["HP:0000001"]',
                "source": "mondo_hpo",
                "inheritance": rng.choice(["Autosomal dominant", "Autosomal recessive", None]),
            }
        )
    with engine.begin() as conn:
        conn.execute(gene_phenotype.insert(), rows)


def seed_gnomad(
    engine: sa.Engine, rsids: list[str], match_rate: float = 0.6, seed: int = 42
) -> None:
    """Create and populate a gnomad_af table matching a fraction of rsids."""
    rng = random.Random(seed)
    with engine.connect() as conn:
        conn.execute(
            sa.text("""
            CREATE TABLE IF NOT EXISTS gnomad_af (
                rsid TEXT PRIMARY KEY, chrom TEXT NOT NULL, pos INTEGER NOT NULL,
                ref TEXT NOT NULL, alt TEXT NOT NULL,
                af_global REAL, af_afr REAL, af_amr REAL, af_eas REAL,
                af_asj REAL, af_eur REAL, af_fin REAL, af_sas REAL,
                homozygous_count INTEGER DEFAULT 0
            )
        """)
        )
        conn.execute(
            sa.text("CREATE INDEX IF NOT EXISTS idx_gnomad_chrom_pos ON gnomad_af (chrom, pos)")
        )
        conn.execute(
            sa.text(
                "CREATE INDEX IF NOT EXISTS idx_gnomad_chrom_pos_ref_alt "
                "ON gnomad_af (chrom, pos, ref, alt)"
            )
        )
        conn.commit()

    matched = rng.sample(rsids, int(len(rsids) * match_rate))
    rows = []
    for rsid in matched:
        af = rng.random() * 0.5
        rows.append(
            {
                "rsid": rsid,
                "chrom": rng.choice(CHROMOSOMES[:22]),
                "pos": rng.randint(10_000, 250_000_000),
                "ref": rng.choice(BASES),
                "alt": rng.choice(BASES),
                "af_global": af,
                "af_afr": min(1.0, af * rng.uniform(0.5, 2.0)),
                "af_amr": min(1.0, af * rng.uniform(0.5, 2.0)),
                "af_asj": min(1.0, af * rng.uniform(0.5, 2.0)),
                "af_eas": min(1.0, af * rng.uniform(0.5, 2.0)),
                "af_eur": min(1.0, af * rng.uniform(0.5, 2.0)),
                "af_fin": min(1.0, af * rng.uniform(0.5, 2.0)),
                "af_sas": min(1.0, af * rng.uniform(0.5, 2.0)),
                "homozygous_count": rng.randint(0, 1000),
            }
        )

    batch_size = 10_000
    with engine.begin() as conn:
        for i in range(0, len(rows), batch_size):
            batch = rows[i : i + batch_size]
            conn.execute(
                sa.text(
                    "INSERT INTO gnomad_af (rsid, chrom, pos, ref, alt, af_global, "
                    "af_afr, af_amr, af_asj, af_eas, af_eur, af_fin, af_sas, homozygous_count) "
                    "VALUES (:rsid, :chrom, :pos, :ref, :alt, :af_global, :af_afr, "
                    ":af_amr, :af_asj, :af_eas, :af_eur, :af_fin, :af_sas, :homozygous_count)"
                ),
                batch,
            )


def seed_dbnsfp(
    engine: sa.Engine, rsids: list[str], match_rate: float = 0.5, seed: int = 42
) -> None:
    """Create and populate a dbnsfp_scores table matching a fraction of rsids."""
    rng = random.Random(seed)
    with engine.connect() as conn:
        conn.execute(
            sa.text("""
            CREATE TABLE IF NOT EXISTS dbnsfp_scores (
                rsid TEXT, chrom TEXT NOT NULL, pos INTEGER NOT NULL,
                ref TEXT NOT NULL, alt TEXT NOT NULL,
                cadd_phred REAL, sift_score REAL, sift_pred TEXT,
                polyphen2_hsvar_score REAL, polyphen2_hsvar_pred TEXT,
                revel REAL, mutpred2 REAL, vest4 REAL,
                metasvm REAL, metalr REAL, gerp_rs REAL,
                phylop REAL, mpc REAL, primateai REAL,
                PRIMARY KEY (chrom, pos, ref, alt)
            )
        """)
        )
        conn.execute(sa.text("CREATE INDEX IF NOT EXISTS idx_dbnsfp_rsid ON dbnsfp_scores (rsid)"))
        conn.execute(
            sa.text(
                "CREATE INDEX IF NOT EXISTS idx_dbnsfp_chrom_pos ON dbnsfp_scores (chrom, pos)"
            )
        )
        conn.commit()

    matched = rng.sample(rsids, int(len(rsids) * match_rate))
    rows = []
    seen_keys: set[tuple[str, int, str, str]] = set()
    for rsid in matched:
        chrom = rng.choice(CHROMOSOMES[:22])
        pos = rng.randint(10_000, 250_000_000)
        ref = rng.choice(BASES)
        alt = rng.choice([b for b in BASES if b != ref])
        key = (chrom, pos, ref, alt)
        if key in seen_keys:
            continue
        seen_keys.add(key)

        sift = rng.random()
        pp2 = rng.random()
        rows.append(
            {
                "rsid": rsid,
                "chrom": chrom,
                "pos": pos,
                "ref": ref,
                "alt": alt,
                "cadd_phred": rng.uniform(0, 40),
                "sift_score": sift,
                "sift_pred": "D" if sift < 0.05 else "T",
                "polyphen2_hsvar_score": pp2,
                "polyphen2_hsvar_pred": "D" if pp2 > 0.85 else ("P" if pp2 > 0.15 else "B"),
                "revel": rng.random(),
                "mutpred2": rng.random(),
                "vest4": rng.random(),
                "metasvm": rng.uniform(-2, 2),
                "metalr": rng.random(),
                "gerp_rs": rng.uniform(-12, 6),
                "phylop": rng.uniform(-20, 10),
                "mpc": rng.uniform(0, 5),
                "primateai": rng.random(),
            }
        )

    batch_size = 10_000
    with engine.begin() as conn:
        for i in range(0, len(rows), batch_size):
            batch = rows[i : i + batch_size]
            conn.execute(
                sa.text(
                    "INSERT OR IGNORE INTO dbnsfp_scores (rsid, chrom, pos, ref, alt, "
                    "cadd_phred, sift_score, sift_pred, polyphen2_hsvar_score, "
                    "polyphen2_hsvar_pred, revel, mutpred2, vest4, metasvm, metalr, "
                    "gerp_rs, phylop, mpc, primateai) VALUES "
                    "(:rsid, :chrom, :pos, :ref, :alt, :cadd_phred, :sift_score, "
                    ":sift_pred, :polyphen2_hsvar_score, :polyphen2_hsvar_pred, "
                    ":revel, :mutpred2, :vest4, :metasvm, :metalr, :gerp_rs, "
                    ":phylop, :mpc, :primateai)"
                ),
                batch,
            )


# ── Mock DBRegistry ──────────────────────────────────────────────────────


class BenchmarkDBRegistry:
    """Lightweight DBRegistry substitute for benchmarking.

    Provides the same property interface as DBRegistry but backed by
    in-memory SQLite engines with synthetic data.
    """

    def __init__(
        self,
        reference_engine: sa.Engine,
        vep_engine: sa.Engine,
        gnomad_engine: sa.Engine,
        dbnsfp_engine: sa.Engine,
    ) -> None:
        self.reference_engine = reference_engine
        self._vep_engine = vep_engine
        self._gnomad_engine = gnomad_engine
        self._dbnsfp_engine = dbnsfp_engine

    @property
    def vep_engine(self) -> sa.Engine:
        return self._vep_engine

    @property
    def gnomad_engine(self) -> sa.Engine:
        return self._gnomad_engine

    @property
    def dbnsfp_engine(self) -> sa.Engine:
        return self._dbnsfp_engine


# ── Benchmark runner ─────────────────────────────────────────────────────


def run_benchmark(num_variants: int = 600_000) -> dict:
    """Run the full annotation benchmark and return timing results.

    Args:
        num_variants: Number of synthetic variants to generate.

    Returns:
        Dict with timing results and statistics.
    """
    results: dict = {"num_variants": num_variants}

    print(f"\n{'=' * 60}")
    print("  Yeliztli Annotation Pipeline Benchmark")
    print(f"  Variants: {num_variants:,}")
    print(f"{'=' * 60}\n")

    # 1. Generate synthetic raw variants
    print("[1/5] Generating synthetic variants...", end=" ", flush=True)
    t0 = time.perf_counter()
    raw_data = generate_raw_variants(num_variants)
    rsids = [r["rsid"] for r in raw_data]
    t_gen = time.perf_counter() - t0
    results["generate_variants_s"] = t_gen
    print(f"{t_gen:.2f}s")

    # 2. Populate sample DB
    print("[2/5] Loading variants into sample DB...", end=" ", flush=True)
    t0 = time.perf_counter()
    sample_engine = create_shared_memory_engine()
    create_sample_tables(sample_engine)
    batch_size = 50_000
    with sample_engine.begin() as conn:
        for i in range(0, len(raw_data), batch_size):
            conn.execute(raw_variants.insert(), raw_data[i : i + batch_size])
    t_ingest = time.perf_counter() - t0
    results["ingest_s"] = t_ingest
    print(f"{t_ingest:.2f}s")

    # 3. Populate annotation source DBs
    print("[3/5] Building annotation source databases...", end=" ", flush=True)
    t0 = time.perf_counter()

    reference_engine = create_shared_memory_engine()
    reference_metadata.create_all(reference_engine)

    vep_engine = create_shared_memory_engine()
    gnomad_engine = create_shared_memory_engine()
    dbnsfp_engine = create_shared_memory_engine()

    seed_vep_bundle(vep_engine, rsids, match_rate=0.7)
    seed_clinvar(reference_engine, rsids, match_rate=0.05)
    seed_gene_phenotype(reference_engine)
    seed_gnomad(gnomad_engine, rsids, match_rate=0.6)
    seed_dbnsfp(dbnsfp_engine, rsids, match_rate=0.5)

    t_seed = time.perf_counter() - t0
    results["seed_dbs_s"] = t_seed
    print(f"{t_seed:.2f}s")

    # 4. Build registry
    registry = BenchmarkDBRegistry(
        reference_engine=reference_engine,
        vep_engine=vep_engine,
        gnomad_engine=gnomad_engine,
        dbnsfp_engine=dbnsfp_engine,
    )

    # 5. Run annotation engine
    batch_count = [0]

    def progress_callback(variants_done: int, total: int) -> None:
        batch_count[0] += 1
        pct = variants_done / total * 100
        elapsed = time.perf_counter() - t_annotation_start
        rate = variants_done / elapsed if elapsed > 0 else 0
        eta = (total - variants_done) / rate if rate > 0 else 0
        print(
            f"\r[4/5] Annotating... {pct:5.1f}%  "
            f"({variants_done:,}/{total:,})  "
            f"{rate:,.0f} var/s  ETA {eta:.0f}s",
            end="",
            flush=True,
        )

    print("[4/5] Annotating...", end=" ", flush=True)
    t_annotation_start = time.perf_counter()
    engine_result = run_annotation(sample_engine, registry, progress_callback=progress_callback)
    t_annotation = time.perf_counter() - t_annotation_start
    results["annotation_s"] = t_annotation
    print()  # newline after progress

    # 5. Verify output
    print("[5/5] Verifying results...", end=" ", flush=True)
    with sample_engine.connect() as conn:
        from backend.db.tables import annotated_variants

        count = conn.execute(sa.select(sa.func.count()).select_from(annotated_variants)).scalar()
    results["rows_written"] = count
    results["vep_matched"] = engine_result.vep_matched
    results["clinvar_matched"] = engine_result.clinvar_matched
    results["gnomad_matched"] = engine_result.gnomad_matched
    results["dbnsfp_matched"] = engine_result.dbnsfp_matched
    results["gene_phenotype_matched"] = engine_result.gene_phenotype_matched
    results["batches"] = engine_result.batches_processed
    results["errors"] = engine_result.errors
    print("done")

    # ── Report ───────────────────────────────────────────────────────────
    print(f"\n{'=' * 60}")
    print("  RESULTS")
    print(f"{'=' * 60}")
    print(f"  Variants processed:     {num_variants:>10,}")
    print(f"  Rows written:           {count:>10,}")
    print(f"  Batches:                {engine_result.batches_processed:>10}")
    print()
    print(f"  VEP matched:            {engine_result.vep_matched:>10,}")
    print(f"  ClinVar matched:        {engine_result.clinvar_matched:>10,}")
    print(f"  gnomAD matched:         {engine_result.gnomad_matched:>10,}")
    print(f"  dbNSFP matched:         {engine_result.dbnsfp_matched:>10,}")
    print(f"  Gene-phenotype matched: {engine_result.gene_phenotype_matched:>10,}")
    print()

    rate = num_variants / t_annotation if t_annotation > 0 else 0
    print(f"  Variant generation:     {t_gen:>10.2f}s")
    print(f"  Ingest (DB load):       {t_ingest:>10.2f}s")
    print(f"  DB seeding:             {t_seed:>10.2f}s")
    print(f"  Annotation pipeline:    {t_annotation:>10.2f}s  ({rate:,.0f} var/s)")
    print()

    # Per-source timing breakdown (P4-22 bottleneck identification)
    print("  Per-source timing (cumulative across batches, wall-clock per thread):")
    print(f"    VEP lookup:           {engine_result.timing_vep_s:>10.2f}s")
    print(f"    ClinVar lookup:       {engine_result.timing_clinvar_s:>10.2f}s")
    print(f"    gnomAD lookup:        {engine_result.timing_gnomad_s:>10.2f}s")
    print(f"    dbNSFP lookup:        {engine_result.timing_dbnsfp_s:>10.2f}s")
    print(f"    Gene-phenotype:       {engine_result.timing_gene_phenotype_s:>10.2f}s")
    print(f"    Merge + conflict:     {engine_result.timing_merge_s:>10.2f}s")
    print(f"    Bulk upsert:          {engine_result.timing_upsert_s:>10.2f}s")
    print()

    results["timing_vep_s"] = engine_result.timing_vep_s
    results["timing_clinvar_s"] = engine_result.timing_clinvar_s
    results["timing_gnomad_s"] = engine_result.timing_gnomad_s
    results["timing_dbnsfp_s"] = engine_result.timing_dbnsfp_s
    results["timing_gene_phenotype_s"] = engine_result.timing_gene_phenotype_s
    results["timing_merge_s"] = engine_result.timing_merge_s
    results["timing_upsert_s"] = engine_result.timing_upsert_s

    # Performance targets
    annotation_target = 120.0  # 2 minutes
    annotation_limit = 300.0  # 5 minutes
    ingest_target = 30.0
    ingest_limit = 120.0

    scale = num_variants / 600_000  # scale targets for non-standard sizes

    def status(value: float, target: float, limit: float) -> str:
        if value <= target * scale:
            return "PASS (target)"
        if value <= limit * scale:
            return "PASS (acceptable)"
        return "FAIL"

    ann_status = status(t_annotation, annotation_target, annotation_limit)
    ing_status = status(t_ingest, ingest_target, ingest_limit)

    print(f"  {'Performance Target':<30} {'Result':>8}  {'Limit':>8}  {'Status'}")
    print(f"  {'-' * 30} {'-' * 8}  {'-' * 8}  {'-' * 20}")
    print(
        f"  {'Annotation (target < 2m)':<30} "
        f"{t_annotation:>7.1f}s  {annotation_target * scale:>7.0f}s  {ann_status}"
    )
    print(
        f"  {'Annotation (limit < 5m)':<30} "
        f"{t_annotation:>7.1f}s  {annotation_limit * scale:>7.0f}s  {ann_status}"
    )
    print(
        f"  {'Ingest (target < 30s)':<30} "
        f"{t_ingest:>7.1f}s  {ingest_target * scale:>7.0f}s  {ing_status}"
    )
    print(f"{'=' * 60}\n")

    if engine_result.errors:
        print(f"  Errors: {engine_result.errors}")

    results["annotation_status"] = ann_status
    results["ingest_status"] = ing_status
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="Yeliztli annotation pipeline benchmark")
    parser.add_argument(
        "--variants",
        "-n",
        type=int,
        default=600_000,
        help="Number of synthetic variants to generate (default: 600000)",
    )
    args = parser.parse_args()

    results = run_benchmark(args.variants)

    # Exit non-zero if annotation pipeline exceeds hard limit
    if "FAIL" in results.get("annotation_status", "") or "FAIL" in results.get(
        "ingest_status", ""
    ):
        sys.exit(1)


if __name__ == "__main__":
    main()

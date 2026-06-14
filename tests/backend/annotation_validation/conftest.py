"""Shared live-path harness for the annotation validation suite.

Every test in this package drives the **live** annotation pipeline —
``backend.annotation.engine.run_annotation`` followed by
``backend.analysis.run_all.run_all_analyses`` — against tiny, purpose-built
reference databases, then inspects the resulting ``findings`` and
``annotated_variants`` rows.

Three rules, enforced by this harness, encode the validation strategy in
``docs/annotation-validation-strategy.md`` §4 (the green suite missed every
defect precisely because it broke all three):

1. **Live path only.** We call ``run_annotation`` / ``run_all_analyses`` — never
   the orphaned ``annotate_sample_clinvar`` / ``annotate_sample_dbsnp`` writers.
2. **Real column names.** Reference rows use the genuine reference-DB columns,
   and dbNSFP rows are written to a real-header dbNSFP TSV and loaded through
   the production ``load_dbnsfp_from_tsv`` → ``parse_dbnsfp_tsv_line`` path. This
   is what makes the F31 ``MutPred2_score`` mapping bug observable (a
   pre-normalised CSV fixture would silently hide it).
3. **Assert carriage, not presence.** Tests assert on the genotype the sample
   actually carries, recomputed independently via the project's own
   ``classify_zygosity`` where needed.

The harness exposes a single factory fixture, :func:`build_live_run`, that
builds a sample from inline ``(rsid, chrom, pos, genotype)`` rows (or a vendor
file), seeds whichever sources a test needs, runs the pipeline, and returns a
:class:`LiveRun` snapshot.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import pytest
import sqlalchemy as sa

from backend.analysis.run_all import run_all_analyses
from backend.annotation.alphamissense import create_alphamissense_table
from backend.annotation.dbnsfp import create_dbnsfp_tables, load_dbnsfp_from_tsv
from backend.annotation.engine import run_annotation
from backend.annotation.gnomad import create_gnomad_tables
from backend.config import Settings
from backend.db.connection import DBRegistry, get_registry, reset_registry
from backend.db.sample_schema import create_sample_tables
from backend.db.tables import (
    annotated_variants,
    annotation_state,
    clinvar_variants,
    cpic_alleles,
    cpic_diplotypes,
    cpic_guidelines,
    database_versions,
    dbsnp_merges,
    findings,
    gene_phenotype,
    gwas_associations,
    individuals,
    jobs,
    raw_variants,
    reference_metadata,
    samples,
)

# ── Real dbNSFP TSV header ────────────────────────────────────────────────
#
# The exact column names dbNSFP 5.x distributes. Tests that want a score
# populated supply the value under its *real* header key; the harness writes a
# genuine TSV and loads it through the production parser so the real
# ``parse_dbnsfp_tsv_line`` field map is exercised on every run. (Today the
# parser reads ``MutPred_score`` rather than ``MutPred2_score`` — F31 — so the
# ``mutpred2`` column comes back NULL; the M5 coverage gate catches that.)
DBNSFP_REAL_COLUMNS: tuple[str, ...] = (
    "#chr",
    "pos(1-based)",
    "ref",
    "alt",
    "rs_dbSNP",
    "CADD_phred",
    "SIFT4G_score",
    "SIFT4G_pred",
    "Polyphen2_HVAR_score",
    "Polyphen2_HVAR_pred",
    "REVEL_score",
    "MutPred2_score",
    "VEST4_score",
    "MetaSVM_score",
    "MetaLR_score",
    "GERP++_RS",
    "phyloP100way_vertebrate",
    "MPC_score",
    "PrimateAI_score",
)

# gnomad_af column order (matches create_gnomad_tables / the e2e seeder).
_GNOMAD_COLUMNS: tuple[str, ...] = (
    "rsid",
    "chrom",
    "pos",
    "ref",
    "alt",
    "af_global",
    "af_afr",
    "af_amr",
    "af_eas",
    "af_eur",
    "af_fin",
    "af_sas",
    "homozygous_count",
)

_VEP_DDL = (
    "CREATE TABLE vep_annotations ("
    "  rsid TEXT, chrom TEXT, pos INTEGER, ref TEXT, alt TEXT,"
    "  gene_symbol TEXT, transcript_id TEXT, consequence TEXT,"
    "  hgvs_coding TEXT, hgvs_protein TEXT, strand TEXT,"
    "  exon_number INTEGER, intron_number INTEGER, mane_select INTEGER"
    ")"
)

_VEP_INSERT = (
    "INSERT INTO vep_annotations "
    "(rsid, chrom, pos, ref, alt, gene_symbol, transcript_id, consequence,"
    " hgvs_coding, hgvs_protein, strand, exon_number, intron_number, mane_select) "
    "VALUES (:rsid, :chrom, :pos, :ref, :alt, :gene_symbol, :transcript_id,"
    " :consequence, :hgvs_coding, :hgvs_protein, :strand, :exon_number,"
    " :intron_number, :mane_select)"
)


# ── LiveRun snapshot ──────────────────────────────────────────────────────


@dataclass
class LiveRun:
    """Snapshot of one live pipeline run, ready to assert against."""

    settings: Settings
    registry: DBRegistry
    sample_engine: sa.Engine
    sample_id: int
    file_format: str
    annot_result: object
    analysis_result: dict
    findings: list[sa.Row]
    annotated: list[sa.Row]
    raw: list[sa.Row]

    # ── convenience accessors ─────────────────────────────────────────
    def findings_in(self, *categories: str) -> list[sa.Row]:
        cats = set(categories)
        return [f for f in self.findings if f.category in cats]

    def findings_for_rsid(self, rsid: str) -> list[sa.Row]:
        return [f for f in self.findings if f.rsid == rsid]

    def findings_for_module(self, module: str) -> list[sa.Row]:
        return [f for f in self.findings if f.module == module]

    def annotated_by_rsid(self, rsid: str) -> sa.Row | None:
        for row in self.annotated:
            if row.rsid == rsid:
                return row
        return None

    def raw_by_rsid(self, rsid: str) -> sa.Row | None:
        for row in self.raw:
            if row.rsid == rsid:
                return row
        return None


# ── Source-DB builders (real column names) ────────────────────────────────


def _build_reference_db(
    settings: Settings,
    *,
    clinvar: list[dict],
    gene_phenotype_rows: list[dict],
    dbsnp_merge_rows: list[dict],
    gwas: list[dict],
    cpic_allele_rows: list[dict],
    cpic_diplotype_rows: list[dict],
    cpic_guideline_rows: list[dict],
) -> None:
    engine = sa.create_engine(f"sqlite:///{settings.reference_db_path}")
    try:
        reference_metadata.create_all(engine)
        with engine.begin() as conn:
            if clinvar:
                conn.execute(clinvar_variants.insert(), clinvar)
            if gene_phenotype_rows:
                conn.execute(gene_phenotype.insert(), gene_phenotype_rows)
            if dbsnp_merge_rows:
                conn.execute(dbsnp_merges.insert(), dbsnp_merge_rows)
            if gwas:
                conn.execute(gwas_associations.insert(), gwas)
            if cpic_allele_rows:
                conn.execute(cpic_alleles.insert(), cpic_allele_rows)
            if cpic_diplotype_rows:
                conn.execute(cpic_diplotypes.insert(), cpic_diplotype_rows)
            if cpic_guideline_rows:
                conn.execute(cpic_guidelines.insert(), cpic_guideline_rows)
            conn.execute(
                database_versions.insert().values(
                    db_name="vep_bundle",
                    version="v2.0.0",
                    downloaded_at=datetime.now(UTC),
                )
            )
    finally:
        engine.dispose()


def _build_vep_db(db_path: Path, vep: list[dict]) -> None:
    engine = sa.create_engine(f"sqlite:///{db_path}")
    try:
        with engine.begin() as conn:
            conn.execute(sa.text(_VEP_DDL))
            conn.execute(sa.text("CREATE INDEX idx_vep_rsid ON vep_annotations(rsid)"))
            conn.execute(sa.text("CREATE INDEX idx_vep_chrom_pos ON vep_annotations(chrom, pos)"))
            for row in vep:
                conn.execute(
                    sa.text(_VEP_INSERT),
                    {
                        "rsid": row["rsid"],
                        "chrom": row["chrom"],
                        "pos": row["pos"],
                        "ref": row.get("ref"),
                        "alt": row.get("alt"),
                        "gene_symbol": row.get("gene_symbol"),
                        "transcript_id": row.get("transcript_id"),
                        "consequence": row.get("consequence"),
                        "hgvs_coding": row.get("hgvs_coding"),
                        "hgvs_protein": row.get("hgvs_protein"),
                        "strand": row.get("strand", "+"),
                        "exon_number": row.get("exon_number"),
                        "intron_number": row.get("intron_number"),
                        "mane_select": int(row.get("mane_select", 0)),
                    },
                )
    finally:
        engine.dispose()


def _build_gnomad_db(db_path: Path, gnomad: list[dict]) -> None:
    engine = sa.create_engine(f"sqlite:///{db_path}")
    try:
        create_gnomad_tables(engine)
        with engine.begin() as conn:
            for row in gnomad:
                af = row.get("af_global", 0.0)
                values = {
                    "rsid": row["rsid"],
                    "chrom": row["chrom"],
                    "pos": row["pos"],
                    "ref": row["ref"],
                    "alt": row["alt"],
                    "af_global": af,
                    "af_afr": row.get("af_afr", af),
                    "af_amr": row.get("af_amr", af),
                    "af_eas": row.get("af_eas", af),
                    "af_eur": row.get("af_eur", af),
                    "af_fin": row.get("af_fin", af),
                    "af_sas": row.get("af_sas", af),
                    "homozygous_count": row.get("homozygous_count", 0),
                }
                placeholders = ", ".join(f":{c}" for c in _GNOMAD_COLUMNS)
                conn.execute(
                    sa.text(f"INSERT INTO gnomad_af VALUES ({placeholders})"),
                    values,
                )
    finally:
        engine.dispose()


def write_dbnsfp_tsv(rows: list[dict], path: Path) -> None:
    """Write dbNSFP rows to a genuine-header TSV.

    Each row's keys are *real* dbNSFP column names (see ``DBNSFP_REAL_COLUMNS``);
    missing columns are emitted as ``.`` (dbNSFP's missing-value sentinel). This
    is the only way dbNSFP data enters the harness, so the production
    ``parse_dbnsfp_tsv_line`` field map is always the code under test.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh, delimiter="\t")
        writer.writerow(DBNSFP_REAL_COLUMNS)
        for row in rows:
            writer.writerow(str(row.get(col, ".")) for col in DBNSFP_REAL_COLUMNS)


def _build_dbnsfp_db(db_path: Path, dbnsfp_rows: list[dict]) -> None:
    engine = sa.create_engine(f"sqlite:///{db_path}")
    try:
        create_dbnsfp_tables(engine)
        if dbnsfp_rows:
            tsv_path = db_path.parent / "dbnsfp_seed.tsv"
            write_dbnsfp_tsv(dbnsfp_rows, tsv_path)
            load_dbnsfp_from_tsv(tsv_path, engine, clear_existing=False)
    finally:
        engine.dispose()


def _build_alphamissense_db(db_path: Path, alphamissense_rows: list[dict]) -> None:
    engine = sa.create_engine(f"sqlite:///{db_path}")
    try:
        create_alphamissense_table(engine)
        if alphamissense_rows:
            with engine.begin() as conn:
                conn.execute(
                    sa.text(
                        "INSERT INTO alphamissense_scores "
                        "(chrom, pos, ref, alt, am_pathogenicity, am_class) "
                        "VALUES (:chrom, :pos, :ref, :alt, :am_pathogenicity, :am_class)"
                    ),
                    alphamissense_rows,
                )
    finally:
        engine.dispose()


def _register_sample(
    registry: DBRegistry,
    *,
    file_format: str,
    raw_rows: list[dict],
) -> tuple[int, sa.Engine]:
    now = datetime.now(UTC)
    with registry.reference_engine.begin() as conn:
        ind = conn.execute(
            individuals.insert().values(
                display_name="Validation Subject",
                notes="",
                updated_at=now,
            )
        )
        individual_id = int(ind.inserted_primary_key[0])
        res = conn.execute(
            samples.insert().values(
                name="validation_sample",
                db_path="",
                file_format=file_format,
                file_hash="validation_hash",
                individual_id=individual_id,
                created_at=now,
                updated_at=now,
            )
        )
        sample_id = int(res.inserted_primary_key[0])
        db_path = f"samples/sample_{sample_id}.db"
        conn.execute(samples.update().where(samples.c.id == sample_id).values(db_path=db_path))
        conn.execute(
            jobs.insert().values(
                job_id=f"job-{sample_id}",
                sample_id=sample_id,
                job_type="annotation",
                status="complete",
                progress_pct=100.0,
                message="",
                created_at=now,
                updated_at=now,
            )
        )

    sample_db_path = registry.settings.data_dir / db_path
    sample_db_path.parent.mkdir(parents=True, exist_ok=True)
    engine = registry.get_sample_engine(sample_db_path)
    create_sample_tables(engine, is_merged_sample=False)
    with engine.begin() as conn:
        if raw_rows:
            conn.execute(raw_variants.insert(), raw_rows)
        conn.execute(
            annotation_state.insert().values(
                key="vep_bundle_version", value="v2.0.0", updated_at=now
            )
        )
    return sample_id, engine


# ── The factory fixture ───────────────────────────────────────────────────


@pytest.fixture
def build_live_run(tmp_data_dir: Path):
    """Factory returning a ``_build(...)`` that runs the live pipeline.

    Keeps the ``get_settings`` singleton patched and the registry alive for the
    duration of the test so any incidental ``get_registry()`` call inside the
    analysis modules resolves to the test-scoped databases.
    """
    cleanups: list = []

    def _build(
        *,
        variants: list[dict],
        file_format: str = "23andme_v5",
        clinvar: list[dict] | None = None,
        gene_phenotype_rows: list[dict] | None = None,
        dbsnp_merge_rows: list[dict] | None = None,
        gwas: list[dict] | None = None,
        cpic_allele_rows: list[dict] | None = None,
        cpic_diplotype_rows: list[dict] | None = None,
        cpic_guideline_rows: list[dict] | None = None,
        vep: list[dict] | None = None,
        gnomad: list[dict] | None = None,
        dbnsfp_rows: list[dict] | None = None,
        alphamissense_rows: list[dict] | None = None,
        run_analyses: bool = True,
    ) -> LiveRun:
        settings = Settings(data_dir=tmp_data_dir, wal_mode=False)

        _build_reference_db(
            settings,
            clinvar=clinvar or [],
            gene_phenotype_rows=gene_phenotype_rows or [],
            dbsnp_merge_rows=dbsnp_merge_rows or [],
            gwas=gwas or [],
            cpic_allele_rows=cpic_allele_rows or [],
            cpic_diplotype_rows=cpic_diplotype_rows or [],
            cpic_guideline_rows=cpic_guideline_rows or [],
        )
        _build_vep_db(settings.vep_bundle_db_path, vep or [])
        _build_gnomad_db(settings.gnomad_db_path, gnomad or [])
        _build_dbnsfp_db(settings.dbnsfp_db_path, dbnsfp_rows or [])
        if alphamissense_rows is not None:
            _build_alphamissense_db(settings.alphamissense_db_path, alphamissense_rows)

        patcher = patch("backend.db.connection.get_settings", return_value=settings)
        patcher.start()
        reset_registry()
        registry = get_registry()
        cleanups.append(patcher.stop)
        cleanups.append(reset_registry)

        sample_id, sample_engine = _register_sample(
            registry, file_format=file_format, raw_rows=variants
        )

        annot_result = run_annotation(sample_engine, registry)
        analysis_result = run_all_analyses(sample_engine, registry) if run_analyses else {}
        if run_analyses:
            # Mirror the production Huey flow: stamp per-finding provenance after
            # analysis so the live path exercises it (SW-A4 #8). Audit-only — it
            # does not change finding counts or carriage, so the golden snapshot
            # is unaffected.
            from backend.analysis.provenance import stamp_findings_provenance

            stamp_findings_provenance(sample_engine, registry.reference_engine)

        with sample_engine.connect() as conn:
            findings_rows = conn.execute(sa.select(findings)).fetchall()
            annotated_rows = conn.execute(sa.select(annotated_variants)).fetchall()
            raw_rows_read = conn.execute(sa.select(raw_variants)).fetchall()

        return LiveRun(
            settings=settings,
            registry=registry,
            sample_engine=sample_engine,
            sample_id=sample_id,
            file_format=file_format,
            annot_result=annot_result,
            analysis_result=analysis_result,
            findings=findings_rows,
            annotated=annotated_rows,
            raw=raw_rows_read,
        )

    yield _build

    for fn in reversed(cleanups):
        try:
            fn()
        except Exception:  # noqa: BLE001 - teardown best-effort
            pass


# ── Reference-row helpers ─────────────────────────────────────────────────


def clinvar_row(
    rsid: str,
    chrom: str,
    pos: int,
    ref: str,
    alt: str,
    significance: str,
    review_stars: int,
    *,
    gene: str = "GENEX",
    conditions: str | None = None,
) -> dict:
    """Build one ``clinvar_variants`` row with real column names."""
    return {
        "rsid": rsid,
        "chrom": chrom,
        "pos": pos,
        "ref": ref,
        "alt": alt,
        "significance": significance,
        "review_stars": review_stars,
        "accession": f"VCV_{rsid}_{alt}",
        "conditions": conditions or f"{gene}-related condition",
        "gene_symbol": gene,
        "variation_id": abs(hash((rsid, alt))) % 1_000_000,
    }


# ── Reusable genotype scaffolding ─────────────────────────────────────────

# An evaluable non-PAR chrX pool (a diploid-X heterozygosity rate + homozygous
# filler) plus a chrY no-call denominator. Sex inference requires an aggregate
# denominator on both sex chromosomes before a confident call (issue #363:
# ``MIN_X_NONPAR_TYPED`` typed non-PAR chrX, ``MIN_Y_PROBES`` chrY probes) AND
# decides X dosage on the non-PAR chrX heterozygosity *rate* (issue #519): a real
# female is heterozygous at a large fraction of markers, so the scaffold carries a
# diploid-X het rate (60 het + 60 hom = 0.50, above the 0.15 diploid cutoff), not
# a lone noise het. With the chrY denominator all no-call (rate 0.0, at/below the
# noise floor), ``infer_biological_sex`` resolves XX — prepending it to a variant
# list yields an XX sample without a full chip's worth of rows.
XX_SCAFFOLD: tuple[dict, ...] = (
    *(
        {
            "rsid": f"rs_xx_scaffold_het_{i}",
            "chrom": "X",
            "pos": 50_000_000 + i,
            "genotype": "AG",
        }
        for i in range(60)
    ),
    *(
        {
            "rsid": f"rs_xx_scaffold_hom_{i}",
            "chrom": "X",
            "pos": 50_100_000 + i,
            "genotype": "GG",
        }
        for i in range(60)
    ),
)

# chrY no-call denominator (≥ MIN_Y_PROBES) so the chrY missingness signal is
# evaluable for the XX scaffold; all no-call → contributes 0 to ``y_rate``.
XX_CHRY_DENOMINATOR: tuple[dict, ...] = tuple(
    {
        "rsid": f"rs_xx_y_denom_{i}",
        "chrom": "Y",
        "pos": 2_800_000 + i,
        "genotype": "--",
    }
    for i in range(50)
)

CHRY_NOISE_FLOOR_NOCALLS: tuple[dict, ...] = tuple(
    {
        "rsid": f"rs_y_noise_floor_nc_{i}",
        "chrom": "Y",
        "pos": 2_700_100 + i,
        "genotype": "--",
    }
    for i in range(9)
)


def with_xx_scaffold(variants: list[dict]) -> list[dict]:
    """Return *variants* prefixed with the evaluable XX-supporting chrX pool and
    chrY denominator (issue #363)."""
    return [*XX_SCAFFOLD, *XX_CHRY_DENOMINATOR, *variants]


def with_chry_noise_floor(typed_y_variant: dict) -> list[dict]:
    """Return one typed chrY row plus no-calls so ``y_rate == 0.10``."""
    return [typed_y_variant, *CHRY_NOISE_FLOOR_NOCALLS]

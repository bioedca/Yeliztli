"""Annotation engine orchestrator.

Coordinates all annotation sources (VEP bundle, ClinVar, gnomAD, dbNSFP, AlphaMissense,
gene-phenotype) via the DBRegistry batch lookup pattern.  Processes raw
variants in 10k-variant batches with **concurrent lookups** across sources
using ``ThreadPoolExecutor``, merges results in Python, computes the
``annotation_coverage`` bitmask, and bulk-upserts into the single wide
``annotated_variants`` table.

WAL checkpoint runs after completion.  Crash recovery is full restart:
delete partial results, re-run from scratch.

Usage::

    from backend.annotation.engine import run_annotation

    result = run_annotation(sample_engine, registry)
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import sqlalchemy as sa
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from backend.analysis.insilico_tiers import is_missense_consequence
from backend.analysis.zygosity import classify_zygosity
from backend.annotation.alphamissense import lookup_alphamissense_by_positions
from backend.annotation.dbnsfp import (
    DbNSFPAnnotation,
    is_ensemble_pathogenic,
    is_ensemble_pathogenic_from_counts,
    lookup_dbnsfp_by_positions,
    lookup_dbnsfp_by_rsids,
)
from backend.annotation.evidence_conflict import apply_evidence_conflicts
from backend.annotation.gnomad import (
    GnomADAnnotation,
    lookup_gnomad_by_positions,
    lookup_gnomad_by_rsids,
)
from backend.db.tables import (
    annotated_variants,
    database_versions,
    raw_variants,
    sample_metadata_table,
)

if TYPE_CHECKING:
    from backend.db.connection import DBRegistry

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────

# Batch size for reading raw variants and writing annotations
ENGINE_BATCH_SIZE = 10_000
_VEP_ALLELE_BATCH_SIZE = 500

# Annotation source bitmask bits (must match individual modules)
VEP_BIT = 0b000001  # bit 0 = 1
CLINVAR_BIT = 0b000010  # bit 1 = 2
GNOMAD_BIT = 0b000100  # bit 2 = 4
DBNSFP_BIT = 0b001000  # bit 3 = 8
GENE_PHENOTYPE_BIT = 0b0010000  # bit 4 = 16
GWAS_BIT = 0b0100000  # bit 5 = 32 (GWAS Catalog — P3-09a)
# F33: CPIC must occupy its own bit, not collide with GENE_PHENOTYPE_BIT — a
# variant covered by gene-phenotype was otherwise indistinguishable from one
# covered by CPIC. Bit 6 = 64 is the next free bit above GWAS.
CPIC_BIT = 0b1000000  # bit 6 = 64 (CPIC/PharmGKB — P3-04a)
ALPHAMISSENSE_BIT = 0b10000000  # bit 7 = 128 (AlphaMissense context — issue #55)

# F22: ClinVar significances that disqualify a variant from inheriting its
# gene's disease label. gene→phenotype is a *gene-level* association, so
# attaching it to a variant the curators call benign falsely implies that
# specific variant causes the gene's disease (e.g. a benign BRCA2 SNP labelled
# "breast-ovarian cancer susceptibility 2"). Variants that are VUS, risk-factor
# or simply unclassified keep the gene context — only a confident benign call
# contradicts it. Stored in normalized (lowercase, underscores→spaces) form;
# match through :func:`_is_benign_significance` so casing/separator variants —
# real ClinVar "Likely_benign", normalized "Likely benign", lowercase fixtures —
# are all caught.
_BENIGN_SIGNIFICANCES: frozenset[str] = frozenset(
    {
        "benign",
        "likely benign",
        "benign/likely benign",
    }
)


def _is_benign_significance(significance: str | None) -> bool:
    """True for a confident ClinVar benign / likely-benign call (F22).

    Case- and separator-insensitive: real ClinVar ``CLNSIG`` is capitalized with
    underscores (``Likely_benign``), but stored and fixture forms vary
    (``Benign``, ``Likely benign``, ``benign``).
    """
    if not significance:
        return False
    return significance.strip().lower().replace("_", " ") in _BENIGN_SIGNIFICANCES


# Maximum concurrent annotation source lookups (VEP, ClinVar, gnomAD, dbNSFP)
# Gene-phenotype runs sequentially after VEP since it depends on gene_symbol.
_MAX_WORKERS = 4


# ── Result dataclass ─────────────────────────────────────────────────────


@dataclass
class AnnotationEngineResult:
    """Statistics from a full annotation engine run."""

    total_variants: int = 0
    vep_matched: int = 0
    # Subset of ``vep_matched`` that resolved via the (chrom, pos) fallback
    # rather than rsid lookup. Populated by Plan §5.1's defense-in-depth path
    # for AncestryDNA-style `kgp*` / internal IDs without rsid mapping.
    vep_coord_fallback_matched: int = 0
    clinvar_matched: int = 0
    gnomad_matched: int = 0
    dbnsfp_matched: int = 0
    alphamissense_matched: int = 0
    gene_phenotype_matched: int = 0
    rows_written: int = 0
    batches_processed: int = 0
    errors: list[str] = field(default_factory=list)
    # Sources that were present-but-unreadable (locked / corrupt / raised on
    # access), keyed by source name → error text (F29). Distinct from a source
    # that is simply not installed: an unreadable source must downgrade the run
    # to ``partial`` rather than be silently treated as absent.
    source_failures: dict[str, str] = field(default_factory=dict)
    # Per-source cumulative timing (seconds) for bottleneck identification (P4-22)
    timing_vep_s: float = 0.0
    timing_clinvar_s: float = 0.0
    timing_gnomad_s: float = 0.0
    timing_dbnsfp_s: float = 0.0
    timing_alphamissense_s: float = 0.0
    timing_gene_phenotype_s: float = 0.0
    timing_merge_s: float = 0.0
    timing_upsert_s: float = 0.0
    # §5.6 coverage telemetry payload (populated at the end of run_annotation).
    # Empty dict for runs that processed zero variants.
    coverage_stats: dict[str, Any] = field(default_factory=dict)

    @property
    def total_matched(self) -> int:
        """Variants matched by at least one source (any bit set)."""
        return self.rows_written


# ── Helpers ───────────────────────────────────────────────────────────────


def _wal_checkpoint(engine: sa.Engine) -> None:
    """Run WAL checkpoint if the engine is file-backed (not in-memory)."""
    url = str(engine.url)
    if url == "sqlite://" or ":memory:" in url:
        return
    with engine.connect() as conn:
        conn.execute(sa.text("PRAGMA wal_checkpoint(TRUNCATE)"))
        conn.commit()


def _delete_all_annotations(sample_engine: sa.Engine) -> None:
    """Delete all rows in annotated_variants for crash recovery.

    Called at the start of each run so partial results from a previous
    crashed run are cleaned up before re-annotating.
    """
    with sample_engine.begin() as conn:
        conn.execute(annotated_variants.delete())


# ── Source lookup adapters ────────────────────────────────────────────────
# Each adapter takes a batch of raw variant rows and an engine, and returns
# a dict mapping rsid -> dict of column values to merge.


def _lookup_vep(
    rsids: list[str],
    raw_by_rsid: dict[str, sa.Row],
    vep_engine: sa.Engine,
) -> dict[str, dict]:
    """Look up VEP annotations for a batch of rsids."""
    from backend.annotation.vep_bundle import lookup_vep_by_rsids

    matches = lookup_vep_by_rsids(rsids, vep_engine)

    results: dict[str, dict] = {}
    for rsid, annot in matches.items():
        results[rsid] = {
            "gene_symbol": annot.gene_symbol,
            "transcript_id": annot.transcript_id,
            "consequence": annot.consequence,
            "hgvs_coding": annot.hgvs_coding,
            "hgvs_protein": annot.hgvs_protein,
            "strand": annot.strand,
            "exon_number": annot.exon_number,
            "intron_number": annot.intron_number,
            "mane_select": annot.mane_select,
        }
    return results


def _lookup_vep_alleles(
    rsids: list[str],
    raw_by_rsid: dict[str, sa.Row],
    matched_rsids: set[str],
    vep_engine: sa.Engine,
) -> dict[str, dict]:
    """Fetch private VEP ref/alt allele identity for already matched variants."""
    if not matched_rsids:
        return {}

    out: dict[str, dict] = {}
    matched_list = [rsid for rsid in rsids if rsid in matched_rsids]

    with vep_engine.connect() as conn:
        for i in range(0, len(matched_list), _VEP_ALLELE_BATCH_SIZE):
            batch = matched_list[i : i + _VEP_ALLELE_BATCH_SIZE]
            placeholders = ", ".join(f":r{j}" for j in range(len(batch)))
            params = {f"r{j}": rsid for j, rsid in enumerate(batch)}
            rows = conn.execute(
                sa.text(
                    "SELECT rsid, ref, alt FROM vep_annotations "  # noqa: S608
                    f"WHERE rsid IN ({placeholders})"
                ),
                params,
            ).fetchall()
            for row in rows:
                out.setdefault(row.rsid, {"_vep_ref": row.ref, "_vep_alt": row.alt})

        remaining = [rsid for rsid in matched_list if rsid not in out]
        for i in range(0, len(remaining), _VEP_ALLELE_BATCH_SIZE):
            batch = remaining[i : i + _VEP_ALLELE_BATCH_SIZE]
            clauses: list[str] = []
            params: dict[str, str | int] = {}
            for j, rsid in enumerate(batch):
                raw = raw_by_rsid.get(rsid)
                if raw is None:
                    continue
                chrom = getattr(raw, "chrom", None)
                pos = getattr(raw, "pos", None)
                if chrom is None or pos is None:
                    continue
                clauses.append(f"(chrom = :c{j} AND pos = :p{j})")
                params[f"c{j}"] = str(chrom)
                params[f"p{j}"] = int(pos)
            if not clauses:
                continue
            rows = conn.execute(
                sa.text(
                    "SELECT chrom, pos, ref, alt FROM vep_annotations "
                    f"WHERE {' OR '.join(clauses)}"
                ),
                params,
            ).fetchall()
            by_pos = {(str(row.chrom), int(row.pos)): row for row in rows}
            for rsid in batch:
                raw = raw_by_rsid.get(rsid)
                if raw is None:
                    continue
                hit = by_pos.get((str(raw.chrom), int(raw.pos)))
                if hit is not None:
                    out[rsid] = {"_vep_ref": hit.ref, "_vep_alt": hit.alt}

    return out


def _lookup_clinvar(
    rsids: list[str],
    raw_by_rsid: dict[str, sa.Row],
    reference_engine: sa.Engine,
) -> dict[str, dict]:
    """Look up ClinVar annotations for a batch of rsids.

    Passes the sample genotypes so multi-allelic sites are scored against the
    allele the sample actually carries (``_pick_clinvar_row``), and keeps the
    matched record's ``ref``/``alt`` so ``_merge_annotations`` can compute
    carriage (zygosity). Without these two the engine is genotype-agnostic.
    """
    from backend.annotation.clinvar import lookup_clinvar_by_rsids

    genotype_by_rsid = {rsid: raw_by_rsid[rsid].genotype for rsid in rsids if rsid in raw_by_rsid}
    matches = lookup_clinvar_by_rsids(rsids, reference_engine, genotype_by_rsid=genotype_by_rsid)

    results: dict[str, dict] = {}
    for rsid, annot in matches.items():
        results[rsid] = {
            "clinvar_significance": annot.clinvar_significance,
            "clinvar_review_stars": annot.clinvar_review_stars,
            "clinvar_accession": annot.clinvar_accession,
            "clinvar_conditions": annot.clinvar_conditions,
            # Carried-allele identity for zygosity computation in the merge.
            "ref": annot.ref,
            "alt": annot.alt,
        }
    return results


def _annot_to_dict(annot: GnomADAnnotation) -> dict:
    """Convert a GnomADAnnotation dataclass to an engine-compatible dict."""
    return {
        "gnomad_af_global": annot.af_global,
        "gnomad_af_afr": annot.af_afr,
        "gnomad_af_amr": annot.af_amr,
        "gnomad_af_eas": annot.af_eas,
        "gnomad_af_eur": annot.af_eur,
        "gnomad_af_fin": annot.af_fin,
        "gnomad_af_sas": annot.af_sas,
        "gnomad_homozygous_count": annot.homozygous_count,
        "gnomad_af_popmax": annot.af_popmax,
        "rare_flag": annot.rare_flag,
        "ultra_rare_flag": annot.ultra_rare_flag,
    }


def _lookup_gnomad(
    rsids: list[str],
    raw_by_rsid: dict[str, sa.Row],
    gnomad_engine: sa.Engine,
) -> dict[str, dict]:
    """Look up gnomAD allele frequencies by rsid with position-based fallback.

    Primary strategy: batch rsid lookup via ``lookup_gnomad_by_rsids``.
    Fallback: for unmatched rsids that have chrom/pos data, attempt
    position-based lookup via ``lookup_gnomad_by_positions`` using the
    composite (chrom, pos, ref, alt) index when ref/alt are available,
    or (chrom, pos) scan otherwise.

    Delegates to :mod:`backend.annotation.gnomad` functions so that
    threshold constants (RARE_AF_THRESHOLD, ULTRA_RARE_AF_THRESHOLD) and
    lookup logic are defined in one place.
    """
    if not rsids:
        return {}

    # Primary: rsid-based lookup
    rsid_matches = lookup_gnomad_by_rsids(rsids, gnomad_engine)

    results: dict[str, dict] = {}
    for rsid, annot in rsid_matches.items():
        results[rsid] = _annot_to_dict(annot)

    # Fallback: position-based lookup for unmatched rsids
    unmatched = [r for r in rsids if r not in results]
    if unmatched:
        # Build position tuples from raw variant data where available
        positions: list[tuple[str, int, str, str]] = []
        pos_to_rsid: dict[tuple[str, int, str, str], str] = {}
        for rsid in unmatched:
            raw = raw_by_rsid.get(rsid)
            if raw is None:
                continue
            chrom = getattr(raw, "chrom", None)
            pos = getattr(raw, "pos", None)
            ref = getattr(raw, "ref", None)
            alt = getattr(raw, "alt", None)
            if chrom and pos and ref and alt:
                key = (chrom, pos, ref, alt)
                positions.append(key)
                pos_to_rsid[key] = rsid

        if positions:
            pos_matches = lookup_gnomad_by_positions(positions, gnomad_engine)
            for key, annot in pos_matches.items():
                rsid = pos_to_rsid[key]
                results[rsid] = _annot_to_dict(annot)

    return results


def _dbnsfp_annot_to_dict(annot: DbNSFPAnnotation) -> dict:
    """Convert a DbNSFPAnnotation dataclass to an engine-compatible dict."""
    return {
        "cadd_phred": annot.cadd_phred,
        "sift_score": annot.sift_score,
        "sift_pred": annot.sift_pred,
        "polyphen2_hsvar_score": annot.polyphen2_hsvar_score,
        "polyphen2_hsvar_pred": annot.polyphen2_hsvar_pred,
        "revel": annot.revel,
        "mutpred2": annot.mutpred2,
        "vest4": annot.vest4,
        "metasvm": annot.metasvm,
        "metalr": annot.metalr,
        "gerp_rs": annot.gerp_rs,
        "phylop": annot.phylop,
        "mpc": annot.mpc,
        "primateai": annot.primateai,
        "deleterious_count": annot.deleterious_count,
        "deleterious_total_assessed": annot.deleterious_total_assessed,
        "ensemble_pathogenic": is_ensemble_pathogenic(annot),
    }


def _lookup_dbnsfp(
    rsids: list[str],
    raw_by_rsid: dict[str, sa.Row],
    dbnsfp_engine: sa.Engine,
) -> dict[str, dict]:
    """Look up dbNSFP in-silico prediction scores by rsid with position fallback.

    Primary strategy: batch rsid lookup via ``lookup_dbnsfp_by_rsids``.
    Fallback: for unmatched rsids that have chrom/pos/ref/alt data, attempt
    position-based lookup via ``lookup_dbnsfp_by_positions`` using the
    composite ``(chrom, pos, ref, alt)`` primary key.

    Delegates to :mod:`backend.annotation.dbnsfp` functions so that
    lookup logic, score parsing, and deleterious count computation are
    defined in one place.
    """
    if not rsids:
        return {}

    # Primary: rsid-based lookup. Pass the genotypes so a multi-allelic site
    # resolves to the carried-ALT row, not an arbitrary one (F11) — mirrors the
    # ClinVar carried-allele selection.
    genotype_by_rsid = {rsid: raw_by_rsid[rsid].genotype for rsid in rsids if rsid in raw_by_rsid}
    rsid_matches = lookup_dbnsfp_by_rsids(rsids, dbnsfp_engine, genotype_by_rsid=genotype_by_rsid)

    results: dict[str, dict] = {}
    for rsid, annot in rsid_matches.items():
        results[rsid] = _dbnsfp_annot_to_dict(annot)

    # Fallback: position-based lookup for unmatched rsids
    unmatched = [r for r in rsids if r not in results]
    if unmatched:
        positions: list[tuple[str, int, str, str]] = []
        pos_to_rsid: dict[tuple[str, int, str, str], str] = {}
        for rsid in unmatched:
            raw = raw_by_rsid.get(rsid)
            if raw is None:
                continue
            chrom = getattr(raw, "chrom", None)
            pos = getattr(raw, "pos", None)
            ref = getattr(raw, "ref", None)
            alt = getattr(raw, "alt", None)
            if chrom and pos and ref and alt:
                key = (chrom, pos, ref, alt)
                positions.append(key)
                pos_to_rsid[key] = rsid

        if positions:
            # The chip pipeline is GRCh37 but dbnsfp.db is GRCh38 (F35), so this
            # position fallback is a cross-build join: lookup_dbnsfp_by_positions
            # skips it (default source_build=GRCh37) and the live match stays
            # rsid-based above. Chip raw rows carry no ref/alt, so `positions` is
            # empty here in practice (F32) — the guard covers future VCF inputs.
            pos_matches = lookup_dbnsfp_by_positions(positions, dbnsfp_engine)
            for key, annot in pos_matches.items():
                rsid = pos_to_rsid[key]
                results[rsid] = _dbnsfp_annot_to_dict(annot)

    return results


def _lookup_alphamissense(
    rsids: list[str],
    raw_by_rsid: dict[str, sa.Row],
    vep_data: dict[str, dict],
    clinvar_data: dict[str, dict],
    alphamissense_engine: sa.Engine,
) -> dict[str, dict]:
    """Look up AlphaMissense context for missense variants by position/allele.

    The AlphaMissense database is keyed by GRCh37 ``(chrom, pos, ref, alt)``.
    Chip rows do not always carry ref/alt, so this adapter derives allele
    identity from ClinVar when available and otherwise from the matched VEP row.
    Only missense VEP consequences are queried; AlphaMissense is not meaningful
    for non-missense changes.
    """
    if not rsids:
        return {}

    positions: list[tuple[str, int, str, str]] = []
    key_to_rsids: dict[tuple[str, int, str, str], list[str]] = {}

    for rsid in rsids:
        raw = raw_by_rsid.get(rsid)
        vep = vep_data.get(rsid)
        if raw is None or not vep or not is_missense_consequence(vep.get("consequence")):
            continue

        chrom = getattr(raw, "chrom", None)
        pos = getattr(raw, "pos", None)
        if chrom is None or pos is None:
            continue

        clinvar = clinvar_data.get(rsid, {})
        ref = clinvar.get("ref") or vep.get("_vep_ref") or getattr(raw, "ref", None)
        alt = clinvar.get("alt") or vep.get("_vep_alt") or getattr(raw, "alt", None)
        if not ref or not alt:
            continue

        try:
            key = (str(chrom), int(pos), str(ref), str(alt))
        except (TypeError, ValueError):
            continue
        if key not in key_to_rsids:
            positions.append(key)
        key_to_rsids.setdefault(key, []).append(rsid)

    hits = lookup_alphamissense_by_positions(positions, alphamissense_engine)

    results: dict[str, dict] = {}
    for key, hit in hits.items():
        for rsid in key_to_rsids.get(key, []):
            results[rsid] = {
                "alphamissense_pathogenicity": hit.get("am_pathogenicity"),
                "alphamissense_class": hit.get("am_class"),
            }
    return results


def _lookup_gene_phenotype(
    vep_data: dict[str, dict],
    reference_engine: sa.Engine,
) -> dict[str, dict]:
    """Look up gene-phenotype annotations for variants that have VEP gene symbols.

    This adapter collects unique gene symbols from VEP results, queries the
    gene_phenotype table via :func:`lookup_gene_phenotypes`, and maps results
    back to rsids.  Each rsid gets the first (most relevant) phenotype record
    for its gene — typically the primary disease association.

    Must run *after* VEP lookup since it depends on gene_symbol assignments.
    """
    import json as _json

    from backend.annotation.mondo_hpo import lookup_gene_phenotypes

    if not vep_data:
        return {}

    # Collect unique gene symbols and map rsid -> gene_symbol
    rsid_to_gene: dict[str, str] = {}
    unique_genes: set[str] = set()
    for rsid, vep_dict in vep_data.items():
        gene = vep_dict.get("gene_symbol")
        if gene:
            rsid_to_gene[rsid] = gene
            unique_genes.add(gene)

    if not unique_genes:
        return {}

    # Batch lookup
    gene_pheno_map = lookup_gene_phenotypes(list(unique_genes), reference_engine)

    # Map back to rsids — use the first record per gene (primary association)
    results: dict[str, dict] = {}
    for rsid, gene in rsid_to_gene.items():
        annots = gene_pheno_map.get(gene)
        if annots:
            primary = annots[0]
            results[rsid] = {
                "disease_name": primary.disease_name,
                "disease_id": primary.disease_id,
                "phenotype_source": primary.source,
                "hpo_terms": _json.dumps(primary.hpo_terms) if primary.hpo_terms else None,
                "inheritance_pattern": primary.inheritance,
            }

    return results


# ── Merge + bitmask ──────────────────────────────────────────────────────


def _rekey_to_original(
    data_by_query: dict[str, dict],
    lookup_key: dict[str, str],
) -> dict[str, dict]:
    """Re-key source-lookup results from the queried rsid back to the sample's.

    Source lookups are issued under the *current* (merge-resolved) rsid, so a
    deprecated chip rsid recovers the record filed under its replacement (F18).
    Results come back keyed by the current rsid; map each back to the original
    sample rsid so the annotated row stays keyed by what the chip reported.
    """
    out: dict[str, dict] = {}
    for original, query in lookup_key.items():
        match = data_by_query.get(query)
        if match is not None:
            out[original] = match
    return out


def _merge_annotations(
    raw_rows: list[sa.Row],
    vep_data: dict[str, dict],
    clinvar_data: dict[str, dict],
    gnomad_data: dict[str, dict],
    dbnsfp_data: dict[str, dict],
    gene_phenotype_data: dict[str, dict] | None = None,
    alphamissense_data: dict[str, dict] | None = None,
    merged_rsid_map: dict[str, str] | None = None,
) -> list[dict]:
    """Merge all annotation sources into upsert-ready dicts.

    For each raw variant, merges columns from whichever sources matched
    and computes the ``annotation_coverage`` bitmask. ``merged_rsid_map`` (F18)
    maps a deprecated sample rsid to the current rsid its annotations were
    recovered under; it is recorded in ``dbsnp_rsid_current`` (no coverage bit —
    rsid-merge resolution is a cross-reference, not one of the six sources).
    """
    if gene_phenotype_data is None:
        gene_phenotype_data = {}
    if alphamissense_data is None:
        alphamissense_data = {}
    if merged_rsid_map is None:
        merged_rsid_map = {}

    merged: list[dict] = []

    for raw in raw_rows:
        rsid = raw.rsid
        bitmask = 0

        row_data: dict = {
            "rsid": rsid,
            "chrom": raw.chrom,
            "pos": raw.pos,
            "genotype": raw.genotype,
        }

        current_rsid = merged_rsid_map.get(rsid)
        if current_rsid:
            row_data["dbsnp_rsid_current"] = current_rsid

        if rsid in vep_data:
            row_data.update(vep_data[rsid])
            bitmask |= VEP_BIT

        if rsid in clinvar_data:
            row_data.update(clinvar_data[rsid])
            bitmask |= CLINVAR_BIT

        if rsid in gnomad_data:
            row_data.update(gnomad_data[rsid])
            bitmask |= GNOMAD_BIT

        if rsid in dbnsfp_data:
            row_data.update(dbnsfp_data[rsid])
            bitmask |= DBNSFP_BIT

        if rsid in alphamissense_data:
            row_data.update(alphamissense_data[rsid])
            bitmask |= ALPHAMISSENSE_BIT

        # F22: gate the gene-level disease label on the variant *not* being a
        # confident benign call. ClinVar is merged above, so ``clinvar_significance``
        # is already in ``row_data`` for this decision. A benign variant gets no
        # disease label and no GENE_PHENOTYPE_BIT (the stored row carries no
        # gene-phenotype data, so the coverage bit must stay clear too).
        if rsid in gene_phenotype_data and not _is_benign_significance(
            row_data.get("clinvar_significance")
        ):
            row_data.update(gene_phenotype_data[rsid])
            bitmask |= GENE_PHENOTYPE_BIT

        # Carriage: a genotyping chip reports a call at every probe regardless
        # of whether the person carries the variant, so annotate against the
        # allele actually carried. ``ref``/``alt`` come from the source that
        # supplied allele identity (ClinVar today). ``classify_zygosity``
        # returns None when carriage is indeterminate (indel / no-call /
        # strand-ambiguous), in which case zygosity stays NULL.
        ref = row_data.get("ref")
        alt = row_data.get("alt")
        if ref is not None and alt is not None:
            row_data["zygosity"] = classify_zygosity(raw.genotype, ref, alt)

        # Always emit a row, even when no source matched (F36). An explicit
        # ``annotation_coverage = 0`` marker distinguishes a variant that was
        # *processed but had no source data* from one that *never entered the
        # pipeline* (the latter has no row at all). Previously the ~465
        # per-chip unmatched variants were dropped silently, so the two cases
        # were indistinguishable and raw↔annotated reconciliation was impossible.
        row_data["annotation_coverage"] = bitmask
        merged.append(row_data)

    return merged


# ── Ensemble pathogenicity (P2-13) ───────────────────────────────────────


def apply_ensemble_pathogenic(merged: list[dict]) -> None:
    """Set ``ensemble_pathogenic`` flag on merged variant dicts.

    For variants that already have ``ensemble_pathogenic`` set (e.g. from
    ``_dbnsfp_annot_to_dict``), this is a no-op.  For any variant carrying the
    vote counts but no ``ensemble_pathogenic`` key, the flag is computed here via
    the k-of-present rule (F24/F25), which needs both ``deleterious_count`` and
    ``deleterious_total_assessed``.

    Mutates *merged* in place (same pattern as ``apply_evidence_conflicts``).
    """
    for v in merged:
        if "ensemble_pathogenic" in v:
            continue
        dc = v.get("deleterious_count")
        ta = v.get("deleterious_total_assessed")
        if dc is not None and ta is not None:
            v["ensemble_pathogenic"] = is_ensemble_pathogenic_from_counts(dc, ta)


# ── Bulk upsert ──────────────────────────────────────────────────────────

_UPSERT_COLUMNS = [
    # Carriage (allele identity + zygosity vs the carried allele)
    "ref",
    "alt",
    "zygosity",
    # VEP
    "gene_symbol",
    "transcript_id",
    "consequence",
    "hgvs_coding",
    "hgvs_protein",
    "strand",
    "exon_number",
    "intron_number",
    "mane_select",
    # ClinVar
    "clinvar_significance",
    "clinvar_review_stars",
    "clinvar_accession",
    "clinvar_conditions",
    # dbSNP merge reconciliation (F18) — current rsid a deprecated id resolved to
    "dbsnp_rsid_current",
    # gnomAD
    "gnomad_af_global",
    "gnomad_af_afr",
    "gnomad_af_amr",
    "gnomad_af_eas",
    "gnomad_af_eur",
    "gnomad_af_fin",
    "gnomad_af_sas",
    "gnomad_af_popmax",
    "gnomad_homozygous_count",
    "rare_flag",
    "ultra_rare_flag",
    # dbNSFP
    "cadd_phred",
    "sift_score",
    "sift_pred",
    "polyphen2_hsvar_score",
    "polyphen2_hsvar_pred",
    "revel",
    "mutpred2",
    "vest4",
    "metasvm",
    "metalr",
    "gerp_rs",
    "phylop",
    "mpc",
    "primateai",
    "deleterious_count",
    "deleterious_total_assessed",
    "ensemble_pathogenic",
    # AlphaMissense
    "alphamissense_pathogenicity",
    "alphamissense_class",
    # Gene-phenotype
    "disease_name",
    "disease_id",
    "phenotype_source",
    "hpo_terms",
    "inheritance_pattern",
    # Evidence conflict
    "evidence_conflict",
]


# ── Atomic-swap staging (F28) ─────────────────────────────────────────────
# Crash recovery used to delete ``annotated_variants`` up front and re-annotate
# in place, so a crash mid-run left the table empty — the prior good annotation
# gone, with no transaction protecting it. Instead we annotate into a staging
# clone and swap it into ``annotated_variants`` in a single transaction at the
# end: a crash before the swap leaves the prior annotation untouched, and the
# swap itself is all-or-nothing.
_STAGING_NAME = "annotated_variants_staging"
_STAGING_METADATA = sa.MetaData()
annotated_variants_staging = annotated_variants.to_metadata(_STAGING_METADATA, name=_STAGING_NAME)
# Drop the copied secondary indexes: SQLite index names are database-global so
# they would collide with the live table's, and a write-once staging table
# needs no read indexes. The ``rsid`` primary key (and its ON CONFLICT support)
# is part of the table definition and is preserved.
annotated_variants_staging.indexes.clear()


def _reset_staging_table(sample_engine: sa.Engine) -> None:
    """Drop and recreate an empty staging table (clears any crashed-run remnant)."""
    with sample_engine.begin() as conn:
        annotated_variants_staging.drop(conn, checkfirst=True)
        annotated_variants_staging.create(conn)


def _swap_staging_into_place(sample_engine: sa.Engine) -> None:
    """Atomically replace ``annotated_variants`` with the staged annotation.

    The delete-then-insert-select runs in a single transaction, so a crash
    during the swap rolls back and leaves the prior good annotation intact; a
    crash *before* the swap never touches the live table at all (F28).
    """
    cols = ", ".join(c.name for c in annotated_variants.c)
    with sample_engine.begin() as conn:
        conn.execute(annotated_variants.delete())
        conn.execute(
            sa.text(f"INSERT INTO annotated_variants ({cols}) SELECT {cols} FROM {_STAGING_NAME}")
        )
    with sample_engine.begin() as conn:
        annotated_variants_staging.drop(conn, checkfirst=True)


def _bulk_upsert(
    sample_engine: sa.Engine,
    rows: list[dict],
    target: sa.Table = annotated_variants,
) -> int:
    """Upsert merged annotation rows into *target* (live table or staging).

    Uses SQLite INSERT ... ON CONFLICT DO UPDATE to merge columns.
    The annotation_coverage bitmask is ORed with existing values.

    Returns:
        Number of rows written.
    """
    if not rows:
        return 0

    written = 0

    # Normalise all rows to the same set of keys so multi-row INSERT works.
    all_keys = {"rsid", "chrom", "pos", "genotype", "annotation_coverage"}
    all_keys.update(_UPSERT_COLUMNS)

    # Compute batch size using the detected SQLITE_MAX_VARIABLE_NUMBER.
    # macOS system SQLite defaults to 999; Linux builds typically allow 32766.
    from backend.annotation.sqlite_limits import SQLITE_MAX_VARIABLE_NUMBER

    num_cols = len(all_keys)
    upsert_batch_size = max(1, SQLITE_MAX_VARIABLE_NUMBER // num_cols)
    normalised = [{k: row.get(k) for k in all_keys} for row in rows]

    with sample_engine.begin() as conn:
        for i in range(0, len(normalised), upsert_batch_size):
            batch = normalised[i : i + upsert_batch_size]

            stmt = sqlite_insert(target).values(batch)

            # Build the SET clause: update all annotation columns from incoming row
            set_clause: dict = {}
            for col in _UPSERT_COLUMNS:
                set_clause[col] = getattr(stmt.excluded, col)

            # OR the bitmask into existing coverage
            set_clause["annotation_coverage"] = sa.case(
                (
                    target.c.annotation_coverage.is_(None),
                    stmt.excluded.annotation_coverage,
                ),
                else_=(target.c.annotation_coverage.op("|")(stmt.excluded.annotation_coverage)),
            )

            stmt = stmt.on_conflict_do_update(
                index_elements=["rsid"],
                set_=set_clause,
            )
            conn.execute(stmt)
            written += len(batch)

    return written


# ── VEP coord-fallback (Plan §5.1) ──────────────────────────────────────


def _vep_coord_fallback(
    batch_rsids: list[str],
    raw_by_rsid: dict[str, sa.Row],
    vep_engine: sa.Engine,
    vep_data: dict[str, dict],
    result: AnnotationEngineResult,
) -> int:
    """Resolve unmatched rsids via (chrom, pos) lookup in the VEP bundle.

    Mutates ``vep_data`` in place with any coord-matched annotations and adds
    coord-fallback wall-clock time onto ``result.timing_vep_s``. Returns the
    number of variants resolved here so the caller can roll the count into
    ``result.vep_coord_fallback_matched``.

    The plan calls this "defense-in-depth" for AncestryDNA `kgp*` IDs: the
    rsid string is internal-only, but the (chrom, pos) tuple still hits a
    bundle row carrying a different rsid.
    """
    from backend.annotation.vep_bundle import lookup_vep_by_positions

    unmatched_rsids = [r for r in batch_rsids if r not in vep_data]
    if not unmatched_rsids:
        return 0

    positions: list[tuple[str, int, str]] = []
    for rsid in unmatched_rsids:
        raw = raw_by_rsid.get(rsid)
        if raw is None:
            continue
        chrom = getattr(raw, "chrom", None)
        pos = getattr(raw, "pos", None)
        if chrom and pos is not None:
            try:
                positions.append((chrom, int(pos), rsid))
            except (TypeError, ValueError):
                continue

    if not positions:
        return 0

    t0 = time.perf_counter()
    matches = lookup_vep_by_positions(positions, vep_engine)
    result.timing_vep_s += time.perf_counter() - t0

    for sample_rsid, annot in matches.items():
        vep_data[sample_rsid] = {
            "gene_symbol": annot.gene_symbol,
            "transcript_id": annot.transcript_id,
            "consequence": annot.consequence,
            "hgvs_coding": annot.hgvs_coding,
            "hgvs_protein": annot.hgvs_protein,
            "strand": annot.strand,
            "exon_number": annot.exon_number,
            "intron_number": annot.intron_number,
            "mane_select": annot.mane_select,
        }
    return len(matches)


# ── Timed lookup wrapper (P4-22) ────────────────────────────────────────


def _timed_lookup(
    fn: Callable,
    *args: object,
    source_timings: dict[str, float],
    source_name: str,
) -> dict:
    """Execute a source lookup function and record wall-clock time."""
    t0 = time.perf_counter()
    res = fn(*args)
    source_timings[source_name] = time.perf_counter() - t0
    return res


# ── Coverage telemetry (Plan §5.6) ───────────────────────────────────────


def _read_bundle_version(registry: DBRegistry) -> str | None:
    """Read `vep_bundle.version` from the reference DB's `database_versions`.

    Returns ``None`` when the row is missing or the reference DB is
    unavailable — telemetry collection must never abort the engine run.
    """
    try:
        reference_engine = registry.reference_engine
    except Exception:
        logger.debug("coverage_stats_reference_engine_unavailable", exc_info=True)
        return None
    try:
        with reference_engine.connect() as conn:
            row = conn.execute(
                sa.select(database_versions.c.version).where(
                    database_versions.c.db_name == "vep_bundle"
                )
            ).fetchone()
    except Exception:
        logger.debug("coverage_stats_bundle_version_query_failed", exc_info=True)
        return None
    return row.version if row is not None else None


def _read_sample_file_format(sample_engine: sa.Engine) -> str | None:
    """Read `file_format` from the per-sample `sample_metadata` row."""
    try:
        with sample_engine.connect() as conn:
            row = conn.execute(
                sa.select(sample_metadata_table.c.file_format).where(
                    sample_metadata_table.c.id == 1
                )
            ).fetchone()
    except Exception:
        logger.debug("coverage_stats_file_format_query_failed", exc_info=True)
        return None
    return row.file_format if row is not None else None


# Plan §10.5 step 5: merged samples carry this canonical ``file_format`` so
# every reader (dashboard, variant table, coverage telemetry below) can branch
# on it without re-deriving from per-row state. Duplicated from
# ``backend/services/sample_merge.py::_MERGED_FILE_FORMAT`` to keep the engine
# free of services-layer imports.
_MERGED_FILE_FORMAT = "merged_v1"

# Plan §10.4(b): ``raw_variants.source`` is populated only on merged samples;
# unmerged samples carry the empty-string default. The three-key by_source
# shape always emits every slot — bucket counts of zero are still meaningful
# (Plan §5.6 / §15.1 MRG-09b: ``S1`` / ``S2`` / ``both`` is the canonical
# key set for merged-sample telemetry, parity with the
# ``merge_provenance.concordance_summary`` ``unique_S1`` / ``unique_S2``
# suffix tokens).
_MERGED_SOURCE_KEYS: tuple[str, ...] = ("S1", "S2", "both")


def _zero_source_bucket() -> dict[str, int]:
    return {
        "vep_bundle_rsid_hits": 0,
        "vep_bundle_coord_fallback_hits": 0,
        "vep_misses": 0,
    }


def _build_coverage_stats(
    sample_engine: sa.Engine,
    registry: DBRegistry,
    *,
    total_variants: int,
    vep_rsid_hits: int,
    vep_coord_fallback_hits: int,
    source_counters: dict[str, dict[str, int]] | None = None,
) -> dict[str, Any]:
    """Compose the Plan §5.6 coverage telemetry payload.

    Top-level rollup mirrors the cross-source totals (Plan §5.6: "the top-level
    rollup is the sum across all ``by_source`` keys"). ``by_source`` keys
    depend on whether the sample is a merge artefact:

    * **Unmerged** (file_format ≠ ``merged_v1``) — single-key on the vendor
      derived from ``sample_metadata.file_format`` (``"23andme_v5" → "23andme"``,
      ``"ancestrydna_v2.0" → "ancestrydna"``). ``source_counters`` is ignored
      because every row carries the empty-string ``source`` default.
    * **Merged** (file_format == ``merged_v1``) — three-key uppercase
      ``"S1"`` / ``"S2"`` / ``"both"`` populated from ``source_counters``
      (which the engine accumulated per-batch from ``raw_variants.source``).
      Every slot is emitted even when its bucket count is zero so downstream
      consumers can read a stable shape. The keys match the
      ``raw_variants.source`` enum (Plan §10.4b) and the suffix tokens on
      ``merge_provenance.concordance_summary.unique_S1`` / ``unique_S2``
      (Plan §15.1 MRG-09b).
    """
    vep_misses = max(total_variants - vep_rsid_hits - vep_coord_fallback_hits, 0)

    bundle_version = _read_bundle_version(registry)
    file_format = _read_sample_file_format(sample_engine)

    if file_format == _MERGED_FILE_FORMAT:
        provided = source_counters or {}
        by_source = {key: provided.get(key, _zero_source_bucket()) for key in _MERGED_SOURCE_KEYS}
    else:
        vendor = file_format.split("_", 1)[0].lower() if file_format else "unknown"
        by_source = {
            vendor: {
                "vep_bundle_rsid_hits": vep_rsid_hits,
                "vep_bundle_coord_fallback_hits": vep_coord_fallback_hits,
                "vep_misses": vep_misses,
            }
        }

    return {
        "bundle_version": bundle_version,
        "total_variants": total_variants,
        "vep_bundle_rsid_hits": vep_rsid_hits,
        "vep_bundle_coord_fallback_hits": vep_coord_fallback_hits,
        "vep_misses": vep_misses,
        "by_source": by_source,
    }


# ── Engine availability checks ───────────────────────────────────────────


def _check_engine_available(
    engine_getter: Callable,
    name: str,
    result: AnnotationEngineResult | None = None,
) -> sa.Engine | None:
    """Resolve a source engine, distinguishing *absent* from *unreadable* (F29).

    Returns the engine on success, ``None`` otherwise. A ``FileNotFoundError``
    means the source is simply not installed — unavailable, but not a failure
    (graceful degradation). Any other error (locked, corrupt, permission, a
    registry property that raised) means the source is present-but-unreadable:
    it is recorded in ``result.source_failures`` so the run can be reported as
    ``partial`` instead of silently dropping the source and claiming success.
    """
    try:
        engine = engine_getter()
        # Quick connectivity check
        with engine.connect() as conn:
            conn.execute(sa.text("SELECT 1"))
        return engine
    except FileNotFoundError:
        logger.info("annotation_source_unavailable", extra={"source": name})
        return None
    except Exception as exc:
        logger.warning(
            "annotation_source_failed",
            extra={"source": name, "error": str(exc)},
        )
        if result is not None:
            result.source_failures[name] = str(exc)
        return None


def _registry_supports_engine(registry: DBRegistry, attr: str) -> bool:
    """Return True when a registry-like object explicitly exposes an engine."""
    return attr in dir(type(registry)) or attr in dir(registry)


def _resolve_alphamissense_engine(registry: DBRegistry) -> sa.Engine:
    """Resolve the optional AlphaMissense engine without creating an empty DB file."""
    if not _registry_supports_engine(registry, "alphamissense_engine"):
        raise FileNotFoundError("alphamissense engine is not configured")

    settings = getattr(registry, "settings", None)
    db_path = getattr(settings, "alphamissense_db_path", None)
    if isinstance(db_path, Path) and not db_path.exists():
        raise FileNotFoundError(f"AlphaMissense database not installed: {db_path}")

    return registry.alphamissense_engine


# ── Main entry point ─────────────────────────────────────────────────────


def run_annotation(
    sample_engine: sa.Engine,
    registry: DBRegistry,
    *,
    progress_callback: Callable[[int, int], None] | None = None,
    batch_size: int = ENGINE_BATCH_SIZE,
) -> AnnotationEngineResult:
    """Run the full annotation engine on a sample.

    Coordinates VEP bundle, ClinVar, gnomAD, dbNSFP, AlphaMissense, and
    gene-phenotype lookups, merges results, computes the annotation_coverage
    bitmask, and bulk-upserts into annotated_variants.

    Crash recovery: deletes all existing annotations before starting,
    then re-annotates from scratch.

    Args:
        sample_engine: SQLAlchemy engine for the per-sample database.
        registry: DBRegistry providing engines for all annotation sources.
        progress_callback: Optional callable ``(variants_done, total)``
            for SSE progress reporting.
        batch_size: Number of variants per batch (default 10k).

    Returns:
        :class:`AnnotationEngineResult` with match statistics.
    """
    result = AnnotationEngineResult()

    # 1. Read all raw variants. ``source`` (Plan §10.4b) keys the merged-sample
    # coverage telemetry under ``S1`` / ``S2`` / ``both`` (Plan §5.6 / §15.1
    # MRG-09b); unmerged samples carry the empty-string default and the
    # downstream telemetry branch ignores it.
    with sample_engine.connect() as conn:
        raw_rows = conn.execute(
            sa.select(
                raw_variants.c.rsid,
                raw_variants.c.chrom,
                raw_variants.c.pos,
                raw_variants.c.genotype,
                raw_variants.c.source,
            )
        ).fetchall()

    result.total_variants = len(raw_rows)
    if not raw_rows:
        return result

    # 2. Crash recovery (F28): annotate into a fresh staging table, not in place.
    # The prior good annotation in ``annotated_variants`` is left untouched until
    # the atomic swap at the very end, so a crash mid-run loses nothing.
    _reset_staging_table(sample_engine)

    # 3. Detect available annotation sources
    vep_engine = _check_engine_available(lambda: registry.vep_engine, "vep", result)
    reference_engine = registry.reference_engine  # always available
    gnomad_engine = _check_engine_available(lambda: registry.gnomad_engine, "gnomad", result)
    dbnsfp_engine = _check_engine_available(lambda: registry.dbnsfp_engine, "dbnsfp", result)
    alphamissense_engine = _check_engine_available(
        lambda: _resolve_alphamissense_engine(registry),
        "alphamissense",
        result,
    )

    # 3b. dbSNP merge reconciliation (F18): a chip may carry a deprecated rsid
    # whose ClinVar/gnomAD/dbNSFP record now lives under its current rsid. Build
    # old→current once so every per-source lookup queries the current id and the
    # recovered record is re-keyed back to the rsid the chip actually reported.
    from backend.annotation.dbsnp import lookup_merged_rsids

    merge_records = lookup_merged_rsids([r.rsid for r in raw_rows], reference_engine)
    current_by_old = {
        old: rec.current_rsid for old, rec in merge_records.items() if rec.current_rsid
    }

    # 4. Process in batches
    # Reuse a single ThreadPoolExecutor across all batches to avoid
    # repeated thread creation/teardown overhead (P4-22 optimization).
    total_written = 0

    # Per-source bucket counts for the Plan §5.6 ``by_source`` payload. Keys
    # are ``raw_variants.source`` values verbatim (``S1`` / ``S2`` / ``both``
    # on merged samples; empty string on unmerged). The merged-sample branch
    # in ``_build_coverage_stats`` reads this dict; the unmerged branch
    # ignores it and re-derives the single vendor bucket from the rollup
    # counts so existing test contracts stay unchanged.
    source_counters: dict[str, dict[str, int]] = {}

    def _bump_source(source: str, key: str) -> None:
        bucket = source_counters.get(source)
        if bucket is None:
            bucket = _zero_source_bucket()
            source_counters[source] = bucket
        bucket[key] += 1

    with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as executor:
        for batch_start in range(0, len(raw_rows), batch_size):
            batch_rows = raw_rows[batch_start : batch_start + batch_size]
            batch_rsids = [r.rsid for r in batch_rows]
            raw_by_rsid = {r.rsid: r for r in batch_rows}

            # Resolve deprecated rsids to their current id for the source lookups
            # (F18), keeping ``lookup_key`` so results re-key to the original
            # sample rsid. ``raw_by_query`` carries the genotype/position under
            # the queried id; a directly-genotyped current rsid keeps its own row
            # (self-map wins over a merged old→current contribution).
            lookup_key = {r: current_by_old.get(r, r) for r in batch_rsids}
            query_rsids = list(dict.fromkeys(lookup_key.values()))
            raw_by_query: dict[str, sa.Row] = {}
            for r in batch_rsids:
                q = lookup_key[r]
                if q == r or q not in raw_by_query:
                    raw_by_query[q] = raw_by_rsid[r]

            # 5. Concurrent lookups across annotation sources
            vep_data: dict[str, dict] = {}
            clinvar_data: dict[str, dict] = {}
            gnomad_data: dict[str, dict] = {}
            dbnsfp_data: dict[str, dict] = {}
            alphamissense_data: dict[str, dict] = {}

            # Per-source timing: each future records its own wall-clock time.
            # Since sources run concurrently, we track per-source time for
            # bottleneck identification (P4-22).
            futures: dict = {}
            source_timings: dict[str, float] = {}

            if vep_engine is not None:
                futures[
                    executor.submit(
                        _timed_lookup,
                        _lookup_vep,
                        query_rsids,
                        raw_by_query,
                        vep_engine,
                        source_timings=source_timings,
                        source_name="vep",
                    )
                ] = "vep"

            futures[
                executor.submit(
                    _timed_lookup,
                    _lookup_clinvar,
                    query_rsids,
                    raw_by_query,
                    reference_engine,
                    source_timings=source_timings,
                    source_name="clinvar",
                )
            ] = "clinvar"

            if gnomad_engine is not None:
                futures[
                    executor.submit(
                        _timed_lookup,
                        _lookup_gnomad,
                        query_rsids,
                        raw_by_query,
                        gnomad_engine,
                        source_timings=source_timings,
                        source_name="gnomad",
                    )
                ] = "gnomad"

            if dbnsfp_engine is not None:
                futures[
                    executor.submit(
                        _timed_lookup,
                        _lookup_dbnsfp,
                        query_rsids,
                        raw_by_query,
                        dbnsfp_engine,
                        source_timings=source_timings,
                        source_name="dbnsfp",
                    )
                ] = "dbnsfp"

            for future in as_completed(futures):
                source = futures[future]
                try:
                    data = future.result()
                    if source == "vep":
                        vep_data = data
                    elif source == "clinvar":
                        clinvar_data = data
                    elif source == "gnomad":
                        gnomad_data = data
                    elif source == "dbnsfp":
                        dbnsfp_data = data
                except Exception as exc:
                    msg = f"{source} lookup failed: {exc}"
                    logger.warning(
                        "annotation_source_error",
                        extra={"source": source, "error": str(exc)},
                    )
                    result.errors.append(msg)

            # Re-key merge-resolved lookups back to the sample's original rsids
            # (F18) before any downstream use (coord fallback, telemetry, merge).
            # A no-op when nothing in this batch was a deprecated rsid.
            if current_by_old:
                vep_data = _rekey_to_original(vep_data, lookup_key)
                clinvar_data = _rekey_to_original(clinvar_data, lookup_key)
                gnomad_data = _rekey_to_original(gnomad_data, lookup_key)
                dbnsfp_data = _rekey_to_original(dbnsfp_data, lookup_key)

            # Accumulate per-source timings
            result.timing_vep_s += source_timings.get("vep", 0.0)
            result.timing_clinvar_s += source_timings.get("clinvar", 0.0)
            result.timing_gnomad_s += source_timings.get("gnomad", 0.0)
            result.timing_dbnsfp_s += source_timings.get("dbnsfp", 0.0)

            # Snapshot rsid-hit keys before the coord-fallback augments
            # ``vep_data`` so the per-sample-source bucket counter (Plan §5.6
            # / §15.1 MRG-09b) can separate rsid hits from coord-fallback hits.
            vep_rsid_hit_keys = set(vep_data.keys())

            # 5a. VEP coord-fallback for unmatched rsids (Plan §5.1, §5.6).
            # AncestryDNA's `kgp*` / internal IDs have a known coordinate but
            # no rsid mapping in the bundle; they resolve here. Runs after the
            # concurrent futures so gene-phenotype sees the augmented `vep_data`.
            # Mirrors the future-result error trap: a failing coord lookup
            # (e.g. a partially-built bundle with no `vep_annotations` table)
            # must not abort the engine — it logs and continues.
            if vep_engine is not None:
                try:
                    vep_coord_count = _vep_coord_fallback(
                        batch_rsids,
                        raw_by_rsid,
                        vep_engine,
                        vep_data,
                        result,
                    )
                except Exception as exc:
                    msg = f"vep coord-fallback lookup failed: {exc}"
                    logger.warning(
                        "annotation_source_error",
                        extra={"source": "vep_coord_fallback", "error": str(exc)},
                    )
                    result.errors.append(msg)
                else:
                    result.vep_coord_fallback_matched += vep_coord_count

            if vep_engine is not None and vep_data:
                try:
                    for rsid, alleles in _lookup_vep_alleles(
                        batch_rsids,
                        raw_by_rsid,
                        set(vep_data),
                        vep_engine,
                    ).items():
                        vep_data[rsid].update(alleles)
                except Exception as exc:
                    msg = f"vep allele lookup failed: {exc}"
                    logger.warning(
                        "annotation_source_error",
                        extra={"source": "vep_alleles", "error": str(exc)},
                    )
                    result.errors.append(msg)

            # 5b. AlphaMissense context lookup (depends on VEP/ClinVar allele identity).
            # It is context-only and never contributes an ACMG PP3/BP4 vote.
            if alphamissense_engine is not None and vep_data:
                try:
                    t_am = time.perf_counter()
                    alphamissense_data = _lookup_alphamissense(
                        batch_rsids,
                        raw_by_rsid,
                        vep_data,
                        clinvar_data,
                        alphamissense_engine,
                    )
                    result.timing_alphamissense_s += time.perf_counter() - t_am
                except Exception as exc:
                    msg = f"alphamissense lookup failed: {exc}"
                    logger.warning(
                        "annotation_source_error",
                        extra={"source": "alphamissense", "error": str(exc)},
                    )
                    result.errors.append(msg)

            # Per-source bucket attribution (Plan §5.6 / §15.1 MRG-09b).
            # For merged samples ``raw.source`` is ``"S1"`` / ``"S2"`` /
            # ``"both"``; for unmerged samples it is the empty-string default
            # and the merged-sample branch of ``_build_coverage_stats``
            # ignores this dict, so the unmerged shape stays unchanged.
            for raw in batch_rows:
                rsid = raw.rsid
                source_value = raw.source or ""
                if rsid in vep_rsid_hit_keys:
                    _bump_source(source_value, "vep_bundle_rsid_hits")
                elif rsid in vep_data:
                    _bump_source(source_value, "vep_bundle_coord_fallback_hits")
                else:
                    _bump_source(source_value, "vep_misses")

            # 5c. Gene-phenotype lookup (depends on VEP gene_symbol results)
            gene_phenotype_data: dict[str, dict] = {}
            if vep_data:
                try:
                    t_gp = time.perf_counter()
                    gene_phenotype_data = _lookup_gene_phenotype(vep_data, reference_engine)
                    result.timing_gene_phenotype_s += time.perf_counter() - t_gp
                except Exception as exc:
                    msg = f"gene_phenotype lookup failed: {exc}"
                    logger.warning(
                        "annotation_source_error",
                        extra={"source": "gene_phenotype", "error": str(exc)},
                    )
                    result.errors.append(msg)

            # 6. Merge results and compute bitmask
            t_merge = time.perf_counter()
            merged = _merge_annotations(
                batch_rows,
                vep_data,
                clinvar_data,
                gnomad_data,
                dbnsfp_data,
                gene_phenotype_data,
                alphamissense_data=alphamissense_data,
                merged_rsid_map=current_by_old,
            )

            # 6b. Ensemble pathogenicity flag (P2-13)
            apply_ensemble_pathogenic(merged)

            # 6c. Evidence conflict detection (P2-07)
            apply_evidence_conflicts(merged)
            result.timing_merge_s += time.perf_counter() - t_merge

            # 7. Bulk upsert
            t_upsert = time.perf_counter()
            written = _bulk_upsert(sample_engine, merged, target=annotated_variants_staging)
            result.timing_upsert_s += time.perf_counter() - t_upsert
            total_written += written

            # Update per-source match counts
            result.vep_matched += len(vep_data)
            result.clinvar_matched += len(clinvar_data)
            result.gnomad_matched += len(gnomad_data)
            result.dbnsfp_matched += len(dbnsfp_data)
            result.alphamissense_matched += len(alphamissense_data)
            result.gene_phenotype_matched += len(gene_phenotype_data)
            result.batches_processed += 1

            # 8. Progress callback
            variants_done = min(batch_start + batch_size, len(raw_rows))
            if progress_callback is not None:
                progress_callback(variants_done, len(raw_rows))

    result.rows_written = total_written

    # 8b. Atomic swap (F28): every batch staged successfully, so replace the
    # prior annotation with the staged one in a single transaction. Reached only
    # on full success — a crash in any batch above propagates out before here,
    # leaving ``annotated_variants`` (the prior good run) intact.
    _swap_staging_into_place(sample_engine)

    # 9. Coverage telemetry (Plan §5.6). `vep_matched` aggregates both rsid
    # and (chrom, pos) hits; subtract the coord-fallback subset so the
    # payload reports each bucket independently. ``source_counters`` carries
    # the per-``raw_variants.source`` breakdown the merged-sample branch
    # (Plan §15.1 MRG-09b) needs; unmerged samples ignore it.
    result.coverage_stats = _build_coverage_stats(
        sample_engine,
        registry,
        total_variants=result.total_variants,
        vep_rsid_hits=result.vep_matched - result.vep_coord_fallback_matched,
        vep_coord_fallback_hits=result.vep_coord_fallback_matched,
        source_counters=source_counters,
    )

    # 10. WAL checkpoint
    _wal_checkpoint(sample_engine)

    logger.info(
        "annotation_engine_complete",
        extra={
            "total": result.total_variants,
            "vep": result.vep_matched,
            "clinvar": result.clinvar_matched,
            "gnomad": result.gnomad_matched,
            "dbnsfp": result.dbnsfp_matched,
            "alphamissense": result.alphamissense_matched,
            "gene_phenotype": result.gene_phenotype_matched,
            "written": result.rows_written,
            "batches": result.batches_processed,
            "errors": result.errors,
            "timing_vep_s": round(result.timing_vep_s, 3),
            "timing_clinvar_s": round(result.timing_clinvar_s, 3),
            "timing_gnomad_s": round(result.timing_gnomad_s, 3),
            "timing_dbnsfp_s": round(result.timing_dbnsfp_s, 3),
            "timing_alphamissense_s": round(result.timing_alphamissense_s, 3),
            "timing_gene_phenotype_s": round(result.timing_gene_phenotype_s, 3),
            "timing_merge_s": round(result.timing_merge_s, 3),
            "timing_upsert_s": round(result.timing_upsert_s, 3),
            "coverage_stats": result.coverage_stats,
        },
    )

    return result

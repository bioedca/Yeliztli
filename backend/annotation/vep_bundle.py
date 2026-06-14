"""VEP bundle lookup client for batch rsid annotation.

Reads raw variants from a per-sample database and matches them against
the pre-computed VEP SQLite bundle (``vep_bundle.db``), writing variant
effect predictions into the ``annotated_variants`` table.

The VEP bundle is built by ``scripts/build_vep_bundle.py`` and contains
a ``vep_annotations`` table with consequence, HGVS, and transcript data
for every rsid in the 23andMe v5 catalog.

Usage::

    from backend.annotation.vep_bundle import annotate_sample_vep

    result = annotate_sample_vep(sample_engine, vep_engine)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import sqlalchemy as sa
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from backend.db.tables import annotated_variants, raw_variants

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────

# Annotation coverage bitmask: bit 0 = VEP (value 1)
VEP_BITMASK = 0b000001  # bit 0 = 1

# Batch size for SQLite IN clause (stay below 999 variable limit)
_IN_BATCH_SIZE = 500

# Batch size for upsert into annotated_variants.
# VEP upserts ~13 columns/row; 999 // 13 ≈ 76 rows stays under macOS SQLite
# variable limit. Use a moderate value that works cross-platform.
BATCH_SIZE = 75

# ── Consequence severity ranking (Ensembl SO terms) ──────────────────────
# Higher value = more severe.  Mirrors scripts/build_vep_bundle.py.

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


def consequence_severity(consequence: str | None) -> int:
    """Return the severity score for a consequence SO term.

    If the consequence contains multiple ``&``-separated terms, returns the
    maximum severity among them.  Returns ``-1`` for None/empty.

    Args:
        consequence: A single SO term or ``&``-delimited compound term.

    Returns:
        Integer severity (higher = more severe), or ``-1`` for None/empty.
    """
    if not consequence:
        return -1
    terms = consequence.split("&")
    return max(CONSEQUENCE_SEVERITY.get(t, 0) for t in terms)


# ── Data classes ──────────────────────────────────────────────────────────


@dataclass
class VEPAnnotation:
    """VEP annotation data for a single variant."""

    rsid: str
    gene_symbol: str | None
    transcript_id: str | None
    consequence: str | None
    hgvs_coding: str | None
    hgvs_protein: str | None
    strand: str | None
    exon_number: int | None
    intron_number: int | None
    mane_select: bool
    matched_by: str  # "rsid" or "chrom_pos"


@dataclass
class VEPAnnotationResult:
    """Statistics from a VEP annotation lookup run."""

    total_variants: int = 0
    matched_by_rsid: int = 0
    matched_by_position: int = 0
    not_matched: int = 0
    rows_written: int = 0

    @property
    def total_matched(self) -> int:
        return self.matched_by_rsid + self.matched_by_position


# ── Helpers ───────────────────────────────────────────────────────────────


def _wal_checkpoint(engine: sa.Engine) -> None:
    """Run WAL checkpoint if the engine is file-backed (not in-memory)."""
    url = str(engine.url)
    if url == "sqlite://" or ":memory:" in url:
        return
    with engine.connect() as conn:
        conn.execute(sa.text("PRAGMA wal_checkpoint(TRUNCATE)"))
        conn.commit()


def _pick_best(
    rows: list[sa.Row],
    *,
    matched_by: str,
    key_col: str = "rsid",
) -> dict[str, VEPAnnotation]:
    """Deduplicate VEP rows, preferring MANE Select then most-severe.

    Args:
        rows: Result rows from the vep_annotations query.
        matched_by: Either ``"rsid"`` or ``"chrom_pos"``.
        key_col: Column name to use as the dict key.

    Returns:
        Dict mapping key → best VEPAnnotation.
    """
    best: dict[str, VEPAnnotation] = {}
    best_score: dict[str, tuple[bool, int]] = {}  # (mane, severity)

    for row in rows:
        key = getattr(row, key_col)
        mane = bool(row.mane_select)
        severity = consequence_severity(row.consequence)
        score = (mane, severity)

        prev = best_score.get(key)
        if prev is None or score > prev:
            best[key] = VEPAnnotation(
                rsid=key,
                gene_symbol=row.gene_symbol,
                transcript_id=row.transcript_id,
                consequence=row.consequence,
                hgvs_coding=row.hgvs_coding,
                hgvs_protein=row.hgvs_protein,
                strand=row.strand,
                exon_number=row.exon_number,
                intron_number=row.intron_number,
                mane_select=mane,
                matched_by=matched_by,
            )
            best_score[key] = score

    return best


# ── VEP bundle SQL fragments ─────────────────────────────────────────────
# The vep_annotations table lives in the separate VEP bundle SQLite DB and
# is NOT defined in SQLAlchemy Core metadata, so we use sa.text() queries.

_VEP_COLS = (
    "rsid, gene_symbol, transcript_id, consequence, "
    "hgvs_coding, hgvs_protein, strand, exon_number, "
    "intron_number, mane_select"
)


# ── Public lookup functions ───────────────────────────────────────────────


def lookup_vep_by_rsids(
    rsids: list[str],
    vep_engine: sa.Engine,
) -> dict[str, VEPAnnotation]:
    """Look up VEP annotations for a batch of rsids.

    When multiple records share the same rsid (e.g. multiple transcripts),
    the record marked MANE Select is preferred, with ties broken by the
    most-severe consequence.

    Args:
        rsids: List of rsid strings (e.g. ``["rs429358", "rs7412"]``).
        vep_engine: SQLAlchemy engine for ``vep_bundle.db``.

    Returns:
        Dict mapping rsid to the best :class:`VEPAnnotation`.
    """
    if not rsids:
        return {}

    results: dict[str, VEPAnnotation] = {}

    with vep_engine.connect() as conn:
        for i in range(0, len(rsids), _IN_BATCH_SIZE):
            batch = rsids[i : i + _IN_BATCH_SIZE]

            # Build parameterised IN clause
            placeholders = ", ".join(f":r{j}" for j in range(len(batch)))
            params = {f"r{j}": rsid for j, rsid in enumerate(batch)}

            stmt = sa.text(
                f"SELECT {_VEP_COLS} FROM vep_annotations "  # noqa: S608
                f"WHERE rsid IN ({placeholders})"
            )
            rows = conn.execute(stmt, params).fetchall()

            batch_best = _pick_best(rows, matched_by="rsid")
            results.update(batch_best)

    return results


def lookup_vep_by_positions(
    positions: list[tuple[str, int, str]],
    vep_engine: sa.Engine,
) -> dict[str, VEPAnnotation]:
    """Fallback VEP lookup by (chrom, pos) for unmatched variants.

    Args:
        positions: List of ``(chrom, pos, sample_rsid)`` tuples.  The
            third element is the sample variant's rsid, used as the dict
            key in the result.
        vep_engine: SQLAlchemy engine for ``vep_bundle.db``.

    Returns:
        Dict mapping sample rsid to the best :class:`VEPAnnotation` for
        position-matched variants.
    """
    if not positions:
        return {}

    results: dict[str, VEPAnnotation] = {}

    with vep_engine.connect() as conn:
        for i in range(0, len(positions), _IN_BATCH_SIZE):
            batch = positions[i : i + _IN_BATCH_SIZE]

            # Build OR conditions for (chrom, pos) pairs
            clauses: list[str] = []
            params: dict[str, str | int] = {}
            for j, (chrom, pos, _rsid) in enumerate(batch):
                clauses.append(f"(chrom = :c{j} AND pos = :p{j})")
                params[f"c{j}"] = chrom
                params[f"p{j}"] = pos

            where = " OR ".join(clauses)
            stmt = sa.text(
                f"SELECT {_VEP_COLS}, chrom, pos "  # noqa: S608
                f"FROM vep_annotations WHERE {where}"
            )
            rows = conn.execute(stmt, params).fetchall()

            # Build lookup by (chrom, pos) → list of rows
            pos_rows: dict[tuple[str, int], list[sa.Row]] = {}
            for row in rows:
                key = (row.chrom, row.pos)
                pos_rows.setdefault(key, []).append(row)

            # Pick best per position and map back to sample rsids
            for chrom, pos, sample_rsid in batch:
                key = (chrom, pos)
                if key not in pos_rows or sample_rsid in results:
                    continue

                candidate_rows = pos_rows[key]
                candidate_rsids = {r.rsid for r in candidate_rows}
                if len(candidate_rsids) > 1:
                    logger.warning(
                        "ambiguous_vep_position_match",
                        extra={
                            "sample_rsid": sample_rsid,
                            "chrom": chrom,
                            "pos": pos,
                            "candidate_rsids": sorted(candidate_rsids),
                        },
                    )
                    continue

                best = _pick_best(candidate_rows, matched_by="chrom_pos")
                if best:
                    # best is keyed by the bundle rsid; re-key by sample rsid
                    annot = next(iter(best.values()))
                    annot.rsid = sample_rsid
                    annot.matched_by = "chrom_pos"
                    results[sample_rsid] = annot

    return results


# ── Main annotation entry point ──────────────────────────────────────────


def annotate_sample_vep(
    sample_engine: sa.Engine,
    vep_engine: sa.Engine,
) -> VEPAnnotationResult:
    """Annotate a sample's variants with VEP data from the bundle.

    Reads all ``raw_variants`` from the sample database, matches them
    against ``vep_annotations`` in the VEP bundle (rsid first, then
    chrom/pos fallback), and upserts results into ``annotated_variants``
    with bitmask bit 0 set.

    Args:
        sample_engine: SQLAlchemy engine for the per-sample database.
        vep_engine: SQLAlchemy engine for ``vep_bundle.db``.

    Returns:
        :class:`VEPAnnotationResult` with match statistics.
    """
    result = VEPAnnotationResult()

    # 1. Read all raw variants from the sample
    with sample_engine.connect() as conn:
        raw_rows = conn.execute(
            sa.select(
                raw_variants.c.rsid,
                raw_variants.c.chrom,
                raw_variants.c.pos,
                raw_variants.c.genotype,
            )
        ).fetchall()

    result.total_variants = len(raw_rows)
    if not raw_rows:
        return result

    # Build lookup structures
    all_rsids = [r.rsid for r in raw_rows]
    raw_by_rsid = {r.rsid: r for r in raw_rows}

    # 2. Primary match: by rsid
    rsid_matches = lookup_vep_by_rsids(all_rsids, vep_engine)
    result.matched_by_rsid = len(rsid_matches)

    # 3. Fallback: by (chrom, pos) for unmatched variants
    unmatched_positions = [
        (r.chrom, r.pos, r.rsid) for r in raw_rows if r.rsid not in rsid_matches
    ]
    pos_matches = lookup_vep_by_positions(unmatched_positions, vep_engine)
    result.matched_by_position = len(pos_matches)

    # 4. Merge all matches
    all_matches: dict[str, VEPAnnotation] = {**rsid_matches, **pos_matches}
    result.not_matched = result.total_variants - result.total_matched

    # 5. Upsert into annotated_variants
    rows_to_upsert = []
    for rsid, annot in all_matches.items():
        raw = raw_by_rsid[rsid]
        rows_to_upsert.append(
            {
                "rsid": rsid,
                "chrom": raw.chrom,
                "pos": raw.pos,
                "genotype": raw.genotype,
                "gene_symbol": annot.gene_symbol,
                "transcript_id": annot.transcript_id,
                "consequence": annot.consequence,
                "hgvs_coding": annot.hgvs_coding,
                "hgvs_protein": annot.hgvs_protein,
                "strand": annot.strand,
                "exon_number": annot.exon_number,
                "intron_number": annot.intron_number,
                "mane_select": annot.mane_select,
                "annotation_coverage": VEP_BITMASK,
            }
        )

    if rows_to_upsert:
        with sample_engine.begin() as conn:
            for batch_start in range(0, len(rows_to_upsert), BATCH_SIZE):
                batch = rows_to_upsert[batch_start : batch_start + BATCH_SIZE]

                stmt = sqlite_insert(annotated_variants).values(batch)
                stmt = stmt.on_conflict_do_update(
                    index_elements=["rsid"],
                    set_={
                        "gene_symbol": stmt.excluded.gene_symbol,
                        "transcript_id": stmt.excluded.transcript_id,
                        "consequence": stmt.excluded.consequence,
                        "hgvs_coding": stmt.excluded.hgvs_coding,
                        "hgvs_protein": stmt.excluded.hgvs_protein,
                        "strand": stmt.excluded.strand,
                        "exon_number": stmt.excluded.exon_number,
                        "intron_number": stmt.excluded.intron_number,
                        "mane_select": stmt.excluded.mane_select,
                        # OR the VEP bit into existing coverage
                        "annotation_coverage": sa.case(
                            (
                                annotated_variants.c.annotation_coverage.is_(None),
                                stmt.excluded.annotation_coverage,
                            ),
                            else_=(annotated_variants.c.annotation_coverage.op("|")(VEP_BITMASK)),
                        ),
                    },
                )
                conn.execute(stmt)

        result.rows_written = len(rows_to_upsert)

    # WAL checkpoint after annotation
    _wal_checkpoint(sample_engine)

    logger.info(
        "vep_annotation_complete",
        extra={
            "total": result.total_variants,
            "rsid_matches": result.matched_by_rsid,
            "pos_matches": result.matched_by_position,
            "unmatched": result.not_matched,
        },
    )

    return result

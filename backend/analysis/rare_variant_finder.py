"""Rare variant finder module (P3-28).

Identifies rare and ultra-rare variants from annotated data with flexible
filtering by:
  - Gene panel (custom gene list)
  - Allele frequency threshold (gnomAD global AF)
  - Consequence type filter (SO terms)
  - ClinVar significance filter

The module queries the annotated_variants table in a sample database and
returns matching variants sorted by clinical relevance (ClinVar P/LP first,
then by AF ascending, then by consequence severity descending).

Findings are stored in the unified ``findings`` table with
module='rare_variants'.

Usage::

    from backend.analysis.rare_variant_finder import (
        RareVariantFilter,
        RareVariantResult,
        RareVariantFinderResult,
        find_rare_variants,
        store_rare_variant_findings,
    )

    filters = RareVariantFilter(
        gene_symbols=["BRCA1", "TP53"],
        af_threshold=0.01,
        consequences=["missense_variant", "stop_gained"],
        clinvar_significance=["Pathogenic", "Likely pathogenic"],
    )
    result = find_rare_variants(filters, sample_engine)
    store_rare_variant_findings(result, sample_engine)
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

import sqlalchemy as sa
import structlog

from backend.analysis.evidence import assign_clinvar_evidence_level
from backend.analysis.zygosity import CARRIED_ZYGOSITIES
from backend.annotation.vep_bundle import CONSEQUENCE_SEVERITY
from backend.db.tables import annotated_variants, findings

logger = structlog.get_logger(__name__)

# Default AF threshold for "rare" variants (gnomAD global)
DEFAULT_AF_THRESHOLD = 0.01  # 1%

# High-impact consequence types (loss-of-function + missense)
HIGH_IMPACT_CONSEQUENCES = frozenset(
    {
        "transcript_ablation",
        "splice_acceptor_variant",
        "splice_donor_variant",
        "stop_gained",
        "frameshift_variant",
        "stop_lost",
        "start_lost",
        "inframe_insertion",
        "inframe_deletion",
        "missense_variant",
        "protein_altering_variant",
    }
)

# ClinVar significance values considered pathogenic
PATHOGENIC_SIGNIFICANCE = frozenset(
    {
        "Pathogenic",
        "Likely pathogenic",
        "Pathogenic/Likely pathogenic",
    }
)

# All recognized ClinVar significance values for filtering
ALL_CLINVAR_SIGNIFICANCE = frozenset(
    {
        "Pathogenic",
        "Likely pathogenic",
        "Pathogenic/Likely pathogenic",
        "Uncertain_significance",
        "Likely benign",
        "Benign",
        "Benign/Likely benign",
        "Conflicting_interpretations_of_pathogenicity",
        "risk_factor",
        "drug_response",
        "association",
        "protective",
        "affects",
        "other",
        "not_provided",
    }
)


# ── Data classes ──────────────────────────────────────────────────────────


@dataclass
class RareVariantFilter:
    """Filter parameters for the rare variant finder.

    All filters are optional and combined with AND logic. When a filter
    is None or empty, it is not applied.
    """

    gene_symbols: list[str] | None = None
    af_threshold: float = DEFAULT_AF_THRESHOLD
    consequences: list[str] | None = None
    clinvar_significance: list[str] | None = None
    include_novel: bool = True  # Include variants with no gnomAD AF (F12: not all are novel)
    zygosity: str | None = None  # "het", "hom_alt", or None for any
    # When True, surface only variants the individual actually carries
    # (zygosity in {het, hom_alt}). A genotyping chip reports a call at every
    # probe, so without this the finder dumps homozygous-reference and
    # unscoreable (indel/no-call) calls as findings. ``run_all`` sets it.
    carried_only: bool = False
    # Biologically-inferred sex ("XX"/"XY"/"manual_review"/"unknown"). When set,
    # findings that contradict it are dropped via ``finding_gate.is_surfaceable``
    # — chiefly a Y-chromosome finding on an XX sample (F8). ``run_all`` computes
    # it once and passes it; None means "do not sex-gate" (standalone callers).
    inferred_sex: str | None = None


@dataclass
class RareVariantResult:
    """A single rare variant found by the finder."""

    rsid: str
    chrom: str
    pos: int
    ref: str | None
    alt: str | None
    genotype: str | None
    zygosity: str | None
    gene_symbol: str | None
    consequence: str | None
    hgvs_coding: str | None
    hgvs_protein: str | None
    gnomad_af_global: float | None
    gnomad_af_afr: float | None
    gnomad_af_amr: float | None
    gnomad_af_eas: float | None
    gnomad_af_eur: float | None
    gnomad_af_fin: float | None
    gnomad_af_sas: float | None
    clinvar_significance: str | None
    clinvar_review_stars: int | None
    clinvar_accession: str | None
    clinvar_conditions: str | None
    cadd_phred: float | None
    revel: float | None
    ensemble_pathogenic: bool
    evidence_conflict: bool
    evidence_level: int
    disease_name: str | None
    inheritance_pattern: str | None

    @property
    def is_catalogued(self) -> bool:
        """Whether this variant is recorded in a public catalogue.

        A genotyping-chip variant that carries a dbSNP ``rs`` identifier is, by
        definition, catalogued in dbSNP; a ClinVar record is likewise positive
        evidence of prior description. Either signal means the variant is *not*
        novel regardless of whether gnomAD happens to have an allele frequency.
        """
        has_dbsnp_rsid = bool(self.rsid) and self.rsid.startswith("rs")
        has_clinvar = self.clinvar_significance is not None or self.clinvar_accession is not None
        return has_dbsnp_rsid or has_clinvar

    @property
    def is_novel(self) -> bool:
        """Whether this variant is genuinely uncatalogued (F12).

        Absence from gnomAD is **not** novelty: the gnomAD bundle is
        exome-biased, so the great majority of (common, well-known) chip SNPs
        have no gnomAD AF yet are catalogued dbSNP variants. A variant is novel
        only when it is absent from gnomAD *and* not catalogued in dbSNP (no
        ``rs`` identifier) or ClinVar.
        """
        return self.gnomad_af_global is None and not self.is_catalogued

    @property
    def is_clinvar_pathogenic(self) -> bool:
        """Whether this variant is ClinVar Pathogenic or Likely pathogenic."""
        return self.clinvar_significance in PATHOGENIC_SIGNIFICANCE

    @property
    def consequence_severity_score(self) -> int:
        """Severity score for this variant's consequence."""
        if not self.consequence:
            return -1
        terms = self.consequence.split("&")
        return max(CONSEQUENCE_SEVERITY.get(t, 0) for t in terms)


@dataclass
class RareVariantFinderResult:
    """Complete result from the rare variant finder."""

    variants: list[RareVariantResult] = field(default_factory=list)
    filters_applied: RareVariantFilter | None = None
    total_variants_scanned: int = 0

    @property
    def count(self) -> int:
        """Number of rare variants found."""
        return len(self.variants)

    @property
    def novel_count(self) -> int:
        """Number of genuinely novel variants (uncatalogued, F12 — not merely AF-null)."""
        return sum(1 for v in self.variants if v.is_novel)

    @property
    def pathogenic_count(self) -> int:
        """Number of ClinVar P/LP variants."""
        return sum(1 for v in self.variants if v.is_clinvar_pathogenic)

    @property
    def genes_with_findings(self) -> list[str]:
        """Unique gene symbols with at least one rare variant."""
        return sorted({v.gene_symbol for v in self.variants if v.gene_symbol})


# ── Evidence level assignment ─────────────────────────────────────────────


def _assign_evidence_level(variant: RareVariantResult) -> int:
    """Assign evidence level (1-4 stars) for a rare variant.

    Delegates to the centralized evidence framework (P3-40).
    """
    return assign_clinvar_evidence_level(
        variant.clinvar_significance,
        variant.clinvar_review_stars,
        ensemble_pathogenic=variant.ensemble_pathogenic,
    )


# ── Core query logic ─────────────────────────────────────────────────────


def find_rare_variants(
    filters: RareVariantFilter,
    sample_engine: sa.Engine,
) -> RareVariantFinderResult:
    """Find rare variants matching the given filter criteria.

    Queries the annotated_variants table with all filter conditions
    combined via AND logic. Results are sorted by clinical relevance:
    ClinVar P/LP first, then by AF ascending (novel variants last),
    then by chromosome and position for deterministic ordering.

    Args:
        filters: Filter parameters (gene panel, AF, consequence, ClinVar).
        sample_engine: SQLAlchemy engine for the sample database.

    Returns:
        RareVariantFinderResult with matching variants and metadata.
    """
    av = annotated_variants

    # Select columns needed for the result
    stmt = sa.select(
        av.c.rsid,
        av.c.chrom,
        av.c.pos,
        av.c.ref,
        av.c.alt,
        av.c.genotype,
        av.c.zygosity,
        av.c.gene_symbol,
        av.c.consequence,
        av.c.hgvs_coding,
        av.c.hgvs_protein,
        av.c.gnomad_af_global,
        av.c.gnomad_af_afr,
        av.c.gnomad_af_amr,
        av.c.gnomad_af_eas,
        av.c.gnomad_af_eur,
        av.c.gnomad_af_fin,
        av.c.gnomad_af_sas,
        av.c.clinvar_significance,
        av.c.clinvar_review_stars,
        av.c.clinvar_accession,
        av.c.clinvar_conditions,
        av.c.cadd_phred,
        av.c.revel,
        av.c.ensemble_pathogenic,
        av.c.evidence_conflict,
        av.c.disease_name,
        av.c.inheritance_pattern,
    )

    # Build WHERE conditions
    conditions: list[sa.ColumnElement] = []

    # F15: judge rarity on the population-max AF, not the global average, so a
    # variant common in one ancestry is not surfaced as "rare". Fall back to the
    # global AF for rows annotated before the popmax column existed (NULL popmax).
    effective_af = sa.func.coalesce(av.c.gnomad_af_popmax, av.c.gnomad_af_global)

    # AF threshold filter: include variants below threshold OR with no AF data.
    # The no-data sentinel stays on gnomad_af_global (the F12 novelty signal).
    if filters.include_novel:
        conditions.append(
            sa.or_(
                effective_af < filters.af_threshold,
                av.c.gnomad_af_global.is_(None),
            )
        )
    else:
        conditions.append(effective_af < filters.af_threshold)
        conditions.append(av.c.gnomad_af_global.isnot(None))

    # Gene panel filter
    if filters.gene_symbols:
        # Normalize to uppercase for case-insensitive matching
        upper_symbols = [g.upper() for g in filters.gene_symbols]
        conditions.append(sa.func.upper(av.c.gene_symbol).in_(upper_symbols))

    # Consequence filter
    if filters.consequences:
        # Match any of the specified consequences (exact or compound SO terms)
        consequence_conditions = []
        for cons in filters.consequences:
            consequence_conditions.append(av.c.consequence.like(f"%{cons}%"))
        conditions.append(sa.or_(*consequence_conditions))

    # ClinVar significance filter
    if filters.clinvar_significance:
        conditions.append(av.c.clinvar_significance.in_(filters.clinvar_significance))

    # Zygosity filter
    if filters.zygosity:
        conditions.append(av.c.zygosity == filters.zygosity)

    # Carriage gate: restrict to variants the individual actually carries.
    # NULL zygosity (unscoreable: indel/no-call/strand-ambiguous) is excluded
    # by the IN clause, so unscoreable calls never surface as confident findings.
    if filters.carried_only:
        conditions.append(av.c.zygosity.in_(list(CARRIED_ZYGOSITIES)))

    if conditions:
        stmt = stmt.where(sa.and_(*conditions))

    # Order: ClinVar P/LP first, then AF ascending (NULLs last), then severity
    stmt = stmt.order_by(
        # ClinVar P/LP first (1=P/LP, 0=other)
        sa.case(
            (av.c.clinvar_significance.in_(list(PATHOGENIC_SIGNIFICANCE)), 0),
            else_=1,
        ),
        # AF ascending, NULLs (novel) after known-rare. Ordered by the same
        # popmax-or-global rarity measure used for filtering (F15).
        sa.case(
            (av.c.gnomad_af_global.is_(None), 1),
            else_=0,
        ),
        effective_af.asc(),
        # Chromosome and position for deterministic ordering
        av.c.chrom,
        av.c.pos,
    )

    with sample_engine.connect() as conn:
        # Get total variant count for stats
        total_stmt = sa.select(sa.func.count()).select_from(av)
        total_variants = conn.execute(total_stmt).scalar() or 0

        rows = conn.execute(stmt).fetchall()

    variants: list[RareVariantResult] = []
    for row in rows:
        variant = RareVariantResult(
            rsid=row.rsid,
            chrom=row.chrom,
            pos=row.pos,
            ref=row.ref,
            alt=row.alt,
            genotype=row.genotype,
            zygosity=row.zygosity,
            gene_symbol=row.gene_symbol,
            consequence=row.consequence,
            hgvs_coding=row.hgvs_coding,
            hgvs_protein=row.hgvs_protein,
            gnomad_af_global=row.gnomad_af_global,
            gnomad_af_afr=row.gnomad_af_afr,
            gnomad_af_amr=row.gnomad_af_amr,
            gnomad_af_eas=row.gnomad_af_eas,
            gnomad_af_eur=row.gnomad_af_eur,
            gnomad_af_fin=row.gnomad_af_fin,
            gnomad_af_sas=row.gnomad_af_sas,
            clinvar_significance=row.clinvar_significance,
            clinvar_review_stars=row.clinvar_review_stars,
            clinvar_accession=row.clinvar_accession,
            clinvar_conditions=row.clinvar_conditions,
            cadd_phred=row.cadd_phred,
            revel=row.revel,
            ensemble_pathogenic=bool(row.ensemble_pathogenic),
            evidence_conflict=bool(row.evidence_conflict),
            evidence_level=1,  # placeholder, assigned below
            disease_name=row.disease_name,
            inheritance_pattern=row.inheritance_pattern,
        )
        variant.evidence_level = _assign_evidence_level(variant)
        variants.append(variant)

    # Sex/chromosome gate (F8): drop findings impossible for the inferred sex —
    # e.g. a Y-chromosome finding on an XX sample. Applied only when a sex was
    # supplied (the live ``run_all`` path); standalone callers are unaffected.
    if filters.inferred_sex is not None:
        from backend.analysis.finding_gate import is_surfaceable

        variants = [v for v in variants if is_surfaceable(v.chrom, filters.inferred_sex)]

    logger.info(
        "rare_variants_found",
        total_scanned=total_variants,
        matching=len(variants),
        novel=sum(1 for v in variants if v.is_novel),
        pathogenic=sum(1 for v in variants if v.is_clinvar_pathogenic),
        genes=len({v.gene_symbol for v in variants if v.gene_symbol}),
        af_threshold=filters.af_threshold,
        gene_filter=bool(filters.gene_symbols),
        consequence_filter=bool(filters.consequences),
        clinvar_filter=bool(filters.clinvar_significance),
    )

    return RareVariantFinderResult(
        variants=variants,
        filters_applied=filters,
        total_variants_scanned=total_variants,
    )


# ── Findings storage ─────────────────────────────────────────────────────


def store_rare_variant_findings(
    result: RareVariantFinderResult,
    sample_engine: sa.Engine,
) -> int:
    """Store rare variant findings in the sample database.

    Creates one finding per rare variant with module='rare_variants'.
    Categories:
      - 'clinvar_pathogenic' — ClinVar P/LP rare variants
      - 'ensemble_pathogenic' — computationally predicted pathogenic
      - 'novel' — genuinely uncatalogued (no gnomAD AF, no dbSNP rsid, no ClinVar; F12)
      - 'rare' — other rare variants below AF threshold (incl. catalogued, gnomAD-absent)

    Args:
        result: RareVariantFinderResult from find_rare_variants.
        sample_engine: SQLAlchemy engine for the sample database.

    Returns:
        Number of findings inserted.
    """
    rows: list[dict] = []

    for v in result.variants:
        # Determine category
        if v.is_clinvar_pathogenic:
            # F20 review-star floor: a 0-star P/LP record has no assertion
            # criteria, so route it to a distinct low-confidence sub-tier rather
            # than the headline ``clinvar_pathogenic`` category it would
            # otherwise inflate. (It is already down-ranked to evidence_level 2
            # by assign_clinvar_evidence_level, so it never reaches the
            # high-confidence card; this also keeps it out of the headline count.)
            if (v.clinvar_review_stars or 0) == 0:
                category = "clinvar_pathogenic_low_confidence"
            else:
                category = "clinvar_pathogenic"
        elif v.ensemble_pathogenic:
            category = "ensemble_pathogenic"
        elif v.is_novel:
            category = "novel"
        else:
            category = "rare"

        # Build human-readable finding text. F12: absence from gnomAD is not
        # novelty — only badge "Novel" when the variant is genuinely uncatalogued;
        # otherwise state the neutral fact that gnomAD has no frequency for it.
        if v.gnomad_af_global is not None:
            af_text = f"AF={v.gnomad_af_global:.6f}"
        elif v.is_novel:
            af_text = "Novel (uncatalogued)"
        else:
            af_text = "Not in gnomAD"
        gene_text = v.gene_symbol or "intergenic"
        cons_text = v.consequence or "unknown consequence"

        if v.is_clinvar_pathogenic:
            finding_text = (
                f"{gene_text} {v.rsid} — {v.clinvar_significance} ({cons_text}, {af_text})"
            )
        else:
            finding_text = f"{gene_text} {v.rsid} — {cons_text} ({af_text})"

        detail = {
            "af_global": v.gnomad_af_global,
            "af_populations": {
                "afr": v.gnomad_af_afr,
                "amr": v.gnomad_af_amr,
                "eas": v.gnomad_af_eas,
                "eur": v.gnomad_af_eur,
                "fin": v.gnomad_af_fin,
                "sas": v.gnomad_af_sas,
            },
            "consequence": v.consequence,
            "hgvs_coding": v.hgvs_coding,
            "hgvs_protein": v.hgvs_protein,
            "cadd_phred": v.cadd_phred,
            "revel": v.revel,
            "ensemble_pathogenic": v.ensemble_pathogenic,
            "evidence_conflict": v.evidence_conflict,
            "clinvar_accession": v.clinvar_accession,
            "clinvar_review_stars": v.clinvar_review_stars,
            "disease_name": v.disease_name,
            "inheritance_pattern": v.inheritance_pattern,
        }

        rows.append(
            {
                "module": "rare_variants",
                "category": category,
                "evidence_level": v.evidence_level,
                "gene_symbol": v.gene_symbol,
                "rsid": v.rsid,
                "finding_text": finding_text,
                "conditions": v.clinvar_conditions,
                "zygosity": v.zygosity,
                "clinvar_significance": v.clinvar_significance,
                "detail_json": json.dumps(detail),
            }
        )

    with sample_engine.begin() as conn:
        # Clear previous rare_variants findings
        conn.execute(sa.delete(findings).where(findings.c.module == "rare_variants"))
        if not rows:
            logger.info("no_rare_variant_findings_to_store")
            return 0
        conn.execute(sa.insert(findings), rows)

    logger.info("rare_variant_findings_stored", count=len(rows))
    return len(rows)

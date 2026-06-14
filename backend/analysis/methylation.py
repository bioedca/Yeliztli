"""MTHFR & Methylation module — 5 pathway-level summaries.

Implements P3-52:
  - 5 pathway-level summaries (Folate & MTHFR, Methionine Cycle,
    Transsulfuration, BH4 & Neurotransmitter Synthesis, Choline & Betaine).
  - MTHFR compound heterozygosity calling (C677T + A1298C).
  - COMT Val158Met framed as catecholamine clearance only (not psychiatric).
  - CBS rs234706 proxy with coverage caveat.
  - MTHFR migration from Nutrigenomics on first run.

Panel definition lives in ``backend/data/panels/methylation_panel.json`` (P3-51).

Scoring follows the same base algorithm as nutrigenomics / fitness / sleep:
  - No numeric scores, no summed risk alleles, no effect-size weighting.
  - ★☆ evidence hard-caps pathway at Moderate.
  - Elevated requires ≥★★ evidence + clinically meaningful genotype.
  - Pathway level = highest category across called SNPs.
  - Multiple Moderate findings are surfaced as context, not promoted to Elevated.

Usage::

    from backend.analysis.methylation import (
        load_methylation_panel,
        score_methylation_pathways,
        store_methylation_findings,
    )

    panel = load_methylation_panel()
    results = score_methylation_pathways(panel, sample_engine, reference_engine)
    store_methylation_findings(results, sample_engine)
"""

from __future__ import annotations

import json
from collections.abc import Container
from dataclasses import dataclass, field
from pathlib import Path

import sqlalchemy as sa
import structlog

from backend.analysis.genotype_lookup import (
    genotype_candidates,
    is_acgt_genotype,
    is_strand_ambiguous,
    lookup_by_genotype,
)
from backend.analysis.zygosity import is_no_call
from backend.annotation.engine import GWAS_BIT
from backend.db.tables import annotated_variants, findings, gwas_associations, raw_variants

logger = structlog.get_logger(__name__)

# Path to the curated panel JSON (relative to this file)
_PANEL_PATH = Path(__file__).resolve().parent.parent / "data" / "panels" / "methylation_panel.json"

# Pathway scoring categories
ELEVATED = "Elevated"
MODERATE = "Moderate"
STANDARD = "Standard"
# Runtime-only category for a palindromic (A/T or C/G) homozygote whose strand —
# and therefore its curated category — cannot be resolved from the array genotype
# alone (#170/#269). Surfaced with a strand caveat but withheld from pathway-level
# aggregation; never a confident (possibly flipped) call. Not a valid panel-JSON
# category.
INDETERMINATE = "Indeterminate"

# Minimum evidence level required for Elevated category
_ELEVATED_MIN_STARS = 2

# Mark pathway summaries when several Moderate SNPs are observed without escalation.
_MULTIPLE_MODERATE_FINDINGS_THRESHOLD = 3

# Module name for findings storage
MODULE_NAME = "methylation"

# Some methylation panel entries intentionally use insertion/deletion genotype
# vocabulary that other trait modules treat as unscoreable raw no-calls.
_SCORABLE_PANEL_INDELS = frozenset({"II", "ID", "DI", "DD"})


# ── Data classes ──────────────────────────────────────────────────────────


@dataclass
class PanelSNP:
    """A single SNP entry from the curated methylation panel."""

    rsid: str
    gene: str
    variant_name: str
    hgvs_protein: str | None
    risk_allele: str
    ref_allele: str
    genotype_effects: dict[str, dict[str, str]]
    evidence_level: int
    pmids: list[str]
    recommendation_text: str
    coverage_note: str | None = None


@dataclass
class Pathway:
    """A methylation pathway with its curated SNPs."""

    id: str
    name: str
    description: str
    snps: list[PanelSNP]


@dataclass
class MethylationPanel:
    """The complete curated methylation panel."""

    module: str
    version: str
    pathways: list[Pathway]
    additional_genes: dict | None = None
    special_calling: dict | None = None
    scoring_rules: dict | None = None

    def all_rsids(self) -> list[str]:
        """Return all rsids in the panel."""
        return [snp.rsid for pathway in self.pathways for snp in pathway.snps]


@dataclass
class SNPResult:
    """Scoring result for a single SNP."""

    rsid: str
    gene: str
    variant_name: str
    genotype: str | None  # None if not genotyped
    category: str  # Elevated / Moderate / Standard
    effect_summary: str
    evidence_level: int
    pmids: list[str]
    recommendation_text: str
    present_in_sample: bool
    coverage_note: str | None = None


@dataclass
class CompoundHetResult:
    """MTHFR compound heterozygosity assessment."""

    is_compound_het: bool
    is_double_homozygous: bool
    label: str | None
    description: str | None
    c677t_genotype: str | None
    a1298c_genotype: str | None


@dataclass
class PathwayResult:
    """Scoring result for a complete pathway."""

    pathway_id: str
    pathway_name: str
    level: str  # Elevated / Moderate / Standard
    snp_results: list[SNPResult] = field(default_factory=list)
    # Legacy storage flag retained for compatibility; generic additive promotion is disabled.
    additive_promoted: bool = False

    @property
    def called_snps(self) -> list[SNPResult]:
        """SNPs that were present and genotyped in the sample."""
        return [s for s in self.snp_results if s.present_in_sample]

    @property
    def missing_snps(self) -> list[SNPResult]:
        """SNPs that were not present in the sample."""
        return [s for s in self.snp_results if not s.present_in_sample]


@dataclass
class MethylationResult:
    """Complete methylation scoring result for a sample."""

    pathway_results: list[PathwayResult] = field(default_factory=list)
    gwas_matched_rsids: list[str] = field(default_factory=list)
    compound_het: CompoundHetResult | None = None


# ── Panel loading ─────────────────────────────────────────────────────────


def load_methylation_panel(panel_path: Path | None = None) -> MethylationPanel:
    """Load the curated methylation panel from JSON.

    Args:
        panel_path: Optional override for the panel JSON path.
            Defaults to ``backend/data/panels/methylation_panel.json``.

    Returns:
        Parsed MethylationPanel with all pathways and SNPs.

    Raises:
        FileNotFoundError: If the panel JSON does not exist.
        json.JSONDecodeError: If the panel JSON is malformed.
    """
    path = panel_path or _PANEL_PATH
    logger.info("loading_methylation_panel", path=str(path))

    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    pathways: list[Pathway] = []
    for pw_data in data["pathways"]:
        snps: list[PanelSNP] = []
        for snp_data in pw_data["snps"]:
            snps.append(
                PanelSNP(
                    rsid=snp_data["rsid"],
                    gene=snp_data["gene"],
                    variant_name=snp_data["variant_name"],
                    hgvs_protein=snp_data.get("hgvs_protein"),
                    risk_allele=snp_data["risk_allele"],
                    ref_allele=snp_data["ref_allele"],
                    genotype_effects=snp_data["genotype_effects"],
                    evidence_level=snp_data["evidence_level"],
                    pmids=snp_data.get("pmids", []),
                    recommendation_text=snp_data.get("recommendation_text", ""),
                    coverage_note=snp_data.get("coverage_note"),
                )
            )
        pathways.append(
            Pathway(
                id=pw_data["id"],
                name=pw_data["name"],
                description=pw_data["description"],
                snps=snps,
            )
        )

    return MethylationPanel(
        module=data["module"],
        version=data["version"],
        pathways=pathways,
        additional_genes=data.get("additional_genes"),
        special_calling=data.get("special_calling"),
        scoring_rules=data.get("scoring_rules"),
    )


# ── Genotype scoring ─────────────────────────────────────────────────────


def _normalize_genotype(
    genotype: str | None,
    *,
    scorable_genotypes: Container[str] | None = None,
) -> str | None:
    """Normalize genotype string for lookup.

    Handles common formats: 'CT', 'TC', '--' (no-call).
    Returns None for no-calls or missing data.
    """
    if genotype is None:
        return None

    normalized = genotype.strip().upper()
    if (
        normalized in _SCORABLE_PANEL_INDELS
        and scorable_genotypes is not None
        and normalized in scorable_genotypes
    ):
        return normalized

    if is_no_call(genotype):
        return None
    return normalized


def _score_snp(snp: PanelSNP, genotype: str | None) -> SNPResult:
    """Score a single SNP given a genotype.

    Applies evidence-level gating: ★☆ (evidence_level=1) variants
    are hard-capped at Moderate regardless of genotype.
    """
    if genotype is None:
        return SNPResult(
            rsid=snp.rsid,
            gene=snp.gene,
            variant_name=snp.variant_name,
            genotype=None,
            category=STANDARD,
            effect_summary="Variant not genotyped in this sample.",
            evidence_level=snp.evidence_level,
            pmids=snp.pmids,
            recommendation_text=snp.recommendation_text,
            present_in_sample=False,
            coverage_note=snp.coverage_note,
        )

    # Palindromic-SNP strand guard (#170/#269): for an A/T or C/G homozygote whose
    # curated category differs between strands, the array strand cannot be resolved
    # from the genotype string, so withhold the category instead of risking the
    # flipped one. Compare CATEGORIES (not the full effect dicts) so a homozygote
    # whose two strands share a category is not falsely withheld.
    if is_strand_ambiguous(
        {gt: eff.get("category") for gt, eff in snp.genotype_effects.items()}, genotype
    ):
        return SNPResult(
            rsid=snp.rsid,
            gene=snp.gene,
            variant_name=snp.variant_name,
            genotype=genotype,
            category=INDETERMINATE,
            effect_summary=(
                f"{genotype} is a palindromic (A/T or C/G) homozygote: its strand — and "
                f"therefore its effect category — cannot be determined from the array "
                f"genotype alone, so it is reported as indeterminate rather than a "
                f"possibly strand-flipped call."
            ),
            evidence_level=snp.evidence_level,
            pmids=snp.pmids,
            recommendation_text=snp.recommendation_text,
            present_in_sample=True,
            coverage_note=(
                "Strand-ambiguous palindromic SNP — confirm the genotyping strand "
                "(or use a sequencing-based result) before interpreting this locus."
            ),
        )

    # Look up genotype effect from panel definition, harmonizing allele order
    # and strand (e.g. chip "CT" → panel "GA" for a reverse-strand-keyed SNP).
    effect = lookup_by_genotype(snp.genotype_effects, genotype)

    if effect is None:
        logger.warning(
            "unknown_genotype_for_methylation_snp",
            rsid=snp.rsid,
            gene=snp.gene,
            genotype=genotype,
        )
        # A present, real-nucleotide genotype that resolves to no curated entry is
        # NOT baseline: it carries an allele this locus does not model (a third/rare
        # allele — e.g. QDPR rs1677693 `G/A/T` observed `GT`, #608) or an unkeyed
        # pair, so it is withheld as Indeterminate rather than silently scored
        # Standard. Only non-nucleotide tokens (indels, no-calls) fall through.
        unmodeled = is_acgt_genotype(genotype)
        return SNPResult(
            rsid=snp.rsid,
            gene=snp.gene,
            variant_name=snp.variant_name,
            genotype=genotype,
            category=INDETERMINATE if unmodeled else STANDARD,
            effect_summary=(
                f"Genotype {genotype} carries an allele this locus does not model "
                f"(it matches no curated genotype), so it is reported as indeterminate "
                f"rather than assumed baseline."
                if unmodeled
                else f"Genotype {genotype} not in curated panel definitions."
            ),
            evidence_level=snp.evidence_level,
            pmids=snp.pmids,
            recommendation_text=snp.recommendation_text,
            present_in_sample=True,
            coverage_note=(
                "Observed genotype includes an allele outside this locus's curated "
                "model; not interpretable from the panel."
                if unmodeled
                else snp.coverage_note
            ),
        )

    category = effect.get("category", STANDARD)
    effect_summary = effect.get("effect_summary", "Effect not documented.")

    # Evidence-level gating: ★☆ hard-caps at Moderate
    if snp.evidence_level < _ELEVATED_MIN_STARS and category == ELEVATED:
        category = MODERATE
        logger.debug(
            "evidence_gating_applied",
            rsid=snp.rsid,
            original_category=ELEVATED,
            capped_to=MODERATE,
            evidence_level=snp.evidence_level,
        )

    return SNPResult(
        rsid=snp.rsid,
        gene=snp.gene,
        variant_name=snp.variant_name,
        genotype=genotype,
        category=category,
        effect_summary=effect_summary,
        evidence_level=snp.evidence_level,
        pmids=snp.pmids,
        recommendation_text=snp.recommendation_text,
        present_in_sample=True,
        coverage_note=snp.coverage_note,
    )


def _determine_pathway_level(snp_results: list[SNPResult]) -> tuple[str, bool]:
    """Determine the overall pathway category from individual SNP results.

    The pathway level is the highest category across all called SNPs.
    Ordering: Elevated > Moderate > Standard.

    Only SNPs present in the sample contribute to the pathway level.
    If no SNPs are genotyped, the pathway defaults to Standard.

    Returns:
        Tuple of (level, additive_promoted). ``additive_promoted`` is always
        False because generic additive escalation is not validated.
    """
    # Strand-indeterminate palindromic homozygotes carry no trustworthy category,
    # so they neither raise nor lower the pathway level (#170/#269).
    called = [r for r in snp_results if r.present_in_sample and r.category != INDETERMINATE]
    if not called:
        return STANDARD, False

    category_priority = {ELEVATED: 2, MODERATE: 1, STANDARD: 0}
    present = {r.category for r in called}
    base_level = max(present, key=lambda c: category_priority.get(c, 0), default=STANDARD)

    return base_level, False


# ── MTHFR compound heterozygosity ────────────────────────────────────────


def _genotype_matches(genotype: str, accepted: Container[str]) -> bool:
    """Whether any strand-/order-harmonized form of ``genotype`` is in ``accepted``.

    The compound-het ``special_calling`` genotype lists are curated on the panel's
    strand frame — C677T (``rs1801133``) on the genomic plus strand (``GA``/``AG``),
    A1298C (``rs1801131``) on the cDNA/complement strand (``AC``/``CA``) — while array
    vendors report each on their *design* strand (Ensembl GRCh37 ``rs1801131`` is
    plus-strand ``T/G``, so the het reads ``GT``/``TG``). A raw ``in`` membership test
    therefore silently misses real carriers. Matching via :func:`genotype_candidates`
    (reference strand and allele order first, Watson–Crick complement as fallback)
    makes this check strand-aware and consistent with the per-SNP path's
    :func:`lookup_by_genotype`. (#528)

    Neither MTHFR SNP is a palindromic A/T or C/G locus (C677T is G/A, A1298C is
    T/G), so the complement fallback resolves homozygotes unambiguously here.
    """
    return any(candidate in accepted for candidate in genotype_candidates(genotype))


def _assess_compound_heterozygosity(
    panel: MethylationPanel,
    genotypes: dict[str, str],
) -> CompoundHetResult:
    """Assess MTHFR compound heterozygosity (C677T + A1298C).

    Returns a CompoundHetResult describing the compound het status.
    """
    if panel.special_calling is None:
        return CompoundHetResult(
            is_compound_het=False,
            is_double_homozygous=False,
            label=None,
            description=None,
            c677t_genotype=None,
            a1298c_genotype=None,
        )

    config = panel.special_calling.get("MTHFR_compound_heterozygosity")
    if config is None:
        return CompoundHetResult(
            is_compound_het=False,
            is_double_homozygous=False,
            label=None,
            description=None,
            c677t_genotype=None,
            a1298c_genotype=None,
        )

    c677t_gt = _normalize_genotype(genotypes.get("rs1801133"))
    a1298c_gt = _normalize_genotype(genotypes.get("rs1801131"))

    if c677t_gt is None or a1298c_gt is None:
        return CompoundHetResult(
            is_compound_het=False,
            is_double_homozygous=False,
            label=None,
            description=None,
            c677t_genotype=c677t_gt,
            a1298c_genotype=a1298c_gt,
        )

    states = config["states"]

    # Check compound het
    cpd_het = states.get("compound_het", {})
    c677t_het_gts = cpd_het.get("c677t_genotypes", [])
    a1298c_het_gts = cpd_het.get("a1298c_genotypes", [])

    if _genotype_matches(c677t_gt, c677t_het_gts) and _genotype_matches(a1298c_gt, a1298c_het_gts):
        return CompoundHetResult(
            is_compound_het=True,
            is_double_homozygous=False,
            label=cpd_het.get("label"),
            description=cpd_het.get("description"),
            c677t_genotype=c677t_gt,
            a1298c_genotype=a1298c_gt,
        )

    # Check double homozygous
    dbl_hom = states.get("double_homozygous", {})
    c677t_hom_gts = dbl_hom.get("c677t_genotypes", [])
    a1298c_hom_gts = dbl_hom.get("a1298c_genotypes", [])

    if _genotype_matches(c677t_gt, c677t_hom_gts) and _genotype_matches(a1298c_gt, a1298c_hom_gts):
        return CompoundHetResult(
            is_compound_het=False,
            is_double_homozygous=True,
            label=dbl_hom.get("label"),
            description=dbl_hom.get("description"),
            c677t_genotype=c677t_gt,
            a1298c_genotype=a1298c_gt,
        )

    return CompoundHetResult(
        is_compound_het=False,
        is_double_homozygous=False,
        label=None,
        description=None,
        c677t_genotype=c677t_gt,
        a1298c_genotype=a1298c_gt,
    )


# ── Main scoring function ────────────────────────────────────────────────


def score_methylation_pathways(
    panel: MethylationPanel,
    sample_engine: sa.Engine,
    reference_engine: sa.Engine,
) -> MethylationResult:
    """Score all methylation pathways for a sample.

    1. Fetches raw genotypes from the sample DB for all panel rsids.
    2. Scores each SNP using the curated panel definitions.
    3. Applies evidence-level gating.
    4. Assesses MTHFR compound heterozygosity.
    5. Determines per-pathway level from the highest called SNP category.
    6. Looks up GWAS associations for matched rsids.

    Args:
        panel: Loaded MethylationPanel.
        sample_engine: SQLAlchemy engine for the sample database.
        reference_engine: SQLAlchemy engine for reference.db.

    Returns:
        MethylationResult with all pathway results and compound het status.
    """
    # Fetch all panel rsids from sample
    all_rsids = panel.all_rsids()
    genotypes = _fetch_genotypes(all_rsids, sample_engine)
    logger.info(
        "methylation_genotypes_fetched",
        panel_rsids=len(all_rsids),
        found_in_sample=len(genotypes),
    )

    pathway_results: list[PathwayResult] = []

    for pathway in panel.pathways:
        snp_results: list[SNPResult] = []
        for snp in pathway.snps:
            gt = _normalize_genotype(
                genotypes.get(snp.rsid),
                scorable_genotypes=snp.genotype_effects,
            )
            result = _score_snp(snp, gt)
            snp_results.append(result)

        level, additive_promoted = _determine_pathway_level(snp_results)
        pathway_results.append(
            PathwayResult(
                pathway_id=pathway.id,
                pathway_name=pathway.name,
                level=level,
                snp_results=snp_results,
                additive_promoted=additive_promoted,
            )
        )

    # MTHFR compound heterozygosity assessment
    compound_het = _assess_compound_heterozygosity(panel, genotypes)

    # Identify GWAS-matched rsids for annotation_coverage bitmask
    gwas_matched = _lookup_gwas_matches(
        [r.rsid for pr in pathway_results for r in pr.called_snps],
        reference_engine,
    )

    return MethylationResult(
        pathway_results=pathway_results,
        gwas_matched_rsids=gwas_matched,
        compound_het=compound_het,
    )


def _fetch_genotypes(
    rsids: list[str],
    sample_engine: sa.Engine,
) -> dict[str, str]:
    """Fetch raw genotypes from sample DB for the given rsids."""
    if not rsids:
        return {}

    result: dict[str, str] = {}
    with sample_engine.connect() as conn:
        stmt = sa.select(
            raw_variants.c.rsid,
            raw_variants.c.genotype,
        ).where(raw_variants.c.rsid.in_(rsids))

        for row in conn.execute(stmt):
            result[row.rsid] = row.genotype

    return result


def _lookup_gwas_matches(
    rsids: list[str],
    reference_engine: sa.Engine,
) -> list[str]:
    """Look up which rsids have GWAS Catalog associations."""
    if not rsids:
        return []

    matched: list[str] = []
    with reference_engine.connect() as conn:
        stmt = (
            sa.select(gwas_associations.c.rsid)
            .where(gwas_associations.c.rsid.in_(rsids))
            .distinct()
        )
        for row in conn.execute(stmt):
            matched.append(row.rsid)

    return matched


# ── MTHFR migration from Nutrigenomics ───────────────────────────────────

_MTHFR_MIGRATION_RSIDS = {"rs1801133", "rs1801131", "rs1801394"}


def migrate_mthfr_from_nutrigenomics(sample_engine: sa.Engine) -> int:
    """Remove MTHFR-related findings from the Nutrigenomics module.

    This prevents duplicate MTHFR findings across modules. Called on
    first methylation run. Only deletes nutrigenomics findings that
    reference MTHFR/MTR rsids — not the entire nutrigenomics module.

    Args:
        sample_engine: SQLAlchemy engine for the sample database.

    Returns:
        Number of nutrigenomics MTHFR findings deleted.
    """
    with sample_engine.begin() as conn:
        # Delete nutrigenomics findings referencing MTHFR rsids
        stmt = sa.delete(findings).where(
            sa.and_(
                findings.c.module == "nutrigenomics",
                findings.c.rsid.in_(list(_MTHFR_MIGRATION_RSIDS)),
            )
        )
        result = conn.execute(stmt)
        deleted = result.rowcount

    if deleted > 0:
        logger.info(
            "mthfr_migrated_from_nutrigenomics",
            deleted_findings=deleted,
        )

    return deleted


# ── Findings storage ─────────────────────────────────────────────────────


def store_methylation_findings(
    result: MethylationResult,
    sample_engine: sa.Engine,
) -> int:
    """Store methylation findings in the sample database.

    Creates findings:
      - 5 pathway summaries (one per pathway).
      - Individual SNP findings for non-Standard called SNPs.
      - 1 compound heterozygosity finding (if applicable).

    Also runs MTHFR migration from Nutrigenomics on first call.

    Args:
        result: MethylationResult from score_methylation_pathways.
        sample_engine: SQLAlchemy engine for the sample database.

    Returns:
        Number of findings inserted.
    """
    # Migrate MTHFR findings from Nutrigenomics (idempotent)
    migrate_mthfr_from_nutrigenomics(sample_engine)

    rows: list[dict] = []

    for pr in result.pathway_results:
        # Pathway-level summary finding
        called_count = len(pr.called_snps)
        total_count = len(pr.snp_results)
        moderate_count = sum(1 for snp in pr.called_snps if snp.category == MODERATE)
        multiple_moderate_findings = moderate_count >= _MULTIPLE_MODERATE_FINDINGS_THRESHOLD

        if pr.level != STANDARD:
            level_text = f"{pr.level} consideration"
            if pr.level == MODERATE and multiple_moderate_findings:
                level_text += " (multiple moderate findings)"
        else:
            level_text = "Standard (no variants of concern)"

        finding_text = f"{pr.pathway_name} — {level_text}"

        detail = {
            "pathway_id": pr.pathway_id,
            "called_snps": called_count,
            "total_snps": total_count,
            "missing_snps": [s.rsid for s in pr.missing_snps],
            "additive_promoted": pr.additive_promoted,
            "moderate_snp_count": moderate_count,
            "multiple_moderate_findings": multiple_moderate_findings,
            "snp_details": [
                {
                    "rsid": s.rsid,
                    "gene": s.gene,
                    "variant_name": s.variant_name,
                    "genotype": s.genotype,
                    "category": s.category,
                    "effect_summary": s.effect_summary,
                    "evidence_level": s.evidence_level,
                    "coverage_note": s.coverage_note,
                }
                for s in pr.called_snps
            ],
        }

        # Collect PMIDs from all called SNPs
        all_pmids: list[str] = []
        for s in pr.called_snps:
            all_pmids.extend(s.pmids)
        unique_pmids = list(dict.fromkeys(all_pmids))

        # Pathway evidence level = max evidence among called SNPs
        max_evidence = max(
            (s.evidence_level for s in pr.called_snps),
            default=1,
        )

        rows.append(
            {
                "module": MODULE_NAME,
                "category": "pathway_summary",
                "evidence_level": max_evidence,
                "gene_symbol": None,
                "rsid": None,
                "finding_text": finding_text,
                "pathway": pr.pathway_name,
                "pathway_level": pr.level,
                "pmid_citations": json.dumps(unique_pmids),
                "detail_json": json.dumps(detail),
            }
        )

        # Individual SNP findings for non-Standard results
        for snp in pr.called_snps:
            if snp.category == STANDARD:
                continue

            snp_text = f"{snp.gene} {snp.variant_name} ({snp.genotype}) — {snp.effect_summary}"

            snp_detail: dict = {
                "variant_name": snp.variant_name,
                "genotype": snp.genotype,
                "recommendation": snp.recommendation_text,
            }
            if snp.coverage_note:
                snp_detail["coverage_note"] = snp.coverage_note

            rows.append(
                {
                    "module": MODULE_NAME,
                    "category": "snp_finding",
                    "evidence_level": snp.evidence_level,
                    "gene_symbol": snp.gene,
                    "rsid": snp.rsid,
                    "finding_text": snp_text,
                    "pathway": pr.pathway_name,
                    "pathway_level": snp.category,
                    "pmid_citations": json.dumps(snp.pmids),
                    "detail_json": json.dumps(snp_detail),
                }
            )

    # Compound heterozygosity finding
    if result.compound_het is not None and (
        result.compound_het.is_compound_het or result.compound_het.is_double_homozygous
    ):
        ch = result.compound_het
        rows.append(
            {
                "module": MODULE_NAME,
                "category": "compound_het",
                "evidence_level": 2,
                "gene_symbol": "MTHFR",
                "rsid": None,
                "finding_text": (
                    f"{ch.label}: C677T ({ch.c677t_genotype}) + "
                    f"A1298C ({ch.a1298c_genotype}). {ch.description}"
                ),
                "pathway": "Folate & MTHFR",
                "pathway_level": ELEVATED if ch.is_double_homozygous else MODERATE,
                "pmid_citations": json.dumps(["23824729", "11129332", "16825279"]),
                "detail_json": json.dumps(
                    {
                        "is_compound_het": ch.is_compound_het,
                        "is_double_homozygous": ch.is_double_homozygous,
                        "c677t_genotype": ch.c677t_genotype,
                        "a1298c_genotype": ch.a1298c_genotype,
                    }
                ),
            }
        )

    with sample_engine.begin() as conn:
        # Clear previous methylation findings
        conn.execute(sa.delete(findings).where(findings.c.module == MODULE_NAME))
        if not rows:
            logger.info("no_methylation_findings_to_store")
            return 0
        conn.execute(sa.insert(findings), rows)

    logger.info("methylation_findings_stored", count=len(rows))
    return len(rows)


# ── Annotation coverage bitmask ─────────────────────────────────────────

_BITMASK_BATCH = 500  # Stay under SQLITE_MAX_VARIABLE_NUMBER


def update_annotation_coverage_gwas(
    result: MethylationResult,
    sample_engine: sa.Engine,
) -> int:
    """OR bit 5 (GWAS Catalog, value 32) into annotation_coverage for GWAS-matched variants.

    Args:
        result: MethylationResult from :func:`score_methylation_pathways`.
        sample_engine: SQLAlchemy engine for the sample database.

    Returns:
        Number of variants updated.
    """
    if not result.gwas_matched_rsids:
        return 0

    rsid_list = sorted(set(result.gwas_matched_rsids))
    updated = 0

    with sample_engine.begin() as conn:
        for i in range(0, len(rsid_list), _BITMASK_BATCH):
            batch = rsid_list[i : i + _BITMASK_BATCH]

            stmt = (
                annotated_variants.update()
                .where(annotated_variants.c.rsid.in_(batch))
                .values(
                    annotation_coverage=sa.case(
                        (
                            annotated_variants.c.annotation_coverage.is_(None),
                            GWAS_BIT,
                        ),
                        else_=annotated_variants.c.annotation_coverage.op("|")(GWAS_BIT),
                    )
                )
            )
            res = conn.execute(stmt)
            updated += res.rowcount

    logger.info(
        "methylation_gwas_annotation_coverage_updated",
        gwas_bit=GWAS_BIT,
        gwas_matched_rsids=len(rsid_list),
        rows_updated=updated,
    )
    return updated

"""Traits & Personality module — PRS-primary with evidence-gated individual SNPs.

Implements P3-63:
  - 15 evidence-gated traits across 3 pathways (Cognitive Ability [PRS-primary],
    Personality Dimensions [Big Five], Behavioral Traits).
  - PRS-primary: reuses the generic PRS engine (P3-14) for educational attainment
    and cognitive ability weight sets.
  - ★★☆☆ hard cap on ALL findings (evidence_cap=2).
  - Module-level disclaimer (non-deterministic traits).
  - Associative language only — no directive claims.
  - DRD4 rs747302 proxy with coverage caveat.
  - ADHD cross-link to Gene Health module.

Panel definition lives in ``backend/data/panels/traits_panel.json`` (P3-62).

Scoring follows the same categorical algorithm as other pathway modules:
  - No numeric scores, no summed risk alleles, no effect-size weighting.
  - ★☆ evidence hard-caps individual SNP category at Moderate.
  - Module-level evidence cap (★★☆☆) applied to ALL stored findings.
  - Pathway level = highest category across called SNPs.

PRS pathways (cognitive_ability) delegate to ``run_prs()`` for computation.
PRS outputs include "Research Use Only" labels and ancestry mismatch warnings.

Usage::

    from backend.analysis.traits import (
        load_traits_panel,
        score_traits_pathways,
        store_traits_findings,
    )

    panel = load_traits_panel()
    results = score_traits_pathways(panel, sample_engine, reference_engine)
    store_traits_findings(results, sample_engine)
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import sqlalchemy as sa
import structlog

from backend.analysis.ancestry import get_inferred_ancestry, get_top_ancestry_fraction
from backend.analysis.evidence import TRAITS_EVIDENCE_CAP, cap_evidence_level
from backend.analysis.genotype_lookup import lookup_by_genotype
from backend.analysis.prs import (
    PRSResult,
    PRSSNPWeight,
    PRSWeightSet,
    run_prs,
    store_prs_findings,
)
from backend.analysis.zygosity import is_no_call
from backend.annotation.engine import GWAS_BIT
from backend.db.tables import annotated_variants, findings, gwas_associations, raw_variants

logger = structlog.get_logger(__name__)

# Path to the curated panel JSON (relative to this file)
_PANEL_PATH = Path(__file__).resolve().parent.parent / "data" / "panels" / "traits_panel.json"

# Pathway scoring categories
ELEVATED = "Elevated"
MODERATE = "Moderate"
STANDARD = "Standard"

# Minimum evidence level required for Elevated category
_ELEVATED_MIN_STARS = 2

# Module name for findings storage
MODULE_NAME = "traits"


# ── Data classes ──────────────────────────────────────────────────────────


@dataclass
class PanelSNP:
    """A single SNP entry from the curated traits panel."""

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
    trait_domain: str | None = None
    associative_language: bool = True
    coverage_note: str | None = None
    cross_module: dict | None = None


@dataclass
class Pathway:
    """A traits pathway with its curated SNPs."""

    id: str
    name: str
    description: str
    snps: list[PanelSNP]
    prs_primary: bool = False


@dataclass
class TraitsPanel:
    """The complete curated traits panel."""

    module: str
    version: str
    pathways: list[Pathway]
    prs_weight_sets: list[dict] = field(default_factory=list)
    special_calling: dict | None = None
    cross_module_links: list[dict] = field(default_factory=list)
    module_disclaimer: str = ""
    evidence_cap: int = TRAITS_EVIDENCE_CAP

    def all_rsids(self) -> list[str]:
        """Return all individual SNP rsids in the panel."""
        return [snp.rsid for pathway in self.pathways for snp in pathway.snps]

    def all_prs_rsids(self) -> list[str]:
        """Return all PRS weight set rsids."""
        rsids: list[str] = []
        for ws in self.prs_weight_sets:
            for w in ws.get("weights", []):
                rsids.append(w["rsid"])
        return rsids


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
    trait_domain: str | None = None
    coverage_note: str | None = None  # DRD4 proxy caveat
    cross_module: dict | None = None  # DRD4 → Gene Health


@dataclass
class PathwayResult:
    """Scoring result for a complete pathway."""

    pathway_id: str
    pathway_name: str
    pathway_description: str
    level: str  # Elevated / Moderate / Standard
    prs_primary: bool = False
    snp_results: list[SNPResult] = field(default_factory=list)

    @property
    def called_snps(self) -> list[SNPResult]:
        """SNPs that were present and genotyped in the sample."""
        return [s for s in self.snp_results if s.present_in_sample]

    @property
    def missing_snps(self) -> list[SNPResult]:
        """SNPs that were not present in the sample."""
        return [s for s in self.snp_results if not s.present_in_sample]


@dataclass
class CrossModuleFinding:
    """Cross-module link finding (e.g. DRD4 → Gene Health ADHD)."""

    rsid: str
    gene: str
    from_trait: str
    to_module: str
    link_type: str
    finding_text: str
    evidence_level: int
    pmids: list[str]
    detail: dict


@dataclass
class TraitsResult:
    """Complete traits scoring result for a sample."""

    pathway_results: list[PathwayResult] = field(default_factory=list)
    prs_results: list[PRSResult] = field(default_factory=list)
    gwas_matched_rsids: list[str] = field(default_factory=list)
    cross_module_findings: list[CrossModuleFinding] = field(default_factory=list)
    module_disclaimer: str = ""


# ── Panel loading ─────────────────────────────────────────────────────────


def load_traits_panel(panel_path: Path | None = None) -> TraitsPanel:
    """Load the curated traits panel from JSON.

    Args:
        panel_path: Optional override for the panel JSON path.
            Defaults to ``backend/data/panels/traits_panel.json``.

    Returns:
        Parsed TraitsPanel with all pathways, SNPs, and PRS weight sets.

    Raises:
        FileNotFoundError: If the panel JSON does not exist.
        json.JSONDecodeError: If the panel JSON is malformed.
    """
    path = panel_path or _PANEL_PATH
    logger.info("loading_traits_panel", path=str(path))

    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    pathways: list[Pathway] = []
    for pw_data in data["pathways"]:
        snps: list[PanelSNP] = []
        for snp_data in pw_data.get("snps", []):
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
                    trait_domain=snp_data.get("trait_domain"),
                    associative_language=snp_data.get("associative_language", True),
                    coverage_note=snp_data.get("coverage_note"),
                    cross_module=snp_data.get("cross_module"),
                )
            )
        pathways.append(
            Pathway(
                id=pw_data["id"],
                name=pw_data["name"],
                description=pw_data["description"],
                snps=snps,
                prs_primary=pw_data.get("prs_primary", False),
            )
        )

    return TraitsPanel(
        module=data["module"],
        version=data["version"],
        pathways=pathways,
        prs_weight_sets=data.get("prs_weight_sets", []),
        special_calling=data.get("special_calling"),
        cross_module_links=data.get("cross_module_links", []),
        module_disclaimer=data.get("module_disclaimer", ""),
        evidence_cap=data.get("evidence_cap", TRAITS_EVIDENCE_CAP),
    )


# ── Genotype scoring ─────────────────────────────────────────────────────


def _normalize_genotype(genotype: str | None) -> str | None:
    """Normalize genotype string for lookup.

    Handles common formats: 'CT', 'TC', '--' (no-call).
    Returns None for no-calls or missing data.
    """
    if is_no_call(genotype):
        return None
    return genotype.strip().upper()


def _score_snp(snp: PanelSNP, genotype: str | None) -> SNPResult:
    """Score a single SNP given a genotype.

    Applies evidence-level gating: ★☆ (evidence_level=1) variants
    are hard-capped at Moderate regardless of genotype.

    Also preserves DRD4 coverage notes and cross-module links.
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
            trait_domain=snp.trait_domain,
            coverage_note=snp.coverage_note,
            cross_module=snp.cross_module,
        )

    # Look up genotype effect from panel definition, harmonizing allele order
    # and strand (e.g. chip "CT" → panel "GA" for a reverse-strand-keyed SNP).
    effect = lookup_by_genotype(snp.genotype_effects, genotype)

    if effect is None:
        logger.warning(
            "unknown_genotype_for_traits_snp",
            rsid=snp.rsid,
            gene=snp.gene,
            genotype=genotype,
        )
        return SNPResult(
            rsid=snp.rsid,
            gene=snp.gene,
            variant_name=snp.variant_name,
            genotype=genotype,
            category=STANDARD,
            effect_summary=f"Genotype {genotype} not in curated panel definitions.",
            evidence_level=snp.evidence_level,
            pmids=snp.pmids,
            recommendation_text=snp.recommendation_text,
            present_in_sample=True,
            trait_domain=snp.trait_domain,
            coverage_note=snp.coverage_note,
            cross_module=snp.cross_module,
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
        trait_domain=snp.trait_domain,
        coverage_note=snp.coverage_note,
        cross_module=snp.cross_module,
    )


def _determine_pathway_level(snp_results: list[SNPResult]) -> str:
    """Determine the overall pathway category from individual SNP results.

    The pathway level is the highest category across all called SNPs.
    Ordering: Elevated > Moderate > Standard.

    Only SNPs present in the sample contribute to the pathway level.
    If no SNPs are genotyped, the pathway defaults to Standard.
    """
    called = [r for r in snp_results if r.present_in_sample]
    if not called:
        return STANDARD

    category_priority = {ELEVATED: 2, MODERATE: 1, STANDARD: 0}
    present = {r.category for r in called}
    return max(present, key=lambda c: category_priority.get(c, 0), default=STANDARD)


# ── PRS integration ──────────────────────────────────────────────────────


def _load_prs_weight_sets(panel: TraitsPanel) -> list[PRSWeightSet]:
    """Convert panel PRS weight set definitions into PRSWeightSet objects.

    Reuses the generic PRS engine (P3-14) by constructing PRSWeightSet
    instances from the panel JSON definitions.
    """
    weight_sets: list[PRSWeightSet] = []

    for ws_data in panel.prs_weight_sets:
        weights = [
            PRSSNPWeight(
                rsid=w["rsid"],
                effect_allele=w["effect_allele"],
                weight=w["weight"],
                other_allele=w.get("other_allele"),
            )
            for w in ws_data["weights"]
        ]

        weight_sets.append(
            PRSWeightSet(
                name=ws_data["name"],
                trait=ws_data["trait"],
                module=MODULE_NAME,
                source_ancestry=ws_data["source_ancestry"],
                source_study=ws_data["source_study"],
                source_pmid=ws_data["source_pmid"],
                sample_size=ws_data["sample_size"],
                weights=weights,
                reference_mean=ws_data["reference_mean"],
                reference_std=ws_data["reference_std"],
                # Conservative default: bundled traits PRS sets are treated as
                # uncalibrated unless they explicitly declare a validated
                # reference distribution for this exact score (issue #21).
                calibrated=ws_data.get("calibrated", False),
            )
        )

    return weight_sets


def _run_traits_prs(
    panel: TraitsPanel,
    sample_engine: sa.Engine,
    n_bootstrap: int = 1000,
    rng_seed: int | None = None,
) -> list[PRSResult]:
    """Run PRS computation for all traits weight sets.

    Retrieves inferred ancestry for mismatch warning, then runs
    each PRS weight set through the generic PRS engine.

    Args:
        panel: Loaded TraitsPanel.
        sample_engine: SQLAlchemy engine for the sample database.
        n_bootstrap: Bootstrap iterations (default 1000).
        rng_seed: Optional RNG seed for reproducibility.

    Returns:
        List of PRSResult objects (one per weight set).
    """
    weight_sets = _load_prs_weight_sets(panel)
    if not weight_sets:
        return []

    inferred_ancestry = get_inferred_ancestry(sample_engine)
    top_fraction = get_top_ancestry_fraction(sample_engine)

    results: list[PRSResult] = []
    for ws in weight_sets:
        result = run_prs(
            ws,
            sample_engine,
            inferred_ancestry=inferred_ancestry,
            top_ancestry_fraction=top_fraction,
            n_bootstrap=n_bootstrap,
            rng_seed=rng_seed,
        )
        # Cap PRS evidence level at module cap
        result.evidence_level = cap_evidence_level(result.evidence_level, panel.evidence_cap)
        results.append(result)

        logger.info(
            "traits_prs_computed",
            trait=result.trait,
            percentile=result.percentile,
            sufficient=result.is_sufficient,
            snps_used=result.snps_used,
            snps_total=result.snps_total,
        )

    return results


# ── Cross-module link generation ──────────────────────────────────────────


def _generate_cross_module_findings(
    pathway_results: list[PathwayResult],
    panel: TraitsPanel,
) -> list[CrossModuleFinding]:
    """Generate cross-module link findings from panel cross_module_links.

    Currently handles DRD4 VNTR → Gene Health ADHD cross-link.
    Only generates if the relevant SNP is genotyped and non-Standard.
    """
    cross_findings: list[CrossModuleFinding] = []

    # Find SNPs with cross_module metadata
    for pr in pathway_results:
        for snp in pr.called_snps:
            if snp.cross_module is None or snp.category == STANDARD:
                continue

            to_module = snp.cross_module.get("module", "")
            note = snp.cross_module.get("note", "")

            # Find matching cross_module_link in panel
            link_type = ""
            for link in panel.cross_module_links:
                if link.get("to_module") == to_module:
                    link_type = link.get("link_type", "")
                    break

            finding_text = (
                f"{snp.gene} {snp.variant_name} ({snp.genotype}) — "
                f"{snp.effect_summary} "
                f"See {to_module.replace('_', ' ').title()} module for related findings."
            )

            cross_findings.append(
                CrossModuleFinding(
                    rsid=snp.rsid,
                    gene=snp.gene,
                    from_trait=snp.trait_domain or "",
                    to_module=to_module,
                    link_type=link_type,
                    finding_text=finding_text,
                    evidence_level=cap_evidence_level(snp.evidence_level, panel.evidence_cap),
                    pmids=snp.pmids,
                    detail={
                        "genotype": snp.genotype,
                        "category": snp.category,
                        "trait_domain": snp.trait_domain,
                        "to_module": to_module,
                        "link_type": link_type,
                        "note": note,
                        "coverage_note": snp.coverage_note,
                    },
                )
            )

    return cross_findings


# ── Main scoring function ────────────────────────────────────────────────


def score_traits_pathways(
    panel: TraitsPanel,
    sample_engine: sa.Engine,
    reference_engine: sa.Engine,
    n_bootstrap: int = 1000,
    rng_seed: int | None = None,
) -> TraitsResult:
    """Score all traits pathways for a sample.

    1. Fetches raw genotypes from the sample DB for all panel rsids.
    2. Scores each individual SNP using the curated panel definitions.
    3. Applies evidence-level gating (★☆ → Moderate cap).
    4. Determines per-pathway level (highest category across SNPs).
    5. Runs PRS computation for cognitive ability weight sets.
    6. Generates cross-module link findings (DRD4 → Gene Health).
    7. Looks up GWAS associations for matched rsids.

    Args:
        panel: Loaded TraitsPanel.
        sample_engine: SQLAlchemy engine for the sample database.
        reference_engine: SQLAlchemy engine for reference.db.
        n_bootstrap: Bootstrap iterations for PRS (default 1000).
        rng_seed: Optional RNG seed for PRS reproducibility.

    Returns:
        TraitsResult with all pathway results, PRS results, cross-module
        findings, and GWAS matches.
    """
    # Fetch all individual SNP rsids from sample
    all_rsids = panel.all_rsids()
    genotypes = _fetch_genotypes(all_rsids, sample_engine)
    logger.info(
        "traits_genotypes_fetched",
        panel_rsids=len(all_rsids),
        found_in_sample=len(genotypes),
    )

    pathway_results: list[PathwayResult] = []
    for pathway in panel.pathways:
        snp_results: list[SNPResult] = []
        for snp in pathway.snps:
            gt = _normalize_genotype(genotypes.get(snp.rsid))
            result = _score_snp(snp, gt)
            snp_results.append(result)

        level = _determine_pathway_level(snp_results)
        pathway_results.append(
            PathwayResult(
                pathway_id=pathway.id,
                pathway_name=pathway.name,
                pathway_description=pathway.description,
                level=level,
                prs_primary=pathway.prs_primary,
                snp_results=snp_results,
            )
        )

    # Run PRS for cognitive ability pathway
    prs_results = _run_traits_prs(
        panel,
        sample_engine,
        n_bootstrap=n_bootstrap,
        rng_seed=rng_seed,
    )

    # Cross-module link findings (DRD4 → Gene Health ADHD)
    cross_module = _generate_cross_module_findings(pathway_results, panel)

    # Identify GWAS-matched rsids for annotation_coverage bitmask
    called_rsids = [r.rsid for pr in pathway_results for r in pr.called_snps]
    gwas_matched = _lookup_gwas_matches(called_rsids, reference_engine)

    return TraitsResult(
        pathway_results=pathway_results,
        prs_results=prs_results,
        gwas_matched_rsids=gwas_matched,
        cross_module_findings=cross_module,
        module_disclaimer=panel.module_disclaimer,
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


# ── Findings storage ─────────────────────────────────────────────────────


def store_traits_findings(
    result: TraitsResult,
    sample_engine: sa.Engine,
) -> int:
    """Store traits findings in the sample database.

    Creates findings for:
      - Pathway summaries (one per pathway with individual SNPs).
      - Individual SNP findings (non-Standard called SNPs).
      - PRS findings (via store_prs_findings, one per weight set).
      - Cross-module findings (DRD4 → Gene Health).

    All evidence levels are capped at ★★☆☆ per module cap.

    Args:
        result: TraitsResult from score_traits_pathways.
        sample_engine: SQLAlchemy engine for the sample database.

    Returns:
        Number of findings inserted (individual SNP + pathway summaries
        + cross-module; PRS findings stored separately).
    """
    rows: list[dict] = []

    for pr in result.pathway_results:
        # Skip pathway summaries for PRS-primary pathways (PRS has its own findings)
        if pr.prs_primary and not pr.snp_results:
            continue

        # Pathway-level summary finding
        called_count = len(pr.called_snps)
        total_count = len(pr.snp_results)
        finding_text = (
            f"{pr.pathway_name} — {pr.level} consideration"
            if pr.level != STANDARD
            else f"{pr.pathway_name} — Standard (no variants of note)"
        )

        detail = {
            "pathway_id": pr.pathway_id,
            "called_snps": called_count,
            "total_snps": total_count,
            "prs_primary": pr.prs_primary,
            "missing_snps": [s.rsid for s in pr.missing_snps],
            "snp_details": [
                {
                    "rsid": s.rsid,
                    "gene": s.gene,
                    "variant_name": s.variant_name,
                    "genotype": s.genotype,
                    "category": s.category,
                    "effect_summary": s.effect_summary,
                    "evidence_level": s.evidence_level,
                    "trait_domain": s.trait_domain,
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

        # Pathway evidence level = max evidence among called SNPs, capped
        max_evidence = max(
            (s.evidence_level for s in pr.called_snps),
            default=1,
        )
        max_evidence = cap_evidence_level(max_evidence, TRAITS_EVIDENCE_CAP)

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
                "trait_domain": snp.trait_domain,
                "associative_language": True,
                "research_use_only": True,
            }
            if snp.coverage_note:
                snp_detail["coverage_note"] = snp.coverage_note
            if snp.cross_module:
                snp_detail["cross_module"] = snp.cross_module

            # Cap evidence at module level
            capped_evidence = cap_evidence_level(snp.evidence_level, TRAITS_EVIDENCE_CAP)

            rows.append(
                {
                    "module": MODULE_NAME,
                    "category": "snp_finding",
                    "evidence_level": capped_evidence,
                    "gene_symbol": snp.gene,
                    "rsid": snp.rsid,
                    "finding_text": snp_text,
                    "pathway": pr.pathway_name,
                    "pathway_level": snp.category,
                    "pmid_citations": json.dumps(snp.pmids),
                    "detail_json": json.dumps(snp_detail),
                }
            )

    # Cross-module findings (DRD4 → Gene Health ADHD)
    for cross in result.cross_module_findings:
        rows.append(
            {
                "module": MODULE_NAME,
                "category": "cross_module",
                "evidence_level": cross.evidence_level,
                "gene_symbol": cross.gene,
                "rsid": cross.rsid,
                "finding_text": cross.finding_text,
                "pathway": None,
                "pathway_level": None,
                "pmid_citations": json.dumps(cross.pmids),
                "detail_json": json.dumps(cross.detail),
            }
        )

    # Store individual SNP + pathway + cross-module findings
    snp_count = 0
    if rows:
        with sample_engine.begin() as conn:
            # Clear previous non-PRS traits findings
            conn.execute(
                sa.delete(findings).where(
                    findings.c.module == MODULE_NAME,
                    findings.c.category != "prs",
                )
            )
            conn.execute(sa.insert(findings), rows)
        snp_count = len(rows)

    # Store PRS findings via the generic PRS engine
    prs_count = 0
    if result.prs_results:
        prs_count = store_prs_findings(result.prs_results, sample_engine, module=MODULE_NAME)

    total = snp_count + prs_count
    logger.info(
        "traits_findings_stored",
        snp_findings=snp_count,
        prs_findings=prs_count,
        total=total,
    )
    return total


# ── Annotation coverage bitmask ─────────────────────────────────────────

_BITMASK_BATCH = 500  # Stay under SQLITE_MAX_VARIABLE_NUMBER


def update_annotation_coverage_gwas(
    result: TraitsResult,
    sample_engine: sa.Engine,
) -> int:
    """OR bit 5 (GWAS Catalog, value 32) into annotation_coverage for GWAS-matched variants.

    Args:
        result: TraitsResult from :func:`score_traits_pathways`.
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
        "traits_gwas_annotation_coverage_updated",
        gwas_bit=GWAS_BIT,
        gwas_matched_rsids=len(rsid_list),
        rows_updated=updated,
    )
    return updated

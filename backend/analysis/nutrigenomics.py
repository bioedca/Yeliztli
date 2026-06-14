"""Curated nutrigenomics SNP panel and categorical pathway scoring.

Implements P3-08 and provides the foundation for P3-09:
  - P3-08: Curated nutrigenomics SNP panel — MTHFR, VDR, FUT2, HFE,
    TCF7L2, APOE, FADS1/2, PPARG, MC4R, FTO, LCT.  Panel definition
    lives in ``backend/data/panels/nutrigenomics_panel.json``.
  - P3-09: Categorical pathway scoring (Elevated / Moderate / Standard)
    with evidence-level gating.

Pathways:
    Folate Metabolism, Vitamin D, B12, Omega-3, Iron, Lactose Tolerance.

No numeric scores, no summed risk alleles, no effect-size weighting.
Each pathway is assigned one of three categories:

    Elevated   — ≥★★ evidence AND clinically meaningful genotype.
    Moderate   — ★★ evidence with het/moderate-effect genotype,
                 or ★☆ evidence (hard-capped).
    Standard   — No concerning variants, or only ★☆ evidence with
                 non-risk genotype.

Key rule: ★☆ variants hard-cap pathway at Moderate regardless of
genotype — Elevated is structurally gated on replicated evidence.

Usage::

    from backend.analysis.nutrigenomics import (
        load_nutrigenomics_panel,
        score_nutrigenomics_pathways,
        store_nutrigenomics_findings,
    )

    panel = load_nutrigenomics_panel()
    results = score_nutrigenomics_pathways(panel, sample_engine, reference_engine)
    store_nutrigenomics_findings(results, sample_engine)
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import sqlalchemy as sa
import structlog

from backend.analysis.genotype_lookup import is_strand_ambiguous, lookup_by_genotype
from backend.analysis.zygosity import is_no_call
from backend.annotation.engine import GWAS_BIT
from backend.db.tables import annotated_variants, findings, gwas_associations, raw_variants

logger = structlog.get_logger(__name__)

# Path to the curated panel JSON (relative to this file)
_PANEL_PATH = (
    Path(__file__).resolve().parent.parent / "data" / "panels" / "nutrigenomics_panel.json"
)

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


# ── Data classes ──────────────────────────────────────────────────────────


@dataclass
class PanelSNP:
    """A single SNP entry from the curated panel."""

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
    # Optional ancestry-conditional caveat. When the called category is one of
    # ``applies_to_categories`` and the sample's inferred ancestry is NOT in
    # ``confident_ancestries`` (including unknown), ``caveat_text`` is appended to
    # the effect summary and the result is flagged ``ancestry_caveated``. Used for
    # markers whose validity is ancestry-specific (e.g. the European LCT -13910
    # lactase marker, which does not predict non-persistence outside European/
    # South-Asian ancestry — #181).
    ancestry_caveat: dict | None = None


@dataclass
class Pathway:
    """A nutrient pathway with its curated SNPs."""

    id: str
    name: str
    description: str
    snps: list[PanelSNP]


@dataclass
class NutrigenomicsPanel:
    """The complete curated nutrigenomics panel."""

    module: str
    version: str
    pathways: list[Pathway]

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
    # True when an ancestry-conditional caveat was applied because the sample's
    # inferred ancestry does not support this marker's model (see #181).
    ancestry_caveated: bool = False


@dataclass
class PathwayResult:
    """Scoring result for a complete pathway."""

    pathway_id: str
    pathway_name: str
    level: str  # Elevated / Moderate / Standard
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
class NutrigenomicsResult:
    """Complete nutrigenomics scoring result for a sample."""

    pathway_results: list[PathwayResult] = field(default_factory=list)
    gwas_matched_rsids: list[str] = field(default_factory=list)


# ── Panel loading ─────────────────────────────────────────────────────────


def load_nutrigenomics_panel(panel_path: Path | None = None) -> NutrigenomicsPanel:
    """Load the curated nutrigenomics panel from JSON.

    Args:
        panel_path: Optional override for the panel JSON path.
            Defaults to ``backend/data/panels/nutrigenomics_panel.json``.

    Returns:
        Parsed NutrigenomicsPanel with all pathways and SNPs.

    Raises:
        FileNotFoundError: If the panel JSON does not exist.
        json.JSONDecodeError: If the panel JSON is malformed.
    """
    path = panel_path or _PANEL_PATH
    logger.info("loading_nutrigenomics_panel", path=str(path))

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
                    ancestry_caveat=snp_data.get("ancestry_caveat"),
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

    return NutrigenomicsPanel(
        module=data["module"],
        version=data["version"],
        pathways=pathways,
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


def _apply_ancestry_caveat(
    snp: PanelSNP, category: str, effect_summary: str, inferred_ancestry: str | None
) -> tuple[str, bool]:
    """Append an ancestry-conditional caveat to ``effect_summary`` when warranted.

    Returns ``(effect_summary, ancestry_caveated)``. The caveat fires when the SNP
    carries an ``ancestry_caveat`` block, the called ``category`` is one it applies
    to, and the sample's ``inferred_ancestry`` is **not** in its confident set
    (an unknown/``None`` ancestry also caveats — the marker model can't be
    confirmed). The category itself is left unchanged; the caveat downgrades the
    *certainty* of the call in the user-facing text (see #181).
    """
    cfg = snp.ancestry_caveat
    if not cfg:
        return effect_summary, False
    applies_to = cfg.get("applies_to_categories", [])
    if category not in applies_to:
        return effect_summary, False
    confident = cfg.get("confident_ancestries", [])
    if inferred_ancestry is not None and inferred_ancestry in confident:
        return effect_summary, False
    caveat = cfg.get("caveat_text", "")
    return (f"{effect_summary} {caveat}".strip() if caveat else effect_summary), True


def _score_snp(
    snp: PanelSNP, genotype: str | None, inferred_ancestry: str | None = None
) -> SNPResult:
    """Score a single SNP given a genotype.

    Applies evidence-level gating: ★☆ (evidence_level=1) variants
    are hard-capped at Moderate regardless of genotype. When the SNP carries an
    ``ancestry_caveat`` and the sample's ``inferred_ancestry`` doesn't support the
    marker model, an ancestry/coverage caveat is appended and the result is
    flagged ``ancestry_caveated`` (see :func:`_apply_ancestry_caveat`).

    Args:
        snp: The panel SNP definition.
        genotype: The sample's genotype string, or None if absent.
        inferred_ancestry: The sample's inferred top ancestry code (e.g. ``EUR``),
            or None if ancestry was not inferred.

    Returns:
        SNPResult with the assigned category and effect summary.
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
                f"possibly strand-flipped call. Confirm the genotyping strand (or use a "
                f"sequencing-based result) before interpreting this locus."
            ),
            evidence_level=snp.evidence_level,
            pmids=snp.pmids,
            recommendation_text=snp.recommendation_text,
            present_in_sample=True,
        )

    # Look up genotype effect from panel definition, harmonizing allele order
    # and strand (e.g. chip "CT" → panel "GA" for MTHFR C677T rs1801133).
    effect = lookup_by_genotype(snp.genotype_effects, genotype)

    if effect is None:
        # Unknown genotype — default to Standard
        logger.warning(
            "unknown_genotype_for_panel_snp",
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
        )

    category = effect["category"]
    effect_summary = effect["effect_summary"]

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

    # Ancestry-conditional caveat (e.g. the European LCT -13910 marker is not a
    # valid non-persistence predictor outside European/South-Asian ancestry, #181).
    effect_summary, ancestry_caveated = _apply_ancestry_caveat(
        snp, category, effect_summary, inferred_ancestry
    )
    if ancestry_caveated:
        logger.info(
            "ancestry_caveat_applied",
            rsid=snp.rsid,
            category=category,
            inferred_ancestry=inferred_ancestry,
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
        ancestry_caveated=ancestry_caveated,
    )


def _determine_pathway_level(snp_results: list[SNPResult]) -> str:
    """Determine the overall pathway category from individual SNP results.

    The pathway level is the highest category across all called SNPs.
    Ordering: Elevated > Moderate > Standard.

    Only SNPs present in the sample contribute to the pathway level.
    If no SNPs are genotyped, the pathway defaults to Standard.
    """
    # Strand-indeterminate palindromic homozygotes carry no trustworthy category,
    # so they neither raise nor lower the pathway level (#170/#269).
    called = [r for r in snp_results if r.present_in_sample and r.category != INDETERMINATE]
    if not called:
        return STANDARD

    category_priority = {ELEVATED: 2, MODERATE: 1, STANDARD: 0}
    present = {r.category for r in called}
    return max(present, key=lambda c: category_priority.get(c, 0), default=STANDARD)


# ── Main scoring function ────────────────────────────────────────────────


def score_nutrigenomics_pathways(
    panel: NutrigenomicsPanel,
    sample_engine: sa.Engine,
    reference_engine: sa.Engine,
) -> NutrigenomicsResult:
    """Score all nutrigenomics pathways for a sample.

    1. Fetches raw genotypes from the sample DB for all panel rsids.
    2. Scores each SNP using the curated panel definitions.
    3. Applies evidence-level gating.
    4. Determines per-pathway level (highest category across SNPs).
    5. Looks up GWAS associations for matched rsids.

    Args:
        panel: Loaded NutrigenomicsPanel.
        sample_engine: SQLAlchemy engine for the sample database.
        reference_engine: SQLAlchemy engine for reference.db.

    Returns:
        NutrigenomicsResult with all pathway results and GWAS matches.
    """
    # Fetch all panel rsids from sample
    all_rsids = panel.all_rsids()
    genotypes = _fetch_genotypes(all_rsids, sample_engine)

    # Inferred ancestry gates ancestry-conditional caveats (e.g. the European LCT
    # -13910 lactase marker — #181). None when ancestry has not been inferred, in
    # which case the marker model can't be confirmed and the caveat still applies.
    from backend.analysis.ancestry import get_inferred_ancestry

    inferred_ancestry = get_inferred_ancestry(sample_engine)
    logger.info(
        "nutrigenomics_genotypes_fetched",
        panel_rsids=len(all_rsids),
        found_in_sample=len(genotypes),
        inferred_ancestry=inferred_ancestry,
    )

    pathway_results: list[PathwayResult] = []
    for pathway in panel.pathways:
        snp_results: list[SNPResult] = []
        for snp in pathway.snps:
            gt = _normalize_genotype(genotypes.get(snp.rsid))
            result = _score_snp(snp, gt, inferred_ancestry)
            snp_results.append(result)

        level = _determine_pathway_level(snp_results)
        pathway_results.append(
            PathwayResult(
                pathway_id=pathway.id,
                pathway_name=pathway.name,
                level=level,
                snp_results=snp_results,
            )
        )

    # Identify GWAS-matched rsids for annotation_coverage bitmask (P3-09a)
    gwas_matched = _lookup_gwas_matches(
        [r.rsid for pr in pathway_results for r in pr.called_snps],
        reference_engine,
    )

    return NutrigenomicsResult(
        pathway_results=pathway_results,
        gwas_matched_rsids=gwas_matched,
    )


def _fetch_genotypes(
    rsids: list[str],
    sample_engine: sa.Engine,
) -> dict[str, str]:
    """Fetch raw genotypes from sample DB for the given rsids.

    Returns:
        Dict mapping rsid → genotype string.
    """
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
    """Look up which rsids have GWAS Catalog associations.

    Returns:
        List of rsids that have at least one GWAS association.
    """
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


def store_nutrigenomics_findings(
    result: NutrigenomicsResult,
    sample_engine: sa.Engine,
) -> int:
    """Store nutrigenomics findings in the sample database.

    Creates one finding per pathway (summary) plus one finding per
    called SNP with a non-Standard category.

    Args:
        result: NutrigenomicsResult from score_nutrigenomics_pathways.
        sample_engine: SQLAlchemy engine for the sample database.

    Returns:
        Number of findings inserted.
    """
    rows: list[dict] = []

    for pr in result.pathway_results:
        # Pathway-level summary finding
        called_count = len(pr.called_snps)
        total_count = len(pr.snp_results)
        finding_text = (
            f"{pr.pathway_name} — {pr.level} consideration"
            if pr.level != STANDARD
            else f"{pr.pathway_name} — Standard (no variants of concern)"
        )

        detail = {
            "pathway_id": pr.pathway_id,
            "called_snps": called_count,
            "total_snps": total_count,
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
                    "ancestry_caveated": s.ancestry_caveated,
                }
                for s in pr.called_snps
            ],
        }

        # Collect PMIDs from all called SNPs
        all_pmids = []
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
                "module": "nutrigenomics",
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
            rows.append(
                {
                    "module": "nutrigenomics",
                    "category": "snp_finding",
                    "evidence_level": snp.evidence_level,
                    "gene_symbol": snp.gene,
                    "rsid": snp.rsid,
                    "finding_text": snp_text,
                    "pathway": pr.pathway_name,
                    "pathway_level": snp.category,
                    "pmid_citations": json.dumps(snp.pmids),
                    "detail_json": json.dumps(
                        {
                            "variant_name": snp.variant_name,
                            "genotype": snp.genotype,
                            "recommendation": snp.recommendation_text,
                            "ancestry_caveated": snp.ancestry_caveated,
                        }
                    ),
                }
            )

    with sample_engine.begin() as conn:
        # Clear previous nutrigenomics findings
        conn.execute(sa.delete(findings).where(findings.c.module == "nutrigenomics"))
        if not rows:
            logger.info("no_nutrigenomics_findings_to_store")
            return 0
        conn.execute(sa.insert(findings), rows)

    logger.info("nutrigenomics_findings_stored", count=len(rows))
    return len(rows)


# ── Annotation coverage bitmask ─────────────────────────────────────────

_BITMASK_BATCH = 500  # Stay under SQLITE_MAX_VARIABLE_NUMBER


def update_annotation_coverage_gwas(
    result: NutrigenomicsResult,
    sample_engine: sa.Engine,
) -> int:
    """OR bit 5 (GWAS Catalog, value 32) into annotation_coverage for GWAS-matched variants.

    After the nutrigenomics module runs, every variant whose rsid was
    found in the GWAS Catalog (stored in ``gwas_matched_rsids``) gets
    bit 5 set in its ``annotation_coverage`` column in
    ``annotated_variants``.

    Args:
        result: NutrigenomicsResult from :func:`score_nutrigenomics_pathways`.
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
        "gwas_annotation_coverage_updated",
        gwas_bit=GWAS_BIT,
        gwas_matched_rsids=len(rsid_list),
        rows_updated=updated,
    )
    return updated

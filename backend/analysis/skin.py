"""Gene Skin module — categorical pathway scoring with MC1R multi-allele calling.

Implements P3-55:
  - 20 trait findings across 4 pathway cards (Pigmentation & UV Response,
    Skin Barrier & Inflammation, Oxidative Stress & Aging, Skin Micronutrients).
  - MC1R multi-allele haplotype-aware calling (0/1/2 R alleles).
  - FLG R501X flagged as Insufficient Data due to limited FLG LoF coverage.
  - Cross-links to Cancer (MC1R/melanoma), Nutrigenomics (VDR vitamin D),
    and Allergy (FLG atopic march).
  - Categorical outputs only (Elevated / Moderate / Standard).

Panel definition lives in ``backend/data/panels/skin_panel.json`` (P3-54).

Scoring follows the same algorithm as nutrigenomics / fitness / sleep:
  - No numeric scores, no summed risk alleles, no effect-size weighting.
  - ★☆ evidence hard-caps pathway at Moderate.
  - Elevated requires ≥★★ evidence + clinically meaningful genotype.
  - Pathway level = highest category across called SNPs.

Usage::

    from backend.analysis.skin import (
        load_skin_panel,
        score_skin_pathways,
        store_skin_findings,
    )

    panel = load_skin_panel()
    results = score_skin_pathways(panel, sample_engine, reference_engine)
    store_skin_findings(results, sample_engine)
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import sqlalchemy as sa
import structlog

from backend.analysis.genotype_lookup import genotype_candidates, lookup_by_genotype
from backend.analysis.zygosity import is_no_call
from backend.annotation.engine import GWAS_BIT
from backend.db.tables import annotated_variants, findings, gwas_associations, raw_variants

logger = structlog.get_logger(__name__)

# Path to the curated panel JSON (relative to this file)
_PANEL_PATH = Path(__file__).resolve().parent.parent / "data" / "panels" / "skin_panel.json"

# Pathway scoring categories
ELEVATED = "Elevated"
MODERATE = "Moderate"
STANDARD = "Standard"

# Minimum evidence level required for Elevated category
_ELEVATED_MIN_STARS = 2

# Module name for findings storage
MODULE_NAME = "skin"

# MC1R rsids used for multi-allele calling
_MC1R_RSIDS = frozenset({"rs1805007", "rs1805008", "rs1805009", "rs885479"})


# ── Data classes ──────────────────────────────────────────────────────────


@dataclass
class PanelSNP:
    """A single SNP entry from the curated skin panel."""

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
    mc1r_allele_class: str | None = None
    cross_module: dict | None = None
    coverage_note: str | None = None
    insufficient_data_flag: bool = False


@dataclass
class Pathway:
    """A skin pathway with its curated SNPs."""

    id: str
    name: str
    description: str
    snps: list[PanelSNP]


@dataclass
class SkinPanel:
    """The complete curated skin panel."""

    module: str
    version: str
    pathways: list[Pathway]
    special_calling: dict | None = None

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
    mc1r_allele_class: str | None = None
    coverage_note: str | None = None
    insufficient_data_flag: bool = False


@dataclass
class PathwayResult:
    """Scoring result for a complete pathway."""

    pathway_id: str
    pathway_name: str
    pathway_description: str
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
class MC1RAggregateResult:
    """MC1R multi-allele aggregate calling result."""

    r_allele_count: int
    r_allele_rsids: list[str]
    total_mc1r_called: int
    risk_label: str
    risk_description: str
    evidence_level: int
    pmids: list[str]


@dataclass
class CrossModuleFinding:
    """Cross-module reference finding."""

    rsid: str
    gene: str
    source_module: str
    target_module: str
    finding_text: str
    evidence_level: int
    pmids: list[str]
    detail: dict


@dataclass
class SkinResult:
    """Complete skin scoring result for a sample."""

    pathway_results: list[PathwayResult] = field(default_factory=list)
    gwas_matched_rsids: list[str] = field(default_factory=list)
    mc1r_aggregate: MC1RAggregateResult | None = None
    cross_module_findings: list[CrossModuleFinding] = field(default_factory=list)
    flg_insufficient_data: bool = False


# ── Panel loading ─────────────────────────────────────────────────────────


def load_skin_panel(panel_path: Path | None = None) -> SkinPanel:
    """Load the curated skin panel from JSON.

    Args:
        panel_path: Optional override for the panel JSON path.
            Defaults to ``backend/data/panels/skin_panel.json``.

    Returns:
        Parsed SkinPanel with all pathways and SNPs.

    Raises:
        FileNotFoundError: If the panel JSON does not exist.
        json.JSONDecodeError: If the panel JSON is malformed.
    """
    path = panel_path or _PANEL_PATH
    logger.info("loading_skin_panel", path=str(path))

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
                    mc1r_allele_class=snp_data.get("mc1r_allele_class"),
                    cross_module=snp_data.get("cross_module"),
                    coverage_note=snp_data.get("coverage_note"),
                    insufficient_data_flag=snp_data.get("insufficient_data_flag", False),
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

    return SkinPanel(
        module=data["module"],
        version=data["version"],
        pathways=pathways,
        special_calling=data.get("special_calling"),
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
            mc1r_allele_class=snp.mc1r_allele_class,
            coverage_note=snp.coverage_note,
            insufficient_data_flag=snp.insufficient_data_flag,
        )

    # Look up genotype effect from panel definition, harmonizing allele order
    # and strand (e.g. chip "CT" → panel "GA" for a reverse-strand-keyed SNP).
    effect = lookup_by_genotype(snp.genotype_effects, genotype)

    if effect is None:
        logger.warning(
            "unknown_genotype_for_skin_snp",
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
            mc1r_allele_class=snp.mc1r_allele_class,
            coverage_note=snp.coverage_note,
            insufficient_data_flag=snp.insufficient_data_flag,
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
        mc1r_allele_class=snp.mc1r_allele_class,
        coverage_note=snp.coverage_note,
        insufficient_data_flag=snp.insufficient_data_flag,
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


# ── MC1R multi-allele calling ────────────────────────────────────────────


def _r_allele_dosage(genotype: str, risk_allele: str, ref_allele: str) -> int | None:
    """Strand- and order-aware count of risk ('R') alleles in a genotype.

    Mirrors the harmonization in :func:`lookup_by_genotype` (reference strand and
    allele order first, Watson–Crick complement as a fallback) so the MC1R
    aggregate counts the SAME R-allele dosage that individual SNP scoring already
    resolved. A chip that reports rs1805007 R151C on the reverse strand (e.g.
    ``"AG"`` for the curated ``C/T`` SNP) is scored as a one-R-allele carrier by
    ``_score_snp``; the previous raw ``genotype.count("T")`` returned 0 for that
    representation and undercounted it in the aggregate (issue #24).

    Returns the risk-allele dosage (0, 1, or 2), or ``None`` when no strand/order
    frame explains the genotype with the curated ``{risk, ref}`` allele pair (e.g.
    an off-panel or triallelic call) — the caller then leaves it uncounted rather
    than guessing.
    """
    risk = risk_allele.upper()
    ref = ref_allele.upper()
    for candidate in genotype_candidates(genotype):
        alleles = candidate.split("/") if "/" in candidate else list(candidate)
        if set(alleles) <= {risk, ref}:
            return alleles.count(risk)
    return None


def _compute_mc1r_aggregate(
    pathway_results: list[PathwayResult],
    panel: SkinPanel,
) -> MC1RAggregateResult | None:
    """Compute MC1R multi-allele haplotype-aware aggregate result.

    Counts the number of strong 'R' alleles across all MC1R positions
    (rs1805007, rs1805008, rs1805009). The mild 'r' allele (rs885479)
    contributes to total MC1R variant count but does NOT count toward
    the R-allele aggregate risk state.

    Risk states:
      - 0 R alleles → Low UV Sensitivity
      - 1 R allele  → Moderate UV Sensitivity
      - 2+ R alleles → High UV Sensitivity
    """
    if panel.special_calling is None:
        return None

    mc1r_config = panel.special_calling.get("MC1R_multi_allele")
    if mc1r_config is None:
        return None

    # Collect MC1R SNP results from the Pigmentation pathway
    pigmentation_pr = next(
        (pr for pr in pathway_results if pr.pathway_id == "pigmentation_uv"),
        None,
    )
    if pigmentation_pr is None:
        return None

    mc1r_results = [s for s in pigmentation_pr.called_snps if s.rsid in _MC1R_RSIDS]
    if not mc1r_results:
        return None

    allele_classes = mc1r_config.get("allele_classes", {})
    risk_states = mc1r_config.get("risk_states", {})

    # Count R alleles: for each MC1R SNP with class "R", count risk alleles
    r_allele_count = 0
    r_allele_rsids: list[str] = []

    for snp_result in mc1r_results:
        allele_class = allele_classes.get(snp_result.rsid)
        if allele_class != "R":
            continue

        # Count risk alleles in genotype
        if snp_result.genotype is None:
            continue

        # Find the risk/ref alleles for this SNP from panel
        risk_allele = None
        ref_allele = None
        for pathway in panel.pathways:
            for snp in pathway.snps:
                if snp.rsid == snp_result.rsid:
                    risk_allele = snp.risk_allele
                    ref_allele = snp.ref_allele
                    break
            if risk_allele is not None:
                break

        if risk_allele is None or ref_allele is None:
            continue

        # Count risk-allele dosage strand-/order-aware (consistent with the
        # per-SNP lookup), so a reverse-strand-complemented carrier is not
        # undercounted to 0 (issue #24).
        dosage = _r_allele_dosage(snp_result.genotype, risk_allele, ref_allele)
        if dosage:
            r_allele_count += dosage
            r_allele_rsids.append(snp_result.rsid)

    # Determine risk state
    if r_allele_count == 0:
        state_key = "0_R_alleles"
    elif r_allele_count == 1:
        state_key = "1_R_allele"
    else:
        state_key = "2_R_alleles"

    state = risk_states.get(state_key, {})

    # Collect all MC1R PMIDs
    all_pmids: list[str] = []
    for s in mc1r_results:
        all_pmids.extend(s.pmids)
    unique_pmids = list(dict.fromkeys(all_pmids))

    # Evidence level for aggregate = max of R-allele SNPs (★★★)
    max_evidence = max((s.evidence_level for s in mc1r_results), default=2)

    return MC1RAggregateResult(
        r_allele_count=r_allele_count,
        r_allele_rsids=r_allele_rsids,
        total_mc1r_called=len(mc1r_results),
        risk_label=state.get("label", "Unknown"),
        risk_description=state.get("description", ""),
        evidence_level=max_evidence,
        pmids=unique_pmids,
    )


# ── Cross-module references ──────────────────────────────────────────────


def _generate_cross_module_findings(
    pathway_results: list[PathwayResult],
    panel: SkinPanel,
    mc1r_aggregate: MC1RAggregateResult | None,
) -> list[CrossModuleFinding]:
    """Generate cross-module reference findings.

    Cross-links:
      - MC1R → Cancer module (melanoma predisposition)
      - FLG → Allergy module (atopic march)
      - VDR → Nutrigenomics module (vitamin D)
    """
    cross_findings: list[CrossModuleFinding] = []
    seen_keys: set[tuple[str, str]] = set()

    for pr in pathway_results:
        for snp_result in pr.called_snps:
            if snp_result.category == STANDARD:
                continue

            # Find the panel SNP to get cross_module metadata
            panel_snp = _find_panel_snp(panel, snp_result.rsid)
            if panel_snp is None or panel_snp.cross_module is None:
                continue

            target_module = panel_snp.cross_module["module"]
            note = panel_snp.cross_module["note"]

            # Build cross-module finding text
            if snp_result.gene == "MC1R" and mc1r_aggregate is not None:
                cross_text = (
                    f"MC1R {snp_result.variant_name} ({snp_result.genotype}) — "
                    f"{mc1r_aggregate.risk_label} ({mc1r_aggregate.r_allele_count} R allele"
                    f"{'s' if mc1r_aggregate.r_allele_count != 1 else ''}). {note}"
                )
            elif snp_result.gene == "FLG":
                cross_text = (
                    f"FLG {snp_result.variant_name} ({snp_result.genotype}) — "
                    f"Skin barrier variant. {note}"
                )
            else:
                cross_text = (
                    f"{snp_result.gene} {snp_result.variant_name} ({snp_result.genotype}) — {note}"
                )

            # Deduplicate at variant granularity, not gene-only: distinct VDR
            # polymorphisms (FokI rs2228570, BsmI rs1544410) share gene "VDR"
            # but are different vitamin-D signals — for melanoma they even point
            # in opposite directions — and must not collapse into one cross-link.
            # MC1R is the deliberate exception: its three RHC alleles aggregate
            # into a single MC1R → Cancer link reporting the total R-allele
            # count, so key on the gene for the MC1R aggregate path, else rsid.
            if snp_result.gene == "MC1R" and mc1r_aggregate is not None:
                dedup_identity = snp_result.gene
            else:
                dedup_identity = snp_result.rsid
            dedup_key = (dedup_identity, target_module)
            if dedup_key in seen_keys:
                continue

            seen_keys.add(dedup_key)
            cross_findings.append(
                CrossModuleFinding(
                    rsid=snp_result.rsid,
                    gene=snp_result.gene,
                    source_module="skin",
                    target_module=target_module,
                    finding_text=cross_text,
                    evidence_level=snp_result.evidence_level,
                    pmids=snp_result.pmids,
                    detail={
                        "genotype": snp_result.genotype,
                        "source_pathway": pr.pathway_name,
                        "target_module": target_module,
                        "cross_module_note": note,
                    },
                )
            )

    return cross_findings


def _find_panel_snp(panel: SkinPanel, rsid: str) -> PanelSNP | None:
    """Find a PanelSNP by rsid."""
    for pathway in panel.pathways:
        for snp in pathway.snps:
            if snp.rsid == rsid:
                return snp
    return None


# ── Main scoring function ────────────────────────────────────────────────


def score_skin_pathways(
    panel: SkinPanel,
    sample_engine: sa.Engine,
    reference_engine: sa.Engine,
) -> SkinResult:
    """Score all skin pathways for a sample.

    1. Fetches raw genotypes from the sample DB for all panel rsids.
    2. Scores each SNP using the curated panel definitions.
    3. Applies evidence-level gating.
    4. Determines per-pathway level (highest category across SNPs).
    5. Computes MC1R multi-allele aggregate (0/1/2 R alleles).
    6. Generates cross-module reference findings.
    7. Looks up GWAS associations for matched rsids.

    Args:
        panel: Loaded SkinPanel.
        sample_engine: SQLAlchemy engine for the sample database.
        reference_engine: SQLAlchemy engine for reference.db.

    Returns:
        SkinResult with all pathway results, MC1R aggregate,
        cross-module findings, and GWAS matches.
    """
    # Fetch all panel rsids from sample
    all_rsids = panel.all_rsids()
    genotypes = _fetch_genotypes(all_rsids, sample_engine)
    logger.info(
        "skin_genotypes_fetched",
        panel_rsids=len(all_rsids),
        found_in_sample=len(genotypes),
    )

    pathway_results: list[PathwayResult] = []
    flg_insufficient = False

    for pathway in panel.pathways:
        snp_results: list[SNPResult] = []
        for snp in pathway.snps:
            gt = _normalize_genotype(genotypes.get(snp.rsid))
            result = _score_snp(snp, gt)
            snp_results.append(result)

            # Track FLG insufficient data flag
            if snp.insufficient_data_flag and result.present_in_sample:
                flg_insufficient = True

        level = _determine_pathway_level(snp_results)
        pathway_results.append(
            PathwayResult(
                pathway_id=pathway.id,
                pathway_name=pathway.name,
                pathway_description=pathway.description,
                level=level,
                snp_results=snp_results,
            )
        )

    # MC1R multi-allele aggregate calling
    mc1r_aggregate = _compute_mc1r_aggregate(pathway_results, panel)

    # Cross-module reference findings
    cross_module = _generate_cross_module_findings(
        pathway_results,
        panel,
        mc1r_aggregate,
    )

    # Identify GWAS-matched rsids for annotation_coverage bitmask
    gwas_matched = _lookup_gwas_matches(
        [r.rsid for pr in pathway_results for r in pr.called_snps],
        reference_engine,
    )

    return SkinResult(
        pathway_results=pathway_results,
        gwas_matched_rsids=gwas_matched,
        mc1r_aggregate=mc1r_aggregate,
        cross_module_findings=cross_module,
        flg_insufficient_data=flg_insufficient,
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


def store_skin_findings(
    result: SkinResult,
    sample_engine: sa.Engine,
) -> int:
    """Store skin findings in the sample database.

    Creates up to 20 findings:
      - 4 pathway summaries (one per pathway).
      - Up to 10 individual SNP findings (non-Standard called SNPs).
      - 1 MC1R multi-allele aggregate summary.
      - 1 FLG insufficient data caveat finding (if FLG is genotyped).
      - Up to 4 cross-module reference findings (MC1R→Cancer,
        FLG→Allergy, VDR→Nutrigenomics).

    Args:
        result: SkinResult from score_skin_pathways.
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
                    "mc1r_allele_class": s.mc1r_allele_class,
                    "coverage_note": s.coverage_note,
                    "insufficient_data_flag": s.insufficient_data_flag,
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
            if snp.mc1r_allele_class:
                snp_detail["mc1r_allele_class"] = snp.mc1r_allele_class
            if snp.coverage_note:
                snp_detail["coverage_note"] = snp.coverage_note
            if snp.insufficient_data_flag:
                snp_detail["insufficient_data_flag"] = True

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

    # MC1R multi-allele aggregate summary
    if result.mc1r_aggregate is not None:
        agg = result.mc1r_aggregate
        mc1r_text = (
            f"MC1R multi-allele summary: {agg.risk_label} "
            f"({agg.r_allele_count} R allele"
            f"{'s' if agg.r_allele_count != 1 else ''} "
            f"across {agg.total_mc1r_called} MC1R variants called). "
            f"{agg.risk_description}"
        )
        rows.append(
            {
                "module": MODULE_NAME,
                "category": "mc1r_aggregate",
                "evidence_level": agg.evidence_level,
                "gene_symbol": "MC1R",
                "rsid": None,
                "finding_text": mc1r_text,
                "pathway": "Pigmentation & UV Response",
                "pathway_level": None,
                "pmid_citations": json.dumps(agg.pmids),
                "detail_json": json.dumps(
                    {
                        "r_allele_count": agg.r_allele_count,
                        "r_allele_rsids": agg.r_allele_rsids,
                        "total_mc1r_called": agg.total_mc1r_called,
                        "risk_label": agg.risk_label,
                        "risk_description": agg.risk_description,
                    }
                ),
            }
        )

    # FLG insufficient data caveat finding
    if result.flg_insufficient_data:
        flg_snp = _find_panel_snp_from_result(result, "rs61816761")

        caveat_text = (
            "FLG R501X - Insufficient Data. "
            "Result is based on the single assayed rs61816761 nonsense variant. "
            "A negative result does not rule out other FLG loss-of-function "
            "alleles such as 2282del4. "
            "Full sequencing is required for comprehensive FLG assessment."
        )
        rows.append(
            {
                "module": MODULE_NAME,
                "category": "insufficient_data",
                "evidence_level": 2,
                "gene_symbol": "FLG",
                "rsid": "rs61816761",
                "finding_text": caveat_text,
                "pathway": "Skin Barrier & Inflammation",
                "pathway_level": None,
                "pmid_citations": json.dumps(
                    flg_snp.pmids if flg_snp else ["16550169", "16444271"]
                ),
                "detail_json": json.dumps(
                    {
                        "observed_variant": "FLG R501X (p.Arg501Ter)",
                        "proxy_target": None,
                        "insufficient_data_reason": (
                            "Single FLG loss-of-function site; does not "
                            "capture 2282del4 or other FLG null mutations. "
                            "Full sequencing required for comprehensive FLG assessment."
                        ),
                    }
                ),
            }
        )

    # Cross-module findings
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

    if not rows:
        logger.info("no_skin_findings_to_store")
        return 0

    with sample_engine.begin() as conn:
        # Clear previous skin findings
        conn.execute(sa.delete(findings).where(findings.c.module == MODULE_NAME))
        conn.execute(sa.insert(findings), rows)

    logger.info("skin_findings_stored", count=len(rows))
    return len(rows)


def _find_panel_snp_from_result(result: SkinResult, rsid: str) -> SNPResult | None:
    """Find an SNPResult by rsid from pathway results."""
    for pr in result.pathway_results:
        for snp in pr.snp_results:
            if snp.rsid == rsid:
                return snp
    return None


# ── Annotation coverage bitmask ─────────────────────────────────────────

_BITMASK_BATCH = 500  # Stay under SQLITE_MAX_VARIABLE_NUMBER


def update_annotation_coverage_gwas(
    result: SkinResult,
    sample_engine: sa.Engine,
) -> int:
    """OR bit 5 (GWAS Catalog, value 32) into annotation_coverage for GWAS-matched variants.

    Args:
        result: SkinResult from :func:`score_skin_pathways`.
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
        "skin_gwas_annotation_coverage_updated",
        gwas_bit=GWAS_BIT,
        gwas_matched_rsids=len(rsid_list),
        rows_updated=updated,
    )
    return updated

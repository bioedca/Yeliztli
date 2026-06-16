"""Gene Health expansion module — categorical pathway scoring for disease conditions.

Implements P3-65:
  - 17 disease conditions grouped by system across 4 pathway cards
    (Neurological, Metabolic, Autoimmune, Sensory).
  - HLA tag-SNP proxies are ordinary genotype-effect rows, but carry
    ancestry-conditional caveats when LD/risk transfer is not well supported.
  - No celiac/histamine combined assessments.
  - Cross-links to APOE (Alzheimer's), Allergy (celiac), Methylation (MTHFR),
    Nutrigenomics (FTO), and Traits (ADHD).

Panel definition lives in ``backend/data/panels/gene_health_panel.json``.

Scoring follows the same algorithm as nutrigenomics / fitness / sleep / skin / allergy:
  - No numeric scores, no summed risk alleles, no effect-size weighting.
  - ★☆ evidence hard-caps pathway at Moderate.
  - Elevated requires ≥★★ evidence + clinically meaningful genotype.
  - Pathway level = highest category across called SNPs.

Usage::

    from backend.analysis.gene_health import (
        load_gene_health_panel,
        score_gene_health_pathways,
        store_gene_health_findings,
    )

    panel = load_gene_health_panel()
    results = score_gene_health_pathways(panel, sample_engine, reference_engine)
    store_gene_health_findings(results, sample_engine)
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import sqlalchemy as sa
import structlog

from backend.analysis.genotype_lookup import (
    is_acgt_genotype,
    is_strand_ambiguous,
    lookup_by_genotype,
)
from backend.analysis.zygosity import is_no_call
from backend.annotation.engine import GWAS_BIT
from backend.annotation.gwas import gwas_matched_rsids
from backend.db.tables import (
    annotated_variants,
    findings,
    panel_coverage,
    raw_variants,
)

logger = structlog.get_logger(__name__)

# Path to the curated panel JSON (relative to this file)
_PANEL_PATH = Path(__file__).resolve().parent.parent / "data" / "panels" / "gene_health_panel.json"

# Pathway scoring categories
ELEVATED = "Elevated"
MODERATE = "Moderate"
STANDARD = "Standard"
# Runtime-only category for a palindromic (A/T or C/G) homozygote whose strand —
# and therefore its curated category — cannot be resolved from the array genotype
# alone (#170/#269). Surfaced with a strand caveat but withheld from pathway-level
# aggregation and cross-module emission; never a confident (possibly flipped) call.
# Not a valid panel-JSON category.
INDETERMINATE = "Indeterminate"

# Minimum evidence level required for Elevated category
_ELEVATED_MIN_STARS = 2

# Module name for findings storage
MODULE_NAME = "gene_health"
_CARRIER_CONTEXT_CATEGORY = "carrier_context"


# ── Data classes ──────────────────────────────────────────────────────────


@dataclass
class PanelSNP:
    """A single SNP entry from the curated gene health panel."""

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
    cross_module: dict | None = None
    coverage_note: str | None = None
    # Optional ancestry-conditional caveat. When the called category is one of
    # ``applies_to_categories`` and the sample's inferred ancestry is NOT in
    # ``confident_ancestries`` (including unknown), ``caveat_text`` is surfaced
    # as a coverage note and the result is flagged ``ancestry_caveated``.
    ancestry_caveat: dict | None = None
    # For indel loci typed as vendor I/D codes (e.g. GJB2 35delG / rs80338939):
    # maps the parser's canonical sorted-pair indel call ("DD"/"DI"/"II") to the
    # curated biological genotype key, so the carrier/homozygous model is
    # reachable instead of being discarded as a no-call. Encodes the standard
    # consumer-array deletion=D / insertion=I convention, declared per-rsID so D
    # is only read as the deletion where that is variant-specifically true.
    # Provenance: this is the same convention already shipped for deletion loci
    # elsewhere in the codebase — CFTR F508del (DD->hom_alt/DI->het/II->hom_ref
    # in backend/analysis/carrier_status.py) and APOL1 G2 (rs71785313,
    # risk_allele="D" in apol1_panel.json) — not a novel per-vendor assumption.
    # None for ordinary ACGT loci (issue #159).
    indel_genotype_map: dict[str, str] | None = None


@dataclass
class Pathway:
    """A gene health pathway with its curated SNPs."""

    id: str
    name: str
    description: str
    snps: list[PanelSNP]


@dataclass
class GeneHealthPanel:
    """The complete curated gene health panel."""

    module: str
    version: str
    pathways: list[Pathway]
    cross_module_links: dict | None = None
    module_disclaimer: str | None = None

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
class GeneHealthResult:
    """Complete gene health scoring result for a sample."""

    pathway_results: list[PathwayResult] = field(default_factory=list)
    gwas_matched_rsids: list[str] = field(default_factory=list)
    cross_module_findings: list[CrossModuleFinding] = field(default_factory=list)
    panel_coverage_rows: list[dict] = field(default_factory=list)


# ── Panel loading ─────────────────────────────────────────────────────────


def load_gene_health_panel(panel_path: Path | None = None) -> GeneHealthPanel:
    """Load the curated gene health panel from JSON.

    Args:
        panel_path: Optional override for the panel JSON path.
            Defaults to ``backend/data/panels/gene_health_panel.json``.

    Returns:
        Parsed GeneHealthPanel with all pathways and SNPs.

    Raises:
        FileNotFoundError: If the panel JSON does not exist.
        json.JSONDecodeError: If the panel JSON is malformed.
    """
    path = panel_path or _PANEL_PATH
    logger.info("loading_gene_health_panel", path=str(path))

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
                    cross_module=snp_data.get("cross_module"),
                    coverage_note=snp_data.get("coverage_note"),
                    ancestry_caveat=snp_data.get("ancestry_caveat"),
                    indel_genotype_map=snp_data.get("indel_genotype_map"),
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

    return GeneHealthPanel(
        module=data["module"],
        version=data["version"],
        pathways=pathways,
        cross_module_links=data.get("cross_module_links"),
        module_disclaimer=data.get("module_disclaimer"),
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


def _map_indel_genotype(snp: PanelSNP, raw: str | None) -> str | None:
    """Translate a vendor I/D-coded indel call to the SNP's curated genotype key.

    Consumer-array parsers canonicalize indels to sorted I/D pairs ("DD", "DI",
    "II"; see ``backend/ingestion/base.py``), and :func:`is_no_call` treats those
    as no-calls because most modules cannot map a generic I/D code to ref/alt —
    so without this step an interpretable deletion call at an indel locus is
    silently discarded before lookup (issue #159). When a locus declares an
    ``indel_genotype_map``, return the curated genotype key for the call so the
    carrier/homozygous model is reachable; otherwise return ``None`` so the
    normal ACGT/no-call path runs unchanged.

    The map is declared per-rsID (e.g. ``rs80338939`` GJB2 c.35delG:
    ``DD``→``delG/delG``, ``DI``→``G/delG``, ``II``→``GG``), encoding the standard
    deletion=D / insertion=I convention only where it is variant-specifically
    true — mirroring the reviewed APOL1 G2 (``rs71785313``) indel handling.
    """
    if raw is None or not snp.indel_genotype_map:
        return None
    return snp.indel_genotype_map.get(raw.strip().upper())


def _apply_ancestry_caveat(
    snp: PanelSNP,
    category: str,
    coverage_note: str | None,
    inferred_ancestry: str | None,
) -> tuple[str | None, bool]:
    """Return an ancestry-conditional coverage note when the marker model needs it."""
    cfg = snp.ancestry_caveat
    if not cfg:
        return coverage_note, False
    applies_to = set(cfg.get("applies_to_categories", []))
    if category not in applies_to:
        return coverage_note, False

    confident = {str(code).upper() for code in cfg.get("confident_ancestries", [])}
    if inferred_ancestry is not None and inferred_ancestry.upper() in confident:
        return coverage_note, False

    caveat = cfg.get("caveat_text", "")
    if not caveat:
        return coverage_note, True
    if coverage_note:
        return f"{coverage_note} {caveat}".strip(), True
    return caveat, True


def _score_snp(
    snp: PanelSNP,
    genotype: str | None,
    inferred_ancestry: str | None = None,
) -> SNPResult:
    """Score a single SNP given a genotype.

    Applies evidence-level gating: ★☆ (evidence_level=1) variants
    are hard-capped at Moderate regardless of genotype. Ancestry-conditional
    caveats are surfaced as coverage notes for markers whose proxy model is not
    confident for the sample's inferred ancestry.
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
    # flipped one (e.g. FTO rs9939609 AA↔TT). Compare CATEGORIES (not the full
    # effect dicts) so a palindromic homozygote whose two strands share a category
    # but differ only in summary text — e.g. PPARG rs1801282 (both Standard) — is
    # not falsely withheld.
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
    # (incl. slash-delimited indels like "delG/G") and strand via lookup_by_genotype.
    effect = lookup_by_genotype(snp.genotype_effects, genotype)

    if effect is None:
        logger.warning(
            "unknown_genotype_for_gene_health_snp",
            rsid=snp.rsid,
            gene=snp.gene,
            genotype=genotype,
        )
        # A present, real-nucleotide genotype that resolves to no curated entry is
        # NOT baseline: it carries an allele this locus does not model (a third/rare
        # allele or an unkeyed pair), so it is withheld as Indeterminate rather than
        # silently scored Standard (which would hide a carrier as "no effect"). Only
        # non-nucleotide tokens (indels, no-calls) fall through to the Standard
        # default. (#608, mirroring the fitness/methylation fix in #730.)
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

    coverage_note, ancestry_caveated = _apply_ancestry_caveat(
        snp=snp,
        category=category,
        coverage_note=snp.coverage_note,
        inferred_ancestry=inferred_ancestry,
    )
    if ancestry_caveated:
        logger.info(
            "gene_health_ancestry_caveat_applied",
            rsid=snp.rsid,
            category=category,
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
        coverage_note=coverage_note,
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


# ── Cross-module references ──────────────────────────────────────────────


def _generate_cross_module_findings(
    pathway_results: list[PathwayResult],
    panel: GeneHealthPanel,
) -> list[CrossModuleFinding]:
    """Generate cross-module reference findings.

    Cross-links:
      - APOE (Alzheimer's) → APOE module
      - Celiac-related SNPs → Allergy module
      - MTHFR variants → Methylation module
      - FTO variants → Nutrigenomics module
      - ADHD-related SNPs → Traits module
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
            cross_text = (
                f"{snp_result.gene} {snp_result.variant_name} ({snp_result.genotype}) — {note}"
            )

            # Deduplicate at variant granularity, not gene-only: distinct SNPs
            # under one gene (e.g. VDR FokI rs2228570 / BsmI rs1544410) are
            # different signals and must not collapse into a single cross-link
            # (#315; mirrors skin #309 / allergy #197 / #92). gene_health has no
            # MC1R-style aggregate path, so keying on rsid alone is sufficient.
            dedup_key = (snp_result.rsid, target_module)
            if dedup_key in seen_keys:
                continue

            seen_keys.add(dedup_key)
            cross_findings.append(
                CrossModuleFinding(
                    rsid=snp_result.rsid,
                    gene=snp_result.gene,
                    source_module=MODULE_NAME,
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


def _find_panel_snp(panel: GeneHealthPanel, rsid: str) -> PanelSNP | None:
    """Find a PanelSNP by rsid."""
    for pathway in panel.pathways:
        for snp in pathway.snps:
            if snp.rsid == rsid:
                return snp
    return None


# ── Panel coverage tracking ──────────────────────────────────────────────


def _compute_panel_coverage(
    panel: GeneHealthPanel,
    genotypes: dict[str, str],
) -> list[dict]:
    """Compute panel coverage rows for the panel_coverage table.

    Classifies each panel SNP as called/no_call/not_on_array.
    """
    rows: list[dict] = []
    for pathway in panel.pathways:
        for snp in pathway.snps:
            raw_gt = genotypes.get(snp.rsid)
            if raw_gt is None:
                status = "not_on_array"
            elif _map_indel_genotype(snp, raw_gt) is not None:
                # A vendor I/D code that an indel locus can resolve (e.g. GJB2
                # 35delG DD/DI/II) is a real, interpretable call — keep coverage
                # consistent with scoring rather than letting is_no_call() below
                # mark a now-scored locus as no_call (issue #159).
                status = "called"
            elif is_no_call(raw_gt):
                status = "no_call"
            else:
                status = "called"

            rows.append(
                {
                    "module": MODULE_NAME,
                    "rsid": snp.rsid,
                    "gene": snp.gene,
                    "expected_trait": snp.variant_name,
                    "coverage_status": status,
                }
            )
    return rows


# ── Main scoring function ────────────────────────────────────────────────


def score_gene_health_pathways(
    panel: GeneHealthPanel,
    sample_engine: sa.Engine,
    reference_engine: sa.Engine,
) -> GeneHealthResult:
    """Score all gene health pathways for a sample.

    1. Fetches raw genotypes from the sample DB for all panel rsids.
    2. Scores each SNP using the curated panel definitions.
    3. Applies evidence-level gating.
    4. Determines per-pathway level (highest category across SNPs).
    5. Generates cross-module reference findings.
    6. Looks up GWAS associations for matched rsids.
    7. Computes panel coverage tracking.

    Args:
        panel: Loaded GeneHealthPanel.
        sample_engine: SQLAlchemy engine for the sample database.
        reference_engine: SQLAlchemy engine for reference.db.

    Returns:
        GeneHealthResult with all pathway results, cross-module findings,
        and GWAS matches.
    """
    # Fetch all panel rsids from sample
    all_rsids = panel.all_rsids()
    genotypes = _fetch_genotypes(all_rsids, sample_engine)
    from backend.analysis.ancestry import get_inferred_ancestry

    inferred_ancestry = get_inferred_ancestry(sample_engine)
    logger.info(
        "gene_health_genotypes_fetched",
        panel_rsids=len(all_rsids),
        found_in_sample=len(genotypes),
    )

    pathway_results: list[PathwayResult] = []

    for pathway in panel.pathways:
        snp_results: list[SNPResult] = []
        for snp in pathway.snps:
            raw = genotypes.get(snp.rsid)
            # Variant-specific indel loci (e.g. GJB2 35delG) translate a vendor
            # I/D code to the curated key *before* the no-call normalization that
            # would otherwise discard it (issue #159); ordinary loci fall through
            # to the standard ACGT/no-call path unchanged.
            mapped = _map_indel_genotype(snp, raw)
            gt = mapped if mapped is not None else _normalize_genotype(raw)
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

    # Cross-module reference findings
    cross_module = _generate_cross_module_findings(
        pathway_results,
        panel,
    )

    # Identify GWAS-matched rsids for annotation_coverage bitmask
    gwas_matched = sorted(
        gwas_matched_rsids(
            [r.rsid for pr in pathway_results for r in pr.called_snps],
            reference_engine,
        )
    )

    # Panel coverage tracking
    coverage_rows = _compute_panel_coverage(panel, genotypes)

    return GeneHealthResult(
        pathway_results=pathway_results,
        gwas_matched_rsids=gwas_matched,
        cross_module_findings=cross_module,
        panel_coverage_rows=coverage_rows,
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


# ── Findings storage ─────────────────────────────────────────────────────


def _is_standard_carrier_context(snp: SNPResult) -> bool:
    """Return true for called Standard variants that still need carrier display."""
    if not snp.present_in_sample:
        return False
    text = snp.effect_summary.lower()
    return "carrier" in text and "reproductive" in text


def store_gene_health_findings(
    result: GeneHealthResult,
    sample_engine: sa.Engine,
    module_disclaimer: str | None = None,
) -> int:
    """Store gene health findings in the sample database.

    Creates findings:
      - Pathway summaries (one per pathway).
      - Individual SNP findings for non-Standard called SNPs.
      - Cross-module reference findings (APOE, Allergy, Methylation,
        Nutrigenomics, Traits).

    Also stores panel coverage tracking rows.

    Args:
        result: GeneHealthResult from score_gene_health_pathways.
        sample_engine: SQLAlchemy engine for the sample database.
        module_disclaimer: Optional disclaimer text from the panel.

    Returns:
        Number of findings inserted.
    """
    rows: list[dict] = []

    for idx, pr in enumerate(result.pathway_results):
        # Pathway-level summary finding
        called_count = len(pr.called_snps)
        total_count = len(pr.snp_results)
        finding_text = (
            f"{pr.pathway_name} — {pr.level} consideration"
            if pr.level != STANDARD
            else f"{pr.pathway_name} — Standard (no variants of concern)"
        )

        detail: dict = {
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
                    "coverage_note": s.coverage_note,
                    "ancestry_caveated": s.ancestry_caveated,
                }
                for s in pr.called_snps
            ],
        }

        # Include module disclaimer in first pathway summary for API retrieval
        if idx == 0 and module_disclaimer:
            detail["module_disclaimer"] = module_disclaimer

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
                if _is_standard_carrier_context(snp):
                    carrier_detail: dict = {
                        "variant_name": snp.variant_name,
                        "genotype": snp.genotype,
                        "recommendation": snp.recommendation_text,
                        "carrier_context": True,
                    }
                    if snp.coverage_note:
                        carrier_detail["coverage_note"] = snp.coverage_note

                    rows.append(
                        {
                            "module": MODULE_NAME,
                            "category": _CARRIER_CONTEXT_CATEGORY,
                            "evidence_level": snp.evidence_level,
                            "gene_symbol": snp.gene,
                            "rsid": snp.rsid,
                            "finding_text": (
                                f"{snp.gene} {snp.variant_name} ({snp.genotype}) — "
                                f"{snp.effect_summary}"
                            ),
                            "pathway": pr.pathway_name,
                            "pathway_level": STANDARD,
                            "pmid_citations": json.dumps(snp.pmids),
                            "detail_json": json.dumps(carrier_detail),
                        }
                    )
                continue

            snp_text = f"{snp.gene} {snp.variant_name} ({snp.genotype}) — {snp.effect_summary}"
            if snp.coverage_note:
                snp_text = f"{snp_text} Note: {snp.coverage_note}"

            snp_detail: dict = {
                "variant_name": snp.variant_name,
                "genotype": snp.genotype,
                "recommendation": snp.recommendation_text,
                "ancestry_caveated": snp.ancestry_caveated,
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

    with sample_engine.begin() as conn:
        # Clear previous gene_health findings
        conn.execute(sa.delete(findings).where(findings.c.module == MODULE_NAME))
        if not rows:
            logger.info("no_gene_health_findings_to_store")
            return 0
        conn.execute(sa.insert(findings), rows)

        # Store panel coverage tracking
        if result.panel_coverage_rows:
            conn.execute(sa.delete(panel_coverage).where(panel_coverage.c.module == MODULE_NAME))
            conn.execute(sa.insert(panel_coverage), result.panel_coverage_rows)

    logger.info("gene_health_findings_stored", count=len(rows))
    return len(rows)


# ── Annotation coverage bitmask ─────────────────────────────────────────

_BITMASK_BATCH = 500  # Stay under SQLITE_MAX_VARIABLE_NUMBER


def update_annotation_coverage_gwas(
    result: GeneHealthResult,
    sample_engine: sa.Engine,
) -> int:
    """OR bit 5 (GWAS Catalog, value 32) into annotation_coverage for GWAS-matched variants.

    Args:
        result: GeneHealthResult from :func:`score_gene_health_pathways`.
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
        "gene_health_gwas_annotation_coverage_updated",
        gwas_bit=GWAS_BIT,
        gwas_matched_rsids=len(rsid_list),
        rows_updated=updated,
    )
    return updated

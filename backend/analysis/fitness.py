"""Gene Fitness module — categorical pathway scoring with ACTN3 three-state calling.

Implements P3-46:
  - 17 trait findings across 4 pathway cards (Endurance, Power, Recovery & Injury,
    Training Response).
  - ACTN3 R577X three-state calling (RR/RX/XX).
  - ACE I/D proxy with coverage note.
  - Categorical outputs only (Elevated / Moderate / Standard).
  - Cross-pathway context for ACTN3 (also relevant to Power) and ACE
    (also relevant to Endurance).

Panel definition lives in ``backend/data/panels/fitness_panel.json`` (P3-45).

Scoring follows the same algorithm as nutrigenomics:
  - No numeric scores, no summed risk alleles, no effect-size weighting.
  - ★☆ evidence hard-caps pathway at Moderate.
  - Elevated requires ≥★★ evidence + clinically meaningful genotype.
  - Pathway level = highest category across called SNPs.

Usage::

    from backend.analysis.fitness import (
        load_fitness_panel,
        score_fitness_pathways,
        store_fitness_findings,
    )

    panel = load_fitness_panel()
    results = score_fitness_pathways(panel, sample_engine, reference_engine)
    store_fitness_findings(results, sample_engine)
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
_PANEL_PATH = Path(__file__).resolve().parent.parent / "data" / "panels" / "fitness_panel.json"

# Pathway scoring categories
ELEVATED = "Elevated"
MODERATE = "Moderate"
STANDARD = "Standard"
# Runtime-only category for a palindromic (A/T or C/G) homozygote whose strand —
# and therefore its curated category — cannot be resolved from the array
# genotype alone (#170). It is surfaced to the user with a strand caveat but
# withheld from the pathway-level aggregation, never reported as a confident
# (possibly flipped) Standard/Elevated call. Not a valid panel-JSON category.
INDETERMINATE = "Indeterminate"

# Minimum evidence level required for Elevated category
_ELEVATED_MIN_STARS = 2

# Module name for findings storage
MODULE_NAME = "fitness"


# ── Data classes ──────────────────────────────────────────────────────────


@dataclass
class PanelSNP:
    """A single SNP entry from the curated fitness panel."""

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
    three_state_calling: dict[str, str] | None = None
    coverage_note: str | None = None


@dataclass
class Pathway:
    """A fitness pathway with its curated SNPs."""

    id: str
    name: str
    description: str
    snps: list[PanelSNP]


@dataclass
class FitnessPanel:
    """The complete curated fitness panel."""

    module: str
    version: str
    pathways: list[Pathway]
    additional_genes: dict | None = None
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
    three_state_label: str | None = None  # RR/RX/XX for ACTN3
    coverage_note: str | None = None  # Proxy caveat for ACE


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

    @property
    def indeterminate_snps(self) -> list[SNPResult]:
        """Called SNPs whose strand — and therefore category — is unresolved.

        These are palindromic (A/T or C/G) homozygotes that the panel scored
        ``INDETERMINATE`` (#170). They neither raise nor lower the pathway level,
        but their presence means the pathway is not a confident "no variants of
        concern" result (#270).
        """
        return [s for s in self.snp_results if s.present_in_sample and s.category == INDETERMINATE]


@dataclass
class CrossContextFinding:
    """Cross-pathway context finding for SNPs relevant to multiple pathways."""

    rsid: str
    gene: str
    source_pathway: str
    context_pathway: str
    finding_text: str
    evidence_level: int
    pmids: list[str]
    detail: dict


@dataclass
class FitnessResult:
    """Complete fitness scoring result for a sample."""

    pathway_results: list[PathwayResult] = field(default_factory=list)
    gwas_matched_rsids: list[str] = field(default_factory=list)
    cross_context_findings: list[CrossContextFinding] = field(default_factory=list)


# ── Panel loading ─────────────────────────────────────────────────────────


def load_fitness_panel(panel_path: Path | None = None) -> FitnessPanel:
    """Load the curated fitness panel from JSON.

    Args:
        panel_path: Optional override for the panel JSON path.
            Defaults to ``backend/data/panels/fitness_panel.json``.

    Returns:
        Parsed FitnessPanel with all pathways and SNPs.

    Raises:
        FileNotFoundError: If the panel JSON does not exist.
        json.JSONDecodeError: If the panel JSON is malformed.
    """
    path = panel_path or _PANEL_PATH
    logger.info("loading_fitness_panel", path=str(path))

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
                    three_state_calling=snp_data.get("three_state_calling"),
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

    return FitnessPanel(
        module=data["module"],
        version=data["version"],
        pathways=pathways,
        additional_genes=data.get("additional_genes"),
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


def _resolve_three_state(
    snp: PanelSNP,
    genotype: str | None,
) -> str | None:
    """Resolve ACTN3 R577X three-state calling label (RR/RX/XX).

    Returns None if the SNP has no three_state_calling metadata or
    the genotype doesn't match.
    """
    if snp.three_state_calling is None or genotype is None:
        return None

    # Harmonize allele order and strand when matching the three-state label.
    return lookup_by_genotype(snp.three_state_calling, genotype)


def _score_snp(snp: PanelSNP, genotype: str | None) -> SNPResult:
    """Score a single SNP given a genotype.

    Applies evidence-level gating: ★☆ (evidence_level=1) variants
    are hard-capped at Moderate regardless of genotype.

    Also resolves ACTN3 three-state calling labels and ACE coverage notes.
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
            three_state_label=None,
            coverage_note=snp.coverage_note,
        )

    # Palindromic-SNP strand guard: for an A/T or C/G homozygote whose curated
    # category differs between strands, the array strand cannot be resolved from
    # the genotype string, so withhold the category instead of risking the
    # flipped one (e.g. FTO rs9939609 AA↔TT — #170).
    if is_strand_ambiguous(snp.genotype_effects, genotype):
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
            three_state_label=None,
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
            "unknown_genotype_for_fitness_snp",
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
            three_state_label=_resolve_three_state(snp, genotype),
            coverage_note=snp.coverage_note,
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
        three_state_label=_resolve_three_state(snp, genotype),
        coverage_note=snp.coverage_note,
    )


def _determine_pathway_level(snp_results: list[SNPResult]) -> str:
    """Determine the overall pathway category from individual SNP results.

    The pathway level is the highest category across all called SNPs.
    Ordering: Elevated > Moderate > Standard.

    Only SNPs present in the sample contribute to the pathway level.
    If no SNPs are genotyped, the pathway defaults to Standard.
    """
    # Strand-indeterminate palindromic homozygotes carry no trustworthy category,
    # so they neither raise nor lower the pathway level (#170).
    called = [r for r in snp_results if r.present_in_sample and r.category != INDETERMINATE]
    if not called:
        return STANDARD

    category_priority = {ELEVATED: 2, MODERATE: 1, STANDARD: 0}
    present = {r.category for r in called}
    return max(present, key=lambda c: category_priority.get(c, 0), default=STANDARD)


# ── Cross-pathway context ─────────────────────────────────────────────────


def _generate_cross_context_findings(
    pathway_results: list[PathwayResult],
    panel: FitnessPanel,
) -> list[CrossContextFinding]:
    """Generate cross-pathway context findings for ACTN3 and ACE.

    ACTN3 appears in Endurance but is equally relevant to Power.
    ACE appears in Power but is equally relevant to Endurance.
    """
    cross_findings: list[CrossContextFinding] = []

    if panel.additional_genes is None:
        return cross_findings

    # Find ACTN3 result from Endurance pathway
    actn3_config = panel.additional_genes.get("ACTN3_power_context")
    if actn3_config:
        endurance_pr = next(
            (pr for pr in pathway_results if pr.pathway_id == "endurance"),
            None,
        )
        if endurance_pr:
            actn3_result = next(
                (s for s in endurance_pr.called_snps if s.rsid == "rs1815739"),
                None,
            )
            if actn3_result and actn3_result.three_state_label in ("RX", "XX"):
                # ACTN3 is reported under Endurance but is power-relevant. Surface a
                # Power-pathway context for X-allele carriers regardless of the
                # endurance category — XX is now Standard (not an endurance call), so
                # this must key off the three-state label, not the category (gh #182).
                three_state = actn3_result.three_state_label or ""
                if three_state == "RX":
                    context_text = (
                        f"ACTN3 R577X ({actn3_result.genotype}) — {three_state} genotype. "
                        "Mixed muscle fiber profile suited to both endurance and power activities."
                    )
                else:
                    context_text = (
                        f"ACTN3 R577X ({actn3_result.genotype}) — {three_state} genotype. "
                        "Shift toward slow-twitch fibers; reduced power/sprint advantage."
                    )

                cross_findings.append(
                    CrossContextFinding(
                        rsid="rs1815739",
                        gene="ACTN3",
                        source_pathway="Endurance",
                        context_pathway="Power",
                        finding_text=context_text,
                        evidence_level=actn3_result.evidence_level,
                        pmids=actn3_result.pmids,
                        detail={
                            "three_state_label": three_state,
                            "genotype": actn3_result.genotype,
                            "source_category": actn3_result.category,
                            "source_pathway": "Endurance",
                        },
                    )
                )

    # Find ACE result from Power pathway
    ace_config = panel.additional_genes.get("ACE_endurance_context")
    if ace_config:
        power_pr = next(
            (pr for pr in pathway_results if pr.pathway_id == "power"),
            None,
        )
        if power_pr:
            ace_result = next(
                (s for s in power_pr.called_snps if s.rsid == "rs4341"),
                None,
            )
            if ace_result and ace_result.category != STANDARD:
                # Reuse the softened, non-deterministic row-level summary instead
                # of hardcoded genotype-specific performance strings. ACE I/D
                # sport-performance evidence is heterogeneous, sport-specific, and
                # not a reliable individual predictor — the broad meta-analysis
                # finds no overall power/endurance association (Psatha et al. 2024,
                # PMID 38760851) — so the cross-context text must not assert a
                # deterministic power/endurance outcome (gh #257). Keeping a single
                # source of truth (the panel effect_summary) also prevents this
                # text from drifting out of sync with the curated row again.
                context_text = (
                    f"ACE I/D proxy ({ace_result.genotype}) — {ace_result.effect_summary}"
                )

                cross_findings.append(
                    CrossContextFinding(
                        rsid="rs4341",
                        gene="ACE",
                        source_pathway="Power",
                        context_pathway="Endurance",
                        finding_text=context_text,
                        evidence_level=ace_result.evidence_level,
                        pmids=ace_result.pmids,
                        detail={
                            "genotype": ace_result.genotype,
                            "source_category": ace_result.category,
                            "coverage_note": ace_result.coverage_note,
                            "source_pathway": "Power",
                        },
                    )
                )

    return cross_findings


# ── Main scoring function ────────────────────────────────────────────────


def score_fitness_pathways(
    panel: FitnessPanel,
    sample_engine: sa.Engine,
    reference_engine: sa.Engine,
) -> FitnessResult:
    """Score all fitness pathways for a sample.

    1. Fetches raw genotypes from the sample DB for all panel rsids.
    2. Scores each SNP using the curated panel definitions.
    3. Applies evidence-level gating and ACTN3 three-state calling.
    4. Determines per-pathway level (highest category across SNPs).
    5. Generates cross-pathway context findings.
    6. Looks up GWAS associations for matched rsids.

    Args:
        panel: Loaded FitnessPanel.
        sample_engine: SQLAlchemy engine for the sample database.
        reference_engine: SQLAlchemy engine for reference.db.

    Returns:
        FitnessResult with all pathway results, cross-context findings,
        and GWAS matches.
    """
    # Fetch all panel rsids from sample
    all_rsids = panel.all_rsids()
    genotypes = _fetch_genotypes(all_rsids, sample_engine)
    logger.info(
        "fitness_genotypes_fetched",
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
                snp_results=snp_results,
            )
        )

    # Cross-pathway context findings (ACTN3 ↔ Power, ACE ↔ Endurance)
    cross_context = _generate_cross_context_findings(pathway_results, panel)

    # Identify GWAS-matched rsids for annotation_coverage bitmask
    gwas_matched = _lookup_gwas_matches(
        [r.rsid for pr in pathway_results for r in pr.called_snps],
        reference_engine,
    )

    return FitnessResult(
        pathway_results=pathway_results,
        gwas_matched_rsids=gwas_matched,
        cross_context_findings=cross_context,
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


def store_fitness_findings(
    result: FitnessResult,
    sample_engine: sa.Engine,
) -> int:
    """Store fitness findings in the sample database.

    Creates up to 17 findings:
      - 4 pathway summaries (one per pathway).
      - Up to 8 individual SNP findings (non-Standard called SNPs).
      - Up to 5 cross-context findings (ACTN3→Power, ACE→Endurance context
        + additional trait-level details).

    Args:
        result: FitnessResult from score_fitness_pathways.
        sample_engine: SQLAlchemy engine for the sample database.

    Returns:
        Number of findings inserted.
    """
    rows: list[dict] = []

    for pr in result.pathway_results:
        # Pathway-level summary finding
        called_count = len(pr.called_snps)
        total_count = len(pr.snp_results)
        indeterminate = pr.indeterminate_snps
        if pr.level != STANDARD:
            finding_text = f"{pr.pathway_name} — {pr.level} consideration"
        elif indeterminate:
            # A Standard *level* with strand-unresolved calls is NOT "no variants
            # of concern": the locus was observed but its strand (and therefore
            # category) could not be resolved. Say so instead of implying a
            # confident negative (#270).
            n = len(indeterminate)
            noun = "variant" if n == 1 else "variants"
            finding_text = (
                f"{pr.pathway_name} — Standard for scored variants; {n} {noun} "
                f"observed but strand-unresolved (indeterminate) — see SNP details"
            )
        else:
            finding_text = f"{pr.pathway_name} — Standard (no variants of concern)"

        detail = {
            "pathway_id": pr.pathway_id,
            "called_snps": called_count,
            "total_snps": total_count,
            "indeterminate_snps": [s.rsid for s in indeterminate],
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
                    "three_state_label": s.three_state_label,
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

            # Build SNP finding text with three-state label if available
            if snp.three_state_label:
                snp_text = (
                    f"{snp.gene} {snp.variant_name} ({snp.genotype}) — "
                    f"{snp.three_state_label} genotype; {snp.effect_summary}"
                )
            else:
                snp_text = f"{snp.gene} {snp.variant_name} ({snp.genotype}) — {snp.effect_summary}"

            snp_detail: dict = {
                "variant_name": snp.variant_name,
                "genotype": snp.genotype,
                "recommendation": snp.recommendation_text,
            }
            if snp.three_state_label:
                snp_detail["three_state_label"] = snp.three_state_label
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

    # Cross-context findings (ACTN3 power context, ACE endurance context)
    for cross in result.cross_context_findings:
        rows.append(
            {
                "module": MODULE_NAME,
                "category": "cross_context",
                "evidence_level": cross.evidence_level,
                "gene_symbol": cross.gene,
                "rsid": cross.rsid,
                "finding_text": cross.finding_text,
                "pathway": cross.context_pathway,
                "pathway_level": None,
                "pmid_citations": json.dumps(cross.pmids),
                "detail_json": json.dumps(cross.detail),
            }
        )

    if not rows:
        logger.info("no_fitness_findings_to_store")
        return 0

    with sample_engine.begin() as conn:
        # Clear previous fitness findings
        conn.execute(sa.delete(findings).where(findings.c.module == MODULE_NAME))
        conn.execute(sa.insert(findings), rows)

    logger.info("fitness_findings_stored", count=len(rows))
    return len(rows)


# ── Annotation coverage bitmask ─────────────────────────────────────────

_BITMASK_BATCH = 500  # Stay under SQLITE_MAX_VARIABLE_NUMBER


def update_annotation_coverage_gwas(
    result: FitnessResult,
    sample_engine: sa.Engine,
) -> int:
    """OR bit 5 (GWAS Catalog, value 32) into annotation_coverage for GWAS-matched variants.

    Args:
        result: FitnessResult from :func:`score_fitness_pathways`.
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
        "fitness_gwas_annotation_coverage_updated",
        gwas_bit=GWAS_BIT,
        gwas_matched_rsids=len(rsid_list),
        rows_updated=updated,
    )
    return updated

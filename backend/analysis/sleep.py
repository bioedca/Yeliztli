"""Gene Sleep module — categorical pathway scoring with CYP1A2 metabolizer calling.

Implements P3-49:
  - 14 trait findings across 4 pathway cards (Caffeine & Sleep, Chronotype &
    Circadian Rhythm, Sleep Quality, Sleep Disorders).
  - CYP1A2 caffeine metabolizer three-state calling (rapid/intermediate/slow).
  - CYP1A2 cross-module reference to Pharmacogenomics (read, not re-compute).
  - rs2858884 HLA-DQ region marker (NOT a DQB1*06:02 / narcolepsy proxy;
    informational only, no narcolepsy risk inferred).
  - PER3 VNTR proxy with coverage note.
  - Categorical outputs only (Elevated / Moderate / Standard).

Panel definition lives in ``backend/data/panels/sleep_panel.json`` (P3-48).

Scoring follows the same algorithm as nutrigenomics / fitness:
  - No numeric scores, no summed risk alleles, no effect-size weighting.
  - ★☆ evidence hard-caps pathway at Moderate.
  - Elevated requires ≥★★ evidence + clinically meaningful genotype.
  - Pathway level = highest category across called SNPs.

Usage::

    from backend.analysis.sleep import (
        load_sleep_panel,
        score_sleep_pathways,
        store_sleep_findings,
    )

    panel = load_sleep_panel()
    results = score_sleep_pathways(panel, sample_engine, reference_engine)
    store_sleep_findings(results, sample_engine)
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import sqlalchemy as sa
import structlog

from backend.analysis.genotype_lookup import lookup_by_genotype
from backend.analysis.zygosity import is_no_call
from backend.annotation.engine import GWAS_BIT
from backend.db.tables import annotated_variants, findings, gwas_associations, raw_variants

logger = structlog.get_logger(__name__)

# Path to the curated panel JSON (relative to this file)
_PANEL_PATH = Path(__file__).resolve().parent.parent / "data" / "panels" / "sleep_panel.json"

# Pathway scoring categories
ELEVATED = "Elevated"
MODERATE = "Moderate"
STANDARD = "Standard"

# Minimum evidence level required for Elevated category
_ELEVATED_MIN_STARS = 2

# Module name for findings storage
MODULE_NAME = "sleep"


# ── Data classes ──────────────────────────────────────────────────────────


@dataclass
class PanelSNP:
    """A single SNP entry from the curated sleep panel."""

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


@dataclass
class Pathway:
    """A sleep pathway with its curated SNPs."""

    id: str
    name: str
    description: str
    snps: list[PanelSNP]


@dataclass
class SleepPanel:
    """The complete curated sleep panel."""

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
    metabolizer_state: str | None = None  # rapid/intermediate/slow for CYP1A2
    coverage_note: str | None = None  # Proxy caveat for PER3/HLA


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
class CrossModuleFinding:
    """Cross-module reference finding (CYP1A2 ↔ Pharmacogenomics)."""

    rsid: str
    gene: str
    source_module: str
    target_module: str
    finding_text: str
    evidence_level: int
    pmids: list[str]
    detail: dict


@dataclass
class SleepResult:
    """Complete sleep scoring result for a sample."""

    pathway_results: list[PathwayResult] = field(default_factory=list)
    gwas_matched_rsids: list[str] = field(default_factory=list)
    cross_module_findings: list[CrossModuleFinding] = field(default_factory=list)
    metabolizer_state: str | None = None  # CYP1A2 metabolizer state


# ── Panel loading ─────────────────────────────────────────────────────────


def load_sleep_panel(panel_path: Path | None = None) -> SleepPanel:
    """Load the curated sleep panel from JSON.

    Args:
        panel_path: Optional override for the panel JSON path.
            Defaults to ``backend/data/panels/sleep_panel.json``.

    Returns:
        Parsed SleepPanel with all pathways and SNPs.

    Raises:
        FileNotFoundError: If the panel JSON does not exist.
        json.JSONDecodeError: If the panel JSON is malformed.
    """
    path = panel_path or _PANEL_PATH
    logger.info("loading_sleep_panel", path=str(path))

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

    return SleepPanel(
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


def _resolve_metabolizer_state(
    panel: SleepPanel,
    genotype: str | None,
) -> str | None:
    """Resolve CYP1A2 caffeine metabolizer state (rapid/intermediate/slow).

    Returns None if the panel has no CYP1A2_metabolizer special calling
    or the genotype doesn't match any state.
    """
    if panel.special_calling is None or genotype is None:
        return None

    cyp_config = panel.special_calling.get("CYP1A2_metabolizer")
    if cyp_config is None:
        return None

    for state_name, state_data in cyp_config["states"].items():
        if "genotype" in state_data and state_data["genotype"] == genotype:
            return state_data["label"]
        if "genotypes" in state_data and genotype in state_data["genotypes"]:
            return state_data["label"]

    return None


def _score_snp(snp: PanelSNP, genotype: str | None, panel: SleepPanel) -> SNPResult:
    """Score a single SNP given a genotype.

    Applies evidence-level gating: ★☆ (evidence_level=1) variants
    are hard-capped at Moderate regardless of genotype.

    Also resolves CYP1A2 metabolizer state and coverage notes.
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
            metabolizer_state=None,
            coverage_note=snp.coverage_note,
        )

    # Look up genotype effect from panel definition, harmonizing allele order
    # and strand (e.g. chip "CT" → panel "GA" for a reverse-strand-keyed SNP).
    effect = lookup_by_genotype(snp.genotype_effects, genotype)

    if effect is None:
        logger.warning(
            "unknown_genotype_for_sleep_snp",
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
            metabolizer_state=(
                _resolve_metabolizer_state(panel, genotype) if snp.rsid == "rs762551" else None
            ),
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

    # Resolve CYP1A2 metabolizer state if applicable
    metabolizer = None
    if snp.rsid == "rs762551":
        metabolizer = _resolve_metabolizer_state(panel, genotype)

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
        metabolizer_state=metabolizer,
        coverage_note=snp.coverage_note,
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


# ── Cross-module references ──────────────────────────────────────────────


def _generate_cross_module_findings(
    pathway_results: list[PathwayResult],
    panel: SleepPanel,
) -> list[CrossModuleFinding]:
    """Generate CYP1A2 cross-module reference to Pharmacogenomics.

    CYP1A2 rs762551 appears in the Caffeine & Sleep pathway but is also
    a pharmacogene. The Sleep module references the PGx module but does
    NOT re-compute PGx findings — it's a display-only cross-link.
    """
    cross_findings: list[CrossModuleFinding] = []

    if panel.additional_genes is None:
        return cross_findings

    pgx_config = panel.additional_genes.get("CYP1A2_pgx_context")
    if pgx_config is None:
        return cross_findings

    # Find CYP1A2 result from Caffeine & Sleep pathway
    caffeine_pr = next(
        (pr for pr in pathway_results if pr.pathway_id == "caffeine_sleep"),
        None,
    )
    if caffeine_pr is None:
        return cross_findings

    cyp1a2_result = next(
        (s for s in caffeine_pr.called_snps if s.rsid == "rs762551"),
        None,
    )
    if cyp1a2_result is None:
        return cross_findings

    # Build cross-module reference text based on metabolizer state
    metabolizer = cyp1a2_result.metabolizer_state or "Unknown"
    cross_text = (
        f"CYP1A2 rs762551 ({cyp1a2_result.genotype}) — {metabolizer}. "
        "CYP1A2 is also a pharmacogene affecting metabolism of clozapine, "
        "theophylline, and other drugs. See Pharmacogenomics module for "
        "full drug interaction profile."
    )

    cross_findings.append(
        CrossModuleFinding(
            rsid="rs762551",
            gene="CYP1A2",
            source_module="sleep",
            target_module="pharmacogenomics",
            finding_text=cross_text,
            evidence_level=cyp1a2_result.evidence_level,
            pmids=cyp1a2_result.pmids,
            detail={
                "metabolizer_state": metabolizer,
                "genotype": cyp1a2_result.genotype,
                "source_pathway": "Caffeine & Sleep",
                "target_module": "pharmacogenomics",
                "cross_module_note": pgx_config.get("note", ""),
            },
        )
    )

    return cross_findings


# ── Main scoring function ────────────────────────────────────────────────


def score_sleep_pathways(
    panel: SleepPanel,
    sample_engine: sa.Engine,
    reference_engine: sa.Engine,
) -> SleepResult:
    """Score all sleep pathways for a sample.

    1. Fetches raw genotypes from the sample DB for all panel rsids.
    2. Scores each SNP using the curated panel definitions.
    3. Applies evidence-level gating and CYP1A2 metabolizer calling.
    4. Determines per-pathway level (highest category across SNPs).
    5. Generates CYP1A2 cross-module reference to PGx.
    6. Looks up GWAS associations for matched rsids.

    Args:
        panel: Loaded SleepPanel.
        sample_engine: SQLAlchemy engine for the sample database.
        reference_engine: SQLAlchemy engine for reference.db.

    Returns:
        SleepResult with all pathway results, cross-module findings,
        and GWAS matches.
    """
    # Fetch all panel rsids from sample
    all_rsids = panel.all_rsids()
    genotypes = _fetch_genotypes(all_rsids, sample_engine)
    logger.info(
        "sleep_genotypes_fetched",
        panel_rsids=len(all_rsids),
        found_in_sample=len(genotypes),
    )

    pathway_results: list[PathwayResult] = []
    global_metabolizer: str | None = None

    for pathway in panel.pathways:
        snp_results: list[SNPResult] = []
        for snp in pathway.snps:
            gt = _normalize_genotype(genotypes.get(snp.rsid))
            result = _score_snp(snp, gt, panel)
            snp_results.append(result)

            # Track CYP1A2 metabolizer state
            if result.metabolizer_state is not None:
                global_metabolizer = result.metabolizer_state

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

    # Cross-module reference to PGx for CYP1A2
    cross_module = _generate_cross_module_findings(pathway_results, panel)

    # Identify GWAS-matched rsids for annotation_coverage bitmask
    gwas_matched = _lookup_gwas_matches(
        [r.rsid for pr in pathway_results for r in pr.called_snps],
        reference_engine,
    )

    return SleepResult(
        pathway_results=pathway_results,
        gwas_matched_rsids=gwas_matched,
        cross_module_findings=cross_module,
        metabolizer_state=global_metabolizer,
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


def store_sleep_findings(
    result: SleepResult,
    sample_engine: sa.Engine,
) -> int:
    """Store sleep findings in the sample database.

    Creates up to 14 findings:
      - 4 pathway summaries (one per pathway).
      - Up to 6 individual SNP findings (non-Standard called SNPs).
      - 1 CYP1A2 metabolizer state summary finding.
      - 1 CYP1A2 PGx cross-module reference.
      - Up to 2 additional trait-level findings.

    Args:
        result: SleepResult from score_sleep_pathways.
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
                    "metabolizer_state": s.metabolizer_state,
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

            # Build SNP finding text with metabolizer state if available
            if snp.metabolizer_state:
                snp_text = (
                    f"{snp.gene} {snp.variant_name} ({snp.genotype}) — "
                    f"{snp.metabolizer_state}; {snp.effect_summary}"
                )
            else:
                snp_text = f"{snp.gene} {snp.variant_name} ({snp.genotype}) — {snp.effect_summary}"

            snp_detail: dict = {
                "variant_name": snp.variant_name,
                "genotype": snp.genotype,
                "recommendation": snp.recommendation_text,
            }
            if snp.metabolizer_state:
                snp_detail["metabolizer_state"] = snp.metabolizer_state
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

    # CYP1A2 metabolizer state summary finding
    if result.metabolizer_state is not None:
        rows.append(
            {
                "module": MODULE_NAME,
                "category": "metabolizer_state",
                "evidence_level": 2,
                "gene_symbol": "CYP1A2",
                "rsid": "rs762551",
                "finding_text": (
                    f"Caffeine metabolizer status: {result.metabolizer_state}. "
                    "CYP1A2 determines caffeine half-life and sensitivity "
                    "to caffeine-induced sleep disruption."
                ),
                "pathway": "Caffeine & Sleep",
                "pathway_level": None,
                "pmid_citations": json.dumps(["16522833", "26378246"]),
                "detail_json": json.dumps(
                    {
                        "metabolizer_state": result.metabolizer_state,
                        "gene": "CYP1A2",
                    }
                ),
            }
        )

    # Cross-module findings (CYP1A2 → PGx)
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
        # Clear previous sleep findings
        conn.execute(sa.delete(findings).where(findings.c.module == MODULE_NAME))
        if not rows:
            logger.info("no_sleep_findings_to_store")
            return 0
        conn.execute(sa.insert(findings), rows)

    logger.info("sleep_findings_stored", count=len(rows))
    return len(rows)


# ── Annotation coverage bitmask ─────────────────────────────────────────

_BITMASK_BATCH = 500  # Stay under SQLITE_MAX_VARIABLE_NUMBER


def update_annotation_coverage_gwas(
    result: SleepResult,
    sample_engine: sa.Engine,
) -> int:
    """OR bit 5 (GWAS Catalog, value 32) into annotation_coverage for GWAS-matched variants.

    Args:
        result: SleepResult from :func:`score_sleep_pathways`.
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
        "sleep_gwas_annotation_coverage_updated",
        gwas_bit=GWAS_BIT,
        gwas_matched_rsids=len(rsid_list),
        rows_updated=updated,
    )
    return updated

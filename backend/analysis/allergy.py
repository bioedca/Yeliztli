"""Gene Allergy & Immune Sensitivities module — categorical pathway scoring.

Implements P3-60:
  - ~30 trait findings across 4 pathway cards (Atopic Conditions,
    Drug Hypersensitivity, Food Sensitivity, Histamine Metabolism).
  - HLA proxy calling with r²/ancestry display from hla_proxy_lookup table.
  - Abacavir/HLA-B*57:01 bi-directional cross-link with Pharmacogenomics.
  - Celiac DQ2/DQ8 combined assessment at ★★★☆ with NPV >99% framing.
  - Histamine metabolism at ★☆☆☆ visually de-emphasized.
  - Cross-links to PGx (drug hypersensitivity), Skin (IL13/atopic dermatitis),
    and Nutrigenomics (celiac/gluten).

Panel definition lives in ``backend/data/panels/allergy_panel.json`` (P3-59).

Scoring follows the same algorithm as nutrigenomics / fitness / sleep / skin:
  - No numeric scores, no summed risk alleles, no effect-size weighting.
  - ★☆ evidence hard-caps pathway at Moderate.
  - Elevated requires ≥★★ evidence + clinically meaningful genotype.
  - Pathway level = highest category across called SNPs.

Usage::

    from backend.analysis.allergy import (
        load_allergy_panel,
        score_allergy_pathways,
        store_allergy_findings,
    )

    panel = load_allergy_panel()
    results = score_allergy_pathways(panel, sample_engine, reference_engine)
    store_allergy_findings(results, sample_engine)
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

import sqlalchemy as sa
import structlog

from backend.analysis.ancestry import get_inferred_ancestry
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
    hla_proxy_lookup,
    panel_coverage,
    raw_variants,
)

logger = structlog.get_logger(__name__)

# Path to the curated panel JSON (relative to this file)
_PANEL_PATH = Path(__file__).resolve().parent.parent / "data" / "panels" / "allergy_panel.json"

# Pathway scoring categories
ELEVATED = "Elevated"
MODERATE = "Moderate"
STANDARD = "Standard"
# Runtime-only category for a palindromic (A/T or C/G) homozygote whose strand —
# and therefore its curated category — cannot be resolved from the array genotype
# alone (#170/#269). Surfaced with a strand caveat but withheld from pathway-level
# aggregation and the cumulative AOC1 DAO-load tally.
INDETERMINATE = "Indeterminate"

# Minimum evidence level required for Elevated category
_ELEVATED_MIN_STARS = 2

# Minimum r2 used by the panel for clinical-grade proxy framing.
_HLA_PROXY_CLINICAL_GRADE_R2 = 0.85

# Internal ancestry inference emits AMR for the Admixed American bucket. The
# rs1061235 panel metadata names the reduced-reliability population explicitly,
# so keep the mapping local to this HLA-proxy display rule.
_HLA_PROXY_REDUCED_ANCESTRY_CODES = {"Native American": frozenset({"AMR"})}

# Module name for findings storage
MODULE_NAME = "allergy"

# HLA proxy rsids (drug hypersensitivity + celiac)
_HLA_PROXY_RSIDS = frozenset(
    {
        "rs2395029",
        "rs144012689",
        "rs1061235",
        "rs9263726",
        "rs2187668",
        "rs7454108",
    }
)

# Celiac DQ2/DQ8 rsids for combined assessment
_CELIAC_DQ2_RSID = "rs2187668"
_CELIAC_DQ8_RSID = "rs7454108"

# Histamine metabolism rsids for combined assessment
_HISTAMINE_RSIDS = frozenset({"rs10156191", "rs11558538"})


# ── Data classes ──────────────────────────────────────────────────────────


@dataclass
class PanelSNP:
    """A single SNP entry from the curated allergy panel."""

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
    hla_proxy: dict | None = None
    cross_module: dict | None = None
    coverage_note: str | None = None


@dataclass
class Pathway:
    """An allergy pathway with its curated SNPs."""

    id: str
    name: str
    description: str
    snps: list[PanelSNP]


@dataclass
class AllergyPanel:
    """The complete curated allergy panel."""

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
    hla_proxy: dict | None = None
    coverage_note: str | None = None
    coverage_status: str | None = None


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
class HLAProxyInfo:
    """HLA proxy lookup result with ancestry-specific r² values."""

    hla_allele: str
    proxy_rsid: str
    r_squared_by_pop: dict[str, float]  # e.g. {"EUR": 0.97, "AFR": 0.85}
    clinical_context: str


@dataclass
class CeliacCombinedResult:
    """Celiac DQ2/DQ8 combined assessment result."""

    state: str  # "neither", "dq2_only", "dq8_only", "both", "indeterminate"
    label: str
    description: str
    dq2_genotype: str | None
    dq8_genotype: str | None
    evidence_level: int


@dataclass
class HistamineCombinedResult:
    """Histamine metabolism combined assessment result."""

    aoc1_genotype: str | None
    hnmt_genotype: str | None
    aoc1_category: str
    hnmt_category: str
    combined_text: str
    de_emphasize: bool  # Always True for ★☆ evidence
    # Cumulative AOC1 DAO-deficiency load across the tracked AOC1 SNPs (#386). The
    # literature reports the cumulative risk-allele load — especially the
    # homozygous count — is more predictive of reduced DAO activity than the
    # presence of any single variant (Maintz 2011, Duelo 2024, Okutan 2023).
    aoc1_risk_allele_count: int = 0
    aoc1_homozygous_risk_count: int = 0
    aoc1_snps_assessed: int = 0


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
class AllergyResult:
    """Complete allergy scoring result for a sample."""

    pathway_results: list[PathwayResult] = field(default_factory=list)
    gwas_matched_rsids: list[str] = field(default_factory=list)
    hla_proxy_info: dict[str, HLAProxyInfo] = field(default_factory=dict)
    celiac_combined: CeliacCombinedResult | None = None
    histamine_combined: HistamineCombinedResult | None = None
    cross_module_findings: list[CrossModuleFinding] = field(default_factory=list)
    panel_coverage_rows: list[dict] = field(default_factory=list)
    inferred_ancestry: str | None = None


# ── Panel loading ─────────────────────────────────────────────────────────


def load_allergy_panel(panel_path: Path | None = None) -> AllergyPanel:
    """Load the curated allergy panel from JSON.

    Args:
        panel_path: Optional override for the panel JSON path.
            Defaults to ``backend/data/panels/allergy_panel.json``.

    Returns:
        Parsed AllergyPanel with all pathways and SNPs.

    Raises:
        FileNotFoundError: If the panel JSON does not exist.
        json.JSONDecodeError: If the panel JSON is malformed.
    """
    path = panel_path or _PANEL_PATH
    logger.info("loading_allergy_panel", path=str(path))

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
                    hla_proxy=snp_data.get("hla_proxy"),
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

    return AllergyPanel(
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
            hla_proxy=snp.hla_proxy,
            coverage_note=snp.coverage_note,
        )

    # Palindromic-SNP strand guard (#170/#269/#436): for an A/T or C/G homozygote
    # whose curated category differs between strands, the array strand cannot be
    # resolved from the genotype string, so withhold the category instead of
    # risking the flipped one. Compare CATEGORIES (not the full effect dicts) so a
    # homozygote whose two strands share a category is not falsely withheld.
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
            hla_proxy=snp.hla_proxy,
            coverage_note=snp.coverage_note,
        )

    # Look up genotype effect from panel definition, harmonizing allele order
    # and strand (e.g. chip "CT" → panel "GA" for a reverse-strand-keyed SNP).
    effect = lookup_by_genotype(snp.genotype_effects, genotype)

    if effect is None:
        logger.warning(
            "unknown_genotype_for_allergy_snp",
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
            hla_proxy=snp.hla_proxy,
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
        hla_proxy=snp.hla_proxy,
        coverage_note=snp.coverage_note,
    )


def _determine_pathway_level(snp_results: list[SNPResult]) -> str:
    """Determine the overall pathway category from individual SNP results.

    The pathway level is the highest category across all called SNPs.
    Ordering: Elevated > Moderate > Standard.

    Only SNPs present in the sample contribute to the pathway level.
    Strand-indeterminate palindromic homozygotes (#436) are withheld, so they do
    not contribute. If no SNPs are genotyped, the pathway defaults to Standard.
    """
    called = [r for r in snp_results if r.present_in_sample and r.category != INDETERMINATE]
    if not called:
        return STANDARD

    category_priority = {ELEVATED: 2, MODERATE: 1, STANDARD: 0}
    present = {r.category for r in called}
    return max(present, key=lambda c: category_priority.get(c, 0), default=STANDARD)


# ── HLA proxy lookup ─────────────────────────────────────────────────────


def _fetch_hla_proxy_info(
    rsids: list[str],
    reference_engine: sa.Engine,
) -> dict[str, HLAProxyInfo]:
    """Fetch HLA proxy data from the hla_proxy_lookup table.

    Returns a dict keyed by proxy_rsid with ancestry-specific r² values.
    """
    proxy_rsids = [r for r in rsids if r in _HLA_PROXY_RSIDS]
    if not proxy_rsids:
        return {}

    result: dict[str, HLAProxyInfo] = {}
    with reference_engine.connect() as conn:
        stmt = sa.select(
            hla_proxy_lookup.c.proxy_rsid,
            hla_proxy_lookup.c.hla_allele,
            hla_proxy_lookup.c.r_squared,
            hla_proxy_lookup.c.ancestry_pop,
            hla_proxy_lookup.c.clinical_context,
        ).where(hla_proxy_lookup.c.proxy_rsid.in_(proxy_rsids))

        for row in conn.execute(stmt):
            rsid = row.proxy_rsid
            if rsid not in result:
                result[rsid] = HLAProxyInfo(
                    hla_allele=row.hla_allele,
                    proxy_rsid=rsid,
                    r_squared_by_pop={},
                    clinical_context=row.clinical_context or "",
                )
            result[rsid].r_squared_by_pop[row.ancestry_pop] = row.r_squared

    return result


# ── Celiac DQ2/DQ8 combined assessment ───────────────────────────────────


def _compute_celiac_combined(
    pathway_results: list[PathwayResult],
    panel: AllergyPanel,
) -> CeliacCombinedResult | None:
    """Compute celiac DQ2/DQ8 combined assessment.

    Combines HLA-DQ2 (rs2187668) and HLA-DQ8 (rs7454108) results.
    Key clinical utility is negative predictive value: absence of both
    DQ2 and DQ8 essentially rules out celiac disease (NPV >99%).
    """
    if panel.special_calling is None:
        return None

    celiac_config = panel.special_calling.get("celiac_DQ2_DQ8_combined")
    if celiac_config is None:
        return None

    combined_states = celiac_config.get("combined_states", {})

    # Find DQ2 and DQ8 results from food_sensitivity pathway
    food_pr = next(
        (pr for pr in pathway_results if pr.pathway_id == "food_sensitivity"),
        None,
    )
    if food_pr is None:
        return None

    dq2_result = next(
        (s for s in food_pr.snp_results if s.rsid == _CELIAC_DQ2_RSID),
        None,
    )
    dq8_result = next(
        (s for s in food_pr.snp_results if s.rsid == _CELIAC_DQ8_RSID),
        None,
    )

    dq2_present = dq2_result is not None and dq2_result.present_in_sample
    dq8_present = dq8_result is not None and dq8_result.present_in_sample
    dq2_positive = dq2_present and dq2_result.category != STANDARD
    dq8_positive = dq8_present and dq8_result.category != STANDARD

    if dq2_positive and dq8_positive:
        state_key = "both"
        evidence = 3
    elif dq2_positive:
        state_key = "dq2_only"
        evidence = 3
    elif dq8_positive:
        state_key = "dq8_only"
        evidence = 3
    elif dq2_present and dq8_present:
        # Both proxies observed and reference (non-risk): a genuine negative,
        # which is the only case where the >99% NPV rule-out applies.
        state_key = "neither"
        evidence = 3
    else:
        # At least one proxy was not typed (no-call / not on this array). Missing
        # coverage is NOT a negative result — the NPV claim requires an observed
        # non-risk genotype — so withhold the exclusionary "Low Celiac Risk"
        # framing and report insufficient coverage instead (issue #27).
        state_key = "indeterminate"
        evidence = 3

    state = combined_states.get(state_key, {})

    return CeliacCombinedResult(
        state=state_key,
        label=state.get("label", "Unknown"),
        description=state.get("description", ""),
        dq2_genotype=dq2_result.genotype if dq2_result else None,
        dq8_genotype=dq8_result.genotype if dq8_result else None,
        evidence_level=evidence,
    )


# ── Histamine combined assessment ────────────────────────────────────────


def _compute_histamine_combined(
    pathway_results: list[PathwayResult],
    panel: AllergyPanel,
) -> HistamineCombinedResult | None:
    """Compute histamine metabolism combined assessment.

    Combines AOC1 (extracellular/gut DAO) and HNMT (intracellular)
    results. Both are ★☆ evidence and should be de-emphasized in UI.
    """
    if panel.special_calling is None:
        return None

    histamine_config = panel.special_calling.get("histamine_combined_assessment")
    if histamine_config is None:
        return None

    histamine_pr = next(
        (pr for pr in pathway_results if pr.pathway_id == "histamine_metabolism"),
        None,
    )
    if histamine_pr is None:
        return None

    # Map each tracked AOC1 SNP to its risk allele from the panel definition
    # (SNPResult does not carry the risk allele), so we can tally the cumulative
    # DAO-deficiency load rather than gate on a single variant (#386).
    histamine_panel_pw = next(
        (pw for pw in panel.pathways if pw.id == "histamine_metabolism"),
        None,
    )
    aoc1_risk_alleles = {
        snp.rsid: snp.risk_allele
        for snp in (histamine_panel_pw.snps if histamine_panel_pw else [])
        if snp.gene == "AOC1"
    }

    aoc1_risk_allele_count = 0
    aoc1_homozygous_risk_count = 0
    aoc1_snps_assessed = 0
    aoc1_cat = STANDARD
    rs10156191_result = None
    for s in histamine_pr.snp_results:
        if s.rsid not in aoc1_risk_alleles:
            continue
        if s.rsid == "rs10156191":
            rs10156191_result = s
        if not s.present_in_sample or not s.genotype:
            continue
        # A strand-indeterminate palindromic homozygote (e.g. rs1049793 CC/GG,
        # #436) has an unresolvable allele dosage, so it must not contribute to
        # the cumulative DAO-deficiency load.
        if s.category == INDETERMINATE:
            continue
        aoc1_snps_assessed += 1
        n_risk = s.genotype.upper().count(aoc1_risk_alleles[s.rsid].upper())
        aoc1_risk_allele_count += n_risk
        if n_risk == 2:
            aoc1_homozygous_risk_count += 1
        if s.category == ELEVATED:
            aoc1_cat = ELEVATED
        elif s.category == MODERATE and aoc1_cat != ELEVATED:
            aoc1_cat = MODERATE

    hnmt_result = next(
        (s for s in histamine_pr.snp_results if s.rsid == "rs11558538"),
        None,
    )
    # rs10156191 genotype kept as the representative AOC1 genotype for back-compat.
    aoc1_gt = (
        rs10156191_result.genotype
        if rs10156191_result and rs10156191_result.present_in_sample
        else None
    )
    hnmt_gt = hnmt_result.genotype if hnmt_result and hnmt_result.present_in_sample else None
    hnmt_cat = hnmt_result.category if hnmt_result and hnmt_result.present_in_sample else STANDARD

    aoc1_present = aoc1_risk_allele_count > 0
    load_phrase = (
        f"Cumulative AOC1 load: {aoc1_risk_allele_count} DAO-deficiency risk "
        f"allele(s) across {aoc1_snps_assessed} assessed SNP(s) "
        f"({aoc1_homozygous_risk_count} homozygous); a higher load — especially "
        "the homozygous count — is more predictive of reduced DAO activity than "
        "any single variant."
    )

    # Build combined text
    if aoc1_present and hnmt_cat != STANDARD:
        combined_text = (
            f"Both AOC1 (DAO) and HNMT variants detected. {load_phrase} Combined "
            "reduction in histamine catabolism may amplify histamine intolerance "
            "risk. Evidence is at the candidate gene level."
        )
    elif aoc1_present:
        combined_text = (
            f"AOC1 (DAO) variant(s) detected. {load_phrase} Reduced gut histamine "
            "clearance. Evidence is at the candidate gene level."
        )
    elif hnmt_cat != STANDARD:
        combined_text = (
            "HNMT variant detected. Reduced intracellular histamine inactivation. "
            "Evidence is at the candidate gene level."
        )
    else:
        combined_text = (
            "No panel-tracked AOC1/HNMT risk genotypes detected. This candidate-gene "
            "panel (all four main AOC1 DAO-deficiency variants) does not "
            "rule out reduced DAO activity or histamine intolerance, which depend on "
            "additional variants and non-genetic factors (diet, medications, "
            "clinical phenotype)."
        )

    return HistamineCombinedResult(
        aoc1_genotype=aoc1_gt,
        hnmt_genotype=hnmt_gt,
        aoc1_category=aoc1_cat,
        hnmt_category=hnmt_cat,
        combined_text=combined_text,
        de_emphasize=histamine_config.get("de_emphasize_in_ui", True),
        aoc1_risk_allele_count=aoc1_risk_allele_count,
        aoc1_homozygous_risk_count=aoc1_homozygous_risk_count,
        aoc1_snps_assessed=aoc1_snps_assessed,
    )


# ── Cross-module references ──────────────────────────────────────────────


def _generate_cross_module_findings(
    pathway_results: list[PathwayResult],
    panel: AllergyPanel,
    hla_proxy_info: dict[str, HLAProxyInfo],
) -> list[CrossModuleFinding]:
    """Generate cross-module reference findings.

    Cross-links:
      - HLA-B*57:01 (rs2395029) → Pharmacogenomics (abacavir) — bi-directional
      - HLA-B*15:02 (rs144012689) → Pharmacogenomics (carbamazepine)
      - HLA-A*31:01 (rs1061235) → Pharmacogenomics (carbamazepine)
      - HLA-B*58:01 (rs9263726) → Pharmacogenomics (allopurinol)
      - IL13 (rs20541) → Skin (atopic dermatitis)
      - Celiac DQ2/DQ8 → Nutrigenomics (gluten)
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
            if snp_result.hla_proxy is not None:
                hla_allele = snp_result.hla_proxy.get("hla_allele", "")
                # Include r² from hla_proxy_lookup if available
                proxy_info = hla_proxy_info.get(snp_result.rsid)
                r2_text = ""
                if proxy_info:
                    r2_parts = [
                        f"{pop}: r²={r2:.2f}"
                        for pop, r2 in sorted(proxy_info.r_squared_by_pop.items())
                    ]
                    r2_text = f" ({', '.join(r2_parts)})"

                cross_text = (
                    f"{hla_allele} proxy ({snp_result.rsid}, {snp_result.genotype})"
                    f"{r2_text} — {note}"
                )
            elif snp_result.gene == "IL13":
                cross_text = (
                    f"IL13 {snp_result.variant_name} ({snp_result.genotype}) — "
                    f"Atopic susceptibility variant. {note}"
                )
            else:
                cross_text = (
                    f"{snp_result.gene} {snp_result.variant_name} ({snp_result.genotype}) — {note}"
                )

            # Deduplicate at allele/drug-context granularity, not gene-only:
            # multiple HLA-B alleles share gene "HLA-B" but are distinct
            # drug-safety handoffs that must not collapse into one finding —
            # e.g. HLA-B*15:02 (carbamazepine SJS/TEN) and HLA-B*58:01
            # (allopurinol SCAR). Key on the HLA allele for proxies, else rsid.
            if snp_result.hla_proxy is not None:
                dedup_identity = snp_result.hla_proxy.get("hla_allele") or snp_result.rsid
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


def _find_panel_snp(panel: AllergyPanel, rsid: str) -> PanelSNP | None:
    """Find a PanelSNP by rsid."""
    for pathway in panel.pathways:
        for snp in pathway.snps:
            if snp.rsid == rsid:
                return snp
    return None


# ── Panel coverage tracking ──────────────────────────────────────────────


def _compute_panel_coverage(
    panel: AllergyPanel,
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


def score_allergy_pathways(
    panel: AllergyPanel,
    sample_engine: sa.Engine,
    reference_engine: sa.Engine,
) -> AllergyResult:
    """Score all allergy pathways for a sample.

    1. Fetches raw genotypes from the sample DB for all panel rsids.
    2. Scores each SNP using the curated panel definitions.
    3. Applies evidence-level gating.
    4. Determines per-pathway level (highest category across SNPs).
    5. Fetches HLA proxy lookup data for ancestry-specific r² display.
    6. Computes celiac DQ2/DQ8 combined assessment.
    7. Computes histamine combined assessment.
    8. Generates cross-module reference findings.
    9. Looks up GWAS associations for matched rsids.
    10. Computes panel coverage tracking.

    Args:
        panel: Loaded AllergyPanel.
        sample_engine: SQLAlchemy engine for the sample database.
        reference_engine: SQLAlchemy engine for reference.db.

    Returns:
        AllergyResult with all pathway results, HLA proxy info,
        celiac combined, histamine combined, cross-module findings,
        and GWAS matches.
    """
    # Fetch all panel rsids from sample
    all_rsids = panel.all_rsids()
    genotypes = _fetch_genotypes(all_rsids, sample_engine)
    logger.info(
        "allergy_genotypes_fetched",
        panel_rsids=len(all_rsids),
        found_in_sample=len(genotypes),
    )

    pathway_results: list[PathwayResult] = []

    for pathway in panel.pathways:
        snp_results: list[SNPResult] = []
        for snp in pathway.snps:
            raw_gt = genotypes.get(snp.rsid)
            gt = _normalize_genotype(raw_gt)
            result = _score_snp(snp, gt)
            if raw_gt is None:
                result.coverage_status = "not_on_array"
            elif gt is None:
                result.coverage_status = "no_call"
            else:
                result.coverage_status = "called"
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

    # Fetch HLA proxy lookup data for ancestry-specific r² display
    hla_proxy_info = _fetch_hla_proxy_info(all_rsids, reference_engine)
    inferred_ancestry = get_inferred_ancestry(sample_engine)

    # Celiac DQ2/DQ8 combined assessment
    celiac_combined = _compute_celiac_combined(pathway_results, panel)

    # Histamine combined assessment
    histamine_combined = _compute_histamine_combined(pathway_results, panel)

    # Cross-module reference findings
    cross_module = _generate_cross_module_findings(
        pathway_results,
        panel,
        hla_proxy_info,
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

    return AllergyResult(
        pathway_results=pathway_results,
        gwas_matched_rsids=gwas_matched,
        hla_proxy_info=hla_proxy_info,
        celiac_combined=celiac_combined,
        histamine_combined=histamine_combined,
        cross_module_findings=cross_module,
        panel_coverage_rows=coverage_rows,
        inferred_ancestry=inferred_ancestry,
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


def _hla_proxy_lookup_detail(
    snp: SNPResult,
    hla_proxy_info: dict[str, HLAProxyInfo],
) -> dict | None:
    """Build stored HLA proxy lookup detail for pathway/SNP finding JSON."""
    info = hla_proxy_info.get(snp.rsid)
    if info is None:
        return None

    return {
        "hla_allele": info.hla_allele,
        "r_squared_by_pop": info.r_squared_by_pop,
        "clinical_context": info.clinical_context,
    }


def _negative_hla_proxy_caveat(
    snp: SNPResult,
    hla_proxy_info: dict[str, HLAProxyInfo],
) -> str | None:
    """Return residual-risk caveat for negative HLA proxy calls when needed."""
    if snp.category != STANDARD or snp.hla_proxy is None or snp.rsid not in hla_proxy_info:
        return None

    info = hla_proxy_info[snp.rsid]
    min_r2 = min(info.r_squared_by_pop.values(), default=1.0)
    if snp.rsid != "rs9263726" or min_r2 >= _HLA_PROXY_CLINICAL_GRADE_R2:
        return None

    return (
        f"No {info.hla_allele} tag-SNP allele was detected at {snp.rsid}, "
        "but this proxy result does not exclude the HLA allele. Proxy LD varies "
        "by ancestry; use high-resolution HLA typing for clinical allopurinol "
        "decisions."
    )


def _hla_proxy_reduced_ancestry_applies(
    snp: SNPResult,
    *,
    inferred_ancestry: str | None,
) -> bool:
    """Whether an HLA-proxy reduced-ancestry caveat applies to this sample."""
    if snp.hla_proxy is None:
        return False
    reduced = snp.hla_proxy.get("reduced_ancestry")
    if not reduced or not inferred_ancestry:
        return False

    inferred = inferred_ancestry.strip()
    reduced_name = str(reduced).strip()
    if not inferred or not reduced_name:
        return False
    if inferred.casefold() == reduced_name.casefold():
        return True

    mapped_codes = _HLA_PROXY_REDUCED_ANCESTRY_CODES.get(reduced_name)
    if mapped_codes is None:
        return False
    return inferred.upper() in mapped_codes


def _contextual_hla_proxy_coverage_note(
    snp: SNPResult,
    *,
    inferred_ancestry: str | None,
) -> str | None:
    """Return coverage note with sample-specific reduced-ancestry text gated."""
    note = snp.coverage_note
    if not note or snp.hla_proxy is None:
        return note

    reduced = snp.hla_proxy.get("reduced_ancestry")
    if not reduced or _hla_proxy_reduced_ancestry_applies(
        snp,
        inferred_ancestry=inferred_ancestry,
    ):
        return note

    pattern = (
        rf"\s*Proxy performance is also reduced in {re.escape(str(reduced))} ancestry "
        r"\([^)]*\)\."
    )
    return re.sub(pattern, "", note).strip()


def _positive_hla_proxy_specificity_caveat(
    snp: SNPResult,
    *,
    inferred_ancestry: str | None = None,
) -> str | None:
    """Specificity caveat for a POSITIVE (carrier) HLA-proxy call (#611).

    A tag SNP can mark a positive that is actually a different, cross-reactive HLA
    type — a *false positive* — which is precisely why confirmatory typing is
    required before acting on a positive. Surfaced only for carrier calls whose
    panel ``hla_proxy`` records the cross-reactivity mechanism (e.g. rs1061235
    false-positives via HLA-A*33). Counterpart to ``_negative_hla_proxy_caveat``
    (which addresses residual risk on *negative* calls). Applies only to an actual
    positive carrier call (Elevated/Moderate) — never to a Standard non-carrier or
    a withheld Indeterminate (e.g. rs1061235's palindromic A/T homozygotes).
    """
    if snp.category not in (ELEVATED, MODERATE) or snp.hla_proxy is None:
        return None
    mechanism = snp.hla_proxy.get("false_positive_mechanism")
    if not mechanism:
        return None

    hla_allele = snp.hla_proxy.get("hla_allele", "the HLA allele")
    caveat = (
        f"This positive {hla_allele} result is from an imperfect tag SNP and can be a "
        f"false positive via {mechanism}; confirmatory high-resolution HLA typing is "
        "required before acting on it (e.g. withholding the drug)."
    )
    reduced = snp.hla_proxy.get("reduced_ancestry")
    if reduced and _hla_proxy_reduced_ancestry_applies(
        snp,
        inferred_ancestry=inferred_ancestry,
    ):
        better = snp.hla_proxy.get("better_proxy_for_reduced_ancestry")
        suffix = f" (better alternative: {better})" if better else ""
        caveat += f" Proxy reliability is also reduced in {reduced} ancestry{suffix}."
    return caveat


def _stored_snp_detail(
    snp: SNPResult,
    hla_proxy_info: dict[str, HLAProxyInfo],
    *,
    inferred_ancestry: str | None = None,
) -> dict:
    """Build pathway-level SNP detail JSON, including HLA proxy caveats."""
    detail = {
        "rsid": snp.rsid,
        "gene": snp.gene,
        "variant_name": snp.variant_name,
        "genotype": snp.genotype,
        "category": snp.category,
        "effect_summary": snp.effect_summary,
        "evidence_level": snp.evidence_level,
        "hla_proxy": snp.hla_proxy,
        "coverage_note": _contextual_hla_proxy_coverage_note(
            snp,
            inferred_ancestry=inferred_ancestry,
        ),
    }

    lookup = _hla_proxy_lookup_detail(snp, hla_proxy_info)
    if lookup is not None:
        detail["hla_proxy_lookup"] = lookup

    caveat = _negative_hla_proxy_caveat(snp, hla_proxy_info)
    if caveat is not None:
        detail["hla_proxy_caveat"] = caveat

    specificity_caveat = _positive_hla_proxy_specificity_caveat(
        snp,
        inferred_ancestry=inferred_ancestry,
    )
    if specificity_caveat is not None:
        detail["hla_proxy_specificity_caveat"] = specificity_caveat

    return detail


# ── Findings storage ─────────────────────────────────────────────────────


def store_allergy_findings(
    result: AllergyResult,
    sample_engine: sa.Engine,
) -> int:
    """Store allergy findings in the sample database.

    Creates findings:
      - 4 pathway summaries (one per pathway).
      - Individual SNP findings for non-Standard called SNPs.
      - 1 celiac DQ2/DQ8 combined assessment finding.
      - 1 histamine metabolism combined assessment finding.
      - Cross-module reference findings (PGx, Skin, Nutrigenomics).

    Also stores panel coverage tracking rows.

    Args:
        result: AllergyResult from score_allergy_pathways.
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
            # Preserve missing_snps as the historical union while exposing
            # on-array failed calls separately for actionable UI labels.
            "no_call_snps": [s.rsid for s in pr.missing_snps if s.coverage_status == "no_call"],
            "snp_details": [
                _stored_snp_detail(
                    s,
                    result.hla_proxy_info,
                    inferred_ancestry=result.inferred_ancestry,
                )
                for s in pr.called_snps
            ],
        }

        # Add HLA proxy info for drug hypersensitivity and food sensitivity pathways
        if pr.pathway_id in ("drug_hypersensitivity", "food_sensitivity"):
            hla_details = {}
            for s in pr.called_snps:
                if s.rsid in result.hla_proxy_info:
                    info = result.hla_proxy_info[s.rsid]
                    hla_details[s.rsid] = {
                        "hla_allele": info.hla_allele,
                        "r_squared_by_pop": info.r_squared_by_pop,
                        "clinical_context": info.clinical_context,
                    }
            if hla_details:
                detail["hla_proxy_lookup"] = hla_details

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
            if snp.hla_proxy:
                snp_detail["hla_proxy"] = snp.hla_proxy
                # Include r² from hla_proxy_lookup if available
                lookup = _hla_proxy_lookup_detail(snp, result.hla_proxy_info)
                if lookup is not None:
                    snp_detail["hla_proxy_lookup"] = lookup
            coverage_note = _contextual_hla_proxy_coverage_note(
                snp,
                inferred_ancestry=result.inferred_ancestry,
            )
            if coverage_note:
                snp_detail["coverage_note"] = coverage_note

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

    # Celiac DQ2/DQ8 combined assessment finding
    if result.celiac_combined is not None:
        cc = result.celiac_combined
        celiac_text = f"Celiac Disease Risk Assessment — {cc.label}. {cc.description}"
        rows.append(
            {
                "module": MODULE_NAME,
                "category": "celiac_combined",
                "evidence_level": cc.evidence_level,
                "gene_symbol": None,
                "rsid": None,
                "finding_text": celiac_text,
                "pathway": "Food Sensitivity",
                "pathway_level": None,
                "pmid_citations": json.dumps(["18311140", "20190752"]),
                "detail_json": json.dumps(
                    {
                        "state": cc.state,
                        "label": cc.label,
                        "dq2_genotype": cc.dq2_genotype,
                        "dq8_genotype": cc.dq8_genotype,
                    }
                ),
            }
        )

    # Histamine metabolism combined assessment finding
    if result.histamine_combined is not None:
        hc = result.histamine_combined
        rows.append(
            {
                "module": MODULE_NAME,
                "category": "histamine_combined",
                "evidence_level": 1,  # ★☆ candidate gene level
                "gene_symbol": None,
                "rsid": None,
                "finding_text": f"Histamine Metabolism — {hc.combined_text}",
                "pathway": "Histamine Metabolism",
                "pathway_level": None,
                "pmid_citations": json.dumps(
                    ["15046637", "17490952", "23886886", "21488903", "38674832"]
                ),
                "detail_json": json.dumps(
                    {
                        "aoc1_genotype": hc.aoc1_genotype,
                        "hnmt_genotype": hc.hnmt_genotype,
                        "aoc1_category": hc.aoc1_category,
                        "hnmt_category": hc.hnmt_category,
                        "de_emphasize": hc.de_emphasize,
                        "aoc1_risk_allele_count": hc.aoc1_risk_allele_count,
                        "aoc1_homozygous_risk_count": hc.aoc1_homozygous_risk_count,
                        "aoc1_snps_assessed": hc.aoc1_snps_assessed,
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

    with sample_engine.begin() as conn:
        # Clear previous allergy findings
        conn.execute(sa.delete(findings).where(findings.c.module == MODULE_NAME))
        if not rows:
            logger.info("no_allergy_findings_to_store")
            return 0
        conn.execute(sa.insert(findings), rows)

        # Store panel coverage tracking
        if result.panel_coverage_rows:
            conn.execute(sa.delete(panel_coverage).where(panel_coverage.c.module == MODULE_NAME))
            conn.execute(sa.insert(panel_coverage), result.panel_coverage_rows)

    logger.info("allergy_findings_stored", count=len(rows))
    return len(rows)


# ── Annotation coverage bitmask ─────────────────────────────────────────

_BITMASK_BATCH = 500  # Stay under SQLITE_MAX_VARIABLE_NUMBER


def update_annotation_coverage_gwas(
    result: AllergyResult,
    sample_engine: sa.Engine,
) -> int:
    """OR bit 5 (GWAS Catalog, value 32) into annotation_coverage for GWAS-matched variants.

    Args:
        result: AllergyResult from :func:`score_allergy_pathways`.
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
        "allergy_gwas_annotation_coverage_updated",
        gwas_bit=GWAS_BIT,
        gwas_matched_rsids=len(rsid_list),
        rows_updated=updated,
    )
    return updated

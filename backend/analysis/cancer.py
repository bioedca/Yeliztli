"""Cancer predisposition gene panel definition, loader, and analysis module.

Implements P3-12 (panel) and P3-13 (ClinVar P/LP extraction):
  - P3-12: Curated cancer gene panel with expected ClinVar entries.
  - P3-13: Extract ClinVar Pathogenic/Likely pathogenic variants in the
    cancer gene panel and generate findings with accession, review stars,
    syndrome, and inheritance pattern.

The panel covers 28 genes (22 gene groups per PRD) associated with
hereditary cancer syndromes:

    BRCA1, BRCA2, TP53, PALB2, ATM, CHEK2, RAD51C, RAD51D,
    MLH1, MSH2, MSH6, PMS2, APC, MUTYH, VHL, RET, PTEN, STK11,
    CDH1, NF1, NF2, MEN1, SDHA, SDHB, SDHC, SDHD, BAP1, CDKN2A

Each gene entry includes associated syndromes, cancer types, inheritance
pattern, evidence level, expected ClinVar P/LP rsids, and PubMed citations.

BRCA1/2 have cross-links to the carrier status module — variants in these
genes produce findings in both the cancer and carrier modules with distinct
framing.

Usage::

    from backend.analysis.cancer import (
        load_cancer_panel,
        extract_cancer_variants,
        store_cancer_findings,
        CancerPanel,
        CancerGene,
        CancerVariantResult,
        CancerAnalysisResult,
    )

    panel = load_cancer_panel()
    result = extract_cancer_variants(panel, sample_engine)
    store_cancer_findings(result, sample_engine)
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import sqlalchemy as sa
import structlog

from backend.analysis.clinvar_significance import (
    LOWER_PENETRANCE_RISK_ALLELE_CATEGORY,
    LOWER_PENETRANCE_RISK_ALLELE_PMIDS,
    is_low_penetrance_or_risk_allele,
    low_penetrance_or_risk_allele_filter,
    pathogenic_significance_filter,
)
from backend.analysis.evidence import assign_clinvar_evidence_level
from backend.analysis.gene_constraint import lookup_gene_constraints
from backend.analysis.inheritance import (
    DISEASE_CARRIER,
    DISEASE_POSSIBLE_BIALLELIC,
    classify_disease_status,
)
from backend.analysis.insilico_tiers import insilico_block
from backend.analysis.zygosity import CARRIED_ZYGOSITIES
from backend.db.tables import annotated_variants, findings

logger = structlog.get_logger(__name__)

# Path to the curated panel JSON (relative to this file)
_PANEL_PATH = Path(__file__).resolve().parent.parent / "data" / "panels" / "cancer_panel.json"


# ── Data classes ──────────────────────────────────────────────────────────


@dataclass
class CancerGene:
    """A single gene entry from the curated cancer panel."""

    gene_symbol: str
    name: str
    chromosome: str
    syndromes: list[str]
    cancer_types: list[str]
    inheritance: str  # AD or AR
    evidence_level: int  # 1-4 stars
    cross_links: list[str]  # module names (e.g. "carrier")
    expected_clinvar_rsids: list[str]
    pmids: list[str]
    notes: str

    @property
    def is_dual_role(self) -> bool:
        """Whether this gene produces findings in multiple modules."""
        return len(self.cross_links) > 0


@dataclass
class CancerPanel:
    """The complete curated cancer predisposition gene panel."""

    module: str
    version: str
    description: str
    genes: list[CancerGene]

    def all_gene_symbols(self) -> list[str]:
        """Return all gene symbols in the panel."""
        return [g.gene_symbol for g in self.genes]

    def all_expected_rsids(self) -> list[str]:
        """Return all expected ClinVar rsids across all genes."""
        return [rsid for gene in self.genes for rsid in gene.expected_clinvar_rsids]

    def get_gene(self, gene_symbol: str) -> CancerGene | None:
        """Look up a gene by symbol (case-insensitive)."""
        symbol_upper = gene_symbol.upper()
        for gene in self.genes:
            if gene.gene_symbol.upper() == symbol_upper:
                return gene
        return None

    def dual_role_genes(self) -> list[CancerGene]:
        """Return genes that have cross-links to other modules."""
        return [g for g in self.genes if g.is_dual_role]

    def genes_by_syndrome(self, syndrome: str) -> list[CancerGene]:
        """Return all genes associated with a given syndrome (substring match)."""
        syndrome_lower = syndrome.lower()
        return [g for g in self.genes if any(syndrome_lower in s.lower() for s in g.syndromes)]

    def genes_by_cancer_type(self, cancer_type: str) -> list[CancerGene]:
        """Return all genes associated with a given cancer type (substring match)."""
        cancer_lower = cancer_type.lower()
        return [g for g in self.genes if any(cancer_lower in ct.lower() for ct in g.cancer_types)]


# ── Panel loading ─────────────────────────────────────────────────────────


def load_cancer_panel(panel_path: Path | None = None) -> CancerPanel:
    """Load the curated cancer gene panel from JSON.

    Args:
        panel_path: Optional override for the panel JSON path.
            Defaults to ``backend/data/panels/cancer_panel.json``.

    Returns:
        Parsed CancerPanel with all genes.

    Raises:
        FileNotFoundError: If the panel JSON does not exist.
        json.JSONDecodeError: If the panel JSON is malformed.
    """
    path = panel_path or _PANEL_PATH
    logger.info("loading_cancer_panel", path=str(path))

    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    genes: list[CancerGene] = []
    for idx, gene_data in enumerate(data["genes"]):
        try:
            genes.append(
                CancerGene(
                    gene_symbol=gene_data["gene_symbol"],
                    name=gene_data["name"],
                    chromosome=gene_data["chromosome"],
                    syndromes=gene_data["syndromes"],
                    cancer_types=gene_data["cancer_types"],
                    inheritance=gene_data["inheritance"],
                    evidence_level=gene_data["evidence_level"],
                    cross_links=gene_data.get("cross_links", []),
                    expected_clinvar_rsids=gene_data.get("expected_clinvar_rsids", []),
                    pmids=gene_data.get("pmids", []),
                    notes=gene_data.get("notes", ""),
                )
            )
        except KeyError as e:
            symbol = gene_data.get("gene_symbol", f"index {idx}")
            raise ValueError(f"Missing required field {e} for gene {symbol}") from e

    panel = CancerPanel(
        module=data["module"],
        version=data["version"],
        description=data["description"],
        genes=genes,
    )

    logger.info(
        "cancer_panel_loaded",
        gene_count=len(panel.genes),
        total_expected_rsids=len(panel.all_expected_rsids()),
        dual_role_genes=[g.gene_symbol for g in panel.dual_role_genes()],
    )

    return panel


# ── P3-13: Cancer predisposition analysis ─────────────────────────────────

# Policy guard for issue #837: only PMS2 exons currently modeled as
# PMS2/PMS2CL-confounded are withheld from consumer cancer findings.
_PMS2_PSEUDOGENE_CONFOUNDED_EXONS = frozenset({12, 13, 14, 15})
_DOMINANT_HOM_ALT_EXPECTED_FREQ_MAX = 1e-4


@dataclass
class CancerVariantResult:
    """A single ClinVar P/LP variant found in the cancer gene panel."""

    rsid: str
    gene_symbol: str
    genotype: str
    zygosity: str | None
    clinvar_significance: str
    clinvar_review_stars: int
    clinvar_accession: str | None
    clinvar_conditions: str | None
    syndromes: list[str]
    cancer_types: list[str]
    inheritance: str
    evidence_level: int
    cross_links: list[str]
    pmids: list[str]
    revel: float | None = None
    consequence: str | None = None
    clinvar_low_penetrance_or_risk_allele: bool = False


@dataclass
class CancerAnalysisResult:
    """Complete cancer predisposition analysis result for a sample."""

    variants: list[CancerVariantResult] = field(default_factory=list)
    panel_genes_checked: int = 0
    variants_in_panel_genes: int = 0
    pseudogene_suppressed: int = 0
    hom_alt_plausibility_suppressed: int = 0

    @property
    def pathogenic_count(self) -> int:
        """Number of P/LP variants found."""
        return sum(1 for v in self.variants if not v.clinvar_low_penetrance_or_risk_allele)

    @property
    def dual_role_variants(self) -> list[CancerVariantResult]:
        """Variants in genes with cross-links (e.g. BRCA1/2)."""
        return [v for v in self.variants if v.cross_links]


def _assign_evidence_level(
    clinvar_significance: str,
    clinvar_review_stars: int,
    gene_evidence_level: int,
) -> int:
    """Assign evidence level (1-4 stars) based on ClinVar data.

    Delegates to the centralized evidence framework (P3-40).
    """
    return assign_clinvar_evidence_level(
        clinvar_significance,
        clinvar_review_stars,
        gene_baseline=gene_evidence_level,
    )


def extract_cancer_variants(
    panel: CancerPanel,
    sample_engine: sa.Engine,
) -> CancerAnalysisResult:
    """Extract ClinVar P/LP variants in the cancer gene panel from annotated variants.

    Queries the annotated_variants table for variants where:
      1. gene_symbol is in the cancer panel genes
      2. clinvar_significance is Pathogenic or Likely pathogenic
      3. the sample's genotype actually carries the ALT allele
         (zygosity het or hom_alt)

    Criterion 3 is essential: a 23andMe chip reports a genotype at every probe
    regardless of carriage, so without it every chip position overlapping a
    ClinVar P/LP record would be (wrongly) surfaced as a finding even when the
    individual is homozygous reference. Carriage is computed at annotation time
    (``backend.annotation.clinvar``) via the shared ``classify_zygosity`` helper;
    rows that are homozygous reference or unscoreable (indel/no-call/strand) have
    ``zygosity`` outside ``CARRIED_ZYGOSITIES`` and are excluded here.

    For each matching variant, enriches with panel metadata (syndromes,
    cancer types, inheritance, cross-links, PMIDs).

    Args:
        panel: Loaded CancerPanel.
        sample_engine: SQLAlchemy engine for the sample database.

    Returns:
        CancerAnalysisResult with all P/LP variants found.
    """
    gene_symbols = panel.all_gene_symbols()
    # Build gene lookup for enrichment
    gene_map = {g.gene_symbol.upper(): g for g in panel.genes}

    # Query annotated variants in panel genes with ClinVar P/LP
    with sample_engine.connect() as conn:
        # Count total variants in panel genes (for stats)
        count_stmt = (
            sa.select(sa.func.count())
            .select_from(annotated_variants)
            .where(annotated_variants.c.gene_symbol.in_(gene_symbols))
        )
        total_in_panel = conn.execute(count_stmt).scalar() or 0

        # Fetch P/LP variants
        stmt = (
            sa.select(
                annotated_variants.c.rsid,
                annotated_variants.c.gene_symbol,
                annotated_variants.c.genotype,
                annotated_variants.c.zygosity,
                annotated_variants.c.clinvar_significance,
                annotated_variants.c.clinvar_review_stars,
                annotated_variants.c.clinvar_accession,
                annotated_variants.c.clinvar_conditions,
                annotated_variants.c.exon_number,
                annotated_variants.c.revel,
                annotated_variants.c.consequence,
                annotated_variants.c.gnomad_af_popmax,
                annotated_variants.c.gnomad_af_global,
                annotated_variants.c.gnomad_homozygous_count,
            )
            .where(
                annotated_variants.c.gene_symbol.in_(gene_symbols),
                sa.or_(
                    pathogenic_significance_filter(annotated_variants.c.clinvar_significance),
                    low_penetrance_or_risk_allele_filter(
                        annotated_variants.c.clinvar_significance
                    ),
                ),
                # Only surface variants the individual actually carries.
                annotated_variants.c.zygosity.in_(list(CARRIED_ZYGOSITIES)),
            )
            .order_by(annotated_variants.c.gene_symbol, annotated_variants.c.rsid)
        )
        rows = conn.execute(stmt).fetchall()

    variants: list[CancerVariantResult] = []
    pseudogene_suppressed = 0
    hom_alt_plausibility_suppressed = 0
    for row in rows:
        gene_symbol = (row.gene_symbol or "").upper()
        gene_info = gene_map.get(gene_symbol)
        if gene_info is None:
            continue
        if _is_pms2_pseudogene_confounded(row):
            pseudogene_suppressed += 1
            continue
        if _is_implausible_dominant_hom_alt(row, gene_info):
            hom_alt_plausibility_suppressed += 1
            continue

        evidence = _assign_evidence_level(
            row.clinvar_significance or "",
            row.clinvar_review_stars or 0,
            gene_info.evidence_level,
        )
        lower_penetrance = is_low_penetrance_or_risk_allele(row.clinvar_significance)

        variants.append(
            CancerVariantResult(
                rsid=row.rsid,
                gene_symbol=row.gene_symbol,
                genotype=row.genotype or "",
                zygosity=row.zygosity,
                clinvar_significance=row.clinvar_significance,
                clinvar_review_stars=row.clinvar_review_stars or 0,
                clinvar_accession=row.clinvar_accession,
                clinvar_conditions=row.clinvar_conditions,
                syndromes=gene_info.syndromes,
                cancer_types=gene_info.cancer_types,
                inheritance=gene_info.inheritance,
                evidence_level=evidence,
                cross_links=gene_info.cross_links,
                pmids=gene_info.pmids,
                revel=row.revel,
                consequence=row.consequence,
                clinvar_low_penetrance_or_risk_allele=lower_penetrance,
            )
        )

    logger.info(
        "cancer_variants_extracted",
        panel_genes=len(gene_symbols),
        variants_in_panel_genes=total_in_panel,
        pathogenic_variants=len(variants),
        pseudogene_suppressed=pseudogene_suppressed,
        hom_alt_plausibility_suppressed=hom_alt_plausibility_suppressed,
        dual_role_variants=len([v for v in variants if v.cross_links]),
    )

    return CancerAnalysisResult(
        variants=variants,
        panel_genes_checked=len(gene_symbols),
        variants_in_panel_genes=total_in_panel,
        pseudogene_suppressed=pseudogene_suppressed,
        hom_alt_plausibility_suppressed=hom_alt_plausibility_suppressed,
    )


def _is_pms2_pseudogene_confounded(row: sa.Row) -> bool:
    """Return true for PMS2 calls in exons that need PMS2/PMS2CL disambiguation."""
    gene_symbol = (row.gene_symbol or "").upper()
    return gene_symbol == "PMS2" and row.exon_number in _PMS2_PSEUDOGENE_CONFOUNDED_EXONS


def _is_implausible_dominant_hom_alt(row: sa.Row, gene_info: CancerGene) -> bool:
    """Return true for rare dominant hom-alt calls without population support."""
    if row.zygosity != "hom_alt" or gene_info.inheritance.upper() != "AD":
        return False
    if row.gnomad_homozygous_count is not None and row.gnomad_homozygous_count > 0:
        return False

    af = _effective_gnomad_af(row)
    if af is None:
        return False
    return af * af <= _DOMINANT_HOM_ALT_EXPECTED_FREQ_MAX


def _effective_gnomad_af(row: sa.Row) -> float | None:
    """Return usable popmax AF, falling back to global AF."""
    for af in (row.gnomad_af_popmax, row.gnomad_af_global):
        if af is not None and 0 <= af <= 1:
            return af
    return None


# ── Findings storage ─────────────────────────────────────────────────────


def _cancer_finding_text(variant: CancerVariantResult, status: str) -> str:
    """Render the user-facing finding text, gated by AR disease status (issue #86)."""
    syndrome_text = ", ".join(variant.syndromes) if variant.syndromes else "Cancer predisposition"
    sig = variant.clinvar_significance
    head = f"{variant.gene_symbol} {variant.rsid} ({variant.genotype})"
    if variant.clinvar_low_penetrance_or_risk_allele:
        return (
            f"{head} — {sig} for {syndrome_text}. ClinVar marks this as "
            "lower-penetrance/risk-allele, so it is reported separately from "
            "high-penetrance P/LP cancer predisposition variants."
        )
    if status == DISEASE_CARRIER:
        return (
            f"{head} — {sig}, heterozygous carrier. {syndrome_text} is autosomal "
            f"recessive and requires biallelic (two-copy) variants, so a single "
            f"pathogenic allele is a carrier state — not an affected diagnosis. "
            f"Monoallelic carriers have at most a modest, still-debated increase in "
            f"colorectal cancer risk; array data may not exclude a second untyped "
            f"allele, so clinical/genetic confirmation is needed if indicated."
        )
    if status == DISEASE_POSSIBLE_BIALLELIC:
        return (
            f"{head} — {sig}, heterozygous (one of multiple {variant.gene_symbol} "
            f"pathogenic alleles). {syndrome_text} is autosomal recessive; genotype "
            f"data cannot phase these alleles, so biallelic (compound-heterozygous) "
            f"status is possible but unconfirmed and requires clinical confirmation."
        )
    return f"{head} — {sig} for {syndrome_text}"


def store_cancer_findings(
    result: CancerAnalysisResult,
    sample_engine: sa.Engine,
    reference_engine: sa.Engine | None = None,
) -> int:
    """Store cancer predisposition findings in the sample database.

    Creates one finding per P/LP variant with module='cancer' and
    category='monogenic_variant'. Each finding includes ClinVar accession,
    review stars, syndrome, inheritance, and cross-link metadata.

    Args:
        result: CancerAnalysisResult from extract_cancer_variants.
        sample_engine: SQLAlchemy engine for the sample database.
        reference_engine: Optional reference.db engine. When given, each finding
            gains a ``detail_json['gene_constraint']`` context block (gnomAD
            LOEUF/pLI). Omitted entirely when ``None`` (back-compatible). The
            badge is context only and never alters evidence_level/classification.

    Returns:
        Number of findings inserted.
    """
    rows: list[dict] = []

    constraints: dict = {}
    if reference_engine is not None:
        constraints = lookup_gene_constraints(
            reference_engine, [v.gene_symbol for v in result.variants]
        )

    high_penetrance_variants = [
        v for v in result.variants if not v.clinvar_low_penetrance_or_risk_allele
    ]
    for v in result.variants:
        # Build human-readable finding text, gating autosomal-recessive conditions
        # (MUTYH-Associated Polyposis) so a single heterozygous P/LP allele is framed
        # as a carrier rather than an affected diagnosis (issue #86, mirroring #36).
        disease_status_scope = (
            high_penetrance_variants if not v.clinvar_low_penetrance_or_risk_allele else [v]
        )
        disease_status = classify_disease_status(v, disease_status_scope)
        finding_text = _cancer_finding_text(v, disease_status)

        detail = {
            "genotype": v.genotype,
            "clinvar_accession": v.clinvar_accession,
            "clinvar_review_stars": v.clinvar_review_stars,
            "clinvar_conditions": v.clinvar_conditions,
            "syndromes": v.syndromes,
            "cancer_types": v.cancer_types,
            "inheritance": v.inheritance,
            "disease_status": disease_status,
            "cross_links": v.cross_links,
            "clinvar_low_penetrance_or_risk_allele": (v.clinvar_low_penetrance_or_risk_allele),
            # Additive, DRAFT in-silico evidence tag (Pejaver 2022, REVEL-only).
            # Never mutates evidence_level / clinvar_significance below.
            "insilico": insilico_block(v.revel, v.consequence),
        }
        # Optional gnomAD gene-constraint context (only when reference_engine given).
        if reference_engine is not None:
            detail["gene_constraint"] = constraints.get(v.gene_symbol)

        rows.append(
            {
                "module": "cancer",
                "category": (
                    LOWER_PENETRANCE_RISK_ALLELE_CATEGORY
                    if v.clinvar_low_penetrance_or_risk_allele
                    else "monogenic_variant"
                ),
                "evidence_level": v.evidence_level,
                "gene_symbol": v.gene_symbol,
                "rsid": v.rsid,
                "finding_text": finding_text,
                "conditions": v.clinvar_conditions,
                "zygosity": v.zygosity,
                "clinvar_significance": v.clinvar_significance,
                "pmid_citations": json.dumps(
                    [
                        *v.pmids,
                        *(
                            p
                            for p in LOWER_PENETRANCE_RISK_ALLELE_PMIDS
                            if v.clinvar_low_penetrance_or_risk_allele and p not in v.pmids
                        ),
                    ]
                ),
                "detail_json": json.dumps(detail),
            }
        )

    with sample_engine.begin() as conn:
        # Always clear previous cancer findings first, so a rerun or replaced
        # sample with no current reportable P/LP variants does not leave a stale
        # hereditary-cancer call in place (issue #252). Only insert when the
        # current run actually produced findings.
        conn.execute(sa.delete(findings).where(findings.c.module == "cancer"))
        if not rows:
            logger.info("no_cancer_findings_to_store")
            return 0

        conn.execute(sa.insert(findings), rows)

    logger.info("cancer_findings_stored", count=len(rows))
    return len(rows)

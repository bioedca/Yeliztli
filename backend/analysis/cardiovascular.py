"""Cardiovascular gene panel definition, loader, and analysis module.

Implements P3-19 (cardiovascular module annotation) and P3-20 (FH variant
status reporting):
  - Curated cardiovascular gene panel covering familial hypercholesterolemia
    (LDLR, PCSK9, APOB), lipid metabolism (LPA, ABCG5/8),
    channelopathies (KCNQ1, SCN5A, KCNH2, RYR2), and
    cardiomyopathies (MYBPC3, MYH7, TNNT2, LMNA, DSP, PKP2).
  - Extract ClinVar Pathogenic/Likely pathogenic variants in the
    cardiovascular gene panel and generate findings.
  - Determine FH status based on P/LP variants in LDLR, PCSK9, APOB
    and store a summary finding.

The panel covers 16 genes across 4 cardiovascular categories:
  - Familial hypercholesterolemia: LDLR, PCSK9, APOB
  - Lipid metabolism: LPA, ABCG5, ABCG8
  - Channelopathies: KCNQ1, SCN5A, KCNH2, RYR2
  - Cardiomyopathies: MYBPC3, MYH7, TNNT2, LMNA, DSP, PKP2

Usage::

    from backend.analysis.cardiovascular import (
        load_cardiovascular_panel,
        extract_cardiovascular_variants,
        store_cardiovascular_findings,
        determine_fh_status,
        store_fh_status_finding,
        CardiovascularPanel,
        CardiovascularGene,
        CardiovascularVariantResult,
        CardiovascularAnalysisResult,
        FHStatus,
    )

    panel = load_cardiovascular_panel()
    result = extract_cardiovascular_variants(panel, sample_engine)
    store_cardiovascular_findings(result, sample_engine)
    fh = determine_fh_status(result)
    store_fh_status_finding(fh, sample_engine)
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import sqlalchemy as sa
import structlog

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
_PANEL_PATH = (
    Path(__file__).resolve().parent.parent / "data" / "panels" / "cardiovascular_panel.json"
)

# Cardiovascular categories for grouping findings
CATEGORY_FH = "familial_hypercholesterolemia"
CATEGORY_LIPID = "lipid_metabolism"
CATEGORY_CHANNELOPATHY = "channelopathy"
CATEGORY_CARDIOMYOPATHY = "cardiomyopathy"

VALID_CATEGORIES = {CATEGORY_FH, CATEGORY_LIPID, CATEGORY_CHANNELOPATHY, CATEGORY_CARDIOMYOPATHY}


# ── Data classes ──────────────────────────────────────────────────────────


@dataclass
class CardiovascularGene:
    """A single gene entry from the curated cardiovascular panel."""

    gene_symbol: str
    name: str
    chromosome: str
    conditions: list[str]
    cardiovascular_category: str  # familial_hypercholesterolemia, lipid_metabolism, etc.
    inheritance: str  # AD or AR
    evidence_level: int  # 1-4 stars
    cross_links: list[str]
    expected_clinvar_rsids: list[str]
    pmids: list[str]
    notes: str


@dataclass
class CardiovascularPanel:
    """The complete curated cardiovascular gene panel."""

    module: str
    version: str
    description: str
    genes: list[CardiovascularGene]

    def all_gene_symbols(self) -> list[str]:
        """Return all gene symbols in the panel."""
        return [g.gene_symbol for g in self.genes]

    def all_expected_rsids(self) -> list[str]:
        """Return all expected ClinVar rsids across all genes."""
        return [rsid for gene in self.genes for rsid in gene.expected_clinvar_rsids]

    def get_gene(self, gene_symbol: str) -> CardiovascularGene | None:
        """Look up a gene by symbol (case-insensitive)."""
        symbol_upper = gene_symbol.upper()
        for gene in self.genes:
            if gene.gene_symbol.upper() == symbol_upper:
                return gene
        return None

    def genes_by_category(self, category: str) -> list[CardiovascularGene]:
        """Return all genes in a given cardiovascular category."""
        return [g for g in self.genes if g.cardiovascular_category == category]

    def genes_by_condition(self, condition: str) -> list[CardiovascularGene]:
        """Return all genes associated with a given condition (substring match)."""
        condition_lower = condition.lower()
        return [g for g in self.genes if any(condition_lower in c.lower() for c in g.conditions)]

    def fh_genes(self) -> list[CardiovascularGene]:
        """Return genes associated with familial hypercholesterolemia."""
        return self.genes_by_category(CATEGORY_FH)


# ── Panel loading ─────────────────────────────────────────────────────────


def load_cardiovascular_panel(panel_path: Path | None = None) -> CardiovascularPanel:
    """Load the curated cardiovascular gene panel from JSON.

    Args:
        panel_path: Optional override for the panel JSON path.
            Defaults to ``backend/data/panels/cardiovascular_panel.json``.

    Returns:
        Parsed CardiovascularPanel with all genes.

    Raises:
        FileNotFoundError: If the panel JSON does not exist.
        json.JSONDecodeError: If the panel JSON is malformed.
    """
    path = panel_path or _PANEL_PATH
    logger.info("loading_cardiovascular_panel", path=str(path))

    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    genes: list[CardiovascularGene] = []
    for idx, gene_data in enumerate(data["genes"]):
        try:
            genes.append(
                CardiovascularGene(
                    gene_symbol=gene_data["gene_symbol"],
                    name=gene_data["name"],
                    chromosome=gene_data["chromosome"],
                    conditions=gene_data["conditions"],
                    cardiovascular_category=gene_data["cardiovascular_category"],
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

    panel = CardiovascularPanel(
        module=data["module"],
        version=data["version"],
        description=data["description"],
        genes=genes,
    )

    logger.info(
        "cardiovascular_panel_loaded",
        gene_count=len(panel.genes),
        total_expected_rsids=len(panel.all_expected_rsids()),
        fh_genes=[g.gene_symbol for g in panel.fh_genes()],
    )

    return panel


# ── P3-19: Cardiovascular module annotation ───────────────────────────────

# ClinVar significance values considered pathogenic
_PATHOGENIC_SIGNIFICANCE = {"Pathogenic", "Likely pathogenic", "Pathogenic/Likely pathogenic"}


@dataclass
class CardiovascularVariantResult:
    """A single ClinVar P/LP variant found in the cardiovascular gene panel."""

    rsid: str
    gene_symbol: str
    genotype: str
    zygosity: str | None
    clinvar_significance: str
    clinvar_review_stars: int
    clinvar_accession: str | None
    clinvar_conditions: str | None
    conditions: list[str]
    cardiovascular_category: str
    inheritance: str
    evidence_level: int
    cross_links: list[str]
    pmids: list[str]
    revel: float | None = None
    consequence: str | None = None


@dataclass
class CardiovascularAnalysisResult:
    """Complete cardiovascular analysis result for a sample."""

    variants: list[CardiovascularVariantResult] = field(default_factory=list)
    panel_genes_checked: int = 0
    variants_in_panel_genes: int = 0

    @property
    def pathogenic_count(self) -> int:
        """Number of P/LP variants found."""
        return len(self.variants)

    @property
    def fh_variants(self) -> list[CardiovascularVariantResult]:
        """Variants in FH genes (LDLR, PCSK9, APOB)."""
        return [v for v in self.variants if v.cardiovascular_category == CATEGORY_FH]

    @property
    def cardiomyopathy_variants(self) -> list[CardiovascularVariantResult]:
        """Variants in cardiomyopathy genes."""
        return [v for v in self.variants if v.cardiovascular_category == CATEGORY_CARDIOMYOPATHY]

    @property
    def channelopathy_variants(self) -> list[CardiovascularVariantResult]:
        """Variants in channelopathy genes."""
        return [v for v in self.variants if v.cardiovascular_category == CATEGORY_CHANNELOPATHY]

    @property
    def lipid_variants(self) -> list[CardiovascularVariantResult]:
        """Variants in lipid metabolism genes."""
        return [v for v in self.variants if v.cardiovascular_category == CATEGORY_LIPID]


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


def extract_cardiovascular_variants(
    panel: CardiovascularPanel,
    sample_engine: sa.Engine,
) -> CardiovascularAnalysisResult:
    """Extract ClinVar P/LP variants in the cardiovascular gene panel.

    Queries the annotated_variants table for variants where:
      1. gene_symbol is in the cardiovascular panel genes
      2. clinvar_significance is Pathogenic or Likely pathogenic
      3. the sample's genotype actually carries the ALT allele
         (zygosity het or hom_alt)

    Criterion 3 is essential: a 23andMe chip reports a genotype at every probe
    regardless of carriage, so without it every chip position overlapping a
    ClinVar P/LP record would be (wrongly) surfaced — including a spurious FH
    "Positive" status from homozygous-reference LDLR/APOB/PCSK9 probes. Carriage
    is computed at annotation time via the shared ``classify_zygosity`` helper;
    homozygous-reference or unscoreable rows are excluded here.

    For each matching variant, enriches with panel metadata (conditions,
    cardiovascular category, inheritance, cross-links, PMIDs).

    Args:
        panel: Loaded CardiovascularPanel.
        sample_engine: SQLAlchemy engine for the sample database.

    Returns:
        CardiovascularAnalysisResult with all P/LP variants found.
    """
    gene_symbols = panel.all_gene_symbols()
    gene_map = {g.gene_symbol.upper(): g for g in panel.genes}

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
                annotated_variants.c.revel,
                annotated_variants.c.consequence,
            )
            .where(
                annotated_variants.c.gene_symbol.in_(gene_symbols),
                annotated_variants.c.clinvar_significance.in_(list(_PATHOGENIC_SIGNIFICANCE)),
                # Only surface variants the individual actually carries.
                annotated_variants.c.zygosity.in_(list(CARRIED_ZYGOSITIES)),
            )
            .order_by(annotated_variants.c.gene_symbol, annotated_variants.c.rsid)
        )
        rows = conn.execute(stmt).fetchall()

    variants: list[CardiovascularVariantResult] = []
    for row in rows:
        gene_info = gene_map.get((row.gene_symbol or "").upper())
        if gene_info is None:
            continue

        evidence = _assign_evidence_level(
            row.clinvar_significance or "",
            row.clinvar_review_stars or 0,
            gene_info.evidence_level,
        )

        variants.append(
            CardiovascularVariantResult(
                rsid=row.rsid,
                gene_symbol=row.gene_symbol,
                genotype=row.genotype or "",
                zygosity=row.zygosity,
                clinvar_significance=row.clinvar_significance,
                clinvar_review_stars=row.clinvar_review_stars or 0,
                clinvar_accession=row.clinvar_accession,
                clinvar_conditions=row.clinvar_conditions,
                conditions=gene_info.conditions,
                cardiovascular_category=gene_info.cardiovascular_category,
                inheritance=gene_info.inheritance,
                evidence_level=evidence,
                cross_links=gene_info.cross_links,
                pmids=gene_info.pmids,
                revel=row.revel,
                consequence=row.consequence,
            )
        )

    logger.info(
        "cardiovascular_variants_extracted",
        panel_genes=len(gene_symbols),
        variants_in_panel_genes=total_in_panel,
        pathogenic_variants=len(variants),
        fh_variants=len([v for v in variants if v.cardiovascular_category == CATEGORY_FH]),
        cardiomyopathy_variants=len(
            [v for v in variants if v.cardiovascular_category == CATEGORY_CARDIOMYOPATHY]
        ),
        channelopathy_variants=len(
            [v for v in variants if v.cardiovascular_category == CATEGORY_CHANNELOPATHY]
        ),
    )

    return CardiovascularAnalysisResult(
        variants=variants,
        panel_genes_checked=len(gene_symbols),
        variants_in_panel_genes=total_in_panel,
    )


# ── Findings storage ─────────────────────────────────────────────────────


def _cardiovascular_finding_text(variant: CardiovascularVariantResult, status: str) -> str:
    """Render the user-facing finding text, gated by AR disease status (issue #36)."""
    condition_text = (
        ", ".join(variant.conditions) if variant.conditions else "Cardiovascular condition"
    )
    sig = variant.clinvar_significance
    head = f"{variant.gene_symbol} {variant.rsid} ({variant.genotype})"
    if status == DISEASE_CARRIER:
        return (
            f"{head} — {sig}, heterozygous carrier. {condition_text} is autosomal "
            f"recessive and requires biallelic (two-copy) variants, so a single "
            f"pathogenic allele is a carrier state — not an affected diagnosis. "
            f"Array data may not exclude a second untyped allele; biochemical "
            f"(plant-sterol) or clinical confirmation is needed if indicated."
        )
    if status == DISEASE_POSSIBLE_BIALLELIC:
        return (
            f"{head} — {sig}, heterozygous (one of multiple {variant.gene_symbol} "
            f"pathogenic alleles). {condition_text} is autosomal recessive; genotype "
            f"data cannot phase these alleles, so biallelic (compound-heterozygous) "
            f"status is possible but unconfirmed and requires clinical/biochemical "
            f"confirmation."
        )
    return f"{head} — {sig} for {condition_text}"


def store_cardiovascular_findings(
    result: CardiovascularAnalysisResult,
    sample_engine: sa.Engine,
    reference_engine: sa.Engine | None = None,
) -> int:
    """Store cardiovascular findings in the sample database.

    Creates one finding per P/LP variant with module='cardiovascular' and
    category='monogenic_variant'. Each finding includes ClinVar accession,
    review stars, cardiovascular category, inheritance, and condition metadata.

    Args:
        result: CardiovascularAnalysisResult from extract_cardiovascular_variants.
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

    for v in result.variants:
        # Build human-readable finding text, gating autosomal-recessive conditions
        # (e.g. ABCG5/ABCG8 sitosterolemia) so a single heterozygous P/LP allele is
        # framed as a carrier rather than an affected diagnosis (issue #36).
        disease_status = classify_disease_status(v, result.variants)
        finding_text = _cardiovascular_finding_text(v, disease_status)

        detail = {
            # The participant's own allele call — clinically meaningful for a
            # monogenic P/LP finding (het carrier vs hom/affected). The route reads
            # it back via detail.get("genotype"); omitting it dropped the genotype +
            # zygosity from the variant cards (#802; mirrors cancer.py).
            "genotype": v.genotype,
            "clinvar_accession": v.clinvar_accession,
            "clinvar_review_stars": v.clinvar_review_stars,
            "clinvar_conditions": v.clinvar_conditions,
            "conditions": v.conditions,
            "cardiovascular_category": v.cardiovascular_category,
            "inheritance": v.inheritance,
            "disease_status": disease_status,
            "cross_links": v.cross_links,
            # Additive, DRAFT in-silico evidence tag (Pejaver 2022, REVEL-only).
            # Never mutates evidence_level / clinvar_significance below.
            "insilico": insilico_block(v.revel, v.consequence),
        }
        # Optional gnomAD gene-constraint context (only when reference_engine given).
        if reference_engine is not None:
            detail["gene_constraint"] = constraints.get(v.gene_symbol)

        rows.append(
            {
                "module": "cardiovascular",
                "category": "monogenic_variant",
                "evidence_level": v.evidence_level,
                "gene_symbol": v.gene_symbol,
                "rsid": v.rsid,
                "finding_text": finding_text,
                "conditions": v.clinvar_conditions,
                "zygosity": v.zygosity,
                "clinvar_significance": v.clinvar_significance,
                "pmid_citations": json.dumps(v.pmids),
                "detail_json": json.dumps(detail),
            }
        )

    with sample_engine.begin() as conn:
        # Clear previous cardiovascular monogenic findings only
        conn.execute(
            sa.delete(findings).where(
                findings.c.module == "cardiovascular",
                findings.c.category == "monogenic_variant",
            )
        )
        if rows:
            conn.execute(sa.insert(findings), rows)
        else:
            logger.info("no_cardiovascular_findings_to_store")

    logger.info("cardiovascular_findings_stored", count=len(rows))
    return len(rows)


# ── P3-20: FH variant status reporting ───────────────────────────────

# FH status values
FH_STATUS_POSITIVE = "Positive"
FH_STATUS_NEGATIVE = "Negative"


@dataclass
class FHStatus:
    """Familial hypercholesterolemia status determination.

    Summarises whether the sample has P/LP variants in FH genes
    (LDLR, PCSK9, APOB) and the clinical significance of each.
    """

    status: str  # Positive or Negative
    affected_genes: list[str]
    variant_count: int
    variants: list[CardiovascularVariantResult]
    has_homozygous: bool
    highest_evidence_level: int

    @property
    def is_positive(self) -> bool:
        return self.status == FH_STATUS_POSITIVE

    @property
    def summary_text(self) -> str:
        """Human-readable FH status summary."""
        if not self.is_positive:
            return (
                "No pathogenic or likely pathogenic variants identified in "
                "FH-associated genes (LDLR, PCSK9, APOB)."
            )

        genes_str = ", ".join(sorted(self.affected_genes))
        zygosity_note = " (includes homozygous variant)" if self.has_homozygous else ""
        return (
            f"Familial Hypercholesterolemia — {self.variant_count} pathogenic/"
            f"likely pathogenic variant(s) identified in {genes_str}"
            f"{zygosity_note}."
        )


def determine_fh_status(result: CardiovascularAnalysisResult) -> FHStatus:
    """Determine FH status from cardiovascular analysis results.

    Examines the FH-category variants (LDLR, PCSK9, APOB) from the
    cardiovascular extraction and classifies the sample as FH Positive
    or Negative.

    Args:
        result: CardiovascularAnalysisResult from extract_cardiovascular_variants.

    Returns:
        FHStatus with status determination and affected gene details.
    """
    fh_vars = result.fh_variants

    if not fh_vars:
        return FHStatus(
            status=FH_STATUS_NEGATIVE,
            affected_genes=[],
            variant_count=0,
            variants=[],
            has_homozygous=False,
            highest_evidence_level=0,
        )

    affected_genes = sorted({v.gene_symbol for v in fh_vars})
    has_hom = any(v.zygosity == "hom_alt" for v in fh_vars)
    max_evidence = max(v.evidence_level for v in fh_vars)

    status = FHStatus(
        status=FH_STATUS_POSITIVE,
        affected_genes=affected_genes,
        variant_count=len(fh_vars),
        variants=fh_vars,
        has_homozygous=has_hom,
        highest_evidence_level=max_evidence,
    )

    logger.info(
        "fh_status_determined",
        status=status.status,
        affected_genes=affected_genes,
        variant_count=len(fh_vars),
        has_homozygous=has_hom,
        highest_evidence_level=max_evidence,
    )

    return status


def store_fh_status_finding(
    fh_status: FHStatus,
    sample_engine: sa.Engine,
) -> int:
    """Store the FH status summary finding in the sample database.

    Creates a single summary finding with module='cardiovascular' and
    category='fh_status'. This is separate from the per-variant
    monogenic_variant findings and provides an at-a-glance FH determination.

    Always clears previous fh_status findings before inserting,
    ensuring idempotent re-runs.

    Args:
        fh_status: FHStatus from determine_fh_status.
        sample_engine: SQLAlchemy engine for the sample database.

    Returns:
        Number of findings inserted (0 or 1).
    """
    detail = {
        "status": fh_status.status,
        "affected_genes": fh_status.affected_genes,
        "variant_count": fh_status.variant_count,
        "has_homozygous": fh_status.has_homozygous,
        "highest_evidence_level": fh_status.highest_evidence_level,
        "fh_variants": [
            {
                "rsid": v.rsid,
                "gene_symbol": v.gene_symbol,
                "genotype": v.genotype,
                "zygosity": v.zygosity,
                "clinvar_significance": v.clinvar_significance,
                "clinvar_review_stars": v.clinvar_review_stars,
                "clinvar_accession": v.clinvar_accession,
                "evidence_level": v.evidence_level,
            }
            for v in fh_status.variants
        ],
    }

    row = {
        "module": "cardiovascular",
        "category": "fh_status",
        "evidence_level": fh_status.highest_evidence_level if fh_status.is_positive else None,
        "gene_symbol": None,
        "rsid": None,
        "finding_text": fh_status.summary_text,
        "conditions": "Familial Hypercholesterolemia" if fh_status.is_positive else None,
        "zygosity": None,
        "clinvar_significance": None,
        "pmid_citations": None,
        "detail_json": json.dumps(detail),
    }

    with sample_engine.begin() as conn:
        # Clear previous FH status finding
        conn.execute(
            sa.delete(findings).where(
                findings.c.module == "cardiovascular",
                findings.c.category == "fh_status",
            )
        )
        conn.execute(sa.insert(findings), [row])

    logger.info(
        "fh_status_finding_stored",
        status=fh_status.status,
        variant_count=fh_status.variant_count,
    )
    return 1

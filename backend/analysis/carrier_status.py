"""Carrier status gene panel definition, loader, and analysis module.

Implements P3-35 (panel) and P3-36 (het P/LP filtering):
  - P3-35: Curated carrier gene panel with expected ClinVar entries.
  - P3-36: Extract heterozygous ClinVar Pathogenic/Likely pathogenic variants
    in carrier panel genes. Homozygous P/LP = disease (out of scope).

Curated panel of 7 genes associated with autosomal recessive conditions
relevant to reproductive carrier screening:

    CFTR   — Cystic Fibrosis
    HBB    — Sickle Cell Disease / Beta-Thalassemia
    GBA    — Gaucher Disease
    HEXA   — Tay-Sachs Disease
    BRCA1  — Hereditary Breast and Ovarian Cancer (dual-role: cancer + carrier)
    BRCA2  — Hereditary Breast and Ovarian Cancer (dual-role: cancer + carrier)
    SMN1   — Spinal Muscular Atrophy

BRCA1/2 are included for reproductive carrier context — distinct from the
cancer module's disease predisposition framing.  A heterozygous BRCA1/2 P/LP
variant produces TWO distinct findings: one in the cancer module (disease
risk) and one in the carrier module (reproductive risk).

Usage::

    from backend.analysis.carrier_status import (
        load_carrier_panel,
        extract_carrier_variants,
        store_carrier_findings,
        CarrierPanel,
        CarrierGene,
        CarrierVariantResult,
        CarrierAnalysisResult,
    )

    panel = load_carrier_panel()
    result = extract_carrier_variants(panel, sample_engine)
    store_carrier_findings(result, sample_engine)
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import sqlalchemy as sa
import structlog

from backend.analysis.evidence import assign_clinvar_evidence_level
from backend.db.tables import annotated_variants, findings

logger = structlog.get_logger(__name__)

# Path to the curated panel JSON (relative to this file)
_PANEL_PATH = Path(__file__).resolve().parent.parent / "data" / "panels" / "carrier_panel.json"


# ── Data classes ──────────────────────────────────────────────────────────


@dataclass
class CarrierGene:
    """A single gene entry from the curated carrier panel."""

    gene_symbol: str
    name: str
    chromosome: str
    conditions: list[str]
    inheritance: str  # AR (most) or AD (BRCA1/2)
    evidence_level: int  # 1-4 stars
    cross_links: list[str]  # module names (e.g. "cancer" for BRCA1/2)
    expected_clinvar_rsids: list[str]
    pmids: list[str]
    notes: str

    @property
    def is_dual_role(self) -> bool:
        """Whether this gene produces findings in multiple modules."""
        return len(self.cross_links) > 0


@dataclass
class CarrierPanel:
    """The complete curated carrier status gene panel."""

    module: str
    version: str
    description: str
    genes: list[CarrierGene]

    def all_gene_symbols(self) -> list[str]:
        """Return all gene symbols in the panel."""
        return [g.gene_symbol for g in self.genes]

    def all_expected_rsids(self) -> list[str]:
        """Return all expected ClinVar rsids across all genes."""
        return [rsid for gene in self.genes for rsid in gene.expected_clinvar_rsids]

    def get_gene(self, gene_symbol: str) -> CarrierGene | None:
        """Look up a gene by symbol (case-insensitive)."""
        symbol_upper = gene_symbol.upper()
        for gene in self.genes:
            if gene.gene_symbol.upper() == symbol_upper:
                return gene
        return None

    def dual_role_genes(self) -> list[CarrierGene]:
        """Return genes that have cross-links to other modules."""
        return [g for g in self.genes if g.is_dual_role]

    def autosomal_recessive_genes(self) -> list[CarrierGene]:
        """Return only AR-inheritance genes (excludes BRCA1/2)."""
        return [g for g in self.genes if g.inheritance == "AR"]

    def genes_by_condition(self, condition: str) -> list[CarrierGene]:
        """Return all genes associated with a given condition (substring match)."""
        condition_lower = condition.lower()
        return [g for g in self.genes if any(condition_lower in c.lower() for c in g.conditions)]


# ── Panel loading ─────────────────────────────────────────────────────────


def load_carrier_panel(panel_path: Path | None = None) -> CarrierPanel:
    """Load the curated carrier gene panel from JSON.

    Args:
        panel_path: Optional override for the panel JSON path.
            Defaults to ``backend/data/panels/carrier_panel.json``.

    Returns:
        Parsed CarrierPanel with all genes.

    Raises:
        FileNotFoundError: If the panel JSON does not exist.
        json.JSONDecodeError: If the panel JSON is malformed.
    """
    path = panel_path or _PANEL_PATH
    logger.info("loading_carrier_panel", path=str(path))

    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    genes: list[CarrierGene] = []
    for idx, gene_data in enumerate(data["genes"]):
        try:
            genes.append(
                CarrierGene(
                    gene_symbol=gene_data["gene_symbol"],
                    name=gene_data["name"],
                    chromosome=gene_data["chromosome"],
                    conditions=gene_data["conditions"],
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

    try:
        module = data["module"]
        version = data["version"]
        description = data["description"]
    except KeyError as e:
        raise ValueError(f"Missing required panel field: {e}") from e

    panel = CarrierPanel(
        module=module,
        version=version,
        description=description,
        genes=genes,
    )

    logger.info(
        "carrier_panel_loaded",
        gene_count=len(panel.genes),
        total_expected_rsids=len(panel.all_expected_rsids()),
        dual_role_genes=[g.gene_symbol for g in panel.dual_role_genes()],
        ar_gene_count=len(panel.autosomal_recessive_genes()),
    )

    return panel


# ── P3-36: Carrier status analysis (het P/LP filtering) ──────────────────

# ClinVar significance values considered pathogenic
_PATHOGENIC_SIGNIFICANCE = {"Pathogenic", "Likely pathogenic", "Pathogenic/Likely pathogenic"}
_AUTOSOMAL_RECESSIVE_CARRIER_CATEGORY = "autosomal_recessive_carrier"
_DUAL_ROLE_CARRIER_CATEGORY = "autosomal_dominant_dual_role_carrier"
_SUPPORTED_CARRIER_INDEL_ZYGOSITY: dict[tuple[str, str, str, str], dict[str, str]] = {
    # CFTR F508del / p.Phe508del, represented in ClinVar/VCF form as ATCT>A.
    # Consumer-array exports can represent this marker either as a probe-level
    # A/T carrier call or as literal D/I indel tokens.
    ("CFTR", "rs113993960", "ATCT", "A"): {
        "AT": "het",
        "TA": "het",
        "DI": "het",
        "ID": "het",
        "DD": "hom_alt",
        "II": "hom_ref",
    },
}


@dataclass
class CarrierVariantResult:
    """A single heterozygous P/LP variant found in the carrier gene panel."""

    rsid: str
    gene_symbol: str
    genotype: str
    zygosity: str
    clinvar_significance: str
    clinvar_review_stars: int
    clinvar_accession: str | None
    clinvar_conditions: str | None
    conditions: list[str]
    inheritance: str
    evidence_level: int
    cross_links: list[str]
    pmids: list[str]
    notes: str


@dataclass
class CarrierAnalysisResult:
    """Complete carrier status analysis result for a sample."""

    variants: list[CarrierVariantResult] = field(default_factory=list)
    panel_genes_checked: int = 0
    variants_in_panel_genes: int = 0
    homozygous_plp_skipped: int = 0

    @property
    def carrier_count(self) -> int:
        """Number of heterozygous P/LP carrier variants found."""
        return len(self.variants)

    @property
    def dual_role_variants(self) -> list[CarrierVariantResult]:
        """Variants in genes with cross-links (e.g. BRCA1/2)."""
        return [v for v in self.variants if v.cross_links]

    @property
    def genes_with_findings(self) -> list[str]:
        """Unique gene symbols with carrier findings."""
        return sorted(set(v.gene_symbol for v in self.variants))


def _assign_carrier_evidence_level(
    clinvar_significance: str,
    clinvar_review_stars: int,
    gene_evidence_level: int,
) -> int:
    """Assign evidence level (1-4 stars) for carrier findings.

    Delegates to the centralized evidence framework (P3-40).
    """
    return assign_clinvar_evidence_level(
        clinvar_significance,
        clinvar_review_stars,
        gene_baseline=gene_evidence_level,
    )


def _has_cancer_crosslink(variant: CarrierVariantResult) -> bool:
    """Return whether the carrier finding cross-links to the Cancer module."""
    return "cancer" in variant.cross_links


def _is_hbb_hbs_trait(variant: CarrierVariantResult) -> bool:
    """Return whether this is the HBB HbS carrier finding."""
    rsid = (variant.rsid or "").strip().lower()
    return variant.gene_symbol.upper() == "HBB" and rsid == "rs334"


def _has_personal_risk_context(variant: CarrierVariantResult) -> bool:
    """Return whether the carrier finding also has personal disease-risk context."""
    return variant.inheritance == "AD" or _has_cancer_crosslink(variant)


def _carrier_finding_category(variant: CarrierVariantResult) -> str:
    """Return the storage category for a carrier finding."""
    if _has_personal_risk_context(variant):
        return _DUAL_ROLE_CARRIER_CATEGORY
    return _AUTOSOMAL_RECESSIVE_CARRIER_CATEGORY


def _carrier_finding_text(variant: CarrierVariantResult) -> str:
    """Build the user-facing carrier finding text for one variant."""
    condition_text = ", ".join(variant.conditions) if variant.conditions else "carrier status"
    base = (
        f"{variant.gene_symbol}: You carry one copy of a "
        f"{variant.clinvar_significance.lower()} variant ({variant.rsid}) "
        f"associated with {condition_text}. "
    )
    if _has_cancer_crosslink(variant):
        return (
            base + "This may be relevant for family planning. Because this gene also has "
            "cancer-predisposition implications, the same result may indicate "
            "personal hereditary cancer risk; review it in the Cancer module and "
            "with a genetics professional."
        )
    if _has_personal_risk_context(variant):
        return (
            base + "This may be relevant for family planning. Review this result with "
            "a genetics professional."
        )
    if _is_hbb_hbs_trait(variant):
        return (
            base + "This is consistent with sickle-cell trait, not sickle-cell "
            "disease. Sickle-cell trait is usually asymptomatic, but it has "
            "documented personal health associations including kidney findings, "
            "pulmonary embolism/VTE context, and exertional-stress risks such as "
            "rhabdomyolysis. Review this result with a clinician or genetics "
            "professional. This may also be relevant for family planning."
        )
    return base + "Carriers are typically unaffected. This may be relevant for family planning."


def _classify_supported_carrier_indel(
    gene: CarrierGene,
    *,
    rsid: str | None,
    genotype: str | None,
    ref: str | None,
    alt: str | None,
) -> str | None:
    """Resolve carrier-panel indels with explicit, curated raw-call mappings."""
    if not rsid or not genotype or not ref or not alt:
        return None
    if rsid not in gene.expected_clinvar_rsids:
        return None

    key = (gene.gene_symbol.upper(), rsid, ref.upper(), alt.upper())
    calls = _SUPPORTED_CARRIER_INDEL_ZYGOSITY.get(key)
    if calls is None:
        return None
    return calls.get(genotype.strip().upper())


def _carrier_row_zygosity(row: sa.Row, gene: CarrierGene) -> str | None:
    """Return annotated zygosity, with a carrier-only rescue for supported indels."""
    if row.zygosity is not None:
        return row.zygosity
    return _classify_supported_carrier_indel(
        gene,
        rsid=row.rsid,
        genotype=row.genotype,
        ref=row.ref,
        alt=row.alt,
    )


def extract_carrier_variants(
    panel: CarrierPanel,
    sample_engine: sa.Engine,
) -> CarrierAnalysisResult:
    """Extract heterozygous ClinVar P/LP variants in the carrier gene panel.

    Queries annotated_variants for variants where:
      1. gene_symbol is in the carrier panel genes
      2. clinvar_significance is Pathogenic or Likely pathogenic
      3. zygosity is 'het' (heterozygous only — homozygous = disease)

    Homozygous P/LP variants are counted but excluded from carrier findings,
    as they represent affected status rather than carrier status.

    Args:
        panel: Loaded CarrierPanel.
        sample_engine: SQLAlchemy engine for the sample database.

    Returns:
        CarrierAnalysisResult with all het P/LP variants found.
    """
    gene_symbols = panel.all_gene_symbols()
    gene_map = {g.gene_symbol.upper(): g for g in panel.genes}

    with sample_engine.connect() as conn:
        # Count total variants in panel genes
        count_stmt = (
            sa.select(sa.func.count())
            .select_from(annotated_variants)
            .where(annotated_variants.c.gene_symbol.in_(gene_symbols))
        )
        total_in_panel = conn.execute(count_stmt).scalar() or 0

        # Fetch all P/LP variants in panel genes (both het and hom)
        stmt = (
            sa.select(
                annotated_variants.c.rsid,
                annotated_variants.c.gene_symbol,
                annotated_variants.c.genotype,
                annotated_variants.c.ref,
                annotated_variants.c.alt,
                annotated_variants.c.zygosity,
                annotated_variants.c.clinvar_significance,
                annotated_variants.c.clinvar_review_stars,
                annotated_variants.c.clinvar_accession,
                annotated_variants.c.clinvar_conditions,
            )
            .where(
                annotated_variants.c.gene_symbol.in_(gene_symbols),
                annotated_variants.c.clinvar_significance.in_(list(_PATHOGENIC_SIGNIFICANCE)),
            )
            .order_by(annotated_variants.c.gene_symbol, annotated_variants.c.rsid)
        )
        rows = conn.execute(stmt).fetchall()

    variants: list[CarrierVariantResult] = []
    hom_skipped = 0

    for row in rows:
        gene_info = gene_map.get((row.gene_symbol or "").upper())
        if gene_info is None:
            continue

        # P3-36: Heterozygous only — homozygous P/LP = affected, not carrier
        zygosity = _carrier_row_zygosity(row, gene_info)
        if zygosity != "het":
            hom_skipped += 1
            continue

        evidence = _assign_carrier_evidence_level(
            row.clinvar_significance or "",
            row.clinvar_review_stars or 0,
            gene_info.evidence_level,
        )

        variants.append(
            CarrierVariantResult(
                rsid=row.rsid,
                gene_symbol=row.gene_symbol,
                genotype=row.genotype or "",
                zygosity=zygosity,
                clinvar_significance=row.clinvar_significance,
                clinvar_review_stars=row.clinvar_review_stars or 0,
                clinvar_accession=row.clinvar_accession,
                clinvar_conditions=row.clinvar_conditions,
                conditions=gene_info.conditions,
                inheritance=gene_info.inheritance,
                evidence_level=evidence,
                cross_links=gene_info.cross_links,
                pmids=gene_info.pmids,
                notes=gene_info.notes,
            )
        )

    logger.info(
        "carrier_variants_extracted",
        panel_genes=len(gene_symbols),
        variants_in_panel_genes=total_in_panel,
        carrier_variants=len(variants),
        homozygous_plp_skipped=hom_skipped,
        dual_role_variants=len([v for v in variants if v.cross_links]),
    )

    return CarrierAnalysisResult(
        variants=variants,
        panel_genes_checked=len(gene_symbols),
        variants_in_panel_genes=total_in_panel,
        homozygous_plp_skipped=hom_skipped,
    )


# ── Findings storage ─────────────────────────────────────────────────────


def store_carrier_findings(
    result: CarrierAnalysisResult,
    sample_engine: sa.Engine,
) -> int:
    """Store carrier status findings in the sample database.

    Creates one finding per heterozygous P/LP variant with module='carrier'.
    Classic autosomal-recessive findings use reproductive framing language;
    dual-role BRCA1/2 findings preserve reproductive context without hiding
    their personal hereditary-cancer-risk implications.

    BRCA1/2 findings are stored with cross_links in detail_json,
    enabling the UI to show a dual-role banner linking to the
    cancer module.

    Args:
        result: CarrierAnalysisResult from extract_carrier_variants.
        sample_engine: SQLAlchemy engine for the sample database.

    Returns:
        Number of findings inserted.
    """
    rows: list[dict] = []

    for v in result.variants:
        finding_text = _carrier_finding_text(v)

        detail = {
            "clinvar_accession": v.clinvar_accession,
            "clinvar_review_stars": v.clinvar_review_stars,
            "clinvar_conditions": v.clinvar_conditions,
            "conditions": v.conditions,
            "inheritance": v.inheritance,
            "cross_links": v.cross_links,
            "genotype": v.genotype,
            "notes": v.notes,
        }

        rows.append(
            {
                "module": "carrier",
                "category": _carrier_finding_category(v),
                "evidence_level": v.evidence_level,
                "gene_symbol": v.gene_symbol,
                "rsid": v.rsid,
                "finding_text": finding_text,
                "conditions": v.clinvar_conditions,
                "zygosity": "het",
                "clinvar_significance": v.clinvar_significance,
                "pmid_citations": json.dumps(v.pmids),
                "detail_json": json.dumps(detail),
            }
        )

    if not rows:
        logger.info("no_carrier_findings_to_store")
        return 0

    with sample_engine.begin() as conn:
        # Clear previous carrier findings before inserting fresh
        conn.execute(sa.delete(findings).where(findings.c.module == "carrier"))
        conn.execute(sa.insert(findings), rows)

    logger.info("carrier_findings_stored", count=len(rows))
    return len(rows)

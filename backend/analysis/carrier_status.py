"""Carrier status gene panel definition, loader, and analysis module.

Implements P3-35 (panel) and P3-36 (carrier and affected-status filtering):
  - P3-35: Curated carrier gene panel with expected ClinVar entries.
  - P3-36: Extract heterozygous ClinVar Pathogenic/Likely pathogenic variants
    in carrier panel genes, and surface autosomal-recessive homozygous or
    possible compound-heterozygous P/LP patterns as affected-status findings.

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
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import sqlalchemy as sa
import structlog

from backend.analysis.clinvar_significance import (
    LOWER_PENETRANCE_RISK_ALLELE_CATEGORY,
    LOWER_PENETRANCE_RISK_ALLELE_PMIDS,
    is_low_penetrance_or_risk_allele,
    is_pathogenic_primary,
    low_penetrance_or_risk_allele_filter,
    pathogenic_significance_filter,
)
from backend.analysis.evidence import assign_clinvar_evidence_level
from backend.analysis.zygosity import is_implausible_recessive_affected_hom_alt
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

# Genes whose array-derived calls are too unreliable to report as carrier
# findings because a highly homologous pseudogene confounds genotyping. GBA1's
# pseudogene GBAP1 is ~96% homologous in the coding region (rising to ~98% across
# exons 8–11, where the carrier-panel variants N370S/rs76763715 and
# L444P/rs421016 both sit), so array-based GBA1 genotyping mis-calls these
# positions (Pachchek et al. 2023, npj Park Dis, PMID 37996455; Filocamo et al.
# 2001, J Med Genet — both N370S and L444P mis-genotyped). The Parkinson's module
# already suppresses GBA1 on these grounds (see parkinsons.py / disclaimers.py);
# carrier status — a reproductive-risk finding — applies the same policy rather
# than turning a questionable array call into a carrier result (#221).
_PSEUDOGENE_UNRELIABLE_GENES = frozenset({"GBA"})
_COPY_NUMBER_INCOMPLETE_GENE_CAVEATS = {
    "SMN1": (
        "Copy-number not assessed: SNP-array data do not measure SMN1 exon 7 "
        "dosage/copy-number. Confirm SMN1 status with clinical testing that "
        "includes dosage/CNV assessment, such as qPCR or MLPA."
    )
}
_AUTOSOMAL_RECESSIVE_CARRIER_CATEGORY = "autosomal_recessive_carrier"
_AUTOSOMAL_RECESSIVE_AFFECTED_CATEGORY = "autosomal_recessive_affected"
_AUTOSOMAL_RECESSIVE_COMPOUND_HET_CATEGORY = "autosomal_recessive_possible_compound_heterozygote"
_DUAL_ROLE_CARRIER_CATEGORY = "autosomal_dominant_dual_role_carrier"
_FINDING_TYPE_CARRIER = "carrier"
_FINDING_TYPE_AFFECTED_HOMOZYGOUS = "affected_homozygous"
_FINDING_TYPE_POSSIBLE_COMPOUND_HET = "possible_compound_heterozygote"
_AFFECTED_HOMOZYGOUS_ZYGOSITIES = frozenset({"hom", "hom_alt"})
# Re-verifiable provenance for the I/D indel polarity used below (#256). The
# vendor I/D token convention is the same one applied to GJB2 35delG
# (gene_health_panel.json) and APOL1 G2 (apol1_panel.json) — kept here as a
# traceable datum so a future manifest/code change can't silently invert a
# clinical carrier call. Locked by tests/backend/test_indel_polarity_provenance.py.
_CFTR_F508DEL_INDEL_POLARITY: dict[str, str | list[str]] = {
    "variant_class": "deletion",
    "variant_allele_token": "D",
    "reference_allele_token": "I",
    "d_token_meaning": "deletion (F508del / c.1521_1523delCTT variant allele)",
    "i_token_meaning": "insertion / reference (the CTT codon is present)",
    "hgvs": "NM_000492.4:c.1521_1523delCTT (p.Phe508del)",
    "dbsnp": "rs113993960",
    "vcf_form": "ATCT>A",
    "vendor_id_convention": (
        "23andMe / AncestryDNA encode indel markers with literal I/D tokens where "
        "D = the deletion (shorter) allele and I = the insertion/reference (longer) "
        "allele; the ingested-array parsers pass these tokens through unchanged, so "
        "DD resolves to hom_alt (F508del/F508del), II to hom_ref, and DI/ID to het."
    ),
    # List form, matching the three panel indel_polarity records (#570) — a future
    # consumer iterating pmids must get whole PMIDs, not characters of a string.
    "pmids": ["2570460"],  # Kerem et al. 1989, Science — CFTR gene + F508del deletion
    "accessed": "2026-06-13",
}
_HEXA_EXON11_DUP_INDEL_POLARITY: dict[str, str | list[str]] = {
    "variant_class": "insertion",
    "variant_allele_token": "I",
    "reference_allele_token": "D",
    "i_token_meaning": "insertion / duplication (c.1274_1277dupTATC variant allele)",
    "d_token_meaning": "deletion / shorter reference allele (the duplicated TATC is absent)",
    "hgvs": "NM_000520.6:c.1274_1277dupTATC (p.Tyr427IlefsTer5)",
    "dbsnp": "rs387906309",
    "vcf_form": "GATA>GATAGATA",
    "vendor_id_convention": (
        "23andMe / AncestryDNA encode indel markers with literal I/D tokens where "
        "I = the insertion/longer allele and D = the deletion/shorter allele. "
        "For this HEXA founder allele the longer inserted/duplicated allele is "
        "pathogenic, so II resolves to hom_alt, DD to hom_ref, and DI/ID to het."
    ),
    "pmids": ["2848800", "2355960", "2220809"],
    "accessed": "2026-07-01",
}

_SUPPORTED_CARRIER_INDEL_ZYGOSITY: dict[tuple[str, str, str, str], dict[str, str]] = {
    # CFTR F508del / p.Phe508del, represented in ClinVar/VCF form as ATCT>A.
    # Consumer-array exports can represent this marker either as a probe-level
    # A/T carrier call or as literal D/I indel tokens. The D=deletion (variant) /
    # I=reference polarity is documented in _CFTR_F508DEL_INDEL_POLARITY (#256).
    ("CFTR", "rs113993960", "ATCT", "A"): {
        "AT": "het",
        "TA": "het",
        "DI": "het",
        "ID": "het",
        "DD": "hom_alt",
        "II": "hom_ref",
    },
    # HEXA Ashkenazi Jewish founder allele c.1274_1277dupTATC, represented by
    # Ensembl GRCh37/dbSNP as GATA>GATAGATA. Unlike CFTR F508del, the variant
    # allele is the insertion/longer allele, so I carries the pathogenic allele.
    ("HEXA", "rs387906309", "GATA", "GATAGATA"): {
        "DI": "het",
        "ID": "het",
        "II": "hom_alt",
        "DD": "hom_ref",
    },
}


@dataclass
class CarrierVariantResult:
    """A single user-facing carrier-module finding."""

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
    clinvar_low_penetrance_or_risk_allele: bool = False
    finding_type: str = _FINDING_TYPE_CARRIER
    variant_ids: list[str] = field(default_factory=list)
    component_variants: list[dict[str, object]] = field(default_factory=list)
    phase_caveat: str | None = None
    copy_number_limited: bool = False
    copy_number_caveat: str | None = None


@dataclass
class CarrierAnalysisResult:
    """Complete carrier status analysis result for a sample."""

    variants: list[CarrierVariantResult] = field(default_factory=list)
    panel_genes_checked: int = 0
    variants_in_panel_genes: int = 0
    homozygous_plp_skipped: int = 0
    affected_status_findings: int = 0
    possible_compound_heterozygous_findings: int = 0
    affected_hom_alt_plausibility_suppressed: int = 0
    # P/LP rows dropped because the gene is pseudogene-confounded from array data
    # (GBA1/GBAP1) and not reportable as a carrier finding (#221).
    pseudogene_suppressed: int = 0
    # Findings still reported but carrying a mandatory copy-number limitation
    # disclosure because SNP arrays do not assay the main disease mechanism.
    copy_number_disclosed: int = 0

    @property
    def carrier_count(self) -> int:
        """Number of carrier-module findings found."""
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
    if variant.finding_type == _FINDING_TYPE_AFFECTED_HOMOZYGOUS:
        return _AUTOSOMAL_RECESSIVE_AFFECTED_CATEGORY
    if variant.finding_type == _FINDING_TYPE_POSSIBLE_COMPOUND_HET:
        return _AUTOSOMAL_RECESSIVE_COMPOUND_HET_CATEGORY
    if variant.clinvar_low_penetrance_or_risk_allele:
        return LOWER_PENETRANCE_RISK_ALLELE_CATEGORY
    if _has_personal_risk_context(variant):
        return _DUAL_ROLE_CARRIER_CATEGORY
    return _AUTOSOMAL_RECESSIVE_CARRIER_CATEGORY


def _variant_id_text(variant: CarrierVariantResult) -> str:
    """Return a compact display list for one or more component variants."""
    return ", ".join(variant.variant_ids or [variant.rsid])


def _carrier_finding_text(variant: CarrierVariantResult) -> str:
    """Build the user-facing carrier finding text for one variant."""
    condition_text = ", ".join(variant.conditions) if variant.conditions else "carrier status"
    variant_text = _variant_id_text(variant)
    copy_number_caveat = f"{variant.copy_number_caveat} " if variant.copy_number_caveat else ""
    if variant.finding_type == _FINDING_TYPE_AFFECTED_HOMOZYGOUS:
        return (
            f"{variant.gene_symbol}: A homozygous {variant.clinvar_significance.lower()} "
            f"variant ({variant_text}) is present in an autosomal-recessive disease gene "
            f"associated with {condition_text}. This is an affected-status result, not a "
            f"typical carrier finding. {copy_number_caveat}Review this result with a "
            "clinician or genetics professional and confirm with clinical-grade testing."
        )
    if variant.finding_type == _FINDING_TYPE_POSSIBLE_COMPOUND_HET:
        caveat = variant.phase_caveat or (
            "Genotyping arrays do not phase these variants, so clinical testing is needed "
            "to determine whether they are on opposite chromosomes."
        )
        return (
            f"{variant.gene_symbol}: Two distinct {variant.clinvar_significance.lower()} "
            f"variants ({variant_text}) were found in an autosomal-recessive disease gene "
            f"associated with {condition_text}. If these variants are in trans, this pattern "
            "is consistent with affected status rather than an unaffected carrier state. "
            f"{caveat} {copy_number_caveat}Review this result with a clinician or "
            "genetics professional."
        )
    base = (
        f"{variant.gene_symbol}: You carry one copy of a "
        f"{variant.clinvar_significance.lower()} variant ({variant.rsid}) "
        f"associated with {condition_text}. "
    )
    if variant.copy_number_caveat:
        return (
            base
            + variant.copy_number_caveat
            + " Review this result with a genetics professional. This may be relevant "
            "for family planning."
        )
    if variant.clinvar_low_penetrance_or_risk_allele:
        return (
            base + "ClinVar marks this as lower-penetrance/risk-allele, so it is "
            "reported separately from high-penetrance P/LP carrier findings. "
            "Review this result with a genetics professional."
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


def _carrier_variant_dedupe_key(row: sa.Row, zygosity: str) -> tuple[object, ...]:
    """Return a key for one biological carried allele, not one array probe row."""
    chrom = (row.chrom or "").strip().casefold()
    ref = (row.ref or "").strip().upper()
    alt = (row.alt or "").strip().upper()
    gene_symbol = (row.gene_symbol or "").strip().upper()
    rsid = (row.rsid or "").strip().casefold()
    if not chrom or row.pos is None or not ref or not alt:
        if rsid:
            return ("rsid", gene_symbol, rsid, zygosity)
        return (
            "incomplete_allele",
            gene_symbol,
            chrom,
            row.pos,
            ref,
            alt,
            (row.genotype or "").strip().upper(),
            (row.clinvar_accession or "").strip().casefold(),
            zygosity,
        )
    return ("allele", gene_symbol, chrom, int(row.pos), ref, alt, zygosity)


def _carrier_rsid_preference_key(row: sa.Row) -> tuple[int, str]:
    """Prefer public dbSNP rsIDs over array probe IDs for duplicate allele rows."""
    rsid = (row.rsid or "").strip()
    normalized = rsid.casefold()
    is_dbsnp_rsid = normalized.startswith("rs") and normalized[2:].isdigit()
    return (0 if is_dbsnp_rsid else 1, normalized)


def _unique_join(values: list[str | None]) -> str:
    """Join non-empty values while preserving first-seen order."""
    seen: set[str] = set()
    kept: list[str] = []
    for value in values:
        if not value:
            continue
        normalized = value.strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        kept.append(normalized)
    return "; ".join(kept)


def _component_variant_from_row(row: sa.Row, zygosity: str) -> dict[str, object]:
    """Return serializable component detail for an aggregate carrier finding."""
    return {
        "rsid": row.rsid,
        "chrom": row.chrom,
        "pos": row.pos,
        "ref": row.ref,
        "alt": row.alt,
        "genotype": row.genotype,
        "zygosity": zygosity,
        "clinvar_significance": row.clinvar_significance,
        "clinvar_review_stars": row.clinvar_review_stars or 0,
        "clinvar_accession": row.clinvar_accession,
        "clinvar_conditions": row.clinvar_conditions,
    }


def _copy_number_caveat_for_gene(gene_symbol: str | None) -> str | None:
    """Return a mandatory caveat for genes whose main mechanism is copy number."""
    return _COPY_NUMBER_INCOMPLETE_GENE_CAVEATS.get((gene_symbol or "").strip().upper())


def _variant_ids_from_rows(rows: list[sa.Row]) -> list[str]:
    """Return stable component variant IDs for display and storage."""
    return [(row.rsid or "").strip() for row in rows if (row.rsid or "").strip()]


def _evidence_level_for_rows(rows: list[sa.Row], gene_info: CarrierGene) -> int:
    """Return conservative aggregate evidence across component variant rows."""
    levels = [
        _assign_carrier_evidence_level(
            row.clinvar_significance or "",
            row.clinvar_review_stars or 0,
            gene_info.evidence_level,
        )
        for row in rows
    ]
    return min(levels) if levels else gene_info.evidence_level


def _build_carrier_variant_result(
    row: sa.Row,
    gene_info: CarrierGene,
    zygosity: str,
) -> CarrierVariantResult:
    """Build a standard single-variant carrier finding."""
    evidence = _assign_carrier_evidence_level(
        row.clinvar_significance or "",
        row.clinvar_review_stars or 0,
        gene_info.evidence_level,
    )
    lower_penetrance = is_low_penetrance_or_risk_allele(row.clinvar_significance)
    variant_ids = _variant_ids_from_rows([row])
    copy_number_caveat = _copy_number_caveat_for_gene(gene_info.gene_symbol)

    return CarrierVariantResult(
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
        clinvar_low_penetrance_or_risk_allele=lower_penetrance,
        finding_type=_FINDING_TYPE_CARRIER,
        variant_ids=variant_ids,
        component_variants=[_component_variant_from_row(row, zygosity)],
        copy_number_limited=copy_number_caveat is not None,
        copy_number_caveat=copy_number_caveat,
    )


def _build_aggregate_carrier_result(
    entries: list[tuple[sa.Row, str]],
    gene_info: CarrierGene,
    *,
    finding_type: str,
    zygosity: str,
    phase_caveat: str | None = None,
) -> CarrierVariantResult:
    """Build a single affected-status finding from multiple component rows."""
    sorted_entries = sorted(entries, key=lambda entry: _carrier_rsid_preference_key(entry[0]))
    sorted_rows = [entry[0] for entry in sorted_entries]
    variant_ids = _variant_ids_from_rows(sorted_rows)
    genotype = "; ".join(
        f"{row.rsid}:{row.genotype}" for row in sorted_rows if row.rsid and row.genotype
    )
    significances = _unique_join([row.clinvar_significance for row in sorted_rows])
    accessions = _unique_join([row.clinvar_accession for row in sorted_rows]) or None
    clinvar_conditions = _unique_join([row.clinvar_conditions for row in sorted_rows]) or None
    copy_number_caveat = _copy_number_caveat_for_gene(gene_info.gene_symbol)

    return CarrierVariantResult(
        rsid=", ".join(variant_ids),
        gene_symbol=gene_info.gene_symbol,
        genotype=genotype,
        zygosity=zygosity,
        clinvar_significance=significances,
        clinvar_review_stars=min((row.clinvar_review_stars or 0) for row in sorted_rows),
        clinvar_accession=accessions,
        clinvar_conditions=clinvar_conditions,
        conditions=gene_info.conditions,
        inheritance=gene_info.inheritance,
        evidence_level=_evidence_level_for_rows(sorted_rows, gene_info),
        cross_links=gene_info.cross_links,
        pmids=gene_info.pmids,
        notes=gene_info.notes,
        finding_type=finding_type,
        variant_ids=variant_ids,
        component_variants=[
            _component_variant_from_row(row, component_zygosity)
            for row, component_zygosity in sorted_entries
        ],
        phase_caveat=phase_caveat,
        copy_number_limited=copy_number_caveat is not None,
        copy_number_caveat=copy_number_caveat,
    )


def extract_carrier_variants(
    panel: CarrierPanel,
    sample_engine: sa.Engine,
) -> CarrierAnalysisResult:
    """Extract carrier and affected-status findings in the carrier gene panel.

    Queries annotated_variants for variants where:
      1. gene_symbol is in the carrier panel genes
      2. clinvar_significance is Pathogenic or Likely pathogenic
      3. zygosity indicates heterozygous carriage, homozygous affected status,
         or multiple possible compound-heterozygous AR variants.

    Homozygous AR P/LP variants and AR genes with two or more distinct
    heterozygous P/LP variants are surfaced as affected-status findings rather
    than ordinary "typically unaffected" carrier findings.

    Args:
        panel: Loaded CarrierPanel.
        sample_engine: SQLAlchemy engine for the sample database.

    Returns:
        CarrierAnalysisResult with all carrier-module findings found.
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
                annotated_variants.c.chrom,
                annotated_variants.c.pos,
                annotated_variants.c.gene_symbol,
                annotated_variants.c.genotype,
                annotated_variants.c.ref,
                annotated_variants.c.alt,
                annotated_variants.c.zygosity,
                annotated_variants.c.clinvar_significance,
                annotated_variants.c.clinvar_review_stars,
                annotated_variants.c.clinvar_accession,
                annotated_variants.c.clinvar_conditions,
                annotated_variants.c.gnomad_af_global,
                annotated_variants.c.gnomad_af_popmax,
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
            )
            .order_by(annotated_variants.c.gene_symbol, annotated_variants.c.rsid)
        )
        rows = conn.execute(stmt).fetchall()

    variants: list[CarrierVariantResult] = []
    carrier_rows: dict[tuple[object, ...], tuple[sa.Row, CarrierGene, str]] = {}
    homozygous_affected_rows: dict[tuple[object, ...], tuple[sa.Row, CarrierGene, str]] = {}
    carrier_rows_seen = 0
    hom_skipped = 0
    pseudogene_suppressed = 0
    affected_hom_alt_plausibility_suppressed = 0

    for row in rows:
        gene_info = gene_map.get((row.gene_symbol or "").upper())
        if gene_info is None:
            continue

        # Pseudogene-confounded genes (GBA1/GBAP1) are unreliable from array data,
        # so they are not reported as carrier findings — same policy the Parkinson's
        # module applies to GBA1 (#221). Confirm with a GBA1-specific clinical assay.
        if (row.gene_symbol or "").upper() in _PSEUDOGENE_UNRELIABLE_GENES:
            pseudogene_suppressed += 1
            continue

        # P3-36: Ordinary carrier rows are heterozygous; AR homozygous P/LP rows
        # are affected-status signals and must not be labeled "typically unaffected."
        zygosity = _carrier_row_zygosity(row, gene_info)
        lower_penetrance = is_low_penetrance_or_risk_allele(row.clinvar_significance)
        high_penetrance_plp = is_pathogenic_primary(row.clinvar_significance)
        if (
            gene_info.inheritance == "AR"
            and high_penetrance_plp
            and not lower_penetrance
            and zygosity in _AFFECTED_HOMOZYGOUS_ZYGOSITIES
        ):
            if is_implausible_recessive_affected_hom_alt(
                row,
                gene_info.inheritance,
                zygosity=zygosity,
            ):
                affected_hom_alt_plausibility_suppressed += 1
                continue
            dedupe_key = _carrier_variant_dedupe_key(row, "hom_alt")
            existing = homozygous_affected_rows.get(dedupe_key)
            if existing is None or _carrier_rsid_preference_key(
                row
            ) < _carrier_rsid_preference_key(existing[0]):
                homozygous_affected_rows[dedupe_key] = (row, gene_info, "hom_alt")
            continue

        if zygosity != "het":
            hom_skipped += 1
            continue

        carrier_rows_seen += 1
        dedupe_key = _carrier_variant_dedupe_key(row, zygosity)
        existing = carrier_rows.get(dedupe_key)
        if existing is None or _carrier_rsid_preference_key(row) < _carrier_rsid_preference_key(
            existing[0]
        ):
            carrier_rows[dedupe_key] = (row, gene_info, zygosity)

    homozygous_by_gene: dict[str, list[tuple[sa.Row, CarrierGene, str]]] = defaultdict(list)
    for row, gene_info, zygosity in homozygous_affected_rows.values():
        homozygous_by_gene[gene_info.gene_symbol.upper()].append((row, gene_info, zygosity))

    het_plp_by_gene: dict[str, list[tuple[sa.Row, CarrierGene, str]]] = defaultdict(list)
    for row, gene_info, zygosity in carrier_rows.values():
        if (
            gene_info.inheritance == "AR"
            and is_pathogenic_primary(row.clinvar_significance)
            and not is_low_penetrance_or_risk_allele(row.clinvar_significance)
        ):
            het_plp_by_gene[gene_info.gene_symbol.upper()].append((row, gene_info, zygosity))

    affected_genes = set(homozygous_by_gene)
    compound_het_genes = {
        gene_symbol
        for gene_symbol, entries in het_plp_by_gene.items()
        if gene_symbol not in affected_genes and len(entries) >= 2
    }

    for gene_symbol, entries in homozygous_by_gene.items():
        gene_info = entries[0][1]
        variants.append(
            _build_aggregate_carrier_result(
                [(entry[0], entry[2]) for entry in entries],
                gene_info,
                finding_type=_FINDING_TYPE_AFFECTED_HOMOZYGOUS,
                zygosity="hom_alt",
            )
        )

    for gene_symbol, entries in het_plp_by_gene.items():
        if gene_symbol not in compound_het_genes:
            continue
        gene_info = entries[0][1]
        variants.append(
            _build_aggregate_carrier_result(
                [(entry[0], entry[2]) for entry in entries],
                gene_info,
                finding_type=_FINDING_TYPE_POSSIBLE_COMPOUND_HET,
                zygosity="possible_compound_heterozygous",
                phase_caveat=(
                    "Genotyping arrays do not phase these variants, so this result cannot "
                    "distinguish in-trans affected status from same-chromosome variants."
                ),
            )
        )

    for row, gene_info, zygosity in carrier_rows.values():
        gene_symbol = gene_info.gene_symbol.upper()
        if (
            gene_symbol in affected_genes
            and is_pathogenic_primary(row.clinvar_significance)
            and not is_low_penetrance_or_risk_allele(row.clinvar_significance)
        ):
            continue
        if (
            gene_symbol in compound_het_genes
            and is_pathogenic_primary(row.clinvar_significance)
            and not is_low_penetrance_or_risk_allele(row.clinvar_significance)
        ):
            continue
        variants.append(_build_carrier_variant_result(row, gene_info, zygosity))

    variants.sort(
        key=lambda variant: (
            variant.gene_symbol.upper(),
            variant.finding_type,
            (variant.rsid or "").casefold(),
        )
    )
    copy_number_disclosed = sum(1 for variant in variants if variant.copy_number_limited)

    logger.info(
        "carrier_variants_extracted",
        panel_genes=len(gene_symbols),
        variants_in_panel_genes=total_in_panel,
        carrier_variants=len(variants),
        duplicate_carrier_rows=carrier_rows_seen - len(carrier_rows),
        homozygous_plp_skipped=hom_skipped,
        affected_status_findings=len(affected_genes),
        possible_compound_heterozygous_findings=len(compound_het_genes),
        affected_hom_alt_plausibility_suppressed=affected_hom_alt_plausibility_suppressed,
        pseudogene_suppressed=pseudogene_suppressed,
        copy_number_disclosed=copy_number_disclosed,
        dual_role_variants=len([v for v in variants if v.cross_links]),
    )

    return CarrierAnalysisResult(
        variants=variants,
        panel_genes_checked=len(gene_symbols),
        variants_in_panel_genes=total_in_panel,
        homozygous_plp_skipped=hom_skipped,
        affected_status_findings=len(affected_genes),
        possible_compound_heterozygous_findings=len(compound_het_genes),
        affected_hom_alt_plausibility_suppressed=affected_hom_alt_plausibility_suppressed,
        pseudogene_suppressed=pseudogene_suppressed,
        copy_number_disclosed=copy_number_disclosed,
    )


# ── Findings storage ─────────────────────────────────────────────────────


def store_carrier_findings(
    result: CarrierAnalysisResult,
    sample_engine: sa.Engine,
) -> int:
    """Store carrier status findings in the sample database.

    Creates one finding per carrier-module result with module='carrier'.
    Classic autosomal-recessive carrier findings use reproductive framing
    language; AR affected-status findings avoid "typically unaffected" carrier
    wording; dual-role BRCA1/2 findings preserve reproductive context without
    hiding their personal hereditary-cancer-risk implications.

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
            "clinvar_low_penetrance_or_risk_allele": (v.clinvar_low_penetrance_or_risk_allele),
            "finding_type": v.finding_type,
            "variant_ids": v.variant_ids,
            "component_variants": v.component_variants,
            "phase_caveat": v.phase_caveat,
            "copy_number_limited": v.copy_number_limited,
            "copy_number_caveat": v.copy_number_caveat,
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
        # Clear previous carrier findings before inserting fresh
        conn.execute(sa.delete(findings).where(findings.c.module == "carrier"))
        if not rows:
            logger.info("no_carrier_findings_to_store")
            return 0
        conn.execute(sa.insert(findings), rows)

    logger.info("carrier_findings_stored", count=len(rows))
    return len(rows)

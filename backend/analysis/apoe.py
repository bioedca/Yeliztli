"""APOE genotype determination and findings generation.

Implements P3-22a: Determine the APOE diplotype from two defining SNPs.
Implements P3-22b: Generate three APOE findings (CV risk, Alzheimer's, lipid/dietary).

APOE alleles are defined by combinations at two positions on chromosome 19:
  - rs429358 (codon 112): T→C corresponds to Cys→Arg
  - rs7412   (codon 158): C→T corresponds to Arg→Cys

Haplotype definitions (forward-strand alleles):
  - ε2: rs429358=T + rs7412=T  (Cys112, Cys158)
  - ε3: rs429358=T + rs7412=C  (Cys112, Arg158)  ← reference/common
  - ε4: rs429358=C + rs7412=C  (Arg112, Arg158)

Array-reliability caveat (#557): the two ε-defining SNPs are a recognised
array-genotyping weak spot — they are absent from most genome-wide arrays and
"only imperfectly captured" on common microarray platforms (Radmanesh 2014,
PMID 24448547; Lill 2012, PMID 22972946), and array/imputed APOE agrees only
~90% (ε genotype) / ~93% (ε4 status) with direct clinical genotyping
(Oldmeadow 2014, PMID 24903779). 23andMe v5 adds them as custom (directly
typed) content, but other vendors/arrays do not, so an array ε-call — and the
ε4 Alzheimer's finding that rides on it — is NOT equivalent to clinical
genotyping. This module therefore attaches ``APOE_ARRAY_RELIABILITY_CAVEAT`` to
the genotype and every derived finding. Following the repo's reliability-flag
pattern (``array_confidence`` / ``gene_constraint``), this is a caveat ONLY: it
records that an actionable ε-call should be confirmed in a CLIA/accredited lab,
and it never changes the (well-established) APOE evidence level.

Usage::

    from backend.analysis.apoe import determine_apoe_genotype, APOEResult

    result = determine_apoe_genotype(sample_engine)
    print(result.diplotype)   # e.g. "ε3/ε4"
    print(result.has_e4)      # True
    print(result.e4_count)    # 1
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

import sqlalchemy as sa
import structlog

from backend.analysis.zygosity import is_no_call
from backend.db.tables import findings, raw_variants

logger = structlog.get_logger(__name__)

# ── APOE defining SNPs ──────────────────────────────────────────────────

APOE_RS429358 = "rs429358"  # codon 112: T=Cys (ε2/ε3), C=Arg (ε4)
APOE_RS7412 = "rs7412"  # codon 158: C=Arg (ε3/ε4), T=Cys (ε2)

# Chromosome 19 positions on the app's primary build, GRCh37 (Ensembl GRCh37 REST;
# the GRCh38 positions are 44908684 / 44908822, which had been hardcoded here while
# mislabeled GRCh37 — #476). APOE genotypes are matched by rsID, so these are
# reference/display constants; they must match the GRCh37 vep_bundle and real
# build-37 23andMe data.
APOE_RS429358_POS = 45411941
APOE_RS7412_POS = 45412079
APOE_CHROM = "19"

# ── Array-genotyping reliability caveat (#557) ──────────────────────────
#
# The ε-defining SNPs rs429358/rs7412 are a documented array weak spot: absent
# from most genome-wide arrays and only imperfectly captured on common platforms
# (Radmanesh 2014; Lill 2012), with array/imputed-vs-direct concordance ~90% for
# the ε genotype and ~93% for ε4 status (Oldmeadow 2014). The same person's two
# vendor files can therefore disagree at rs429358 and flip ε4 status. This is a
# RELIABILITY FLAG ONLY (mirrors backend.analysis.array_confidence): it does NOT
# change the well-established APOE evidence_level — it records that an actionable
# ε-call from array data should be confirmed in a CLIA/accredited lab.
APOE_RELIABILITY_PMIDS = ["24448547", "22972946", "24903779"]
APOE_ARRAY_RELIABILITY_CAVEAT = (
    "APOE ε-status here is derived from consumer genotyping-array calls at "
    "rs429358 and rs7412. These two ε-defining SNPs are a recognised array weak "
    "spot — absent from most genome-wide arrays and only imperfectly captured on "
    "common platforms — and array/imputed APOE agrees only ~90% (ε genotype) / "
    "~93% (ε4 status) with direct clinical genotyping, so the same person's data "
    "from two vendors can disagree and flip ε4 status. This is a reliability "
    "caveat, not a change to the finding's evidence level: confirm an actionable "
    "ε4 (or ε2) call in a CLIA/accredited laboratory before any medical decision, "
    "and treat a single-vendor array ε-status — especially one that conflicts "
    "with another file for the same person — as provisional."
)


def _apoe_array_reliability_flag() -> dict[str, Any]:
    """Structured array-reliability flag attached to APOE findings (#557).

    Reliability flag only — does NOT change evidence_level (cf. array_confidence).
    """
    return {
        "caveat": APOE_ARRAY_RELIABILITY_CAVEAT,
        "confirm_in_clia_recommended": True,
        "concordance_with_direct_genotyping": "~90% ε genotype / ~93% ε4 status",
        "pmids": APOE_RELIABILITY_PMIDS,
    }


class APOEAllele(StrEnum):
    """Individual APOE allele (one per chromosome copy)."""

    E2 = "ε2"
    E3 = "ε3"
    E4 = "ε4"


# ── Haplotype → allele mapping ──────────────────────────────────────────
#
# Each chromosome carries one allele at each SNP position.
# The combination defines the APOE allele on that chromosome:
#
#   rs429358  rs7412   → allele
#   T         T        → ε2
#   T         C        → ε3
#   C         C        → ε4
#   C         T        → (ε1, extremely rare — not called in standard panels)

_HAPLOTYPE_TO_ALLELE: dict[tuple[str, str], APOEAllele] = {
    ("T", "T"): APOEAllele.E2,
    ("T", "C"): APOEAllele.E3,
    ("C", "C"): APOEAllele.E4,
}

# ── Diplotype lookup from unphased genotypes ────────────────────────────
#
# Since array data is unphased, we work with the two-SNP genotype
# combination (sorted allele counts) to determine the diplotype.
#
# rs429358 genotype × rs7412 genotype → diplotype
# (genotype strings are sorted pairs, e.g. "CC", "CT", "TT")

_DIPLOTYPE_TABLE: dict[tuple[str, str], tuple[APOEAllele, APOEAllele]] = {
    # rs429358=TT (both Cys112) × rs7412 options
    ("TT", "TT"): (APOEAllele.E2, APOEAllele.E2),  # ε2/ε2
    ("TT", "CT"): (APOEAllele.E2, APOEAllele.E3),  # ε2/ε3
    ("TT", "CC"): (APOEAllele.E3, APOEAllele.E3),  # ε3/ε3
    # rs429358=CT (one Cys112, one Arg112) × rs7412 options
    ("CT", "CT"): (APOEAllele.E2, APOEAllele.E4),  # ε2/ε4
    ("CT", "CC"): (APOEAllele.E3, APOEAllele.E4),  # ε3/ε4
    # rs429358=CC (both Arg112) × rs7412 options
    ("CC", "CC"): (APOEAllele.E4, APOEAllele.E4),  # ε4/ε4
    # NOTE: CT/TT, CC/CT, CC/TT are biologically impossible without the
    # extremely rare ε1 allele. They are intentionally omitted so that a
    # lookup miss falls through to AMBIGUOUS status.
}


class APOEStatus(StrEnum):
    """APOE determination status."""

    DETERMINED = "determined"
    MISSING_SNPS = "missing_snps"
    NO_CALL = "no_call"
    AMBIGUOUS = "ambiguous"


@dataclass
class APOEResult:
    """Result of APOE genotype determination.

    Attributes:
        status: Whether the diplotype was successfully determined.
        allele1: First APOE allele (lower or equal ε number), or None.
        allele2: Second APOE allele (higher or equal ε number), or None.
        diplotype: Human-readable diplotype string (e.g. "ε3/ε4"), or None.
        rs429358_genotype: Raw genotype at rs429358, or None if missing.
        rs7412_genotype: Raw genotype at rs7412, or None if missing.
        has_e4: Whether at least one ε4 allele is present.
        e4_count: Number of ε4 alleles (0, 1, or 2).
        has_e2: Whether at least one ε2 allele is present.
        e2_count: Number of ε2 alleles (0, 1, or 2).
    """

    status: APOEStatus
    allele1: APOEAllele | None = None
    allele2: APOEAllele | None = None
    diplotype: str | None = None
    rs429358_genotype: str | None = None
    rs7412_genotype: str | None = None

    @property
    def has_e4(self) -> bool:
        """Whether at least one ε4 allele is present."""
        return self.allele1 == APOEAllele.E4 or self.allele2 == APOEAllele.E4

    @property
    def e4_count(self) -> int:
        """Number of ε4 alleles (0, 1, or 2)."""
        count = 0
        if self.allele1 == APOEAllele.E4:
            count += 1
        if self.allele2 == APOEAllele.E4:
            count += 1
        return count

    @property
    def has_e2(self) -> bool:
        """Whether at least one ε2 allele is present."""
        return self.allele1 == APOEAllele.E2 or self.allele2 == APOEAllele.E2

    @property
    def e2_count(self) -> int:
        """Number of ε2 alleles (0, 1, or 2)."""
        count = 0
        if self.allele1 == APOEAllele.E2:
            count += 1
        if self.allele2 == APOEAllele.E2:
            count += 1
        return count

    @property
    def is_determined(self) -> bool:
        """Whether the diplotype was successfully determined."""
        return self.status == APOEStatus.DETERMINED


def _normalise_genotype(genotype: str) -> str:
    """Sort a two-character genotype so the alleles are in alphabetical order.

    23andMe reports genotypes as two-character strings (e.g. "TC" or "CT").
    We normalise to sorted order for consistent lookup.
    """
    if len(genotype) != 2:
        return genotype
    return "".join(sorted(genotype))


def determine_apoe_genotype(sample_engine: sa.Engine) -> APOEResult:
    """Determine the APOE diplotype from raw variant genotypes.

    Looks up rs429358 and rs7412 in the raw_variants table and maps
    the genotype combination to an APOE diplotype (ε2/ε2 through ε4/ε4).

    Args:
        sample_engine: SQLAlchemy engine for the sample database.

    Returns:
        APOEResult with the diplotype determination.
    """
    with sample_engine.connect() as conn:
        stmt = sa.select(raw_variants.c.rsid, raw_variants.c.genotype).where(
            raw_variants.c.rsid.in_([APOE_RS429358, APOE_RS7412])
        )
        rows = {row.rsid: row.genotype for row in conn.execute(stmt)}

    rs429358_gt = rows.get(APOE_RS429358)
    rs7412_gt = rows.get(APOE_RS7412)

    # Check for missing SNPs
    missing = []
    if rs429358_gt is None:
        missing.append(APOE_RS429358)
    if rs7412_gt is None:
        missing.append(APOE_RS7412)

    if missing:
        logger.warning("apoe_snps_missing", missing_rsids=missing)
        return APOEResult(
            status=APOEStatus.MISSING_SNPS,
            rs429358_genotype=rs429358_gt,
            rs7412_genotype=rs7412_gt,
        )

    # Check for no-call genotypes
    if is_no_call(rs429358_gt) or is_no_call(rs7412_gt):
        logger.warning(
            "apoe_no_call",
            rs429358=rs429358_gt,
            rs7412=rs7412_gt,
        )
        return APOEResult(
            status=APOEStatus.NO_CALL,
            rs429358_genotype=rs429358_gt,
            rs7412_genotype=rs7412_gt,
        )

    # Normalise genotypes (sort alleles alphabetically)
    norm_429358 = _normalise_genotype(rs429358_gt)
    norm_7412 = _normalise_genotype(rs7412_gt)

    # Look up diplotype
    allele_pair = _DIPLOTYPE_TABLE.get((norm_429358, norm_7412))

    if allele_pair is None:
        logger.warning(
            "apoe_ambiguous_genotype",
            rs429358=norm_429358,
            rs7412=norm_7412,
        )
        return APOEResult(
            status=APOEStatus.AMBIGUOUS,
            rs429358_genotype=rs429358_gt,
            rs7412_genotype=rs7412_gt,
        )

    # Sort alleles so lower ε number comes first (ε2 < ε3 < ε4)
    allele1, allele2 = sorted(allele_pair, key=lambda a: a.value)
    diplotype = f"{allele1.value}/{allele2.value}"

    logger.info(
        "apoe_genotype_determined",
        diplotype=diplotype,
        rs429358=rs429358_gt,
        rs7412=rs7412_gt,
        has_e4=(allele1 == APOEAllele.E4 or allele2 == APOEAllele.E4),
        e4_count=sum(1 for a in (allele1, allele2) if a == APOEAllele.E4),
    )

    return APOEResult(
        status=APOEStatus.DETERMINED,
        allele1=allele1,
        allele2=allele2,
        diplotype=diplotype,
        rs429358_genotype=rs429358_gt,
        rs7412_genotype=rs7412_gt,
    )


# ── Findings storage ─────────────────────────────────────────────────────


def store_apoe_finding(
    result: APOEResult,
    sample_engine: sa.Engine,
) -> int:
    """Store the APOE genotype finding in the sample database.

    Creates a single finding with module='apoe' and category='genotype'.
    This records the diplotype determination for downstream use by
    P3-22b (three findings generation) and P3-22d (APOE UI).

    Always clears previous APOE genotype findings before inserting,
    ensuring idempotent re-runs.

    Args:
        result: APOEResult from determine_apoe_genotype.
        sample_engine: SQLAlchemy engine for the sample database.

    Returns:
        Number of findings inserted (0 or 1).
    """
    if not result.is_determined:
        logger.info(
            "apoe_finding_skipped",
            status=result.status.value,
            reason="APOE genotype not determined",
        )
        # Still clear any previous findings
        with sample_engine.begin() as conn:
            conn.execute(
                sa.delete(findings).where(
                    findings.c.module == "apoe",
                    findings.c.category == "genotype",
                )
            )
        return 0

    detail = {
        "allele1": result.allele1.value,
        "allele2": result.allele2.value,
        "rs429358_genotype": result.rs429358_genotype,
        "rs7412_genotype": result.rs7412_genotype,
        "has_e4": result.has_e4,
        "e4_count": result.e4_count,
        "has_e2": result.has_e2,
        "e2_count": result.e2_count,
        "array_reliability": _apoe_array_reliability_flag(),
    }

    finding_text = f"APOE genotype: {result.diplotype}"
    if result.has_e4:
        finding_text += f" ({result.e4_count}× ε4 allele)"

    row = {
        "module": "apoe",
        "category": "genotype",
        "evidence_level": 4,  # ★★★★ — both SNPs well-characterised
        "gene_symbol": "APOE",
        "rsid": None,  # composite of two rsids
        "finding_text": finding_text,
        "conditions": None,  # findings generation (P3-22b) assigns conditions
        "zygosity": None,
        "clinvar_significance": None,
        "diplotype": result.diplotype,
        "pmid_citations": None,
        "detail_json": json.dumps(detail),
    }

    with sample_engine.begin() as conn:
        # Clear previous APOE genotype finding
        conn.execute(
            sa.delete(findings).where(
                findings.c.module == "apoe",
                findings.c.category == "genotype",
            )
        )
        conn.execute(sa.insert(findings), [row])

    logger.info(
        "apoe_finding_stored",
        diplotype=result.diplotype,
        has_e4=result.has_e4,
        e4_count=result.e4_count,
    )
    return 1


# ── APOE three findings generation (P3-22b) ─────────────────────────────
#
# Three findings per diplotype:
#   1. Cardiovascular risk   (★★★★) — Type III HLP, LDL metabolism, statin response
#   2. Alzheimer's risk      (★★★★) — Relative risk by diplotype, prominently caveated
#   3. Lipid/dietary context (★★★☆) — Saturated fat response differential

APOE_FINDING_CV = "cardiovascular_risk"
APOE_FINDING_ALZHEIMERS = "alzheimers_risk"
APOE_FINDING_LIPID = "lipid_dietary"

# All three APOE finding categories
APOE_FINDING_CATEGORIES = (APOE_FINDING_CV, APOE_FINDING_ALZHEIMERS, APOE_FINDING_LIPID)


@dataclass
class APOEFinding:
    """A single APOE-derived finding."""

    category: str
    evidence_level: int
    finding_text: str
    conditions: str
    phenotype: str
    pmid_citations: list[str]
    detail_json: dict[str, Any] = field(default_factory=dict)


# ── Per-diplotype cardiovascular risk content ────────────────────────────

_CV_RISK: dict[str, dict[str, Any]] = {
    "ε2/ε2": {
        "finding_text": (
            "APOE ε2/ε2 is associated with Type III hyperlipoproteinemia "
            "(familial dysbetalipoproteinemia), characterised by elevated "
            "remnant lipoproteins. LDL cholesterol may appear paradoxically "
            "low due to impaired hepatic uptake of VLDL remnants. "
            "Statin response is generally preserved."
        ),
        "risk_level": "elevated",
        "conditions": "Type III hyperlipoproteinemia; LDL metabolism; statin response",
        "phenotype": "Type III hyperlipoproteinemia risk (elevated)",
    },
    "ε2/ε3": {
        "finding_text": (
            "APOE ε2/ε3 is associated with modestly lower LDL cholesterol "
            "relative to the ε3/ε3 reference. The ε2 allele reduces hepatic "
            "LDL receptor binding efficiency but rarely causes clinical "
            "dyslipidaemia in heterozygous form. Statin response is typical."
        ),
        "risk_level": "slightly_reduced",
        "conditions": "LDL metabolism; statin response",
        "phenotype": "Cardiovascular risk (slightly reduced vs reference)",
    },
    "ε2/ε4": {
        "finding_text": (
            "APOE ε2/ε4 carries one copy each of the ε2 and ε4 alleles, "
            "which have opposing effects on LDL metabolism. Net cardiovascular "
            "risk is approximately similar to ε3/ε3. LDL cholesterol levels "
            "are variable. Statin response is typical."
        ),
        "risk_level": "average",
        "conditions": "LDL metabolism; statin response",
        "phenotype": "Cardiovascular risk (approximately average)",
    },
    "ε3/ε3": {
        "finding_text": (
            "APOE ε3/ε3 is the most common genotype (population frequency "
            "~60%). This is the reference genotype for APOE-related "
            "cardiovascular risk. LDL metabolism and statin response "
            "are typical for the general population."
        ),
        "risk_level": "reference",
        "conditions": "LDL metabolism; statin response",
        "phenotype": "Cardiovascular risk (population reference)",
    },
    "ε3/ε4": {
        "finding_text": (
            "APOE ε3/ε4 is associated with modestly higher LDL cholesterol "
            "relative to the ε3/ε3 reference. The ε4 allele increases "
            "hepatic LDL receptor binding, leading to higher circulating LDL. "
            "Statin response is generally good, with some evidence of "
            "enhanced LDL reduction."
        ),
        "risk_level": "modestly_elevated",
        "conditions": "LDL metabolism; statin response",
        "phenotype": "Cardiovascular risk (modestly elevated)",
    },
    "ε4/ε4": {
        "finding_text": (
            "APOE ε4/ε4 is associated with higher LDL cholesterol and "
            "elevated cardiovascular risk relative to the ε3/ε3 reference. "
            "LDL levels are typically 10–30% higher than non-carriers. "
            "Statin response is generally good, with some evidence of "
            "enhanced LDL reduction."
        ),
        "risk_level": "elevated",
        "conditions": "LDL metabolism; statin response",
        "phenotype": "Cardiovascular risk (elevated)",
    },
}

# ── Per-diplotype Alzheimer's risk content ───────────────────────────────
#
# Relative risk estimates from Genin et al. 2011 (PMID: 21460841) and
# Farrer et al. 1997 (PMID: 9343467). These are approximate population-
# level odds ratios vs ε3/ε3 reference. Belloy et al. 2023 (PMID: 37930705)
# shows APOE-AD effect sizes differ across age, sex, race/ethnicity, and
# ancestry strata; Lumsden et al. 2020 (PMID: 32818802) is a White British UK
# Biobank PheWAS, so reused numeric estimates must preserve population context.

_ALZHEIMERS_RISK_CONTEXT = (
    "These numeric estimates are population-aggregate approximations; APOE "
    "effect sizes vary by age, sex, race/ethnicity, and genetic ancestry, so "
    "they are not calibrated to this individual's background."
)

_ALZHEIMERS_RISK: dict[str, dict[str, Any]] = {
    "ε2/ε2": {
        "finding_text": (
            "APOE ε2/ε2 is associated with substantially reduced risk of "
            "late-onset Alzheimer's disease relative to ε3/ε3 "
            "(approximate OR 0.6). This genotype is rare (~1% of the population). "
            "This is a probabilistic association, not a diagnosis. Most "
            "Alzheimer's cases occur in people without ε4 alleles, and many "
            "protective-genotype carriers still develop the disease."
        ),
        "relative_risk": "substantially_reduced",
        "approximate_or": 0.6,
        "phenotype": "Alzheimer's disease risk (substantially reduced vs reference)",
    },
    "ε2/ε3": {
        "finding_text": (
            "APOE ε2/ε3 is associated with reduced risk of late-onset "
            "Alzheimer's disease relative to ε3/ε3 (approximate OR 0.6). "
            "The ε2 allele appears to be protective. This is a probabilistic "
            "association, not a diagnosis. Environmental factors, other "
            "genetic variants, and lifestyle contribute substantially."
        ),
        "relative_risk": "reduced",
        "approximate_or": 0.6,
        "phenotype": "Alzheimer's disease risk (reduced vs reference)",
    },
    "ε2/ε4": {
        "finding_text": (
            "APOE ε2/ε4 carries one protective (ε2) and one risk-elevating "
            "(ε4) allele. The net effect on Alzheimer's risk is "
            "approximately 2.6× that of ε3/ε3 — the ε4 allele dominates "
            "the risk profile. This is a probabilistic association, not a "
            "diagnosis. Many ε4 carriers never develop Alzheimer's disease."
        ),
        "relative_risk": "elevated",
        "approximate_or": 2.6,
        "phenotype": "Alzheimer's disease risk (elevated vs reference)",
    },
    "ε3/ε3": {
        "finding_text": (
            "APOE ε3/ε3 is the most common genotype and serves as the "
            "population reference for Alzheimer's risk assessment. "
            "Lifetime risk of Alzheimer's disease for ε3/ε3 carriers is "
            "approximately 10–15% by age 85. Other genetic and "
            "environmental factors contribute substantially to individual risk."
        ),
        "relative_risk": "reference",
        "approximate_or": 1.0,
        "phenotype": "Alzheimer's disease risk (population reference)",
    },
    "ε3/ε4": {
        "finding_text": (
            "APOE ε3/ε4 is associated with approximately 3.2× the risk of "
            "late-onset Alzheimer's disease relative to ε3/ε3. "
            "Approximately 25% of the general population carries one ε4 "
            "allele. This is a probabilistic risk factor — most ε3/ε4 "
            "carriers do not develop Alzheimer's disease. No approved "
            "prevention currently exists, and clinical utility of this "
            "information is limited."
        ),
        "relative_risk": "elevated",
        "approximate_or": 3.2,
        "phenotype": "Alzheimer's disease risk (elevated vs reference)",
    },
    "ε4/ε4": {
        "finding_text": (
            "APOE ε4/ε4 is associated with approximately 8–12× the risk of "
            "late-onset Alzheimer's disease relative to ε3/ε3. "
            "Approximately 2–3% of the general population is ε4 homozygous. "
            "Despite this elevated relative risk, the absolute lifetime risk "
            "is still probabilistic — not all ε4/ε4 carriers develop "
            "Alzheimer's disease. This is not a diagnosis. No approved "
            "prevention currently exists. Genetic counselling is recommended "
            "for individuals who wish to discuss the implications of this result."
        ),
        "relative_risk": "substantially_elevated",
        "approximate_or": 11.6,
        "phenotype": "Alzheimer's disease risk (substantially elevated vs reference)",
    },
}

# ── Per-diplotype lipid/dietary context content ──────────────────────────

_LIPID_DIETARY: dict[str, dict[str, Any]] = {
    "ε2/ε2": {
        "finding_text": (
            "APOE ε2/ε2 carriers may show an atypical lipid response to "
            "dietary saturated fat. The impaired remnant clearance associated "
            "with ε2 homozygosity means standard dietary cholesterol "
            "guidelines may not apply. A lipid panel is recommended to "
            "assess individual response."
        ),
        "dietary_response": "atypical",
        "phenotype": "Dietary fat response (atypical — remnant clearance impaired)",
    },
    "ε2/ε3": {
        "finding_text": (
            "APOE ε2/ε3 carriers tend to show a slightly reduced LDL "
            "response to dietary saturated fat compared to ε3/ε3 carriers. "
            "Standard dietary recommendations generally apply. Lipid panel "
            "monitoring is recommended for personalised guidance."
        ),
        "dietary_response": "slightly_reduced",
        "phenotype": "Dietary fat response (slightly reduced LDL sensitivity)",
    },
    "ε2/ε4": {
        "finding_text": (
            "APOE ε2/ε4 carriers have opposing allele effects on dietary "
            "fat response. Net LDL response to saturated fat intake is "
            "variable and difficult to predict from genotype alone. "
            "Lipid panel monitoring is recommended for personalised guidance."
        ),
        "dietary_response": "variable",
        "phenotype": "Dietary fat response (variable — opposing allele effects)",
    },
    "ε3/ε3": {
        "finding_text": (
            "APOE ε3/ε3 carriers have a typical LDL response to dietary "
            "saturated fat intake. This is the reference genotype — standard "
            "dietary recommendations for saturated fat reduction are expected "
            "to produce the typical population-level LDL response."
        ),
        "dietary_response": "typical",
        "phenotype": "Dietary fat response (typical — population reference)",
    },
    "ε3/ε4": {
        "finding_text": (
            "APOE ε3/ε4 carriers tend to show a greater LDL increase in "
            "response to dietary saturated fat compared to ε3/ε3 carriers. "
            "Dietary saturated fat reduction may produce a larger-than-average "
            "LDL lowering effect. Lipid panel monitoring is recommended."
        ),
        "dietary_response": "enhanced",
        "phenotype": "Dietary fat response (enhanced LDL sensitivity)",
    },
    "ε4/ε4": {
        "finding_text": (
            "APOE ε4/ε4 carriers tend to show the greatest LDL increase in "
            "response to dietary saturated fat among all APOE genotypes. "
            "Dietary saturated fat reduction may produce a larger-than-average "
            "LDL lowering effect. Lipid panel monitoring is recommended."
        ),
        "dietary_response": "markedly_enhanced",
        "phenotype": "Dietary fat response (markedly enhanced LDL sensitivity)",
    },
}

# PubMed citations shared across findings
_CV_PMIDS = ["21460841", "9343467", "17309940", "28577312"]
_ALZHEIMERS_PMIDS = ["21460841", "9343467", "24162737", "23571587", "37930705", "32818802"]
_LIPID_DIETARY_PMIDS = ["9343467", "17309940", "26109578", "24820091"]


def generate_apoe_findings(result: APOEResult) -> list[APOEFinding]:
    """Generate the three APOE findings from a determined genotype.

    Produces findings for:
      1. Cardiovascular risk   (★★★★)
      2. Alzheimer's risk      (★★★★)
      3. Lipid/dietary context (★★★☆)

    Args:
        result: A determined APOEResult (is_determined must be True).

    Returns:
        List of three APOEFinding objects, or empty list if not determined.
    """
    if not result.is_determined:
        return []

    diplotype = result.diplotype
    generated: list[APOEFinding] = []

    if diplotype not in _CV_RISK:
        raise ValueError(f"Unknown APOE diplotype: {diplotype}")

    # 1. Cardiovascular risk (★★★★)
    cv_data = _CV_RISK[diplotype]
    generated.append(
        APOEFinding(
            category=APOE_FINDING_CV,
            evidence_level=4,
            finding_text=cv_data["finding_text"],
            conditions=cv_data["conditions"],
            phenotype=cv_data["phenotype"],
            pmid_citations=_CV_PMIDS,
            detail_json={
                "diplotype": diplotype,
                "risk_level": cv_data["risk_level"],
                "scope": "Type III hyperlipoproteinemia, LDL metabolism, statin response",
                "array_reliability": _apoe_array_reliability_flag(),
            },
        )
    )

    # 2. Alzheimer's risk (★★★★)
    alz_data = _ALZHEIMERS_RISK[diplotype]
    generated.append(
        APOEFinding(
            category=APOE_FINDING_ALZHEIMERS,
            evidence_level=4,
            finding_text=f"{alz_data['finding_text']} {_ALZHEIMERS_RISK_CONTEXT}",
            conditions="Alzheimer's disease",
            phenotype=alz_data["phenotype"],
            pmid_citations=_ALZHEIMERS_PMIDS,
            detail_json={
                "diplotype": diplotype,
                "relative_risk": alz_data["relative_risk"],
                "approximate_or": alz_data["approximate_or"],
                "non_actionable": True,
                "caveats": (
                    "This is a probabilistic risk factor, not a diagnosis. "
                    "Clinical utility is limited. No approved prevention exists. "
                    f"{_ALZHEIMERS_RISK_CONTEXT} {APOE_ARRAY_RELIABILITY_CAVEAT}"
                ),
                "risk_estimate_context": _ALZHEIMERS_RISK_CONTEXT,
                "array_reliability": _apoe_array_reliability_flag(),
            },
        )
    )

    # 3. Lipid/dietary context (★★★☆)
    lipid_data = _LIPID_DIETARY[diplotype]
    generated.append(
        APOEFinding(
            category=APOE_FINDING_LIPID,
            evidence_level=3,
            finding_text=lipid_data["finding_text"],
            conditions="Saturated fat response differential",
            phenotype=lipid_data["phenotype"],
            pmid_citations=_LIPID_DIETARY_PMIDS,
            detail_json={
                "diplotype": diplotype,
                "dietary_response": lipid_data["dietary_response"],
                "scope": "Saturated fat response differential",
                "array_reliability": _apoe_array_reliability_flag(),
            },
        )
    )

    return generated


def store_apoe_three_findings(
    result: APOEResult,
    sample_engine: sa.Engine,
) -> int:
    """Generate and store the three APOE findings in the sample database.

    Creates three findings with module='apoe' and categories:
      - cardiovascular_risk
      - alzheimers_risk
      - lipid_dietary

    Always clears previous APOE analysis findings before inserting,
    ensuring idempotent re-runs. Does NOT touch the genotype finding.

    Args:
        result: APOEResult from determine_apoe_genotype.
        sample_engine: SQLAlchemy engine for the sample database.

    Returns:
        Number of findings inserted (0 or 3).
    """
    if not result.is_determined:
        logger.info(
            "apoe_three_findings_skipped",
            status=result.status.value,
            reason="APOE genotype not determined",
        )
        # Clear previous findings even when not determined
        with sample_engine.begin() as conn:
            conn.execute(
                sa.delete(findings).where(
                    findings.c.module == "apoe",
                    findings.c.category.in_(APOE_FINDING_CATEGORIES),
                )
            )
        return 0

    apoe_findings = generate_apoe_findings(result)

    rows = [
        {
            "module": "apoe",
            "category": f.category,
            "evidence_level": f.evidence_level,
            "gene_symbol": "APOE",
            "rsid": None,
            "finding_text": f.finding_text,
            "phenotype": f.phenotype,
            "conditions": f.conditions,
            "diplotype": result.diplotype,
            "pmid_citations": json.dumps(f.pmid_citations),
            "detail_json": json.dumps(f.detail_json),
        }
        for f in apoe_findings
    ]

    with sample_engine.begin() as conn:
        # Atomic: clear previous + insert new in single transaction
        conn.execute(
            sa.delete(findings).where(
                findings.c.module == "apoe",
                findings.c.category.in_(APOE_FINDING_CATEGORIES),
            )
        )
        conn.execute(sa.insert(findings), rows)

    logger.info(
        "apoe_three_findings_stored",
        diplotype=result.diplotype,
        count=len(rows),
        categories=[f.category for f in apoe_findings],
    )
    return len(rows)

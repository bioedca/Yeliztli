"""Pharmacogenomics star-allele calling via CPIC lookup tables.

Implements P3-02, P3-03, and P3-04:
  - P3-02: pure SQLite joins — rsid genotype → star allele component →
    diplotype inference → phenotype lookup.
  - P3-03: Three-state calling confidence (Complete/Partial/Insufficient).
  - P3-04: Prescribing alert generation — drug name, gene, phenotype,
    action, CPIC level, call confidence state → findings records with
    ``module='pharmacogenomics'``.

Supported genes: CYP2D6, CYP2C19, CYP2C9, CYP3A5, SLCO1B1, DPYD, TPMT, UGT1A1.

Three-state calling model (P3-03):
    Complete   ✅ — All defining rsids present and genotyped, no structural
                    variant ambiguity.
    Partial    ⚠️ — SNP-based alleles called, but structural variants
                    (copy number, gene conversion) cannot be excluded from
                    array data. Phenotype shown as provisional.
    Insufficient ❌ — Key defining rsids not on the 23andMe array.

Algorithm:
    1. For each CPIC gene, load allele definitions from reference.db
    2. Fetch the sample's raw genotypes for all defining rsids
    3. Count alt alleles per rsid from the sample genotype string
    4. Greedily assign star alleles (most specific first: alleles with the
       most defining variants take priority — handles phasing ambiguity
       per CPIC unphased-data guidelines)
    5. Look up the resulting diplotype in cpic_diplotypes → phenotype
    6. Assign call confidence (Complete/Partial/Insufficient)
    7. Match phenotype against cpic_guidelines → prescribing alerts (P3-04)

Usage::

    from backend.analysis.pharmacogenomics import (
        call_all_star_alleles,
        generate_prescribing_alerts,
    )

    results = call_all_star_alleles(reference_engine, sample_engine)
    for r in results:
        print(f"{r.gene}: {r.diplotype} → {r.phenotype} ({r.call_confidence})")

    alerts = generate_prescribing_alerts(results, reference_engine)
    # alerts is a list of PrescribingAlert dataclasses
    # Call store_prescribing_alerts() to persist them as findings records
"""

from __future__ import annotations

import enum
import json
import re
from dataclasses import dataclass, field

import sqlalchemy as sa
import structlog

from backend.analysis.evidence import assign_cpic_evidence_level
from backend.analysis.zygosity import is_no_call
from backend.annotation.cpic import CPIC_GENES
from backend.annotation.engine import CPIC_BIT
from backend.db.tables import (
    annotated_variants,
    cpic_alleles,
    cpic_diplotypes,
    cpic_guidelines,
    findings,
    raw_variants,
)
from backend.disclaimers import CYP2D6_CNV_CAVEAT, DPYD_FLUOROPYRIMIDINE_CAVEAT

logger = structlog.get_logger(__name__)

_STAR_ALLELE_RE = re.compile(r"^\*?(\d+)(.*)")
_TRUE_NO_CALLS: frozenset[str] = frozenset({"", "--", "??", "-", "0", "00"})

# Genes with known structural variant complexity (copy number variation,
# gene conversion, hybrid alleles) that array genotyping cannot resolve.
# These always receive "Partial" confidence at best.
STRUCTURAL_VARIANT_GENES: frozenset[str] = frozenset({"CYP2D6", "CYP2B6"})

# Gene-specific interpretive caveats attached to prescribing-alert findings
# (detail_json["gene_caveat"]) and surfaced by the pharma route. Context only —
# they never change metabolizer_status or evidence_level.
#   DPYD (SW-E5): absent-allele / fatal-toxicity caveat — only 4 variants typed, a
#     normal result does not exclude DPD deficiency (severe/fatal fluoropyrimidine
#     toxicity).
#   CYP2D6 (SW-E3): structural-variant / copy-number caveat — array data cannot
#     assess duplications, the *5 deletion, or CYP2D7 hybrids, so the activity
#     score is an assayed estimate that may be higher (duplication → UM) or lower
#     (*5 deletion → PM). Pairs with the "Partial" confidence from
#     STRUCTURAL_VARIANT_GENES.
_GENE_INTERPRETATION_CAVEATS: dict[str, str] = {
    "DPYD": DPYD_FLUOROPYRIMIDINE_CAVEAT,
    "CYP2D6": CYP2D6_CNV_CAVEAT,
}


class CallConfidence(enum.Enum):
    """Three-state calling confidence for pharmacogenomics (P3-03).

    Complete:     All defining rsids present and genotyped; no structural
                  variant ambiguity. Safe to report as definitive.
    Partial:      SNP-based alleles called, but structural variants (copy
                  number, gene conversion) cannot be excluded from array
                  data. Phenotype shown as provisional.
    Insufficient: Key defining rsids not on the array or could not be
                  genotyped. Call is unreliable.
    """

    COMPLETE = "Complete"
    PARTIAL = "Partial"
    INSUFFICIENT = "Insufficient"


def _allele_sort_key(name: str) -> tuple[int, str]:
    """Sort key for star allele names: numeric part first, then suffix.

    Examples: *1 < *1A < *2 < *3A < *3B < *3C < *10 < *15
    Non-star alleles (e.g. "c.2846A>T") sort after all star alleles.
    """
    m = _STAR_ALLELE_RE.match(name)
    if m:
        return (int(m.group(1)), m.group(2))
    return (999999, name)


@dataclass
class StarAlleleResult:
    """Result of star-allele calling for a single gene."""

    gene: str
    allele1: str
    allele2: str
    diplotype: str
    phenotype: str | None = None
    ehr_notation: str | None = None
    activity_score: float | None = None
    involved_rsids: set[str] = field(default_factory=set)
    missing_rsids: set[str] = field(default_factory=set)
    uncalled_rsids: set[str] = field(default_factory=set)
    defining_rsid_count: int = 0
    call_confidence: CallConfidence = CallConfidence.COMPLETE
    confidence_note: str = ""
    # Non-reference alleles that could NOT be excluded because a defining variant
    # was not assayed/callable on the array (the reference *1 fill is an assumption,
    # not an observation). SW-E1.
    indeterminate_alleles: list[str] = field(default_factory=list)

    @property
    def coverage_assessed(self) -> int:
        """Number of the gene's defining SNP positions actually assayed and called.

        ``defining_rsid_count`` minus the positions that were missing from the
        array or could not be genotyped. This is *SNP defining-position* coverage
        only — it does not (and from array data cannot) account for copy-number or
        gene-conversion alleles, which the reference-bias disclosure covers
        separately.
        """
        unusable = self.missing_rsids | self.uncalled_rsids
        return max(0, self.defining_rsid_count - len(unusable))


def _indel_alt_token(ref: str, alt: str) -> str | None:
    """Return the D/I token for a simple indel whose ALT defines the star allele."""
    ref = ref.upper()
    alt = alt.upper()
    if not ref or not alt or len(ref) == len(alt):
        return None
    if len(ref) > len(alt):
        return "D" if ref.startswith(alt) or ref.endswith(alt) else None
    return "I" if alt.startswith(ref) or alt.endswith(ref) else None


def _count_indel_alt_alleles(genotype: str | None, ref: str, alt: str) -> int | None:
    """Count copies of a declared simple indel ALT from I/D raw genotype tokens."""
    if genotype is None:
        return None
    gt = genotype.strip().upper()
    if gt in _TRUE_NO_CALLS:
        return None

    alt_token = _indel_alt_token(ref, alt)
    if alt_token is None:
        return None
    ref_token = "I" if alt_token == "D" else "D"

    if len(gt) == 1:
        return None
    if len(gt) == 2:
        alleles = list(gt)
    else:
        return None

    if any(a not in {ref_token, alt_token} for a in alleles):
        return None
    return min(sum(1 for a in alleles if a == alt_token), 2)


def _count_alt_alleles(genotype: str, ref: str, alt: str) -> int | None:
    """Count how many copies of the alt allele are in a genotype string.

    Args:
        genotype: Two-character genotype from 23andMe (e.g. "CT", "CC").
        ref: Reference allele (single base for SNPs).
        alt: Alternate allele (single base for SNPs).

    Returns:
        Number of alt alleles (0, 1, or 2), or None if the genotype
        cannot be interpreted (no-call, unsupported indel, unexpected bases).
    """
    if len(ref) > 1 or len(alt) > 1:
        return _count_indel_alt_alleles(genotype, ref, alt)

    if is_no_call(genotype):
        return None
    if len(genotype) < 2:
        return None

    g1, g2 = genotype[0], genotype[1]
    count = 0
    if g1 == alt:
        count += 1
    if g2 == alt:
        count += 1

    # Validate that the alleles are ref or alt (not some third allele)
    valid_bases = {ref, alt}
    if g1 not in valid_bases or g2 not in valid_bases:
        return None

    return count


_SQLITE_BATCH = 500  # Stay well under SQLITE_MAX_VARIABLE_NUMBER (999)


def _fetch_sample_genotypes(
    rsids: list[str],
    sample_engine: sa.Engine,
) -> dict[str, str]:
    """Fetch raw genotypes for a list of rsids from the sample database.

    Batches the IN clause to stay under SQLite's variable limit.

    Args:
        rsids: List of rsid strings to look up.
        sample_engine: SQLAlchemy engine for the sample database.

    Returns:
        Dict mapping rsid → genotype string (e.g. "CT").
    """
    if not rsids:
        return {}

    results: dict[str, str] = {}

    with sample_engine.connect() as conn:
        for i in range(0, len(rsids), _SQLITE_BATCH):
            batch = rsids[i : i + _SQLITE_BATCH]
            stmt = sa.select(
                raw_variants.c.rsid,
                raw_variants.c.genotype,
            ).where(raw_variants.c.rsid.in_(batch))

            for row in conn.execute(stmt).fetchall():
                results[row.rsid] = row.genotype

    return results


def _fetch_alleles_for_gene(
    gene: str,
    reference_engine: sa.Engine,
) -> list[dict]:
    """Fetch all CPIC allele definitions for a gene.

    Returns list of dicts with keys: allele_name, defining_variants (parsed),
    function, activity_score.
    """
    with reference_engine.connect() as conn:
        stmt = (
            sa.select(
                cpic_alleles.c.allele_name,
                cpic_alleles.c.defining_variants,
                cpic_alleles.c.function,
                cpic_alleles.c.activity_score,
            )
            .where(cpic_alleles.c.gene == gene)
            .order_by(cpic_alleles.c.allele_name)
        )
        rows = conn.execute(stmt).fetchall()

    results = []
    for row in rows:
        try:
            variants = json.loads(row.defining_variants) if row.defining_variants else []
        except json.JSONDecodeError:
            variants = []

        results.append(
            {
                "allele_name": row.allele_name,
                "defining_variants": variants,
                "function": row.function,
                "activity_score": row.activity_score,
            }
        )
    return results


def _fetch_diplotype_phenotype(
    gene: str,
    diplotype: str,
    reference_engine: sa.Engine,
) -> dict | None:
    """Look up a diplotype→phenotype mapping from cpic_diplotypes.

    Args:
        gene: Gene symbol.
        diplotype: Diplotype string (e.g. "*1/*4").
        reference_engine: SQLAlchemy engine for reference.db.

    Returns:
        Dict with phenotype, ehr_notation, activity_score or None if not found.
    """
    with reference_engine.connect() as conn:
        stmt = (
            sa.select(
                cpic_diplotypes.c.phenotype,
                cpic_diplotypes.c.ehr_notation,
                cpic_diplotypes.c.activity_score,
            )
            .where(
                sa.and_(
                    cpic_diplotypes.c.gene == gene,
                    cpic_diplotypes.c.diplotype == diplotype,
                )
            )
            .limit(1)
        )
        row = conn.execute(stmt).first()

    if row is None:
        return None

    return {
        "phenotype": row.phenotype,
        "ehr_notation": row.ehr_notation,
        "activity_score": row.activity_score,
    }


def _assess_call_confidence(
    gene: str,
    all_defining_rsids: set[str],
    missing_rsids: set[str],
    uncalled_rsids: set[str],
) -> tuple[CallConfidence, str]:
    """Determine three-state calling confidence for a gene (P3-03).

    Args:
        gene: Gene symbol.
        all_defining_rsids: All rsids that define non-reference alleles.
        missing_rsids: Rsids not present in the sample at all.
        uncalled_rsids: Rsids present but with invalid/no-call genotypes.

    Returns:
        Tuple of (CallConfidence, human-readable note).
    """
    unusable = missing_rsids | uncalled_rsids
    total = len(all_defining_rsids)

    # No defining rsids means reference-only gene — trivially complete
    if total == 0:
        if gene in STRUCTURAL_VARIANT_GENES:
            return (
                CallConfidence.PARTIAL,
                f"{gene} has structural variant complexity (copy number "
                "variation, gene conversion) that cannot be resolved from "
                "array data. Phenotype is provisional.",
            )
        return (CallConfidence.COMPLETE, "All defining positions assessed.")

    unusable_fraction = len(unusable) / total

    # Insufficient: >50% of defining rsids missing/uncalled
    if unusable_fraction > 0.5:
        missing_list = ", ".join(sorted(unusable)[:5])
        suffix = f" (and {len(unusable) - 5} more)" if len(unusable) > 5 else ""
        return (
            CallConfidence.INSUFFICIENT,
            f"{len(unusable)}/{total} defining positions for {gene} are "
            f"missing or uncalled: {missing_list}{suffix}. "
            "Star-allele call is unreliable.",
        )

    # Partial: structural variant genes always partial (even if all SNPs ok)
    if gene in STRUCTURAL_VARIANT_GENES:
        return (
            CallConfidence.PARTIAL,
            f"{gene} has structural variant complexity (copy number "
            "variation, gene conversion) that cannot be resolved from "
            "array data. Phenotype is provisional.",
        )

    # Partial: some (≤50%) defining rsids missing/uncalled
    if unusable:
        missing_list = ", ".join(sorted(unusable))
        return (
            CallConfidence.PARTIAL,
            f"{len(unusable)}/{total} defining positions for {gene} are "
            f"missing or uncalled ({missing_list}). Call may be incomplete.",
        )

    # Complete: all defining rsids present and genotyped
    return (CallConfidence.COMPLETE, "All defining positions assessed.")


def call_star_alleles_for_gene(
    gene: str,
    alleles: list[dict],
    sample_genotypes: dict[str, str],
    reference_engine: sa.Engine,
) -> StarAlleleResult:
    """Call star alleles for a single gene given allele definitions and genotypes.

    Uses a greedy algorithm: alleles with the most defining variants are
    prioritized (most specific first). This handles phasing ambiguity for
    unphased array data per CPIC recommendations.

    Args:
        gene: Gene symbol (e.g. "CYP2D6").
        alleles: List of allele dicts from _fetch_alleles_for_gene.
        sample_genotypes: Dict of rsid → genotype string from sample.
        reference_engine: SQLAlchemy engine for diplotype lookup.

    Returns:
        StarAlleleResult with called diplotype and phenotype.
    """
    # Separate reference allele (no defining variants) from non-reference
    ref_allele_name: str | None = None
    non_ref_alleles: list[dict] = []

    for allele in alleles:
        if not allele["defining_variants"]:
            if ref_allele_name is None:
                ref_allele_name = allele["allele_name"]
        else:
            non_ref_alleles.append(allele)

    # Default reference allele name
    if ref_allele_name is None:
        ref_allele_name = "*1"

    # Collect all defining rsids for this gene
    all_defining_rsids: set[str] = set()
    for allele in non_ref_alleles:
        for v in allele["defining_variants"]:
            all_defining_rsids.add(v["rsid"])

    # Track missing rsids (not genotyped in sample)
    missing_rsids = all_defining_rsids - set(sample_genotypes.keys())

    # Track remaining alt copies per rsid (from sample genotypes)
    remaining_alts: dict[str, int] = {}
    uncalled_rsids: set[str] = set()

    for allele in non_ref_alleles:
        for v in allele["defining_variants"]:
            rsid = v["rsid"]
            if rsid in remaining_alts or rsid in uncalled_rsids:
                continue
            if rsid not in sample_genotypes:
                continue
            alt_count = _count_alt_alleles(sample_genotypes[rsid], v["ref"], v["alt"])
            if alt_count is None:
                uncalled_rsids.add(rsid)
            else:
                remaining_alts[rsid] = alt_count

    # Sort non-ref alleles: most defining variants first (most specific),
    # then alphabetically for deterministic results
    non_ref_alleles.sort(key=lambda a: (-len(a["defining_variants"]), a["allele_name"]))

    # Greedily assign alleles
    called_alleles: list[str] = []
    involved_rsids: set[str] = set()

    for allele in non_ref_alleles:
        slots_left = 2 - len(called_alleles)
        if slots_left <= 0:
            break

        variants = allele["defining_variants"]
        max_copies = slots_left

        for v in variants:
            rsid = v["rsid"]
            if rsid not in remaining_alts:
                max_copies = 0
                break
            max_copies = min(max_copies, remaining_alts[rsid])

        if max_copies > 0:
            # Consume alt copies
            for v in variants:
                remaining_alts[v["rsid"]] -= max_copies
                involved_rsids.add(v["rsid"])

            called_alleles.extend([allele["allele_name"]] * max_copies)

    # Fill remaining slots with reference allele
    while len(called_alleles) < 2:
        called_alleles.append(ref_allele_name)

    # Sort for canonical diplotype string (e.g. "*1/*4" not "*4/*1")
    # Use CPIC-aware sorting: numeric part first, then suffix
    called_alleles = sorted(called_alleles[:2], key=_allele_sort_key)
    allele1, allele2 = called_alleles

    diplotype = f"{allele1}/{allele2}"

    # Look up diplotype → phenotype
    diplo_data = _fetch_diplotype_phenotype(gene, diplotype, reference_engine)

    # Assess three-state call confidence (P3-03)
    call_confidence, confidence_note = _assess_call_confidence(
        gene, all_defining_rsids, missing_rsids, uncalled_rsids
    )

    # Explicit indeterminate flag (SW-E1): a non-reference allele whose defining
    # variant(s) were not assayed/callable cannot be excluded — the reference fill
    # is an assumption, not an observation (e.g. the UGT1A1*28 TA-repeat, which a
    # SNP array cannot type). Surface these so a "Normal"/reference call is not
    # mistaken for confident exclusion of every star allele.
    unusable_rsids = missing_rsids | uncalled_rsids
    indeterminate_alleles = sorted(
        a["allele_name"]
        for a in non_ref_alleles
        if a["allele_name"] not in called_alleles
        and any(v["rsid"] in unusable_rsids for v in a["defining_variants"])
    )
    if indeterminate_alleles:
        confidence_note = (
            f"{confidence_note} Cannot exclude {', '.join(indeterminate_alleles)} — "
            "defining variant(s) not assayed on this array."
        ).strip()

    return StarAlleleResult(
        gene=gene,
        allele1=allele1,
        allele2=allele2,
        diplotype=diplotype,
        phenotype=diplo_data["phenotype"] if diplo_data else None,
        ehr_notation=diplo_data["ehr_notation"] if diplo_data else None,
        activity_score=diplo_data["activity_score"] if diplo_data else None,
        involved_rsids=involved_rsids,
        missing_rsids=missing_rsids,
        uncalled_rsids=uncalled_rsids,
        defining_rsid_count=len(all_defining_rsids),
        call_confidence=call_confidence,
        confidence_note=confidence_note,
        indeterminate_alleles=indeterminate_alleles,
    )


def call_all_star_alleles(
    reference_engine: sa.Engine,
    sample_engine: sa.Engine,
    *,
    genes: frozenset[str] | None = None,
) -> list[StarAlleleResult]:
    """Call star alleles for all CPIC genes given a sample.

    This is the main entry point for pharmacogenomics star-allele calling.
    For each supported CPIC gene:
      1. Loads allele definitions from reference.db
      2. Fetches sample genotypes for relevant rsids
      3. Calls star alleles via greedy matching
      4. Looks up diplotype → phenotype

    Args:
        reference_engine: SQLAlchemy engine for reference.db.
        sample_engine: SQLAlchemy engine for the sample database.
        genes: Optional subset of genes to call. Defaults to all CPIC_GENES.

    Returns:
        List of StarAlleleResult, one per gene (sorted by gene name).
    """
    target_genes = sorted(genes or CPIC_GENES)
    results: list[StarAlleleResult] = []

    for gene in target_genes:
        # Step 1: Get allele definitions
        alleles = _fetch_alleles_for_gene(gene, reference_engine)
        if not alleles:
            logger.warning("cpic_no_alleles", gene=gene)
            continue

        # Step 2: Collect all rsids needed for this gene
        all_rsids: list[str] = []
        for allele in alleles:
            for v in allele["defining_variants"]:
                if v["rsid"] not in all_rsids:
                    all_rsids.append(v["rsid"])

        # Step 3: Fetch sample genotypes
        sample_genotypes = _fetch_sample_genotypes(all_rsids, sample_engine)

        # Step 4: Call star alleles
        result = call_star_alleles_for_gene(gene, alleles, sample_genotypes, reference_engine)

        results.append(result)

        logger.info(
            "pgx_star_allele_called",
            gene=gene,
            diplotype=result.diplotype,
            phenotype=result.phenotype,
            call_confidence=result.call_confidence.value,
            involved_rsids=sorted(result.involved_rsids),
            missing_rsids=sorted(result.missing_rsids),
        )

    return results


# ═══════════════════════════════════════════════════════════════════════
# Prescribing Alert Generation (P3-04)
# ═══════════════════════════════════════════════════════════════════════

# CPIC classification → evidence star level mapping per PRD §6 evidence
# star criteria: CPIC Tier A → ★★★★ (4), CPIC Tier B → ★★★ (3),
# Tier C/D → ★★ (2).
_CPIC_CLASSIFICATION_STARS: dict[str | None, int] = {
    "A": 4,
    "B": 3,
    "C": 2,
    "D": 2,
}

# Coarse keyword signals for classifying a CPIC prescribing recommendation's
# actionability (SW-E4 medication-safety report). A recommendation is treated as
# "routine" (standard label dosing, no PGx-driven change) when it matches a routine
# marker and carries no action verb, "actionable" when it implies avoidance, an
# alternative agent, a dose change, or extra monitoring. This is a presentation aid
# to surface attention-worthy results first; it is NOT a clinical-decision signal
# and never alters the recommendation text, phenotype, or evidence level.
_ROUTINE_RECOMMENDATION_MARKERS: tuple[str, ...] = (
    "label-recommended",
    "label recommended",
    "standard dosing",
    "standard, label",
    "no dose adjustment",
    "no recommended dose change",
    "no dose change",
    "routine",
)
_ACTIONABLE_RECOMMENDATION_MARKERS: tuple[str, ...] = (
    "avoid",
    "alternative",
    "reduce",
    "increase",
    "decrease",
    "lower dose",
    "higher dose",
    "adjust",
    "titrate",
    "contraindicat",
    "consider",
    "caution",
    "select ",
    "monitor",
)
# Negated "no-change" phrasings that embed an action substring (e.g. "no dose
# adjustment" contains "adjust"). These are stripped before the action scan so
# they classify as routine, not actionable.
_NEGATED_ROUTINE_MARKERS: tuple[str, ...] = (
    "no dose adjustment",
    "no recommended dose change",
    "no dose change",
)

ACTIONABILITY_ACTIONABLE = "actionable"
ACTIONABILITY_ROUTINE = "routine"
ACTIONABILITY_INDETERMINATE = "indeterminate"


def classify_actionability(recommendation: str | None) -> str:
    """Coarsely classify a CPIC prescribing recommendation's actionability.

    Returns ``"actionable"`` when the recommendation implies a PGx-driven change
    (avoid / alternative agent / dose adjustment / added monitoring),
    ``"routine"`` when it is standard label-recommended dosing, and
    ``"indeterminate"`` when there is no recommendation to classify.

    Honesty guardrail: this is a presentation aid for ordering the
    medication-safety report (attention-worthy results first); it is NOT a
    clinical-decision signal and never changes the underlying phenotype,
    evidence level, or recommendation text.
    """
    if not recommendation:
        return ACTIONABILITY_INDETERMINATE
    rec = recommendation.lower()
    # Neutralize negated "no-change" phrases first so their embedded action
    # substrings (e.g. "adjust" inside "no dose adjustment") don't spuriously flag
    # a genuinely routine recommendation as actionable.
    action_scan = rec
    for marker in _NEGATED_ROUTINE_MARKERS:
        action_scan = action_scan.replace(marker, " ")
    has_action = any(marker in action_scan for marker in _ACTIONABLE_RECOMMENDATION_MARKERS)
    has_routine = any(marker in rec for marker in _ROUTINE_RECOMMENDATION_MARKERS)
    if has_action:
        return ACTIONABILITY_ACTIONABLE
    if has_routine:
        return ACTIONABILITY_ROUTINE
    # Unknown phrasing with no routine marker and no action verb: default to
    # actionable so a recommendation is never under-flagged (fail toward attention).
    return ACTIONABILITY_ACTIONABLE


@dataclass
class PrescribingAlert:
    """A single prescribing alert for a gene-drug interaction.

    Generated by matching a star-allele calling result (gene + phenotype)
    against CPIC guideline recommendations.
    """

    gene: str
    drug: str
    diplotype: str
    phenotype: str
    recommendation: str
    classification: str | None  # CPIC level: A, B, C, D
    guideline_url: str | None
    call_confidence: CallConfidence
    confidence_note: str
    evidence_level: int  # 1-4 stars
    activity_score: float | None = None
    ehr_notation: str | None = None
    involved_rsids: list[str] = field(default_factory=list)
    # SNP defining-position coverage for the gene (SW-E4): how many of the gene's
    # defining array positions were assayed and called out of the total defined.
    coverage_assessed: int = 0
    coverage_total: int = 0
    # Star alleles that could not be excluded (defining variant unassayed). SW-E1.
    indeterminate_alleles: list[str] = field(default_factory=list)


def _fetch_guidelines_for_gene_phenotype(
    gene: str,
    phenotype: str,
    reference_engine: sa.Engine,
) -> list[dict]:
    """Fetch CPIC guideline recommendations for a gene + phenotype pair.

    Args:
        gene: Gene symbol (e.g. "CYP2D6").
        phenotype: Metabolizer phenotype (e.g. "Poor Metabolizer").
        reference_engine: SQLAlchemy engine for reference.db.

    Returns:
        List of dicts with keys: drug, recommendation, classification,
        guideline_url.
    """
    with reference_engine.connect() as conn:
        stmt = (
            sa.select(
                cpic_guidelines.c.drug,
                cpic_guidelines.c.recommendation,
                cpic_guidelines.c.classification,
                cpic_guidelines.c.guideline_url,
            )
            .where(
                sa.and_(
                    cpic_guidelines.c.gene == gene,
                    cpic_guidelines.c.phenotype == phenotype,
                )
            )
            .order_by(cpic_guidelines.c.drug)
        )
        rows = conn.execute(stmt).fetchall()

    return [
        {
            "drug": row.drug,
            "recommendation": row.recommendation,
            "classification": row.classification,
            "guideline_url": row.guideline_url,
        }
        for row in rows
    ]


def generate_prescribing_alerts(
    star_allele_results: list[StarAlleleResult],
    reference_engine: sa.Engine,
) -> list[PrescribingAlert]:
    """Generate prescribing alerts from star-allele calling results.

    For each gene result with a resolved phenotype, looks up matching
    CPIC guidelines and creates a PrescribingAlert for every gene-drug
    pair. Genes with ``Insufficient`` call confidence are excluded (their
    phenotype assignments are unreliable).

    Args:
        star_allele_results: Output from call_all_star_alleles().
        reference_engine: SQLAlchemy engine for reference.db.

    Returns:
        List of PrescribingAlert, sorted by (gene, drug).
    """
    alerts: list[PrescribingAlert] = []

    for result in star_allele_results:
        # Skip genes with no phenotype or insufficient confidence
        if not result.phenotype:
            logger.info(
                "pgx_alert_skipped_no_phenotype",
                gene=result.gene,
                diplotype=result.diplotype,
            )
            continue

        if result.call_confidence == CallConfidence.INSUFFICIENT:
            logger.info(
                "pgx_alert_skipped_insufficient",
                gene=result.gene,
                diplotype=result.diplotype,
                confidence_note=result.confidence_note,
            )
            continue

        # Look up matching guidelines
        guidelines = _fetch_guidelines_for_gene_phenotype(
            result.gene, result.phenotype, reference_engine
        )

        if not guidelines:
            logger.debug(
                "pgx_no_guidelines",
                gene=result.gene,
                phenotype=result.phenotype,
            )
            continue

        for guideline in guidelines:
            evidence_level = assign_cpic_evidence_level(guideline["classification"])

            alert = PrescribingAlert(
                gene=result.gene,
                drug=guideline["drug"],
                diplotype=result.diplotype,
                phenotype=result.phenotype,
                recommendation=guideline["recommendation"],
                classification=guideline["classification"],
                guideline_url=guideline["guideline_url"],
                call_confidence=result.call_confidence,
                confidence_note=result.confidence_note,
                evidence_level=evidence_level,
                activity_score=result.activity_score,
                ehr_notation=result.ehr_notation,
                involved_rsids=sorted(result.involved_rsids),
                coverage_assessed=result.coverage_assessed,
                coverage_total=result.defining_rsid_count,
                indeterminate_alleles=result.indeterminate_alleles,
            )
            alerts.append(alert)

            logger.info(
                "pgx_prescribing_alert",
                gene=result.gene,
                drug=guideline["drug"],
                phenotype=result.phenotype,
                recommendation=guideline["recommendation"],
                classification=guideline["classification"],
                call_confidence=result.call_confidence.value,
                evidence_level=evidence_level,
            )

    # Sort by gene, then drug for deterministic output
    alerts.sort(key=lambda a: (a.gene, a.drug))
    return alerts


def _build_finding_text(alert: PrescribingAlert) -> str:
    """Build a human-readable finding_text for a prescribing alert.

    Format: "{Gene} {diplotype}: {phenotype} -- {drug}: {recommendation}"
    If call confidence is Partial, appends a provisional note.
    """
    text = (
        f"{alert.gene} {alert.diplotype}: {alert.phenotype} -- "
        f"{alert.drug}: {alert.recommendation}"
    )
    if alert.call_confidence == CallConfidence.PARTIAL:
        text += " (provisional -- see call confidence note)"
    return text


def store_prescribing_alerts(
    alerts: list[PrescribingAlert],
    sample_engine: sa.Engine,
) -> int:
    """Persist prescribing alerts as findings records in the sample database.

    Each alert becomes one row in the ``findings`` table with
    ``module='pharmacogenomics'`` and ``category='prescribing_alert'``.

    Args:
        alerts: List of PrescribingAlert from generate_prescribing_alerts().
        sample_engine: SQLAlchemy engine for the sample database.

    Returns:
        Number of findings rows inserted.
    """
    if not alerts:
        return 0

    rows = []
    for alert in alerts:
        detail = {
            "recommendation": alert.recommendation,
            "classification": alert.classification,
            "guideline_url": alert.guideline_url,
            "call_confidence": alert.call_confidence.value,
            "confidence_note": alert.confidence_note,
            "activity_score": alert.activity_score,
            "ehr_notation": alert.ehr_notation,
            "involved_rsids": alert.involved_rsids,
            "coverage": {
                "assessed": alert.coverage_assessed,
                "total": alert.coverage_total,
            },
            "indeterminate_alleles": alert.indeterminate_alleles,
        }
        gene_caveat = _GENE_INTERPRETATION_CAVEATS.get(alert.gene)
        if gene_caveat:
            detail["gene_caveat"] = gene_caveat

        rows.append(
            {
                "module": "pharmacogenomics",
                "category": "prescribing_alert",
                "evidence_level": alert.evidence_level,
                "gene_symbol": alert.gene,
                "diplotype": alert.diplotype,
                "metabolizer_status": alert.phenotype,
                "drug": alert.drug,
                "finding_text": _build_finding_text(alert),
                "detail_json": json.dumps(detail),
            }
        )

    with sample_engine.begin() as conn:
        conn.execute(findings.insert(), rows)

    logger.info("pgx_alerts_stored", count=len(rows))
    return len(rows)


# ═══════════════════════════════════════════════════════════════════════
# Annotation Coverage Bitmask Update (P3-04a)
# ═══════════════════════════════════════════════════════════════════════

_BITMASK_BATCH = 500  # Stay under SQLITE_MAX_VARIABLE_NUMBER


def update_annotation_coverage_cpic(
    star_allele_results: list[StarAlleleResult],
    sample_engine: sa.Engine,
) -> int:
    """OR bit 4 (CPIC, value 16) into annotation_coverage for involved variants.

    After the pharmacogenomics module runs, every variant that participated
    in a star-allele call (i.e. its rsid appears in ``involved_rsids`` of
    any :class:`StarAlleleResult`) gets bit 4 set in its
    ``annotation_coverage`` column in ``annotated_variants``.

    Variants not involved in any CPIC gene leave bit 4 unset.

    Args:
        star_allele_results: Output from :func:`call_all_star_alleles`.
        sample_engine: SQLAlchemy engine for the sample database.

    Returns:
        Number of variants updated.
    """
    # Collect all unique rsids that participated in star-allele calls
    involved: set[str] = set()
    for result in star_allele_results:
        involved.update(result.involved_rsids)

    if not involved:
        return 0

    rsid_list = sorted(involved)
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
                            CPIC_BIT,
                        ),
                        else_=annotated_variants.c.annotation_coverage.op("|")(CPIC_BIT),
                    )
                )
            )
            result = conn.execute(stmt)
            updated += result.rowcount

    logger.info(
        "pgx_annotation_coverage_updated",
        cpic_bit=CPIC_BIT,
        involved_rsids=len(involved),
        rows_updated=updated,
    )
    return updated

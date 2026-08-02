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
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import sqlalchemy as sa
import structlog

from backend.analysis.evidence import assign_cpic_evidence_level
from backend.analysis.zygosity import _NO_CALL_SENTINELS, is_no_call
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

# The two-char indel codes are genuine no-calls for SNV scoring, but the indel
# star-allele counter (_count_indel_alt_alleles) must *score* I/D tokens at
# simple-indel loci rather than discard them. So the indel no-call set is the
# shared sentinel set MINUS those indel codes — derived from the single source
# of truth (zygosity._NO_CALL_SENTINELS) so it cannot silently drift, mirroring
# risk_genotype._TRUE_NO_CALLS (#525/#582). The derived frozenset is
# byte-identical to the prior literal {"", "--", "??", "-", "0", "00"}.
_INDEL_NO_CALL_CODES: frozenset[str] = frozenset({"DD", "II", "DI", "ID"})
_TRUE_NO_CALLS: frozenset[str] = _NO_CALL_SENTINELS - _INDEL_NO_CALL_CODES

# Genes with known structural variant complexity (copy number variation,
# gene conversion, hybrid alleles) that array genotyping cannot resolve.
# These always receive "Partial" confidence at best.
STRUCTURAL_VARIANT_GENES: frozenset[str] = frozenset({"CYP2D6", "CYP2B6"})

# Structural / copy-number alleles represented in CPIC tables but not callable
# from SNP-array genotypes. Keep these out of empty-definition reference-allele
# selection and surface them as indeterminate rather than silently assuming *1.
STRUCTURAL_UNCALLABLE_ALLELES: dict[str, tuple[str, ...]] = {
    "CYP2D6": ("*5",),
    # PharmVar/CPIC define NUDT15 *6 and *9 as non-SNV indels at the same rsid
    # (rs746071566), and PharmVar's legacy *2 haplotype is now NUDT15*3.002
    # with the same insertion plus *3. SNP-array D/I tokens cannot resolve these
    # sequence states safely, so surface them as indeterminate rather than direct
    # calls.
    "NUDT15": ("*3.002", "*6", "*9"),
}

# Issue #1081/#1413: for these genes, an untyped reduced/no-function marker can
# make the direct reference-filled call clinically milder than a plausible CPIC
# phenotype. Keep this policy scoped to reproduced genes so existing
# gene-specific caveats (e.g. NUDT15 non-SNV alleles) do not change alert
# semantics without separate review.
CONSERVATIVE_UNTYPED_PHENOTYPE_GENES: frozenset[str] = frozenset({"CYP2B6", "CYP2C9", "UGT1A1"})

# Issue #2169: genes whose drug recommendation must be *withheld* — not swapped for
# a milder one — when an untyped defining marker leaves the plausible diplotypes
# spanning different shipped recommendations.
#
# The conservative policy above answers "which plausible diplotype do we alert on?"
# by picking the lowest activity score. That is only sound when the alternatives
# differ in *degree*. For CYP3A5 they differ in *direction*: CPIC increases the
# tacrolimus starting dose for expressers and keeps label-recommended dosing for
# non-expressers, and it supplies those recommendations by genotype *when known*
# (Birdwell et al., 2015; PMID:25801146, DOI:10.1002/cpt.113, accessed 2026-07-30).
# The `*3`/`*6` SNPs abolish CYP3A5 expression, so only a `*1` carrier expresses the
# enzyme (Kuehl et al., 2001; PMID:11279519, DOI:10.1038/86882, accessed
# 2026-07-30); nomenclature per PharmVar GeneFocus: CYP3A5 (Rodriguez-Antona et al.,
# 2022; PMID:35202484, DOI:10.1002/cpt.2563, accessed 2026-07-30).
#
# Neither direction is safely assumable from an untyped marker: the expresser dose
# increase could overexpose a true non-expresser, and the non-expresser standard
# dose could underexpose a true expresser (tacrolimus has a narrow therapeutic
# index, so both errors are clinically real). No source establishes either branch
# as the correct default for an *unknown* genotype, so this code does not pick one.
# The alert is withheld and the gene result still records the Partial call and the
# indeterminate allele.
WITHHOLD_CROSS_DIRECTION_GENES: frozenset[str] = frozenset({"CYP3A5"})

# CPIC's CYP2D6/tamoxifen rows remain in the bundled reference database as a
# source-faithful, audit-only record (#2019). They must not become Yeliztli
# prescribing output: the required two independent, agreeing sources for the
# genotype-guided treatment actions are not available, and independent
# authorities/trials conflict. Keep this pair explicitly withheld until a
# scientific-validity review can clear that gate.
WITHHELD_PRESCRIBING_ALERT_PAIRS: frozenset[tuple[str, str]] = frozenset({("CYP2D6", "tamoxifen")})

# Python's ``str.strip`` recognizes these 29 Unicode whitespace code points.
# SQLite's one-argument ``trim`` only removes U+0020, so SQL presentation
# boundaries pass this complete set explicitly to preserve the same fail-closed
# normalization as :func:`is_prescribing_alert_withheld`.
_SQLITE_PYTHON_STRIP_CHARS = (
    "\t\n\v\f\r\x1c\x1d\x1e\x1f "
    "\x85\xa0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a"
    "\u2028\u2029\u202f\u205f\u3000"
)

# Arbitrary raw SQL cannot safely inject the row-level presentation predicate
# into a user query. These tables retain either source-faithful finding payloads
# or serialized finding diffs, so neither may be read through the interactive
# console/export while a pair is clinically withheld.
_RAW_SQL_AUDIT_ONLY_TABLES: frozenset[str] = frozenset({"annotation_state", "findings"})


def is_prescribing_alert_withheld(gene: object, drug: object) -> bool:
    """Whether a gene-drug pair is held from Yeliztli prescribing output.

    The reference row remains available for audit provenance, but a held pair
    must not be rendered as a patient-specific prescribing recommendation.
    Normalize both inputs here so every output surface applies the same hold.
    """
    if not isinstance(gene, str) or not isinstance(drug, str):
        return False
    return (gene.strip().upper(), drug.strip().casefold()) in WITHHELD_PRESCRIBING_ALERT_PAIRS


def is_withheld_prescribing_alert_finding(
    _module: str | None,
    category: str | None,
    gene: str | None,
    drug: str | None,
) -> bool:
    """Whether a stored finding is held from patient-visible presentation.

    This policy applies to every prescribing alert for the held pair, including
    a legacy or custom row under a differently named module. Source-faithful
    rows stay in storage for provenance and future scientific review, but generic
    result, report, and diff surfaces must not turn their free-text payload into
    patient-specific clinical guidance.
    """
    return (
        category or ""
    ).strip().casefold() == "prescribing_alert" and is_prescribing_alert_withheld(gene, drug)


def patient_visible_finding_clause(columns: Any) -> Any:
    """Return a SQL predicate excluding clinically withheld alert pairs.

    Apply this at patient-visible presentation boundaries and when replacing
    reanalysis-generated alerts. It keeps audit-only source rows available to
    scientific-validity review while SQL normalization mirrors
    :func:`is_withheld_prescribing_alert_finding`, including case and whitespace.
    """
    if not WITHHELD_PRESCRIBING_ALERT_PAIRS:
        return sa.true()

    category = sa.func.lower(
        sa.func.trim(sa.func.coalesce(columns.category, ""), _SQLITE_PYTHON_STRIP_CHARS)
    )
    gene = sa.func.upper(
        sa.func.trim(sa.func.coalesce(columns.gene_symbol, ""), _SQLITE_PYTHON_STRIP_CHARS)
    )
    drug = sa.func.lower(
        sa.func.trim(sa.func.coalesce(columns.drug, ""), _SQLITE_PYTHON_STRIP_CHARS)
    )
    held_pairs = [
        sa.and_(
            category == "prescribing_alert",
            gene == held_gene,
            drug == held_drug,
        )
        for held_gene, held_drug in WITHHELD_PRESCRIBING_ALERT_PAIRS
    ]
    return sa.not_(sa.or_(*held_pairs))


def configure_raw_sql_findings_guard(connection: sqlite3.Connection) -> Callable[[], bool]:
    """Prevent raw SQL consoles from reading stored audit-only finding payloads.

    The console accepts arbitrary read-only SQL, so safely injecting a row-level
    visibility predicate is not possible. While a clinically withheld pair is
    retained for audit provenance, deny all reads of ``findings`` and serialized
    finding history through SQLite's execution-time authorizer instead. This
    blocks aliases, CTEs, and other syntactic rewrites without deleting the
    audit record or weakening ordinary patient-visible query boundaries. The
    returned callback reports whether this connection's authorizer denied a
    protected-table read, so callers can distinguish SQLite's ambiguous bare
    ``not authorized`` error from an unrelated SQL failure.
    """
    if not WITHHELD_PRESCRIBING_ALERT_PAIRS:
        return lambda: False

    denied_protected_read = False

    def _authorizer(
        action: int,
        table: str | None,
        _column: str | None,
        _database: str | None,
        _trigger: str | None,
    ) -> int:
        nonlocal denied_protected_read
        if (
            action == sqlite3.SQLITE_READ
            and (table or "").casefold() in _RAW_SQL_AUDIT_ONLY_TABLES
        ):
            denied_protected_read = True
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    connection.set_authorizer(_authorizer)
    return lambda: denied_protected_read


def is_raw_sql_audit_only_access_denied(error: object, *, guard_denied: bool) -> bool:
    """Whether this connection's guard rejected a protected audit-only read."""
    message = str(error).casefold().strip()
    # SQLite omits the table name when an aggregate invokes the authorizer
    # without a readable column (for example ``COUNT(*)``). A bare message is
    # therefore meaningful only if this exact guard recorded its own denial:
    # unrelated functions such as load_extension() can emit the same text.
    return guard_denied and ("not authorized" in message or "prohibited" in message)


# Genes whose diplotype must be flagged as phase-inferred when two *different*
# non-reference alleles are called from unphased array genotypes. The helper
# below requires each allele to have its own heterozygous defining marker so
# shared-marker diplotypes are not overflagged.
_PHASE_INFERENCE_GENES: frozenset[str] = frozenset(
    {
        "CYP2B6",
        "CYP2C19",
        "CYP2C9",
        "CYP3A5",
        "DPYD",
        "NAT2",
        "NUDT15",
        "SLCO1B1",
        "TPMT",
    }
)
_TPMT_STAR3A_PHASE_RSIDS: frozenset[str] = frozenset({"rs1800460", "rs1142345"})
_NUDT15_LEGACY_STAR2_PHASE_RSIDS: frozenset[str] = frozenset({"rs746071566", "rs116855232"})
_VariantKey = tuple[str, str, str]

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
    assessed_rsids: set[str] = field(default_factory=set)
    missing_rsids: set[str] = field(default_factory=set)
    uncalled_rsids: set[str] = field(default_factory=set)
    defining_rsid_count: int = 0
    call_confidence: CallConfidence = CallConfidence.COMPLETE
    confidence_note: str = ""
    # Non-reference alleles that could NOT be excluded because a defining variant
    # was not assayed/callable on the array (the reference *1 fill is an assumption,
    # not an observation). SW-E1.
    indeterminate_alleles: list[str] = field(default_factory=list)
    indeterminate_allele_rsids: dict[str, list[str]] = field(default_factory=dict)
    reference_allele: str | None = None
    # Conservative phenotype used for prescribing when an untyped defining marker
    # could make the directly called phenotype less severe than the CPIC-worst
    # plausible one. The direct diplotype/phenotype remain above for auditability.
    conservative_diplotype: str | None = None
    conservative_phenotype: str | None = None
    conservative_activity_score: float | None = None
    conservative_allele: str | None = None

    @property
    def coverage_assessed(self) -> int:
        """Number of the gene's defining SNP positions actually assayed and called.

        This is *SNP defining-position* coverage only — it does not (and from
        array data cannot) account for copy-number or gene-conversion alleles,
        which the reference-bias disclosure covers separately.
        """
        if self.assessed_rsids:
            return len(self.assessed_rsids)
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


def _variant_key(variant: dict) -> _VariantKey:
    """Return a stable key for a defining variant.

    Most CPIC definitions are one rsid per variant, but NUDT15 rs746071566 has
    both insertion and deletion definitions. The full ref/alt signature is needed
    to avoid collapsing distinct alleles into a single rsid count.
    """
    return (
        variant["rsid"],
        variant["ref"].upper(),
        variant["alt"].upper(),
    )


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


@dataclass(frozen=True)
class _ConservativePhenotype:
    """Worst plausible lower-activity result from one untyped allele."""

    diplotype: str
    phenotype: str
    activity_score: float | None
    ehr_notation: str | None
    allele: str


def _canonical_diplotype(allele1: str, allele2: str) -> str:
    """Return a CPIC-sorted diplotype string for two star alleles."""
    return "/".join(sorted([allele1, allele2], key=_allele_sort_key))


def _conservative_alert_note(
    result: StarAlleleResult,
    conservative: _ConservativePhenotype,
) -> str:
    """Explain why prescribing alerts use a conservative phenotype."""
    conservative_score = (
        f" (activity score {conservative.activity_score})"
        if conservative.activity_score is not None
        else ""
    )
    called_score = (
        f" (activity score {result.activity_score})" if result.activity_score is not None else ""
    )
    return (
        "Conservative prescribing alert uses "
        f"{conservative.phenotype}{conservative_score} because "
        f"untyped {result.gene}{conservative.allele} could make "
        f"{conservative.diplotype}; the directly called "
        f"{result.diplotype} maps to {result.phenotype}{called_score}."
    )


def _has_score_specific_guideline(
    gene: str,
    phenotype: str,
    activity_score: float | None,
    reference_engine: sa.Engine,
) -> bool:
    """Whether any guideline explicitly keys this phenotype on ``activity_score``."""
    if activity_score is None:
        return False
    stmt = (
        sa.select(cpic_guidelines.c.id)
        .where(
            cpic_guidelines.c.gene == gene,
            cpic_guidelines.c.phenotype == phenotype,
            cpic_guidelines.c.activity_score == activity_score,
        )
        .limit(1)
    )
    with reference_engine.connect() as conn:
        return conn.execute(stmt).first() is not None


def _infer_conservative_phenotype(
    result: StarAlleleResult,
    reference_engine: sa.Engine,
) -> _ConservativePhenotype | None:
    """Return a lower-activity plausible phenotype from indeterminate alleles.

    The caller fills unobserved chromosomes with the reference allele. For a
    partial call, replacing one such reference-filled chromosome with an
    indeterminate allele models the smallest clinically relevant uncertainty:
    one untyped reduced/no-function allele may be present. If that plausible
    diplotype has a lower CPIC activity score, use it for prescribing alerts
    rather than alerting on the milder direct call. A same-label candidate is
    relevant only when a shipped guideline explicitly keys that lower score
    within the phenotype band (for example CYP2C9/phenytoin).

    This substitution answers "which plausible diplotype do we alert on?" and is
    only sound when the alternatives differ in *degree*. When they differ in
    *direction* the answer is to withhold instead — see
    :data:`WITHHOLD_CROSS_DIRECTION_GENES` and
    :func:`_untyped_marker_spans_conflicting_recommendations` (#2169).
    """
    if (
        result.gene not in CONSERVATIVE_UNTYPED_PHENOTYPE_GENES
        or result.call_confidence != CallConfidence.PARTIAL
        or not result.phenotype
        or result.activity_score is None
        or not result.indeterminate_alleles
    ):
        return None

    reference_allele = result.reference_allele or "*1"
    called_alleles = [result.allele1, result.allele2]
    reference_slots = [
        index for index, allele in enumerate(called_alleles) if allele == reference_allele
    ]
    if not reference_slots:
        return None

    candidates: list[_ConservativePhenotype] = []
    for indeterminate_allele in result.indeterminate_alleles:
        if indeterminate_allele in called_alleles:
            continue
        allele_rsids = set(result.indeterminate_allele_rsids.get(indeterminate_allele, []))
        if allele_rsids & result.involved_rsids:
            continue

        for index in reference_slots:
            plausible = called_alleles.copy()
            plausible[index] = indeterminate_allele
            plausible_diplotype = _canonical_diplotype(plausible[0], plausible[1])
            if plausible_diplotype == result.diplotype:
                continue

            diplo_data = _fetch_diplotype_phenotype(
                result.gene, plausible_diplotype, reference_engine
            )
            if diplo_data is None:
                continue

            activity_score = diplo_data["activity_score"]
            phenotype = diplo_data["phenotype"]
            if activity_score is None or activity_score >= result.activity_score:
                continue
            if phenotype == result.phenotype and not _has_score_specific_guideline(
                result.gene,
                phenotype,
                activity_score,
                reference_engine,
            ):
                continue

            candidates.append(
                _ConservativePhenotype(
                    diplotype=plausible_diplotype,
                    phenotype=phenotype,
                    activity_score=activity_score,
                    ehr_notation=diplo_data["ehr_notation"],
                    allele=indeterminate_allele,
                )
            )

    if not candidates:
        return None

    return sorted(
        candidates,
        key=lambda candidate: (
            candidate.activity_score if candidate.activity_score is not None else float("inf"),
            candidate.diplotype,
            candidate.allele,
        ),
    )[0]


def _untyped_marker_spans_conflicting_recommendations(
    result: StarAlleleResult,
    drug: str,
    called_recommendation: str,
    reference_engine: sa.Engine,
) -> bool:
    """Whether an untyped marker leaves this drug's shipped advice undetermined.

    Substitutes each unobserved indeterminate allele into the reference-filled
    chromosomes — one copy and, because an unassayed marker constrains *neither*
    chromosome, both copies — and compares the shipped recommendation of every
    resulting diplotype against the directly called one. A difference means the
    data admit genotypes whose CPIC advice points in different directions, so no
    recommendation can be chosen without asserting a genotype that was not typed.

    This is a comparison of shipped guideline rows, not a new clinical claim.
    """
    if result.call_confidence != CallConfidence.PARTIAL or not result.indeterminate_alleles:
        return False

    reference_allele = result.reference_allele or "*1"
    called_alleles = [result.allele1, result.allele2]
    reference_slots = [i for i, allele in enumerate(called_alleles) if allele == reference_allele]
    if not reference_slots:
        return False

    for indeterminate_allele in result.indeterminate_alleles:
        if indeterminate_allele in called_alleles:
            continue
        allele_rsids = set(result.indeterminate_allele_rsids.get(indeterminate_allele, []))
        if allele_rsids & result.involved_rsids:
            continue

        plausible_sets = [
            [
                indeterminate_allele if i == slot else allele
                for i, allele in enumerate(called_alleles)
            ]
            for slot in reference_slots
        ]
        if len(reference_slots) == len(called_alleles):
            plausible_sets.append([indeterminate_allele] * len(called_alleles))

        for plausible in plausible_sets:
            plausible_diplotype = _canonical_diplotype(plausible[0], plausible[1])
            if plausible_diplotype == result.diplotype:
                continue
            diplo_data = _fetch_diplotype_phenotype(
                result.gene, plausible_diplotype, reference_engine
            )
            if diplo_data is None:
                continue
            guidelines = _fetch_guidelines_for_gene_phenotype(
                result.gene,
                diplo_data["phenotype"],
                reference_engine,
                activity_score=diplo_data["activity_score"],
            )
            for guideline in guidelines:
                if (
                    guideline["drug"] == drug
                    and guideline["recommendation"] != called_recommendation
                ):
                    return True
    return False


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


def _is_tpmt_star3a_phase_ambiguous(
    gene: str,
    diplotype: str,
    observed_alt_counts: dict[str, int],
) -> bool:
    """Return True when unphased TPMT data cannot distinguish *1/*3A from *3B/*3C."""
    return (
        gene == "TPMT"
        and diplotype == "*1/*3A"
        and all(observed_alt_counts.get(rsid) == 1 for rsid in _TPMT_STAR3A_PHASE_RSIDS)
    )


def _defining_rsids_by_allele(alleles: list[dict]) -> dict[str, set[str]]:
    """Map each star allele to its defining rsids."""
    return {
        allele["allele_name"]: {v["rsid"] for v in allele["defining_variants"]}
        for allele in alleles
    }


def _has_heterozygous_unique_marker(
    allele: str,
    other_allele: str,
    defining_rsids: dict[str, set[str]],
    observed_alt_counts: dict[str, int],
) -> bool:
    """Return whether an allele has a heterozygous marker not shared by the other allele."""
    unique_rsids = defining_rsids.get(allele, set()) - defining_rsids.get(other_allele, set())
    return any(observed_alt_counts.get(rsid) == 1 for rsid in unique_rsids)


def _is_phase_inferred_compound_het(
    gene: str,
    allele1: str,
    allele2: str,
    ref_allele_name: str,
    alleles: list[dict],
    observed_alt_counts: dict[str, int],
) -> bool:
    """Return True for distinct non-reference alleles inferred from unphased markers."""
    if (
        gene not in _PHASE_INFERENCE_GENES
        or allele1 == ref_allele_name
        or allele2 == ref_allele_name
        or allele1 == allele2
    ):
        return False

    defining_rsids = _defining_rsids_by_allele(alleles)
    return _has_heterozygous_unique_marker(
        allele1, allele2, defining_rsids, observed_alt_counts
    ) and _has_heterozygous_unique_marker(allele2, allele1, defining_rsids, observed_alt_counts)


def _is_nudt15_legacy_star2_phase_ambiguous(
    gene: str,
    diplotype: str,
    observed_alt_counts: dict[str, int],
) -> bool:
    """Return True when NUDT15 *3 plus the legacy *2 indel marker are unphased."""
    return (
        gene == "NUDT15"
        and diplotype == "*1/*3"
        and all(observed_alt_counts.get(rsid) == 1 for rsid in _NUDT15_LEGACY_STAR2_PHASE_RSIDS)
    )


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
    structural_uncallable = set(STRUCTURAL_UNCALLABLE_ALLELES.get(gene, ()))

    for allele in alleles:
        if not allele["defining_variants"]:
            if allele["allele_name"] in structural_uncallable:
                continue
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

    # Track remaining alt copies per defining-variant signature (from sample
    # genotypes). Use the full (rsid, ref, alt) key because NUDT15 rs746071566
    # has multiple non-equivalent definitions under one rsid.
    remaining_alts: dict[_VariantKey, int] = {}
    observed_variant_alt_counts: dict[_VariantKey, int] = {}
    observed_alt_counts: dict[str, int] = {}
    uncalled_rsids: set[str] = set()

    for allele in non_ref_alleles:
        for v in allele["defining_variants"]:
            rsid = v["rsid"]
            key = _variant_key(v)
            if key in remaining_alts:
                continue
            if rsid not in sample_genotypes:
                continue
            alt_count = _count_alt_alleles(sample_genotypes[rsid], v["ref"], v["alt"])
            if alt_count is None:
                uncalled_rsids.add(rsid)
            else:
                remaining_alts[key] = alt_count
                observed_variant_alt_counts[key] = alt_count
                observed_alt_counts[rsid] = max(observed_alt_counts.get(rsid, 0), alt_count)

    assessed_rsids = all_defining_rsids - missing_rsids - uncalled_rsids

    # Sort non-ref alleles: most defining variants first (most specific),
    # then alphabetically for deterministic results
    non_ref_alleles.sort(key=lambda a: (-len(a["defining_variants"]), a["allele_name"]))

    # Greedily assign alleles
    called_alleles: list[str] = []
    involved_rsids: set[str] = set()

    for allele in non_ref_alleles:
        if allele["allele_name"] in structural_uncallable:
            continue

        slots_left = 2 - len(called_alleles)
        if slots_left <= 0:
            break

        variants = allele["defining_variants"]
        max_copies = slots_left

        for v in variants:
            key = _variant_key(v)
            if key not in remaining_alts:
                max_copies = 0
                break
            max_copies = min(max_copies, remaining_alts[key])

        if max_copies > 0:
            # Consume alt copies
            for v in variants:
                remaining_alts[_variant_key(v)] -= max_copies
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
    snp_indeterminate_alleles = set()
    for allele in non_ref_alleles:
        if allele["allele_name"] in called_alleles:
            continue

        has_unusable_marker = False
        excluded_by_typed_reference = False
        for variant in allele["defining_variants"]:
            if variant["rsid"] in unusable_rsids:
                has_unusable_marker = True
                continue
            if observed_variant_alt_counts.get(_variant_key(variant), 0) == 0:
                excluded_by_typed_reference = True
                break

        if has_unusable_marker and not excluded_by_typed_reference:
            snp_indeterminate_alleles.add(allele["allele_name"])
    indeterminate_alleles = sorted(snp_indeterminate_alleles | structural_uncallable)
    indeterminate_allele_rsids = {
        allele["allele_name"]: sorted({v["rsid"] for v in allele["defining_variants"]})
        for allele in non_ref_alleles
        if allele["allele_name"] in indeterminate_alleles
    }
    for allele_name in structural_uncallable:
        indeterminate_allele_rsids.setdefault(allele_name, [])
    if indeterminate_alleles:
        confidence_note = (
            f"{confidence_note} Cannot exclude {', '.join(indeterminate_alleles)} — "
            "defining variant(s) or structural/copy-number state not assayed on this array."
        ).strip()
        if structural_uncallable and call_confidence == CallConfidence.COMPLETE:
            call_confidence = CallConfidence.PARTIAL

    # Phase-inference guard: a diplotype built from two distinct non-reference
    # alleles can be a trans compound-heterozygote inferred from unphased array
    # genotypes. Flag it (PARTIAL, never overriding a worse confidence) and carry
    # the caveat into the alert so clinically load-bearing phenotypes are not
    # presented as directly phased calls.
    if diplo_data is not None and _is_phase_inferred_compound_het(
        gene, allele1, allele2, ref_allele_name, alleles, observed_alt_counts
    ):
        phase_note = (
            f"{gene} {diplotype} combines two different non-reference alleles called "
            "from unphased array genotypes; the trans (compound-heterozygous) "
            "configuration is inferred from the star-allele model, but phase was not "
            "directly determined and a cis configuration can alter the inferred "
            "diplotype or phenotype for some CPIC interpretations. Treat this as a "
            "phase-inferred star-allele call."
        )
        confidence_note = f"{confidence_note} {phase_note}".strip()
        if call_confidence == CallConfidence.COMPLETE:
            call_confidence = CallConfidence.PARTIAL

    # TPMT issue #60: a double heterozygote at the two TPMT*3 loci is not an
    # unambiguous *1/*3A call from unphased SNP data. The same observed genotype
    # can be cis (*1/*3A, Intermediate) or trans (*3B/*3C, Poor), so keep the
    # CPIC-compatible greedy label for lookup but downgrade confidence and surface
    # the possible Poor Metabolizer configuration.
    if _is_tpmt_star3a_phase_ambiguous(gene, diplotype, observed_alt_counts):
        phase_note = (
            "TPMT *1/*3A is inferred from two heterozygous *3-defining variants "
            "in unphased SNP genotypes; the same observed genotype can also "
            "represent TPMT *3B/*3C (possible Poor Metabolizer). Phase was not "
            "directly determined, so treat the thiopurine phenotype as provisional."
        )
        confidence_note = f"{confidence_note} {phase_note}".strip()
        if call_confidence == CallConfidence.COMPLETE:
            call_confidence = CallConfidence.PARTIAL

    # NUDT15 legacy *2 / current PharmVar *3.002: the same unphased observation
    # of heterozygous rs116855232 plus heterozygous rs746071566 can be cis
    # (*1/*3.002, legacy *2) or trans (*3/*6, possible Poor Metabolizer).
    if _is_nudt15_legacy_star2_phase_ambiguous(gene, diplotype, observed_alt_counts):
        phase_note = (
            "NUDT15 *1/*3 is inferred while rs746071566 is also heterozygous; "
            "without phase, the same observed genotype can represent current "
            "PharmVar NUDT15*3.002 (legacy *2) or NUDT15 *3/*6 "
            "(possible Poor Metabolizer). Treat the thiopurine phenotype as provisional."
        )
        confidence_note = f"{confidence_note} {phase_note}".strip()
        if call_confidence == CallConfidence.COMPLETE:
            call_confidence = CallConfidence.PARTIAL

    result = StarAlleleResult(
        gene=gene,
        allele1=allele1,
        allele2=allele2,
        diplotype=diplotype,
        phenotype=diplo_data["phenotype"] if diplo_data else None,
        ehr_notation=diplo_data["ehr_notation"] if diplo_data else None,
        activity_score=diplo_data["activity_score"] if diplo_data else None,
        involved_rsids=involved_rsids,
        assessed_rsids=assessed_rsids,
        missing_rsids=missing_rsids,
        uncalled_rsids=uncalled_rsids,
        defining_rsid_count=len(all_defining_rsids),
        call_confidence=call_confidence,
        confidence_note=confidence_note,
        indeterminate_alleles=indeterminate_alleles,
        indeterminate_allele_rsids=indeterminate_allele_rsids,
        reference_allele=ref_allele_name,
    )
    conservative = _infer_conservative_phenotype(result, reference_engine)
    if conservative is not None:
        result.conservative_diplotype = conservative.diplotype
        result.conservative_phenotype = conservative.phenotype
        result.conservative_activity_score = conservative.activity_score
        result.conservative_allele = conservative.allele
        note = _conservative_alert_note(result, conservative)
        if note not in result.confidence_note:
            result.confidence_note = f"{result.confidence_note} {note}".strip()

    return result


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
# CPIC's phenytoin no-adjustment rows continue with ordinary TDM/response and
# HLA-B safety guidance, so a generic action-verb scan sees "adjusted" and
# "monitoring". Only neutralize those exact standard-practice clauses after the
# published no-adjustment preamble; any other appended text keeps the normal
# fail-toward-attention scan.
_ROUTINE_RECOMMENDATION_PREFIXES: tuple[str, ...] = (
    "no adjustments needed from typical dosing strategies.",
)
_ROUTINE_FOLLOW_ON_CLAUSES: tuple[str, ...] = (
    "subsequent doses should be adjusted according to therapeutic drug monitoring, response, "
    "and side effects.",
    "subsequent doses should be adjusted according to therapeutic drug monitoring, response "
    "and side effects.",
    "an hla-b*15:02 negative test does not eliminate the risk of phenytoin-induced sjs/ten, "
    "and patients should be carefully monitored according to standard practice.",
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
    routine_prefix = next(
        (prefix for prefix in _ROUTINE_RECOMMENDATION_PREFIXES if rec.startswith(prefix)),
        None,
    )
    if routine_prefix is not None:
        action_scan = rec.removeprefix(routine_prefix)
        for clause in _ROUTINE_FOLLOW_ON_CLAUSES:
            action_scan = action_scan.replace(clause, " ")
        if not action_scan.strip(" \t\r\n.,;"):
            return ACTIONABILITY_ROUTINE
    else:
        action_scan = rec
    # Neutralize negated "no-change" phrases first so their embedded action
    # substrings (e.g. "adjust" inside "no dose adjustment") don't spuriously flag
    # a genuinely routine recommendation as actionable.
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
    indeterminate_allele_rsids: dict[str, list[str]] = field(default_factory=dict)
    # True when ``phenotype``/``activity_score`` come from the conservative
    # lower-activity phenotype rather than the directly called diplotype.
    conservative_alert: bool = False
    called_phenotype: str | None = None
    called_activity_score: float | None = None
    called_ehr_notation: str | None = None
    conservative_diplotype: str | None = None
    conservative_allele: str | None = None


def _fetch_guidelines_for_gene_phenotype(
    gene: str,
    phenotype: str,
    reference_engine: sa.Engine,
    activity_score: float | None = None,
) -> list[dict]:
    """Fetch CPIC guideline recommendations for a gene phenotype/activity score.

    CPIC keys some recommendations on the gene activity score, not the phenotype
    label alone. A NULL score is the generic phenotype fallback. Precedence is
    resolved per drug: return every exact-score row for a drug when present,
    otherwise every matching generic row. This lets score-keyed phenytoin coexist
    with phenotype-keyed warfarin and deliberately preserves duplicate rows at
    the winning specificity so data regressions remain visible. See #1993/#1989.

    Args:
        gene: Gene symbol (e.g. "CYP2D6").
        phenotype: Metabolizer phenotype (e.g. "Poor Metabolizer").
        reference_engine: SQLAlchemy engine for reference.db.
        activity_score: The called gene activity score, if any.

    Returns:
        List of dicts with keys: drug, recommendation, classification,
        guideline_url.
    """

    score_predicate = cpic_guidelines.c.activity_score.is_(None)
    if activity_score is not None:
        score_predicate = sa.or_(
            score_predicate,
            cpic_guidelines.c.activity_score == activity_score,
        )

    stmt = (
        sa.select(
            cpic_guidelines.c.drug,
            cpic_guidelines.c.recommendation,
            cpic_guidelines.c.classification,
            cpic_guidelines.c.guideline_url,
            cpic_guidelines.c.activity_score,
        )
        .where(
            sa.and_(
                cpic_guidelines.c.gene == gene,
                cpic_guidelines.c.phenotype == phenotype,
                score_predicate,
            )
        )
        .order_by(cpic_guidelines.c.drug, cpic_guidelines.c.id)
    )
    with reference_engine.connect() as conn:
        rows = conn.execute(stmt).fetchall()

    exact_drugs = {row.drug for row in rows if row.activity_score is not None}
    selected_rows = [
        row for row in rows if row.activity_score is not None or row.drug not in exact_drugs
    ]

    return [
        {
            "drug": row.drug,
            "recommendation": row.recommendation,
            "classification": row.classification,
            "guideline_url": row.guideline_url,
        }
        for row in selected_rows
    ]


def _fetch_guideline_phenotypes_for_gene(
    gene: str,
    reference_engine: sa.Engine,
) -> set[str]:
    """Return the set of phenotypes that have any guideline row for a gene.

    Used to distinguish a gene that is generally covered (rows exist for
    other phenotypes) from one with no guidelines at all, so a missing row
    for a single phenotype can be flagged as a likely coverage gap.
    """
    with reference_engine.connect() as conn:
        rows = conn.execute(
            sa.select(cpic_guidelines.c.phenotype).where(cpic_guidelines.c.gene == gene).distinct()
        ).fetchall()
    return {row.phenotype for row in rows}


# CPIC's thiopurine guideline is keyed on the joint (TPMT, NUDT15) phenotype
# pair, not either gene alone: a patient Intermediate at BOTH genes has a
# distinct, lower starting-dose band and higher toxicity risk than a single-gene
# Intermediate Metabolizer (#2007). The per-gene guideline schema emits two
# independent, conflicting single-gene alerts; these constants let
# generate_prescribing_alerts collapse the (IM, IM) case into CPIC's one
# compound-IM recommendation, stored under a synthetic joint "gene" so that
# single-gene lookups never surface it.
_THIOPURINE_JOINT_GENES = ("TPMT", "NUDT15")
_THIOPURINE_JOINT_GENE_LABEL = "TPMT/NUDT15"
_COMPOUND_IM_PHENOTYPE = "Compound Intermediate Metabolizer"
_SINGLE_IM_PHENOTYPE = "Intermediate Metabolizer"


def _apply_thiopurine_joint_phenotype(
    alerts: list[PrescribingAlert],
    reference_engine: sa.Engine,
) -> list[PrescribingAlert]:
    """Collapse independent TPMT-IM + NUDT15-IM thiopurine alerts into CPIC's joint call.

    CPIC dosing for thiopurines is keyed on the (TPMT, NUDT15) phenotype pair.
    A patient Intermediate at both genes is a *compound* intermediate metabolizer
    with a lower starting-dose band (20-50% of standard) than either single-gene
    Intermediate Metabolizer (30-80%), reflecting additive toxicity (#2007;
    CPIC 2025 update, PMID:41618934 / DOI:10.1002/cpt.70209). The per-gene schema
    emits two independent single-gene alerts whose bands both sit above CPIC's
    compound cap; when both genes are callable IM this replaces that pair with the
    single joint recommendation.

    Genes with Insufficient call confidence never produce an alert (they are
    filtered upstream), so this is inert for array-only inputs where NUDT15 cannot
    be typed — the current behaviour of a lone TPMT alert is preserved.
    """
    # Group single-gene IM alerts by drug then gene as lists: _fetch_guidelines_
    # for_gene_phenotype deliberately preserves duplicate winning-specificity rows
    # so a data regression stays visible (#1993/#1989), and collapsing them into a
    # dict here would silently hide that. One alert per source row is kept.
    im_by_drug: dict[str, dict[str, list[PrescribingAlert]]] = {}
    for alert in alerts:
        if alert.gene in _THIOPURINE_JOINT_GENES and alert.phenotype == _SINGLE_IM_PHENOTYPE:
            im_by_drug.setdefault(alert.drug, {}).setdefault(alert.gene, []).append(alert)

    both_im_drugs = {
        drug
        for drug, by_gene in im_by_drug.items()
        if all(gene in by_gene for gene in _THIOPURINE_JOINT_GENES)
    }
    if not both_im_drugs:
        return alerts

    compound_rows_by_drug: dict[str, list[dict]] = {}
    for row in _fetch_guidelines_for_gene_phenotype(
        _THIOPURINE_JOINT_GENE_LABEL, _COMPOUND_IM_PHENOTYPE, reference_engine
    ):
        compound_rows_by_drug.setdefault(row["drug"], []).append(row)

    superseded_drugs: set[str] = set()
    joint_alerts: list[PrescribingAlert] = []
    for drug in sorted(both_im_drugs):
        guideline_rows = compound_rows_by_drug.get(drug, [])
        if not guideline_rows:
            # No compound-IM row for this drug: leave the single-gene alerts in
            # place rather than dropping guidance we cannot replace.
            logger.warning(
                "pgx_thiopurine_compound_im_row_missing",
                drug=drug,
                genes=list(_THIOPURINE_JOINT_GENES),
            )
            continue
        superseded_drugs.add(drug)

        # Merge provenance across every superseded component alert so a partial or
        # conservative joint call keeps the reasons and loci behind its uncertainty.
        components = im_by_drug[drug]["TPMT"] + im_by_drug[drug]["NUDT15"]
        partial = any(c.call_confidence == CallConfidence.PARTIAL for c in components)
        conservative = any(c.conservative_alert for c in components)
        involved_rsids = sorted({rsid for c in components for rsid in c.involved_rsids})
        indeterminate_alleles = sorted({a for c in components for a in c.indeterminate_alleles})
        indeterminate_allele_rsids: dict[str, list[str]] = {}
        for component in components:
            for allele, rsids in component.indeterminate_allele_rsids.items():
                indeterminate_allele_rsids.setdefault(allele, []).extend(rsids)
        indeterminate_allele_rsids = {
            allele: sorted(set(rsids)) for allele, rsids in indeterminate_allele_rsids.items()
        }
        component_notes = dict.fromkeys(c.confidence_note for c in components if c.confidence_note)
        confidence_note = " ".join(
            [
                "Joint TPMT/NUDT15 dosing: Intermediate Metabolizer at both genes "
                "(compound intermediate metabolizer).",
                *component_notes,
            ]
        ).strip()
        tpmt_rep = im_by_drug[drug]["TPMT"][0]
        nudt15_rep = im_by_drug[drug]["NUDT15"][0]
        diplotype = f"TPMT {tpmt_rep.diplotype} + NUDT15 {nudt15_rep.diplotype}"

        # One joint alert per compound guideline row, so a duplicate row surfaces
        # as a duplicate alert rather than being silently consolidated away.
        for guideline in guideline_rows:
            joint_alerts.append(
                PrescribingAlert(
                    gene=_THIOPURINE_JOINT_GENE_LABEL,
                    drug=drug,
                    diplotype=diplotype,
                    phenotype=_COMPOUND_IM_PHENOTYPE,
                    recommendation=guideline["recommendation"],
                    classification=guideline["classification"],
                    guideline_url=guideline["guideline_url"],
                    call_confidence=CallConfidence.PARTIAL if partial else CallConfidence.COMPLETE,
                    confidence_note=confidence_note,
                    evidence_level=assign_cpic_evidence_level(guideline["classification"]),
                    involved_rsids=involved_rsids,
                    coverage_assessed=tpmt_rep.coverage_assessed + nudt15_rep.coverage_assessed,
                    coverage_total=tpmt_rep.coverage_total + nudt15_rep.coverage_total,
                    indeterminate_alleles=indeterminate_alleles,
                    indeterminate_allele_rsids=indeterminate_allele_rsids,
                    conservative_alert=conservative,
                )
            )
        logger.info(
            "pgx_thiopurine_compound_im_alert",
            drug=drug,
            tpmt_diplotype=tpmt_rep.diplotype,
            nudt15_diplotype=nudt15_rep.diplotype,
        )

    if not joint_alerts:
        return alerts

    remaining = [
        alert
        for alert in alerts
        if not (
            alert.drug in superseded_drugs
            and alert.gene in _THIOPURINE_JOINT_GENES
            and alert.phenotype == _SINGLE_IM_PHENOTYPE
        )
    ]
    remaining.extend(joint_alerts)
    return remaining


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

        conservative = _infer_conservative_phenotype(result, reference_engine)
        alert_phenotype = conservative.phenotype if conservative else result.phenotype
        alert_activity_score = (
            conservative.activity_score if conservative else result.activity_score
        )
        alert_ehr_notation = conservative.ehr_notation if conservative else result.ehr_notation
        confidence_note = result.confidence_note
        if conservative is not None:
            note = _conservative_alert_note(result, conservative)
            if note not in confidence_note:
                confidence_note = f"{confidence_note} {note}".strip()

        # Look up matching guidelines (AS-keyed where CPIC splits a phenotype
        # band by activity score, e.g. DPYD — #1993).
        guidelines = _fetch_guidelines_for_gene_phenotype(
            result.gene, alert_phenotype, reference_engine, activity_score=alert_activity_score
        )

        if not guidelines:
            # No guideline row matched this callable, confidently-called
            # phenotype. Distinguish a likely *missing* row (the gene is
            # otherwise covered) from a gene with no guidelines at all, so a
            # silently-dropped actionable pair becomes a visible warning
            # rather than an invisible gap. See issue #23.
            covered_phenotypes = _fetch_guideline_phenotypes_for_gene(
                result.gene, reference_engine
            )
            if covered_phenotypes:
                logger.warning(
                    "pgx_phenotype_no_guideline_row",
                    gene=result.gene,
                    phenotype=alert_phenotype,
                    diplotype=result.diplotype,
                    covered_phenotypes=sorted(covered_phenotypes),
                )
            else:
                logger.debug(
                    "pgx_no_guidelines",
                    gene=result.gene,
                    phenotype=result.phenotype,
                )
            continue

        for guideline in guidelines:
            if is_prescribing_alert_withheld(result.gene, guideline["drug"]):
                logger.warning(
                    "pgx_alert_withheld_insufficient_clinical_evidence",
                    withheld_alert_count=1,
                )
                continue

            # #2169: an untyped defining marker can leave the plausible genotypes
            # spanning opposite shipped recommendations (CYP3A5/tacrolimus: increase
            # the starting dose for an expresser, keep label dosing for a
            # non-expresser). Emitting either would assert a genotype that was never
            # typed, and for a narrow-therapeutic-index drug both errors are real, so
            # withhold this gene-drug pair instead of guessing a direction.
            if result.gene in WITHHOLD_CROSS_DIRECTION_GENES and (
                _untyped_marker_spans_conflicting_recommendations(
                    result, guideline["drug"], guideline["recommendation"], reference_engine
                )
            ):
                logger.info(
                    "pgx_alert_withheld_undetermined_direction",
                    gene=result.gene,
                    drug=guideline["drug"],
                    diplotype=result.diplotype,
                    indeterminate_alleles=result.indeterminate_alleles,
                    confidence_note=result.confidence_note,
                )
                continue

            evidence_level = assign_cpic_evidence_level(guideline["classification"])

            alert = PrescribingAlert(
                gene=result.gene,
                drug=guideline["drug"],
                diplotype=result.diplotype,
                phenotype=alert_phenotype,
                recommendation=guideline["recommendation"],
                classification=guideline["classification"],
                guideline_url=guideline["guideline_url"],
                call_confidence=result.call_confidence,
                confidence_note=confidence_note,
                evidence_level=evidence_level,
                activity_score=alert_activity_score,
                ehr_notation=alert_ehr_notation,
                involved_rsids=sorted(result.involved_rsids),
                coverage_assessed=result.coverage_assessed,
                coverage_total=result.defining_rsid_count,
                indeterminate_alleles=result.indeterminate_alleles,
                indeterminate_allele_rsids=result.indeterminate_allele_rsids,
                conservative_alert=conservative is not None,
                called_phenotype=result.phenotype if conservative is not None else None,
                called_activity_score=result.activity_score if conservative is not None else None,
                called_ehr_notation=result.ehr_notation if conservative is not None else None,
                conservative_diplotype=conservative.diplotype if conservative else None,
                conservative_allele=conservative.allele if conservative else None,
            )
            alerts.append(alert)

            logger.info(
                "pgx_prescribing_alert",
                gene=result.gene,
                drug=guideline["drug"],
                phenotype=alert_phenotype,
                recommendation=guideline["recommendation"],
                classification=guideline["classification"],
                call_confidence=result.call_confidence.value,
                evidence_level=evidence_level,
                conservative_alert=conservative is not None,
            )

    # Collapse the CPIC-joint thiopurine (TPMT, NUDT15) compound-IM case into one
    # recommendation before sorting, so a double-Intermediate sample gets CPIC's
    # single 20-50% band instead of two conflicting single-gene alerts (#2007).
    alerts = _apply_thiopurine_joint_phenotype(alerts, reference_engine)

    # Sort by gene, then drug for deterministic output
    alerts.sort(key=lambda a: (a.gene, a.drug))
    return alerts


def _build_finding_text(alert: PrescribingAlert) -> str:
    """Build a human-readable finding_text for a prescribing alert.

    Format: "{Gene} {diplotype}: {phenotype} -- {drug}: {recommendation}"
    If call confidence is Partial, appends a provisional note.
    """
    diplotype = alert.diplotype
    if alert.conservative_alert and alert.conservative_diplotype:
        diplotype = f"{diplotype} (possible {alert.conservative_diplotype})"

    text = f"{alert.gene} {diplotype}: {alert.phenotype} -- {alert.drug}: {alert.recommendation}"
    if alert.conservative_alert:
        text += " (conservative partial call -- see call confidence note)"
    elif alert.call_confidence == CallConfidence.PARTIAL:
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
            "indeterminate_allele_rsids": alert.indeterminate_allele_rsids,
        }
        gene_caveat = _GENE_INTERPRETATION_CAVEATS.get(alert.gene)
        if gene_caveat:
            detail["gene_caveat"] = gene_caveat
        if alert.conservative_alert:
            detail.update(
                {
                    "conservative_alert": True,
                    "called_phenotype": alert.called_phenotype,
                    "called_activity_score": alert.called_activity_score,
                    "called_ehr_notation": alert.called_ehr_notation,
                    "conservative_diplotype": alert.conservative_diplotype,
                    "conservative_phenotype": alert.phenotype,
                    "conservative_activity_score": alert.activity_score,
                    "conservative_allele": alert.conservative_allele,
                }
            )

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
        # Clear prior patient-presentable prescribing alerts BEFORE (re)inserting so
        # a rerun never duplicates them, and a rerun that now yields none doesn't
        # leave stale ones (#481). Held pairs deliberately remain as audit-only
        # source/custom records, including across reanalysis (#2019). The delete
        # still runs when ``rows`` is empty, so unrelated alert pairs retain the
        # sibling store_* empty-path clear behavior.
        conn.execute(
            sa.delete(findings).where(
                findings.c.module == "pharmacogenomics",
                findings.c.category == "prescribing_alert",
                patient_visible_finding_clause(findings.c),
            )
        )
        if rows:
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
    """OR bit 6 (CPIC, value 64) into annotation_coverage for CPIC-assessed variants.

    After the pharmacogenomics module runs, every variant that participated
    in CPIC star-allele assessment (i.e. its rsid appears in
    ``assessed_rsids`` of any :class:`StarAlleleResult`) gets bit 6 set in
    its ``annotation_coverage`` column in ``annotated_variants``. Older
    manually-constructed results without ``assessed_rsids`` fall back to
    ``involved_rsids``.

    Variants not assessed for any CPIC gene leave bit 6 unset.

    Args:
        star_allele_results: Output from :func:`call_all_star_alleles`.
        sample_engine: SQLAlchemy engine for the sample database.

    Returns:
        Number of variants updated.
    """
    # Collect all unique rsids that were assessed by star-allele calls. Keep
    # involved_rsids as a fallback for tests or callers that construct legacy
    # StarAlleleResult values directly.
    cpic_rsids: set[str] = set()
    for result in star_allele_results:
        cpic_rsids.update(result.assessed_rsids or result.involved_rsids)

    if not cpic_rsids:
        return 0

    rsid_list = sorted(cpic_rsids)
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
        cpic_rsids=len(cpic_rsids),
        rows_updated=updated,
    )
    return updated

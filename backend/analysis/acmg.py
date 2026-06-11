"""DRAFT ACMG/AMP variant classification engine (SW-F1 / roadmap #13).

An InterVar-style (Li 2017, PMID 28132688) reimplementation of the subset of the
2015 ACMG/AMP sequence-variant criteria (Richards 2015, PMID 25741868) that is
*computable from this app's array + annotation evidence*, combined with the
Tavtigian Bayesian **point** system (Tavtigian 2018 PMID 29300386 / 2020 PMID
31843900) and a PVS1 evaluation following the ClinGen SVI Abou-Tayoun decision
tree (Abou-Tayoun 2018, PMID 30192042). PP3/BP4 reuse the Pejaver-2022 REVEL
calibration already implemented in :mod:`backend.analysis.insilico_tiers`.

**This engine is DRAFT and non-clinical.** It is additive context only: it NEVER
mutates a finding's ``evidence_level`` or ``clinvar_significance`` and never
auto-upgrades an existing classification. Criteria that need data array genotyping
cannot provide are deliberately *not assessed* (see ``UNASSESSABLE_CRITERIA``):

* functional (PS3/BS3), de novo (PS2/PM6), segregation (PP1/BS4), case-control
  (PS4), reputable-source (PP5/BP6 — also withdrawn by ClinGen SVI, Biesecker
  2018), allelic-data (BP2);
* **PM3 (in trans with a pathogenic variant) is never assessable from unphased
  array genotypes** (plan SW-F1);
* PS1/PM5 (same / different amino-acid change as a known pathogenic variant) and
  PM1 (mutational hotspot / functional domain) and BP1/BP3 (gene mechanism /
  repeat region) need curated amino-acid- or domain-level datasets this engine
  does not ship.

Computed criteria: PVS1, PM2, PM4, PP2, PP3, BA1, BS1, BP4, BP7.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from backend.analysis.insilico_tiers import is_missense_consequence, revel_to_acmg_tier
from backend.disclaimers import ACMG_DRAFT_CONTEXT_ONLY

if TYPE_CHECKING:
    import sqlalchemy as sa

CITATION_PMIDS = [
    "25741868",  # Richards 2015 — ACMG/AMP guidelines
    "28132688",  # Li 2017 — InterVar
    "29300386",  # Tavtigian 2018 — Bayesian framework
    "31843900",  # Tavtigian 2020 — point system
    "30192042",  # Abou-Tayoun 2018 — PVS1 decision tree
    "36413997",  # Pejaver 2022 — PP3/BP4 REVEL calibration
]

# Criteria this engine cannot evaluate from array + annotation data, with the
# reason — surfaced so a draft "Uncertain significance" is never mistaken for a
# complete assessment.
UNASSESSABLE_CRITERIA: dict[str, str] = {
    "PS1": "needs a curated same-amino-acid-change pathogenic dataset",
    "PS2": "de novo — needs parental genotypes",
    "PS3": "functional studies — not available",
    "PS4": "case-control prevalence — not available",
    "PM1": "mutational hotspot / functional domain — needs domain annotation",
    "PM3": "in trans with a pathogenic variant — unphased array cannot assess phase",
    "PM5": "different amino-acid change at a pathogenic residue — needs AA dataset",
    "PM6": "assumed de novo — needs parental data",
    "PP1": "co-segregation — needs family data",
    "PP4": "phenotype specificity — not computed here",
    "PP5": "reputable source — withdrawn by ClinGen SVI (Biesecker 2018)",
    "BS3": "functional studies — not available",
    "BS4": "lack of segregation — needs family data",
    "BP1": "missense where only truncating is pathogenic — needs gene mechanism list",
    "BP2": "observed in trans/cis with pathogenic — needs phase",
    "BP3": "in-frame indel in a repeat region — needs repeat annotation",
    "BP5": "alternate molecular cause — not computed here",
    "BP6": "reputable source benign — withdrawn by ClinGen SVI (Biesecker 2018)",
}

# ── Tavtigian point system (2018/2020) ────────────────────────────────────────
# Points are proportional to log(odds of pathogenicity); pathogenic criteria add,
# benign criteria subtract. Strength → magnitude:
_STRENGTH_POINTS = {
    "Supporting": 1,
    "Moderate": 2,
    "Strong": 4,
    "Very Strong": 8,
    "Standalone": 8,  # BA1 — treated as stand-alone benign (see classify_points)
}

# Tavtigian 2020 combined-point classification thresholds.
POINTS_PATHOGENIC = 10
POINTS_LIKELY_PATHOGENIC = 6
POINTS_LIKELY_BENIGN = -1
POINTS_BENIGN = -7

PATHOGENIC = "Pathogenic"
LIKELY_PATHOGENIC = "Likely pathogenic"
UNCERTAIN = "Uncertain significance"
LIKELY_BENIGN = "Likely benign"
BENIGN = "Benign"

# ── Criterion thresholds (general defaults; documented + cited) ────────────────
# BA1: MAF > 5% is stand-alone benign (Ghosh 2018, ClinGen SVI; PMID 30311383).
BA1_AF_MIN = 0.05
# BS1: "allele frequency greater than expected for the disorder" — a general 1%
# default (gene/disease-specific thresholds would be stricter; flagged as general).
BS1_AF_MIN = 0.01
# PM2_Supporting: absent or extremely low frequency (ClinGen SVI downgraded PM2 to
# Supporting). Applied when popmax is absent or below 0.01%.
PM2_AF_MAX = 1e-4
# PP2: missense in a missense-constrained gene (gnomAD mis_z ≥ 3.09 ≈ top decile).
PP2_MISZ_MIN = 3.09

# SO consequence tokens.
_LOF_TOKENS = frozenset(
    {
        "transcript_ablation",
        "stop_gained",
        "frameshift_variant",
        "splice_acceptor_variant",
        "splice_donor_variant",
        "start_lost",
    }
)
_SPLICE_NULL_TOKENS = frozenset({"splice_acceptor_variant", "splice_donor_variant"})
_NONSENSE_FS_TOKENS = frozenset({"transcript_ablation", "stop_gained", "frameshift_variant"})
# PM4 — protein-length-changing without LoF/NMD.
_INFRAME_TOKENS = frozenset({"inframe_insertion", "inframe_deletion", "stop_lost"})
_SYNONYMOUS_TOKEN = "synonymous_variant"
_SPLICE_REGION_TOKEN = "splice_region_variant"


def _tokens(consequence: str | None) -> set[str]:
    if not consequence:
        return set()
    out: set[str] = set()
    for part in consequence.replace(",", "&").replace(";", "&").split("&"):
        tok = part.strip().lower()
        if tok:
            out.add(tok)
    return out


@dataclass(frozen=True)
class AcmgCriterion:
    """One applied ACMG/AMP criterion with its Tavtigian point contribution."""

    code: str  # e.g. "PVS1", "PM2", "BP4"
    direction: str  # "pathogenic" | "benign"
    strength: str  # "Supporting" | "Moderate" | "Strong" | "Very Strong" | "Standalone"
    points: int  # signed: +pathogenic, -benign
    rationale: str


@dataclass
class AcmgEvidence:
    """Per-variant inputs the engine reads (all already annotated)."""

    rsid: str | None = None
    gene_symbol: str | None = None
    consequence: str | None = None
    gnomad_af_global: float | None = None
    gnomad_af_popmax: float | None = None
    revel: float | None = None
    # Gene-level context from an explicit, mechanism-specific source.
    gene_lof_mechanism: bool = False  # LoF is a plausible disease mechanism for the gene
    gene_missense_z: float | None = None
    clinvar_significance: str | None = None


@dataclass
class AcmgResult:
    """A DRAFT ACMG/AMP classification for one variant."""

    classification: str
    points: int
    criteria: list[AcmgCriterion] = field(default_factory=list)
    unassessable: dict[str, str] = field(default_factory=dict)
    is_draft: bool = True
    note: str = ACMG_DRAFT_CONTEXT_ONLY
    pmid_citations: list[str] = field(default_factory=lambda: list(CITATION_PMIDS))


def _effective_af(ev: AcmgEvidence) -> float | None:
    """Population-max AF, falling back to global (the rarity denominator, F15)."""
    if ev.gnomad_af_popmax is not None:
        return ev.gnomad_af_popmax
    return ev.gnomad_af_global


def _points_for(direction: str, strength: str) -> int:
    mag = _STRENGTH_POINTS[strength]
    return mag if direction == "pathogenic" else -mag


# ── PVS1 — Abou-Tayoun (2018) decision tree ───────────────────────────────────
# Gated on LoF being a plausible disease mechanism for the gene. Strength follows
# the modal Abou-Tayoun outcome per variant type: nonsense/frameshift → Very Strong
# (NMD is the common fate and is *assumed* here — array annotation lacks the
# transcript structure to confirm last-exon escape), canonical ±1,2 splice → Strong
# (exact splice/NMD outcome unconfirmable), and start-loss → Moderate. Every
# rationale states the assumption so a reviewer can downgrade where it doesn't hold.
def criterion_pvs1(ev: AcmgEvidence) -> AcmgCriterion | None:
    toks = _tokens(ev.consequence)
    if not (toks & _LOF_TOKENS):
        return None
    if not ev.gene_lof_mechanism:
        # PVS1 requires LoF to be a known mechanism of disease for the gene.
        return None
    if toks & _NONSENSE_FS_TOKENS:
        return AcmgCriterion(
            "PVS1",
            "pathogenic",
            "Very Strong",
            _points_for("pathogenic", "Very Strong"),
            "Predicted loss-of-function (nonsense/frameshift) in a gene where LoF is "
            "a plausible disease mechanism. Assumes nonsense-mediated decay; array "
            "annotation lacks the transcript structure to confirm last-exon escape, "
            "which would downgrade PVS1.",
        )
    if toks & _SPLICE_NULL_TOKENS:
        return AcmgCriterion(
            "PVS1",
            "pathogenic",
            "Strong",
            _points_for("pathogenic", "Strong"),
            "Canonical (±1,2) splice variant in a gene where LoF is a plausible "
            "disease mechanism; applied at Strong because the exact splice outcome "
            "and NMD cannot be confirmed from array annotation.",
        )
    return AcmgCriterion(  # start_lost
        "PVS1",
        "pathogenic",
        "Moderate",
        _points_for("pathogenic", "Moderate"),
        "Initiation-codon (start-loss) variant in a gene where loss of function is a "
        "disease mechanism; applied at Moderate per the Abou-Tayoun tree.",
    )


def criterion_pm2(ev: AcmgEvidence) -> AcmgCriterion | None:
    af = _effective_af(ev)
    if af is None or af < PM2_AF_MAX:
        where = "absent from gnomAD" if af is None else f"popmax AF {af:.2g} < 0.01%"
        return AcmgCriterion(
            "PM2",
            "pathogenic",
            "Supporting",
            _points_for("pathogenic", "Supporting"),
            f"Rare/absent in population databases ({where}); applied at Supporting "
            "per the ClinGen SVI down-weighting of PM2.",
        )
    return None


def criterion_pm4(ev: AcmgEvidence) -> AcmgCriterion | None:
    if _tokens(ev.consequence) & _INFRAME_TOKENS:
        return AcmgCriterion(
            "PM4",
            "pathogenic",
            "Moderate",
            _points_for("pathogenic", "Moderate"),
            "Protein-length change (in-frame indel or stop-loss). Note: a repeat-"
            "region exception (BP3) cannot be checked from available annotation.",
        )
    return None


def criterion_pp2(ev: AcmgEvidence) -> AcmgCriterion | None:
    if (
        is_missense_consequence(ev.consequence)
        and ev.gene_missense_z is not None
        and ev.gene_missense_z >= PP2_MISZ_MIN
    ):
        return AcmgCriterion(
            "PP2",
            "pathogenic",
            "Supporting",
            _points_for("pathogenic", "Supporting"),
            f"Missense in a missense-constrained gene (gnomAD mis_z "
            f"{ev.gene_missense_z:.2f} ≥ {PP2_MISZ_MIN}).",
        )
    return None


def criterion_pp3_bp4(ev: AcmgEvidence) -> AcmgCriterion | None:
    tier = revel_to_acmg_tier(ev.revel, is_missense=is_missense_consequence(ev.consequence))
    if tier is None:
        return None
    direction = "pathogenic" if tier.criterion == "PP3" else "benign"
    return AcmgCriterion(
        tier.criterion,
        direction,
        tier.strength,
        _points_for(direction, tier.strength),
        f"REVEL {ev.revel:.3f} → {tier.tier} (Pejaver 2022 calibration).",
    )


def criterion_ba1(ev: AcmgEvidence) -> AcmgCriterion | None:
    af = _effective_af(ev)
    if af is not None and af > BA1_AF_MIN:
        return AcmgCriterion(
            "BA1",
            "benign",
            "Standalone",
            _points_for("benign", "Standalone"),
            f"Allele frequency {af:.2%} > 5% — stand-alone benign (Ghosh 2018).",
        )
    return None


def criterion_bs1(ev: AcmgEvidence) -> AcmgCriterion | None:
    af = _effective_af(ev)
    if af is not None and BS1_AF_MIN < af <= BA1_AF_MIN:
        return AcmgCriterion(
            "BS1",
            "benign",
            "Strong",
            _points_for("benign", "Strong"),
            f"Allele frequency {af:.2%} > 1% — higher than generally expected for a "
            "rare Mendelian disorder (general default threshold).",
        )
    return None


def criterion_bp7(ev: AcmgEvidence) -> AcmgCriterion | None:
    toks = _tokens(ev.consequence)
    if _SYNONYMOUS_TOKEN in toks and _SPLICE_REGION_TOKEN not in toks:
        return AcmgCriterion(
            "BP7",
            "benign",
            "Supporting",
            _points_for("benign", "Supporting"),
            "Synonymous variant outside the splice region (no predicted splice "
            "impact from the annotated consequence).",
        )
    return None


_EVALUATORS = (
    criterion_pvs1,
    criterion_pm2,
    criterion_pm4,
    criterion_pp2,
    criterion_pp3_bp4,
    criterion_ba1,
    criterion_bs1,
    criterion_bp7,
)


def classify_points(points: int, *, standalone_benign: bool = False) -> str:
    """Map a Tavtigian point total to an ACMG/AMP category.

    ``standalone_benign`` forces Benign for a BA1 hit regardless of the point sum
    (BA1 is a stand-alone benign criterion in the ACMG/AMP framework).
    """
    if standalone_benign:
        return BENIGN
    if points >= POINTS_PATHOGENIC:
        return PATHOGENIC
    if points >= POINTS_LIKELY_PATHOGENIC:
        return LIKELY_PATHOGENIC
    if points <= POINTS_BENIGN:
        return BENIGN
    if points <= POINTS_LIKELY_BENIGN:
        return LIKELY_BENIGN
    return UNCERTAIN


def classify_acmg(ev: AcmgEvidence) -> AcmgResult:
    """Run all computable criteria on one variant → a DRAFT ACMG/AMP classification."""
    criteria = [c for c in (fn(ev) for fn in _EVALUATORS) if c is not None]
    total = sum(c.points for c in criteria)
    standalone_benign = any(c.code == "BA1" for c in criteria)
    classification = classify_points(total, standalone_benign=standalone_benign)
    return AcmgResult(
        classification=classification,
        points=total,
        criteria=criteria,
        unassessable=dict(UNASSESSABLE_CRITERIA),
    )


# ── Sample-level assessment ───────────────────────────────────────────────────
# The candidate set is deliberately bounded to *notable* variants — anything
# ClinVar-listed, any predicted loss-of-function or in-frame indel, and missense
# variants that are PP3-eligible (REVEL ≥ 0.644). The mass of low-REVEL rare
# missense (which would all draft to VUS/Likely-benign) is excluded from the batch
# to keep the endpoint bounded and useful; classify_acmg() still handles them for
# per-variant callers.
_PP3_ELIGIBLE_REVEL = 0.644
_DEFAULT_MAX_VARIANTS = 2000


def _is_candidate(
    consequence: str | None, clinvar_significance: str | None, revel: float | None
) -> bool:
    if clinvar_significance:
        return True
    toks = _tokens(consequence)
    if toks & (_LOF_TOKENS | _INFRAME_TOKENS):
        return True
    return (
        is_missense_consequence(consequence) and revel is not None and revel >= _PP3_ELIGIBLE_REVEL
    )


def assess_sample_acmg(
    sample_engine: sa.Engine,
    reference_engine: sa.Engine,
    *,
    max_variants: int = _DEFAULT_MAX_VARIANTS,
) -> dict[str, Any]:
    """DRAFT ACMG/AMP classification for the notable variants in a sample.

    Read-only. Returns ``{"variants": [...], "truncated": bool, "total_candidates": int}``.
    Never mutates any finding. Each variant row carries the draft classification,
    the Tavtigian point total, the applied criteria, and the original ClinVar
    significance for side-by-side context (the draft never overrides ClinVar).
    """
    import sqlalchemy as sa

    from backend.analysis.gene_constraint import lookup_gene_constraints
    from backend.db.tables import annotated_variants as av

    eff_af = sa.func.coalesce(av.c.gnomad_af_popmax, av.c.gnomad_af_global)
    stmt = (
        sa.select(
            av.c.rsid,
            av.c.gene_symbol,
            av.c.consequence,
            av.c.gnomad_af_global,
            av.c.gnomad_af_popmax,
            av.c.revel,
            av.c.clinvar_significance,
        )
        .where(
            av.c.gene_symbol.isnot(None),
            sa.or_(
                av.c.clinvar_significance.isnot(None),
                eff_af.is_(None),
                eff_af < 0.01,
            ),
        )
        .order_by(av.c.rsid)
    )
    with sample_engine.connect() as conn:
        rows = conn.execute(stmt).fetchall()

    candidates = [r for r in rows if _is_candidate(r.consequence, r.clinvar_significance, r.revel)]
    total_candidates = len(candidates)
    truncated = total_candidates > max_variants
    candidates = candidates[:max_variants]

    genes = sorted({r.gene_symbol for r in candidates if r.gene_symbol})
    constraints = lookup_gene_constraints(reference_engine, genes) if genes else {}

    out: list[dict[str, Any]] = []
    for r in candidates:
        gene = r.gene_symbol
        constraint = constraints.get(gene or "")
        # The sample endpoint currently has no curated disease-mechanism or
        # dosage-sensitivity source. gnomAD constraint and ClinGen gene-disease
        # validity are context-only and must not substitute for PVS1 LoF
        # mechanism evidence.
        lof_mechanism = False
        evidence = AcmgEvidence(
            rsid=r.rsid,
            gene_symbol=gene,
            consequence=r.consequence,
            gnomad_af_global=r.gnomad_af_global,
            gnomad_af_popmax=r.gnomad_af_popmax,
            revel=r.revel,
            gene_lof_mechanism=lof_mechanism,
            gene_missense_z=constraint.get("mis_z") if constraint else None,
            clinvar_significance=r.clinvar_significance,
        )
        result = classify_acmg(evidence)
        out.append(
            {
                "rsid": r.rsid,
                "gene_symbol": gene,
                "consequence": r.consequence,
                "clinvar_significance": r.clinvar_significance,
                "acmg_classification": result.classification,
                "points": result.points,
                "is_draft": True,
                "criteria": [
                    {
                        "code": c.code,
                        "direction": c.direction,
                        "strength": c.strength,
                        "points": c.points,
                        "rationale": c.rationale,
                    }
                    for c in result.criteria
                ],
                "note": result.note,
                "pmid_citations": result.pmid_citations,
            }
        )
    return {
        "variants": out,
        "truncated": truncated,
        "total_candidates": total_candidates,
    }

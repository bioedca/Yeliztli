"""Metabolic disease PRS — type 2 diabetes & obesity/BMI (SW-B5).

Scores the sample against the bundle-eligible PGS Catalog scores selected by the
SW-B4 bridge (T2D ``PGS000713``; multi-ancestry BMI ``PGS005198``), reports SNP
coverage and an ancestry-mismatch warning, and surfaces a small set of
established **anchor SNPs** (directly-typed, large-effect common variants) for
interpretability.

Honest-coverage posture: a genome-wide polygenic score requires dense genotype
coverage. On un-imputed direct-to-consumer array data only ~35–60% of a score's
variants are typed, which is below the threshold for a reliable percentile, so
the polygenic **percentile is withheld** (coverage is reported instead) until
genotype imputation (Wave C, separately-scheduled) lands. The anchor SNPs remain
fully interpretable because they are individually typed. This mirrors the
project's "calibration is not accuracy / report what you can defend" stance.

Findings: ``module="metabolic"`` with ``category="prs"`` (one per trait, coverage
disclosed) and ``category="anchor_snp"`` (the established single-variant anchors).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

import sqlalchemy as sa
import structlog

from backend.analysis.evidence import EVIDENCE_MODERATE
from backend.analysis.pgs_bridge import build_trait_weight_set, load_pgs_registry
from backend.analysis.prs import (
    PRSResult,
    _count_effect_allele,
    run_prs,
    store_prs_findings,
)
from backend.db.tables import annotated_variants, findings

logger = structlog.get_logger(__name__)

MODULE_NAME = "metabolic"
METABOLIC_TRAITS = ("type_2_diabetes", "body_mass_index")

TRAIT_LABELS = {
    "type_2_diabetes": "Type 2 diabetes",
    "body_mass_index": "Body mass index / obesity",
}

# Established large-effect common variants, directly typed on standard arrays.
# These are an interpretable anchor — NOT the polygenic score — each a replicated
# genome-wide-significant locus.
ANCHOR_SNPS: dict[str, list[dict]] = {
    "type_2_diabetes": [
        {
            "rsid": "rs7903146",
            "gene": "TCF7L2",
            "effect_allele": "T",
            "other_allele": "C",
            "summary": (
                "TCF7L2 — the strongest common type 2 diabetes association; each T "
                "allele raises risk roughly 1.4-fold."
            ),
            "pmid": "16415884",
        },
    ],
    "body_mass_index": [
        {
            "rsid": "rs9939609",
            "gene": "FTO",
            "effect_allele": "A",
            "other_allele": "T",
            "summary": (
                "FTO — the strongest common BMI/adiposity locus; each A allele adds "
                "~0.4 kg/m² on average."
            ),
            "pmid": "17434869",
        },
        {
            "rsid": "rs17782313",
            "gene": "MC4R",
            "effect_allele": "C",
            "other_allele": "T",
            "summary": (
                "MC4R — a replicated common adiposity locus; each C allele modestly raises BMI."
            ),
            "pmid": "18454148",
        },
    ],
}

COVERAGE_CONTEXT = (
    "Genome-wide polygenic scores need dense genotype coverage. On un-imputed "
    "array data only a fraction of a score's variants are typed, so the polygenic "
    "percentile is withheld and only coverage is reported; reliable percentiles "
    "require genotype imputation (a separately-scheduled feature). The anchor "
    "variants are individually typed and interpretable on their own."
)


@dataclass
class AnchorResult:
    """A single anchor-SNP result for a sample."""

    rsid: str
    gene: str
    effect_allele: str
    genotype: str | None
    dosage: int
    summary: str
    pmid: str
    trait: str


@dataclass
class MetabolicResult:
    """Aggregated metabolic PRS + anchor results."""

    prs_results: list[PRSResult] = field(default_factory=list)
    anchors: list[AnchorResult] = field(default_factory=list)


# ── Anchor SNP scoring ─────────────────────────────────────────────────────


def score_anchor_snps(sample_engine: sa.Engine, trait: str) -> list[AnchorResult]:
    """Resolve genotype + effect-allele dosage for a trait's anchor SNPs.

    Anchors are single, directly-typed variants reported with their raw
    genotype, so the effect-allele dosage is a **literal** count of that
    genotype (always 0/1/2 when typed) rather than the strand-aware,
    palindrome-dropping PRS matcher — the latter would return an indeterminate
    ``None`` for palindromic anchors (e.g. FTO rs9939609 is A/T near MAF 0.5)
    and drop the most informative locus. Strand orientation is a general array
    caveat covered by the module disclaimer; the displayed genotype keeps it
    transparent.
    """
    anchors = ANCHOR_SNPS.get(trait, [])
    if not anchors:
        return []
    rsids = [a["rsid"] for a in anchors]
    with sample_engine.connect() as conn:
        rows = conn.execute(
            sa.select(
                annotated_variants.c.rsid,
                annotated_variants.c.genotype,
            ).where(annotated_variants.c.rsid.in_(rsids))
        ).fetchall()
    geno = {r.rsid: r.genotype for r in rows}

    out: list[AnchorResult] = []
    for a in anchors:
        genotype = geno.get(a["rsid"])
        out.append(
            AnchorResult(
                rsid=a["rsid"],
                gene=a["gene"],
                effect_allele=a["effect_allele"],
                genotype=genotype,
                dosage=_count_effect_allele(genotype, a["effect_allele"]),
                summary=a["summary"],
                pmid=a["pmid"],
                trait=trait,
            )
        )
    return out


# ── Pipeline ───────────────────────────────────────────────────────────────


def run_metabolic_prs(
    sample_engine: sa.Engine,
    pgs_engine: sa.Engine | None,
    inferred_ancestry: str | None = None,
    top_ancestry_fraction: float | None = None,
) -> MetabolicResult:
    """Run T2D & BMI PRS (uncalibrated, coverage-reported) + anchor SNPs.

    When ``pgs_engine`` is None (score DB not installed) the polygenic results are
    empty but anchor SNPs are still resolved.
    """
    result = MetabolicResult()
    registry = load_pgs_registry()

    for trait in METABOLIC_TRAITS:
        result.anchors.extend(score_anchor_snps(sample_engine, trait))

        if pgs_engine is None:
            continue
        weight_set = build_trait_weight_set(
            pgs_engine, trait, inferred_ancestry, registry=registry
        )
        if weight_set is None:
            logger.info("metabolic_score_unavailable", trait=trait)
            continue
        # calibrated stays False on the bridge weight set: percentile is withheld
        # (coverage on un-imputed arrays is too low to calibrate reliably).
        prs = run_prs(
            weight_set,
            sample_engine,
            inferred_ancestry=inferred_ancestry,
            top_ancestry_fraction=top_ancestry_fraction,
            n_bootstrap=0,
        )
        result.prs_results.append(prs)
        logger.info(
            "metabolic_prs_trait",
            trait=trait,
            pgs_id=prs.pgs_id,
            coverage=round(prs.coverage_fraction, 3),
            snps_used=prs.snps_used,
            snps_total=prs.snps_total,
        )

    return result


def store_metabolic_findings(result: MetabolicResult, sample_engine: sa.Engine) -> int:
    """Store metabolic PRS + anchor findings. Returns the total inserted."""
    # PRS findings: surface even below the coverage threshold (transparency).
    n = store_prs_findings(
        result.prs_results, sample_engine, module=MODULE_NAME, store_insufficient=True
    )

    # Anchor SNP findings (replace previous on re-run).
    anchor_rows: list[dict] = []
    for a in result.anchors:
        if a.genotype is None:
            continue  # not typed in this sample → nothing to report
        dosage_text = {0: "no copies", 1: "1 copy", 2: "2 copies"}.get(a.dosage, f"{a.dosage}")
        anchor_rows.append(
            {
                "module": MODULE_NAME,
                "category": "anchor_snp",
                "evidence_level": EVIDENCE_MODERATE,
                "gene_symbol": a.gene,
                "rsid": a.rsid,
                "finding_text": (
                    f"{a.gene} {a.rsid}: {dosage_text} of the {a.effect_allele} "
                    f"effect allele ({a.genotype}) — Research Use Only"
                ),
                "pmid_citations": json.dumps([a.pmid]),
                "detail_json": json.dumps(
                    {
                        "trait": a.trait,
                        "trait_label": TRAIT_LABELS.get(a.trait, a.trait),
                        "gene": a.gene,
                        "rsid": a.rsid,
                        "effect_allele": a.effect_allele,
                        "genotype": a.genotype,
                        "dosage": a.dosage,
                        "summary": a.summary,
                        "research_use_only": True,
                    }
                ),
            }
        )

    with sample_engine.begin() as conn:
        conn.execute(
            sa.delete(findings).where(
                findings.c.module == MODULE_NAME,
                findings.c.category == "anchor_snp",
            )
        )
        if anchor_rows:
            conn.execute(sa.insert(findings), anchor_rows)

    return n + len(anchor_rows)

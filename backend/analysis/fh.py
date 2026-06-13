"""Familial hypercholesterolemia (FH) view — monogenic + polygenic (SW-B6).

A dedicated FH assessment that composes three genetic strands and frames them
against the clinical FH criteria, **without** claiming a clinical diagnosis:

  1. **Monogenic FH** — pathogenic/likely-pathogenic variants in LDLR, APOB,
     PCSK9 (already extracted by the cardiovascular module; read here).
  2. **APOB R3527Q (rs5742904)** — familial defective apolipoprotein B-100
     (legacy R3500Q), the single most common APOB FH variant; surfaced
     explicitly with its genotype.
  3. **LDL-C polygenic score** — a common-variant burden score (PGS000688) via
     the SW-B4 bridge, with honest coverage reporting (percentile withheld on
     un-imputed arrays, as in SW-B5). Polygenic hypercholesterolemia explains a
     substantial fraction of clinical-FH phenotypes without a monogenic cause.

Clinical FH is diagnosed with the **Dutch Lipid Clinic Network (DLCN)** or
**Simon Broome** criteria, which integrate LDL-C level, personal/family history
of premature coronary disease, physical signs (tendon xanthomata, corneal
arcus), and a causative DNA variant. This tool supplies only the *genetic*
component — it does not measure LDL-C or assess clinical signs — so it can
neither make nor exclude a clinical FH diagnosis (McGowan et al., JAHA 2019).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

import sqlalchemy as sa
import structlog

from backend.analysis.pgs_bridge import build_trait_weight_set, load_pgs_registry
from backend.analysis.prs import PRSResult, run_prs, store_prs_findings
from backend.db.tables import annotated_variants, findings

logger = structlog.get_logger(__name__)

MODULE_NAME = "fh"
FH_GENES = ("LDLR", "APOB", "PCSK9")
FH_TRAIT = "ldl_cholesterol"

# APOB familial defective apoB-100 variant (the canonical APOB FH cause).
APOB_FDB_RSID = "rs5742904"
APOB_FDB_PROTEIN = "p.Arg3527Gln (legacy R3500Q)"

FH_PMIDS = ["31838973"]  # McGowan et al., JAHA 2019 — diagnosis & treatment of HeFH

# Educational framing (no clinical diagnosis is produced).
FH_CRITERIA_CONTEXT: dict[str, str] = {
    "dutch_lipid": (
        "The Dutch Lipid Clinic Network (DLCN) criteria score family history of "
        "premature coronary disease and high LDL-C, the individual's own LDL-C "
        "level, physical signs (tendon xanthomata, corneal arcus), and a causative "
        "DNA variant in LDLR/APOB/PCSK9; the total points grade FH as unlikely, "
        "possible, probable, or definite."
    ),
    "simon_broome": (
        "The Simon Broome criteria combine elevated total/LDL cholesterol with "
        "tendon xanthomata and/or a causative DNA variant and family history to "
        "classify definite vs possible FH."
    ),
    "genetic_role": (
        "Within both criteria a confirmed pathogenic LDLR/APOB/PCSK9 variant is a "
        "strong, points-contributing line of evidence. This report provides that "
        "genetic line only."
    ),
    "disclaimer": (
        "This is not a clinical FH diagnosis. It does not measure your LDL-C, "
        "assess physical signs, or apply the DLCN/Simon Broome scoring. Discuss "
        "lipid testing and FH evaluation with a clinician or genetic counsellor."
    ),
}


def _is_pathogenic(significance: str | None) -> bool:
    """Whether a ClinVar significance string is (likely) pathogenic, not conflicting."""
    if not significance:
        return False
    s = significance.lower()
    if "conflicting" in s:
        return False
    return "pathogenic" in s  # matches both "pathogenic" and "likely_pathogenic"


@dataclass
class ApobFdbResult:
    """APOB rs5742904 (FDB) genotype state for a sample."""

    rsid: str
    gene: str
    protein: str
    genotype: str | None
    zygosity: str | None
    clinvar_significance: str | None
    present: bool  # variant site is typed in this sample
    is_carrier: bool  # carries a non-reference allele (het/hom_alt)


@dataclass
class FhMonogenicVariant:
    """A monogenic FH variant read from the cardiovascular findings."""

    gene: str
    rsid: str | None
    clinvar_significance: str | None
    zygosity: str | None
    evidence_level: int


@dataclass
class FHAssessment:
    """Composed FH assessment: monogenic + APOB FDB + LDL-C PRS."""

    monogenic: list[FhMonogenicVariant] = field(default_factory=list)
    apob_fdb: ApobFdbResult | None = None
    ldl_prs: PRSResult | None = None

    @property
    def has_monogenic(self) -> bool:
        return len(self.monogenic) > 0


# ── Detection ──────────────────────────────────────────────────────────────


def detect_fh_monogenic(sample_engine: sa.Engine) -> list[FhMonogenicVariant]:
    """Read monogenic FH variants (LDLR/APOB/PCSK9 P/LP) from stored findings."""
    with sample_engine.connect() as conn:
        rows = conn.execute(
            sa.select(findings)
            .where(
                findings.c.category == "monogenic_variant",
                findings.c.gene_symbol.in_(FH_GENES),
                findings.c.zygosity.in_(("het", "hom_alt")),
            )
            .order_by(findings.c.gene_symbol)
        ).fetchall()
    return [
        FhMonogenicVariant(
            gene=r.gene_symbol or "",
            rsid=r.rsid,
            clinvar_significance=r.clinvar_significance,
            zygosity=r.zygosity,
            evidence_level=r.evidence_level or 0,
        )
        for r in rows
    ]


def detect_apob_fdb(sample_engine: sa.Engine) -> ApobFdbResult:
    """Resolve the APOB rs5742904 (FDB) genotype + carrier status for a sample."""
    with sample_engine.connect() as conn:
        row = conn.execute(
            sa.select(
                annotated_variants.c.genotype,
                annotated_variants.c.zygosity,
                annotated_variants.c.clinvar_significance,
            ).where(annotated_variants.c.rsid == APOB_FDB_RSID)
        ).fetchone()
    zygosity = row.zygosity if row else None
    return ApobFdbResult(
        rsid=APOB_FDB_RSID,
        gene="APOB",
        protein=APOB_FDB_PROTEIN,
        genotype=row.genotype if row else None,
        zygosity=zygosity,
        clinvar_significance=row.clinvar_significance if row else None,
        present=row is not None and row.genotype is not None,
        is_carrier=zygosity in ("het", "hom_alt"),
    )


def score_ldl_prs(
    sample_engine: sa.Engine,
    pgs_engine: sa.Engine | None,
    inferred_ancestry: str | None = None,
    top_ancestry_fraction: float | None = None,
) -> PRSResult | None:
    """Compute the LDL-C PRS via the bridge (uncalibrated, coverage reported)."""
    if pgs_engine is None:
        return None
    weight_set = build_trait_weight_set(
        pgs_engine, FH_TRAIT, inferred_ancestry, registry=load_pgs_registry()
    )
    if weight_set is None:
        return None
    return run_prs(
        weight_set,
        sample_engine,
        inferred_ancestry=inferred_ancestry,
        top_ancestry_fraction=top_ancestry_fraction,
        n_bootstrap=0,
    )


# ── Pipeline ───────────────────────────────────────────────────────────────


def assess_fh(
    sample_engine: sa.Engine,
    pgs_engine: sa.Engine | None,
    inferred_ancestry: str | None = None,
    top_ancestry_fraction: float | None = None,
) -> FHAssessment:
    """Compose the full FH assessment for a sample."""
    return FHAssessment(
        monogenic=detect_fh_monogenic(sample_engine),
        apob_fdb=detect_apob_fdb(sample_engine),
        ldl_prs=score_ldl_prs(sample_engine, pgs_engine, inferred_ancestry, top_ancestry_fraction),
    )


def store_fh_findings(assessment: FHAssessment, sample_engine: sa.Engine) -> int:
    """Store the FH-module LDL-C PRS + APOB FDB findings (replaced on re-run)."""
    # store_prs_findings clears prior module/prs rows unconditionally (it deletes
    # before the empty-list early return, #244), so passing [] when no LDL-C PRS is
    # computable this run clears any stale fh/prs finding identically — otherwise a
    # previously-stored score would be surfaced with broken provenance (#149).
    n = store_prs_findings(
        [assessment.ldl_prs] if assessment.ldl_prs is not None else [],
        sample_engine,
        module=MODULE_NAME,
        store_insufficient=True,
    )

    fdb = assessment.apob_fdb
    with sample_engine.begin() as conn:
        conn.execute(
            sa.delete(findings).where(
                findings.c.module == MODULE_NAME,
                findings.c.category == "fdb_variant",
            )
        )
        # Emit a finding only when the sample actually carries a non-reference
        # allele (het/hom_alt). A typed homozygous-reference genotype is a
        # non-carrier and is intentionally not surfaced as a finding.
        if fdb is not None and fdb.is_carrier:
            is_pathogenic = _is_pathogenic(fdb.clinvar_significance)
            status = "pathogenic carrier" if is_pathogenic else "carrier"
            conn.execute(
                sa.insert(findings),
                [
                    {
                        "module": MODULE_NAME,
                        "category": "fdb_variant",
                        "evidence_level": 4 if is_pathogenic else 2,
                        "gene_symbol": "APOB",
                        "rsid": APOB_FDB_RSID,
                        "clinvar_significance": fdb.clinvar_significance,
                        "finding_text": (
                            f"APOB {APOB_FDB_PROTEIN} ({APOB_FDB_RSID}) — "
                            f"familial defective apoB-100; genotype {fdb.genotype} "
                            f"({status}) — Research Use Only"
                        ),
                        "pmid_citations": json.dumps(FH_PMIDS),
                        "detail_json": json.dumps(
                            {
                                "rsid": APOB_FDB_RSID,
                                "gene": "APOB",
                                "protein": APOB_FDB_PROTEIN,
                                "genotype": fdb.genotype,
                                "clinvar_significance": fdb.clinvar_significance,
                                "is_pathogenic": is_pathogenic,
                                "research_use_only": True,
                            }
                        ),
                    }
                ],
            )
            n += 1

    return n

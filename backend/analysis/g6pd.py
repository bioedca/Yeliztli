"""G6PD deficiency X-linked pharmacogenomic context (SW-E6).

Glucose-6-phosphate dehydrogenase (G6PD) deficiency is the most common human
enzymopathy and a classic pharmacogenetic trait: in deficient individuals,
oxidative drugs (rasburicase, the 8-aminoquinolines primaquine/tafenoquine,
dapsone, methylene blue, …) and infections can trigger **acute hemolytic
anemia**. The CPIC guideline (Gammal 2023, PMID 36049896; rasburicase-specific
Relling 2014, PMID 24787449) interprets G6PD genotype to flag this risk.

**X-linked dosage — the safety-critical nuance.** G6PD is on chromosome X, so
phenotype depends on biological sex:

* **Male (XY, hemizygous):** one deficiency allele ⇒ Deficient.
* **Female (XX):** the call depends on *where* the deficiency alleles sit, which an
  unphased array only partly resolves:
  * **Homozygous at one locus** (two copies of one deficiency allele) ⇒ Deficient —
    both X chromosomes carry that variant, no phase ambiguity.
  * **One deficiency allele** ⇒ **Variable** — random X-inactivation gives
    heterozygotes a wide activity range, and many test "normal" yet still hemolyse
    on oxidative drugs (Chu 2017, PMID 28170391; Domingo 2018, PMID 30184203). We
    therefore never report a heterozygous female as reassuring "normal".
  * **Two *different* heterozygous deficiency loci** (e.g. A− het + Mediterranean
    het) ⇒ **phase-indeterminate**. A SNP array does not phase the two chrX calls,
    so they may sit in *trans* (true compound heterozygote — both X's affected ⇒
    deficient) or in *cis* (one X carries both, the other is normal ⇒ variable). For
    two X-linked loci, cis variants are co-expressed per cell-clone while in trans
    only one allele is expressed per clone (Goldstein 1971, PMID 5283930), so phase
    changes the phenotype; short-read/array genotyping cannot resolve it (Chamchoy
    2026, PMID 41717344). We surface a distinct
    *variable-or-deficient* result rather than silently summing the two loci into a
    definitive "deficient" call. Both states carry the high-risk-drug caution and an
    enzyme-assay-confirmation prompt.

Biological sex is *inferred* from the array (:func:`infer_biological_sex`), not
recorded; when it cannot be inferred we decline to assign a zygosity-dependent
phenotype.

**Array-typeable variants (forward/plus strand, Ensembl GRCh37).** G6PD is on the
minus strand, so the gene's reference base is the complement of the forward base:

* **A− (rs1050828, c.202G>A / V68M)** — the common African deficiency marker
  (Class III/B, ~12-30% activity), carried on the c.376G background. Forward C/T;
  the deficiency allele is forward **T**.
* **c.376A>G (rs1050829, N126D)** — defines the *non-deficient* A+ allele on its
  own; relevant only as the A− background. Forward T/C; the 376G allele is
  forward **C**. Context, never a deficiency call by itself.
* **Mediterranean (rs5030868, c.563C>T / S188F)** — severe (Class II/B, <10%
  activity). Forward G/A; the deficiency allele is forward **A**.

**Context only — not a diagnosis.** An array types only a handful of the 200+
known G6PD variants; a non-deficient genotype does not exclude an untyped
deficiency variant. G6PD status is confirmed by an enzyme-activity assay. This
layer changes no finding. See :data:`backend.disclaimers.G6PD_PGX_CONTEXT_ONLY`.
"""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa

from backend.analysis.pharmacogenomics import _fetch_sample_genotypes
from backend.analysis.zygosity import is_no_call
from backend.disclaimers import G6PD_PGX_CONTEXT_ONLY
from backend.services.sex_inference import infer_biological_sex

# CPIC: expanded medication guideline (primary) + the rasburicase guideline.
G6PD_PMID_CITATIONS = ["36049896", "24787449"]

# A- deficiency marker — rs1050828 (c.202G>A). Forward-strand alleles.
G6PD_A_MINUS_RSID = "rs1050828"
G6PD_A_MINUS_REF = "C"  # forward = gene "G" (normal)
G6PD_A_MINUS_DEF = "T"  # forward = gene "A" (A- deficiency allele)

# c.376A>G — rs1050829 (defines the non-deficient A+ allele alone). Context only.
G6PD_376_RSID = "rs1050829"
G6PD_376_REF = "T"  # forward = gene "A"
G6PD_376_G = "C"  # forward = gene "G" (the 376G / A+ background)

# Mediterranean — rs5030868 (c.563C>T). Forward-strand alleles.
G6PD_MED_RSID = "rs5030868"
G6PD_MED_REF = "G"  # forward = gene "C" (normal)
G6PD_MED_DEF = "A"  # forward = gene "T" (Mediterranean deficiency allele)

# Representative CPIC high-risk drugs to avoid in G6PD deficiency (not exhaustive).
G6PD_HIGH_RISK_DRUGS = (
    "rasburicase",
    "primaquine",
    "tafenoquine",
    "dapsone",
    "methylene blue",
)


def _deficiency_alleles(
    genotype: str | None, ref: str, deficiency_allele: str
) -> dict[str, int] | None:
    """Count deficiency alleles, handling hemizygous (single-char) chrX calls.

    23andMe stores a male's non-PAR chrX call as a single character (e.g. ``"T"``)
    and a female's as a sorted pair (``"CT"``). Returns ``{"deficiency": n,
    "copies": 1|2}`` or ``None`` when the call is missing, a no-call, or carries an
    unexpected base (third allele / indel).
    """
    if not genotype or is_no_call(genotype):
        return None
    g = genotype.strip().upper()
    if len(g) not in (1, 2):
        return None
    ref_u, def_u = ref.upper(), deficiency_allele.upper()
    if any(base not in {ref_u, def_u} for base in g):
        return None
    return {"deficiency": sum(1 for base in g if base == def_u), "copies": len(g)}


def g6pd_phenotype(
    sex: str,
    total_deficiency: int,
    any_called: bool,
    max_locus_deficiency: int = 0,
) -> dict[str, str]:
    """Assign a G6PD phenotype from inferred sex + deficiency-allele counts.

    ``sex`` is the :func:`infer_biological_sex` result (``"XX"`` / ``"XY"`` /
    ``"manual_review"`` / ``"unknown"``). ``total_deficiency`` sums deficiency
    alleles across the callable deficiency loci (A− and Mediterranean);
    ``max_locus_deficiency`` is the largest deficiency-allele count at any *single*
    locus, which distinguishes a phase-unambiguous homozygote (one locus == 2) from
    two unphased heterozygous loci that merely *sum* to 2.
    """
    if not any_called:
        return {
            "phenotype": "indeterminate",
            "detail": (
                "No G6PD deficiency variant on this array was callable, so G6PD status "
                "could not be assessed (this does not exclude an untyped variant)."
            ),
        }
    if sex == "XY":
        if total_deficiency >= 1:
            return {
                "phenotype": "deficient",
                "detail": (
                    "Hemizygous male carrying a G6PD deficiency allele — G6PD deficient. "
                    "Avoid high-risk oxidative drugs."
                ),
            }
        return {
            "phenotype": "normal",
            "detail": "Hemizygous male with no typed G6PD deficiency allele — G6PD normal.",
        }
    if sex == "XX":
        if max_locus_deficiency >= 2:
            return {
                "phenotype": "deficient",
                "detail": (
                    "Female homozygous for a G6PD deficiency allele at one locus (both X "
                    "chromosomes affected) — G6PD deficient. Avoid high-risk oxidative drugs."
                ),
            }
        if total_deficiency >= 2:
            return {
                "phenotype": "phase_indeterminate",
                "detail": (
                    "Two G6PD deficiency alleles are present at different loci, but a SNP "
                    "array does not phase them. They may sit in trans (true compound "
                    "heterozygote — both X chromosomes affected, G6PD deficient) or in cis "
                    "(one X carries both, the other is normal — heterozygous, VARIABLE "
                    "activity). These states differ in phenotype, so deficiency cannot be "
                    "confirmed from genotype alone. Treat as potentially deficient and "
                    "confirm with an enzyme-activity assay before a high-risk oxidative drug."
                ),
            }
        if total_deficiency == 1:
            return {
                "phenotype": "variable",
                "detail": (
                    "Heterozygous female — G6PD activity is VARIABLE (random "
                    "X-inactivation). She may test normal yet still be at risk of "
                    "drug-induced hemolysis; treat as potentially deficient and confirm "
                    "with an enzyme-activity assay before a high-risk oxidative drug."
                ),
            }
        return {
            "phenotype": "normal",
            "detail": "Female with no typed G6PD deficiency allele — G6PD normal.",
        }
    # Sex could not be inferred (manual_review / unknown): zygosity is undefined.
    if total_deficiency >= 1:
        return {
            "phenotype": "indeterminate",
            "detail": (
                "A G6PD deficiency allele is present, but biological sex could not be "
                "inferred from the array, so the X-linked (zygosity-dependent) phenotype "
                "cannot be assigned. Treat as potentially deficient and confirm with an "
                "enzyme-activity assay."
            ),
        }
    return {
        "phenotype": "indeterminate",
        "detail": (
            "No typed G6PD deficiency allele was detected, but biological sex could not "
            "be inferred; phenotype not assigned."
        ),
    }


def _locus_call(
    *, name: str, rsid: str, cdna: str, ref: str, deficiency_allele: str, genotype: str | None
) -> dict[str, Any]:
    """Per-variant observed deficiency call."""
    state = _deficiency_alleles(genotype, ref, deficiency_allele)
    return {
        "name": name,
        "rsid": rsid,
        "cdna": cdna,
        "observed_genotype": genotype,
        "called": state is not None,
        "deficiency_alleles": state["deficiency"] if state else None,
    }


def assess_g6pd(sample_engine: sa.Engine) -> dict[str, Any]:
    """Context-only, sex-aware G6PD deficiency summary for a sample.

    Read-only. Infers biological sex, genotypes the array-typeable G6PD variants,
    and assigns an X-linked-aware phenotype (normal / variable / deficient /
    indeterminate) plus high-risk-drug context. Emits no diagnosis and changes no
    finding — G6PD status is confirmed by an enzyme-activity assay.
    """
    sex = infer_biological_sex(sample_engine)
    genotypes = _fetch_sample_genotypes(
        [G6PD_A_MINUS_RSID, G6PD_376_RSID, G6PD_MED_RSID], sample_engine
    )

    a_minus = _locus_call(
        name="A- (V68M)",
        rsid=G6PD_A_MINUS_RSID,
        cdna="c.202G>A",
        ref=G6PD_A_MINUS_REF,
        deficiency_allele=G6PD_A_MINUS_DEF,
        genotype=genotypes.get(G6PD_A_MINUS_RSID),
    )
    mediterranean = _locus_call(
        name="Mediterranean (S188F)",
        rsid=G6PD_MED_RSID,
        cdna="c.563C>T",
        ref=G6PD_MED_REF,
        deficiency_allele=G6PD_MED_DEF,
        genotype=genotypes.get(G6PD_MED_RSID),
    )
    deficiency_loci = [a_minus, mediterranean]

    any_called = any(loc["called"] for loc in deficiency_loci)
    locus_deficiency_counts = [loc["deficiency_alleles"] or 0 for loc in deficiency_loci]
    total_deficiency = sum(locus_deficiency_counts)
    # Largest count at any single locus: a locus == 2 is a phase-unambiguous homozygote,
    # whereas two heterozygous loci that merely sum to 2 are not phaseable on an array.
    max_locus_deficiency = max(locus_deficiency_counts, default=0)

    # rs1050829 distinguishes the non-deficient A+ allele from reference (context only).
    g376 = _deficiency_alleles(genotypes.get(G6PD_376_RSID), G6PD_376_REF, G6PD_376_G)
    a_plus_present = bool(
        g376 and g376["deficiency"] >= 1 and a_minus["deficiency_alleles"] in (0, None)
    )

    verdict = g6pd_phenotype(sex, total_deficiency, any_called, max_locus_deficiency)
    phenotype = verdict["phenotype"]
    # Surface the drug warning whenever a deficiency allele is present — including the
    # phase-indeterminate compound and the sex-indeterminate case, both of which the
    # phenotype detail says to treat as potentially deficient.
    at_risk = phenotype in {"deficient", "variable", "phase_indeterminate"} or (
        phenotype == "indeterminate" and total_deficiency >= 1
    )

    return {
        "inferred_sex": sex,
        "variants": deficiency_loci,
        "any_called": any_called,
        "phenotype": phenotype,
        "detail": verdict["detail"],
        "at_risk": at_risk,
        "a_plus_nondeficient_present": a_plus_present,
        "high_risk_drugs": list(G6PD_HIGH_RISK_DRUGS) if at_risk else [],
        "context_only": True,
        "note": G6PD_PGX_CONTEXT_ONLY,
        "pmid_citations": G6PD_PMID_CITATIONS,
    }

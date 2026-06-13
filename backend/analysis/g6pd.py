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

**Typed deficiency variants (forward/plus strand, Ensembl GRCh37).** G6PD is on the
minus strand, so each variant's forward/plus-strand base is the *complement* of the
gene/cDNA base. 23andMe/AncestryDNA report plus-strand calls and dbSNP RefSNP alleles
are plus-strand, so the alleles below are stored exactly as the array reports them.
Every row was taken from the CPIC G6PD Allele Definition + Functionality tables
(Gammal 2023, PMID 36049896) and cross-checked on Ensembl GRCh37 REST + NCBI dbSNP;
population distributions are from He 2020 (PMID 33051526, Han Chinese) and the wider
Southeast/South-Asian literature (Nuchprayoon 2002; Iwai 2001; Ainoon 1999):

* **A− (rs1050828, c.202G>A / V68M)** — common African marker (Class III, ~10-30%
  activity), on the c.376G background. Forward C/T; deficiency allele forward **T**.
* **Mediterranean (rs5030868, c.563C>T / S188F)** — severe (Class II, <10%).
  Forward G/A; deficiency allele forward **A**.
* **Mahidol (rs137852314, c.487G>A / G163S)** — dominant in Myanmar/Thailand
  (Class III). Forward C/T; deficiency forward **T**.
* **Canton (rs72554665, c.1376G>T / R459L)** — a top East-Asian variant (Class II).
  Forward C→**A**. This position is multiallelic; the C>G alt is the distinct
  *Cosenza* (R459P) allele, which is not encoded here.
* **Kaiping (rs72554664, c.1388G>A / R463H)** — most common Han-Chinese variant
  (Class II). Forward C/T; deficiency forward **T**.
* **Viangchan (rs137852327, c.871G>A / V291M)** — most common non-Chinese SE-Asian
  variant (Class II). Forward C/T; deficiency forward **T**.
* **Union (rs398123546, c.1360C>T / R454C)** — Pacific/East-Asian (Class II).
  Forward G/A; deficiency forward **A**.
* **Chinese-5 (rs137852342, c.1024C>T / L342F)** — East-Asian (Class III).
  Forward G/A; deficiency forward **A**.
* **Coimbra (rs137852330, c.592C>T / R198C)** — South/SE-Asian (Class II).
  Forward G/A; deficiency forward **A**.
* **Chatham (rs5030869, c.1003G>A / A335T)** — Middle-Eastern/South-Asian
  (Class II). Forward C/T; deficiency forward **T**.
* **Gaohe (rs137852340, c.95A>G / H32R)** — East-Asian (Class III). Forward T/C;
  deficiency forward **C**.
* **c.376A>G (rs1050829, N126D)** — defines the *non-deficient* A+ allele on its
  own; relevant only as the A− background. Forward T/C; the 376G allele is
  forward **C**. Context, never a deficiency call by itself.

**Which of these a consumer array actually types varies.** A− (rs1050828) and
Mediterranean (rs5030868) are standard content on the Illumina GSA and therefore on
23andMe v5 / AncestryDNA; the remaining (mostly East/Southeast-Asian) alleles are
CPIC-defined deficiency variants whose presence depends on the specific chip/version,
so they are included for sensitivity but their *absence* from a sample is reported as
not-called, never as a false deficiency call (SNP chips type common variants reliably
yet rarer ones inconsistently — Weedon 2021, PMID 33589468). Adding a locus can only
*raise* sensitivity for ancestries whose deficiency is not driven by A−/Mediterranean.

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

# CPIC: expanded medication guideline (primary) + the rasburicase guideline; He 2020
# (Han-Chinese variant spectrum) backs the East/Southeast-Asian deficiency panel.
G6PD_PMID_CITATIONS = ["36049896", "24787449", "33051526"]

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

# Array-typed deficiency loci. Each row is (name, rsid, cDNA, forward_ref, forward_def):
# forward_ref is the plus-strand base of the gene-NORMAL allele, forward_def the
# plus-strand base of the DEFICIENCY allele. G6PD is minus-strand, so these are the
# complement of the cDNA base — and exactly what 23andMe/AncestryDNA and dbSNP report
# (plus strand). Every row is from the CPIC G6PD Allele Definition Table (PMID 36049896)
# and was cross-checked on Ensembl GRCh37 REST + NCBI dbSNP. A−/Mediterranean are
# reliably array-typed; the remaining (mostly E/SE-Asian) CPIC deficiency alleles vary
# by chip — a non-call never excludes them (see the module docstring). Note rs72554665
# (Canton), rs5030869 (Chatham) are multiallelic; only the deficiency alt is encoded.
G6PD_DEFICIENCY_VARIANTS: tuple[tuple[str, str, str, str, str], ...] = (
    ("A- (V68M)", G6PD_A_MINUS_RSID, "c.202G>A", G6PD_A_MINUS_REF, G6PD_A_MINUS_DEF),
    ("Mediterranean (S188F)", G6PD_MED_RSID, "c.563C>T", G6PD_MED_REF, G6PD_MED_DEF),
    ("Mahidol (G163S)", "rs137852314", "c.487G>A", "C", "T"),
    ("Canton (R459L)", "rs72554665", "c.1376G>T", "C", "A"),
    ("Kaiping (R463H)", "rs72554664", "c.1388G>A", "C", "T"),
    ("Viangchan (V291M)", "rs137852327", "c.871G>A", "C", "T"),
    ("Union (R454C)", "rs398123546", "c.1360C>T", "G", "A"),
    ("Chinese-5 (L342F)", "rs137852342", "c.1024C>T", "G", "A"),
    ("Coimbra (R198C)", "rs137852330", "c.592C>T", "G", "A"),
    ("Chatham (A335T)", "rs5030869", "c.1003G>A", "C", "T"),
    ("Gaohe (H32R)", "rs137852340", "c.95A>G", "T", "C"),
)

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
    alleles across all callable deficiency loci (:data:`G6PD_DEFICIENCY_VARIANTS`);
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
    rsids = [rsid for _, rsid, _, _, _ in G6PD_DEFICIENCY_VARIANTS]
    genotypes = _fetch_sample_genotypes([*rsids, G6PD_376_RSID], sample_engine)

    deficiency_loci = [
        _locus_call(
            name=name,
            rsid=rsid,
            cdna=cdna,
            ref=ref,
            deficiency_allele=deficiency_allele,
            genotype=genotypes.get(rsid),
        )
        for name, rsid, cdna, ref, deficiency_allele in G6PD_DEFICIENCY_VARIANTS
    ]

    any_called = any(loc["called"] for loc in deficiency_loci)
    locus_deficiency_counts = [loc["deficiency_alleles"] or 0 for loc in deficiency_loci]
    total_deficiency = sum(locus_deficiency_counts)
    # Largest count at any single locus: a locus == 2 is a phase-unambiguous homozygote,
    # whereas two heterozygous loci that merely sum to 2 are not phaseable on an array.
    max_locus_deficiency = max(locus_deficiency_counts, default=0)

    # rs1050829 distinguishes the non-deficient A+ allele from reference (context only).
    a_minus = next(loc for loc in deficiency_loci if loc["rsid"] == G6PD_A_MINUS_RSID)
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

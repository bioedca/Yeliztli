# Interpretation reference

Yeliztli's [modules](index.md) produce findings in a handful of common "languages." This page
explains the **evidence rating** that accompanies every finding, the different **kinds of
output** you'll see, and the **recurring caveats** that apply across modules. It complements
the per-module pages and the gentler
[reading your results](../getting-started/reading-your-results.md) overview.

--8<-- "health-disclaimer.md"

## Evidence ratings (★ to ★★★★)

Every finding carries a star rating so you can weigh it. The bands map to established evidence
frameworks:

| Rating | Roughly corresponds to |
|--------|------------------------|
| ★★★★ | ClinVar **Pathogenic / Likely-Pathogenic** with a reviewed (2+ star) status, **CPIC Level A**, or a **genome-wide-significant** GWAS association with a very large effect size (for example, odds ratio > 5) |
| ★★★ | ClinVar **Pathogenic / Likely-Pathogenic** (single submitter), **CPIC Level B**, or a **replicated, genome-wide-significant** GWAS association |
| ★★ | A **variant of uncertain significance** with functional support, a single **genome-wide-significant** GWAS association without independent replication, or PharmGKB level 2A/2B |
| ★ | A single study, a candidate-gene association, or PharmGKB level 3/4 |

For GWAS findings, the conventional p < 5×10⁻⁸ threshold controls genome-wide multiple
testing, but it does not by itself make an association definitive. Yeliztli reserves higher
GWAS tiers for genome-wide significance plus independent replication, or for genome-wide
significance plus a very large effect size under Yeliztli's tiering rule. The cited GWAS papers
support the p-value, replication, and false-positive-control rationale [1,2].

Two rules keep weak signals from looking strong:

- **Wellness/trait modules are capped** (often at ★★). The [Traits & personality](wellness/traits-personality.md)
  module, for example, caps every finding regardless of the underlying variant.
- **Weak variants can't escalate a category.** In categorical modules, a ★ variant cannot push
  a pathway to *Elevated* (it's held at *Moderate*).

## Kinds of output

### Pathogenic-variant findings

The hereditary-risk modules ([cancer](health-risk/cancer.md),
[cardiovascular](health-risk/cardiovascular.md), [carrier status](health-risk/carrier-status.md),
[rare variants](rare-variants.md)) report **specific variants** classified by ClinVar using the
ACMG/AMP framework. Read these with the inheritance pattern in mind:

- **Carrier vs affected.** For recessive conditions, carrying **one** copy makes you a *carrier*
  (usually unaffected); **two** copies is an *affected* state. Yeliztli labels which applies.
- **Cross-links.** Some genes appear in more than one module (e.g. *BRCA1/2* in both cancer and
  carrier status) with framing appropriate to each.
- **Negatives aren't clearance.** Arrays type specific variants, not whole genes — a negative
  result doesn't exclude untyped variants.

#### ClinVar classifications that conflict

Yeliztli reports pathogenic-variant findings only when ClinVar has a clear
Pathogenic/Likely-Pathogenic classification. It deliberately does **not** turn records labelled
**Conflicting classifications of pathogenicity** into findings, because the submitted clinical
interpretations disagree and should not be presented as a definitive finding.[^clinvar-conflict]

That means "no findings" does **not** mean "no variants with any pathogenic-leaning ClinVar
evidence." To review contested records, open the
[Variant Explorer](../features/variant-explorer.md) and filter or search ClinVar significance
for `conflicting`.

### Categorical pathway levels

The wellness modules ([nutrigenomics](wellness/nutrigenomics.md),
[methylation](wellness/methylation.md), [fitness](wellness/fitness.md),
[sleep](wellness/sleep.md), [skin](wellness/skin.md), [allergy](wellness/allergy.md)) and
[gene health](health-risk/gene-health.md) report a **level** per pathway — *Elevated*,
*Moderate*, or *Standard* — rather than a number. A pathway's level reflects its
highest-category contributing variant, subject to the evidence cap above. Sites that can't be
resolved from array data (e.g. strand-ambiguous palindromic homozygotes) are marked
**Indeterminate** and withheld from the level rather than guessed.

### Star-allele diplotypes & CPIC status

[Pharmacogenomics](pharma/pharmacogenomics.md) reports a **diplotype** (e.g. `*1/*4`) and a
CPIC phenotype or functional status, each with a **call-confidence** of *Complete*, *Partial*,
or *Insufficient*. Genes reported with **metabolizer phenotypes** use **Poor Metabolizer**,
**Intermediate Metabolizer**, **Normal Metabolizer**, **Rapid Metabolizer**, and
**Ultrarapid Metabolizer**. **Rapid** is a distinct
increased-activity category, not a synonym for **ultrarapid**; for example, Yeliztli maps
`CYP2C19 *1/*17` to **Rapid Metabolizer** and `*17/*17` to **Ultrarapid Metabolizer**.[^cpic-terms]
Other CPIC pharmacogenes use different status families, such as **Normal function**,
**Decreased function**, and **Poor function** for *SLCO1B1*, or **Rapid Acetylator**,
**Intermediate Acetylator**, and **Slow Acetylator** for *NAT2*.
Drug alerts use **CPIC** levels. Treat *Partial*/*Insufficient* calls with extra caution —
arrays can miss copy-number and structural variation.

### Polygenic scores (percentiles)

The polygenic modules ([metabolic](health-risk/metabolic.md),
[bone density](health-risk/bone-density-ebmd.md),
[familial hypercholesterolemia](health-risk/familial-hypercholesterolemia.md), cancer PRS,
cognitive traits) summarise many small-effect variants into a **population percentile**, never
a raw score or an absolute risk. Read these carefully:

- **Research use only.** They are not diagnostic and not clinically validated.
- **Percentiles can be withheld.** When a score isn't calibrated for your inferred ancestry, or
  your array covers too few of its variants, Yeliztli **withholds** the percentile and shows
  coverage instead — rather than report a misleading number.
- **Ancestry matters — a lot.** Scores are mostly derived in European-ancestry cohorts and
  **transfer poorly** to other ancestries, which can worsen health disparities if misused [^1].
  Accuracy in fact decays continuously with genetic distance from the training data — even
  *within* a single labelled ancestry group [^2] — and varies with non-genetic factors such as
  age, sex, and socio-economic status [^3][^4]. Yeliztli shows an ancestry-mismatch warning when
  relevant.

### Risk-genotype findings (common variants)

Several condition modules (e.g. [haemochromatosis, thrombophilia, AMD, gout, APOL1](specialized.md))
report **common risk genotypes** with **odds ratios**. These describe **relative** risk; the
**absolute** lifetime risk for most carriers often remains low, penetrance is frequently
**reduced**, and some effects are **ancestry- or sex-stratified**. They are risk modifiers, not
diagnoses.

### Haplogroups & ancestry

Ancestry estimates, admixture fractions, and haplogroups are statistical inferences — see
[Ancestry methods & validation](../ancestry-methods.md) for how they're computed and their
limitations.

## Recurring caveats

- **Array-proxy genotyping.** Some variants are read via a nearby proxy SNP, not directly; proxy
  accuracy varies by ancestry. And array calls for **rare** variants are unreliable in general —
  see [Intended use & disclaimers](../intended-use.md).
- **Reduced penetrance.** Carrying a risk genotype is not the same as having (or developing) the
  condition.
- **Sex/ancestry stratification.** Where penetrance differs by sex or ancestry, Yeliztli says so.
- **Honest gaps.** When a result can't be supported (an uncalibrated percentile, an
  unresolvable palindromic call), Yeliztli withholds it instead of fabricating one.

## Module → output type

| Output type | Modules |
|-------------|---------|
| Pathogenic variants (ClinVar P/LP) | Cancer, Cardiovascular, Carrier status, Rare variants |
| Categorical pathway levels | Nutrigenomics, Methylation, Fitness, Sleep, Skin, Allergy, Gene health |
| Star-allele diplotype + CPIC phenotype/status | Pharmacogenomics |
| Polygenic percentile | Metabolic, Bone density, Familial hypercholesterolemia, Cancer (PRS), Traits |
| Common-variant odds ratios | Haemochromatosis, Thrombophilia, Alpha-1, AMD, APOL1, Gout |
| Diplotype / risk genotype (gated) | APOE, Parkinson's, Sex-aneuploidy |
| Mitochondrial / pharmacogenomic risk | LHON, MT-RNR1, G6PD, BChE |
| Ancestry & haplogroups | Ancestry |

## References

[1] Barsh GS, Copenhaver GP, Gibson G, Williams SM. [Guidelines for Genome-Wide Association Studies](https://doi.org/10.1371/journal.pgen.1002812). *PLOS Genetics*. 2012;8(7):e1002812.

[2] Chen Z, Boehnke M, Wen X, Mukherjee B. [Revisiting the genome-wide significance threshold for common variant GWAS](https://doi.org/10.1093/g3journal/jkaa056). *G3: Genes, Genomes, Genetics*. 2021;11(2):jkaa056.

[^1]: [Principles and methods for transferring polygenic risk scores across global populations](https://consensus.app/papers/details/186416618e965ebf97cb3e095a9a217d/) (Kachuri et al., 2023, *Nature Reviews Genetics*).
[^2]: [Polygenic scoring accuracy varies across the genetic ancestry continuum](https://consensus.app/papers/details/143ee361696e5cbe8836e5bc45a471cd/) (Ding et al., 2023, *Nature*).
[^3]: [Variable prediction accuracy of polygenic scores within an ancestry group](https://consensus.app/papers/details/dcbc46184d195a21965cf4614828d104/) (Mostafavi et al., 2019, *eLife*).
[^4]: [Portability of 245 polygenic scores when derived from the UK Biobank and applied to 9 ancestry groups from the same cohort](https://consensus.app/papers/details/41027bd6083a52a7adc632d041a4a299/) (Privé et al., 2022, *Am. J. Hum. Genet.*).
[^clinvar-conflict]: [ClinVar: improving access to variant interpretations and supporting evidence](https://doi.org/10.1093/nar/gkx1153) (Landrum et al., 2018, *Nucleic Acids Research*) describes ClinVar as a public archive of submitted clinical-significance interpretations for human variants.
[^cpic-terms]: [Standardizing terms for clinical pharmacogenetic test results: consensus terms from the Clinical Pharmacogenetics Implementation Consortium (CPIC)](https://doi.org/10.1038/gim.2016.87) (Caudle et al., 2017, *Genetics in Medicine*) defines consensus pharmacogenetic phenotype terminology for consistent PGx interpretation.

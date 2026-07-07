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
| ★ | A single study, a candidate-gene association, PharmGKB level 3/4, or a carried rare/novel variant surfaced for discovery context without stronger ClinVar or functional evidence |

Not every ★ row is a disease association. The [Rare Variant Finder](rare-variants.md) also
uses ★ for carried rare, AF-missing, or novel variants that pass its discovery filters but lack
stronger clinical or functional evidence. Treat those rows as a low-evidence variant inventory
until you review the variant details and any linked evidence.

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
[cardiovascular](health-risk/cardiovascular.md), [carrier status](health-risk/carrier-status.md))
report **specific variants** classified by ClinVar using the ACMG/AMP framework. The
[Rare Variant Finder](rare-variants.md) overlaps with that when a carried rare variant has a
ClinVar Pathogenic/Likely-Pathogenic assertion, but it also reports a broader inventory of
carried rare, AF-missing, ensemble-pathogenic, and novel variants. Only its ClinVar pathogenic
categories should be read as pathogenic-variant findings. Read these with the inheritance
pattern in mind:

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

### In-silico pathogenicity scores (CADD, REVEL)

Variant surfaces — the [Variant Explorer](../features/variant-explorer.md) side panel and the
[Rare Variant Finder](rare-variants.md) — show two computational pathogenicity predictors as raw
numbers. Unlike SIFT and PolyPhen-2 (shown with a plain-language *Deleterious* / *Tolerated*
label), CADD and REVEL are unlabelled, so their **direction** and **scale** are:

- **CADD** (Combined Annotation-Dependent Depletion) — a **phred-scaled** measure of variant
  **deleteriousness**: **higher = more deleterious**, on a scale of roughly **0–99**. A CADD-Phred
  of 10, 20, or 30 corresponds to the ~top 10%, 1%, or 0.1% most deleterious of all possible
  single-nucleotide variants.[^cadd]
- **REVEL** (Rare Exome Variant Ensemble Learner) — a **missense-specific** ensemble score on a
  **0–1** scale: **higher = more likely pathogenic**.[^revel]

The Rare Variant Finder highlights a score in red above the thresholds it uses for display —
**CADD ≥ 20** and **REVEL ≥ 0.5**. These are **display heuristics to draw attention, not diagnostic
cut-offs**: in-silico predictions are *supporting* evidence only, to be weighed alongside ClinVar,
allele frequency, and inheritance — never read as a diagnosis on their own.

### Categorical pathway levels

The wellness modules ([nutrigenomics](wellness/nutrigenomics.md),
[methylation](wellness/methylation.md), [fitness](wellness/fitness.md),
[sleep](wellness/sleep.md), [skin](wellness/skin.md), [allergy](wellness/allergy.md),
[traits](wellness/traits-personality.md)) and
[gene health](health-risk/gene-health.md) report a **level** per pathway — *Elevated*,
*Moderate*, or *Standard* — rather than a number. A pathway's level reflects its
highest-category contributing variant, subject to the evidence cap above. Sites that can't be
resolved from array data (e.g. strand-ambiguous palindromic homozygotes) are marked
**Indeterminate** and withheld from the level rather than guessed.

When your array only partially covers a pathway's tracked SNPs — common across genotyping
vendors — a **Standard** pathway carries a coverage-qualified badge so the call isn't read as
fully supported:

- **Tested Standard** — the pathway looks Standard, but only *some* of its tracked SNPs were
  on-chip or callable from your array; the rest were not assessed. It is a Standard call made
  on incomplete coverage (the card's coverage note says how many were off-chip vs. no-call).
- **Not Assessed** — *none* of the pathway's tracked SNPs were callable, so no level could be
  determined for that pathway.

These differ from a plain **Standard** badge (all tracked SNPs covered) and from
**Indeterminate** (individual sites that *were* typed but can't be resolved, withheld from an
otherwise-computed level). *Elevated* and *Moderate* pathways always show their plain level,
never a coverage-qualified badge.

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
Some PGx results also include an **activity score**. Treat it as a gene-specific support value
for the phenotype/status, not as a universal scale: activity-score thresholds are defined per
gene and diplotype system, and the same number can map to different interpretations in
different pharmacogenes.[^cpic-cyp2d6-as]
Drug alerts use **CPIC** levels. Treat *Partial*/*Insufficient* calls with extra caution —
arrays can miss copy-number and structural variation.

### Polygenic scores (percentiles)

The polygenic modules ([metabolic](health-risk/metabolic.md),
[bone density](health-risk/bone-density-ebmd.md),
[familial hypercholesterolemia](health-risk/familial-hypercholesterolemia.md), cancer PRS,
cognitive traits) summarise many small-effect variants into a **population percentile**, never
a raw score or a PRS-derived absolute risk. Read these carefully:

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

This PRS rule is separate from the cancer module's opt-in absolute-risk context, which can show
population baseline and monogenic *BRCA1*/*BRCA2* carrier-penetrance figures after explicit
consent. That overlay is not a PRS-derived personal risk estimate.

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
| Pathogenic variants (ClinVar P/LP) | Cancer, Cardiovascular, Carrier status, Rare variants (ClinVar pathogenic category) |
| ClinVar lower-penetrance/risk-allele variants | Rare variants |
| Carried rare/novel variant inventory (mostly ★ discovery context) | Rare variants |
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
[^cpic-cyp2d6-as]: [Standardizing CYP2D6 Genotype to Phenotype Translation: Consensus Recommendations from the Clinical Pharmacogenetics Implementation Consortium and Dutch Pharmacogenetics Working Group](https://doi.org/10.1111/cts.12692) (Caudle et al., 2020, *Clinical and Translational Science*; [PMID 31647186](https://pubmed.ncbi.nlm.nih.gov/31647186/); [PMCID PMC6951851](https://pmc.ncbi.nlm.nih.gov/articles/PMC6951851/)) describes the CYP2D6 activity-score system as summed allele activity values and explains that phenotype translation depends on consensus, gene-specific thresholds.
[^cadd]: [CADD: predicting the deleteriousness of variants throughout the human genome](https://doi.org/10.1093/nar/gky1016) (Rentzsch et al., 2019, *Nucleic Acids Research*; [PMID 30371827](https://pubmed.ncbi.nlm.nih.gov/30371827/)) describes CADD as a phred-scaled, genome-wide measure of variant deleteriousness in which higher scores indicate more deleterious variants.
[^revel]: [REVEL: An Ensemble Method for Predicting the Pathogenicity of Rare Missense Variants](https://doi.org/10.1016/j.ajhg.2016.08.016) (Ioannidis et al., 2016, *American Journal of Human Genetics*; [PMID 27666373](https://pubmed.ncbi.nlm.nih.gov/27666373/)) presents REVEL as a missense-specific ensemble score from 0 to 1 in which higher scores indicate a greater probability of pathogenicity.

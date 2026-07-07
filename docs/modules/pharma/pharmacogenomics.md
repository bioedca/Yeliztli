# Pharmacogenomics

--8<-- "health-disclaimer.md"

Pharmacogenomics looks at genes that affect how your body processes certain medications, and
surfaces the relevant CPIC prescribing guidance.

## What it looks at

**11 CPIC pharmacogenes:** *CYP2D6*, *CYP2C19*, *CYP2C9*, *CYP3A5*, *SLCO1B1*, *DPYD*,
*TPMT*, *UGT1A1*, *NUDT15*, *NAT2*, and *CYP2B6*.

## What you'll see

- **Star-allele diplotypes** (e.g. `*1/*4`) and the resulting CPIC phenotype or functional
  status. Genes reported with **metabolizer phenotypes** use **Poor Metabolizer**,
  **Intermediate Metabolizer**, **Normal Metabolizer**, **Rapid Metabolizer**, and
  **Ultrarapid Metabolizer**.
- **Rapid** and **ultrarapid** are separate metabolizer categories, so a result such as
  `CYP2C19 *1/*17` is reported as **Rapid Metabolizer**, while `*17/*17` is reported as
  **Ultrarapid Metabolizer**.[^cpic-terms] Other pharmacogenes use different CPIC status
  families, including *SLCO1B1* **Normal function**, **Decreased function**, and
  **Poor function**, and *NAT2* **Rapid Acetylator**, **Intermediate Acetylator**, and
  **Slow Acetylator**.
- Some cards also show an **activity score**. This is a gene-specific numeric summary of
  allele function that Yeliztli uses only with that gene's diplotype-to-phenotype table. In
  activity-score systems, allele values are commonly summed from `0` for no-function alleles,
  fractional values for reduced-function alleles, and `1` for normal-function alleles;
  copy-number gains can raise the total when known. The thresholds that turn that number into
  a phenotype are gene-specific, so compare the displayed score only within the same gene and
  rely on the adjacent CPIC phenotype/status for interpretation.[^cpic-cyp2d6-as]
- A **three-state call confidence** — *Complete*, *Partial*, or *Insufficient* — so you know
  how much to trust each call.
- A **card tint** that summarizes the metabolizer result, separate from call confidence:
  **green** means a normal/routine result, **amber** means a non-normal phenotype or functional
  status worth reviewing in the context of the affected drugs, and **red** means no phenotype
  could be determined for that gene. Amber is deliberately broad: it can cover reduced,
  increased, or otherwise non-normal statuses, so read the phenotype text and any drug alerts
  for the direction of effect.
- **Per-drug prescribing alerts** based on CPIC guidelines, with the CPIC level mapped to an
  evidence rating, plus context from PharmGKB, DPWG, and FDA labels.
- A consolidated **medication-safety summary** grouping the actionable interactions.

## Good to know

- **Arrays can't see everything.** They cannot resolve *CYP2D6*/*CYP2B6* copy-number changes
  or gene conversions, so those calls are *Partial* at best. They also cannot genotype the
  *UGT1A1* `*28` promoter TA-repeat from SNP array data, so that reduced-function allele
  remains indeterminate when only array calls are available.[^ugt1a1-repeat]
- **A normal result isn't a guarantee.** For *DPYD*, only a handful of variants are typed — a
  normal call does **not** rule out DPD deficiency, which matters for certain chemotherapy
  drugs. For *UGT1A1*, a normal `*1/*1` call does **not** rule out reduced *UGT1A1* activity
  from untyped `*28`, which matters for irinotecan and atazanavir prescribing
  context.[^ugt1a1-irinotecan][^ugt1a1-atazanavir]
- Some diplotypes are inferred from unphased data and carry phase ambiguity.

!!! danger "Never change a medication based on this"
    Pharmacogenomic results here are research/educational. Do not start, stop, or change any
    medication without your prescriber, who can order a clinical PGx test if warranted.

[^ugt1a1-repeat]: [Correlation between the UDP-glucuronosyltransferase (UGT1A1) TATAA box polymorphism and carcinogen detoxification phenotype](https://doi.org/10.1158/1055-9965.epi-03-0070) (Fang & Lazarus, 2004, *Cancer Epidemiology, Biomarkers & Prevention*; [PMID 14744740](https://pubmed.ncbi.nlm.nih.gov/14744740/)) describes `UGT1A1*28` as an additional `(TA)` repeat in the promoter TATA box linked to lower UGT1A1 expression/activity.
[^ugt1a1-irinotecan]: [Dutch Pharmacogenetics Working Group guideline for the gene-drug interaction between UGT1A1 and irinotecan](https://doi.org/10.1038/s41431-022-01243-2) (Hulshof et al., 2023, *European Journal of Human Genetics*; [PMID 36443464](https://pubmed.ncbi.nlm.nih.gov/36443464/)) describes UGT1A1 poor metabolizers and genotype-informed irinotecan starting-dose adjustment.
[^ugt1a1-atazanavir]: [Clinical Pharmacogenetics Implementation Consortium guideline for UGT1A1 and atazanavir prescribing](https://doi.org/10.1002/cpt.269) (Gammal et al., 2016, *Clinical Pharmacology & Therapeutics*; [PMID 26417955](https://pubmed.ncbi.nlm.nih.gov/26417955/)) summarizes atazanavir prescribing recommendations when UGT1A1 genotype is known.
[^cpic-terms]: [Standardizing terms for clinical pharmacogenetic test results: consensus terms from the Clinical Pharmacogenetics Implementation Consortium (CPIC)](https://doi.org/10.1038/gim.2016.87) (Caudle et al., 2017, *Genetics in Medicine*) defines consensus pharmacogenetic phenotype terminology for consistent PGx interpretation.
[^cpic-cyp2d6-as]: [Standardizing CYP2D6 Genotype to Phenotype Translation: Consensus Recommendations from the Clinical Pharmacogenetics Implementation Consortium and Dutch Pharmacogenetics Working Group](https://doi.org/10.1111/cts.12692) (Caudle et al., 2020, *Clinical and Translational Science*; [PMID 31647186](https://pubmed.ncbi.nlm.nih.gov/31647186/); [PMCID PMC6951851](https://pmc.ncbi.nlm.nih.gov/articles/PMC6951851/)) describes the CYP2D6 activity-score system as summed allele activity values and explains that phenotype translation depends on consensus, gene-specific thresholds.

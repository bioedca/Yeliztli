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
- A consolidated **medication-safety summary** grouping the actionable interactions. When a
  drug's guidance conservatively assumes a reduced-function phenotype because an allele that
  would change the call couldn't be typed, that drug's card labels the phenotype
  **Conservative** and shows your directly called result beside the explanation. The
  per-gene coverage panel always lists the direct call.

## Good to know

- **Arrays can't see everything.** They cannot resolve *CYP2D6*/*CYP2B6* copy-number changes
  or gene conversions, so those calls are *Partial* at best. They also cannot genotype the
  *UGT1A1* `*28` promoter TA-repeat from SNP array data, so that reduced-function allele
  remains indeterminate when only array calls are available.[^ugt1a1-repeat]
- **A normal result isn't a guarantee.** For *DPYD*, only a handful of variants are typed — a
  normal call does **not** rule out DPD deficiency, which matters for certain chemotherapy
  drugs. The typed set includes `c.557A>G`, a decreased-function allele carried mainly by people
  of African ancestry,[^dpyd-c557-ancestry] but not every array includes that probe. When yours
  does not — but the rest of the *DPYD* markers are covered — the card still shows the diplotype
  that was directly called, which will read `*1/*1` **Normal Metabolizer** if nothing else was
  found, and marks the call *Partial* while naming the allele as one it could not exclude. The
  fluoropyrimidine guidance beside it is then **Conservative**: it is based on the lowest-activity
  diplotype the untyped allele could still make, so you are not given label-recommended dosing on
  the strength of a position that was never assayed.
- **A very sparse array gets no *DPYD* drug guidance at all.** If most of the gene's defining
  positions are missing and none of the typed ones shows a variant, the call is reported as
  *Insufficient* and no fluoropyrimidine recommendation is shown — withholding is more honest than
  either a reassuring call or a conservative one built on almost no coverage. If one of those
  positions *does* show a variant, that is a real finding and its guidance is kept rather than
  being withheld along with the rest.
  For *UGT1A1*, a normal `*1/*1` call does **not** rule out reduced *UGT1A1* activity from
  untyped `*28`, which matters for irinotecan and atazanavir prescribing
  context.[^ugt1a1-irinotecan][^ugt1a1-atazanavir]
- **An untyped position can leave a *CYP3A5* drug recommendation undetermined.** Tacrolimus
  guidance turns on whether you express the enzyme, and the no-function alleles — chiefly `*3`
  (`rs776746`) — are what decide that.[^cyp3a5-expression] CPIC gives a starting dose *by
  genotype, when the genotype is known*: an increase for expressers, label-recommended dosing
  for non-expressers.[^cpic-cyp3a5-tacrolimus] If an untyped position leaves **both** groups
  possible, they point in opposite directions, so no tacrolimus recommendation is shown at all
  rather than guessing one — the gene still appears with its *Partial* call and the untyped
  allele listed as indeterminate, and direct *CYP3A5* typing is what resolves it. If the typed
  positions already settle expresser status the recommendation is still shown: a sample
  homozygous for a no-function allele is a non-expresser whichever way the untyped position
  falls, so it keeps its label-recommended dosing.
- Some diplotypes are inferred from unphased data and carry phase ambiguity.

!!! danger "Never change a medication based on this"
    Pharmacogenomic results here are research/educational. Do not start, stop, or change any
    medication without your prescriber, who can order a clinical PGx test if warranted.

[^dpyd-c557-ancestry]: [Prevalence of the DPYD variant (Y186C) in Brazilian individuals of African ancestry](https://doi.org/10.1007/s00280-019-03974-4) (Cunha-Junior et al., 2019, *Cancer Chemotherapy and Pharmacology*; [PMID 31641844](https://pubmed.ncbi.nlm.nih.gov/31641844/); accessed 2026-10-15) reports that this allele is found almost exclusively in African populations, measuring it in 3 of 115 African Brazilians — a 2.6% carrier prevalence, equivalent to a 1.3% allele frequency. [gnomAD v2.1.1](https://gnomad.broadinstitute.org/) (accessed 2026-10-15) independently finds the same ancestry pattern in a separate and much larger dataset: an African allele frequency of 2.30% in the exomes against 0.0053% in non-Finnish Europeans, with 374 of 417 observed copies in the African group, and 1.87% against 0% in non-Finnish Europeans in the genomes. The two studies agree on the ancestry pattern rather than on a single frequency; their point estimates differ, as different populations and sample sizes would predict. Its CPIC decreased-function assignment comes from the CPIC fluoropyrimidine guideline ([PMID 29152729](https://pubmed.ncbi.nlm.nih.gov/29152729/); accessed 2026-10-15).
[^ugt1a1-repeat]: [Correlation between the UDP-glucuronosyltransferase (UGT1A1) TATAA box polymorphism and carcinogen detoxification phenotype](https://doi.org/10.1158/1055-9965.epi-03-0070) (Fang & Lazarus, 2004, *Cancer Epidemiology, Biomarkers & Prevention*; [PMID 14744740](https://pubmed.ncbi.nlm.nih.gov/14744740/)) describes `UGT1A1*28` as an additional `(TA)` repeat in the promoter TATA box linked to lower UGT1A1 expression/activity.
[^ugt1a1-irinotecan]: [Dutch Pharmacogenetics Working Group guideline for the gene-drug interaction between UGT1A1 and irinotecan](https://doi.org/10.1038/s41431-022-01243-2) (Hulshof et al., 2023, *European Journal of Human Genetics*; [PMID 36443464](https://pubmed.ncbi.nlm.nih.gov/36443464/)) describes UGT1A1 poor metabolizers and genotype-informed irinotecan starting-dose adjustment.
[^ugt1a1-atazanavir]: [Clinical Pharmacogenetics Implementation Consortium guideline for UGT1A1 and atazanavir prescribing](https://doi.org/10.1002/cpt.269) (Gammal et al., 2016, *Clinical Pharmacology & Therapeutics*; [PMID 26417955](https://pubmed.ncbi.nlm.nih.gov/26417955/)) summarizes atazanavir prescribing recommendations when UGT1A1 genotype is known.
[^cpic-cyp3a5-tacrolimus]: [Clinical Pharmacogenetics Implementation Consortium (CPIC) Guidelines for CYP3A5 Genotype and Tacrolimus Dosing](https://doi.org/10.1002/cpt.113) (Birdwell et al., 2015, *Clinical Pharmacology & Therapeutics*; [PMID 25801146](https://pubmed.ncbi.nlm.nih.gov/25801146/); [PMCID PMC4481158](https://pmc.ncbi.nlm.nih.gov/articles/PMC4481158/); accessed 2026-07-30) gives tacrolimus dosing recommendations by CYP3A5 genotype *when known*, distinguishing expressers (extensive and intermediate metabolizers) from non-expressers (poor metabolizers).
[^cyp3a5-expression]: [Sequence diversity in CYP3A promoters and characterization of the genetic basis of polymorphic CYP3A5 expression](https://doi.org/10.1038/86882) (Kuehl et al., 2001, *Nature Genetics*; [PMID 11279519](https://pubmed.ncbi.nlm.nih.gov/11279519/); accessed 2026-07-30) shows the `CYP3A5*3` and `*6` SNPs cause alternative splicing and protein truncation so CYP3A5 is absent from tissue, and that only people carrying at least one `*1` allele express CYP3A5 — evidence independent of the CPIC dosing guideline. Star-allele nomenclature follows [PharmVar GeneFocus: CYP3A5](https://doi.org/10.1002/cpt.2563) (Rodriguez-Antona et al., 2022, *Clinical Pharmacology & Therapeutics*; [PMID 35202484](https://pubmed.ncbi.nlm.nih.gov/35202484/); [PMCID PMC9399309](https://pmc.ncbi.nlm.nih.gov/articles/PMC9399309/); accessed 2026-07-30).
[^cpic-terms]: [Standardizing terms for clinical pharmacogenetic test results: consensus terms from the Clinical Pharmacogenetics Implementation Consortium (CPIC)](https://doi.org/10.1038/gim.2016.87) (Caudle et al., 2017, *Genetics in Medicine*) defines consensus pharmacogenetic phenotype terminology for consistent PGx interpretation.
[^cpic-cyp2d6-as]: [Standardizing CYP2D6 Genotype to Phenotype Translation: Consensus Recommendations from the Clinical Pharmacogenetics Implementation Consortium and Dutch Pharmacogenetics Working Group](https://doi.org/10.1111/cts.12692) (Caudle et al., 2020, *Clinical and Translational Science*; [PMID 31647186](https://pubmed.ncbi.nlm.nih.gov/31647186/); [PMCID PMC6951851](https://pmc.ncbi.nlm.nih.gov/articles/PMC6951851/)) describes the CYP2D6 activity-score system as summed allele activity values and explains that phenotype translation depends on consensus, gene-specific thresholds.

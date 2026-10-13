# Familial hypercholesterolemia

--8<-- "health-disclaimer.md"

This view focuses on **familial hypercholesterolemia (FH)** — an inherited tendency toward
very high LDL cholesterol — by combining three lines of evidence. It is framed against the
clinical FH criteria (Dutch Lipid Clinic Network, Simon Broome) for **educational reference
only**, and is **not** a clinical FH diagnosis.

## What it looks at

1. **Monogenic FH variants** in *LDLR*, *APOB*, and *PCSK9* (read from the
   [cardiovascular module](cardiovascular.md)'s findings). *APOB* findings follow that
   module's existing FH criteria, including its ClinVar condition-label and
   lower-penetrance/risk-allele filters.
2. **APOB R3527Q** (rs5742904), the familial-defective-apoB variant, if it's typed and you
   carry it.
3. An **LDL-C polygenic score** (PGS000688) summarising many small-effect variants.

## What you'll see

A three-part assessment: any monogenic FH variants you carry, your APOB R3527Q status, and
your LDL-C polygenic burden. The polygenic part shows a raw score and how much of it your
array actually covers. It also shows where the score comes from: the population it was
developed in, the study's sample size, and how many variants it uses. The card's ancestry
caveat refers to that development population.

!!! note "It does not measure your cholesterol"
    This module reads genetics only — it does **not** measure your LDL-C or check for clinical
    signs of FH. The polygenic percentile is withheld when it can't be calibrated on
    un-imputed array data.

## Good to know

- It doesn't detect FH variants on its own — it summarises the cardiovascular module's
  findings plus the single APOB variant and the polygenic score.
- **APOB is bidirectional.** *APOB* findings with low-LDL-only ClinVar labels—and
  protein-truncating *APOB* variants linked to familial hypobetalipoproteinemia even when
  ClinVar labels aggregate both directions—are reported under Other lipid metabolism and
  excluded from FH status. Other *APOB* findings follow the cardiovascular module's existing
  FH criteria, including its ClinVar condition-label and lower-penetrance/risk-allele
  filters. Human genetic studies associate protein-truncating variants with low LDL
  cholesterol
  ([PMID:30939045](https://pubmed.ncbi.nlm.nih.gov/30939045/);
  [DOI:10.1161/CIRCGEN.118.002376](https://doi.org/10.1161/CIRCGEN.118.002376)
  (accessed 2026-07-31); [PMID:36723951](https://pubmed.ncbi.nlm.nih.gov/36723951/);
  [DOI:10.1001/jamacardio.2022.5271](https://doi.org/10.1001/jamacardio.2022.5271)
  (accessed 2026-07-31)).
- **PCSK9 is bidirectional too.** FH-causing *PCSK9* variants act by gain of function,
  so *PCSK9* nonsense and frameshift variants are reported under Other lipid metabolism and
  excluded from FH status, even when ClinVar labels them as FH. Other *PCSK9* findings
  follow the cardiovascular module's existing FH criteria. Human genetic studies associate
  nonsense and frameshift *PCSK9* variants with low LDL cholesterol:
  nonsense variants
  ([PMID:15654334](https://pubmed.ncbi.nlm.nih.gov/15654334/);
  [DOI:10.1038/ng1509](https://doi.org/10.1038/ng1509) (accessed 2026-10-11);
  [PMID:16989838](https://pubmed.ncbi.nlm.nih.gov/16989838/);
  [DOI:10.1016/j.atherosclerosis.2006.08.039](https://doi.org/10.1016/j.atherosclerosis.2006.08.039)
  (accessed 2026-10-11)),
  a frameshift variant ([PMID:17170371](https://pubmed.ncbi.nlm.nih.gov/17170371/);
  [DOI:10.1161/01.ATV.0000255311.26383.2f](https://doi.org/10.1161/01.ATV.0000255311.26383.2f)
  (accessed 2026-10-11)), and
  protein-truncating variants as a class ([PMID:36723951](https://pubmed.ncbi.nlm.nih.gov/36723951/);
  [DOI:10.1001/jamacardio.2022.5271](https://doi.org/10.1001/jamacardio.2022.5271)
  (accessed 2026-10-11)). *PCSK9* splice-site variants keep the existing FH criteria:
  studies count only splice changes that a stricter loss-of-function filter accepts, and a
  splice annotation alone does not show that.
- High LDL cholesterol has many causes; genetics is only one. Discuss any concern, and your
  actual lipid panel, with a clinician.

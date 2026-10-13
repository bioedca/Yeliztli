# Cardiovascular

--8<-- "health-disclaimer.md"

The cardiovascular module screens a 16-gene panel for pathogenic variants linked to
inherited heart and lipid conditions, and summarises familial-hypercholesterolemia status.

## What it looks at

- **Familial hypercholesterolemia (FH):** *LDLR*, and FH-causing *PCSK9* and *APOB*
  variants
- **Other lipid metabolism:** *LPA*, *ABCG5*, *ABCG8*, and *APOB* findings scoped to
  low-LDL conditions, including protein-truncating variants linked to familial
  hypobetalipoproteinemia, and *PCSK9* nonsense and frameshift variants
- **Channelopathies** (arrhythmia syndromes): *KCNQ1*, *SCN5A*, *KCNH2*, *RYR2*
- **Cardiomyopathies:** *MYBPC3*, *MYH7*, *TNNT2*, *LMNA*, *DSP*, *PKP2*

## What you'll see

- **Per-variant findings** — ClinVar Pathogenic/Likely-Pathogenic variants with review stars,
  accession, inheritance pattern, an evidence rating, and the relevant cardiovascular
  category. ClinVar lower-penetrance/risk-allele findings are stored under a distinct findings
  category from high-penetrance P/LP variants, although the current page displays both
  categories together.[^clingen-risk-allele]
- **An FH status summary** — Positive or Negative, listing the affected genes and the
  strongest evidence found.

For recessive conditions, you're told whether you're a *carrier* or *affected*, based on how
many copies you carry.

## Good to know

- A negative result doesn't exclude these conditions — the panel types specific variants, not
  full gene sequences.
- **APOB is bidirectional.** *APOB* findings with low-LDL-only ClinVar labels—and
  protein-truncating *APOB* variants linked to familial hypobetalipoproteinemia even when
  ClinVar labels aggregate both directions—are reported under Other lipid metabolism and
  excluded from FH status. Other *APOB* findings follow the module's existing FH criteria,
  including its ClinVar condition-label and lower-penetrance/risk-allele filters. This
  distinction is supported by human genetic studies
  ([PMID:30939045](https://pubmed.ncbi.nlm.nih.gov/30939045/);
  [DOI:10.1161/CIRCGEN.118.002376](https://doi.org/10.1161/CIRCGEN.118.002376)
  (accessed 2026-07-31); [PMID:36723951](https://pubmed.ncbi.nlm.nih.gov/36723951/);
  [DOI:10.1001/jamacardio.2022.5271](https://doi.org/10.1001/jamacardio.2022.5271)
  (accessed 2026-07-31)).
- **PCSK9 is bidirectional too.** FH-causing *PCSK9* variants act by gain of function,
  so *PCSK9* nonsense and frameshift variants are reported under Other lipid metabolism and
  excluded from FH status, even when ClinVar labels them as FH. Other *PCSK9* findings
  follow the module's existing FH criteria. Human genetic studies associate nonsense and
  frameshift *PCSK9* variants with low LDL cholesterol:
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
- The dedicated [familial-hypercholesterolemia](familial-hypercholesterolemia.md) view builds
  on these findings and adds an LDL-C polygenic score for a fuller FH picture.

[^clingen-risk-allele]: Schmidt RJ, et al. [Recommendations for risk allele evidence
    curation, classification, and reporting from the ClinGen Low Penetrance/Risk Allele
    Working Group](https://doi.org/10.1016/j.gim.2023.101036). *Genetics in Medicine*.
    2024;26(3):101036. PMID:38054408; DOI:10.1016/j.gim.2023.101036
    (accessed 2026-07-31).

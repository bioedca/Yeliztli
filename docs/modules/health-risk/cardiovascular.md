# Cardiovascular

--8<-- "health-disclaimer.md"

The cardiovascular module screens a 16-gene panel for pathogenic variants linked to
inherited heart and lipid conditions, and summarises familial-hypercholesterolemia status.

## What it looks at

- **Familial hypercholesterolemia (FH):** *LDLR*, *PCSK9*, *APOB*
- **Other lipid metabolism:** *LPA*, *ABCG5*, *ABCG8*
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
- The dedicated [familial-hypercholesterolemia](familial-hypercholesterolemia.md) view builds
  on these findings and adds an LDL-C polygenic score for a fuller FH picture.

[^clingen-risk-allele]: Schmidt RJ, et al. [Recommendations for risk allele evidence
    curation, classification, and reporting from the ClinGen Low Penetrance/Risk Allele
    Working Group](https://doi.org/10.1016/j.gim.2023.101036). *Genetics in Medicine*.
    2024;26(3):101036. PMID:38054408; DOI:10.1016/j.gim.2023.101036
    (accessed 2026-07-31).

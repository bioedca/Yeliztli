# Cardiovascular

--8<-- "health-disclaimer.md"

The cardiovascular module screens a 16-gene panel for pathogenic variants linked to
inherited heart and lipid conditions, and summarises familial-hypercholesterolemia status.

## What it looks at

- **Familial hypercholesterolemia (FH):** *LDLR*, *PCSK9*, and FH-causing *APOB*
  variants
- **Other lipid metabolism:** *LPA*, *ABCG5*, *ABCG8*, and *APOB* findings scoped to
  low-LDL conditions, including protein-truncating variants linked to familial
  hypobetalipoproteinemia
- **Channelopathies** (arrhythmia syndromes): *KCNQ1*, *SCN5A*, *KCNH2*, *RYR2*
- **Cardiomyopathies:** *MYBPC3*, *MYH7*, *TNNT2*, *LMNA*, *DSP*, *PKP2*

## What you'll see

- **Per-variant findings** — ClinVar Pathogenic/Likely-Pathogenic variants with review stars,
  accession, inheritance pattern, an evidence rating, and the relevant cardiovascular
  category.
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
- The dedicated [familial-hypercholesterolemia](familial-hypercholesterolemia.md) view builds
  on these findings and adds an LDL-C polygenic score for a fuller FH picture.

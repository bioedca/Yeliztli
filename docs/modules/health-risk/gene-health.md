# Gene health

--8<-- "health-disclaimer.md"

Gene health gives a **categorical** read on common multifactorial conditions, grouped by body
system. These are common GWAS-style associations — small nudges to risk, not yes/no answers.

## What it looks at

About **40 SNPs** across four pathways:

- **Neurological** — Alzheimer's, Parkinson's, multiple sclerosis, ADHD, migraine
  (*ABCA7*, *CLU*, *SNCA*, *HLA-DRB1*, and others)
- **Metabolic** — type-2 diabetes, obesity, gout, fatty liver
  (*TCF7L2*, *FTO*, *MC4R*, *ABCG2*, …)
- **Autoimmune** — rheumatoid arthritis, type-1 diabetes, IBD, celiac, lupus
  (*PTPN22*, *STAT4*, *HLA-DQB1*, …)
- **Sensory** — age-related macular degeneration, glaucoma, and hereditary or age-related
  hearing loss (*CFH*, *ARMS2*, *MYOC*, *GJB2*, *SLC26A4*, …)

## What you'll see

A **level** for each pathway — *Elevated*, *Moderate*, or *Standard* — plus the individual SNP
calls behind it. Where a SNP overlaps another module (APOE, Allergy, Methylation,
Nutrigenomics, Traits), you'll see a cross-reference.

## Good to know

- **The strongest single risk factors are deliberately *not* here.** *APOE* ε4 and the LRRK2
  Parkinson's variant are excluded and only available through their opt-in
  [gated modules](../gated/apoe.md).
- Weak (1-star) associations can't push a pathway to *Elevated*.
- Some palindromic SNPs (A/T or C/G) can't be strand-resolved from array data and are marked
  **Indeterminate** and left out of the pathway level, rather than guessed.

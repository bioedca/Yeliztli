# APOE

--8<-- "health-disclaimer.md"

!!! info "This is an opt-in module"
    Determined *APOE* results — especially the ε4 status linked to Alzheimer's risk — stay
    **hidden until you explicitly acknowledge** that you want to see them. If the upload does
    not contain enough direct genotype data to make an *APOE* call, Yeliztli reports that
    limitation instead of guessing. You're in control of when (or whether) to reveal a
    determined result.

*APOE* is one of the most studied genes in human health. When your uploaded raw-data file
contains direct calls for both epsilon-defining SNPs, rs429358 and rs7412, Yeliztli determines
your *APOE* **diplotype** (the ε2/ε3/ε4 combination).

## What you'll see

If both ε-defining SNPs are directly typed and callable, you'll see your diplotype
(e.g. ε3/ε4) and three findings:

1. **Cardiovascular** — lipid metabolism and statin-response context.
2. **Alzheimer's disease** — a relative-risk estimate, explicitly framed as a **probabilistic
   risk factor, not a diagnosis**.
3. **Dietary fat response** — how your diplotype relates to saturated-fat sensitivity.

## When APOE can't be determined

An *APOE* result requires direct, callable genotypes at **both** rs429358 and rs7412. If either
SNP is absent from your raw file, appears as a no-call, or has an unexpected allele pattern,
Yeliztli does **not** infer an *APOE* diplotype and does **not** create the three *APOE*
findings. The page may report a status such as **missing SNPs**, **no call**, or **ambiguous**;
that means the array data cannot support a call, not that Alzheimer's-risk status has been
silently determined.

This is expected for many consumer-array exports. The module treats the uploaded file as the
source of truth and does not rescue missing *APOE* SNPs by imputation. 23andMe v5 is the
supported custom-content case most likely to include both ε-defining SNPs directly; older
23andMe v3/v4 and AncestryDNA v2.0 uploads may produce **missing SNPs** if their raw files do
not include both rs429358 and rs7412.

## Good to know

- The Alzheimer's estimate is a **population-average relative risk** — it is **not** tailored
  to your age, sex, or ancestry, and **many ε4 carriers never develop Alzheimer's**.
- There is no preventive treatment that depends on knowing your *APOE* status, which is part
  of why the result is gated behind explicit consent.
- The diplotype is inferred from two epsilon-defining SNPs, rs429358 and rs7412.
- Treat consumer-array *APOE* calls as provisional: these loci are documented array
  weak spots, and array/imputed *APOE* agrees with direct clinical genotyping at
  only ~90% ε genotype / ~93% ε4 status. Confirm any actionable ε4 or ε2 call in a
  CLIA/accredited lab before medical decisions
  ([PMID 24448547](https://pubmed.ncbi.nlm.nih.gov/24448547/),
  [PMID 22972946](https://pubmed.ncbi.nlm.nih.gov/22972946/),
  [PMID 24903779](https://pubmed.ncbi.nlm.nih.gov/24903779/)).

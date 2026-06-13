# Pharmacogenomics

--8<-- "health-disclaimer.md"

Pharmacogenomics looks at genes that affect how your body processes certain medications, and
surfaces the relevant CPIC prescribing guidance.

## What it looks at

**11 CPIC pharmacogenes:** *CYP2D6*, *CYP2C19*, *CYP2C9*, *CYP3A5*, *SLCO1B1*, *DPYD*,
*TPMT*, *UGT1A1*, *NUDT15*, *NAT2*, and *CYP2B6*.

## What you'll see

- **Star-allele diplotypes** (e.g. `*1/*4`) and the resulting **metabolizer phenotype**
  (poor / intermediate / normal / ultrarapid).
- A **three-state call confidence** — *Complete*, *Partial*, or *Insufficient* — so you know
  how much to trust each call.
- **Per-drug prescribing alerts** based on CPIC guidelines, with the CPIC level mapped to an
  evidence rating, plus context from PharmGKB, DPWG, and FDA labels.
- A consolidated **medication-safety summary** grouping the actionable interactions.

## Good to know

- **Arrays can't see everything.** They cannot resolve *CYP2D6*/*CYP2B6* copy-number changes
  or gene conversions, so those calls are *Partial* at best.
- **A normal result isn't a guarantee.** For *DPYD*, only a handful of variants are typed — a
  normal call does **not** rule out DPD deficiency, which matters for certain chemotherapy
  drugs.
- Some diplotypes are inferred from unphased data and carry phase ambiguity.

!!! danger "Never change a medication based on this"
    Pharmacogenomic results here are research/educational. Do not start, stop, or change any
    medication without your prescriber, who can order a clinical PGx test if warranted.

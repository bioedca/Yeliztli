# Hereditary cancer

--8<-- "health-disclaimer.md"

The cancer module looks for inherited variants linked to hereditary-cancer syndromes, and
separately estimates polygenic risk for a few common cancers.

## What it looks at

A **28-gene** hereditary-cancer panel, including *BRCA1*, *BRCA2*, *TP53*, *PALB2*, *ATM*,
*CHEK2*, the Lynch-syndrome mismatch-repair genes (*MLH1*, *MSH2*, *MSH6*, *PMS2*), *APC*,
*MUTYH*, *PTEN*, *STK11*, *CDH1*, and others. It also computes **polygenic risk scores**
(PRS) for breast, prostate, colorectal, and melanoma.

## What you'll see

- **Monogenic findings** — ClinVar Pathogenic/Likely-Pathogenic variants in panel genes,
  grouped by cancer syndrome, each with its ClinVar accession, review stars, inheritance
  pattern, and an evidence rating. For recessive genes, you're told whether you're a
  *carrier* versus *affected*. *BRCA1/BRCA2* findings cross-reference the
  [carrier module](carrier-status.md), where they also appear in a reproductive context.
- **Polygenic scores** — shown as a **population percentile** with a confidence interval,
  never as a raw score or an absolute risk. They are explicitly research-use-only.

!!! note "Percentiles can be withheld"
    A polygenic percentile is only shown when the score is properly calibrated for your
    inferred ancestry. When it isn't, Yeliztli withholds the percentile rather than show a
    misleading number.

## Good to know

- A negative result does **not** rule out hereditary cancer risk — arrays type only specific
  variants, not whole genes.
- Polygenic scores are derived mostly in European-ancestry cohorts; an ancestry-mismatch
  warning is shown when your inferred ancestry differs, and the per-component evidence is
  capped low.
- *MUTYH* single-copy carriers are framed as carriers, not as affected.

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
- **Polygenic scores** — shown as a **population percentile**, never as a raw score,
  confidence interval, or absolute risk. This statement is specific to cancer PRS output:
  PRS percentiles are explicitly research-use-only and are kept separate from the optional
  monogenic absolute-risk context below.
- **Absolute-risk context (optional)** — after explicit opt-in, the Cancer page can show a
  breast-cancer context card. It compares a general-population female lifetime-risk baseline
  from NCI SEER with published female-carrier penetrance estimates for *BRCA1*/*BRCA2*
  Pathogenic/Likely-Pathogenic findings [1]. This is not a PRS-derived estimate and not a
  personalized clinical risk model.

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

## How to read the absolute-risk context

The absolute-risk context is hidden until you opt in because it displays sensitive disease-risk
numbers. When shown, read it as context for clinical follow-up, not as a diagnosis or a
complete personal risk calculation.

- **Population lifetime risk** is the general-population baseline used for comparison. In this
  card it comes from NCI SEER's female breast-cancer statistics; it is not computed from your
  genotype.
- **Carrier penetrance** is the estimated proportion of people with a specific pathogenic
  genotype who develop the condition over a defined time window.
- **Cumulative risk to age 80** means the estimated probability that a female *BRCA1* or
  *BRCA2* carrier is diagnosed with breast cancer by age 80 in the cited carrier-cohort study
  [1]. It is a group estimate, not your personal probability.
- **95% CI** means the study's 95% confidence interval around the estimate. It describes
  statistical uncertainty in the published estimate, not a guarantee that an individual's risk
  falls inside that range.

Sex-specific context matters. For XY/male samples, Yeliztli does not show the female
*BRCA1*/*BRCA2* cumulative-risk figures; it shows male-specific framing instead. If biological
sex cannot be resolved from array data, sex-specific numeric penetrance is withheld.

## References

[1] Kuchenbaecker KB, Hopper JL, Barnes DR, et al. [Risks of breast, ovarian, and
contralateral breast cancer for BRCA1 and BRCA2 mutation carriers](https://doi.org/10.1001/jama.2017.7112).
*JAMA*. 2017;317(23):2402-2416. [PMID 28632866](https://pubmed.ncbi.nlm.nih.gov/28632866/).

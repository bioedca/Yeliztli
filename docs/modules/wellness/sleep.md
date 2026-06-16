# Gene sleep

--8<-- "health-disclaimer.md"

The sleep module gives a **categorical** read on caffeine metabolism, sleep quality, and a
couple of sleep-disorder markers, plus GWAS Catalog associations for sleep/circadian traits.

## What it looks at

- **Caffeine & sleep** — *CYP1A2* (rs762551), *ADORA2A* (rs5751876)
- **Sleep quality** — *MEIS1*, *BTBD9* (restless-legs / periodic-limb-movement markers)
- **Sleep disorders** — an HLA-region marker (rs2858884)
- **Sleep/circadian GWAS associations** — your variants matched against GWAS Catalog sleep
  terms (sleep duration, insomnia, chronotype / morningness, …)

## What you'll see

A **level** per pathway (*Elevated* / *Moderate* / *Standard*), per-SNP genotypes and effects,
and a *CYP1A2* caffeine-metaboliser read (rapid / intermediate / slow), which cross-references
[pharmacogenomics](../pharma/pharmacogenomics.md).

## Good to know

- Chronotype / morningness are **not** scored as a dedicated pathway — they surface only
  indirectly through the GWAS Catalog sleep/circadian term matches above. (A dedicated
  chronotype pathway was dropped because its only marker was the *PER3* 54-bp VNTR, which
  consumer SNP arrays do not type and no validated array-typeable tag SNP replaces.)
- The HLA-region marker is **not** a validated proxy for the narcolepsy allele
  (HLA-DQB1\*06:02), so **no narcolepsy risk is inferred** — that needs direct HLA typing.
- The *CYP1A2* marker reports caffeine metabolism only; it is not a full star-allele call.

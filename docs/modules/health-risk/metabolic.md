# Metabolic (type-2 diabetes & obesity)

--8<-- "health-disclaimer.md"

The metabolic module estimates **polygenic risk** for type-2 diabetes and for BMI/obesity,
and reports a few well-established anchor variants.

## What it looks at

- **Polygenic scores:** type-2 diabetes (PGS000713) and a multi-ancestry BMI score
  (PGS005198).
- **Anchor SNPs:** *TCF7L2* (rs7903146), *FTO* (rs9939609), *MC4R* (rs17782313) — the
  best-known common variants for these traits.

## What you'll see

- **Polygenic findings** with the **coverage fraction** (how many of the score's variants your
  array actually typed) and whether that's sufficient. Because consumer arrays aren't imputed,
  typical coverage is only ~35–60%, so the percentile is **withheld** and the coverage is
  reported instead.
- **Anchor-SNP findings** with your genotype and a strand-harmonised dosage, marked
  *indeterminate* when a palindromic variant can't be resolved.

## Good to know

- These are research-use estimates, not predictions — most of your metabolic risk comes from
  lifestyle and environment, not these variants.
- An ancestry-mismatch warning is shown when your inferred ancestry differs from the score's
  source population.

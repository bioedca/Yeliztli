# Ancestry

Yeliztli estimates your **global ancestry** from genome-wide patterns, and (optionally)
assigns your maternal and paternal **haplogroups**.

!!! note "Ancestry results are statistical estimates"
    Ancestry inference compares you to a reference panel; results are estimates with real
    uncertainty, sensitive to how many markers your file covers and to admixture. They are not
    statements of identity or fact.

## What it looks at

- **5,000 ancestry-informative markers (AIMs)** projected against a 3,419-sample reference
  panel spanning **7 super-populations** (AFR, AMR, CSA, EAS, EUR, MID, OCE).
- **Haplogroup-defining SNPs** for mitochondrial (maternal) and Y-chromosome (paternal)
  lineages.

## What you'll see

- A **top population** (or *Admixed* / *Uncertain* when the data is between clusters or too
  sparse).
- **Admixture fractions** per population with confidence intervals, shown as a bar chart, plus
  an interactive **PCA plot** placing you among the reference populations.
- **Haplogroup assignment cards** when a lineage is supported beyond the tree root.
  Mitochondrial analysis runs for every sample; Y-chromosome analysis runs only for samples
  inferred as XY. Each card shows a percentage currently labelled *confidence*, an overall
  `N / M defining SNPs matched` count, and a traversal path with an `N/M` count for each tree
  node. The percentage is path-marker support that is sensitive to array coverage — **not the
  probability that the assignment is correct**. See [how to read the haplogroup
  badge](../../ancestry-methods.md#how-to-read-the-haplogroup-confidence-badge).

## Good to know

- A **confident single-population** call needs good marker coverage (>55%); below that you'll
  see *Uncertain*. Genuinely admixed samples are reported as *Admixed* rather than forced into
  one group.
- Missing markers are filled with panel averages before projection, which can pull sparse
  samples toward the centre and soften the signal.
- Untyped or no-call haplogroup markers remain in the badge denominator. A low haplogroup
  percentage can therefore reflect a sparse input file rather than contradictory evidence,
  and percentages should not be compared across arrays with different marker coverage.
- For the statistics — PCA projection, NNLS admixture, local ancestry inference, accuracy,
  and limitations (with citations) — see
  **[Ancestry methods & validation](../../ancestry-methods.md)**.

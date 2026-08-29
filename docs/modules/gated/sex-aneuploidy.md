# Sex-chromosome aneuploidy

--8<-- "health-disclaimer.md"

!!! info "This is an opt-in, sensitive screen"
    Because this result can be psychosocially significant, it stays **hidden until you
    explicitly choose to view it**. It never changes the sex recorded for your sample.

This module screens your genotype data for a signature consistent with **XXY (Klinefelter
syndrome)**.

## What you'll see

A single result: **possible XXY**, **manual review**, **no aneuploidy signal**, or
**indeterminate** (when probe coverage is insufficient), along with the supporting counts
(heterozygous X calls and Y-probe presence). It is framed as a **screen that requires clinical
karyotype confirmation**.

## Good to know

- This is a **screen, not a diagnosis** — a positive signal needs confirmation by clinical
  karyotyping.
- From array genotypes, **XXY is the only sex-chromosome aneuploidy reliably detectable**. It
  cannot detect Turner syndrome (45,X) or XYY (those need different signal types), and XXX is
  indistinguishable from typical XX.
- Minimum probe-count thresholds guard against false calls from stray probes, and discordant
  X/Y signals in the manual-review band are not reported as clean negative screens.
- **An unresolvable X dosage with a chromosome-Y signal returns manual review, not a
  negative.** X-chromosome heterozygosity separates one X from two X
  (`PMID:28035028` / `DOI:10.1093/bioinformatics/btw696`, accessed 2026-08-29), and a level
  between the two is not something this array can resolve. When that happens *and* a
  chromosome-Y signal is present above the noise floor, the screen withholds the negative
  rather than asserting one — it is explicitly *not* a positive finding either; the screen
  simply cannot tell. With no chromosome-Y signal the result stays a negative, because the XXY
  pattern needs a Y whatever the X reads. These are the same samples the app's sex inference
  also declines to resolve.

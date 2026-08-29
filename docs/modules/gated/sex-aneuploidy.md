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
- **An unresolvable X dosage alongside a chromosome-Y signal returns manual review, not a
  negative.** Some samples fall between the levels this screen can tell apart. When that
  happens *and* there is a chromosome-Y signal above the noise floor — including one too weak
  to meet the presence threshold — the screen withholds the negative rather than asserting
  one. It is explicitly *not* a positive finding either; the screen cannot tell. With no
  chromosome-Y signal the result stays a negative, because the XXY pattern needs a Y whatever
  the X reads (`PMID:39806878`; `PMID:39932051`, both accessed 2026-08-29). These are the same
  samples the app's sex inference also declines to resolve. Evidence for the two background
  facts this behaviour rests on is recorded in
  `data/science-evidence/2026-08-29-xxy-screen-ambiguous-x-2040/`.

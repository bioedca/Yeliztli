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
  one. It is explicitly *not* a positive finding either; the screen cannot tell. When enough
  chromosome-Y probes were typed to judge and no Y signal is found, the result stays a
  negative; when too few were typed to judge at all, the result is *indeterminate*. These are
  the same samples the app's sex inference also declines to resolve.

# Gene fitness

--8<-- "health-disclaimer.md"

The fitness module gives a **categorical** read on exercise-related traits. Individual genetic
effects on athletic performance are small and non-deterministic — treat this as interesting
context, not destiny.

## What it looks at

**8 SNPs** across four pathways:

- **Endurance** — *PPARGC1A*, *AMPD1*, and the well-known *ACTN3* R577X
- **Power** — *ACE* (an rs4341 I/D proxy — see *Good to know*), *MCT1*
- **Recovery & injury** — *COL5A1*, *COL1A1* (connective-tissue)
- **Training response** — *FTO*

## What you'll see

A **level** per pathway (*Elevated* / *Moderate* / *Standard*), with per-SNP findings. *ACTN3*
is reported with its familiar three-state call (RR / RX / XX), and the *ACE* proxy carries a
caveat.

## Good to know

- The *ACE* I/D variant isn't directly on the array — it's inferred from a nearby proxy SNP,
  and that proxy's accuracy varies by ancestry. A pathway level is the highest category across
  its called SNPs, so this ACE proxy can on its own set the **Power** level to *Elevated* — read
  that level with the proxy caveat in mind.
- Weak (1-star) variants are capped at *Moderate*, and strand-ambiguous palindromic SNPs are
  marked *indeterminate* and left out of the pathway level.
- Sport-specific genetic associations are heterogeneous; these variants predict very little
  about any one person.

# Bone density (eBMD)

--8<-- "health-disclaimer.md"

This module estimates a **polygenic score** for heel estimated bone-mineral density (eBMD), a
measure related to fracture risk.

## What it looks at

A single published polygenic score, **PGS000657** (gSOS, Forgetta et al. 2020), summarising
many small-effect variants associated with heel bone density.

## What you'll see

When available, a polygenic result with a raw score, z-score, population percentile, and the
SNP coverage your array provided — plus an ancestry-mismatch warning when relevant.

Direction matters for this score: a **lower** heel eBMD percentile means lower genetically
predicted bone density and higher fracture-risk context. A high percentile means higher
genetically predicted bone density and is protective.

!!! warning "Often unavailable by default"
    PGS000657 is distributed under a **non-commercial** license (CC BY-NC-ND), so Yeliztli does
    **not** bundle it. This module reports *unavailable* unless you have separately fetched the
    score into your local scores database. When the score isn't calibrated for your ancestry,
    the percentile is withheld.

## Good to know

- This is a **research-grade risk-stratification** signal only — it is **not** a substitute for
  a DXA bone-density scan (the diagnostic standard) or a validated tool like FRAX.
- Its evidence is intentionally capped low.

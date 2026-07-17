---
applyTo: "backend/data/**"
---

# Reference-data & scientific-correctness instructions

Files here are curated reference data — panels, CPIC allele/diplotype tables,
ClinGen/PGx data — whose correctness is a **scientific fact**, not a coding
preference. A wrong value looks right and ships silently. When writing or
reviewing changes here, hold the highest bar.

## Genome build & strand

- Coordinates are **GRCh37 / b37**. A GRCh38 position in a GRCh37 context is a
  defect — flag it.
- **CPIC allele-defining variants** (`backend/data/cpic/*`) must be recorded on the
  **plus (+) strand**, with `alt` = the defining base (NOT a raw genomic-VCF
  orientation). The classic trap is minus-strand genes (e.g. `rs776746`,
  `rs3892097`) where the naïvely-copied base is the complement.
- **Panel JSON `ref_allele`** is the **non-risk** allele of the {risk, ref} pair —
  it is *not* necessarily the genome-reference base, and risk ≠ ref. Verify the
  risk-allele direction against GWAS Catalog / Ensembl, not by assuming reference.
- Palindromic (A/T, C/G) SNPs are strand-ambiguous; treat them with the project's
  strand-ambiguity guards rather than assuming an orientation.

## Evidence & citations

- Every value that encodes a biological/clinical fact must be **verifiable against
  an authoritative source**: CPIC API, GWAS Catalog, Ensembl GRCh37 REST, gnomAD,
  dbSNP, ClinVar, PharmGKB, PGS Catalog. Record provenance (source, version/build,
  accessed date, license).
- Carry a citation with the datum where the format allows it: `PMID:… / DOI:… /
  ChEMBL:…` + `(accessed YYYY-MM-DD)`.
- **High-stakes values** (allele function/strand, risk-allele identity, a
  metabolizer call, a clinical threshold) need **≥2 independent, agreeing
  sources**. On disagreement, **withhold and flag** — don't pick a side.

## Never fabricate

- **Do not invent, stub, or placeholder** reference values to close a gap. A
  placeholder distribution or a guessed frequency produces wrong results that look
  right (cf. the PRS reference distribution that was a placeholder `0/1` and
  inflated every percentile).
- If the defensible data genuinely doesn't exist, **withhold** the result and say
  what real source would close the gap — don't emit a fabricated one.
- Check licensing/usability against `docs/external-inputs-strategy.md` before
  bundling any newly sourced data.
- Land new reference data **with a test** that locks its shape/values so the gap
  can't silently regress — and never weaken an existing test to match the data.

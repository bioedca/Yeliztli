# Carrier status

--8<-- "health-disclaimer.md"

Carrier screening looks for a single copy of a disease-causing variant in genes where **two**
copies (one from each parent) would be needed to cause a recessive condition. Being a carrier
usually doesn't affect your own health, but it's relevant for reproductive planning.

## What it looks at

A 7-gene reproductive panel:

- *CFTR* (cystic fibrosis), *HBB* (sickle cell / beta-thalassemia), *GBA* (Gaucher),
  *HEXA* (Tay-Sachs), *SMN1* (spinal muscular atrophy)
- *BRCA1* / *BRCA2* — dual-role, also reported by the [cancer module](cancer.md)

## What you'll see

**Heterozygous** (single-copy) Pathogenic/Likely-Pathogenic variants, grouped by inheritance
pattern, each with ClinVar significance, review stars, an evidence rating, the associated
condition, and reproductive context. A single recessive variant means you are typically an
**unaffected carrier**, so these findings are framed around reproductive risk and partner testing.

For **autosomal-recessive** genes, the module also surfaces **biallelic** patterns as
**affected-status** findings — worded as a disease-state result rather than a "typically
unaffected" carrier, with copy-number caveats and a prompt to confirm with clinical-grade
testing:

- a **homozygous** (two-copy) P/LP variant; and
- **two distinct** heterozygous P/LP variants in the same gene — a **possible compound
  heterozygote**, flagged as *possible* because genotyping arrays can't phase the variants
  (i.e. tell whether they sit on opposite chromosomes); it is affected-status only if they are
  *in trans*, which clinical testing would confirm.

- *HBB* rs334 (sickle-cell **trait**) findings include personal health context (kidney, clot,
  and exertional considerations).
- *BRCA1/2* findings note their dual role and cross-link to the cancer module.

## Good to know

- *GBA* results are suppressed when derived from array data, because a nearby pseudogene
  (*GBAP1*) makes array genotyping of *GBA* unreliable.
- *SMN1* (spinal muscular atrophy) is **not meaningfully screened from array data.** SMA
  carrier status is a *copy-number* determination — the *SMN1* exon-7 deletion behind the
  great majority of carriers — which a SNP array cannot measure; clinical screening uses
  dosage assays (qPCR/MLPA) that detect ~96% of carriers, and ACOG/ACMG recommend it
  pan-ethnically.[^smn1] This module checks only the rare intragenic *SMN1* point mutations
  (~5% of pathogenic alleles), and only if they are on your chip — so **a negative or absent
  *SMN1* result here does not reduce your chance of being an SMA carrier.** For reproductive
  planning, seek clinical *SMN1* dosage carrier screening.
- Carrier screening is not exhaustive — these are specific common variants, not full gene
  sequencing.

[^smn1]: [The clinical utility of a risk-modifying SNP to detect carriers for spinal muscular atrophy with increased sensitivity](https://doi.org/10.1002/mgg3.1897) (Ware et al., 2022, *Mol. Genet. Genomic Med.*) — routine *SMN1* copy-number assessment detects ~96% of carriers; current guidelines recommend pan-ethnic SMA carrier screening.

# SLC19A1 rs1051266 nomenclature evidence for #2023

## Claim and scope

`backend/data/panels/methylation_panel.json` labels SLC19A1 rs1051266. The label
used to read `G80A (His27Arg)`; it now reads `A80G (His27Arg)`, and the row's
`recommendation_text` records `G80A` as the legacy spelling.

**This packet supports one factual claim: which base the GRCh37 reference carries
at SLC19A1 c.80, and therefore which direction the coding shorthand must run to
agree with `p.His27Arg`.**

It asserts nothing about biology. In particular it does **not** support, and must
not be read as supporting, any claim about which rs1051266 allele reduces folate
transport, alters folate status, or changes methotrexate response. The row's
`risk_allele`/`ref_allele` pair and its `genotype_effects` categories are
unchanged by this work, and the direction-of-effect question is deliberately left
open.

## The claim, and why it needs no biology

Residue 27 is codon c.79-81, so c.80 is codon position 2.

- His is encoded by `CAT`/`CAC` — c.80 = **A**
- Arg is encoded by `CGT`/`CGC`/`CGA`/`CGG` — c.80 = **G**

A single-base change producing His→Arg at that codon is therefore A→G, written
`c.80A>G` and shortened to `A80G`. `G80A` asserts G→A, which is the reverse
substitution (Arg27His). The genetic code is settled and is not sourced here.

What *does* need a source is which residue the reference carries, since that
fixes which of the two spellings is the HGVS-conformant one.

## Sources

Two independent upstreams, not sharing a dataset or an assertion chain:

- **NCBI dbSNP, RefSNP v2** — `rs1051266`, not merged, on the RefSeq transcript
  `NM_194255.4` with protein `NP_919231.1`.
  `NM_194255.4:c.80=` maps to `NP_919231.1` residue 27 **H→H**, and
  `NM_194255.4:c.80A>G` to residue 27 **H→R**, `missense_variant`.
  Public-domain US Government work (NLM). Accessed 2026-08-10.
- **Ensembl GRCh37 REST** — `rs1051266` maps to chr21:46,957,794 on the plus
  strand with `allele_string` `T/C/G`, `ancestral_allele` `C`, `minor_allele` `C`
  (MAF 0.4886). SLC19A1 is transcribed from the minus strand, so plus-strand T is
  coding **A** (the reference) and plus-strand C is coding **G** (the ancestral
  allele). Ensembl data are released under the Apache 2.0 licence.
  Accessed 2026-08-10.

The two agree: the GRCh37 reference carries **80A / His27**, and the alternate is
**80G / Arg27**. `A80G (His27Arg)` is the internally consistent spelling.

Both external sources also agree with the in-repo `tests/fixtures/seed_csvs/vep_seed.csv`
row for rs1051266 (`ref=T`, `alt=C`, `c.80A>G`, `p.His27Arg`), which is recorded
here as corroboration rather than as a source.

## Why the literature disagrees with itself, and what the row now says

The locus carries two competing names, and the row's own three citations are
split between them:

| PMID | Spelling used in the title |
| --- | --- |
| 16750224 | G80A |
| 24597986 | G80A |
| 33935279 | A80G |

The split has a mechanical cause rather than a factual one: the reference genome
carries the **derived** allele at this locus (reference A/His27; ancestral
G/Arg27, per the Ensembl `ancestral_allele` above), so a name written against the
ancestral allele runs G→A while a name written against the reference runs A→G.

The panel had one field from each frame, which is why the rendered string could
not be read under either convention. The fix puts the rendered label in the
reference frame — the frame `hgvs_protein` was already in, and the one HGVS
requires — and names the legacy spelling in the row's prose so a reader following
those two citations is not left to rediscover the conflict.

## A contradicting in-repo artifact, retained deliberately

`bundles/vep_bundle.db` as committed at `origin/main` reports
`hgvs_protein = p.Arg27His` for this rsID — the opposite polarity to both sources
above. It is **not** treated as evidence here, because it is demonstrably not
recording reference/alternate at all: all 102,254 of its single-base rows have
`ref` alphabetically before `alt`, with zero exceptions, where a genuine
reference-first assignment would be near 50/50. Its `hgvs_protein` is
correspondingly inverted for the whole T/C class — rs1051266, PON1 rs662
(`p.Arg192Gln` for `p.Gln192Arg`) and KCNJ11 rs5219 (`p.Glu23Lys` for
`p.Lys23Glu`) are all reversed relative to `vep_seed.csv`.

The raw query is kept in `raw/` as the record of that contradiction. The artifact
itself is written up as a separate issue; it is a live trap for anyone who reads
it as authoritative, which is exactly how #2023 described it.

## Files

- `queries.json` — the exact requests, with URLs, timestamps and result mapping.
- `raw/dbsnp-refsnp-1051266-nm194255-extract-2026-08-10.json` — reduced dbSNP
  response (retention and drop rules recorded inside the file).
- `raw/ensembl-grch37-rs1051266-2026-08-10.json` — full Ensembl response.
- `raw/in-repo-vep-bundle-rs1051266-DISCREPANT-2026-08-10.json` — the
  contradicting in-repo artifact and the measurement that disqualifies it.

No sample, genotype or other personal data was read, transmitted or stored at any
point; both external lookups are single-rsID metadata queries.

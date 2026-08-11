# SLC19A1 rs1051266 nomenclature evidence for #2023

## Claim and scope

`backend/data/panels/methylation_panel.json` labels SLC19A1 rs1051266. The label
used to read `G80A (His27Arg)`; it now reads `A80G (His27Arg)`, and the row's
`recommendation_text` records `G80A` / `c.80G>A` as the competing spelling.

**This packet supports one factual claim: which base and residue the GRCh37
reference carries at SLC19A1 c.80 / residue 27, and therefore which direction the
coding shorthand must run to agree with `p.His27Arg`.**

It asserts nothing about biology. In particular it does **not** support, and must
not be read as supporting, any claim about which rs1051266 allele reduces folate
transport, alters folate status, or changes methotrexate response — the
literature genuinely disagrees on that (see *Direction of effect* below). The
row's `risk_allele`/`ref_allele` pair, its `genotype_effects` categories and its
evidence level are unchanged by this work.

## The claim, and why most of it needs no biology

Residue 27 is codon c.79-81, so c.80 is codon position 2.

- His is encoded by `CAT`/`CAC` — c.80 = **A**
- Arg is encoded by `CGT`/`CGC`/`CGA`/`CGG` — c.80 = **G**

A single-base change producing His→Arg at that codon is therefore A→G, written
`c.80A>G` and shortened to `A80G`. `G80A` asserts G→A, which is the reverse
substitution (Arg27His). The genetic code is settled and is not sourced here.

What *does* need a source is which residue the reference carries, since that
fixes which of the two spellings is the HGVS-conformant one.

## Sources, and why the two-source rule does not apply here

- **NCBI dbSNP, RefSNP v2** — `rs1051266`, not merged, on the RefSeq transcript
  `NM_194255.4` with protein `NP_919231.1`. `NM_194255.4:c.80=` maps to residue
  27 **H→H**, and `NM_194255.4:c.80A>G` to residue 27 **H→R**,
  `missense_variant`. Public domain (NLM) (accessed 2026-08-10).
- **UniProtKB `P41440` (S19A1_HUMAN)** — canonical sequence version 3, 591 aa,
  CRC64 `0437B1615F5517EB`. Residues 20-35 are `PELRSWRHLVCYLCFY`, so **residue
  27 is H (His)**. CC BY 4.0 (accessed 2026-08-10).
- **Ensembl GRCh37 REST** — chr21:46,957,794, plus strand, `allele_string`
  `T/C/G`, `ancestral_allele` `C`, `minor_allele` `C` (MAF 0.4886). SLC19A1 is
  minus-strand, so plus-strand T is coding **A** and plus-strand C is coding
  **G**. Apache 2.0 (accessed 2026-08-10).

**None of these is independent of the others, and this packet got that wrong
twice before saying so.** The first version named dbSNP and Ensembl as the
independent pair; Ensembl's own response declares `"source": "Variants (including
SNPs and indels) imported from dbSNP"`. The second named UniProt instead — but
`P41440` explicitly cross-references `NP_919231.1` / `NM_194255.4`, the exact
RefSeq records dbSNP reports against (`raw/uniprot-P41440-crossrefs-2026-08-10.json`) (accessed 2026-08-10).
Both claims were wrong, and both were caught in review rather than here.

**The deeper error was invoking the gate at all.** The contract's high-stakes rule
covers facts where being wrong changes a clinical interpretation — allele
direction, risk-allele identity, a metabolizer call, a shipped threshold. *Which
spelling names the SNP* is not one of those: no genotype call, category, evidence
level or effect claim depends on it. Classifying a rendered string as high-stakes
set up a gate that then could not be met, and two attempts to satisfy it named
sources that were not independent.

It could not have been met in any case. "Which residue does the human reference
carry at position 27" is a lookup in a single curated artifact, not a replicated
measurement. There is one GRCh37 and one RefSeq curation of SLC19A1; every database
reports that same choice, so no second independent determination exists to be
found. Hunting for one produces the appearance of corroboration, not corroboration.

**The high-stakes fact at this locus is the direction of effect** — which allele
reduces folate transport. That one *is* contested, *is* withheld, and *is* filed
separately. This change does not touch it: `risk_allele`, `ref_allele`, every
`genotype_effects` category and the evidence level are unchanged, and the effect
prose was softened rather than sharpened once the conflict came to light.

**What makes the change safe is that it asserts no new fact.** `hgvs_protein:
p.His27Arg` was already in the row before this work, agrees with the committed
`tests/fixtures/seed_csvs/vep_seed.csv`, and matches every database consulted. All
this change does is conform the single outlier field — `variant_name` — to a claim
the row already made. And the guard it adds enforces *internal consistency*, which
is frame-independent: if the reference frame were ever shown wrong, the correct
repair would be to change `hgvs_protein`, and the guard would then require
`variant_name` to follow rather than stand in the way.

## The primary clones carry the other allele

Following UniProt's EMBL cross-references to the cDNA submissions it derives from:
`AAA98442.1` (placental folate transporter), `AAC50180.1` (reduced folate carrier
protein) and `AAB35058.1` are each 591 aa with residues 20-35
`PELRSWR**R**LVCYLCFY` — **residue 27 is Arg**, not His
(`raw/genbank-primary-cdna-submissions-2026-08-10.fasta`) (accessed 2026-08-10).

That is not a contradiction of the reference; it is the polymorphism. At MAF
≈ 0.49 a clone carrying either allele is unremarkable, and which allele the
assembly later adopted is a separate matter.

**Scope of what this supports, stated narrowly.** The retained artifact is FASTA —
headers and sequence only. It establishes *that these three accessions carry
Arg27*, and nothing else. It does **not** establish that they are the earliest
submissions, that they were produced independently of one another, or that early
authors named the SNP from them: no submission dates, submitters, publication
links or inter-record relationships are retained, and none were checked. An
earlier draft of this section asserted all three of those, which went beyond the
evidence. The causal story about the literature's naming is therefore an unproven
inference and is not relied on anywhere; the shipped user-facing text says only
that the two frames take opposite bases as the reference, which is the observable
fact.

## Files

- `queries.json` — the exact requests and SQL, with URLs, dates and result mapping.
- `raw/dbsnp-refsnp-1051266-full-2026-08-10.json` — source-native dbSNP response, as retrieved.
- `raw/dbsnp-refsnp-1051266-nm194255-extract-2026-08-10.json` — derived extract of the `NM_194255.4` placements, retained alongside the source-native payload for readability.
- `raw/ensembl-grch37-rs1051266-2026-08-10.json` — full Ensembl response.
- `raw/uniprot-P41440-2026-08-10.json` — full UniProtKB response, including the canonical sequence.
- `raw/uniprot-P41440-crossrefs-2026-08-10.json` — UniProt's RefSeq and EMBL cross-references, the evidence that it is not independent of RefSeq.
- `raw/genbank-primary-cdna-submissions-2026-08-10.fasta` — the three original cDNA submissions, which carry Arg27.
- `raw/consensus-search-2026-08-10.json` — all 20 Consensus records, abstracts omitted (see below).
- `raw/scite-and-pubmed-notices-2026-08-10.json` — all 3 Scite and all 3 PubMed records with their editorial-notice status, abstracts omitted.
- `raw/evidence-ladder-2026-08-10.md` — the prose *reading* of those two artifacts. This is analysis, not a source payload.
- `raw/in-repo-vep-bundle-rs1051266-DISCREPANT-2026-08-10.json` — the contradicting in-repo artifact and the measurement that disqualifies it.

**On the discovery artifacts.** Consensus, Scite and PubMed are reached over MCP,
which returns results into the conversation rather than as files, so their bytes
cannot be preserved. The two JSON artifacts above are transcriptions, labelled as
such in `queries.json`. They are complete with respect to *records* — all 20
Consensus results and all 3 of 3 Scite/PubMed records, none selected away — and
drop only abstracts and other copyrighted body text, following the same
bibliographic-metadata-only retention rule the earlier packets in this directory
record. A reviewer can therefore check what was returned and what was omitted,
which is the property that matters; what they cannot do is re-derive an abstract
from here, which is intended.

No sample, genotype or other personal data was read, transmitted or stored at any
point; the external lookups are single-identifier metadata queries and a
literature search on a public SNP.

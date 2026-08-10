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

## Sources, and why the two-source rule cannot be met here

- **NCBI dbSNP, RefSNP v2** — `rs1051266`, not merged, on the RefSeq transcript
  `NM_194255.4` with protein `NP_919231.1`. `NM_194255.4:c.80=` maps to residue
  27 **H→H**, and `NM_194255.4:c.80A>G` to residue 27 **H→R**,
  `missense_variant`. Public domain (NLM). Accessed 2026-08-10.
- **UniProtKB `P41440` (S19A1_HUMAN)** — canonical sequence version 3, 591 aa,
  CRC64 `0437B1615F5517EB`. Residues 20-35 are `PELRSWRHLVCYLCFY`, so **residue
  27 is H (His)**. CC BY 4.0. Accessed 2026-08-10.
- **Ensembl GRCh37 REST** — chr21:46,957,794, plus strand, `allele_string`
  `T/C/G`, `ancestral_allele` `C`, `minor_allele` `C` (MAF 0.4886). SLC19A1 is
  minus-strand, so plus-strand T is coding **A** and plus-strand C is coding
  **G**. Apache 2.0. Accessed 2026-08-10.

**None of these is independent of the others, and this packet got that wrong
twice before saying so.** The first version named dbSNP and Ensembl as the
independent pair; Ensembl's own response declares `"source": "Variants (including
SNPs and indels) imported from dbSNP"`. The second named UniProt instead — but
`P41440` explicitly cross-references `NP_919231.1` / `NM_194255.4`, the exact
RefSeq records dbSNP reports against (`raw/uniprot-P41440-crossrefs-2026-08-10.json`).
Both claims were wrong, and both were caught in review rather than here.

**The rule cannot be satisfied for a claim of this kind, and pretending otherwise
was the actual error.** "Which residue does the human reference carry at position
27" is a lookup in a single curated artifact, not a replicated measurement. There
is one GRCh37 and one RefSeq curation of SLC19A1; every database reports that same
choice, so no second independent determination exists to be found. Hunting for one
produces the appearance of corroboration, not corroboration.

**What makes the change safe is that it asserts no new fact.** `hgvs_protein:
p.His27Arg` was already in the row before this work, agrees with the committed
`tests/fixtures/seed_csvs/vep_seed.csv`, and matches every database consulted. All
this change does is conform the single outlier field — `variant_name` — to a claim
the row already made. And the guard it adds enforces *internal consistency*, which
is frame-independent: if the reference frame were ever shown wrong, the correct
repair would be to change `hgvs_protein`, and the guard would then require
`variant_name` to follow rather than stand in the way.

## The primary clones carry the other allele

Following UniProt's EMBL cross-references to the original cDNA submissions turned
up something the packet had previously only guessed at. All three of the earliest
independent clones — `AAA98442.1` (placental folate transporter), `AAC50180.1`
(reduced folate carrier protein) and `AAB35058.1` — are 591 aa with residues 20-35
`PELRSWR**R**LVCYLCFY`: **residue 27 is Arg**, not His
(`raw/genbank-primary-cdna-submissions-2026-08-10.fasta`, accessed 2026-08-10).

That is not a contradiction of the reference; it is the polymorphism. At MAF
≈ 0.49 a clone carrying either allele is unremarkable, and which allele the
assembly later adopted is a separate matter. But it does explain the naming split
with evidence rather than inference: **the first sequences in the literature
carried G/Arg, so early papers naturally wrote the G allele as the reference and
named the SNP `G80A`.** An earlier draft of the shipped text attributed the legacy
name to the ancestral allele; that was an unsupported inference and was removed.
This is the same story with a source behind it, and it is recorded here rather than
in user-facing text because the user does not need it.

## Why the literature disagrees with itself, and what the row now says

The locus carries two competing names, and the row's own three citations are
split between them:

| PMID | Spelling used in the title |
| --- | --- |
| 16750224 | G80A |
| 24597986 | G80A |
| 33935279 | A80G |

A Consensus search (all 20 records in `raw/consensus-search-2026-08-10.json`) shows the
split is not confined to the legacy shorthand: peer-reviewed papers write
`c.80G>A` and `c.80A>G` for the same rsID.

The panel had one field from each frame — `variant_name` in the G-first frame,
`hgvs_protein` in the reference frame — which is why the rendered string could
not be read under either convention. The fix puts the rendered label in the
reference frame, the frame `hgvs_protein` was already in and the one HGVS
requires, and names the competing spelling in the row's prose so a reader
following those citations is not left to rediscover the conflict.

**What this packet deliberately does not claim** is *why* the G-first frame
exists. Ensembl gives the ancestral coding allele as G, so the reference genome
carries the derived allele here, and an earlier draft of the shipped text
asserted that the legacy name was "written against the ancestral allele". That is
an inference about authors' intent which nothing retained here establishes, so it
was removed in favour of the observable fact: the two frames take opposite bases
as the reference.

## Direction of effect — contested, and untouched

The Consensus results disagree about which allele does what:

| Source | Reported direction |
| --- | --- |
| PMID:19650776 (Stanisławska-Sachadyn 2009, Ann Hum Genet) | GA/AA had *higher* red-cell folate than GG; 80G homozygotes flagged as the at-risk group |
| PMID:33749319 (Naushad 2021, Ann Pharmacother, 18 studies) | 80A allele *increased* methotrexate efficacy and safety |
| PMID:16962770 (Wang 2006, Eur J Cancer) | 80AA associated with *increased* gastro-oesophageal cancer risk |
| PMID:41673870 (Yue 2026, World J Surg Oncol, 13 studies) | 80AA associated with *greater* methotrexate toxicity |
| PMID:24597986 (He 2014) — the row's own citation | *No* influence of G80A on methotrexate toxicity |

The panel's "AA → reduced folate carrier efficiency" is at least in tension with
the first of those. That question is filed separately and is not decided here.

## Corrections and retractions

Checked, not argued. All three cited PMIDs were queried through Scite by DOI and
none carries an editorial notice (retraction, correction, concern or erratum);
PubMed likewise records no `Retracted Publication` or `Erratum` article type for
any of them. dbSNP reports rs1051266 as not merged. The per-record status is in
`raw/scite-and-pubmed-notices-2026-08-10.json`; the reading of it is in
`raw/evidence-ladder-2026-08-10.md`.

## A contradicting in-repo artifact, retained deliberately

`bundles/vep_bundle.db` as committed at `origin/main` reports
`hgvs_protein = p.Arg27His` for this rsID — the opposite polarity to every source
above. It is **not** treated as evidence here, because it is demonstrably not
recording reference/alternate at all: all 102,254 of its single-base rows have
`ref` alphabetically before `alt`, with zero exceptions, where a genuine
reference-first assignment would be near 50/50. Its `hgvs_protein` is
correspondingly inverted for the whole T/C class — rs1051266, PON1 rs662
(`p.Arg192Gln` for `p.Gln192Arg`) and KCNJ11 rs5219 (`p.Glu23Lys` for
`p.Lys23Glu`) are all reversed relative to `vep_seed.csv`.

The exact queries and their results are in `queries.json` under
`in-repo-bundle-crosscheck`, including the full join accounting: of the 679 seed
rows, 44 match the bundle on `(rsid, pos)` — 12 in the same orientation, 6
reversed, and 26 that name a different allele pair entirely and so cannot be
scored for orientation at all. Those 26 are a further discrepancy class between
the two artifacts, not evidence for the ordering finding, which rests on the
102,254/102,254 count above. The retained record is in `raw/`. The artifact
itself is written up as a separate issue; it is a live trap for anyone who reads
it as authoritative, which is exactly how #2023 described it.

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

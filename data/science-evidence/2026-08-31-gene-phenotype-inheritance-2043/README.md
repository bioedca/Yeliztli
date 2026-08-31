# The `inheritance` column of `gene_phenotype_seed.csv` (#2043)

Two claims, both evidenced with primary literature. One drives a data correction; the other
confirms an existing value without changing it.

## Why this column is load-bearing

`inheritance` is not decoration. `_lookup_gene_phenotype` (`backend/annotation/engine.py`) prefers
the first association carrying HPO terms **or** an inheritance pattern when choosing a gene's
primary disease, and ships the value to the frontend as `inheritance_pattern`. A blank on a
Mendelian row therefore does two things: it demotes that row in primary selection, and it renders a
monogenic disease with no inheritance mode.

## C1 — IL10-related early-onset IBD is autosomal recessive

IL10/IL10R deficiency causes very-early-onset IBD through **homozygous or compound-heterozygous**
loss-of-function variants in `IL10`, `IL10RA` or `IL10RB`.

| Source | Kind | Cohort |
| --- | --- | --- |
| `PMID:19890111` / `DOI:10.1056/NEJMoa0907206` — Glocker EO et al., *N Engl J Med* 2009 | Primary | Two unrelated European consanguineous families + 6 further early-onset colitis patients |
| `PMID:28267044` / `DOI:10.1097/MIB.0000000000001058` — Huang Z et al., *Inflamm Bowel Dis* 2017 | Primary | 42-patient Chinese VEO-IBD multicentre survey |

Disjoint author lists, 8 years apart, different continents and cohorts. Neither derives from the
other's data.

**This corrects the issue's own premise.** #2043 describes the 15 blank-inheritance rows as "all
complex/polygenic susceptibility traits" and then enumerates **14**, omitting IL10. Its row names a
specific monogenic disease (`MONDO:0016542`, "IL10-related early-onset inflammatory bowel disease").
A guard written to the stated premise — *blank means polygenic* — would have frozen a blank onto a
Mendelian recessive disease and called it correct.

## C2 — VKORC1 coumarin resistance is autosomal dominant (confirmed, not changed)

Heterozygous *VKORC1* missense variants suffice for warfarin/coumarin resistance. This is distinct
from **VKCFD2**, a recessive phenotype of the *same gene* caused by homozygous loss of function.

| Source | Kind |
| --- | --- |
| `PMID:14765194` / `DOI:10.1038/nature02214` — Rost S et al., *Nature* 2004 | Primary — original mapping in warfarin-resistant kindreds and VKCFD2 families |
| `PMID:26513304` — Lewis BC et al., *Pharmacogenet Genomics* 2016 | Primary — independent clinical case with molecular-mechanism characterisation |

The two-phenotype split is exactly why this row needed confirming rather than pattern-matching off
the gene symbol: one gene, dominant for one phenotype and recessive for the other, so only the
row's `disease_name` settles it. The row names resistance, so `Autosomal dominant` is right and is
left unchanged — which is what #2043 asked for.

## Retraction checking

Checked through **PubMed eLink linked relations**, not publication type. An original record keeps
its own `pubtype` while corrections, errata and retractions are carried as *linked relations*, so a
`pubtype` scan is not a retraction check. All four `elink` responses are retained; none carries a
retraction, erratum or correction linkname.

## What is deliberately **not** claimed

No inheritance value other than IL10's is changed. #2043's item 3 — spot-checking the remaining 13
Mendelian rows against Monarch/OLS4 and correcting via `gene_inheritance_overrides.json` — is **not**
done here. It needs a per-gene authoritative check, which is its own evidence exercise rather than a
rider on this one, and is filed separately.

## Discovery-tool ladder

- **Consensus** — invoked 2026-08-31; 20 papers, retained as a **derived** record. Surfaced C1's primary sources.
- **Scite** — **unavailable**: monthly MCP quota exhausted 2026-08-27, service-reported reset 2026-09-03, not yet passed.
- **Fallback** — PubMed, used for resolution and verification of every record.

Neither surface exposes a version on these calls; both recorded as **not exposed** rather than
guessed.

## Retained evidence

`raw/` holds **five unedited verbatim NCBI responses** (one eSummary, four eLink) and **one derived
record** of the Consensus call, labelled as derived in the file itself. `source-manifest.json`
records each exact request, source, SHA-256, byte length, per-source licence and the
retraction-check method. No payload required sanitization: all are public bibliographic records.

# The `inheritance` column of `gene_phenotype_seed.csv` (#2043)

Two claims, and neither changes the seed: C1 withholds a value for want of evidence, C2 confirms an existing value. This change is tests and evidence only.

## Why this column is load-bearing

`inheritance` reaches the user: `_lookup_gene_phenotype` (`backend/annotation/engine.py`) ships it
to the frontend as `inheritance_pattern`, so a missing pattern renders a monogenic disease with no
inheritance mode.

> **Correction.** An earlier revision of this packet also claimed the blank *demoted the row in
> primary selection*. That was wrong. Selection keys on `annot.hpo_terms or annot.inheritance`, and
> the IL10 row already carries two HPO terms, so it was never demoted. Only the emitted
> `inheritance_pattern` changes. The overclaim is recorded here rather than quietly deleted.

## C1 — WITHHELD: the IL10 row keeps no inheritance value

**Outcome: no value is set.** The row stays blank and is listed in
`_UNVERIFIED_BLANK_INHERITANCE` as **unverified** — and explicitly **not** as polygenic, which was
#2043's mis-classification and the finding that started this work. No inheritance mode of any kind
is asserted for the row until the two-source gate is met.

**Why withheld.** The row is gene-keyed. `_lookup_gene_phenotype` returns its value as the
`inheritance_pattern` rendered beside an **IL10 variant**, so it reads as a per-gene claim, not as
ontology metadata about a disease entity. MONDO's synonyms for `MONDO:0016542` do include
*"autosomal recessive early-onset inflammatory bowel disease"* **(accessed 2026-08-31)**, ontology
version mondo 2026-08-04 from the retained OLS4 record — but that is **one** source, and the contract requires two agreeing sources that do
not share an upstream assertion before a user-facing claim of this kind is encoded.

**What the search found.** Three receptor cohorts, all characterising only the `IL10RA`/`IL10RB`
**receptor** forms (described as three cohorts, not as independent ones: `PMID:19890111` and
`PMID:21519361` share an author in the retained eSummaries, and no cohort-lineage assessment is made
here because nothing rests on their independence):

| Source | Cohort | Genes reported |
| --- | --- | --- |
| `PMID:19890111` / `DOI:10.1056/NEJMoa0907206` — Glocker EO et al., *N Engl J Med* 2009 **(accessed 2026-08-31)** | European consanguineous families + 6 further patients | `IL10RA`, `IL10RB` |
| `PMID:28267044` / `DOI:10.1097/MIB.0000000000001058` — Huang Z et al., *Inflamm Bowel Dis* 2017 **(accessed 2026-08-31)** | 42-patient Chinese multicentre survey | `IL10RA`, `IL10RB` |
| `PMID:21519361` / `DOI:10.1038/ajg.2011.112` — Begue B et al., *Am J Gastroenterol* 2011 **(accessed 2026-08-31)**; payloads retained 2026-09-05 | French cohort, 75 children | `IL10RA` p.R262C, `IL10RB` p.E141X |

Searches for an IL10 **ligand** cohort, each retained: Consensus (run 2026-08-31, re-run 2026-09-06
with all 20 result identities kept — an earlier revision retained only the count, which review
caught); Scite on 2026-09-05/06 (a ligand-only query excluding the receptor genes, paged to
exhaustion — 17 records returned of a reported 22, every DOI and title retained; read at title level
only, none names a ligand cohort, and two congress-proceedings volumes cannot be classified from a
title, so this search surfaced none *by title* rather than excluding one); and a PubMed ESearch on 2026-09-06 (Title/Abstract term excluding `IL10RA`/`IL10RB`,
verbatim ESearch and eSummary retained; one record). Two of the surfaced records report IL10-mutant
patients, and they were assessed as a possible independent pair:

| Candidate | What it reports | Why it does not close C1 |
| --- | --- | --- |
| `PMID:22549091` / `DOI:10.1053/j.gastro.2012.04.045` — Kotlarz D et al., *Gastroenterology* 2012 **(accessed 2026-09-06)** | 66 early-onset IBD patients; 3 with `IL10` mutations, homozygous loss of function (abstract of the live record) | Its 35-author list shares **14** authors with the cited receptor cohort `PMID:19890111` — the same group. One source. |
| `PMID:24216686` / `DOI:10.1097/01.MIB.0000435439.22484.d3` — Pigneur B et al., *Inflamm Bowel Dis* 2013 **(accessed 2026-09-06)** | GENIUS network survey of 10 patients with confirmed `IL10`, `IL10RA` or `IL10RB` mutations; the abstract reports IL10-mutant patients without counting them | A different research group from Kotlarz 2012, but the two papers share **2** contributing-clinician authors (Buderus S, Guariso G), and it shares **7** authors with the cited `PMID:21519361`. A multi-centre survey with contributing clinicians in common with the other ligand cohort cannot be shown, from the retained abstract-level records, not to contain the same IL10 patients. The contract requires sources that **do not share a cohort**; this pair is not proven to be two. |

`PMID:30212871` (Zheng C et al., 2019) compares IL10-mutant carriers across its own 61 patients plus 78
reviewed cases without attributing any to its own cohort, and shares 5 authors with the cited
`PMID:28267044`; it is not assessable as a ligand cohort from the retained record and is not counted.
Author lists were read from the retained eSummaries. None of these three is cited in support of a
claim, but the cohort composition and author-overlap reading rest on them, so all three carry the
same typed notice checks as the cited sources (PubMed EFetch `CommentsCorrections` and Scite
metadata, 2026-09-06): **no notice of any kind on any of them**. That is the whole supported
statement about the literature.

> **Correction.** The revision before the previous one stated that none of the returned records
> was an IL10 ligand cohort and called the ESearch hit a review. Its retained publication types are
> `Evaluation Study` / `Journal Article`, and its abstract reports three IL-10-mutant patients. The
> statement was corrected within the hour, before any claim rested on it; the Consensus re-run then
> surfaced the second candidate assessed above.

**What would close it.** A second IL10 (ligand) loss-of-function cohort whose independence from
`PMID:22549091` is documented across all three dimensions the contract names — no shared patients,
no shared dataset, and a genotype–phenotype observation that does not derive from the other paper's
assertion — author disjointness being necessary but not sufficient; or patient-level evidence that the
IL10-mutant patients in `PMID:24216686` are not those in `PMID:22549091` plus the same dataset and
upstream assessment; or a maintainer decision that the entity-level MONDO assertion is sufficient for
a gene-keyed row. Filed as a scientific-validity issue rather than left as a silent blank.

**History, kept rather than deleted.** An earlier revision set the value on receptor-only evidence
and wrote *"loss-of-function variants in `IL10`, `IL10RA` or `IL10RB`"*. A later revision restated it
at the entity level. Review established that the entity framing does not change what the software
renders beside an IL10 variant, so the value is now withheld outright. Withholding is the contract's
prescribed answer to insufficient evidence, and the guard added here has a place to record it.

## C2 — VKORC1 coumarin resistance is autosomal dominant (confirmed, not changed)

Heterozygous *VKORC1* missense variants suffice for coumarin/warfarin resistance — the one claim
both sources make. *VKORC1* carries more than one phenotype in the literature (Rost 2004 also maps
VKCFD2 families), which is why the row could not be pattern-matched off the gene symbol; this packet
asserts nothing about those other phenotypes' mode or mechanism, and only the row's `disease_name`
settles that the resistance row is the one at issue.

> **Correction.** An earlier revision stated VKCFD2's recessive mode and mechanism as part of C2 on
> a single source. That half is withdrawn from the claim; it was never load-bearing for the row.

| Source | Kind |
| --- | --- |
| `PMID:14765194` / `DOI:10.1038/nature02214` — Rost S et al., *Nature* 2004 **(accessed 2026-08-31)** | Primary — original mapping in warfarin-resistant kindreds and VKCFD2 families |
| `PMID:26513304` / `DOI:10.1097/FPC.0000000000000184` — Lewis BC et al., *Pharmacogenet Genomics* 2016 **(accessed 2026-08-31)** | Primary — independent clinical case with molecular-mechanism characterisation |

**Independence of the two C2 sources**, assessed across the contract's three dimensions from the
retained eSummaries and the live abstracts **(accessed 2026-09-06)**. *Cohort:* Rost 2004 reports the
original warfarin-resistant kindreds and VKCFD2 families; Lewis 2016 reports a single patient carrying
Val66Met, studied twelve years later at a different institution. Nothing in the retained records
states or implies that this patient belongs to Rost's kindreds; that they are different individuals
is not provable from abstract-level records and is recorded as such. *Dataset:* separate — Rost's
linkage and sequencing versus Lewis's own patient genotype, clinical response and homology model.
*Upstream assertion:* the mapped claim is that a heterozygous missense variant suffices for coumarin
resistance; Lewis's support for it is its own genotype–phenotype observation in that patient, and it
relies on Rost only for the identification of *VKORC1* as the gene, which is not the claim mapped
here. Disjoint authors and the twelve-year gap are supporting facts, not the test. Were this
assessment judged insufficient, the value would stay exactly as it is — it is pre-existing and
unchanged by this pull request — and only the word "confirmed" would be withdrawn.

One gene, more than one phenotype in the literature, so only the row's `disease_name` settles which
association this row is. It names resistance, so `Autosomal dominant` is right and stays — the
confirmation #2043 asked for.

> An earlier revision of this manifest recorded the Lewis DOI as `10.1097/FPC.0000000000000197`.
> That was wrong: the retained eSummary says `...184`. The identifier is now copied from the payload
> rather than written from memory.

## What the guard does and does not assert

The seed's remaining blank rows are listed in `_UNVERIFIED_BLANK_INHERITANCE`, which records **only
an observable fact**: the row carries no inheritance value and none has been verified. It makes no
biological claim. Asserting "polygenic" for 14 rows with no evidence would repeat the exact error
this work exists to correct, and would turn an unevidenced classification into a test-enforced gate.
Their audit is filed separately.

The list is keyed by `(gene_symbol, disease_id)`, not by gene: a gene may legitimately carry several
rows (`HBB` does), and a gene-wide exemption would silently excuse a new Mendelian row whose
inheritance was simply omitted.

## Retraction checking

Checked through **PubMed EFetch typed `CommentsCorrections` relations**, not publication type and
not eLink. An original record keeps its own `pubtype` while corrections, errata and retractions are
carried as *separate linked records*, so a `pubtype` scan is not a retraction check. The XML for
all five cited PMIDs is retained (abstracts removed, nothing else touched) with a derived reduction
of its notice fields: **no record carries a link of any notice `RefType`** (`ErratumIn`,
`RetractionIn`, `PartialRetractionIn`, `ExpressionOfConcernIn`, `CorrectedandRepublishedIn`,
`RepublishedIn`, `UpdateIn`, `ReprintIn`). Three records carry `CommentIn` links — comments, not
corrections. Scite's metadata for the same five DOIs **(accessed 2026-09-05)** reports no editorial
notice either — a second notice source that does not share PubMed's index. For the ontology term,
obsolescence is the analogous check — `MONDO:0016542` is not obsolete.

> **Correction.** An earlier revision of this packet checked only the eLink `pubmed_pubmed*` link
> sets and read their silence as clearance. Those are generic related-article neighbourhoods and do
> not expose the typed relation, so that check could not have found a retraction. The eLink payloads
> are retained for what they are; the typed check above replaces them as the authority. The same
> revision cited the Begue paper without retaining any payload for it while claiming every record
> had been re-resolved; its eSummary, eLink and EFetch records were added on 2026-09-05.

## Discovery-tool ladder

- **Consensus** — invoked 2026-08-31 (count and outcome only were kept, which review caught) and re-run with the same query on 2026-09-06; all 20 result identities are retained as a **derived** record and read above.
- **Scite** — invoked **2026-09-05/06**, after the service-reported quota reset of 2026-09-03 (an earlier revision recorded the August failure as current). Retained as **derived** records: metadata for the cited and candidate DOIs (no editorial notice on any) and a term search for an IL10 **ligand** loss-of-function cohort excluding the receptor genes, paged to exhaustion — 17 records returned of a reported 22; read at title level only, none names a ligand cohort, so the search surfaced none by title. C1 stays withheld on the independent-pair ground.
- **PubMed ESearch** — **2026-09-06**, the negative search itself retained verbatim (1 record, `PMID:22549091`, an IL10 ligand cohort from the same author group as the cited receptor cohort; recorded above as insufficient on its own).
- **Fallback** — PubMed for the literature, EBI OLS4 for the ontology record.

Versions: OLS4 exposes **mondo 2026-08-04**, retained as a payload. Every retained NCBI E-utilities
JSON response (ESearch, eSummary, eLink) exposes response-schema version **0.3** in `header.version`,
recorded as such; no database release or build is exposed on any E-utilities endpoint, and the
Consensus and Scite MCP surfaces expose no version at all — those are recorded as **not exposed**
rather than guessed.

## Retained evidence

`raw/` holds **thirteen unedited verbatim API responses** (four NCBI eSummary, two NCBI ESearch, five
NCBI eLink, two EBI OLS4), **three sanitized responses** (the NCBI EFetch XML for the five cited
PMIDs, for `PMID:22549091` and for the two candidate PMIDs, abstracts removed and nothing else
touched, labelled in their filenames) and **six derived records**, each labelled as derived in the
file itself: the Consensus call, the two Scite calls, and the three reductions of the EFetch XML
files to their `CommentsCorrections` and publication-type fields. `source-manifest.json` records each exact
request, source, SHA-256, byte length, access date, per-source licence and the retraction-check
method. No payload required further sanitization: all are public bibliographic or ontology records.

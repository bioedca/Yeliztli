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
`_UNVERIFIED_BLANK_INHERITANCE` as *Mendelian but unverified* — explicitly **not** as polygenic,
which was #2043's mis-classification and the finding that started this work.

**Why withheld.** The row is gene-keyed. `_lookup_gene_phenotype` returns its value as the
`inheritance_pattern` rendered beside an **IL10 variant**, so it reads as a per-gene claim, not as
ontology metadata about a disease entity. MONDO's synonyms for `MONDO:0016542` do include
*"autosomal recessive early-onset inflammatory bowel disease"* **(accessed 2026-08-31, mondo
2026-08-04)** — but that is **one** source, and the contract requires two agreeing sources that do
not share an upstream assertion before a user-facing claim of this kind is encoded.

**What the search found.** Three independent cohorts, all characterising only the `IL10RA`/`IL10RB`
**receptor** forms:

| Source | Cohort | Genes reported |
| --- | --- | --- |
| `PMID:19890111` / `DOI:10.1056/NEJMoa0907206` — Glocker EO et al., *N Engl J Med* 2009 **(accessed 2026-08-31)** | European consanguineous families + 6 further patients | `IL10RA`, `IL10RB` |
| `PMID:28267044` / `DOI:10.1097/MIB.0000000000001058` — Huang Z et al., *Inflamm Bowel Dis* 2017 **(accessed 2026-08-31)** | 42-patient Chinese multicentre survey | `IL10RA`, `IL10RB` |
| `PMID:21519361` / `DOI:10.1038/ajg.2011.112` — Begue B et al., *Am J Gastroenterol* 2011 **(accessed 2026-08-31; payloads retained 2026-09-05)** | French cohort, 75 children | `IL10RA` p.R262C, `IL10RB` p.E141X |

Targeted searches for IL10 **ligand** cohorts — Consensus and PubMed on 2026-08-31, Scite on
2026-09-05 (a ligand-only query excluding the receptor genes, 22 results, none a ligand cohort) —
found no qualifying pair of independent primary sources. That is the supported statement; nothing
is asserted here about who authored the ligand literature.

**What would close it.** Two agreeing, independently-authored primary sources reporting IL10
(ligand) loss-of-function variants and their transmission — or a maintainer decision that the
entity-level MONDO assertion is sufficient for a gene-keyed row. Filed as a scientific-validity
issue rather than left as a silent blank.

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

One gene, dominant for one phenotype and recessive for the other, so only the row's `disease_name`
settles which applies. It names resistance, so `Autosomal dominant` is right and stays — the
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

- **Consensus** — invoked 2026-08-31; 20 papers, retained as a **derived** record.
- **Scite** — invoked **2026-09-05**, after the service-reported quota reset of 2026-09-03 (an earlier revision recorded the August failure as current). Two calls, retained as **derived** records: metadata for the five cited DOIs (no editorial notice on any) and a term search for an IL10 **ligand** loss-of-function cohort excluding the receptor genes — 22 results, none of the ten returned is such a cohort, so C1 stays withheld.
- **Fallback** — PubMed for the literature, EBI OLS4 for the ontology record.

Versions: OLS4 exposes **mondo 2026-08-04**, retained as a payload. Neither the Consensus MCP
surface, the Scite MCP surface nor NCBI E-utilities exposes a version on these calls; all recorded
as **not exposed** rather than guessed.

## Retained evidence

`raw/` holds **nine unedited verbatim API responses** (two NCBI eSummary, five NCBI eLink, two EBI
OLS4), **one sanitized response** (the NCBI EFetch XML for all five PMIDs, abstracts removed and
nothing else touched, labelled in its filename) and **four derived records**, each labelled as
derived in the file itself: the Consensus call, the two Scite calls, and the reduction of the
EFetch XML to its `CommentsCorrections` and publication-type fields. `source-manifest.json` records each exact
request, source, SHA-256, byte length, access date, per-source licence and the retraction-check
method. No payload required further sanitization: all are public bibliographic or ontology records.

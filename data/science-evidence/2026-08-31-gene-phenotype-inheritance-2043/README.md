# The `inheritance` column of `gene_phenotype_seed.csv` (#2043)

Two claims. One drives a data correction; the other confirms an existing value without changing it.

## Why this column is load-bearing

`inheritance` reaches the user: `_lookup_gene_phenotype` (`backend/annotation/engine.py`) ships it
to the frontend as `inheritance_pattern`, so a missing pattern renders a monogenic disease with no
inheritance mode.

> **Correction.** An earlier revision of this packet also claimed the blank *demoted the row in
> primary selection*. That was wrong. Selection keys on `annot.hpo_terms or annot.inheritance`, and
> the IL10 row already carries two HPO terms, so it was never demoted. Only the emitted
> `inheritance_pattern` changes. The overclaim is recorded here rather than quietly deleted.

## C1 — the disease the IL10 row cites is autosomal recessive

The claim is made about **the entity the row cites**, `MONDO:0016542`, not about a gene list.

**Primary assertion — the ontology record itself.** MONDO's term for `MONDO:0016542` carries the
synonyms *"autosomal recessive early-onset IBD"* and *"autosomal recessive early-onset inflammatory
bowel disease"*. OLS4/MONDO is the authoritative source of record this repository already uses for
these rows — it is what `tests/fixtures/mondo_label_snapshot.json` is built from — and the assertion
is about the exact `disease_id` in the row.

- `MONDO:0016542` — "IL10-related early-onset inflammatory bowel disease", EBI OLS4, MONDO 2026-08-04 **(accessed 2026-08-31)**

**Supporting cohorts**, showing recessive transmission (homozygous and compound-heterozygous loss of
function) in this disease entity:

| Source | Kind | Cohort |
| --- | --- | --- |
| `PMID:19890111` / `DOI:10.1056/NEJMoa0907206` — Glocker EO et al., *N Engl J Med* 2009 **(accessed 2026-08-31)** | Primary | Two unrelated European consanguineous families + 6 further patients |
| `PMID:28267044` / `DOI:10.1097/MIB.0000000000001058` — Huang Z et al., *Inflamm Bowel Dis* 2017 **(accessed 2026-08-31)** | Primary | 42-patient Chinese VEO-IBD multicentre survey |

Disjoint authors, 8 years apart, different continents and cohorts.

### Stated limit — read this before reusing the claim

Both retained cohorts characterise the **receptor** forms (`IL10RA`/`IL10RB`); neither reports
`IL10` **ligand** mutations. A search for an independent pair of primaries specific to the ligand
did not find one: that literature is dominated by a single group, so two sources there would share a
lineage rather than agree independently.

The claim is therefore made about the MONDO disease entity — which is what the row cites — and
**not** as a per-gene assertion about IL10 the ligand. An earlier revision of this packet wrote
*"loss-of-function variants in IL10, IL10RA or IL10RB"*; the `IL10` half of that was **unsupported
by the retained sources** and has been withdrawn rather than papered over.

## C2 — VKORC1 coumarin resistance is autosomal dominant (confirmed, not changed)

Heterozygous *VKORC1* missense variants suffice for coumarin/warfarin resistance. This is distinct
from **VKCFD2**, a recessive phenotype of the *same gene* from homozygous loss of function.

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

Checked through **PubMed eLink linked relations**, not publication type. An original record keeps
its own `pubtype` while corrections, errata and retractions are carried as *linked relations*, so a
`pubtype` scan is not a retraction check. All four `elink` responses are retained; none carries a
retraction, erratum or correction linkname. For the ontology term, obsolescence is the analogous
check — `MONDO:0016542` is not obsolete.

## Discovery-tool ladder

- **Consensus** — invoked 2026-08-31; 20 papers, retained as a **derived** record.
- **Scite** — **unavailable**: monthly MCP quota exhausted 2026-08-27, service-reported reset 2026-09-03, not yet passed.
- **Fallback** — PubMed for the literature, EBI OLS4 for the ontology record.

Versions: OLS4 exposes **mondo 2026-08-04**, retained as a payload. Neither the Consensus MCP
surface nor NCBI E-utilities exposes a version on these calls; both recorded as **not exposed**
rather than guessed.

## Retained evidence

`raw/` holds **seven unedited verbatim API responses** (one NCBI eSummary, four NCBI eLink, two EBI
OLS4) and **one derived record** of the Consensus call, labelled as derived in the file itself.
`source-manifest.json` records each exact request, source, SHA-256, byte length, per-source licence
and the retraction-check method. No payload required sanitization: all are public bibliographic or
ontology records.

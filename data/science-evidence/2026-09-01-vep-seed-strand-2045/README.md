# `vep_seed.csv` strand column and the CYP2C19\*4 annotation (#2045)

## Stakes, stated plainly

**Fixture provenance, not a shipped user-facing error.** Production builds the VEP table from the
real bundle. This CSV seeds `mini_vep_bundle.db`, a **test oracle** whose rows are copied into
`annotated_variants` unchanged. The defect is that the oracle disagreed with the reference assembly,
so tests could not detect a real regression in the very fields they were meant to pin.

## C1 — 19 of 57 annotated genes carried the wrong strand

| source | coverage | result |
| --- | --- | --- |
| Ensembl GRCh37 REST `lookup/symbol` **(accessed 2026-09-01)** | all 57 genes | 57 resolved, **zero fetch failures**; 19 disagreed with the fixture |
| NCBI Gene `esummary` `genomicinfo` **(accessed 2026-09-01)** | the 19 corrected genes | **zero disagreements** with Ensembl |
| Ensembl GRCh37 REST `lookup/symbol`, **verbatim re-fetch (accessed 2026-09-05)** | all 57 genes | every `strand` and `id` equals the 2026-09-01 reduction and the committed snapshot |
| NCBI Gene `esearch` + `esummary`, **verbatim re-fetch (accessed 2026-09-05)** | the 19 corrected genes | every uid, coordinate pair and orientation equals the 2026-09-01 reduction; orientation agrees with Ensembl for all 19 |

The two 2026-09-01 rows are **reductions** (see *Retained evidence*): the strand and gene id selected from
each Ensembl response, and the live record's uid and `genomicinfo` coordinates from each NCBI response with
an orientation *computed* from their order, because `esummary` exposes no strand field. The NCBI
coordinates sit on the accession the payload names, not on GRCh37, and were never compared to the fixture;
only orientation, which is assembly-invariant, was.

**On independence:** these are two annotation projects run by different organisations (EMBL-EBI and
NCBI/RefSeq) with separate gene-model pipelines. They are *not fully* independent — both are
anchored to the same reference assembly — but neither imports the other's strand call, unlike the
Ensembl-imports-dbSNP case that makes those two count as one.

**Four of the 19 need no external source at all.** `CYP2C19`, `MTHFR`, `PON1` and `CYP2R1` each
carried **both** strands on different rows of the same gene. A gene has one strand; the fixture
contradicted itself.

## C2 — rs28399504 (CYP2C19\*4) is a start loss, not a missense

Ensembl GRCh37 VEP on `ENST00000371321` — **the transcript the row itself cites** — reports:

```
consequence_terms: ['start_lost']      impact: HIGH      strand: 1
hgvsc: ENST00000371321.3:c.1A>G        (fixture already correct)
hgvsp: ENSP00000360372.3:p.Met1?       (fixture said p.Met1Val)
seq_region_name: 10  start: 96522463   assembly_name: GRCh37   (fixture said 10:94781859)
```

`c.1A>G` changes the first base of the initiator codon, and VEP calls that `start_lost` at HIGH
impact. The fixture's `missense_variant` downgraded a HIGH-impact consequence to MODERATE and asserted
a specific residue at a codon VEP reports as lost. That consequence is the whole of what this packet
claims about the variant's effect; it says nothing about the allele's function. The HGVS strings above were read
from the retained payload, not copied from the issue text.

> **Correction.** An earlier revision of this section said the start loss is "why `*4` is a
> no-function allele". Neither retained payload nor either cited paper makes that allele-function
> claim, so it was withdrawn on 2026-09-05 after review. The consequence claim is unchanged and rests
> on the VEP payload alone.

**Coordinate (added 2026-09-05 after review).** The same retained payload places rs28399504 at
GRCh37 `10:96522463`. The fixture row carried `10:94781859`, which is not where GRCh37 places the
variant — it falls inside the CYP2C19 span the retained NCBI `esummary` reports on `NC_000010.11`, a
different assembly release — and `mini_vep_bundle.db` repeated it. Because the annotation engine only
derives ref/alt and zygosity from a VEP row that sits at the sample's own coordinate, a GRCh37 sample
would have matched the rsID but lost carriage. Corrected in `vep_seed.csv` and the regenerated bundle,
sourced from the payload's `seq_region_name` / `start`. No other row's coordinate was audited by this
packet; filed separately.

**One source, deliberately.** This is not a contested biological claim; it is the output of the
annotation tool the fixture exists to mirror. VEP's call on the cited transcript *is* the oracle. A
second opinion would be answering a different question.

## The issue's counts, corrected

| | issue claimed | verified |
| --- | --- | --- |
| genes with wrong strand | 25 | **19** |
| internally contradictory | 2 | **4** (also `PON1`, `CYP2R1`) |
| file shape | "57 genes" | 679 rows (**678** unique rsIDs — `rs121913529` appears twice, `G>A` and `G>C`), **100** with a `gene_symbol` (99 unique rsIDs) |

`COL1A1`, `F5`, `FADS1`, `GC` and `KCNJ11` are listed as wrong but are already correct in the
fixture. `HEXA` is listed but is **not in this file at all** (it is in `gene_phenotype_seed.csv`).
The issue overstates the strand breadth and understates the contradictions.

## What is deliberately **not** corrected

The retained audit was requested **once per unique rsID (678)**. It holds **677** reduced
responses and **one recorded failure**: `rs5778923`, `HTTP 400`, an unannotated `intron_variant` row
on chrX. A 2026-09-05 retry returned the same status with the body `No variant found with ID
'rs5778923'` (retained verbatim), so the audit does **not** cover that row and it is listed as an
exclusion, not a response. Of the 100 annotated rows (99 unique rsIDs; `rs5778923` is not among
them):

- **52** agree with VEP on the fixture's own transcript
- **15** disagree — only `rs28399504` is corrected here
- **33** rows (32 rsIDs, `rs121913529` counted on both of its rows) cite a transcript Ensembl VEP does
  not report for that variant

> **Correction.** An earlier revision said the audit "covers every rsid" and reduces "679 VEP
> responses". It reduces 677; one request failed and is recorded, and one rsID appears on two rows.
> Corrected 2026-09-05 after review; the 52 / 15 / 33 breakdown was already computed over rows and
> is unchanged.

The other 14 mismatches are predominantly `5_prime_UTR_variant` vs `upstream_gene_variant`, which
turn on **transcript selection** rather than being outright errors, and the 33 unresolvable
transcripts need per-row adjudication (transcript version, assembly, MANE choice). Adjudicating 47
rows off a single REST call each would be exactly the sort of bulk edit that produced this issue.
Filed separately.

## Citations

Both claims are read off annotation databases queried directly, so the durable identifiers this
packet carries name the **resource** and the **tool** rather than a paper about any locus. Neither
paper asserts a gene's strand or rs28399504's consequence; those rest on the retained payloads.

| Source | Names | Supports |
| --- | --- | --- |
| `PMID:39656687` / `DOI:10.1093/nar/gkae1071` — Dyer SC et al., *Ensembl 2025*, Nucleic Acids Res 2025;53(D1):D948–D957 **(accessed 2026-09-05)** | the annotation project whose GRCh37 gene models `lookup/symbol` serves | C1 |
| `PMID:27268795` / `DOI:10.1186/s13059-016-0974-4` — McLaren W et al., *The Ensembl Variant Effect Predictor*, Genome Biol 2016;17:122 **(accessed 2026-09-05)** | the tool whose `start_lost` / `p.Met1?` call is C2's oracle | C2 |

NCBI Gene, C1's second source, is identified by its retained `esummary` payload and stable GeneIDs
rather than by a paper. Retraction check for both PMIDs: PubMed EFetch typed `CommentsCorrections`
relations — **neither record carries a link of any `RefType`**; publication types are retained in
the derived record. Scite's metadata for both DOIs **(accessed 2026-09-05)** reports no editorial
notice either — a second notice source that does not share PubMed's index.

> **Correction.** An earlier revision of this packet carried no `PMID:` / `DOI:` identifier at all,
> which the repository contract requires of every durable evidence artifact. Added 2026-09-05 after
> review, without changing either claim.

## Discovery-tool ladder

Consensus and Scite are literature-discovery tools and are not the right instrument for a
genome-annotation fact — the authoritative sources are the annotation projects themselves, queried
directly. Scite was unavailable on 2026-09-01 (monthly MCP quota exhausted 2026-08-27) and was
invoked on **2026-09-05** after its reset, for the two cited papers' metadata and notice status:
**no editorial notice on either**, retained as a derived record.

Versions: Ensembl GRCh37 REST exposes no version header on these endpoints — the assembly is the pin
that matters and is in the hostname. NCBI E-utilities exposes no build field. The Scite MCP surface
exposes no index version. All recorded as **not exposed** rather than guessed.

## Retained evidence

`raw/` holds **seven unedited verbatim API responses**, **one sanitized response** and **five derived
records**.

Verbatim: the two Ensembl VEP records for rs28399504 and the PubMed eSummary for the two cited papers;
three per-symbol captures made on 2026-09-05 — Ensembl `lookup/symbol` for all 57 genes, and NCBI Gene
`esearch` and `esummary` for the 19 corrected genes — in which the `_artifact_note` and the
`responses` wrapper keyed by queried symbol are the only additions; and the 48-byte `HTTP 400` error
body Ensembl GRCh37 VEP returned for `rs5778923` on 2026-09-05, stored exactly as received.

Sanitized: the PubMed EFetch XML for the two cited papers, abstracts removed and nothing else touched,
labelled in its filename.

Derived, each labelled DERIVED in the manifest with the verbatim payload it derives from and the fields
it selects:

- `ensembl-grch37-lookup-symbol-57-genes.json` selects each gene's `strand` (1 → `+`, −1 → `-`) and
  `id` from the `lookup/symbol` response and joins the strand values the pre-correction fixture carried.
- `ncbi-gene-esummary-19-corrected-genes.json` selects, per gene, the **live** `esummary` record's
  `uid`, `genomicinfo[0].chrstart` and `genomicinfo[0].chrstop`, and **computes** `orientation` from
  their order (`-` when start > stop). `CYP2C19`, `CYP2R1` and `HLA-DRB1` also return discontinued ids
  whose `currentid` points at the retained one and which carry no `genomicinfo`.
- the consequence audit reduces 677 VEP responses (one per unique rsID; 678 requested, the failed
  `rs5778923` recorded under `failures`) to the fixture consequence, the transcript-matched terms and
  `most_severe_consequence`, because retaining 677 full payloads would be megabytes of unread JSON;
- the PubMed reduction lists each cited record's `CommentsCorrections` and publication types;
- the Scite record holds the two cited DOIs' metadata and notice status.

`source-manifest.json` carries each request, source, SHA-256, byte length, access date and licence. No
payload required further sanitization.

> **Correction.** An earlier revision counted the two reductions above among the unedited verbatim
> responses. Relabelled DERIVED on 2026-09-05 after review. The 2026-09-01 responses they were read
> from had not been retained, so the verbatim payloads are a **re-fetch** made that day, not the
> original capture; every reduced value re-derived from the re-fetch equals the 2026-09-01 reduction,
> so neither claim changes.

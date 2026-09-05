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
```

`c.1A>G` destroys the initiator methionine, which is why `*4` is a no-function allele. The fixture's
`missense_variant` downgraded a HIGH-impact consequence to MODERATE and asserted a specific residue
at a codon that is no longer a start. The HGVS strings above were read from the retained payload,
not copied from the issue text.

**One source, deliberately.** This is not a contested biological claim; it is the output of the
annotation tool the fixture exists to mirror. VEP's call on the cited transcript *is* the oracle. A
second opinion would be answering a different question.

## The issue's counts, corrected

| | issue claimed | verified |
| --- | --- | --- |
| genes with wrong strand | 25 | **19** |
| internally contradictory | 2 | **4** (also `PON1`, `CYP2R1`) |
| file shape | "57 genes" | 679 rows, **100** with a `gene_symbol` |

`COL1A1`, `F5`, `FADS1`, `GC` and `KCNJ11` are listed as wrong but are already correct in the
fixture. `HEXA` is listed but is **not in this file at all** (it is in `gene_phenotype_seed.csv`).
The issue overstates the strand breadth and understates the contradictions.

## What is deliberately **not** corrected

The retained audit covers **every** rsid in the file. Of the 100 annotated rows:

- **52** agree with VEP on the fixture's own transcript
- **15** disagree — only `rs28399504` is corrected here
- **33** cite a transcript Ensembl VEP does not report for that variant

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
the derived record.

> **Correction.** An earlier revision of this packet carried no `PMID:` / `DOI:` identifier at all,
> which the repository contract requires of every durable evidence artifact. Added 2026-09-05 after
> review, without changing either claim.

## Discovery-tool ladder

Consensus and Scite are literature-discovery tools and are not the right instrument for a
genome-annotation fact — the authoritative sources are the annotation projects themselves, queried
directly. (Scite was unavailable regardless: monthly MCP quota exhausted 2026-08-27, reset
2026-09-03.)

Versions: Ensembl GRCh37 REST exposes no version header on these endpoints — the assembly is the pin
that matters and is in the hostname. NCBI E-utilities exposes no build field. Both recorded as **not
exposed** rather than guessed.

## Retained evidence

`raw/` holds **five unedited verbatim API responses** (four Ensembl / NCBI Gene records and one
PubMed eSummary), **one sanitized response** (the PubMed EFetch XML for the two cited papers,
abstracts removed and nothing else touched, labelled in its filename) and **two derived records**:
the consequence audit reduces 679 VEP responses per rsid to the fixture consequence, the
transcript-matched terms and `most_severe_consequence`, because retaining 679 full payloads would be
megabytes of unread JSON; the PubMed reduction lists each cited record's `CommentsCorrections` and
publication types. Both are labelled DERIVED in the manifest. `source-manifest.json` carries each
request, source, SHA-256, byte length, access date and licence. No payload required further
sanitization.

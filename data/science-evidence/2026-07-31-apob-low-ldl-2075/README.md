# APOB low-LDL documentation evidence for issue #2075

## Scope

This packet supports the narrow documentation statement that APOB is
direction-specific: APOB variants associated with high LDL cholesterol may
support familial hypercholesterolemia (FH), whereas protein-truncating APOB
variants associated with familial hypobetalipoproteinemia lower LDL cholesterol
and must not be presented as FH evidence.

Repository base: `804d3ea94a1ae409d4751c0dba1719604a493071`

Access date: `2026-07-31`

## Sanitized discovery queries

Consensus:

> Do APOB protein-truncating loss-of-function variants cause familial
> hypobetalipoproteinemia with low LDL cholesterol rather than familial
> hypercholesterolemia, and are they associated with lower coronary heart
> disease risk? human:true year:2018-2026

Result: unavailable because the monthly search quota was exhausted; the service
reported that it resets on 2026-08-01.

Scite:

> DOI 10.1016/j.atherosclerosis.2025.119569 APOB protein-truncating
> hypobetalipoproteinemia low LDL familial hypercholesterolemia

Result: unavailable because the monthly MCP quota was exhausted; the service
reported that it resets on 2026-08-03 UTC.

NCBI Entrez:

- `esummary`, database `pubmed`, ID `30939045`, JSON
- `esummary`, database `pubmed`, ID `36723951`, JSON
- `efetch`, database `pubmed`, IDs `30939045,36723951`, XML; inspect
  `MedlineCitation/CommentsCorrectionsList`

## Primary sources and claim mapping

1. Peloso GM et al. “Rare Protein-Truncating Variants in APOB, Lower
   Low-Density Lipoprotein Cholesterol, and Protection Against Coronary Heart
   Disease.” `PMID:30939045`; `DOI:10.1161/CIRCGEN.118.002376`
   (accessed 2026-07-31).
   - Supports the association of rare APOB protein-truncating variants with
     familial hypobetalipoproteinemia and lower LDL cholesterol.
   - Public metadata:
     `data/science-evidence/2026-07-31-apob-low-ldl-2075/pubmed-30939045-esummary.json`.
2. Dron JS et al. “Association of Rare Protein-Truncating DNA Variants in APOB
   or PCSK9 With Low-density Lipoprotein Cholesterol Level and Risk of Coronary
   Heart Disease.” `PMID:36723951`; `DOI:10.1001/jamacardio.2022.5271`
   (accessed 2026-07-31).
   - Independently supports the association of rare APOB protein-truncating
     variants with lower LDL cholesterol.
   - Public metadata:
     `data/science-evidence/2026-07-31-apob-low-ldl-2075/pubmed-36723951-esummary.json`.

The studies share some investigators, but the mapped evidence is independent at
the participant-data level: Peloso et al. analyzed familial
hypobetalipoproteinemia families and multiple coronary-disease case-control
cohorts, while the mapped component of Dron et al. is the separately enrolled
UK Biobank cohort. Both studies analyze genotype and phenotype data directly
rather than relying on the other paper's conclusion.

## Corrections and retractions

PubMed EFetch records for `PMID:30939045` and `PMID:36723951` were retrieved on
2026-07-31 and inspected at `MedlineCitation/CommentsCorrectionsList`, which is where PubMed
records linked corrections, retractions, expressions of concern, and related notices. The
element was absent from both records. This linked-record check avoids the false-negative risk
of combining an original article's DOI with a notice publication type when the notice has its
own identifier.

The source-structured snapshot preserves both `MedlineCitation` records and every source
element except the publisher-text containers `Abstract`, `OtherAbstract`, `ReferenceList`,
and `CoiStatement`; its sanitizer explicitly retains `CommentsCorrectionsList` whenever
present.
The raw-response SHA-256, sanitized SHA-256, removal counts, request, and snapshot path are
recorded in:
`data/science-evidence/2026-07-31-apob-low-ldl-2075/pubmed-comments-corrections-extract.json`.
The auditable sanitized source payload is:
`data/science-evidence/2026-07-31-apob-low-ldl-2075/pubmed-efetch-sanitized.xml`.
These results record what PubMed indexed and returned on 2026-07-31, not a guarantee that
no later notice exists.

## Payload format and usage

The two ESummary files are raw NCBI E-utilities PubMed JSON responses captured on
2026-07-31 and identify JSON schema version `0.3`. The correction/retraction file is a
sanitized source snapshot plus field extraction from official EFetch XML using the PubMed DTD
dated 2025-01-01. The `Abstract`, `OtherAbstract`, `ReferenceList`, and `CoiStatement`
publisher-text containers are not redistributed. All remaining source XML structure is
retained, so the absence of `CommentsCorrectionsList` is auditable against the immutable
snapshot rather than a later live response. The extract
preserves the exact requests, record identifiers, revision dates, hashes, sanitizer removals,
and linked-notice field result needed to reproduce the check. The live PubMed service does
not expose a frozen database build identifier, so the access date is the packet's retrieval
version boundary. The packet contains citation/link metadata only, not abstracts, article
full text, participant-level data, genotypes, credentials, or restricted material.

NCBI's [Website and Data Usage Policies and Disclaimers](https://www.ncbi.nlm.nih.gov/home/about/policies/)
(accessed 2026-07-31) state that U.S.-government-created site information is public domain and
may be copied with acknowledgment, while contributed or licensed material may remain
protected; NLM does not claim copyright in PubMed abstracts, but publishers or authors may.
The [E-utilities usage requirements](https://www.ncbi.nlm.nih.gov/books/NBK25497/)
(accessed 2026-07-31) require the NCBI disclaimer/copyright notice to remain evident when
E-utilities are used in software. This packet therefore redistributes only the citation/link
metadata returned by the API, preserves NCBI and publisher attribution, excludes abstracts and
full text, and asserts no blanket license over publisher-contributed material. The repository
documentation paraphrases the mapped claims and links to the primary sources.

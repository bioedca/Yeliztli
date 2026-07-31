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
- `esearch`, database `pubmed`, DOI `10.1161/CIRCGEN.118.002376`, correction/retraction
  publication types, JSON
- `esearch`, database `pubmed`, DOI `10.1001/jamacardio.2022.5271`,
  correction/retraction publication types, JSON

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

Reproducible PubMed ESearch requests run on 2026-07-31:

- Source `DOI:10.1161/CIRCGEN.118.002376` was searched for records whose publication type
  is Correction, Published Erratum, Retraction of Publication, Retracted Publication, or
  Expression of Concern. PubMed returned `count=0` and an empty ID list. Raw response:
  `data/science-evidence/2026-07-31-apob-low-ldl-2075/pubmed-30939045-correction-retraction-esearch.json`.
- Source `DOI:10.1001/jamacardio.2022.5271` was searched with the same publication-type
  filter. PubMed returned `count=0` and an empty ID list. Raw response:
  `data/science-evidence/2026-07-31-apob-low-ldl-2075/pubmed-36723951-correction-retraction-esearch.json`.

The saved responses retain PubMed's normalized query, warnings, result count, and ID list.
PubMed recognized Published Erratum, Retracted Publication, and Expression of Concern in the
normalized query; its response reports the unrecognized phrases transparently. Supplemental
public web searches using each DOI plus `correction OR erratum OR retraction` also surfaced no
notice. These results record what was indexed and returned on 2026-07-31, not a guarantee that
no later notice exists.

## Payload format and usage

The raw files are NCBI E-utilities PubMed ESummary and ESearch JSON responses captured on
2026-07-31. Every response identifies JSON schema version `0.3`; the live PubMed service does
not expose a frozen database build identifier, so the access date is the packet's retrieval
version boundary. They contain citation/search metadata only, not abstracts, article full
text, participant-level data, genotypes, credentials, or restricted material.

NCBI's [Website and Data Usage Policies and Disclaimers](https://www.ncbi.nlm.nih.gov/home/about/policies/)
(accessed 2026-07-31) state that U.S.-government-created site information is public domain and
may be copied with acknowledgment, while contributed or licensed material may remain
protected; NLM does not claim copyright in PubMed abstracts, but publishers or authors may.
The [E-utilities usage requirements](https://www.ncbi.nlm.nih.gov/books/NBK25497/)
(accessed 2026-07-31) require the NCBI disclaimer/copyright notice to remain evident when
E-utilities are used in software. This packet therefore redistributes only the citation/search
metadata returned by the API, preserves NCBI and publisher attribution, excludes abstracts and
full text, and asserts no blanket license over publisher-contributed material. The repository
documentation paraphrases the mapped claims and links to the primary sources.

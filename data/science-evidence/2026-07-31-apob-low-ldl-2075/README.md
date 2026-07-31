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

Targeted public web searches run on 2026-07-31:

- `"10.1161/CIRCGEN.118.002376" correction OR erratum OR retraction`
- `"10.1001/jamacardio.2022.5271" correction OR erratum OR retraction`

Both searches returned the primary article or citation pages and surfaced no
correction, erratum, expression of concern, or retraction notice. The saved
PubMed ESummary records likewise contain neither a correction/retraction
publication type nor such a linked record. This records the searches and
returned metadata as of 2026-07-31, not a guarantee that no later notice exists.

## Payload format and usage

The raw files are NCBI E-utilities ESummary JSON responses captured on
2026-07-31. They contain public PubMed citation metadata only, not article full
text, participant-level data, genotypes, credentials, or restricted material.
Use is subject to NCBI's public data and usage policies. The repository
documentation paraphrases the mapped claims and links to the primary sources.

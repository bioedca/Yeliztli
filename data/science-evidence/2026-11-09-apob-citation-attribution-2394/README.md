# APOB source-attribution audit for issue #2394

This packet corrects the attribution above the APOB protein-truncating rule in
`backend/analysis/cardiovascular.py`. The comment now cites the primary studies
already supporting that rule. Executable code, panel data, reference build
(GRCh37/b37), and clinical interpretation are unchanged.

Audit base: `107f79d638658fda46507b1061047018128c9445`.
Queries and retrieval fallbacks are recorded in `queries.json`.

## Current attribution evidence

- The official [ClinGen CSpec registry](https://cspec.genome.network/cspec/api/svis?detail=high)
  lists APOB `CSpec:GN237`, version **1.0**, with status **Classification Rules
  In Prep** (accessed 2026-11-09). The same response lists LDLR `CSpec:GN013`,
  version **1.2**, as **Released** (accessed 2026-11-09), providing a positive
  control for the registry's distinct status values. Both identify the Familial
  Hypercholesterolemia Variant Curation Expert Panel. The response contained 212
  records; its hash and selected source fields are retained in
  `data/science-evidence/2026-11-09-apob-citation-attribution-2394/raw/cspec-fh-specifications.json`.
- [DOI:10.1016/j.atherosclerosis.2025.119569](https://doi.org/10.1016/j.atherosclerosis.2025.119569)
  identifies Chora et al.'s 2025 publication about developing APOB/PCSK9
  interpretation guidelines (accessed 2026-11-09).
  [Crossref's deposited record](https://api.crossref.org/works/10.1016/j.atherosclerosis.2025.119569)
  gives *Atherosclerosis*, volume 407, article 119569, August 2025, and the broad
  type `journal-article` (accessed 2026-11-09). That type does not establish
  expert-panel approval. The indexed
  [publisher page](https://www.atherosclerosis-journal.com/article/S0021-9150%2825%2900467-8/fulltext)
  labels the item an abstract in the supplement and supplies conference-session
  identifiers (accessed 2026-11-09). The direct page returned HTTP 403, so this
  audit does not claim to have verified the abstract body's approval wording.
  These sources are retained as selected metadata in
  `data/science-evidence/2026-11-09-apob-citation-attribution-2394/raw/crossref-119569.json`
  and
  `data/science-evidence/2026-11-09-apob-citation-attribution-2394/raw/publisher-index-metadata.json`.

The registry status is the authority for the current release-state assertion.
The publication is removed from the rule's supporting comment because it is
not evidence of a released APOB specification. The comment instead points to
the existing primary-study evidence below. This decision does not depend on
interpreting inaccessible abstract prose, nor does it claim that the abstract
is scientifically invalid.

## Retained biological evidence

The existing
[`2026-07-31-apob-low-ldl-2075` packet](../2026-07-31-apob-low-ldl-2075/README.md)
maps the APOB protein-truncating/low-LDL statement to two primary studies:

1. Peloso et al. (2019),
   [PMID:30939045](https://pubmed.ncbi.nlm.nih.gov/30939045/),
   [DOI:10.1161/CIRCGEN.118.002376](https://doi.org/10.1161/CIRCGEN.118.002376)
   (accessed 2026-11-09). Its public abstract describes direct analysis of
   Japanese hypobetalipoproteinemia families and CHD case-control studies, with
   lower LDL cholesterol among APOB protein-truncating-variant carriers.
2. Dron et al. (2023),
   [PMID:36723951](https://pubmed.ncbi.nlm.nih.gov/36723951/),
   [DOI:10.1001/jamacardio.2022.5271](https://doi.org/10.1001/jamacardio.2022.5271)
   (accessed 2026-11-09). The primary
   [PMC article](https://pmc.ncbi.nlm.nih.gov/articles/PMC9996405/)
   reports a separately enrolled UK Biobank analysis supporting the low-LDL
   direction (accessed 2026-11-09).

For independence, the mapped components are Peloso's Japanese family series
and Dron's UK Biobank cohort, with distinct recruitment populations and direct
genotype/phenotype analyses. They share investigators, and Dron cites earlier
work; that does not make the mapped result a reused upstream assertion. This
audit does not count Dron's additional NHLBI cohorts as another independent
source or assume that every cohort in both papers is disjoint. It introduces
no new effect-size, treatment, or individual-risk claim.

Fresh NCBI PubMed EFetch metadata for both PMIDs contains no
`CommentsCorrectionsList` entries and no retraction publication type. The
sanitized source snapshot retains every such element and attribute if present:
`data/science-evidence/2026-11-09-apob-citation-attribution-2394/raw/pubmed-citations.xml`.
Requests, document type, source/sanitized hashes, record counts, and extracted
identifiers/notices are in
`data/science-evidence/2026-11-09-apob-citation-attribution-2394/raw/pubmed-citations-provenance.json`.
Scite also resolved all three publication identifiers and returned no
indexed editorial notices or contrasting-evidence flags (accessed 2026-11-09):
`data/science-evidence/2026-11-09-apob-citation-attribution-2394/raw/scite-editorial-check.json`.
These checks describe the returned indexes on that date, not a guarantee that
no notice exists. Scite and Consensus are discovery/audit tools, not the cited
scientific authorities.

## Versions, licensing, and limits

CSpec supplies per-record versions, but no overall frozen registry version or
license in this API response. Only factual identifier/status metadata is
retained; no specification prose is redistributed and no broader license is
asserted. Crossref's deposit/index timestamps and index version are retained.
Its [Metadata Retrieval documentation](https://www.crossref.org/documentation/retrieve-metadata)
describes bibliographic metadata as factual and Crossref-generated data as CC0;
abstracts retain publisher/author copyright (accessed 2026-11-09). The article's
deposited text-mining license links are recorded without treating them as a
general redistribution license. The publisher excerpt contains only brief
publication/session metadata, with its indexed retrieval limitation explicit.

NCBI's [usage policy](https://www.ncbi.nlm.nih.gov/home/about/policies/)
distinguishes government-created public-domain information from contributed
material that may remain protected and requests attribution to NLM
(accessed 2026-11-09). This packet retains citation/link metadata only. PubMed's
document type and returned revision dates identify the source format; the
access date is the retrieval boundary, not a frozen PubMed release. Full raw
EFetch text was sanitized in memory before writing any file. Abstracts, article
text, authors, contacts, affiliations, participant data, genotypes, credentials,
institutional-access links, and third-party HTML/JavaScript are excluded.

Crossref and the publisher share the same publication deposit; CSpec and the
panel share the same specification authority. They are not counted as
independent biological studies. `raw-hashes.json` covers every retained payload.
Software tests verify unchanged application behavior and metadata consistency;
they do not scientifically validate the underlying biological statement.

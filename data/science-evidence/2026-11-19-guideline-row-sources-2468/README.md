# Guideline mapping inventory for #2468

The snapshot covers the 81 production rows in
`backend/data/cpic/cpic_guidelines.csv` at `cf08f712a130591598eae00648a1b8dce2d0b384`.
It supports a representation decision. It evaluates no individual's treatment
and makes no clinical-equivalence determination.

## Primary sources

- CPIC's published guideline-development method distinguishes grading the underlying evidence from assigning a strength to prescribing guidance. Caudle et al. [PMID:24479687](https://pubmed.ncbi.nlm.nih.gov/24479687/) (accessed 2026-11-19).
- CPIC's account of its structured implementation resources and their updating describes the source authority. Relling et al. [PMID:31562822](https://pubmed.ncbi.nlm.nih.gov/31562822/) (accessed 2026-11-19). These authors speak for the same consortium. Their descriptions and the implementing database together establish its terminology. They supply no independent clinical replication.
- The current implementation returns `cpiclevel` and `guidelineurl` from a gene–drug pair and `classification`, `population`, `phenotypes`, `activityscore` and `lookupkey` on recommendation records. The drug, pair and guideline responses establish authorship fields, key shapes and the inventory counts. [CPIC v1 registry](https://api.cpicpgx.org/v1/) (accessed 2026-11-19).
- The three shipped irinotecan rows link to the official FDA pharmacogenetic-association table. CPIC's UGT1A1/irinotecan pair level is A with a null guideline URL and zero returned drug recommendations. This establishes which source a row cites. No claim about the equivalence of the shipped wording to a label follows. [FDA table](https://www.fda.gov/medical-devices/precision-medicine/table-pharmacogenetic-associations) (accessed 2026-11-19).
- PubMed EFetch returned no linked correction or retraction for either methods paper on the access date. Source fields and the response digest are in `data/science-evidence/2026-11-19-guideline-row-sources-2468/raw/pubmed.json`. Guideline records can change independently of those publications.

## Acquisition and comparison

Consensus query: `Clinical Pharmacogenetics Implementation Consortium database and API terminology guideline recommendation strength gene drug pair CPIC level structured pharmacogenomics recommendations`.
Scite query: `"Clinical Pharmacogenetics Implementation Consortium" AND ("database" OR "informatics") AND ("recommendations" OR "guidelines")`. NCBI Entrez resolved
`10.2174/1389200215666140130124910[doi] OR 10.1002/cpt.1651[doi]` and
fetched the two corresponding records. Discovery results are not evidence
of prescribing effects.

The 18 distinct drug names in the CSV each resolved to exactly one CPIC
drug ID. For each drug, the audit fetched its records from `recommendation`
and `pair_view`. One further `guideline` request located the 12 documents
referenced by the recommendations. All 55 CPIC requests used
`Prefer: count=exact`. Each returned array length equaled the server's
reported total, including valid empty arrays. All 304 recommendation IDs
were distinct. The 49 pair rows include genes beyond the shipped set.
All retrieved recommendation versions were 1. The access date and response
hashes bound this live API snapshot; the per-record version alone does not
identify a database release.

| File | Retained metadata |
| --- | --- |
| `raw/requests.json` | Exact request URLs and counts, raw response SHA-256 digests and source CSV digest. |
| `raw/registry.json` | Projected source records for drug identifiers, gene–drug levels, URLs and referenced guidelines. |
| `raw/fda-source.json` | FDA table URL, response digest and the matching drug and gene cells. No clinical text or participant data is retained. |
| `raw/recommendations.jsonl` | One source record per line with original selected fields, except recommendation prose is represented by the SHA-256 of its unmodified UTF-8 bytes. Null is preserved. |
| `shipped-comparison.jsonl` | One line per production CSV row, in source order, with local key, text digest, candidate IDs and identical-text IDs. Duplicated rows would still be separate observations. |

To reproduce the grouping offline, select candidate IDs by drug ID and
literal equality of `phenotypes[shipped_gene]` to the shipped phenotype.
Within each candidate set compare the SHA-256 fields. Count empty sets
(12), nonempty sets with any identical text (6), and the rest (63). Count
the distinct source text hashes per shipped row to find 32 rows with more
than one source text. Compare `cpiclevel` only for an actual source
gene–drug pair. Keep slash-delimited local keys separate from that join.
The absence of a literal match includes three SLCO1B1 capitalization
differences and cannot establish that source guidance is unavailable.

CPIC curated content is offered under CC0 1.0, with attribution, access date,
version and a request to consult the current guideline. The packet retains
factual metadata and digests. Article text and labels are not redistributed.
[CPIC terms](https://www.clinpgx.org/page/cpicLicensing) (accessed 2026-11-19).
NCBI's terms distinguish government-created content from material with
contributed rights. [NCBI usage policy](https://www.ncbi.nlm.nih.gov/home/about/policies/) (accessed 2026-11-19).

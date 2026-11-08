# Persisted PRS ancestry-calibration repair (#2474)

This packet records public bibliographic verification for references already
cited by `backend/analysis/prs_calibration.py`. The change applies the existing
software withholding policy to stored results. It introduces no new biological,
clinical or statistical effect claim, model, calibration formula or threshold.
All regression scores and samples are synthetic. GRCh37/b37 storage and joins
are unchanged; this content migration does not interpret genomic coordinates.

## Source verification

Consensus and Scite were queried first, followed by the NCBI Entrez skill.
Exact queries, selected metadata and the verification response are retained in
`raw/discovery-metadata.json`; selected source-native PubMed XML is in
`raw/pubmed-metadata.xml`. These repository-relative files contain no sample
data, abstracts, affiliations, contact details or provider access links.

- *Polygenic scoring accuracy varies across the genetic ancestry continuum*.
  [PMID:37198491](https://pubmed.ncbi.nlm.nih.gov/37198491/),
  [DOI:10.1038/s41586-023-06079-4](https://doi.org/10.1038/s41586-023-06079-4)
  (accessed 2026-11-08). PubMed returned no linked correction or retraction.
- *Selection, optimization and validation of ten chronic disease polygenic risk
  scores for clinical implementation in diverse US populations*.
  [PMID:38374346](https://pubmed.ncbi.nlm.nih.gov/38374346/),
  [DOI:10.1038/s41591-024-02796-z](https://doi.org/10.1038/s41591-024-02796-z)
  (accessed 2026-11-08). PubMed links this publication as an update of preprint
  PMID:37333246; that `UpdateOf` relation is not a correction or retraction.

Scite verified both publications and reported zero editorial notices on
2026-11-08. This bounded check cannot establish that future notices will not
appear. Discovery providers and PubMed records of the same paper are not
independent scientific evidence. The two papers are background references, not
a pair of independent validations of Yeliztli's ancestry gate or score models;
no shared-cohort independence or model-validity claim is made here. Tests prove
software behavior, not scientific validity.

## Claim mapping and limits

The migration's authority is the already released software contract from
#2168/#2473: `get_ancestry_fractions` withholds a reference when the selected
ancestry is `UNCERTAIN`, explicitly insufficient, or classified `uncertain`.
The stored source is selected using the scorer's category/recency order, with
the existing local-ancestry quarantine and presentation policy intact.

Schema 36 repairs only the five named PRS modules when the stored continuous
calibration fractions exactly match the selected uncertain source's normalized
fractions, the numeric fields agree, and the finding text matches released
producer wording. It clears percentile, z-score, interval and applied
calibration fields, and rewrites that generated text to the existing
uncalibrated wording. The raw score, coverage, source provenance, citations and
unknown metadata remain unchanged. Exact legacy caveat wording loses obsolete
percentile guidance; customized caveats remain intact.

Matching current `added`/`changed` finding-diff text is repaired atomically.
Ambiguous identities and historical `removed` entries remain unchanged because
the current row cannot prove a removed result's calibration provenance.
Confident/admixture-sufficient, static-reference, mismatched-source, malformed,
custom-text and unrelated rows remain unchanged. Such rows require independent
review or reanalysis if their validity is in doubt; this repair does not infer
their history. It does not rescore or certify any retained result.

The report renderer used by PDF generation and the five module APIs consume
the repaired stored state. Raw SQL findings access remains blocked by its
existing clinical-audit protection.

## Terms

Only bibliographic metadata is retained, not article abstracts or full text.
Article rights remain with their respective rights holders; no full-text reuse
license is claimed. The [NCBI policies](https://www.ncbi.nlm.nih.gov/home/about/policies/)
(accessed 2026-11-08) describe government-created information as public domain
and preserve submitter intellectual-property caveats. Attribution is to the
cited publications and NCBI. No provider fallback or NGS pipeline was needed.

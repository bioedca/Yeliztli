# Query log — ACMG BS1 founder-frequency window

Initial discovery queries used public, sanitized scientific terms and were
accessed on 2026-08-02. The provider-response and PubMed correction snapshots
were re-fetched on 2026-08-03. No real genotype, sample, credential, or
restricted payload was submitted or retained.

## Claim identifiers

- C1: fail-closed repository-consistency boundary for BS1.
- C2: repository AF/AN ingestion, persistence, and assessment inventory.
- C3: citation provenance for the existing BA1/general-population distinction.
- C4: public gnomAD GraphQL endpoint availability only.

## Evidence ladder

1. Consensus search: `ACMG BS1 BA1 allele frequency founder populations
   Ashkenazi Jewish Finnish filtering allele frequency ClinGen SVI`.
   The returned Ghosh et al. record was fetched as Consensus ID
   `4439bbb6071d5097972fac0f2fea8fb0`. The sanitized search/fetch envelopes
   are retained in `raw/consensus-search-fetch-sanitized.json`.
2. The 2026-08-03 Scite snapshots comprise a batch DOI lookup for
   `10.1002/humu.23642`, `10.1002/cphg.93`, and `10.1002/humu.24152` with no
   term (retaining returned metadata for all three papers), then a targeted
   `10.1002/cphg.93` lookup with `filtering allele frequency BA1 BS1` and a
   targeted `10.1002/humu.24152` lookup with `Ashkenazi Finnish BA1 BS1`.
   The sanitized response envelopes are retained in
   `raw/scite-targeted-doi-responses-sanitized.json`.
3. NCBI Entrez ESummary queried PubMed IDs `30311383,32461654,28518168`.
   A 2026-08-03 PubMed EFetch correction check additionally resolved the Scite
   DOIs to PMIDs `31479589` and `33300245`; its sanitized result is retained in
   `pubmed-efetch-corrections-sanitized.json`.
4. The gnomAD GraphQL skill sent a POST request to
   `https://gnomad.broadinstitute.org/api` for
   `query { meta { clinvar_release_date } }` to record public endpoint
   availability. It did not fetch a dataset, variant, sample, or genotype record.

## Results and checks

- Consensus supplied the Ghosh BA1 recommendation record; no Consensus result
  is cited without its required fetch step. The direct request/result envelope
  is indexed by `source-response-index.json` rather than replaced by prose.
- Scite's targeted response contains short, source-field-addressed context for
  the FAF methodological note and TP53-specific ASJ/FIN example. Each response
  omits a `retraction_notices` field; that field absence is preserved, but is
  not treated as a retraction clearance.
- PubMed EFetch checked correction links for all five cited PMIDs. The Ghosh,
  FAF-overview, TP53, and Whiffin records have no
  `CommentsCorrectionsList`; the gnomAD reference record (PMID:32461654) has
  two `ErratumIn` links and two `CommentIn` links. The packet retains that
  reference only for dataset provenance, not for a population-frequency result.
- The gnomAD endpoint returned `clinvar_release_date` `2026-06-06`; this is
  endpoint metadata, not a gnomAD dataset release version. The local reference
  bundle remains separately versioned as gnomAD r2.1.1 exomes on GRCh37/b37.

## Sanitization

`literature-metadata.json` is a derived citation index. The linked sanitized
raw response artifacts preserve provider operation, query/identifier, returned
citation metadata, availability/status fields, and bounded source context while
removing abstracts, long excerpts, author/affiliation detail, access/download
URLs, tracking parameters, and email-bearing URLs. The PubMed artifact removes
article text and personal/affiliation metadata while retaining only the
correction-link fields needed for this check.

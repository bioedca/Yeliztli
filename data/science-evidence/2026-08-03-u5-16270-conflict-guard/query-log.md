# Query log — U5 m.16270 ancestral-conflict guard

All searches used public, sanitized terms and were accessed on 2026-08-03. No
real genotype, sample, credential, or restricted data was submitted or retained.

## Claim identifiers

- C1: exact Build 17 U5 source motif and local source-record extract.
- C2: narrow fail-closed repository behavior for an ancestral or discordant
  typed guard position.
- C3: literature context for a curated mtDNA phylogeny in haplogrouping.
- C4: correction/retraction lookup results and their limits.

## Evidence ladder

1. Consensus search query: human mitochondrial DNA phylogeny U5 C16270T
   m.16270 PhyloTree. The returned records
   70d82b1aaccb53b9a0722084905a6ba3 (Build 17) and
   acdbf1a62608546cb0a06fc9a83e04fb (2009 PhyloTree paper) were each fetched
   before being retained. The bounded request and metadata envelopes are in
   raw/consensus-search-fetch-sanitized.json.
2. Scite targeted DOI lookup queried 10.1002/humu.20921 and
   10.3390/ijms22115747 with PhyloTree mitochondrial DNA haplogroup U5. The
   response metadata and retraction-field availability are retained in
   raw/scite-targeted-doi-responses-sanitized.json.
3. The NCBI Entrez skill queried PubMed ESummary for 18853457,34072215, then
   EFetch for the same public IDs to inspect correction-link fields. The
   bounded, sanitized metadata is in pubmed-esummary.json.
4. The exact U5 source row was copied from the repository's Build 17
   source-audited registry, not inferred from literature text. The release
   archive URL and SHA-256 are retained with the minimal U5 extract in
   raw/phylotree-build17-u5-source-extract.json.

## Results and limits

- Consensus results were fetched before use. They identify the Build 17 and
  2009 phylogeny publications but do not provide the exact U5 marker evidence.
- Scite identifies the two DOI records and reports Build 17 as the basis of the
  2021 refinement. Its response omitted retraction_notices; that absence is
  recorded and is not interpreted as a retraction clearance.
- PubMed EFetch did not emit a CommentsCorrectionsList for either retained
  record. This is a per-query observation, not a claim that no correction or
  retraction can exist elsewhere.
- The release-pinned Build 17 registry is the sole primary source for the exact
  C16270T edge. The literature supports context only, so the implementation
  makes no clinical or high-stakes biological assertion.

## Sanitization

The raw payload copies preserve operation, public query or identifier, returned
citation metadata, source-release identity, and correction-status fields needed
for review. They omit article abstracts and full text, author and affiliation
details, service access/download URLs, tracking parameters, and any data about a
person or sample.

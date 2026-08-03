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

1. Consensus was invoked with the sanitized query: human mitochondrial DNA
   phylogeny U5 C16270T m.16270 PhyloTree. It was used for primary-source
   discovery only. No response payload, generated summary, citation context,
   or result identifier is retained, redistributed, or used as evidence; see
   the [Consensus terms](https://consensus.app/home/terms-of-service/).
2. Scite was invoked with the sanitized query: PhyloTree mitochondrial DNA
   haplogroup U5. It was used for primary-source discovery only. No response
   payload, citation context, tally, classifier result, or result identifier
   is retained, redistributed, or used as evidence; see the
   [Scite terms](https://scite.org/terms).
3. The NCBI Entrez skill queried PubMed ESummary for 18853457,34072215, then
   EFetch for the same public IDs to inspect correction-link fields. The
   bounded, sanitized metadata is in pubmed-esummary.json.
4. The exact U5 source row was copied from the repository's Build 17
   source-audited registry, not inferred from literature text. The release
   archive URL and SHA-256 are retained with the minimal U5 extract in
   raw/phylotree-build17-u5-source-extract.json.

## Results and limits

- Consensus and Scite are discovery aids only. Their output is not retained or
  used as evidence; the primary identifiers were independently retrieved and
  recorded through NCBI and the source-audited registry.
- PubMed EFetch did not emit a CommentsCorrectionsList for either retained
  record. This is a per-query observation, not a claim that no correction or
  retraction can exist elsewhere.
- The release-pinned Build 17 registry is the sole primary source for the exact
  C16270T edge. The literature supports context only, so the implementation
  makes no clinical or high-stakes biological assertion.

## Sanitization

The retained raw payload copies preserve only source-release identity, durable
primary identifiers, and correction-status fields needed for review. They omit
article abstracts and full text, author and affiliation details, service
access/download URLs, tracking parameters, and any data about a person or
sample. No Consensus or Scite provider output is retained.

# Query log — U5 m.16270 ancestral-conflict guard

All searches used public, sanitized terms and were accessed on 2026-08-03. No
real genotype, sample, credential, or restricted data was submitted or retained.

## Claim identifiers

- C1: exact Build 17 U5 source motif and local source-record extract.
- C2: narrow fail-closed repository behavior for an ancestral or discordant
  typed guard position.
- C3: literature context for a curated mtDNA phylogeny in haplogrouping.
- C4: correction/retraction lookup results and their limits.
- C5: recurrence and back mutation at m.16270, which bound how strongly the
  guard may be stated.

## Evidence ladder

1. Consensus was invoked with the sanitized queries: human mitochondrial DNA
   phylogeny U5 C16270T m.16270 PhyloTree; and recurrent mutation hotspot
   mitochondrial control region site 16270 back mutation homoplasy haplogroup
   assignment. It was used for primary-source discovery only. No response
   payload, generated summary, citation context, or result identifier is
   retained, redistributed, or used as evidence; see the
   [Consensus terms](https://consensus.app/home/terms-of-service/).
2. Scite was invoked with the sanitized queries: PhyloTree mitochondrial DNA
   haplogroup U5; "haplogroup U5" AND "16270" AND mitochondrial HVS-I motif;
   and a DOI-scoped lookup of 10.1086/302802 for 16270 back mutation U5 16192
   recurrent. It was used for primary-source discovery only. No response
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
5. For C5, PubMed was searched for `"16270"[All Fields] AND mitochondrial AND
   U5` (one hit, 10712215) and for the founder-analysis record (11032788).
   ESummary and EFetch were then run for both IDs to resolve durable
   identifiers and inspect correction-link fields; the results are in
   pubmed-esummary.json.

## Adverse-evidence search

C5 came from searching *against* the change rather than for it. The question
asked was whether an ancestral call at m.16270 can occur in a real U5 lineage.
It can: PMID:10712215 reports a back mutation at nt 16270 in a cohort whose 22
haplogroup-U mtDNAs were all U5, and PMID:11032788 treats recurrent
control-region mutation as a first-order obstacle requiring explicit screening
criteria across far larger, unrelated sample sets. The packet's earlier
"incompatible with descent" wording was therefore withdrawn in favour of
withholding language. The runtime behavior did not need to change: it already
degrades to the emitted ancestor U instead of asserting an exclusion.

## Results and limits

- Consensus and Scite are discovery aids only. Their output is not retained or
  used as evidence; the primary identifiers were independently retrieved and
  recorded through NCBI and the source-audited registry.
- One Consensus call failed with a rate-limit error and was not retried. One
  Scite call exceeded the client result-size limit and was narrowed to DOI- and
  title-scoped lookups. Both fallbacks are recorded in
  source-response-index.json.
- An attempt to retrieve PMID:11032788 full text through an authoritative route
  failed: PMC1288566 returns front matter only, and the publisher copy returned
  HTTP 403. A corroborating passage that Scite's excerpts surfaced for that
  record is therefore excluded from the claim map, and C5 rests on the two
  Abstract-level statements NCBI does serve.
- PubMed EFetch did not emit a CommentsCorrectionsList for any of the four
  retained records. This is a per-query observation, not a claim that no
  correction or retraction can exist elsewhere.
- The release-pinned Build 17 registry is the sole primary source for the exact
  C16270T edge. The literature supports context and the C5 limitation, so the
  implementation makes no clinical or high-stakes biological assertion.
- No source found here quantifies how often a genuine U5 carrier would be typed
  ancestral at m.16270, so no false-veto rate is stated anywhere in this packet.

## Sanitization

The retained raw payload copies preserve only source-release identity, durable
primary identifiers, correction-status fields needed for review, and — where a
record supports a claim — a human-authored paraphrase naming the section it was
read from. They omit article abstracts and full text, author and affiliation
details, service access/download URLs, tracking parameters, and any data about a
person or sample. No Consensus or Scite provider output is retained.

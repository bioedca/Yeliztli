# U5 m.16270 ancestral-conflict guard — issue #2165

## Scope

This packet supports a narrow, fail-closed change to mtDNA haplogroup assignment.
PhyloTree Build 17 records C16270T in U5's direct source motif. The repository
keeps U5 markerless because that position is callable in only two of the four
audited primary array exports; emitting it as an ordinary required marker would
wrongly exclude otherwise compatible U5 descendants. A typed ancestral C at that
exact position is nevertheless incompatible with descent through the U5 source
edge, so the implementation withholds U5 and descendants. Only an unambiguous
derived T or a missing/no-call value permits traversal; neither establishes U5.
Discordant or mixed typed vendor rows also withhold the U5 subtree rather than
being collapsed to missing.

This is an implementation-level source-conflict rule. It does not make a
clinical, phenotypic, population, ancestry, or forensic conclusion, and it does
not emit a U5 label from m.16270 alone. All test records are synthetic; no
genotype, sample, PII, credential, or restricted source payload is retained.

## Claim map

| ID | Claim | Source and version | Sanitized payload | Scope and limitation |
| --- | --- | --- | --- | --- |
| C1 | The direct Build 17 source motif for U5 contains C16192T and C16270T; C16270T has ancestral C, derived T, and is represented locally as i5016270 at position 16270. | PhyloTree Build 17 public archive, SHA-256 3fe8cf00a15e1ccb09235091016eef1af3a68f44dd9355dd2b7666f8f767b146 (accessed 2026-07-12; registry extract reviewed 2026-08-03). | raw/phylotree-build17-u5-source-extract.json | The archive is the authoritative source for the exact motif; the packet does not claim an independently replicated laboratory observation. |
| C2 | A typed ancestral C, including a discordant typed position, must stop markerless U5 descent; T or missing/no-call must not be treated as a positive U5 call. | Repository implementation and synthetic regression tests, immutable pre-evidence commit aa3acc8df76f782de3ade41a95d6e5a1d9f96da4 (accessed 2026-08-03). | source-inventory.json | Conservative software behavior only. It withholds an otherwise possible label rather than inferring a biological or clinical result. |
| C3 | PhyloTree is a curated mtDNA phylogeny used as input to haplogrouping methods, and Build 17 was used as the basis for the later refinement study. | PMID:18853457, DOI:10.1002/humu.20921; PMID:34072215, DOI:10.3390/ijms22115747 (accessed 2026-08-03). | pubmed-esummary.json | These papers provide separate methodological context; neither is represented as an independent validation of the exact U5 C16270T row. Consensus and Scite were used only to discover primary sources; their output is neither retained nor evidence. |
| C4 | A correction-link check was recorded for both literature records. | PubMed EFetch (accessed 2026-08-03). | pubmed-esummary.json | PubMed returned no CommentsCorrectionsList in either retained record. This is not a general retraction clearance. |

## Evidence interpretation

The primary evidence for the exact source edge is the pinned public PhyloTree
Build 17 archive represented by the repository's source-audited U5 extract. The
2009 PhyloTree paper and the 2021 haplogrouping-method paper agree
that a curated global mtDNA phylogeny is an appropriate input to haplogrouping;
the latter explicitly describes Build 17 as the basis for motif refinement.
They share the PhyloTree lineage of evidence and therefore are not counted as
independent confirmation of m.16270 itself. The rule deliberately avoids a
high-stakes biological or clinical claim that would require independent source
evidence.

The implementation consequence follows only from source-edge logic: an observed
ancestral allele contradicts the edge's listed derived event, so continuation
through that edge is withheld. Missing data remains non-evidence, and a derived
allele remains insufficient to assign U5 without the rest of the path. This
matches the existing markerless-node contract and prevents an ambiguous
aggregation of conflicting rows from becoming a fail-open path.

## Scope boundaries and residual risk

- The guard is scoped to U5. It does not alter ordinary mtDNA marker aggregation
  or reinterpret the same locus for L1b.
- The packet does not add a medical, ancestry, forensic, frequency, or population
  claim, and it does not replace a full mtDNA sequencing workflow.
- The direct motif and array-coverage observations are release-pinned to the
  repository's existing Build 17 registry and audited primary exports. A future
  source-build or array-coverage update needs a new provenance review.
- Absence of a PubMed correction-link field is only a recorded lookup result; it
  is not a general guarantee that a record has never been corrected or retracted.

## Reproducibility and source handling

source-inventory.json binds the evidence packet to the pre-evidence
implementation commit and the exact local source extract, avoiding a
self-referential commit identifier. source-response-index.json maps the two
retained primary-source payloads to checksums. The primary archive URL in
`source_archive.archive_url` is an explicit exception to the routine exclusion
of access URLs: it identifies the versioned public archive whose checksum is
needed to reproduce C1. Service access URLs are not retained in raw payloads;
the policy URLs below are human-authored citations rather than provider output.
All paths are repository-relative.

PhyloTree's Build 17 landing, archive-link, home, and update-history pages did
not state a licence or reuse grant when inspected on 2026-08-03. This packet
stores only a minimal public provenance extract and source digest, cites the
requested source, and does not assert a reuse licence. The PubMed payload is
limited to document-summary metadata retrieved through the official NCBI
interface; it reproduces neither abstracts nor full text and follows the
[NCBI policies](https://www.ncbi.nlm.nih.gov/home/about/policies/) and
[PubMed disclaimer](https://pubmed.ncbi.nlm.nih.gov/disclaimer/).

Discovery-service handling (accessed 2026-08-03). Consensus and Scite were
invoked only with a sanitized public query to discover primary sources and
screen the literature. No provider response payload, generated summary,
citation context, citation tally, classifier result, account data, or
personal/genomic data is retained in this repository. The evidence packet uses
only independently obtained primary-source records and identifiers. Provider
output is neither redistributed nor used as scientific evidence. See the
[Consensus terms](https://consensus.app/home/terms-of-service/) and
[Scite terms](https://scite.org/terms).

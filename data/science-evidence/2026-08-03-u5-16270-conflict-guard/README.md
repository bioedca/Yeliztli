# U5 m.16270 ancestral-conflict guard — issue #2165

## Scope

This packet supports a narrow, fail-closed change to mtDNA haplogroup assignment.
PhyloTree Build 17 records C16270T in U5's direct source motif. The repository
keeps U5 markerless because that position is callable in only two of the four
audited primary array exports; emitting it as an ordinary required marker would
wrongly exclude otherwise compatible U5 descendants. A typed ancestral C at that
exact position does not carry the source edge's listed derived state, so the
implementation stops at the emitted parent U and withholds U5 and its
descendants. Only an unambiguous derived T or a missing/no-call value permits
traversal; neither establishes U5. Rows whose typed evidence disagrees with
itself — two discordant vendor probes at the position, or a merged sample's
single `flag_only` ambiguity sentinel — also withhold the U5 subtree rather than
being collapsed to missing.

The guard withholds a subtype; it does not assert that the sample is not U5.
That distinction is required by the evidence: back mutation at m.16270 inside
U5 is documented (C5 below), so an ancestral C is a reason to stop reporting a
finer label, not grounds for an exclusion claim. The reported result degrades to
the less specific ancestor U, which is the conservative direction.

This is an implementation-level source-conflict rule. It does not make a
clinical, phenotypic, population, ancestry, or forensic conclusion, and it does
not emit a U5 label from m.16270 alone. All test records are synthetic; no
genotype, sample, PII, credential, or restricted source payload is retained.

## Claim map

| ID | Claim | Source and version | Sanitized payload | Scope and limitation |
| --- | --- | --- | --- | --- |
| C1 | The direct Build 17 source motif for U5 contains C16192T and C16270T; C16270T has ancestral C, derived T, and is represented locally as i5016270 at position 16270. | PhyloTree Build 17 public archive, SHA-256 3fe8cf00a15e1ccb09235091016eef1af3a68f44dd9355dd2b7666f8f767b146 (accessed 2026-07-12; registry extract reviewed 2026-08-03). | raw/phylotree-build17-u5-source-extract.json | The archive is the authoritative source for the exact motif; the packet does not claim an independently replicated laboratory observation. |
| C2 | A typed ancestral C must stop markerless U5 descent, as must any position whose typed evidence disagrees with itself — two discordant vendor probes, or a merged sample's single `flag_only` ambiguity sentinel; T or missing/no-call must not be treated as a positive U5 call. | Repository implementation and synthetic regression tests, immutable pre-evidence commit aa3acc8df76f782de3ade41a95d6e5a1d9f96da4 (accessed 2026-08-03). | source-inventory.json | Conservative software behavior only. It withholds an otherwise possible label rather than inferring a biological or clinical result. |
| C3 | PhyloTree is a curated mtDNA phylogeny used as input to haplogrouping methods, and Build 17 was used as the basis for the later refinement study. | PMID:18853457, DOI:10.1002/humu.20921; PMID:34072215, DOI:10.3390/ijms22115747 (accessed 2026-08-03). | pubmed-esummary.json | These papers provide separate methodological context; neither is represented as an independent validation of the exact U5 C16270T row. Consensus and Scite were used only to discover primary sources; their output is neither retained nor evidence. |
| C4 | A correction-link check was recorded for every retained literature record. | PubMed EFetch (accessed 2026-08-03). | pubmed-esummary.json | PubMed returned no CommentsCorrectionsList in any retained record. This is not a general retraction clearance. |
| C5 | Position 16270 is not an infallible U5 discriminator: a back mutation at nt 16270 has been reported within a haplogroup-U5 cohort, and recurrent control-region mutation is treated as a first-order obstacle that must be screened for when assigning mtDNA lineages. | PMID:10712215, DOI:10.1086/302802 (22 Finnish haplogroup-U mtDNAs, all U5, coding-region CSGE network vs. HVS-I; supporting statement in the record Abstract). PMID:11032788 (1,234 Near Eastern + 2,804 European + 208 north-Caucasus HVS-I samples; founder analysis with explicit recurrent-mutation criteria; supporting statement in the record Abstract). Both accessed 2026-08-03. | pubmed-esummary.json | This is the limiting claim, and it is the reason the guard is written as withholding rather than exclusion. The two records are separate cohorts, laboratories, and methods; they are not fully independent, because the 2000 founder-analysis paper cites the 2000 Finnish network paper for instability at the neighbouring position 16192. Neither record quantifies a per-sample false-veto rate, so this packet states no rate. |

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
ancestral allele does not carry the edge's listed derived event, so continuation
through that edge is withheld. Missing data remains non-evidence, and a derived
allele remains insufficient to assign U5 without the rest of the path. This
matches the existing markerless-node contract and prevents an ambiguous
aggregation of conflicting rows from becoming a fail-open path.

C5 is the claim that constrains how far that logic may be pushed, and it was
allowed to change the wording of this packet. The search deliberately looked for
evidence against a strict reading of the guard and found it: Finnilä et al.
(PMID:10712215) resolved a reticulation in a U5 network by assuming a back
mutation at nt 16270, which means an m.16270 ancestral C can occur in a genuine
U5 lineage. Richards et al. (PMID:11032788) independently treat recurrent
control-region mutation as significant enough to require explicit screening
criteria before lineage assignment, over a much larger and unrelated sample set.
Taken together they rule out any claim that ancestral C at m.16270 excludes U5
membership. They do not undermine the guard, because the guard's only effect is
to stop at U instead of publishing a U5 subtype from a contradicted edge — a
loss of resolution, never a positive misassignment. The exclusion-flavoured
wording that an earlier revision of this packet used has been removed.

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
- Because m.16270 can back-mutate inside U5 (C5), a real U5 carrier whose array
  types an ancestral C is reported as U. That is a known, accepted false veto:
  the packet does not estimate how often it occurs, and no source found here
  supplies such a rate. If a future source does, the guard's cost is quantifiable
  and this trade-off should be revisited rather than assumed.
- The guard is not a test for U5 membership and must not be read as one. It
  changes only which label this repository is willing to publish.

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
invoked only with sanitized public queries to discover primary sources and
screen the literature. No provider response payload, generated summary,
citation context, citation tally, classifier result, account data, or
personal/genomic data is retained in this repository. Every claim above rests on
a record retrieved directly from NCBI Entrez or on the release-pinned source
archive; provider output is neither redistributed nor used as scientific
evidence. One Consensus call returned a rate-limit error and was not retried
(recorded in query-log.md). Scite full-text excerpts surfaced a further
corroborating passage in PMID:11032788 that is deliberately **not** cited here,
because that article's PubMed Central deposit carries front matter only and the
publisher copy returned HTTP 403, so the passage could not be re-obtained
through an authoritative route. C5 therefore rests on the two Abstract-level
statements that NCBI does serve. See the
[Consensus terms](https://consensus.app/home/terms-of-service/) and
[Scite terms](https://scite.org/terms).

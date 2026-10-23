# ClinVar probe and source identity (#2391)

Scope: software matching on GRCh37/b37, using synthetic in-memory fixtures only.
No clinical effect, patient genotype, reference allele correction, or vendor-wide
coordinate conversion is asserted. The existing I/D-token carriage policy remains
unscoreable (`zygosity = NULL`); matching a reference record does not prove carriage.

## Sources and claim mapping

- [VCFv4.5 specification](https://samtools.github.io/hts-specs/VCFv4.5.pdf), printing
  `e821e4f`, 2026-02-25, sections 1.6.1 and 5.2 (accessed 2026-10-23).
  Defines POS and the padding convention for simple indels. Position 1 and complex
  variants have exceptions; the specification does not prove that every array
  probe differs from ClinVar by one base. Sanitized selected metadata/excerpt:
  `data/science-evidence/2026-10-23-issue-2391-clinvar-probe-identity/vcf-specification.json`.
  Specification copyright is retained by its authors; no full document is copied.
- Tan, Abecasis and Kang, *Unified representation of genetic variants* (2015),
  [PMID:25701572](https://pubmed.ncbi.nlm.nih.gov/25701572/),
  [DOI:10.1093/bioinformatics/btv112](https://doi.org/10.1093/bioinformatics/btv112),
  PMCID:PMC4481842 (accessed 2026-10-23). Supports treating coordinates and alleles
  together when resolving variant representations. PubMed efetch returned a
  Journal Article with no CommentsCorrections links or retraction publication
  type on this access date. This is a metadata check, not a guarantee against
  future corrections. Sanitized selected response fields:
  `data/science-evidence/2026-10-23-issue-2391-clinvar-probe-identity/pubmed-metadata.json`.
  Copyright Oxford University Press; metadata only, no abstract or full text copied.

These sources share the VCF representation convention and are not independent
biological cohorts or evidence of a clinical effect. They explain the technical
identity constraint. The bounded compatibility policy comes from issue #2391:
only an own-rsID I/D probe and an indel reference record may differ by exactly one
base upstream on the same chromosome. Merged rsIDs and other offsets retain the
strict guard. This is not sequence-based normalization or a general lift-over.

## Implementation and regression evidence

The shared allele-class helper filters ClinVar coordinate candidates before
review-star/carriage selection. Known SNV and indel classes cannot cross. Ordinary
SNV coordinate fallback and the existing handling of unclassified calls remain.
The engine retains the reference record's native full identity for downstream
VEP queries, rather than combining its alleles with the raw probe position.
Records selected earlier at the raw position are reselected for these offset
contexts. gnomAD frequencies are re-queried by that exact native full identity,
with mismatched raw-position frequencies withheld. Own-rsID eligibility is
rechecked after alias fan-out, independently of batching and insertion order.
Existing sample coordinates and the public storage schema are unchanged.

Synthetic regressions exercise the engine and standalone writer, compatible and
incompatible coordinate candidates, a lower-star compatible candidate, ordinary
SNP fallback, chromosome/direction/distance guards, and VEP source-identity joins.
I/D calls explicitly remain unscoreable; the compatible SNP control asserts
`hom_ref`. Tests verify software behavior, not scientific validity of a clinical
classification. Previously stored annotations require an explicit re-annotation.

## Downstream reader audit

- `backend/analysis/cancer.py` and `cardiovascular.py` filter to carried zygosities.
- `backend/analysis/acmg.py` likewise filters candidates to carried zygosities.
- `backend/analysis/carrier_status.py` uses a separate narrowly supported indel
  rescue; ordinary carrier output requires `het`, and affected-status output has
  separate homozygous guards. This change does not broaden that rescue.
- `backend/api/routes/variants.py` serializes stored ClinVar significance separately
  from zygosity, and its distribution endpoint counts stored non-null labels.
  `backend/api/routes/watches.py` reads stored significance without a carriage
  predicate. Correct source matching therefore matters even for unscoreable calls.

No downstream risk-score or carriage rule is changed here.

## Discovery and availability

Queries and fallbacks are recorded in `queries.json`. Consensus discovery and
paper retrieval succeeded. Scite's monthly quota was exhausted, so no Scite
verdict was used. The packaged ClinVar Variation beta endpoint returned HTTP 404
for a public rsID-only request; no failed lookup payload or sample data was used.
The primary VCF specification and the narrow NCBI Entrez metadata workflow were
used directly. No sequencing input, cluster job, restricted payload, or genotype
was submitted to a discovery service. No licenses for source datasets or installed
reference snapshots are changed.

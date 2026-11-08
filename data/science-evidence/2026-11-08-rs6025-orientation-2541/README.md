# GRCh37 rs6025 fixture identity (#2541)

All new sources below were accessed 2026-11-08. These are public test-reference
fixtures. This packet verifies source identity and copied source outputs; it
does not independently validate clinical classifications, biological effects,
treatment recommendations, penetrance or population risk.

## Selection and claim mapping

The three seeds select **GRCh37 `1:169519049 T>C`**. Ensembl's primary mapping
reports `T/C/A` on strand +1, and its sequence endpoint returns T. The retained
independent-server check in
`data/science-evidence/2026-10-20-issue-2344-seed-ref-orientation/assembly_bases.json`
also reports T from UCSC hg19 (accessed 2026-10-20). Both servers read the same
assembly; their agreement is not independent biological evidence.

| Fixture | Selected source and identity |
| --- | --- |
| VEP | The manifest-pinned VEP v4.0.0 / Ensembl 112 snapshot's T>C row: ENST00000367797, `c.1601A>G`, `p.Gln534Arg`, missense, minus strand, exon 10, canonical flag 1. |
| ClinVar | **ClinVar:VCV000226007.28** explicitly maps to `NC_000001.10:g.169519049T>C`. Its Clinical Tables record supplies the significance, review status and conditions. |
| gnomAD | **gnomAD:1-169519049-T-C**, dataset `gnomad_r2_1` (r2.1.1), exome counts only. All eight AF fields and the homozygote count describe C. |

The old seed accession **ClinVar:VCV000000642.133** is a different allele:
ClinVar maps it to `NC_000001.10:g.169519049=` and VCF identity T/T on GRCh37,
but C>T on GRCh38. It cannot annotate the newly selected GRCh37 C allele.
The new accession names `NM_000130.5:c.1601=` / `p.Arg534=` because that RefSeq
transcript differs from the GRCh37 Ensembl transcript used by the pinned VEP
bundle. We retain both source-native names and genomic mappings; we do not
rewrite one transcript's HGVS to look like the other.

ClinVar's selected Clinical Tables label is **Conflicting classifications of
pathogenicity**, with **criteria provided, conflicting classifications**. The
seed copies these through the existing production normalization and star
mapping. This is a faithful database-output fixture, not a resolution of the
conflicting clinical submissions or a benign/pathogenic assertion. The old
drug-response classification, three stars, accession and conditions are not
transferred to C. `clinvar_seed_snapshot.json` records the new accession with
a dated record-level provenance override; other snapshot records are unchanged.

## Annotation and frequency provenance

`raw/pinned-vep-snapshot-derived.json` preserves both available bundle ALTs,
the snapshot provenance and SHA-256
`c8e5f162e1872ecd0ff94408c66ce0874b79dea5f4f645c758d2c45c3b6cc4d3`.
Every mirrored VEP field is copied from its T>C row. The local production bundle
is not resnapshotted. The offline manifest/snapshot guard remains in force.

The software's defining resource paper is McLaren et al. (2016), *The Ensembl
Variant Effect Predictor*, Genome Biology 17:122,
[PMID:27268795](https://pubmed.ncbi.nlm.nih.gov/27268795/) /
[DOI:10.1186/s13059-016-0974-4](https://doi.org/10.1186/s13059-016-0974-4)
(accessed 2026-11-08). It identifies the tool, not this specific allele.
PubMed's retained metadata has no correction relation or retraction publication
type; Scite verified the citation with no reported editorial notice.

gnomAD's exome record has empty filters and positive global and population AN.
The global AF is **246328 / 251250**; homozygotes are **120783**. Each population
AF is its unsuffixed exome AC/AN (`nfe` supplies `af_eur`). ASJ is **9892 / 10078**.
Ratios use Python float round-trip precision. Genome counts are retained for
audit context but never pooled with exomes. No value uses `1 - AF`, a complement,
a frequency from GRCh38, or an inferred zero. The ASJ provenance record explicitly
overrides the file's legacy v4.1 defaults with this r2.1.1 exome source.

NCBI, Ensembl and gnomAD share reference/variant resources, and Clinical Tables
derives from ClinVar. The pinned VEP row and VEP documentation are not independent
annotation opinions. These relationships are recorded rather than treating the
sources as two independent clinical validations. No new clinical effect claim
or analysis policy is introduced.

## Retention, reproduction and regression

`queries.json` records exact requests, builds, identifiers, licences and relative
payload paths. `gnomad-query.graphql` selects the public aggregate fields.
Ensembl and gnomAD payloads are retained as returned by their research-skill
clients. ClinVar XML and PubMed records are explicitly labelled whitelist
reductions: no submissions, observations, participant data, author contacts or
article abstracts are retained. Clinical Tables returns only the selected
aggregate accession metadata. `raw_hashes.json` inventories these artifacts.

Consensus and Scite were consulted first; `discovery.json` records the queries.
Discovery papers were not promoted to allele/frequency evidence. ClinVar's beta
Variation endpoint returned 404, so the Entrez skill retrieved the two VCV
records. Ensembl requests used its GRCh37 endpoint explicitly; the gnomAD query
reuses the inspected #2591 r2.1.1 schema. All network retrieval is outside CI.

The new regression tests fail in nine cases against the old fixtures, with one
source-identity control passing. They pin all three selected alleles, accession
and metadata, mirrored annotation, every AF, homozygote count and production
VEP/ClinVar/gnomAD lookups. The gnomAD lookup must reject the old C>T pair and
the third allele T>A. The general REF guard now covers rs6025 without an
exception and rejects C>T. Only the three #2541 allele exceptions and its VEP
locus exception are removed; #2563 remains tracked.

Regenerate with `python scripts/regenerate_fixtures.py --output-dir <preview>`.
Compare every SQLite table before replacing the three affected mini databases:
exactly one rs6025 row changes in each; schemas, row counts and every other row
remain identical. Preserve the unchanged dbNSFP database bytes. The thrombophilia,
ACMG and allele-match suites remain required dependent checks. Curated analysis
alleles and behavior are unchanged; a test comment now distinguishes transcript
notation from genomic REF/ALT.

# GRCh37 rs1800795 fixture identity (#2563)

All new sources below were accessed 2026-11-08. This packet verifies public
source identities and copied source outputs for test fixtures. It does not
validate clinical associations, biological effects, population risk or treatment.

## Selection and claim mapping

All three seeds select **GRCh37 `7:22766645 C>G`**. Ensembl's primary mapping
reports `C/G/T` on strand +1; its sequence endpoint returns C. ClinVar's
GRCh37 sequence location independently identifies the same tuple. These are
separate database/server checks against a shared assembly, not independent
biological evidence. The seed-wide oracle already covers this rsID, and its
assembly cross-check is retained in
`data/science-evidence/2026-10-22-issue-2583-seed-allele-orientation/assembly_bases.json`
(accessed 2026-10-22).

| Fixture | Selected source and identity |
| --- | --- |
| VEP | Manifest-pinned VEP v4.0.0 / Ensembl 112 C>G row: `AC073072.5`, `ENST00000325042`, `n.54-321G>C`, `intron_variant&non_coding_transcript_variant`, minus strand, intron 1, canonical flag 1. |
| ClinVar | **ClinVar:VCV000014718.25**, explicitly `NC_000007.13:g.22766645C>G` on GRCh37. Clinical Tables supplies its aggregate metadata. |
| gnomAD | **gnomAD:7-22766645-C-G**, dataset `gnomad_r2_1` (r2.1.1), genome counts only. Frequencies and homozygotes describe G. |

The old G>C seed is reversed. Its traditional `IL6`, `c.-174G>C`, and
`5_prime_UTR_variant` VEP annotation does not match the pinned bundle's selected
transcript. Every mirrored field is copied from that bundle's C>G row. ClinVar
continues to associate its record with IL6: the two source-native gene/transcript
contexts are retained, not forced to agree. Neither selection establishes an
IL6 effect or changes curated analysis alleles.

ClinVar's accession and aggregate metadata already belonged to C>G and remain
unchanged: `other; risk factor`, `no assertion criteria provided`, and the
source's phenotype list. Existing production normalization maps the review
status to zero stars. Retaining this database-output label does not independently
endorse the underlying clinical associations. A dated record-level override in
`clinvar_seed_snapshot.json` links the fresh identity and aggregate payloads.

## Annotation and frequency provenance

`raw/pinned-vep-snapshot-derived.json` preserves both available bundle ALTs,
their full mirrored fields, and the snapshot provenance/SHA-256:
`c8e5f162e1872ecd0ff94408c66ce0874b79dea5f4f645c758d2c45c3b6cc4d3`.
The production bundle is not resnapshotted; its manifest/snapshot guard remains
in force. No transcript name or consequence is inferred from a promoter label.

The changed selected gene also requires a fresh gene-strand oracle. The existing
strict generator resolved all 58 real seed genes through the Ensembl skill's
GRCh37 `lookup/symbol` requests, retaining their unedited responses in
`raw/ensembl-gene-strands.json` (accessed 2026-11-08). The resulting snapshot
replaces IL6 with the verified minus-strand AC073072.5 entry; every shared
gene's strand and Ensembl ID is unchanged. The synthetic exemption remains
exactly GENE1, and evidence-reduction and exact-coverage guards remain intact.

The defining software paper is McLaren et al. (2016), *The Ensembl Variant
Effect Predictor*, Genome Biology 17:122,
[PMID:27268795](https://pubmed.ncbi.nlm.nih.gov/27268795/) /
[DOI:10.1186/s13059-016-0974-4](https://doi.org/10.1186/s13059-016-0974-4)
(accessed 2026-11-08). It identifies the tool, not this allele's annotation.
The retained PubMed metadata has no correction relation or retraction type;
Scite verified the citation with no reported editorial notice.

The exact gnomAD r2.1.1 record has **no exome entry**. Its genome record has
empty filters, global **AC 21362 / AN 31278**, and **7882 homozygotes**. Each
available population frequency is its unsuffixed genome AC/AN (`nfe` supplies
`af_eur`); ASJ is **223 / 290**. The `sas` record has AC 0 and AN 0: no frequency
can be computed, so `af_sas` is blank in CSV and NULL in SQLite. The observed
East Asian frequency is 1558/1558, distinct from the absent SAS measurement.

The seven available AFs use Python float round-trip precision. No value uses
`1 - AF`, reverse-complement arithmetic, pooled exome/genome counts, GRCh38
frequencies, or an inferred zero. The ASJ provenance record explicitly overrides
the file's legacy v4.1 defaults with this r2.1.1 genome source, and removes only
rs1800795 from its withheld list.

NCBI, Ensembl and gnomAD share reference/variant resources; Clinical Tables
derives from ClinVar. The pinned VEP output and VEP paper are not independent
annotation opinions. These dependencies are explicit because no independent
clinical-effect validation is claimed.

## Retention, reproduction and regression

`queries.json` records exact requests, identifiers, builds, licences and relative
payload paths. `gnomad-query.graphql` selects public aggregate fields. Ensembl
and gnomAD responses are retained as returned by the skill clients. ClinVar XML
and PubMed are labelled whitelist reductions: no observations, submissions,
participant data, author contacts or abstracts are retained. Clinical Tables
returns only selected aggregate accession fields. `raw_hashes.json` inventories
the artifacts.

Consensus and Scite were consulted first; `discovery.json` records their queries
and disposition. A discovered rs1800795 allele-label erratum is contextual
literature, not the source of genomic identity or gnomAD counts. ClinVar's beta
Variation endpoint returned 404; the Entrez skill supplied the VCV identity.
All network retrieval is outside CI; Ensembl uses its explicit GRCh37 endpoint,
and the gnomAD query reuses the inspected r2.1.1 schema from #2591.

The new regression fails in nine cases against the original fixtures, with
one source-identity control passing. It verifies all three seed alleles,
source-specific metadata, the pinned VEP transcript, every AF including missing
SAS, homozygotes, and production ClinVar/VEP/gnomAD lookups. The gnomAD lookup
must reject the old G>C pair and the other C>T allele. The existing seed-wide
allele guard already reaches every located seed rsID; removing these last three
exceptions leaves its exception list empty. Only this rsID leaves the VEP
bundle-locus exclusion map; genuinely absent bundle rows remain tracked.

Regenerate with `python scripts/regenerate_fixtures.py --output-dir <preview>`.
Compare every SQLite table before replacing the three affected databases:
only one rs1800795 row may change in each; schemas, row counts and every other
row must remain identical. Preserve the unchanged dbNSFP database bytes.

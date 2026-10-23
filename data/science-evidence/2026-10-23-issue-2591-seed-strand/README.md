# GRCh37 allele selection for five seed rows (#2591)

Accessed 2026-10-23. This packet corrects public **test-reference fixtures**;
it makes no clinical, allele-function, penetrance or treatment claim. The mini
databases are test inputs, not a source for interpreting a person's genome.

## Selection and claim mapping

| Rows | Selected genomic identity | Source and selection |
| --- | --- | --- |
| CYP2D6 rs1065852, VEP / ClinVar / gnomAD | `22:42526694 G>A` | ClinVar `VCV000016893.11` records `NC_000022.10:g.42526694G>A`, with coding `c.100C>T` and protein `p.Pro34Ser`. Select the matching G>A row in the pinned VEP snapshot, not its G>C row. |
| ABCB1 rs2032582, VEP / gnomAD | `7:87160618 A>C` | ClinVar `VCV000166622.13` records `NC_000007.13:g.87160618A>C`, coding `c.2677T>G`, protein `p.Ser893Ala`. Select this reference-relative row in the pinned VEP snapshot, not the distinct A>T / p.Ser893Thr row. |

The old ABCB1 row combined transcript-strand G>T / p.Ala893Ser with a genomic
REF/ALT contract. That historical direction is the reverse of the selected
GRCh37 reference-relative allele, not another way to write A>T. This correction
deliberately represents the **A>C non-reference allele** at the same rsID and
updates its annotation to `c.2677T>G`, `p.Ser893Ala`, exon 22. It does not claim
to preserve the old protein-change direction. The decision follows the explicit
ClinVar genomic/coding mapping and the corresponding pinned bundle row; it is
not an ALT inferred from a strand complement or an allele string alone.

ClinVar's identity reductions retain all reported assembly locations and HGVS
expressions for those two records, with current record status and version. They
omit submissions, observations and clinical interpretations. The CYP2D6 seed
keeps its existing ClinVar accession, significance and condition columns; only
the genomic alleles change. No ABCB1 ClinVar seed row is added.

The live Ensembl GRCh37 variation responses independently confirm that these
pairs belong to the **primary-chromosome** mappings: G/A/C on chromosome 22 and
A/C/T on chromosome 7, strand +1. CYP2D6 also has a patch mapping at another
position whose first allele is A; it is retained for audit but is not the join
coordinate. The assembly REF bases were also checked against Ensembl sequence
and UCSC hg19 in the retained #2583 packet:
`data/science-evidence/2026-10-22-issue-2583-seed-allele-orientation/assembly_bases.json`.

Ensembl imports dbSNP variation identity, and NCBI's identity resources share
reference/variant ancestry. These are **not two independent clinical sources**.
The bundle snapshot and live Ensembl output are not independent VEP opinions
either. The claims here are bounded source-output/fixture-identity contracts;
clinical or biological effect claims would require separate independent
evidence and are withheld.

## Annotation and frequency provenance

`raw/pinned-vep-snapshot-derived.json` retains both bundle alleles at each rsID
and the existing snapshot provenance: VEP bundle v4.0.0, Ensembl 112, SHA-256
`c8e5f162e1872ecd0ff94408c66ce0874b79dea5f4f645c758d2c45c3b6cc4d3`.
The source is `tests/fixtures/vep_seed_bundle_snapshot.json`, already bound to
`bundles/manifest.json` by offline tests. The local `bundles/vep_bundle.db` has
a different hash and was not used or resnapshotted. Every annotation field is
copied from the selected snapshot row, including the canonical flag.

This tool's resource paper is McLaren et al. (2016), *The Ensembl Variant Effect
Predictor*, Genome Biology 17:122,
[PMID:27268795](https://pubmed.ncbi.nlm.nih.gov/27268795/) /
[DOI:10.1186/s13059-016-0974-4](https://doi.org/10.1186/s13059-016-0974-4)
(accessed 2026-10-23). It identifies the software, not the specific variants.
The retained PubMed metadata contains no typed correction relation or retraction
publication type. Scite's editorial-notice check was unavailable due quota.

The two exact GRCh37 variants were queried in **gnomAD r2.1.1**, API dataset
`gnomad_r2_1`. All eight AF columns use the **exome** counts, matching the
production gnomAD loader's r2.1.1 exome source. The global AF is exome AC/AN;
the seven population AFs are the corresponding unsuffixed population AC/AN
(`nfe` maps to the fixture's `eur`). Both exome records have empty filters and
positive global and population AN. Homozygote counts come from the same exome
record. Genome counts are retained for context but are never combined with the
exome counts. No complement, `1 - AF`, legacy frequency, or inferred zero is
used. Ratios are stored at Python float round-trip precision.

The ASJ provenance file now explicitly overrides its legacy v4.1 defaults for
these two rows with the exact r2.1.1 exome variant ID, AC/AN, access date and
method. ABCB1 leaves the withheld list. Other records retain their provenance;
this packet does not audit their frequencies.

## Retention, reproduction and validation

`queries.json` records exact requests, builds, source roles and licences;
`gnomad-query.graphql` is the actual frequency selection. Source JSON from
Ensembl and gnomAD is retained as returned by the narrow research-skill
clients. ClinVar and PubMed artifacts are labelled **derived** whitelist
reductions from Entrez XML; no article abstracts, emails, participant data or
restricted data are retained. `raw_hashes.json` inventories the retained source
artifacts and query metadata. Public database accessions and aggregate counts
are not participant genotypes.

Consensus was consulted first. Its discovery metadata is retained without
abstracts; no discovered paper was promoted to allele/frequency evidence.
Scite's 250-call monthly quota was exhausted. NCBI Variation beta endpoints
returned 404, so the Entrez skill fetched the VCV identifiers instead. Numeric
multi-ID retrieval returned an empty set and was rejected. Context7 resolved
Ensembl and ClinVar documentation but not gnomAD; live schema introspection
supplied the exact gnomAD field names. See `discovery.json` for these fallbacks.

The first nine regression cases fail against the original five seed rows.
Offline tests pin selected alleles, the ClinVar identity, every mirrored VEP
field, every frequency and homozygote count, and the production VEP/gnomAD
position lookup paths. gnomAD lookups also reject the old allele pair and the
other ALT at each multiallelic site. No carriage-gated analysis changes.

Regenerate with `python scripts/regenerate_fixtures.py --output-dir <preview>`
and compare SQLite `iterdump()` output before replacing the mini databases.
The inspected deltas are exactly two VEP rows, two gnomAD rows and one ClinVar
row, with no schema, row-count, ordering or unrelated-row change. The dbNSFP
database is semantically identical and its original bytes are preserved.
Five #2591 allele exceptions and the two VEP-locus exceptions are removed.

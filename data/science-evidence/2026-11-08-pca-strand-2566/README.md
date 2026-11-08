# PCA allele orientation on stored GRCh37 calls (#2566)

Public-reference access date: 2026-11-08. Historical payloads retain their original
2026-10-20 access date. No sample, personal genotype, clinical record or restricted input
was sent to a discovery service or reference API.

## Question and result

The PCA training bundle describes ordered GRCh38 alleles. Which orientation maps each
stored GRCh37 plus-strand call onto those training alleles, without changing training
weights or guessing from an A/T or C/G pair?

The shipped `backend/data/panels/ancestry_pca_bundle.grch37.tsv` covers all 5,000 AIMs.
Six map with strand -1: rs11638659, rs75536403, rs28493303, rs11901495, rs28689766
and rs199898426. The other 4,994 map with strand +1. The paired JSON pins the exact
NPZ and TSV hashes. The table's `grch37_ref` and `grch37_alt` are the mapped **training**
reference and alternate alleles, not a promise that the first is the GRCh37 reference
base. A third allele in a public multiallelic record is outside this biallelic model.

This is an assembly-coordinate/allele-identity correction. Synthetic regression tests
verify software dosage, missingness, projections, bootstrap inputs and persistence;
they do not establish ancestry accuracy or clinical validity.

## Claims and sources

| Claim | Evidence and repository-relative payloads |
| --- | --- |
| C1: all 5,000 marker identities, GRCh37 positions and orientations | `data/science-evidence/2026-10-20-issue-2493-fixture-grch37-positions/derived/grch37_positions.json.gz` and its 109 hash-pinned payloads, scripts and source manifest; all hashes reverified. The generator checks ordered marker identity, GRCh38 position, allele pair and agreeing orientation routes for every row. |
| C2: the six reversed markers and palindromic rs75536403 retain training allele identity | C1, plus the seven-locus refresh under `raw/ensembl-refresh/` and `raw/ensembl-refresh-retry/`; exact queries, successes and failures in `refresh-queries.jsonl` and `refresh-retry-queries.jsonl`; audited values in `refresh-audit.json`. |
| C3: rs2270662 has strand +1 even though its quad-allelic strings do not establish orientation | Historical and fresh assembly flanks match with 0 mismatches as-is and 34 when reversed, excluding the centre. `raw/refsnp-rs2270662.json` independently reports both NC_000017.10 (GRCh37.p13, position 35219205) and NC_000017.11 (GRCh38.p14, position 36861944) aligned forward to the GRCh38 PTLP. The schema in `raw/ncbi-var-service.yaml` defines `is_aln_opposite_orientation`. SPDI positions are zero-based; the table is one-based. |
| C4: reference versions are stable across this refresh | Current Ensembl release/assembly metadata under `raw/ensembl-refresh/`: release 116, GRCh37.p13 and GRCh38.p14. RefSNP's last-update dbSNP build is 157; its update time is 2024-11-01T21:14Z (ISO-normalized display; the original unpadded value is preserved in the raw record). |

The historical evidence has 4,972 orientations supported by identity plus assembly map,
27 by flank plus one of those methods, and one previously supported by flank alone.
C3 adds explicit NCBI alignment direction for that last marker. The reproducible
generator requires at least two agreeing orientation observations per marker.

**Independence and limits.** Ensembl Variation imports dbSNP, so they share a variant
identity lineage and are not two independent biological sources. Assembly maps align
reference assemblies and flank comparisons read their sequence; neither depends on
allele membership in the variant record. These are separate methods, but share reference
assemblies and, for Ensembl endpoints, a provider. For rs2270662, NCBI placement-alignment
metadata agrees with the direct flank comparison; the assembly inputs are shared.
There is no cohort or clinical-effect inference here, and this evidence must not be
reused as two independent sources for one. No statistical, clinical or population-effect
claim is added.

## Refresh and unavailable endpoints

There were 39 initial Ensembl requests and 22 bounded retry attempts (three attempts at
most per failed URL): 36 distinct URLs succeeded. All seven GRCh37 identity lookups and
all seven assembly maps agree with the recorded primary-chromosome placements. Patch
placements are retained in raw responses but excluded from the primary-chromosome
comparison, as in the historical derivation. Maps that target a different copy or have
no primary projection cannot establish the rsID placement; the identity/flank route is
retained for those markers.

Three GRCh38 records remained unavailable: variation rs11901495, variation rs28493303,
and the rs11901495 flank. Failed requests provide no evidence. Their original immutable
2026-10-20 payloads remain the source, with hash verification and their original date;
no fresh successful download is asserted. Current paired flanks at the other six loci
agree exactly with the recorded orientation. The refresh metadata still reports release
116 and the same assembly patch versions.

The installed Variation helper's beta RefSNP URL returned 404. The official current
OpenAPI schema lists `https://api.ncbi.nlm.nih.gov/variation/v0` and `/refsnp/{rsid}`.
Importing that same helper with its base URL overridden in memory retrieved rs2270662
successfully; installed skill files were not changed. See `ncbi-queries.json`.

## Discovery, citations, corrections and licenses

Consensus search and selected-paper fetches, followed by Scite screening, preceded the
narrow Ensembl and NCBI Variation skills. Neither discovery tool was quota-blocked.
Only the public queries and handling notes are retained in `discovery.json` and
`source-response-index.json`. Provider responses, generated summaries, abstracts,
citation contexts and classifier results are neither retained in this packet nor used
as evidence. Papers found through both discovery tools are counted once.

The identifiers and correction check were independently refreshed through the NCBI
Entrez skill: `raw/pubmed-primary-metadata.json` records primary bibliographic and
publication-type fields and an empty CommentsCorrections list for each of the three
papers (accessed 2026-11-08). Abstracts, author/affiliation data and the full response XML
were excluded from the retained extract. This is a record of the available PubMed
fields, not a guarantee against a future correction. No clinical-validity claim is
inferred from dbSNP membership.

- PMID:25236461; DOI:10.1093/bioinformatics/btu613 (accessed 2026-11-08):
  Yates et al., [The Ensembl REST API: Ensembl Data for Any Language](https://doi.org/10.1093/bioinformatics/btu613).
- PMID:25352552; DOI:10.1093/nar/gku1010 (accessed 2026-11-08):
  Cunningham et al., [Ensembl 2015](https://doi.org/10.1093/nar/gku1010).
- PMID:11125122; DOI:10.1093/nar/29.1.308 (accessed 2026-11-08):
  Sherry et al., [dbSNP: the NCBI database of genetic variation](https://doi.org/10.1093/nar/29.1.308).
- RefSeq:GCF_000001405.25 and RefSeq:GCF_000001405.40 (accessed 2026-11-08):
  GRCh37.p13 and GRCh38.p14, respectively, from the current RefSNP placements.
  Ensembl metadata gives the corresponding GenBank accessions GCA_000001405.14 and
  GCA_000001405.29. The two accession systems are not competing builds.
- dbSNP:rs2270662 (accessed 2026-11-08):
  [RefSNP record](https://api.ncbi.nlm.nih.gov/variation/v0/refsnp/2270662) and
  [official schema](https://api.ncbi.nlm.nih.gov/variation/v0/var_service.yaml).

Ensembl data-use terms and NCBI distribution terms are preserved in the historical
packet's `raw/terms/data_use_terms.json` (accessed 2026-10-20): no restrictions on access,
use or distribution of those data. The current NCBI service schema explicitly declares
Public Domain and links its policies. Primary PubMed metadata is retained without
abstracts or full text. The discovery-service handling follows the recorded
[Consensus terms](https://consensus.app/home/terms-of-service/) and
[Scite terms](https://scite.ai/terms) (accessed 2026-11-08); no redistribution license
for their response payloads is asserted. No UCSC chain is used.

## Reproduction and reanalysis

From the repository root, with the development dependencies installed:

```sh
python data/science-evidence/2026-11-08-pca-strand-2566/scripts/build_mapping.py --check
python data/science-evidence/2026-11-08-pca-strand-2566/scripts/audit_refresh.py
python -m pytest tests/backend/test_ancestry_strand_harmonization.py -q
```

`build_mapping.py --write` regenerates only the two mapping sidecars. It never reads
sample genotypes or the synthetic fixture oracle; tests compare against that separate
oracle. The NPZ stays byte-identical. `manifest.json` records hashes for this packet.
Both audit scripts explicitly reject `-O`/`-OO` so disabled assertions cannot produce
a false validation pass. The rs2270662 alignment vote is computed from the two retained
NCBI placement-orientation flags.

Existing stored ancestry estimates are not automatically recalculated at update time.
Rerun global ancestry to replace the PCA/NNLS/kNN findings and intervals; stored sample
calls and unrelated findings remain unchanged. New findings identify mapping version
`grch37-pca-v1` in `inference_provenance`; absence marks a legacy result. Recompute any
saved downstream analysis that used a changed ancestry estimate. GRCh38 LAI/Gnomix
pipelines and haplogroup matching are outside this correction.

# GRCh37 positions and strand for the synthetic EUR fixture pair (#2493)

Accessed 2026-10-20.

**Question.** `tests/fixtures/synthetic_eur_ancestrydna.txt` presents as an AncestryDNA V2.0 file, which the parser reads as GRCh37. Its template, `tests/fixtures/synthetic_eur_23andme.txt`, names no build. Which assembly do the two files' coordinates and calls belong to? And what is each row's GRCh37 position and strand?

**Where the coordinates came from (C1).** All 5,000 template rows equal the PCA bundle's `aim_positions_grch38` (`backend/data/panels/ancestry_pca_bundle.npz`), keyed by its `aim_rsids_23andme`. Against Ensembl GRCh38 Variation (release 116, GRCh38.p14):
- every rsID with one primary-assembly mapping (4,795) sits at it;
- 190 rsIDs have no GRCh38 record under that ID.

A seeded sample of 100 rsIDs in dbSNP gives the same picture: 97 sit at their GRCh38 position, and 3 have identical coordinates in both builds. Every call draws on the AIM's GRCh38 allele pair (5,000/5,000), so the calls are on the GRCh38 strand. The template is wholly GRCh38; the 50 rows that looked GRCh37 sit where the two builds coincide. The AncestryDNA file is the generator's byte-for-byte output from the template, so it inherited both.

**Routes.**
- **Identity:** the rsID's own GRCh37 placement and alleles, from Ensembl GRCh37 Variation (release 116, GRCh37.p13). For the five rsIDs Ensembl does not carry, it comes from dbSNP's GRCh37 placement (`chrpos_prev_assm`).
- **Projection:** the row's GRCh38 coordinate projected to GRCh37 by Ensembl's assembly map (`/map/human/GRCh38/{region}/GRCh37`), with the strand of that projection. This route never consults a variant record.
- **Flank:** ±25 bp of assembly sequence (Ensembl `sequence/region`) at the GRCh37 position, compared with the same window at the GRCh38 coordinate, as-is and reverse-complemented, with the centre base excluded. This reads only the two assemblies. It was fetched for the 28 rows where identity and projection did not both decide position and strand.

**GRCh37 positions (C2–C4).**

| Rows | Position rests on | Basis in the oracle |
| ---: | --- | --- |
| 4,974 | Ensembl identity = projection | `variation+map` |
| 5 | dbSNP identity = projection | `dbsnp+map` |
| 15 | Vendor `i` probe: projection, and an exact flank match (C3) | `map+flank` |
| 6 | Region restructured between builds: identity, and an exact flank match (C4) | `variation+flank` |
| **5,000** | | |

- **C3.** All 15 `i` probes match their flank with 0 mismatches in one orientation and 30 or more in the other. As a further control, 13 of the 15 land on a GRCh37 VEP bundle variant whose alleles include the AIM's.
- **C4.** For `rs11638659`, `rs79228236`, `rs2270662` and `rs199898426`, the assembly map has no single-base projection. For `rs75536403` and `rs137955391`, it aligns the GRCh38 base to a different GRCh37 copy than the rsID's own placement. These sit in 15q11, 17q12, 10q11, the chromosome 9 pericentromere and 7q11.23.
  - Ensembl and dbSNP agree on each rsID's placement in both builds.
  - The flank at the identity position matches the GRCh38 flank exactly, so the identity position is the same sequence.
  - The fixture is labelled by rsID, so it takes that placement.

**Strand (C6).** Six loci are reversed between builds: `rs28493303`, `rs11901495`, `rs28689766`, `rs11638659`, `rs75536403` and `rs199898426`. A GRCh37 vendor file reports calls on the GRCh37 plus strand, so these six calls are complemented and the other 4,994 are unchanged.
- **Identity test:** the rsID's Ensembl allele strings in the two builds. GRCh37 equal to GRCh38 means the same orientation; equal to its complement means reversed. Order is compared first, then sets.
- **Projection test:** the projection's strand.
- **Agreement:** 4,972 rows are decided by identity and projection together.
- **Flank checks:** all 27 rows decided by only one of those routes agree with the flank. `rs2270662` is quad-allelic in both builds with no single-base projection, so the flank decides it alone (0 mismatches as-is, 34 reversed).
- **Centre-base control:** for all 28 flank-checked rows, the GRCh37 centre base belongs to the AIM's allele pair on the GRCh37 strand.

**Result.** 4,949 rows move, none changes chromosome, and 6 calls are complemented. The positions, strand and plus-strand allele pair are written to `tests/fixtures/synthetic_eur_aim_coordinates.tsv`, and positions and calls to the template. The AncestryDNA file was then regenerated with `scripts/regenerate_fixtures.py --vendor=ancestrydna`.

**Controls.**
- **C5.** Ensembl GRCh37 Variation agrees with the v4.0.0 VEP bundle (Ensembl 112) for all 4,980 rsIDs both carry. They share a lineage.
- dbSNP's GRCh37 placement agrees with Ensembl's for the 100-rsID sample (seed 2493, `raw/sample_rsids.json`).

**Independence.**
- **Not independent:** Ensembl Variation imports its rsID placements from dbSNP, so Ensembl, dbSNP and the VEP bundle are one identity lineage.
- **Independent in method:** the assembly-map projection is an alignment between assemblies, and the flank comparison reads assembly sequence. Neither reads a variant record, so each is independent of the identity route in method. All three are served by Ensembl.

**Assemblies.** These are cited by registry accession, verified through NCBI Assembly E-utilities (`raw/citation/`):
- GRCh37.p13 is `GCF_000001405.25`;
- GRCh38.p14 is `GCF_000001405.40`.

Both were submitted by the Genome Reference Consortium.

**Citations:**
- `PMID:25236461 / DOI:10.1093/bioinformatics/btu613 (accessed 2026-10-20)`: Yates et al., *The Ensembl REST API: Ensembl Data for Any Language*, Bioinformatics 2015. It covers the variation, assembly-map and sequence endpoints used here.
- `PMID:25352552 / DOI:10.1093/nar/gku1010 (accessed 2026-10-20)`: Cunningham et al., *Ensembl 2015*, Nucleic Acids Res 2015. It describes the dedicated GRCh37 site and the REST server.
- `PMID:11125122 / DOI:10.1093/nar/29.1.308 (accessed 2026-10-20)`: Sherry et al., *dbSNP: the NCBI database of genetic variation*, Nucleic Acids Res 2001.

PubMed `efetch` lists no CommentsCorrections entries (corrections, errata or retractions) for any of them. The title search for *Ensembl 2015* also returned `PMID:25300481` (SMART 2015), which is unrelated and not cited.

**Discovery ladder.**
- Consensus surfaced *Ensembl 2015*, *Ensembl 2016* and *ensembldb*. *Ensembl 2015* was taken forward.
- Scite was unavailable: its monthly MCP quota is exhausted until 2026-11-03.
- PubMed and NCBI Assembly E-utilities verified the citations and accessions.

See `raw/citation/discovery_ladder.json`.

**Licenses** (sentences fetched 2026-10-20, `raw/terms/data_use_terms.json`):
- Ensembl: "Ensembl imposes no restrictions on access to, or use of, the data provided…".
- NCBI: "NCBI itself places no restrictions on the use or distribution of the data contained therein."

UCSC's hg38ToHg19 liftOver chain was downloaded as a candidate route and then not used. Its README restricts the chain to non-commercial use by independent researchers and nonprofits and conditions redistribution, so nothing from it is retained or relied on.

**Scripts** (`scripts/`, as run, in this order):
1. `fetch_grch37_variation_and_dbsnp_sample.py`
2. `fetch_dbsnp.py`
3. `fetch_grch38_variation.py`
4. `fetch_assembly_map.py`
5. `fetch_citations.py`
6. `derive_grch37_positions.py`
7. `decide_orientation.py`
8. `fetch_flanks.py`
9. `fold_flanks.py`
10. `write_oracle_and_template.py`

Notes on the run:
- **Discarded downloads.** `fetch_grch38_variation.py` also downloaded the UCSC chain and an early 20-row assembly-map sample. Both were discarded: the sample's directory was cleared before `fetch_assembly_map.py` made the full run that is retained.
- **Transient failures.** The fetchers retried transient 5xx responses and logged them: six in `raw/ensembl_map/transient_failures.json` and two in `raw/ensembl_flanks/transient_failures.json`.
- **Compressed files.** `derived/grch37_positions.json.gz` holds every per-row record. `derive_grch37_positions.py` wrote it uncompressed, and it was then gzip-compressed (mtime 0). `decide_orientation.py` and `fold_flanks.py` update the compressed file in place. `raw/dbsnp/esummary_sample_100.json` was also written uncompressed and is stored as `.json.gz`.

**Privacy.** Only public rsIDs, vendor probe IDs and reference coordinates were sent. No sample, genotype or patient-derived data was sent. The synthetic genotypes stay local.

See `queries.json` for sources, versions, the claim mapping and payload hashes.

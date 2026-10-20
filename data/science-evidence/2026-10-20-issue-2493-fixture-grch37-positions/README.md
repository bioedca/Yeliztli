# GRCh37 positions for the synthetic EUR fixture pair (#2493)

Accessed 2026-10-20.

**Question.** `tests/fixtures/synthetic_eur_ancestrydna.txt` presents as an AncestryDNA V2.0 file, which the parser reads as GRCh37. Its template, `tests/fixtures/synthetic_eur_23andme.txt`, names no build. Which assembly do the two files' coordinates belong to, and what is each row's GRCh37 position?

**Where the coordinates came from (C1).** All 5,000 template rows equal the PCA bundle's `aim_positions_grch38` (`backend/data/panels/ancestry_pca_bundle.npz`), keyed by its `aim_rsids_23andme`. Against Ensembl GRCh38 Variation (release 116, GRCh38.p14):
- every rsID with one primary-assembly mapping (4,795) sits at it;
- 190 rsIDs have no GRCh38 record under that ID.

A seeded sample of 100 rsIDs in dbSNP gives the same picture: 97 sit at their GRCh38 position, and 3 have identical coordinates in both builds. The template is wholly GRCh38; the 50 rows that looked GRCh37 sit where the two builds coincide. The AncestryDNA file is the generator's byte-for-byte output from the template, so it inherited them.

**GRCh37 positions.** Two routes were taken for every row:
- **Identity:** the rsID's own GRCh37 placement, from Ensembl GRCh37 Variation (release 116, GRCh37.p13). For the five rsIDs Ensembl does not carry, it comes from dbSNP's GRCh37 placement (`chrpos_prev_assm`).
- **Projection:** the row's GRCh38 coordinate projected to GRCh37 by Ensembl's assembly map (`/map/human/GRCh38/{region}/GRCh37`). This route never consults a variant record.

| Outcome | Rows | Basis in the oracle |
| --- | ---: | --- |
| Both routes agree (Ensembl identity) | 4,974 | `variation+map` |
| Both routes agree (dbSNP identity) | 5 | `dbsnp+map` |
| Vendor `i` probe, no public identity record: projection only (C3) | 15 | `map` |
| Region restructured between builds: identity only (C4) | 6 | `variation` |
| **Total** | **5,000** | |

- **C3.** For 13 of the 15 `i` probes, the GRCh37 VEP bundle has a variant at the projected position, and its alleles include the AIM's two alleles. That is a consistency control, not a second source. `i707242` and `i709135` land where the bundle lists no variant.
- **C4.** For `rs11638659`, `rs79228236`, `rs2270662` and `rs199898426`, the assembly map has no single-base projection. For `rs75536403` and `rs137955391`, it aligns the GRCh38 base to a different GRCh37 copy than the rsID's own placement. These sit in 15q11, 17q12, 10q11, the chromosome 9 pericentromere and 7q11.23.
  - Ensembl GRCh38 and dbSNP GRCh38 both place each rsID at the fixture's GRCh38 coordinate.
  - Ensembl GRCh37 and dbSNP GRCh37 both place it at the identity position.
  - The fixture is labelled by rsID, so a GRCh37 vendor file would carry the rsID's own placement. These six rows rest on that single lineage, which is recorded here rather than presented as two sources.

**Result.** 4,949 rows move and none changes chromosome. The positions are written to `tests/fixtures/synthetic_eur_aim_coordinates.tsv` (id, chromosome, GRCh38 position, GRCh37 position, basis) and to the template. The AncestryDNA file was then regenerated with `scripts/regenerate_fixtures.py --vendor=ancestrydna`.

**Controls.**
- **C5.** Ensembl GRCh37 Variation agrees with the v4.0.0 VEP bundle (Ensembl 112) for all 4,980 rsIDs both carry. They share a lineage.
- dbSNP's GRCh37 placement agrees with Ensembl's for the 100-rsID sample (seed 2493, `raw/sample_rsids.json`).

**Independence.**
- **Not independent:** Ensembl Variation imports its rsID placements from dbSNP, so the identity sources (Ensembl, dbSNP and the VEP bundle) count once.
- **Independent in method:** the assembly-map projection is an assembly-to-assembly alignment that reads no variant record, so it is independent of the identity route in method. It is not independent in institution, since both identity-route Ensembl data and the projection are served by Ensembl.

The claim is a coordinate correspondence, not a biological effect. For the 21 single-route rows, the limit is stated above rather than implied away.

**Assemblies.** These are cited by registry accession, verified through NCBI Assembly E-utilities (`raw/citation/`):
- GRCh37.p13 is `GCF_000001405.25`;
- GRCh38.p14 is `GCF_000001405.40`.

Both were submitted by the Genome Reference Consortium.

**Citations:**
- `PMID:25236461 / DOI:10.1093/bioinformatics/btu613 (accessed 2026-10-20)`: Yates et al., *The Ensembl REST API: Ensembl Data for Any Language*, Bioinformatics 2015. It covers the variation and assembly-map endpoints used here.
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

UCSC's hg38ToHg19 liftOver chain was downloaded as a candidate second route and then not used. Its README restricts the chain to non-commercial use by independent researchers and nonprofits and conditions redistribution, so nothing from it is retained or relied on.

**Scripts** (`scripts/`, as run). `derived/grch37_positions.json` and `raw/dbsnp/esummary_sample_100.json` were written uncompressed by these scripts and are stored gzip-compressed (mtime 0) to keep the packet small:
- `fetch_grch38_variation.py` also made that chain download and an early 20-row assembly-map sample. Both were discarded: the sample's directory was cleared before `fetch_assembly_map.py` made the full run that is retained.
- `fetch_assembly_map.py` retried six transient 5xx responses, logged in `raw/ensembl_map/transient_failures.json`.

**Privacy.** Only public rsIDs, vendor probe IDs and reference coordinates were sent. No sample, genotype or patient-derived data was sent. The synthetic genotypes stay local.

See `queries.json` for sources, versions, the claim mapping and payload hashes.

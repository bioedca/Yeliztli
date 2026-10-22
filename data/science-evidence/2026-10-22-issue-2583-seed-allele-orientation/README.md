# Seed allele orientation against the GRCh37 assembly (#2583)

Accessed 2026-10-22. Public reference sequence only; no genotype or sample data was sent.

**Question.** The seed allele guard (#2344) pinned REF only for the 38 single-base rsIDs in the panel oracle. #2583 extends the guard to every coordinate-seed row (`vep_seed`, `clinvar_seed`, `gnomad_seed`) at its GRCh37 locus. For that, REF must be pinned to the GRCh37 assembly base at each of those positions, and the committed seed-wide oracle (`tests/fixtures/seed_rsid_grch37_coordinates.json`, #2375) gives only an Ensembl `allele_string`. Is that string's first allele the assembly base at every position the guard pins?

**What was checked.** For all 644 located positions whose allele string is single-base (`assembly_bases.json`):
- the base from Ensembl GRCh37 REST `sequence/region` (`raw/ensembl_grch37_sequence_region.json`);
- a second source:
  - for 1-22, X and Y, the UCSC hg19 API (`raw/ucsc_hg19_sequence.json`);
  - for MT, NCBI RefSeq `NC_012920.1` (`raw/ncbi_NC_012920.1.fasta`). The NCBI GRCh37.p13 assembly report (`raw/GCF_000001405.25_GRCh37.p13_assembly_report.txt`) maps GRCh37 `MT` to that record.

UCSC hg19 `chrM` is **not** used. Fetched at the same 7 positions (`raw/ucsc_hg19_chrM_not_used.json`), it differs from `NC_012920.1` at MT:3243 and MT:4160.

**Results.**
- The two sources agree at all 644 positions.
- The oracle's first allele equals that base at all 644. So the guard can pin REF to the oracle's first allele with no network source at test time, as #2344 did for its 38.

**Rows that break the contract today:** 491, listed in `tests/fixtures/seed_allele_known_exceptions.json`:
- 304 carry an allele outside the rsID's allele string;
- 182 hold both alleles but not reference-first;
- 5 are SNVs written for an indel rsID.

`rs6025` is tracked by #2541; all the others by #2583's correction follow-up. The guard requires the list to match exactly, so it can only shrink.

**Generator scripts.** `scripts/fetch_assembly_bases.py` wrote `assembly_bases.json` and the raw payloads (hashes in `raw_hashes.json`). `scripts/write_seed_allele_exceptions.py` wrote the exception list from the guard's own classifier.

**Citations** for the databases the bases were read from, as verified in #2344's packet:
- `PMID:25236461 / DOI:10.1093/bioinformatics/btu613 (accessed 2026-10-20)`: Yates et al., *The Ensembl REST API*;
- `PMID:41251146 / DOI:10.1093/nar/gkaf1250 (accessed 2026-10-20)`: Casper et al., *The UCSC Genome Browser database: 2026 update*.

NCBI RefSeq `NC_012920.1` and the GRCh37.p13 assembly report (`GCF_000001405.25`) are cited by accession (accessed 2026-10-22).

See `queries.json` for endpoints, licenses, the claim mapping and the discovery ladder.

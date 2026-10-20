# Seed REF orientation against the GRCh37 assembly (#2344)

Accessed 2026-10-20.

**Question.** The seed allele guard in `tests/backend/test_regenerate_fixtures.py` checked only that a row's `{ref, alt}` were members of the Ensembl allele set, so a reversed REF/ALT row passed. Generalizing the REF pin needs each covered row's GRCh37 reference base.

**What was checked.** For each of the 38 single-base rsIDs carried by the three coordinate seeds (`vep_seed.csv`, `clinvar_seed.csv`, `gnomad_seed.csv`) and covered by the coordinate oracle:
- the GRCh37 assembly base at the oracle position, from Ensembl GRCh37 REST `sequence/region` and from the UCSC hg19 API (`raw/<rsid>.ensembl_grch37_sequence.json`, `raw/<rsid>.ucsc_hg19_sequence.json`);
- the oracle's reference allele, the first element of its Ensembl GRCh37 `allele_string`;
- the REF each seed row declares.

**Results** (`assembly_bases.json`):
- Ensembl GRCh37 and UCSC hg19 give the same base for all 38 positions.
- The oracle's first allele equals that base for every panel rsID, so the guard can pin REF from the committed oracle with no new network source.
- Every seed row declares that base as REF except `rs6025`, which all three seeds carry as `C>T` at `1:169519049`, where the assembly base is `T`. That is filed as #2541 and held as a tracked, self-expiring exception (`KNOWN_REVERSED_SEED_REF`).

**Rows disagreeing with the assembly:** rs6025.

**Assembly.** GRCh37 is NCBI Assembly `GCF_000001405.13` (Genome Reference Consortium), verified through E-utilities and accessed 2026-10-20 (`raw/citation/`). The assembly is the defining source for which base it carries, so it is cited by that accession.

**Citations** for the two databases the bases were read from:
- `PMID:25236461 / DOI:10.1093/bioinformatics/btu613 (accessed 2026-10-20)`: Yates et al., *The Ensembl REST API: Ensembl Data for Any Language*, Bioinformatics 2015.
- `PMID:41251146 / DOI:10.1093/nar/gkaf1250 (accessed 2026-10-20)`: Casper et al., *The UCSC Genome Browser database: 2026 update*, Nucleic Acids Res 2026.

PubMed lists no correction or retraction for either. No GRCh37 assembly publication could be verified, so none is cited.

**Discovery ladder.** Consensus surfaced no defining assembly publication. Scite was unavailable: its monthly quota is exhausted until 2026-11-03. PubMed and NCBI Assembly E-utilities verified the citations and the accession.

See `queries.json` for sources, licenses, the claim mapping and payload hashes.

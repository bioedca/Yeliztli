# GRCh37 coordinates for the coordinate-bearing seed CSVs (#2375)

Accessed 2026-10-21. **Status: in progress.** This packet records the evidence for #2375. It is extended as the fix lands, and the PR is finalized only after the corroboration below is complete.

**Question.** Do the `chrom`/`pos` values in `tests/fixtures/seed_csvs/{vep,clinvar,gnomad,gwas}_seed.csv` match each rsID's GRCh37 primary-assembly mapping, as `tests/fixtures/seed_csvs/README.md` requires?

**Identity route (C1).** `POST https://grch37.rest.ensembl.org/variation/homo_sapiens`, with the 678 unique dbSNP-form rsIDs across the four seeds in batches of 200. Responses are stored verbatim (gzip) under `raw/ensembl_grch37/`, and the request list is `raw/ensembl_grch37/request_rsids.json`. Only primary chromosomes (1–22, X, Y, MT) are compared. Patch and alternate contigs are recorded separately per row.

**Result on `main` @ `1c5f7f5f`** (`derived/seed_row_comparison.json.gz`, from `scripts/compare_seed_rows.py`):

| Seed | Rows at the GRCh37 locus | Same chromosome, other position | Other chromosome | No GRCh37 record |
| --- | ---: | ---: | ---: | ---: |
| `vep_seed` | 602 | 54 | 21 | 2 (`rs5778923`, `rs9999007`) |
| `clinvar_seed` | 39 | 32 | 0 | 0 |
| `gnomad_seed` | 49 | 38 | 10 | 0 |
| `gwas_seed` | 65 rows carry no locus, so there is nothing to compare | | | |

These counts include `rs12345`, which is #2345's synthetic GENE1 row and is out of scope here. Excluding it, 75 rsIDs are affected. Every mismatched rsID has exactly one primary-chromosome mapping. None has more than one, and none has only patch-contig mappings.

**Independence (planned).** Ensembl Variation imports rsID placements from dbSNP, and the production VEP bundle is built from Ensembl, so those three are one identity lineage. Corroboration will therefore read assembly sequence: the GRCh37 reference base at each corrected position must belong to the rsID's allele string. dbSNP's own GRCh37 placement is recorded only as a lineage control.

**License.** Ensembl: "Ensembl imposes no restrictions on access to, or use of, the data provided…".

**Scripts** (`scripts/`, as run):
1. `fetch_grch37_variation.py`
2. `compare_seed_rows.py`

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

**Independence.** Ensembl Variation imports rsID placements from dbSNP, and the production VEP bundle is built from Ensembl, so those three are one identity lineage. The corroborating route therefore reads assembly sequence.

**Assembly-sequence route (C2).** `POST https://grch37.rest.ensembl.org/sequence/region/human` was run over the 73 mapped loci of the affected rsIDs that have a GRCh37 record (`raw/ensembl_sequence/`). This reads no variant record. For each locus, the assembly sequence must equal the reference (first) allele of Ensembl's allele string (`derived/reference_base_check.json`, from `scripts/corroborate_reference_bases.py`):
- **71 match.**
- **2 are insertions** (`rs199474672` `MT:5538` `-/T`, `rs794728091` `18:29101148` `-/T`). They have no reference base to compare, so for now they rest on identity alone.
- **No locus mismatches.**

**Seed alleles at the corrected locus (C3, working note).** Moving the coordinate is not sufficient for every row:
- most rows' `ref`/`alt` fit the GRCh37 alleles as-is, or complemented;
- about 20 rows hold both alleles with ref and alt reversed;
- about 23 rows cannot fit, because the rsID is a different variant type at GRCh37 (for example, `rs5030655` is `A/-`, but the seed holds a T>C SNV) or the row's annotation describes another variant (`rs121913529`, labelled PAH, maps to `12:25398284`).

These rows need their alleles, and in the second case their annotation, re-derived from the rsID's GRCh37 record and the pinned production bundle. That decision is recorded on the PR.

**License.** Ensembl: "Ensembl imposes no restrictions on access to, or use of, the data provided…".

**Scripts** (`scripts/`, as run):
1. `fetch_grch37_variation.py`
2. `compare_seed_rows.py`
3. `fetch_reference_bases.py`
4. `corroborate_reference_bases.py`

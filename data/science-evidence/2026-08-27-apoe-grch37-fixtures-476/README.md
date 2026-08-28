# APOE GRCh37 fixture coordinates for issue #476

## Decision

The three coordinate-bearing seed CSVs, the generated mini databases, and the
hand-maintained `mini_clinvar.vcf` now place the two APOE ε-defining SNPs at
their GRCh37 primary-chromosome mappings. Both rsIDs were added to
`ADDITIONAL_VERIFIED_GRCH37_MAPPINGS` so the existing seed/oracle guard covers
them permanently, and `mini_clinvar.vcf` was brought under the same oracle.

`backend/analysis/apoe.py` matches these SNPs by rsID and holds no coordinate
constants (#490 corrected them, #798 removed them as inert), so no production
call changed. What changed is that the fixtures now agree with the build they
declare and with the production `bundles/vep_bundle.db`.

## Coordinates

| rsID | GRCh37 (primary, plus strand) | GRCh38 | Fixture value before this change |
| --- | --- | --- | --- |
| rs429358 | `19:45411941`, alleles `T/C` | `19:44908684` | `19:44908684` (GRCh38) |
| rs7412 | `19:45412079`, alleles `C/T` | `19:44908822` | `19:44908822` (GRCh38) |

## Claim mapping

| ID | Claim | Evidence | Repository consequence |
| --- | --- | --- | --- |
| C1 | rs429358 maps to GRCh37 `19:45411941` on the primary chromosome, plus strand, alleles `T/C`. | Ensembl GRCh37 Variation REST (`raw/ensembl-grch37-rs429358.json`, accessed 2026-08-27) reports a single `chromosome` mapping at `19:45411941`, strand `1`, allele string `T/C`. NCBI dbSNP eSummary (`raw/ncbi-dbsnp-esummary-429358-7412.json`, accessed 2026-08-27) independently reports `chrpos_prev_assm` `19:45411941`. | `clinvar_seed.csv`, `vep_seed.csv`, `gnomad_seed.csv`, the three generated mini databases, and `mini_clinvar.vcf` carry `19:45411941`. |
| C2 | rs7412 maps to GRCh37 `19:45412079` on the primary chromosome, plus strand, alleles `C/T`. | Ensembl GRCh37 Variation REST (`raw/ensembl-grch37-rs7412.json`) reports `19:45412079`, strand `1`, allele string `C/T`. NCBI dbSNP eSummary reports `chrpos_prev_assm` `19:45412079`. | Same files carry `19:45412079`. |
| C3 | `19:44908684` and `19:44908822` are the **GRCh38** positions of the same two rsIDs, not GRCh37. | NCBI dbSNP eSummary reports `chrpos` (GRCh38) `19:44908684` and `19:44908822` for the same records that report the GRCh37 pair above. | The values the fixtures previously carried are identified as a build mislabel, not an alternate GRCh37 mapping. |
| C4 | The repository's own GRCh37→GRCh38 chain reproduces the same pair. | `backend.ingestion.liftover.convert_coordinate("19", 45411941)` returns `("19", 44908684)` and `("19", 45412079)` returns `("19", 44908822)`. | `tests/backend/test_liftover.py` now asserts a record-verified GRCh37→GRCh38 pair instead of `44404524` / `44404662`, which are the positions of no APOE variant and were produced by lifting an already-lifted coordinate. |

## Side finding (not fixed here; filed separately)

While confirming that the APOE block in the build-37-declaring **vendor sample**
fixtures is coordinate-coherent, the three neighbouring APOE-region SNPs were
checked as well. They are outside this issue's scope and were left unchanged.

| rsID | Vendor-fixture position | GRCh38 (record) | GRCh37 (record) |
| --- | --- | --- | --- |
| rs440446 | `19:44905579` | `19:44905910` | `19:45409167` |
| rs405509 | `19:44905791` | `19:44905579` | `19:45408836` |
| rs769449 | `19:44906745` | `19:44906745` | `19:45410002` |

`rs440446` therefore carries `rs405509`'s GRCh38 position, and `rs405509`
carries a position that belongs to neither. This is a distinct defect class —
rsID↔coordinate mispairing rather than a build mislabel — and is reported in
its own issue.

## Retained evidence

`raw/` holds six unedited public reference-database responses; five Ensembl
GRCh37 Variation REST records and one NCBI dbSNP eSummary record.
`source-manifest.json` records each exact request URL, source, SHA-256, and
byte length. No sanitization was required: every payload is a public record for
a named dbSNP identifier and contains no genotype, sample, personal, or
credentialed data.

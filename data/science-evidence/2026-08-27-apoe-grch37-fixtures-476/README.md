# APOE GRCh37 fixture coordinates for issue #476

## Citations

- dbSNP — `PMID:11125122` / `DOI:10.1093/nar/29.1.308`, "dbSNP: the NCBI database of genetic variation." (accessed 2026-08-27)
- UCSC Genome Browser — `PMID:12045153` / `DOI:10.1101/gr.229102`, "The human genome browser at UCSC." (accessed 2026-08-27)
- Ensembl — `PMID:39656687` / `DOI:10.1093/nar/gkae1071`, "Ensembl 2025." (accessed 2026-08-27)

Each record was resolved by PMID through NCBI PubMed eSummary, its title
compared against the citation above, and its `pubtype` checked for a retraction
or correction flag; none carries one. The eSummary responses are retained under
`raw/`.

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

## Source independence

**Ensembl and dbSNP are not independent for this claim.** Ensembl Variation
imports rsID-to-coordinate mappings from dbSNP, so the two records share an
upstream assertion and their agreement does not by itself satisfy the
two-agreeing-source rule for a high-stakes claim. They are recorded here as one
corroborated variant-database assertion, not two.

The independent second source is the **GRCh37/hg19 assembly sequence itself**,
read directly from UCSC. A reference base is an assembly-level observation
rather than a variant-database annotation, so it does not inherit dbSNP's
assertion, and it discriminates: it distinguishes the GRCh37 position from the
GRCh38 one instead of merely agreeing with a label.

| hg19 position | Reference base | Consequence |
| --- | --- | --- |
| `chr19:45411941` | `T` | Matches rs429358's `T/C` allele string — the variant can sit here. |
| `chr19:45412079` | `C` | Matches rs7412's `C/T` allele string — the variant can sit here. |
| `chr19:44908684` | `G` | Neither `T` nor `C`: rs429358 **cannot** be at this GRCh37 position. |
| `chr19:44908822` | `A` | Neither `C` nor `T`: rs7412 **cannot** be at this GRCh37 position. |

The last two rows are the negative control. They are why this is a
discriminating check rather than a restatement: the values the fixtures
previously carried are excluded by the assembly, not merely unsupported by it.

## Claim mapping

| ID | Claim | Evidence | Repository consequence |
| --- | --- | --- | --- |
| C1 | rs429358 maps to GRCh37 `19:45411941` on the primary chromosome, plus strand, alleles `T/C`. | Variant-database assertion: Ensembl GRCh37 Variation REST (`raw/ensembl-grch37-rs429358.json`) reports a single `chromosome` mapping at `19:45411941`, strand `1`, allele string `T/C`; NCBI dbSNP eSummary (`raw/ncbi-dbsnp-esummary-429358-7412.json`) reports `chrpos_prev_assm` `19:45411941`. Independent assembly evidence: hg19 `chr19:45411941` is `T` (C5). | `clinvar_seed.csv`, `vep_seed.csv`, `gnomad_seed.csv`, the three generated mini databases, and `mini_clinvar.vcf` carry `19:45411941`. |
| C2 | rs7412 maps to GRCh37 `19:45412079` on the primary chromosome, plus strand, alleles `C/T`. | Same two record types: Ensembl reports `19:45412079`, strand `1`, `C/T`; dbSNP reports `chrpos_prev_assm` `19:45412079`. Independent assembly evidence: hg19 `chr19:45412079` is `C` (C5). | Same files carry `19:45412079`. |
| C3 | `19:44908684` and `19:44908822` are the **GRCh38** positions of the same two rsIDs, not GRCh37. | NCBI dbSNP eSummary reports `chrpos` (GRCh38) `19:44908684` and `19:44908822` for the same records that report the GRCh37 pair above. | The values the fixtures previously carried are identified as a build mislabel, not an alternate GRCh37 mapping. |
| C4 | The repository's own GRCh37→GRCh38 chain reproduces the same pair. | `backend.ingestion.liftover.convert_coordinate("19", 45411941)` returns `("19", 44908684)` and `("19", 45412079)` returns `("19", 44908822)`. | `tests/backend/test_liftover.py` now asserts a record-verified GRCh37→GRCh38 pair instead of `44404524` / `44404662`, which are the positions of no APOE variant and were produced by lifting an already-lifted coordinate. |
| C5 | The GRCh37 assembly independently admits each variant at its GRCh37 position and **excludes** it at the GRCh38 position. | UCSC hg19 single-base sequence reads (`raw/ucsc-hg19-*.json`): `chr19:45411941` = `T`, `chr19:45412079` = `C`, `chr19:44908684` = `G`, `chr19:44908822` = `A`. The reference base is an assembly observation, not a dbSNP-derived annotation. | Satisfies the two-agreeing-source rule with a source that does not share dbSNP's upstream assertion, and supplies a negative control for the rejected coordinates. |
| C6 | The repository's build-36 path already agreed with the GRCh37 values adopted here. | `tests/backend/test_liftover.py` asserts `lift_build36_to_grch37("19", 50103919, "CC") == ("19", 45412079, "CC")` at `origin/main`, predating this change. | The build-36 fixture `sample_23andme_v3.txt` is left unchanged; the GRCh37 value it lifts to is the one now adopted everywhere else. |

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

`raw/` holds twelve unedited public responses: five Ensembl GRCh37 Variation
REST records, one NCBI dbSNP eSummary record, four UCSC hg19 single-base
sequence reads (two positive, two negative control), and two NCBI PubMed
eSummary records used to verify the citations above.
`source-manifest.json` records each exact request URL, source, SHA-256, and
byte length, together with the citation list and the source-independence note.
No sanitization was required: every payload is a public reference-database or
bibliographic record and contains no genotype, sample, personal, or
credentialed data.

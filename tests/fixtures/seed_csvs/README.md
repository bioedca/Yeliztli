# Mini-fixture coordinate contract

The coordinate-bearing seed CSVs use one common genomic representation so the
mini databases can be joined without an implicit liftover:

- `chrom` and `pos` identify a 1-based GRCh37 position on a primary chromosome
  (`1`-`22`, `X`, `Y`, or `MT`; no `chr` prefix).
- `ref` and `alt` are genomic alleles on the forward (plus) strand at that
  GRCh37 mapping. They are not transcript-strand, coding-sequence, or risk-allele
  notation. Keep VEP HGVS, consequence, and strand fields coherent with the
  selected genomic allele.
- Every occurrence of an rsID follows the contract, including duplicate GWAS
  associations. A duplicate association is not permission to retain a stale
  coordinate.

The contract applies to `clinvar_seed.csv`, `vep_seed.csv`, `gnomad_seed.csv`,
and `gwas_seed.csv`. These files are the source of truth for the corresponding
tables in the checked-in `mini_*.db` fixtures.

`dbnsfp_seed.csv` is deliberately different. dbNSFP predictor values are
provider-fetched, non-redistributed runtime data, so the compact test fixture
contains only synthetic behavior scenarios in the reserved `rsYELIZTLI####`
namespace. Its coordinates are arbitrary GRCh38 lookup keys used to exercise
the production dbNSFP build guard and position-lookup path; they are not human
variant loci. `dbnsfp_seed.contract.json` records the exact scenarios, and the
offline contract test rejects real dbSNP identifiers, unreviewed scenarios, or
incoherent SIFT/PolyPhen score-category pairs. Neither the CSV nor
`mini_dbnsfp.db` is a scientific score oracle.

## Coordinate oracle

`../panel_rsid_coordinates.json` is the committed offline oracle for seed rsIDs
that overlap the application panels. It records Ensembl GRCh37 Variation REST
primary-chromosome mappings and is generated deliberately by
`scripts/build_panel_rsid_coordinates.py`. The regeneration tests derive their
guard scope from the complete seed/oracle intersection; there is no hand-picked
rsID allowlist.

Some useful fixture variants are not application-panel members. Their
independently verified mappings belong in
`ADDITIONAL_VERIFIED_GRCH37_MAPPINGS` in
`tests/backend/test_regenerate_fixtures.py`. For example, rs662 is explicitly
guarded at `7:94937446` rather than disappearing from coverage merely because it
is absent from the panel snapshot.

Records consulted: the committed `panel_rsid_coordinates.json` oracle records
per-record Ensembl GRCh37 Variation REST URLs (accessed 2026-07-16), and
`../clinvar_seed_snapshot.json` records NLM Clinical Tables ClinVar identities
(accessed 2026-07-16). The mappings changed for issue #1949 were rechecked
against Ensembl GRCh37 on 2026-07-19. Ambiguous records are also cross-checked
against NCBI RefSNP or ClinVar as described below; save those access dates and
raw responses with the evidence notes.

An rsID can have multiple alleles and can expose primary, patch, alternate, or
historical mappings. Do not copy the first coordinate returned by an API:

1. Inspect every Ensembl GRCh37 mapping and its assembly, sequence region,
   strand, interval, and allele string.
2. Select the standard primary-chromosome mapping. Treat a patch/alternate
   mapping as supporting context, not as the mini-fixture join coordinate.
3. For a multiallelic record, identify which alternate allele the fixture row
   represents. Confirm that the chosen forward-strand `ref`/`alt` pair occurs in
   the mapping's allele string.
4. Cross-check ambiguous indels, reverse-strand transcript notation, merged
   rsIDs, and multiple primary mappings against another authoritative record
   such as NCBI RefSNP or ClinVar. Save the raw responses and the selection note
   under the gitignored `data/science-evidence/<date>-<concern>/` directory.

## Intentional source-build exceptions

Not every coordinate-looking value in `tests/fixtures/` should be normalized.

- `gnomad_seed.af_asj.provenance.json` retains source-native GRCh38 variant IDs
  and positions. They document where the ASJ frequency came from; the normalized
  GRCh37 join coordinate lives in `gnomad_seed.csv`.
- Raw vendor fixtures retain the genome build declared by the vendor. In
  particular, the build-36 23andMe v3 fixture is an input used to exercise build
  detection and conversion. Changing it to GRCh37 would destroy that test case.

Document any new exception explicitly. Do not weaken the seed/oracle overlap
guard to accommodate stale data.

## Adding or normalizing an rsID

1. Fetch and save the authoritative GRCh37 variation record, then resolve the
   primary mapping and forward-strand allele as described above.
2. If the rsID belongs to an application panel, deliberately refresh
   `panel_rsid_coordinates.json` with an access date. If it is outside the panel
   universe, add its verified mapping to the explicit non-panel oracle.
3. Search the repository for every coordinate-bearing occurrence. Update all
   five seed CSVs where the rsID exists, duplicate rows, manually maintained
   coordinate fixtures such as `mini_clinvar.vcf`, and hard-coded test
   consumers. Keep annotations and alleles internally coherent.
   VCF indels require a left anchor and therefore may start one base before the
   Ensembl mapping interval used by the seed CSVs. Normalize them against the
   GRCh37 reference sequence and verify that applying either representation
   yields the same alternate haplotype; do not copy a seed `-` allele into VCF.
4. Run the coordinate tests before regeneration. A failure should enumerate any
   remaining GRCh38, alternate-locus, or strand-inconsistent occurrence.
5. Preview all four databases in a temporary directory:

   ```bash
   python scripts/regenerate_fixtures.py --output-dir /tmp/yeliztli-mini-preview
   ```

6. Dump both the checked-in and preview databases with `sqlite3 .dump` and
   inspect each SQL diff. Only the intended rows and fields should change;
   investigate unrelated schema, table, row-count, sequence, metadata, or
   ordering drift before replacing a binary.
7. Regenerate the checked-in databases only after the SQL deltas are understood:

   ```bash
   python scripts/regenerate_fixtures.py
   python -m pytest tests/backend/test_regenerate_fixtures.py
   ```

The test suite compares fresh and checked-in databases semantically through SQL
dumps as well as checking coordinate-overlap multiplicity. A seed edit without a
matching binary regeneration, or a binary edit that cannot be reproduced from
the seeds, must fail.

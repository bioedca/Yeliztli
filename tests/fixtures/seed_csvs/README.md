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

`gene_phenotype_seed.csv` is a behavior fixture, not a reference table. Every
row is labelled `source=mondo_hpo`, and its `inheritance` values date from the
initial commit and have not been independently verified. Of the two ever checked
(#2359), IL10's value is withheld for want of evidence, and VKORC1's two
sources agree but their independence was not established, so neither is a
verified claim (#2360). The `inheritance_pattern` users see comes from the
production `gene_phenotype` table, which `backend/annotation/mondo_hpo.py` and
`backend/annotation/omim.py` build from the MONDO/HPO and OMIM downloads. This
seed only builds `../mini_reference.db` for tests, so do not cite it as a
source of inheritance. `tests/backend/test_seed_fixtures_stay_test_only.py`
fails if production code starts reading it.

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

Absence from the panel snapshot is not a small gap. The two APOE ε-defining
SNPs, rs429358 and rs7412, are matched by rsID in `backend/analysis/apoe.py` and
so never entered `panel_rsid_coordinates.json`; with no oracle scope over them
all three seeds carried the GRCh38 pair `19:44908684` / `19:44908822` under this
GRCh37 contract, and the regeneration test asserted one of those values outright
(#476). Both are now pinned in the non-panel oracle at `19:45411941` and
`19:45412079`. Prefer adding a verified mapping over leaving an rsID unguarded.

The panel oracle reaches only panel members, and that gap let 75 seed rsIDs
drift to a coordinate from another assembly or another locus (#2375). Some had
annotation describing a different variant entirely: the row labelled PAH was the
KRAS G12 locus. `../seed_rsid_grch37_coordinates.json` is the seed-wide oracle:
every dbSNP-form rsID in the four coordinate seeds, with its single Ensembl
GRCh37 primary mapping. `tests/backend/test_seed_grch37_coordinates.py` holds
every located seed row to it, loci only. Rows writing an un-padded `-` allele sit
at Ensembl's start; VCF-form indels sit one base earlier. Its exceptions are
explicit sets that can only shrink: #2345's `rs12345`, and the unannotated rows
that mirror the vendor parser fixtures, which are a follow-up. The rsID is a
row's identity, so a corrected row takes its rsID's locus and alleles, and an
annotated row then mirrors the pinned production bundle (#2562). Evidence:
`data/science-evidence/2026-10-21-issue-2375-seed-grch37-coordinates/`.

The loci guard does not look at alleles, and the REF pin of #2344 reaches only
the 38 panel rsIDs. `tests/backend/test_seed_grch37_alleles.py` holds every
located row of `vep_seed`, `clinvar_seed` and `gnomad_seed` to its rsID's
plus-strand GRCh37 alleles (#2583). Where the oracle's allele string is
single-base, `ref` and `alt` must both come from it, and `ref` must be its first
allele. That first allele is the GRCh37 assembly base at every position the
guard pins, checked against two sources in
`data/science-evidence/2026-10-22-issue-2583-seed-allele-orientation/`. An SNV
row written for an indel rsID is flagged too.

Rows that break this today are listed exactly in
`../seed_allele_known_exceptions.json`, so the list can only shrink. #2590 gave
every unannotated `vep_seed` filler row its oracle alleles. The six listed rows
that remain are annotated or carry frequencies, and each has an owning issue
(#2541, #2563). When you
correct a row, also re-source the frequencies of any `gnomad_seed` row whose
orientation changes, rather than flipping them.

#2591 corrected CYP2D6 rs1065852 to genomic G>A and ABCB1 rs2032582 to the
reference-relative A>C allele. The latter also changes its old reverse-direction
coding/protein annotation to the pinned bundle's `c.2677T>G` / `p.Ser893Ala`,
exon 22. Every AF and homozygote count in those two gnomAD rows now comes from
the exact GRCh37 allele's gnomAD r2.1.1 **exome** record, with record-level ASJ
provenance overriding the legacy defaults. The explicit multiallelic selection,
source versions, licences and sanitized public payloads are retained in
`data/science-evidence/2026-10-23-issue-2591-seed-strand/`.

`../mini_clinvar.vcf` is hand-maintained rather than generated, so the
seed/oracle guard above did not reach it and it drifted with the seeds. It is
now checked against the same oracle: it must declare `##reference=GRCh37`, and
every `RS=` record whose rsID the oracle knows must sit at the recorded GRCh37
position — allowing the one-base left anchor that a VCF indel carries.

The same applies to the **vendor raw-data fixtures**, which are ingestion inputs
rather than reference data and so were never in the oracle's scope either. They
are now guarded through the production parsers: each fixture's declared build is
read back from `parse_23andme` / `parse_ancestrydna` (or from `##reference=` for
`sample_not_23andme.vcf`), and its APOE rows must match that build. The build-36
`sample_23andme_v3.txt` is asserted to *keep* its build-36 coordinates and to
lift onto the GRCh37 oracle through `lift_build36_to_grch37`, so the exception
stays deliberate rather than becoming a hole.

The synthetic EUR pair (`synthetic_eur_23andme.txt` and the
`synthetic_eur_ancestrydna.txt` generated from it) carried no APOE rows, so the
APOE guard never reached them. Their positions had been copied from the PCA
bundle's `aim_positions_grch38`, and their calls sampled on the GRCh38 strand,
so the AncestryDNA file presented a GRCh37 vendor format with GRCh38 data in
every row (#2493):
- 4,949 of the 5,000 positions differ from GRCh37;
- six loci are reversed between builds, so their calls need complementing.

Both files now take their GRCh37 position and strand from
`synthetic_eur_aim_coordinates.tsv`, which keys each AIM's GRCh38 coordinate in
the PCA bundle to its GRCh37 position, strand and plus-strand alleles. Each value
is recorded only where two routes agree:
- **4,979 rows:** the rsID's own GRCh37 placement and Ensembl's assembly-map
  projection of the GRCh38 coordinate.
- **21 rows:** for vendor `i` probes, which have no public identity record, and
  for rsIDs in regions restructured between builds, ±25 bp of GRCh37 assembly
  sequence matching the GRCh38 flank stands in for the missing route.

The guard checks four things:
- the oracle still matches the PCA bundle;
- both files sit at the oracle's GRCh37 positions, with calls drawn from its
  plus-strand alleles, under a build their parsers report as GRCh37;
- the committed AncestryDNA file is byte-identical to a fresh regeneration;
- rows the panel oracle also knows agree with it.

Evidence: `data/science-evidence/2026-10-20-issue-2493-fixture-grch37-positions/`.

Records consulted: the committed `panel_rsid_coordinates.json` oracle records
per-record Ensembl GRCh37 Variation REST URLs (accessed 2026-07-16), and
`../clinvar_seed_snapshot.json` records NLM Clinical Tables ClinVar identities
(accessed 2026-07-16). The mappings changed for issue #1949 were rechecked
against Ensembl GRCh37 on 2026-07-19. The two APOE mappings normalized for
issue #476 were verified on 2026-08-27 against Ensembl GRCh37 Variation REST and
independently against NCBI dbSNP eSummary `chrpos_prev_assm`, with the raw
responses and claim mapping under
`data/science-evidence/2026-08-27-apoe-grch37-fixtures-476/`. Ambiguous records are also cross-checked
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

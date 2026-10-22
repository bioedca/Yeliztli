# Seed filler rows take their rsID's GRCh37 alleles (#2590)

No new external source was queried. Every allele written here comes from the committed seed-wide oracle, `tests/fixtures/seed_rsid_grch37_coordinates.json` (Ensembl GRCh37, accessed 2026-10-21, #2375). The oracle's first allele is the GRCh37 assembly base at every single-base position the allele guard pins: Ensembl GRCh37 agrees with UCSC hg19, or with NCBI `NC_012920.1` for MT, at 644/644 positions (`../2026-10-22-issue-2583-seed-allele-orientation/assembly_bases.json`).

**Scope.** The 480 unannotated `vep_seed` filler rows that #2588's guard listed as exceptions: rows with no gene, transcript or HGVS, whose alleles therefore had nothing to stay coherent with. `scripts/correct_filler_alleles.py` rewrote them:
- **SNV, not reference-first** (176 rows): swapped, so REF is the assembly base.
- **SNV outside the allele string** (299): REF is the assembly base. ALT is the row's old allele when that is in the string, otherwise the string's next allele. These rows' old alleles were invented or strand-flipped, so ALT is a valid allele at the site, not a claim about any particular variant.
- **SNV at a padded indel string** (3: `rs397517749` `TT/T`, `rs587782504` `ATA/A`, `rs281860276` `AGAAGA/AGA`): written as the string gives them, at its start.
- **SNV at an un-padded insertion** (2: `rs199474672` and `rs794728091`, `-/T`): written as `-`>`T` at Ensembl's start, the un-padded form the locus guard already accepts.

Annotated rows, `clinvar_seed` and `gnomad_seed` were not touched. The 11 listed rows that remain belong to:
- #2541: `rs6025`;
- #2563: `rs1800795`;
- #2591: `rs1065852` and `rs2032582`, which carry transcript-strand alleles and need annotation and frequency re-sourcing.

`tests/fixtures/mini_vep_bundle.db` was regenerated from the corrected seed with `scripts/regenerate_fixtures.py`.

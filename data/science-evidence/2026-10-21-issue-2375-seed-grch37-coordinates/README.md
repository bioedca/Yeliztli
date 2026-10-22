# GRCh37 coordinates for the coordinate-bearing seed CSVs (#2375)

Accessed 2026-10-21.

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

These rows need their alleles, and in the second case their annotation, re-derived from the rsID's GRCh37 record and the pinned production bundle.

**Rule.** The rsID is the row's identity. The fixtures are keyed by rsID, as in #2493. Each corrected row therefore takes its rsID's GRCh37 locus and alleles. An annotated row then mirrors the pinned production VEP bundle.

**Scope split.** The 75 affected rsIDs form two disjoint groups:
- **49 annotated rsIDs, fixed here.**
- **26 unannotated rsIDs.** Every one mirrors a row of the vendor parser fixtures (`sample_ancestrydna_v2.txt` and its CRLF twin): the `rs9999001`–`rs9999050` placeholder family, the two IDs with no GRCh37 record, and the PAR/MT/Y scenarios. Correcting them changes those fixtures and the parser tests, so they are a separate change, filed as a follow-up.

Two of the 26, `rs199474672` and `rs794728091`, are `-/T` insertions whose rows already sit at the VCF anchor. The first-pass classification compared their position with Ensembl's `start`, which for an insertion is the base after the anchor. Their loci are right and only their alleles are wrong. The guard therefore leaves 24 locus exceptions.

**Rewrite (C4).** `scripts/rewrite_annotated_seed_loci.py` writes `derived/annotated_rewrite_plan.json`. It replaces only the leading `rsid,chrom,pos,ref,alt` text of each line and preserves line endings (`gnomad_seed.csv` has CRLF lines).
- **Rows rewritten:** 129 across 49 rsIDs (50 `vep_seed`, 32 `clinvar_seed`, 47 `gnomad_seed`).
- **Bundle loci:** 48 rsIDs take a row of the pinned bundle (`tests/fixtures/vep_seed_bundle_snapshot.json`, v4.0.0, sha256 `c8e5f162…`). Every chosen bundle locus equals the rsID's Ensembl GRCh37 mapping.
- **Allele choice:** seed alleles 85, complemented seed alleles 19, reversed seed alleles 14, reversed complemented seed alleles 8, vcf deletion from ensembl and assembly 3. "Reversed" means the two assemblies' reference bases differ at the locus. An example is `rs16947` (CYP2D6\*2): the GRCh37 reference carries Cys296, so the row is `A>G` (p.Cys296Arg). No row needed a canonical-transcript fallback.
- **`rs5030655`** (CYP2D6\*6, Ensembl `22:42525086 A/-`) is absent from the bundle. It takes the VCF form `22:42525085 CA>C`. The padding base `C` is assembly sequence (`raw/ensembl_sequence/rs5030655_padding.json`). Its annotation (frameshift, `c.454delT`) was already that deletion's.

**Annotation (C5).** `scripts/mirror_bundle_annotation.py` runs #2562's `apply_bundle`, unchanged, against the committed snapshot. It mirrored 43 rows. The production bundle on the machine this ran on has a different sha256 from the one the manifest pins, so `build_vep_seed_bundle_snapshot.py` refused it. The snapshot is the pinned bundle's rows, so mirroring from it writes what `--apply` would. Several rows now describe the variant their rsID names:
- `rs121913529`: KRAS p.Gly12Asp/Ala, not PAH;
- `rs63750066`: APP p.Ala713Thr, not BRCA2;
- `rs104894396`: GJB2 p.Trp24Ter, not GBA.

`rs10811661` and `rs1111875` became intergenic. `scripts/prune_bundle_snapshot.py` removed them from the snapshot's annotated scope (deletions only). `tests/fixtures/vep_gene_strand_snapshot.json` was regenerated live by its own generator: 6 genes added, 5 dropped, and no existing gene's strand changed.

**Guard.** `scripts/write_seed_coordinate_oracle.py` writes `tests/fixtures/seed_rsid_grch37_coordinates.json` (676 rsIDs) from `raw/ensembl_grch37/`. `tests/backend/test_seed_grch37_coordinates.py` holds every located seed row to it. It fails on `main` @ `1c5f7f5f` and passes on this branch.

**Controls.**
- The production bundle (Ensembl 112) agrees with Ensembl GRCh37 Variation on every chosen locus. They share a lineage, so this agreement is a control, not a second source.
- `mini_clinvar.vcf` carries none of the moved rsIDs at a stale locus. Its only oracle disagreement is #2345's `rs12345`.

**Not covered here (follow-ups).**
- **Seed alleles.** Among rows already at their locus, 307 carry an allele outside the rsID's GRCh37 allele string and 182 do not put GRCh37's reference allele first. The allele guard reaches only the panel oracle's 38 single-base rsIDs (#2344).
- **The 24 parser-fixture locus rows** above.

**Citations** (verified for #2493, `data/science-evidence/2026-10-20-issue-2493-fixture-grch37-positions/raw/citation/`; PubMed lists no correction or retraction):
- `PMID:25236461 / DOI:10.1093/bioinformatics/btu613 (accessed 2026-10-21)`: Yates et al., *The Ensembl REST API: Ensembl Data for Any Language*, Bioinformatics 2015. It covers the variation and sequence endpoints used here.
- `PMID:25352552 / DOI:10.1093/nar/gku1010 (accessed 2026-10-21)`: Cunningham et al., *Ensembl 2015*, Nucleic Acids Res 2015. It describes the dedicated GRCh37 site.

GRCh37.p13 is NCBI Assembly `GCF_000001405.25`, verified in the #2493 packet.

**Discovery ladder.** The Consensus MCP server failed to connect in this session, and Scite was not needed, because no literature claim is made. These are registry coordinates and assembly bases, read from their defining databases.

**License.** Ensembl: "Ensembl imposes no restrictions on access to, or use of, the data provided…".

**Scripts** (`scripts/`, as run):
1. `fetch_grch37_variation.py`
2. `compare_seed_rows.py`
3. `fetch_reference_bases.py`
4. `corroborate_reference_bases.py`
5. `rewrite_annotated_seed_loci.py`
6. `mirror_bundle_annotation.py`
7. `prune_bundle_snapshot.py`
8. `write_seed_coordinate_oracle.py`
9. Then `scripts/build_vep_strand_snapshot.py --evidence-out tests/fixtures/vep_gene_strand_snapshot.evidence.json` and `scripts/regenerate_fixtures.py`, with `mini_dbnsfp.db` restored, since it is logically identical.

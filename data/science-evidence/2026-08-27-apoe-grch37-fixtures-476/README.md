# APOE GRCh37 fixture coordinates for issue #476

## Citations

Primary records for the variant identity claim:

- `PMID:27078154` / `DOI:10.1371/journal.pone.0153593` — "Development of a Melting Curve-Based Allele-Specific PCR of Apolipoprotein E (APOE) Genotyping Method for Genomic DNA, Guthrie Blood Spot, and Whole Blood." *PLoS One* 2016 (accessed 2026-08-27)
- `PMID:34313350` / `DOI:10.1002/jcla.23925` — "APOE gene ɛ4 allele (388C-526C) effects on serum lipids and risk of coronary artery disease in southern Chinese Hakka population." *J Clin Lab Anal* 2021 (accessed 2026-08-27)
- `PMID:39154456` / `DOI:10.1016/j.bcmd.2024.102883` — "Clinical characteristics, laboratory features and genetic profile of hemoglobin E (HBB:c.79 G > A)/β … -thalassemia subjects…" *Blood Cells Mol Dis* 2024 (accessed 2026-08-28) — names hemoglobin E as `HBB:c.79G>A`, the coding identity behind the rs33950507 correction

Resource citations for the databases queried:

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

The independent mapping source is **VariantValidator**, which was given only the
APOE coding HGVS and **no rsID at all** (it returns `rsid: None` for these
queries). It projects the variant onto the genome through RefSeq/UTA transcript
alignments rather than through dbSNP's rsID record, and it returns both builds
in one response:

| Query (no rsID supplied) | GRCh37 | GRCh38 |
| --- | --- | --- |
| `NM_000041.4:c.388T>C` | `NC_000019.9:g.45411941T>C` | `NC_000019.10:g.44908684T>C` |
| `NM_000041.4:c.526C>T` | `NC_000019.9:g.45412079C>T` | `NC_000019.10:g.44908822C>T` |

The link from each rsID to its coding position is anchored in the literature
rather than assumed: `PMID:27078154` identifies rs429358 as the codon-112 and
rs7412 as the codon-158 APOE variant, and `PMID:34313350` names the same two
variants by their coding positions, `388C-526C`, in its title. APOE's 18-residue
signal peptide reconciles the two conventions, and the arithmetic is exact —
`c.388` is precursor codon 130 (mature codon 112) and `c.526` is precursor codon
176 (mature codon 158), matching the `p.Cys130Arg` and `p.Arg176Cys` annotations
already carried in `vep_seed.csv`.

### Assembly-level control

The GRCh37/hg19 reference base is a third, weaker check. It is *necessary but
not sufficient* for the positive claim — a matching base occurs at many
positions — but it is decisive against the rejected coordinates:

| hg19 position | Reference base | Consequence |
| --- | --- | --- |
| `chr19:45411941` | `T` | Consistent with rs429358's `T/C` allele string. |
| `chr19:45412079` | `C` | Consistent with rs7412's `C/T` allele string. |
| `chr19:44908684` | `G` | Neither `T` nor `C`: rs429358 **cannot** be at this GRCh37 position. |
| `chr19:44908822` | `A` | Neither `C` nor `T`: rs7412 **cannot** be at this GRCh37 position. |

The last two rows are the negative control: the values the fixtures previously
carried are *excluded* by the assembly, not merely unsupported by it.

## Claim mapping

| ID | Claim | Evidence | Repository consequence |
| --- | --- | --- | --- |
| C1 | rs429358 is the APOE codon-112 (`c.388T>C`) variant and rs7412 the codon-158 (`c.526C>T`) variant. | `PMID:27078154` (codon 112 / codon 158) and `PMID:34313350` (`388C-526C`), both accessed 2026-08-27 and clear of retraction/correction flags. Signal-peptide arithmetic reconciles the mature and precursor numbering exactly. | Establishes the variant identity that C2 maps to the genome without going through an rsID record. |
| C2 | Those two coding variants map to GRCh37 `19:45411941` and `19:45412079` (and to GRCh38 `19:44908684` / `19:44908822`). | VariantValidator GRCh37 endpoint, given the coding HGVS and no rsID (`raw/variantvalidator-*.json`). Mapping derives from RefSeq/UTA transcript alignment, independent of dbSNP. | The independent source required for the two-agreeing-source rule. |
| C3 | The variant databases agree with C2. | Ensembl GRCh37 Variation REST reports `19:45411941` strand `1` `T/C` and `19:45412079` strand `1` `C/T`; NCBI dbSNP eSummary reports the same values as `chrpos_prev_assm`. These two share an upstream assertion and count once. | `clinvar_seed.csv`, `vep_seed.csv`, `gnomad_seed.csv`, the three generated mini databases, and `mini_clinvar.vcf` carry the GRCh37 pair. |
| C4 | `19:44908684` / `19:44908822` are the **GRCh38** positions of the same variants, not GRCh37. | dbSNP reports them as `chrpos`; VariantValidator returns them as the GRCh38 loci in the same response that returns the GRCh37 loci. | The previous fixture values are identified as a build mislabel, not an alternate GRCh37 mapping. |
| C5 | The GRCh37 assembly excludes the previous fixture coordinates. | UCSC hg19 single-base reads: `45411941`=`T`, `45412079`=`C`, `44908684`=`G`, `44908822`=`A`. | Negative control; see "Assembly-level control" above. |
| C6 | The repository's own GRCh37→GRCh38 chain reproduces the pair. | `backend.ingestion.liftover.convert_coordinate("19", 45411941)` returns `("19", 44908684)`; `("19", 45412079)` returns `("19", 44908822)`. | `tests/backend/test_liftover.py` now asserts a record-verified pair instead of `44404524` / `44404662`, which are the positions of no APOE variant and arose from lifting an already-lifted coordinate. |
| C7 | The repository's build-36 path already agreed with these GRCh37 values. | `tests/backend/test_liftover.py` asserts `lift_build36_to_grch37("19", 50103919, "CC") == ("19", 45412079, "CC")` at `origin/main`, predating this change. | The build-36 fixture `sample_23andme_v3.txt` is left unchanged; the value it lifts to is the one now adopted everywhere else. |

## Discovery-tool ladder

Recorded rather than assumed, per the repository's evidence contract:

- **Consensus** — invoked 2026-08-27; 20 papers returned. No paper asserts a genomic build coordinate, which is expected: the APOE literature identifies these variants by codon or coding position, not by assembly coordinate. It did surface both records now cited in C1.
- **Scite** — **unavailable**: the monthly MCP call quota was exhausted (250/250), with a reported reset of 2026-09-03. No Scite result was used.
- **Fallback** — the PubMed specialist connector was used in Scite's place, and both retained literature records were re-resolved through PubMed eSummary for title, journal, DOI, and retraction status.

## The APOE-region block in the vendor fixtures

The build-37-declaring vendor fixtures carried the same GRCh38 APOE block, and
normalizing only rs429358/rs7412 there would have left those files mixed-build
inside a 7 kb window (and, for the VCF, out of sorted order). Checking the three
neighbouring SNPs turned up a **second, different defect**: two were paired with
the wrong coordinate entirely.

| rsID | Fixture position (before) | GRCh38 (record) | GRCh37 (record) | hg19 REF | Verdict |
| --- | --- | --- | --- | --- | --- |
| rs440446 | `19:44905579` | `19:44905910` | `19:45409167` | `C` | held **rs405509's** GRCh38 position |
| rs405509 | `19:44905791` | `19:44905579` | `19:45408836` | `T` | held a position belonging to neither |
| rs769449 | `19:44906745` | `19:44906745` | `19:45410002` | `G` | correct GRCh38, wrong build |

All three were rewritten to their verified GRCh37 mappings, which necessarily
resolves the mispairing as well, and the affected rows were re-sorted.

**How independent is that?** Partly, and the limit is stated rather than
glossed. These three are non-coding, so there is no coding HGVS to project the
way C1/C2 do for APOE, and the rsID→locus assertion has a single upstream
lineage (dbSNP, mirrored by Ensembl). What *is* independent is the cross-build
correspondence and the reference base: VariantValidator, given the genomic HGVS,
validates the GRCh37 REF and returns GRCh38 `19:44905579`, `19:44905910`, and
`19:44906745` — matching dbSNP's `chrpos` for rs405509, rs440446, and rs769449
respectively. That is exactly what establishes the mispairing, because the
fixture had rs440446 sitting on rs405509's GRCh38 coordinate. These three rows
also carry no analytic weight: no module reads them, and they exist only as
APOE-region context in a vendor fixture. Two rows
in `sample_not_23andme.vcf` additionally declared REF/ALT the wrong way round
(`rs405509` as `G>T`, `rs769449` as `A>G`); REF is the assembly base, so both
were corrected against the hg19 reads. The derived genotypes are unchanged,
because the repository stores heterozygous calls in canonical sorted order.

## rs33950507 (HBB) — a third defect, and an analytic one

The same sweep found one more mismatch, the only one across all six fixtures:
`sample_23andme_v5.txt` placed `rs33950507` at `11:5248281`, which is neither
its GRCh37 (`11:5248173`) nor its GRCh38 (`11:5226943`) position.

This one is **not** low-stakes. `backend/data/panels/carrier_panel.json` lists
rs33950507 among HBB's expected pathogenic ClinVar rsIDs, so it can reach a
user-facing carrier workflow. It therefore takes the full two-agreeing-source
gate rather than inheriting the oracle value, and it gets the same treatment
APOE did:

| Step | Evidence |
| --- | --- |
| Identity | dbSNP RefSNP returns `NM_000518.5:c.79G>A` for rs33950507, and `PMID:39154456` names hemoglobin E as `HBB:c.79G>A` in its title. |
| Independent mapping | VariantValidator, given that coding HGVS and **no rsID**, returns GRCh37 `NC_000011.9:g.5248173C>T` and GRCh38 `NC_000011.10:g.5226943C>T`. |
| Assembly control | hg19 `chr11:5248173` is `C`, matching REF. `chr11:5248281` — the position the fixture used — is `A`, which cannot host a `C>T`. |

HBB is on the minus strand, which is why the genomic `C>T` corresponds to the
coding `G>A`; the fixture's `TT` genotype is a homozygous alternate call and is
unchanged by the correction.

## `mini_clinvar.vcf` access date

Every `RS=` record in the fixture was re-resolved against Ensembl GRCh37 on
2026-08-27 *before* its `##EnsemblGRCh37Accessed` header was advanced to that
date, so the header describes a retrieval that actually happened and that covers
the changed rows. All real rsIDs match, with the documented one-base left anchor
for the two VCF indels; the only non-matching row is the one the fixture itself
flags `SYNTHETIC`.

## Discovery-tool ladder note

The build-36 `sample_23andme_v3.txt` remains at `19:50103781` / `19:50103919`
by design, and the regression guard now asserts that it does *and* that those
coordinates lift onto the GRCh37 oracle values through the production
`lift_build36_to_grch37` path.

## Retained evidence

`raw/` holds the responses behind every claim above, and `source-manifest.json`
records each one's exact request, source, SHA-256, and byte length. The counts
below are the manifest's and total its 44 payloads, so an auditor can reconcile
the two directly.

| Group | Payloads | What they support |
| --- | --- | --- |
| Ensembl GRCh37 Variation REST | 6 rsID records + 11 `mini_clinvar.vcf` re-date records + 1 derived recheck summary + 1 release probe = **19** | corroborating variant-database mapping (C3); the fixture re-dating |
| NCBI dbSNP | 2 eSummary records + 1 RefSNP HGVS record + 1 build probe = **4** | the GRCh37/GRCh38 pairing (C3, C4), the neighbour cross-build check, the rs33950507 coding identity |
| VariantValidator | 3 coding-HGVS mappings + 3 genomic-HGVS validations = **6** | the independent mappings (C2, rs33950507) and the neighbour REF/cross-build check |
| UCSC hg19 | 9 single-base reads (4 APOE incl. 2 negative controls, 3 neighbour REF, 2 HBB incl. 1 negative control) + 1 assembly-metadata probe = **10** | the assembly-level controls (C5) |
| NCBI PubMed eSummary | **4** records | citation verification and retraction checks |
| Consensus | **1** derived result-set record | the discovery-tool ladder's first rung |

Beyond the payloads, `source-manifest.json` carries a `source_provenance` block
giving every source's observed release/version and its license reference with
the terms consulted on 2026-08-27 — including Consensus, whose version is
recorded as **not exposed** rather than guessed, since the MCP surface returns
no server, index, or corpus-version field. It also carries the citation list,
the discovery-tool ladder, the `mini_clinvar.vcf` recheck note, the rs33950507
note, and the source-independence note.

No sanitization was required for the HTTP payloads: each is a public
reference-database, variant-mapping, or bibliographic record containing no
genotype, sample, personal, or credentialed data, and none was edited. The
Consensus entry is explicitly a derived record rather than a wire capture,
because that tool returns through MCP rather than over HTTP.

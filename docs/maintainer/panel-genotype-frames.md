# Which strand does a categorical panel key use?

**Maintainer decision for #2716.** Preserve the source call and its verified
orientation at ingest. Give each curated panel row an explicit key frame.
Resolve the relationship once, where a categorical scorer joins the two.
An observed pair of letters alone cannot establish that relationship.
Implementation and migration belong to [#2301](https://github.com/bioedca/Yeliztli/issues/2301).

## Representations and lookup

1. The ingest record identifies the source format and version, assembly,
   chromosome, one-based position, marker and the frame in which the observed
   call is stored. Preserve the evidence for the declared strand. A file
   with a recognized shape and no supportable strand declaration has unknown
   orientation. Reject contradictions between the header and the supported
   format contract. Preserve no-calls, ploidy and literal indel encodings.
2. A panel adapter identifies the exact locus, modelled allele set, panel
   revision and key frame. An assembly-oriented row names its assembly and
   strand. A transcript-oriented row records its versioned transcript,
   mapping to the GRCh37 locus and orientation. A gene symbol, rsID or the
   spelling of a risk allele cannot substitute for this evidence. Independent
   special-calling tables must declare their relationship to the same locus.
3. After verifying locus, build and frame, transform each supported SNV allele
   exactly once into the row's frame. Then canonicalize the unphased allele
   order and perform one lookup. Use the complete interpretation only for a released
   key whose wording, category and downstream flags have been curated together. An unmodelled alternate or unsupported allele
   leaves the interpretation indeterminate. Keep an observed unresolved call
   distinct from an absent marker, no-call and verified non-carrier. Map an
   I/D or slash-delimited token only through its reviewed locus-specific
   encoding. Preserve the existing mtDNA rules that prohibit complementation.
4. Withhold the row's interpretation whenever the observed strand, key
   strand or evidence linking them is unverified. This rule applies even
   when all enumerated orientations would yield the same complete output.
   Withhold dependent text, aggregate contributions and derived actions.
   Keep independently verified rows visible. A neutral coverage record may
   explain that a call was present and its strand could not be verified.
   The maintainer considered an exception for fully invariant outputs and
   chose to require provenance for every displayed interpretation.

Recording a typed frame at ingest alone cannot interpret mixed historical
panel conventions. Adapter-only metadata cannot recover a missing sample
frame. Withholding without recording either source gives no route to recovery.
Combining both declarations lets a verified reanalysis recover a result and
keeps the original call auditable.

## What the current software knows

The 23andMe and AncestryDNA parsers uppercase and order diploid calls.
`ParsedVariant` carries marker, position, chromosome and genotype.
`ParseResult` carries format/version and build. The ingest path persists
`file_format` and the raw row, without a per-call orientation record.
All eight categorical scorers fetch the stored genotype by rsID. Annotation
REF/ALT fields do not establish which frame a separate panel author used.

The supported AncestryDNA GRCh37 SNP export has a documented forward-strand
convention. The 23andMe synthetic v5 header illustrates an explicit plus-strand
declaration. The vendor's GRCh37 browser-display documentation alone cannot
certify every historical download. Accept only the source build, format and
declaration actually supported by the importer. The 23andMe build-36 path
already maps coordinates and complements nucleotide calls on reverse chain
segments. Record that transformation and the resulting GRCh37 frame to avoid
a second flip. An uncertified input frame or unsuitable mapping stays unknown.

For merged samples, apply the same contract before deciding concordance.
`sample_merge._stream_raw_variants` currently reads calls without frame
metadata and `_apply_semantics` can mark equal spellings as a match.
Preserve source-specific locus, frame and transform evidence for each
candidate. A selected one-sided call inherits only its own proof. Mark a
two-sided match only when both identified loci and calls agree in a verified
common frame. If either source lacks frame evidence, equal letters cannot establish
agreement. Retain the uncertainty and withhold that merged row. A
recorded conflict resolution must identify the selected source and its
verified frame. A `merged_v1` label or `source=both` cannot attest a strand.

Today `lookup_by_genotype` tries the observed spelling and reversed order,
then their complements for eligible nucleotide calls. Seven scorers pass
categories to the palindromic guard. Fitness passes the full effect
dictionaries, so even two same-category entries can differ for that guard.
The table inventories all 133 `pathways[].snps[].genotype_effects` rows in their shipped panels on
`6ec3ba122c5eb319e63b4aa71bf8f39471077c5b`. None declares a typed key frame.
Existing locus-specific evidence nevertheless establishes some conventions,
including the fitness plus-strand fixture for rs1800012 and the nomenclature
audit of the methylation row for rs1051266.

| Panel | Rows | Rows that model both a homozygote and its complement | Other consumers of the mapping |
| --- | ---: | --- | --- |
| Allergy | 14 | rs144012689, rs1061235, rs1049793 | `_compute_histamine_combined` counts AOC1 risk-allele letters in the observed genotype. Use panel-frame dosage for the count and preserve the observed string for provenance. Celiac and cross-module findings also consume scored markers. |
| Fitness | 8 | rs1049434, rs9939609 | `_resolve_three_state` performs another lookup in the three-state table. |
| Gene health | 40 | rs747302, rs1801282, rs9939609, rs738409, rs689, rs2230199 | `_current_curated_effect` also resolves historical finding wording. |
| Methylation | 34 | rs1801198, rs12325817, rs585800 | `_genotype_matches` uses candidates for compound MTHFR rules. |
| Nutrigenomics | 17 | rs1799945, rs41525747 | `_resolve_lactose_persistence` looks up a called marker again. |
| Skin | 10 | rs1805009 | `_r_allele_dosage` generates candidates for the MC1R aggregate. |
| Sleep | 5 | None | `_resolve_metabolizer_state` generates candidates for the caffeine state. |
| Traits | 5 | rs747302 | Pathway summaries and other readers consume the scored result. |

All eight call `_score_snp`. Enumerating both `AA/TT` and `CC/GG` in each
effect map gives 18 panel rows and 16 distinct rsIDs. Of the 18, 17 have
different categories. Gene health rs1801282 has the same category and
different genotype-specific text. The full effect dictionaries for reversed
heterozygote keys also differ in four rows (fitness rs1800012, methylation
rs4880, nutrigenomics rs1799945 and sleep rs762551). Audit their differences
before canonicalizing either alias. Different spellings or prose alone do
not establish a biological disagreement. Reject conflicting unphased aliases
until curation identifies one approved interpretation.

This inventory counts modelled keys. A valid unmodelled alternate can also
spell an existing key in the opposite frame. For rs1051266, the present
methylation panel assigns Standard to all four modelled spellings and still
labels the encoded residues. The earlier Moderate example describes a previous
panel revision. RefSNP build 157, NC_000021.8:g.46957794, lists reference T
and alternatives A, C and G. Ensembl GRCh37 lists T/C/G and identifies dbSNP
as its source. Keep the A discrepancy unresolved and withhold any mapping
that requires resolving it. Neither source establishes a functional effect.
The source queries and extracts are in
`data/science-evidence/2026-11-20-panel-strand-frames-2716/`.

A baseline run on the audited commit ingested five fabricated, one-marker
files in each supported vendor layout. For each, call the ingest route's
`_ingest_file` on a fresh sample, run `score_methylation_pathways` with the
shipped panel, call `store_methylation_findings`, then reopen the sample
database. Select the rs1051266 item from the stored methylation
`pathway_summary.detail_json.snp_details`. Use the synthetic v2 VEP version
fixture for the AncestryDNA intake prerequisite. In both formats, AA and TT
each stored “Homozygous His27,” CT and TC each stored a heterozygote, and CC
stored “Homozygous Arg27.” All ten stored categories were Standard. This
demonstrates the current wording collision on fabricated inputs. The audit
does not measure whether either vendor assays the disputed alternate.

## Migration and acceptance for #2301

Introduce the frame and source-revision fields before enabling the new
resolver. Curate each effect and special-calling table against a cited
versioned GRCh37 reference and its original key convention. Previously held
keys need a separate curation decision before release, even with a verified
frame. For example, both rs41525747 homozygote entries describe withholding,
and the CC entry's prose and `lactase_persistence` flag do not agree.
Keep these interpretations and their dependent lactose state withheld until
the wording and flags have been reconciled against approved evidence. Use
the same release check for every previously unreachable collision or alias.

Existing raw
calls and findings need a verifiable chain from their source file, any
coordinate transform and the exact panel revision. Reconstruct only the
parts that can be proved. Withhold unsupported interpretations on every read
path, including stored pathway detail, reports and exports. A saved category
or file-format label alone cannot certify an earlier calculation. If
provenance is missing, offer reimport from a supported original export or
recovery of a verified panel revision as appropriate. Previously stored
rows that predate the frame contract require strand verification and a new
analysis before display. Only a successfully published rerun restores the
affected result. Preserve independently verified rows throughout recovery.

Use two temporary, entirely synthetic vendor files and temporary sample
databases. Through the production ingest, scorer and finding writer, exercise
the following inputs with a synthetic reverse-oriented panel row. Define its
genomic reference as C and its panel reference as G. Give panel AA a distinct
fixture effect, AG one heterozygote effect, GG the baseline and TT no model.
Alleles and effects here are software fixtures without a clinical claim.

| Check | Expected observation |
| --- | --- |
| A forward GRCh37 file reports AA | The adapter obtains TT. Stored SNP detail withholds the unmodelled call and no derived finding or recommendation inherits the AA effect. |
| The same letters AA arrive with verified panel-frame provenance | The adapter resolves AA directly. At the vendor entry point, a verified forward TT reaches the same stored positive effect. Exercise the panel-frame input at the typed adapter boundary; neither supported vendor needs to emit coding-strand data. |
| A file header contradicts its supported format or build declaration | Reject the upload before publishing a sample or findings. Verify the rejection and absence of a new sample. |
| A valid forward-strand upload has the panel rsID on the wrong GRCh37 position or chromosome | Withhold its interpretation and derived contributions. Store a correctly located positive control from the same upload. Verify both after reopening the database. |
| A forward-keyed synthetic row models AA and TT with distinct effects | Put a verified forward AA call at its exact locus in each vendor file. Assert the complete AA effect in reopened pathway detail and any carrier-gated finding. The reverse-keyed row below receives the same letters at its own locus and returns the distinct TT effect. |
| A 23andMe upload identifies GRCh37 but has no supported strand attestation | Use the header form in `tests/fixtures/sample_23andme_v4.txt` with a fabricated call and a verified panel row. Reopen the accepted sample, confirm the call is present and withhold its interpretation. Independently upload the same call and position with a supported plus-strand declaration and verify the complete expected result. |
| A verified upload joins a row with a missing or unsupported panel-key frame | Keep observed letters and verified source evidence. Withhold the stored interpretation. Restore it only after verifying the row's panel frame and rerunning. |
| An accepted sample contains AA with unverified row provenance | Withhold the interpretation. Repeat with an invariant mapping in which both orientations have the same complete output. Both cases stay withheld. A separately verified positive in the same sample is still present. |
| A second synthetic reverse-oriented row models both AA and TT with distinct effects | Forward AA must store the TT effect; verified panel-frame AA must store the AA effect. Verify both complete outputs, including the pathway detail. Unknown strand withholds both. |
| Forward CT and TC; panel GA and AG | Verify equality of the normalized key, full curated interpretation and finding membership. After reopening, check each source's own observed allele pair, its source frame and any recorded ordering transform. Conflict between order aliases withholds the affected row. |
| Forward CC (`hom_ref`), missing marker, and no-call | CC resolves to panel GG and produces no carrier-gated finding. Assert the baseline in the relevant pathway detail, the absence of the carrier text/action and the presence of the independent positive. The two missing cases retain their distinct coverage states. |
| Reopened historical database, then verified reanalysis | Test first reads before reanalysis, atomic replacement on success, a failed rerun, and an unaffected control. A reported category alone cannot clear unknown-frame state. |
| Merged source calls | Equal letters that disagree after verified normalization are a conflict. An unverified source leaves agreement unresolved. Different source spellings that normalize to the same verified allele pair can agree. Reopen the merged sample and check the stored source identity, interpretation and independent control. |

Repeat the rs1051266 baseline with the migrated, shipped methylation row and
fabricated forward-strand uploads. Verify the original observed call in the
reopened sample. Forward AA must withhold residue wording, including the
current “Homozygous His27” text, because its transformed key has no approved
model. For verified forward TT, assert the full existing panel-AA context
text and Standard category. Neither input emits a carrier finding. Use an
independent called marker to verify the pathway's supported category. Give
`TestSLC19A1ComplementaryStrandRendering` explicit
observed frames for its forward and coding-strand inputs. Its coding AA
control must retain the same approved context text. These tests exercise
the shipped key convention without resolving the disputed alternate or assay
coverage. The existing nomenclature review is recorded in
`data/science-evidence/2026-08-10-slc19a1-rs1051266-nomenclature-2023/`.

Run the same cases through both vendor formats for supported forward calls.
Check values after reopening the sample database, including nonempty
`pathway_summary.detail_json.snp_details`, individual reportable rows and
each special consumer above. For the allergy AOC1 aggregate, place a
synthetic single-copy carrier in the opposite frame from the declared risk
allele. Assert a stored dosage contribution of one and a reference control
of zero. For the celiac combined result, pair an unresolved DQ2 proxy with
a verified reference DQ8 proxy, then swap which proxy is unresolved. Assert
that the reopened `celiac_combined.detail_json.state` is indeterminate in
both cases. Its stored and displayed explanation must say that the proxy was
observed and its strand could not be verified. Exercise a genuinely untyped
proxy separately and verify its missing-coverage wording. Both states
withhold positive and rule-out text. Check a fully verified pair separately. Apply this same distinction
between unresolved coverage and verified reference across aggregate readers.
Exercise MT-RNR1 and LHON with their existing
no-complement controls, plus a literal indel, a build-36 reverse chain
segment and a parser no-call. Evidence of performance in these fixtures
validates software behavior only. Public locus conflicts remain with #2301.

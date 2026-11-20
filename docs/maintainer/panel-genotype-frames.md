# Which strand does a categorical panel key use?

**Proposed decision for #2716.** Preserve the source call and its verified
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
   order and perform one lookup. A known-frame match yields its complete
   curated interpretation. An unmodelled alternate or unsupported allele
   leaves the interpretation indeterminate. Keep an observed unresolved call
   distinct from an absent marker, no-call and verified non-carrier. Map an
   I/D or slash-delimited token only through its reviewed locus-specific
   encoding. Preserve the existing mtDNA rules that prohibit complementation.
4. The proposed default for an unknown observed or key frame is to withhold
   the affected interpretation, even if two possible keys have the same
   category. Withhold dependent text, aggregate contributions and derived
   actions. Keep independently supported results visible. A neutral coverage
   record may explain that a call was present and its frame could not be
   verified. The alternative requires every admissible orientation to resolve
   to the same complete output, including membership, wording, citations and
   every downstream contribution. The maintainer selects the policy before
   implementation.

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

Today `lookup_by_genotype` tries the observed spelling and reversed order,
then their complements for eligible nucleotide calls. The palindromic guard
in the eight scorers compares categories. The table below inventories all
133 `pathways[].snps[].genotype_effects` rows in their shipped panels on
`6ec3ba122c5eb319e63b4aa71bf8f39471077c5b`. None declares a typed key frame.
Existing locus-specific evidence nevertheless establishes some conventions,
including the fitness plus-strand fixture for rs1800012 and the nomenclature
audit of the methylation row for rs1051266.

| Panel | Rows | Rows that model both a homozygote and its complement | Other consumers of the mapping |
| --- | ---: | --- | --- |
| Allergy | 14 | rs144012689, rs1061235, rs1049793 | Celiac, histamine and cross-module findings consume scored markers. |
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
versioned GRCh37 reference and its original key convention. Existing raw
calls and findings need a verifiable chain from their source file, any
coordinate transform and the exact panel revision. Reconstruct only the
parts that can be proved. Withhold unsupported interpretations on every read
path, including stored pathway detail, reports and exports. A saved category
or file-format label alone cannot certify an earlier calculation. After
verification, rerun the affected producer; only successful publication
replaces the corresponding prior result. Preserve independent findings.

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
| The same AA has unknown or contradictory provenance | Apply the selected policy. It cannot borrow the verified AA effect. A separately verified positive in the same sample is still present. |
| Forward CT and TC; panel GA and AG | Verify identical complete stored heterozygote output and membership for each order. Conflict between order aliases withholds the affected row. |
| Forward CC (`hom_ref`), missing marker, and no-call | CC resolves to panel GG and produces no carrier-gated finding. Assert the baseline in the relevant pathway detail, the absence of the carrier text/action and the presence of the independent positive. The two missing cases retain their distinct coverage states. |
| Reopened historical database, then verified reanalysis | Test first reads before reanalysis, atomic replacement on success, a failed rerun, and an unaffected control. A reported category alone cannot clear unknown-frame state. |

Run the same cases through both vendor formats for supported forward calls.
Check values after reopening the sample database, including nonempty
`pathway_summary.detail_json.snp_details`, individual reportable rows and
each special consumer above. Exercise MT-RNR1 and LHON with their existing
no-complement controls, plus a literal indel, a build-36 reverse chain
segment and a parser no-call. Evidence of performance in these fixtures
validates software behavior only. Public locus conflicts remain with #2301.

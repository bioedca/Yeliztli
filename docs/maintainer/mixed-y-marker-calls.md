# Mixed calls at Y lineage markers

For #2472, treat a call containing two different bases at an eligible Y defining marker as **missing evidence for that marker**. Preserve the imported value. Count a pure derived base once, whether the export spells it once or twice. A pure, established ancestral base still supplies conflicting evidence. This is a proposal for maintainer decision under [#2709](https://github.com/bioedca/Yeliztli/issues/2709).

## What the supported exports document

| Export | Manufacturer evidence | Expected import behavior and limits |
| --- | --- | --- |
| 23andMe v3 | The general raw-data guide describes single alleles at most male X/Y sites. A v3-specific mixed-Y rule was not found. | The build-36 parser preserves single, doubled and mixed spellings. Apply the locus check after verified lift-over to GRCh37. |
| 23andMe v4 | Same guide. A v4-specific mixed-Y rule was not found. | Build-37 parser preserves the spellings. Exact marker coverage and the cause of a mixed call are unknown. |
| 23andMe v5 | Same guide. A v5-specific mixed-Y rule was not found. | Build-37 parser preserves the spellings. The bundle's four v5-era coverage exemplars do not certify every v5 export. |
| AncestryDNA v2.0 | The download guide describes paired allele columns on the forward strand of GRCh37 and mentions the numeric X/Y labels. It gives no Y-specific guarantee for doubling, mixed calls, or code 25. | The parser maps 24 to Y and 25 to X. Identical allele columns yield a doubled call, different bases a mixed call, and a zero in either column a no-call. |

The guides describe their formats generally. No reviewed manufacturer document establishes how frequently a mixed call appears at any of the 154 shipped markers in any supported version. Seventeen synthetic parser controls exercise those formats. The build-36 control checks input spelling only. [23andMe article 42965202914711](https://support.23andme.org/hc/en-us/articles/42965202914711-How-23andMe-Reports-Genotypes) (accessed 2026-11-19). [Ancestry article 53933317283603](https://help.ancestry.com/hc/en-us/articles/53933317283603-Downloading-DNA-Data) (accessed 2026-11-19).

## Which markers qualify

Bundle 1.1.30 on GRCh37 contains 154 distinct Y marker IDs at 154 positions, including 135 rsIDs and 19 vendor-internal IDs. All positions fall between 2,655,180 and 23,973,594 inclusive. None overlaps GRCh37 Y PAR1 (10,001–2,649,520) or PAR2 (59,034,050–59,363,566). Check the source row's chromosome, normalized position and identifier together. Exclude unverified placements, PAR rows and unrelated markers from this rule. An internal ID requires an established mapping for the incoming source. Reference placement alone does not establish unique probe binding. [GRC region report for GCF_000001405.25](https://ftp.ncbi.nlm.nih.gov/genomes/all/GCF/000/001/405/GCF_000001405.25_GRCh37.p13/GCF_000001405.25_GRCh37.p13_assembly_regions.txt) (accessed 2026-11-19).

Today the assignment query looks up Y marker IDs across the entire selected table. In the synthetic R1b1a1a fixture, moving the terminal row to X, to Y PAR1 or one base along Y still yielded 20/20. The separate placement defect affects pure calls and needs a regression check under [#2734](https://github.com/bioedca/Yeliztli/issues/2734). A mixed-call change can proceed independently when it makes its input scope explicit. Preserve generic allele matching, mtDNA position handling, ordinary imported genotypes and the existing XY eligibility gate.

## How the alternatives change a result

The columns below show the named terminal, matched/total defining markers on the returned path, and the software's confidence score. This score is a path coverage fraction. It gives no probability that a paternal lineage is correct. Each row perturbs one marker on an otherwise derived synthetic path.

| Marker changed to mixed | Current output | Treat mixed as missing (proposed) | Treat mixed as conflict |
| --- | --- | --- | --- |
| Sole R1b1a1a terminal marker, rs9786153 | R1b1a1a, 20/20, 1.0 | R1b1a, 19/19, 1.0 | R1b1a, 19/19, 1.0 |
| One of two R1b1a markers, rs7893073 | R1b1a1a, 20/20, 1.0 | R1b1a1a, 19/20, 0.95 | R1b1, 17/17, 1.0 |
| Audited B marker on a path to B2, rs2032599 | B2, 3/3, 1.0 | B2, 2/3, 0.6667 | Y-Adam, 0/0, 0.0 |

Measured on the unchanged production path. The missing column uses an actual absent row and a no-call, which agreed. The conflict column uses the known pure ancestral comparator to measure the existing conflict branch. These two columns predict what a new mixed-call classification would do. They do not measure new production code. A surviving descendant can retain its name while its support fraction falls. A withheld leaf can leave a shorter path with a score of 1.0.

At the leaf, pure derived single or doubled C yielded 20/20. Pure ancestral T, absent and no-call each fell back to the parent at 19/19. The issue's original AA also fell back, but the bundled curation identifies **T** as ancestral at this locus. Both the original synthetic AC and the source-pair CT incorrectly received full credit. In the B2 witness, independent descendant support distinguishes missing evidence from an affirmative contradiction. The manufacturer's strings alone cannot establish which base belongs to a particular lineage in a mixed record.

Peng et al. excluded Y sites with heterozygous calls when constructing a tree from an array study of 117 males. That site-level quality control supports withholding positive credit. It does not select Yeliztli's missing-versus-conflict behavior. Neither it nor the manufacturer's guides establish a vendor-specific failure cause or clinical interpretation for an individual. [PMID:24281365](https://pubmed.ncbi.nlm.nih.gov/24281365/) (accessed 2026-11-19).

## Acceptance after the decision

1. Pin the three witnesses, their pure single/doubled derived, pure ancestral, mixed, absent and no-call controls, and both raw and populated annotated sources. Assert terminal name, matched count, denominator and confidence, including the B2 and internal-marker cases where the options differ.
2. Parse synthetic inputs from both vendors before scoring. Include a lowercase/reversed mixed spelling, an Ancestry partial no-call, an X/PAR row and a build-36 case that goes through the actual supported lift-over path. Keep a non-XY eligibility control and the existing mixed-mtDNA and generic heterozygous-match tests.
3. Scope the behavior to ordinary A/C/G/T spellings at eligible defining SNPs. An indel, invalid spelling or ambiguous merge sentinel must never earn a derived vote by string containment. Keep unsupported formats and causes unclassified. Revisit source-specific exceptions only with public manufacturer evidence and a test that distinguishes the exception.

The reproduction script, source revisions, full control outputs and query record are in `data/science-evidence/2026-11-19-mixed-y-2709/`. The maintainer decision is whether an ambiguous marker may be bypassed when independent descendant evidence exists, and whether either supported vendor needs a documented exception.

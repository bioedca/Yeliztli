# PCSK9 protein-truncating variants and FH status — evidence for issue #2064

## Scope

This packet supports one classification rule in `backend/analysis/cardiovascular.py`: a *PCSK9*
nonsense or frameshift variant (an exact `stop_gained` or `frameshift_variant` token) is not
familial hypercholesterolemia (FH) evidence. It supports two claims:

1. FH-causing *PCSK9* variants act by gain of function.
2. *PCSK9* nonsense and frameshift variants lower LDL cholesterol.

Canonical splice-site variants are **not** covered by the rule. No source below measures them,
and a skipped exon can preserve the reading frame, so a splice token alone does not establish
loss of function. *PCSK9* splice-site records keep the module's existing FH criteria. (APOB's
override, from #2057, does include splice tokens.)

Repository base: `bbebbe9ac2f47bcac270ebc9e00a84f50ac1217b`

Access date: `2026-10-11`

## Evidence ladder and queries

The `evidence` workflow ran on 2026-10-11 and returned verdict `supported` (confidence:
moderate). An adversarial refutation pass did not overturn it. Every search it issued is
recorded in `data/science-evidence/2026-10-11-pcsk9-ptv-low-ldl-2064/queries.json`.

| Source | Result |
| --- | --- |
| Consensus | Bio-research plugin endpoint, free tier (3 results/query, no DOIs returned) |
| Scholar Gateway | `ACCESS_DENIED` three times; the agent fell back to NCBI E-utilities, Crossref and Europe PMC |
| PubMed | Search and bibliographic verification |
| Scite | Surfaced a second "erratum" for Cohen 2005 (DOI:10.1038/ng0407-439); Crossref shows it is a commentary, not a correction |
| Crossref | Identified DOI:10.1016/j.atherosclerosis.2025.119569 as a congress abstract |
| ClinGen CSpec registry | `https://cspec.genome.network/cspec/api/svis?detail=high`: no PCSK9 specification |

The claims rest on the primary papers below, verified directly, not on how many tools answered.

## Primary sources and claim mapping

| Claim | Source | Group / cohort |
| --- | --- | --- |
| 1 | Abifadel M et al. *Mutations in PCSK9 cause autosomal dominant hypercholesterolemia.* Nat Genet 2003. `PMID:12730697`; `DOI:10.1038/ng1161` | Paris; FH kindreds |
| 1 | Cunningham D et al. *Structural and biophysical studies of PCSK9 and its mutants linked to familial hypercholesterolemia.* Nat Struct Mol Biol 2007. `PMID:17435765`; `DOI:10.1038/nsmb1235` | Pfizer; biophysics (D374Y binds the LDL receptor more tightly) |
| 1 | Di Taranto MD et al. *Identification and in vitro characterization of two new PCSK9 Gain of Function variants found in patients with Familial Hypercholesterolemia.* Sci Rep 2017. `PMID:29127338`; `DOI:10.1038/s41598-017-15543-x` | Naples / Bilbao; functional assays |
| 2 | Cohen J et al. *Low LDL cholesterol in individuals of African descent resulting from frequent nonsense mutations in PCSK9.* Nat Genet 2005. `PMID:15654334`; `DOI:10.1038/ng1509` | UT Southwestern; Dallas Heart Study (Y142X, C679X) |
| 2 | Hooper AJ et al. *The C679X mutation in PCSK9 is present and lowers blood cholesterol in a Southern African population.* Atherosclerosis 2007. `PMID:16989838`; `DOI:10.1016/j.atherosclerosis.2006.08.039` | Perth / Cape Town; Zimbabwean women |
| 2 | Fasano T et al. *A novel loss of function mutation of PCSK9 gene in white subjects with low-plasma low-density lipoprotein cholesterol.* Arterioscler Thromb Vasc Biol 2007. `PMID:17170371`; `DOI:10.1161/01.ATV.0000255311.26383.2f` | Modena / Palermo; frameshift in white Italian subjects |
| 2 | Dron JS et al. *Association of Rare Protein-Truncating DNA Variants in APOB or PCSK9 With Low-density Lipoprotein Cholesterol Level and Risk of Coronary Heart Disease.* JAMA Cardiol 2023. `PMID:36723951`; `DOI:10.1001/jamacardio.2022.5271` | Broad / MGH; only the UK Biobank arm is independent |

All sources were accessed 2026-10-11.

**Independence.** Each claim has at least two sources from different groups and cohorts that
do not share an upstream assertion.
- Cohen 2006 (`PMID:16554528`, ARIC) and Zhao 2006 (`PMID:16909389`) agree with claim 2.
  They come from the same laboratory as Cohen 2005, so they are not counted.
- Dron 2023's NHLBI arm includes ARIC, so only its UK Biobank arm is treated as independent.
- The documentation cites Cohen 2005 and Hooper 2007, two groups and two cohorts.

**What is not claimed.**
- DOI:10.1016/j.atherosclerosis.2025.119569 is a congress abstract on APOB/PCSK9 variant
  interpretation. The ClinGen CSpec registry (accessed 2026-10-11) lists FH specifications for
  LDLR and APOB only, with no PCSK9 entry. This packet therefore cites no ClinGen PCSK9 rule.
- Not every PCSK9 missense variant raises LDL. R46L lowers it (`PMID:20579540`). That is why
  only exact truncating tokens move a record out of FH; PCSK9 missense records keep the
  module's existing FH criteria.

## Corrections and retractions

PubMed EFetch for all seven PMIDs was retrieved on 2026-10-11. Its
`MedlineCitation/CommentsCorrectionsList` was inspected for erratum, retraction,
expression-of-concern, update and reprint links. The extract, with the request and the raw
response SHA-256, is in
`data/science-evidence/2026-10-11-pcsk9-ptv-low-ldl-2064/pubmed-comments-corrections-extract.json`.

- `PMID:15654334` (Cohen 2005) links an erratum: Nat Genet 2005;37(3):328. The indexed title
  now reads "individuals of African descent", consistent with that erratum correcting the
  title. The erratum text was not read here, and the paper is not retracted.
- None of the other six records links a correction or retraction.

These results record what PubMed returned on 2026-10-11, not a guarantee that no later notice
exists.

## ClinVar note

At the time of access, ClinVar held single-submitter P/LP assertions for PCSK9 truncating
variants that apply null-variant reasoning (no primary data cited), for example a frameshift
recorded as Pathogenic for familial hypercholesterolemia. Their ClinVar significance is
therefore not evidence that a PCSK9 truncating variant causes FH. This is the data trap the
rule guards against.

## Payload format and usage

- **`pubmed-esummary.json`** is the raw NCBI E-utilities ESummary JSON response for the seven
  PMIDs (`header`: esummary, version `0.3`). Request:
  `https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi?db=pubmed&id=12730697,15654334,16989838,17170371,17435765,29127338,36723951&retmode=json`.
- **The corrections extract** keeps only the fields named above, with no abstracts or article
  text.

The packet contains citation and link metadata only. It holds no participant-level data,
genotypes, credentials or restricted material. NCBI's
[Website and Data Usage Policies and Disclaimers](https://www.ncbi.nlm.nih.gov/home/about/policies/)
and the [E-utilities usage requirements](https://www.ncbi.nlm.nih.gov/books/NBK25497/) (both
accessed 2026-10-11) apply. Publisher-contributed material keeps its own rights, and the
documentation paraphrases the mapped claims and links to the primary sources.

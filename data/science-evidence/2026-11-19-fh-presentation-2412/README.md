# Evidence for the presentation of reclassified cardiovascular findings

This packet supports the proposed design in issue #2412. The software baseline
is `cf08f712a130591598eae00648a1b8dce2d0b384`. The question is what the
product can attribute to ClinVar and what it can attribute to its own existing
APOB and PCSK9 truncation rules. The evidence supports a direction of effect
in the studied groups. Individual LDL, diagnosis, treatment and a
condition-specific ClinVar classification each require information beyond that
direction.

## Primary evidence and independence

| Presentation claim | Source and scope |
| --- | --- |
| ClinVar distinguishes a submission, a variant aggregate and a variant–condition aggregate. Flattening a VCV classification and its condition list loses the pairing. | Landrum et al. [PMID:29165669](https://pubmed.ncbi.nlm.nih.gov/29165669/) (accessed 2026-11-19), [ClinVar data model](https://www.ncbi.nlm.nih.gov/clinvar/docs/data_model/) (accessed 2026-11-19) and [ClinVar identifiers](https://www.ncbi.nlm.nih.gov/clinvar/docs/identifiers/) (accessed 2026-11-19). These are the defining paper and implementing registry from the same authority. |
| A low-LDL interpretation has primary-study support for APOB protein truncations. | Peloso et al. [PMID:30939045](https://pubmed.ncbi.nlm.nih.gov/30939045/) (accessed 2026-11-19) studied families and coronary-disease cohorts. Dron et al. [PMID:36723951](https://pubmed.ncbi.nlm.nih.gov/36723951/) (accessed 2026-11-19) also observed the association in the separately enrolled UK Biobank. Despite shared authors, those participant datasets and direct analyses are distinct. Count only Dron's UK Biobank arm here. |
| The PCSK9 nonsense findings have support from independently measured LDL in separate populations. | Cohen et al., Dallas Heart Study, [PMID:15654334](https://pubmed.ncbi.nlm.nih.gov/15654334/) (accessed 2026-11-19), and Hooper et al., Southern African cohort, [PMID:16989838](https://pubmed.ncbi.nlm.nih.gov/16989838/) (accessed 2026-11-19). These investigators each measured the relationship in their own participants. |
| Frameshifts are also represented in the evidence underlying the existing PCSK9 rule. | Fasano et al. reported a frameshift in an Italian family [PMID:17170371](https://pubmed.ncbi.nlm.nih.gov/17170371/) (accessed 2026-11-19). Dron et al. studied truncating variants as a group in UK Biobank [PMID:36723951](https://pubmed.ncbi.nlm.nih.gov/36723951/) (accessed 2026-11-19). The observations have different participants and upstream data. The grouped result supplies no variant-specific family replication. |

The [APOB](../2026-07-31-apob-low-ldl-2075/README.md) and
[PCSK9](../2026-10-11-pcsk9-ptv-low-ldl-2064/README.md) packets retain the
underlying source snapshots and record the limits of each rule. The
[attribution audit](../2026-11-09-apob-citation-attribution-2394/README.md)
documents why the congress abstract cannot establish a released expert-panel
specification. Each earlier packet retains its original retrieval dates.
The proposed presentation introduces no new consequence classes. A synthetic
witness for each existing rule lives in `TestAPOBConditionScope` and
`TestPCSK9ConditionScope` in `tests/backend/test_cardiovascular.py`.

## Discovery and payloads

- Consensus query — `Primary human genetic studies APOB protein truncating variants low LDL hypobetalipoproteinemia and PCSK9 loss of function low LDL human cohorts distinguishing familial hypercholesterolemia functional direction`.
- Scite query — `("PCSK9" OR "APOB") AND ("hypobetalipoproteinemia" OR "low LDL cholesterol") AND ("truncating" OR "nonsense")`.
- NCBI Entrez EFetch verified the six named papers. The two exact requests and sanitized citation/link field copies are in `data/science-evidence/2026-11-19-fh-presentation-2412/raw/pubmed.json`.
- The indexed copies of NCBI's live documentation identified the scopes recorded in `data/science-evidence/2026-11-19-fh-presentation-2412/raw/clinvar-record-scope.json`. Direct browser access served a verification page. This audit used documentation only and queried no individual ClinVar record.

Cohen's PubMed record links an erratum, [DOI:10.1038/ng0305-328c](https://doi.org/10.1038/ng0305-328c)
(accessed 2026-11-19), and two comments. The PubMed record classifies the
linked PMID 17392801 as a comment. None of the six returned records links a
retraction. No other paper in this retrieval links a correction. The retained
field projection and earlier full sanitized snapshots let readers check what
the indexes reported on their respective access dates.

PubMed and ClinVar documentation are live sources. The UTC retrieval date
identifies this snapshot. The packet includes citation metadata and compressed
descriptions of public registry field definitions. NCBI requests attribution
for government-created content, and contributed material retains its applicable
rights. [NCBI usage policy](https://www.ncbi.nlm.nih.gov/home/about/policies/) (accessed 2026-11-19).

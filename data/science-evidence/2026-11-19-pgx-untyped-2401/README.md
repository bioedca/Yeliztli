# Evidence for the partial-call decision in #2401

Source access was on 2026-11-19 UTC. The claim under review is whether a
drug recommendation is identifiable from an incomplete consumer-array
observation. The studies below investigate assay limitations. None tested a
Yeliztli dosing rule or established a clinical benefit from choosing the
lowest possible activity. No dose is proposed.

## Evidence by question

| Question | Primary evidence and interpretation |
| --- | --- |
| Can SNP coverage settle CYP2D6 copy number and structural identity? | PharmVar identifies deletions, duplications and hybrids as classes that require structural characterization. Chen et al. evaluated structural calls using reference genomes and whole-genome sequencing. Frear et al. measured structural variation using multiple copy-number assay sites in a separate clinical laboratory data collection. Both primary studies measured these structures directly, using distinct samples and methods. Their agreement supports the existence of relevant unmeasured states. It gives no per-user probability and says nothing about the clinical benefit of withholding a card. [PMID:37669183](https://pubmed.ncbi.nlm.nih.gov/37669183/) (accessed 2026-11-19), [PMID:33462347](https://pubmed.ncbi.nlm.nih.gov/33462347/) (accessed 2026-11-19), [PMID:38372405](https://pubmed.ncbi.nlm.nih.gov/38372405/) (accessed 2026-11-19). |
| Can an unphased NUDT15 observation determine the diplotype? | Yu et al. used targeted sequencing to distinguish cis from trans in a Taiwanese pediatric leukemia series. A separate long-read study phased reference materials and resolved diplotypes left ambiguous by short reads. The first used clinical samples and the second independently measured physical phase in reference materials. Neither result relies on the other's cohort or a shared phenotype lookup for the phase observation. This supports retaining uncertainty for unphased array tokens. It does not establish dosing for a patient with missing phase. [PMID:32873882](https://pubmed.ncbi.nlm.nih.gov/32873882/) (accessed 2026-11-19), [PMID:36057977](https://pubmed.ncbi.nlm.nih.gov/36057977/) (accessed 2026-11-19). |
| Which NUDT15 names apply? | ClinPGx documents PharmVar's consolidation of historical `*2` into `*3.002`. The corrected CPIC guideline removes the retired `*2` example. The CPIC allele-definition query returns core names `*3`, `*6` and `*9`; it does not return a separate `*3.002` row. Registry nomenclature and the guideline share curation and count only toward the definition of the name. [ClinPGx February 2026 release note](https://blog.clinpgx.org/cpic-guideline-for/) (accessed 2026-11-19), [PMID:42050374](https://pubmed.ncbi.nlm.nih.gov/42050374/) (accessed 2026-11-19). |
| What do the drug-specific authorities cover? | CPIC's opioid guideline covers codeine and tramadol for known CYP2D6 results. The 2026 antiemetic update distinguishes the ultrarapid and normal branches from intermediate, poor and indeterminate branches with no recommendation (Table 2). The corrected 2025 thiopurine update considers TPMT and NUDT15 together, drug and clinical indication. These are descriptions of the guidelines' scopes, not independent trials of guidance from an unknown result. [PMID:33387367](https://pubmed.ncbi.nlm.nih.gov/33387367/) (accessed 2026-11-19), [CPIC antiemetic guideline, PMID:41979467](https://files.cpicpgx.org/data/guideline/publication/ondansetron/2026/41979467.pdf) (accessed 2026-11-19), [PMID:41618934](https://pubmed.ncbi.nlm.nih.gov/41618934/) (accessed 2026-11-19). |

## Source records and retrieval

- The defining PharmVar record names `NUDT15*3.002`, lists legacy label
  `*2` and is active. The former core `NUDT15*2` record is inactive.
  `data/science-evidence/2026-11-19-pgx-untyped-2401/raw/pharmvar-nudt15-names.json`
  retains selected identity fields from both records at registry version
  6.2.29. The gene lookup used `/api/genes/search/findGeneBySymbol?symbol=NUDT15`
  followed by `/api/genes/22812/haplotypes`. The retained fields are factual
  identifiers and status values. No grant to redistribute the full database
  is assumed. [PharmVar:PV03044](https://www.pharmvar.org/api/haplotypes/3044)
  (accessed 2026-11-19), [PharmVar:PV01598](https://www.pharmvar.org/api/haplotypes/1598)
  (accessed 2026-11-19).

The thiopurine guideline's “Other considerations” section permits using the
available gene with a caveat if only one of the two genes yielded a result.
The proposed conflict rule addresses positive but ambiguous information
from the other gene. The single-gene rule has its own acceptance control.
[PMID:41618934](https://pmc.ncbi.nlm.nih.gov/articles/PMC12997511/) (accessed 2026-11-19).

The [two CPIC table and section extracts](raw/cpic-guideline-facts.json)
retain the source facts used for the ondansetron eligibility comparison
and the thiopurine single-gene caveat, together with PDF page locators
and SHA-256 digests. Table 2 of the thiopurine guideline conditions the
mercaptopurine intermediate branch on the starting regimen. The extracts
record bibliographic metadata and factual summaries only. The original
articles remain subject to their publishers' terms.

- `data/science-evidence/2026-11-19-pgx-untyped-2401/raw/pubmed.json`
  retains selected public fields from NCBI ESummary and linked editorial
  notices from EFetch. Both used the comma-separated `id` list in the file,
  with `db=pubmed`, ESummary `retmode=json` and EFetch `retmode=xml`.
  These are bibliographic metadata. Article text, affiliations, patient data
  and genotype records are excluded. Article copyrights remain with their
  respective authors or publishers. [NLM terms](https://www.ncbi.nlm.nih.gov/home/about/policies/)
  (accessed 2026-11-19).
- `data/science-evidence/2026-11-19-pgx-untyped-2401/raw/cpic-nudt15-names.json`
  is the public response to
  [CPIC allele_definition](https://api.cpicpgx.org/v1/allele_definition?genesymbol=eq.NUDT15&name=in.%28%2A3%2C%2A3.002%2C%2A6%2C%2A9%29&select=id%2Cname%2Cgenesymbol&order=id)
  (accessed 2026-11-19). It contains IDs, core names and gene symbols.
  The endpoint exposes no release field in this projection. The access date
  and record IDs identify this snapshot. CPIC dedicates its curated content
  under CC0 1.0 and lists the terms applying to its database and API.
  [CPIC licensing](https://www.clinpgx.org/page/cpicLicensing)
  (accessed 2026-11-19).
- Consensus used these two searches without additional filters:
  - `CYP2D6 copy number structural variation star allele limited genotyping missed allele clinical pharmacogenetics consensus recommendations AMP CYP2D6 testing NUDT15 indels thiopurine genotyping allele ascertainment CPIC PharmVar`
  - `NUDT15 diplotype ambiguity thiopurine common coding insertion deletion R139C cis trans primary functional haplotype pediatric leukemic cells NUDT15 genotype`
- Scite used `("CYP2D6" OR "NUDT15") AND ("allele selection" OR
  "genotyping recommendations" OR "structural variation" OR "diplotype ambiguity")`
  and `("thiopurine" OR "thiopurines") AND ("NUDT15") AND ("haplotype" OR
  "compound heterozygous" OR "2025 update")`. Linked PubMed records and
  guideline tables supply the evidence. NCBI Entrez E-Utilities returned the
  metadata. A Europe PMC full-text request returned HTTP 503; the indexed
  correction and original CPIC publication were available separately.
- NCBI and Scite checks identified two corrections. The Cyrius correction
  concerns copyright and licensing. [DOI:10.1038/s41397-022-00287-3](https://doi.org/10.1038/s41397-022-00287-3)
  (accessed 2026-11-19). The thiopurine correction revises Table 1's TPMT
  phenotype classification and removes the retired NUDT15 `*2` example.
  [DOI:10.1002/cpt.70298](https://doi.org/10.1002/cpt.70298)
  (accessed 2026-11-19). No retraction link appeared in the records checked.

## Reproducing the software comparison

At repository revision `cf08f712a130591598eae00648a1b8dce2d0b384`, compare
the CYP2D6, NUDT15 and TPMT/NUDT15 rows in
`backend/data/cpic/cpic_guidelines.csv` with their counterparts in
`tests/fixtures/seed_csvs/cpic_guidelines_seed.csv` and the `cpic_guidelines`
table in `tests/fixtures/mini_reference.db`. The seven distinct gene/drug
pairs in the proposal count the three joint thiopurine rows with their
corresponding NUDT15 drugs. The production CSV has 27 scoped rows. The seed
and mini fixture each have 21, with identical recommendation text for the
shared keys. Both omit the three NUDT15/thioguanine rows and the three joint
rows. An implementation must exercise production data or explicitly load
those rows when testing all three drugs and the joint case.
Gene and star-allele labels are source-native.
The audit performs no coordinate conversion and reads no sample. The existing
tests in `test_pharmacogenomics_cyp2d6_cnv.py`,
`test_pharmacogenomics_e1.py`, `test_pharmacogenomics_thiopurine_joint.py`
and `test_pharmacogenomics.py` exercise synthetic baseline behavior. They
cannot establish clinical effectiveness.

# ClinGen risk-allele tiers and ClinVar evidence levels — evidence for issue #2094

## Scope

This packet supports one rule in `backend/analysis/evidence.py::assign_clinvar_evidence_level`. A ClinVar value whose terms assert nothing stronger than an *Uncertain risk allele* is rated ★ PRELIMINARY. Two values qualify:
- `Uncertain risk allele`, including `Uncertain risk allele|risk factor`;
- `Uncertain significance/Uncertain risk allele`.

They no longer share the ★★ MODERATE cap that applies to *Likely* and *Established* risk alleles and to P/LP low-penetrance values. The term test is `clinvar_significance.is_uncertain_risk_allele_only`.

The rule rests on two claims:

1. The ClinGen Low Penetrance/Risk Allele Working Group grades risk alleles in three tiers of **decreasing confidence**: *Established*, *Likely*, *Uncertain*. *Uncertain* is the tier for **limited or conflicting evidence**.
2. ClinVar implements these terms "As recommended by ClinGen". Its aggregation treats *Uncertain risk allele* as an **uncertain-level** term that combines with *Uncertain significance*, while *Likely* and *Established* risk alleles sit with the pathogenic/risk terms.

The working group does not call the *Uncertain* tier a VUS. Its tiers correspond to the ACMG/AMP tiers by equivalent confidence (Figure 1). "*Established*" describes confidence in an association, not high penetrance. The rule keeps *Likely* and *Established* at the same ★★ cap. That is coarser than the paper's three tiers but does not contradict them.

Repository base: `85431fbb7d035152434987604661f4e0e1a70e98`

Access date: `2026-10-13`

## Evidence ladder

The `evidence` workflow ran on 2026-10-13, followed by a direct Scite check. Details are in `derived/evidence-ladder-record.json` and `queries.json`.

| Source | Result |
| --- | --- |
| Consensus | Bio-research plugin endpoint, free tier (abstracts only). Confirms the paper; the abstract does not name the tiers. |
| Scite | Paper indexed with no editorial notices; Smart Citations 2 supporting / 0 contrasting / 36 mentioning; full-text excerpts content-denied. |
| Scholar Gateway | `ACCESS_DENIED` twice; fell back to the primary full text. |
| PubMed / PMC | Citation verified; no CommentsCorrections links; full text read (PMC10939896). |

Workflow verdict: **withhold** (confidence moderate). The facts were verified and nothing refutes them. The verdict rests solely on the two-independent-source rule (see Independence).

## Primary sources and claim mapping

| Claim | Source | Evidence in this packet |
| --- | --- | --- |
| 1 | Schmidt RJ et al. *Recommendations for risk allele evidence curation, classification, and reporting from the ClinGen Low Penetrance/Risk Allele Working Group.* Genet Med 2024;26(3):101036. PMID:38054408 / DOI:10.1016/j.gim.2023.101036 (accessed 2026-10-13). | `raw/pmc-PMC10939896-excerpts.json`: verbatim "levels of confidence ranging from uncertain to likely to established" and "Risk alleles with limited or conflicting evidence … are classified as uncertain risk allele" |
| 2 | ClinVar, *Classifications used in ClinVar*, https://www.ncbi.nlm.nih.gov/clinvar/docs/clinsig/ (page last updated 2025-08-15; accessed 2026-10-13). | `raw/clinvar-docs-clinsig-excerpts.json`: "As recommended by ClinGen"; the "Uncertain significance AND Uncertain risk allele" aggregation; "Any pathogenic/risk term vs any uncertain term" conflict rule |

Correction/retraction check: PubMed efetch for PMID 38054408 shows no `CommentsCorrections` links of any kind, and Scite reports no editorial notices.

## Independence

The two sources are **not independent**. ClinVar's terms were "introduced by ClinGen", and the paper says its "recommended terminology has recently been made available in ClinVar".

Both claims here are **definitional**: what ClinGen's risk-allele terms mean and how its tiers are ordered, and how ClinVar's registry classifies them. Under the evidence rule as amended in #2431 (`CLAUDE.md` / `AGENTS.md`), a definitional claim is met by its defining document plus the authoritative registry that implements it, with their non-independence recorded. That is what this packet records. The rule does not extend to effect claims, and none is made: the change lowers an evidence rating; it says nothing about the size of any risk.

For context, every other source restates the same working-group assertion:
- Senol-Cosar et al. 2019 (PMID:31147632) shares authors with the paper and does not name the tiers.
- Asgarian et al. 2024 (PMID:39191897) only shows the term in use in ClinVar.

## Affected values

`derived/reference-clinvar-risk-allele-vocabulary.json` lists all 21 lower-penetrance and risk-allele strings in the reference ClinVar (20260707), with row counts and the rating each now receives. Only the three Uncertain-risk-allele strings (179 rows) change, from ★★ to ★.

## Retention and licences

- The PMC full text is licensed for text mining and fair use, so only verbatim excerpts are kept, with the SHA-256 of the full payload as fetched.
- The ClinVar documentation page is likewise kept as excerpts plus a SHA-256.
- The PubMed efetch record is kept with the publisher abstract and authors' contact details (email addresses) removed. The esummary record is kept whole.
- Excerpts are text extracted from the fetched markup: tags stripped, whitespace collapsed. Table cells in the ClinVar excerpts run together.
- `derived/` files are summaries and counts, not raw captures.

## Inventory

This `README.md`, `manifest.json`, and the 8 files that `manifest.json` lists with SHA-256:
- `queries.json`
- `raw/` (5): `pubmed-esummary-38054408.json`, `pubmed-efetch-38054408-sanitized.xml`, `pubmed-comments-corrections-extract.json`, `pmc-PMC10939896-excerpts.json`, `clinvar-docs-clinsig-excerpts.json`
- `derived/` (2): `evidence-ladder-record.json`, `reference-clinvar-risk-allele-vocabulary.json`

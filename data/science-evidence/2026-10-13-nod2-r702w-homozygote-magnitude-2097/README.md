# NOD2 R702W homozygote Crohn's risk magnitude — evidence for issue #2097

## Scope

This packet supports one panel correction in `backend/data/panels/gene_health_panel.json`, rs2066844 (NOD2 R702W), genotype `TT`.
- **Removed:** "~20-40 fold increased risk of Crohn's disease, particularly ileal involvement", together with the unsupported "Severely impaired muramyl dipeptide sensing" and "Compound heterozygosity … further increases risk" clauses.
- **New text** makes two claims:
  1. Carrying two NOD2 risk alleles is associated with a **higher** Crohn's disease risk than carrying one (gene dosage).
  2. Published two-allele estimates **pool every combination** of the three common NOD2 variants and are **not specific to R702W homozygotes**, so no fold-risk figure is given for this genotype.

The heterozygote text (`CT`/`TC`, "approximately 2-3 fold") is unchanged. The row now also cites PMID:15571588 and PMID:11910337, the sources of the pooled two-allele estimates.

Repository base: `d6f7060090116e10f20993231d9217cc0b68fa45`

Access date: `2026-10-13`

## Evidence ladder

The `evidence` workflow ran on 2026-10-13, followed by direct PubMed and Scite checks. Details are in `derived/evidence-ladder-record.json` and `queries.json`.

| Source | Result |
| --- | --- |
| Consensus | Surfaced the primary papers (abstract level). |
| Scholar Gateway | `ACCESS_DENIED`; fell back to NCBI E-utilities. |
| PubMed | Abstracts re-read; corrections checked for all six cited PMIDs: `CommentIn` only, no erratum, retraction or expression of concern. |
| Scite | All six papers indexed; no editorial notices. |

Workflow verdict: **mixed**. Claims 1–3 of the issue were supported. Its claim 4 premise, a "van Heel 2002 RR 58.9", was not found and is **not** encoded (see Withheld).

## Primary sources and claim mapping

| Claim | Source (accessed 2026-10-13) | Verbatim support (`raw/pubmed-abstract-excerpts.json`) |
| --- | --- | --- |
| 1 | Cuthbert AP et al. *Gastroenterology* 2002;122:867-74. PMID:11910337 / DOI:10.1053/gast.2002.32415 (UK/German case-control) | "Genotype relative risks were 3.0 for mutation heterozygotes and 23.4 for homozygotes or compound heterozygotes." |
| 1 | Lesage S et al. *Am J Hum Genet* 2002;70:845-57. PMID:11875755 / DOI:10.1086/339432 (French cohort) | "This observation confirmed the gene-dosage effect in CD." |
| 2 | Economou M et al. *Am J Gastroenterol* 2004;99:2393-404. PMID:15571588 / DOI:10.1111/j.1572-0241.2004.40304.x (meta-analysis) | "The OR in carriers of at least two alleles was 17.1 (95% CI: 10.7-27.2)." |
| 2 | Cuthbert 2002 (above) | "… 23.4 for homozygotes or compound heterozygotes." |

## Independence

- **Claim 1** rests on Cuthbert 2002 (UK/German case-control) and Lesage 2002 (French families and sporadic cases), which share no cohort. Pascoe 2007 (PMID:17457368, "These results confirm the dosage effect") also agrees, but it uses the CEPH/EPWG families of the Lesage/Hugot lineage, so it is **not** counted separately.
- **Claim 2** is a statement about what each published estimate pools. Each paper's own abstract states that its two-allele figure covers carriers of "at least two alleles" (Economou) or "homozygotes or compound heterozygotes" (Cuthbert). Economou's meta-analysis likely includes Cuthbert's data, so the two do not independently estimate a magnitude. That is why **no magnitude is encoded**: the panel only says that the pooled figures are not R702W-specific.

## Withheld

- **The issue's "van Heel 2002 RR 58.9".** It was not found. The matching van Heel 2002 NOD2 paper (PMID:12210321) reports a TDT association and no genotype relative risk.
- **Any R702W-homozygote-specific relative risk**, e.g. Pascoe 2007's OR 3.29, which comes from a family-ascertained design. No second verified, independent source agrees.
- **The per-allele ranking** of R702W against G908R and 1007fs, and **general-population penetrance**. These were not encoded because this change does not need them.

## Retention

- The PubMed efetch record is kept with abstracts and authors' contact details removed. The esummary record is kept whole.
- Abstract support is kept as short verbatim sentences.
- `derived/` files are summaries, not raw captures.

## Inventory

This `README.md`, `manifest.json`, and the 6 files that `manifest.json` lists with SHA-256:
- `queries.json`
- `raw/` (4): `pubmed-esummary.json`, `pubmed-efetch-sanitized.xml`, `pubmed-abstract-excerpts.json`, `pubmed-comments-corrections-extract.json`
- `derived/` (1): `evidence-ladder-record.json`

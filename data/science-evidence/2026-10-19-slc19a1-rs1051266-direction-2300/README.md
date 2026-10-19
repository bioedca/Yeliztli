# SLC19A1 rs1051266 direction of effect for #2300

## Claim and scope

`backend/data/panels/methylation_panel.json` used to score every A-bearing
rs1051266 genotype (His27 carriers) as `Moderate`, on the premise that His27
reduces folate transport. Methylation reports each pathway at the highest
category among its called SNPs. At MAF ~0.49, most samples therefore carried a
Folate & MTHFR call that rested on that one premise.

This packet supports two claims:

- **C1.** The direction of effect of rs1051266 on folate status is contested.
  Independent groups report opposite findings.
- **C2.** So the panel now scores **no** genotype at this locus. All four
  genotypes are `Standard`, the prose says the studies disagree, and
  `recommendation_text` records both sides.

**Not claimed:**
- that either direction is correct;
- that the locus has no effect on folate metabolism;
- anything about methotrexate response or disease risk. The row's other two
  citations concern those topics and are not used here as evidence for either
  direction.

## The conflict

The literature writes this SNP as `80G>A`; the panel writes it as `A80G`. Both
use the same base letter for the same residue: **80A is His27** and **80G is
Arg27**
(`data/science-evidence/2026-08-10-slc19a1-rs1051266-nomenclature-2023/`). So
the papers' 80AA is the panel's AA, and the panel prose names residues, not
bases.

| Direction | Source | Finding | Cohort, and authors' affiliation |
| --- | --- | --- | --- |
| His27 carriers have **higher** folate | PMID:19650776, Stanisławska-Sachadyn 2009, Ann Hum Genet | Women with GA or AA had higher red-cell folate than GG (P = 0.02); no association in men, or with serum folate or homocysteine | Healthy young adults from Northern Ireland |
| His27 carriers have **higher** folate | PMID:10993718, Chango 2000, Mol Genet Metab | 80AA/677CT had higher plasma folate than 80GG/677CT (P = 0.02) | 169 healthy subjects; INSERM U-308, France |
| His27 **lowers** cellular uptake | PMID:15964598, Yates & Lucock 2005, Life Sci | The 80A allele raised the extracellular-to-intracellular folate ratio | 156 patients; University of Newcastle, Australia (Lucock group) |
| His27 **lowers** cellular uptake | PMID:16750224, Dufficy 2006, Life Sci (already cited) | Dietary folate tracked red-cell folate in GG and GA, but not in AA | 119 retirement-village residents; University of Newcastle, Australia (Lucock group) |

**Independence.** The two higher-folate studies share no authors with each other
or with the other side. The two lower-uptake studies come from one group: Yates Z
and Lucock M(D) appear on both. So that side counts **once**. The Dufficy
finding is also an absence of correlation, not a measured reduction.

**Why that is enough to withhold.** C1 claims only that the evidence conflicts,
and opposite findings on folate status from independent groups establish that.
This is a wellness-panel locus at evidence level 1, not a clinical call. The
change only stops the panel asserting a direction. It does not assert the
opposite one.

## What changed in the panel

- `GG`, `GA`, `AG` and `AA` are all `Standard`. Each summary names the residues
  and ends with "Published studies disagree on whether His27 or Arg27 lowers
  folate status, so this genotype is not scored as an effect."
- `recommendation_text` states that independent cohorts report higher red-cell or
  plasma folate in His27 carriers, while another group reports lower cellular
  uptake. It also says the locus is context only and does not raise the pathway
  result. The `G80A` / `c.80A>G` nomenclature note from #2023 is kept.
- `pmids` gains PMID:19650776, PMID:10993718 and PMID:15964598, beside the
  existing three.
- **Unchanged:** `risk_allele`/`ref_allele` (the `A`/`G` dosage frame), the label,
  `hgvs_protein`, and the evidence level.

A `Standard` SNP stores no `snp_finding`, so the summary in the pathway's
`snp_details` is now where the disagreement reaches the user. Samples scored
before this change keep their stored result until methylation is run again. That
staleness class is tracked by #2435.

## Evidence route

- **Consensus:** two searches. Three of the four studies surfaced through them;
  PMID:16750224 was already cited by the row. See
  `raw/consensus-searches-2026-10-19.json`.
- **Scite:** unavailable, because its monthly MCP quota is spent until 2026-11-03
  (`raw/scite-unavailable-2026-10-19.json`).
- **PubMed:**
  - The three new PMIDs were resolved by author and title search and
    title-matched.
  - Abstracts were read for all six of the row's PMIDs.
  - CommentsCorrections was read from the retained efetch XML. There are no
    corrections, errata, expressions of concern or retractions. PMID:10993718
    has a CommentIn link (PMID:11461197), which is a published comment.

## Citations

- Stanisławska-Sachadyn A, et al. *Ann Hum Genet.* 2009. PMID:19650776 /
  DOI:10.1111/j.1469-1809.2009.00529.x (accessed 2026-10-19).
- Chango A, et al. *Mol Genet Metab.* 2000. PMID:10993718 /
  DOI:10.1006/mgme.2000.3034 (accessed 2026-10-19).
- Yates Z, Lucock M. *Life Sci.* 2005. PMID:15964598 /
  DOI:10.1016/j.lfs.2005.02.029 (accessed 2026-10-19).
- Dufficy L, et al. *Life Sci.* 2006. PMID:16750224 /
  DOI:10.1016/j.lfs.2006.05.009 (accessed 2026-10-19).

## Inventory

`queries.json` lists all 7 files under `raw/` with their sizes and sha256.

| Kind | Files |
| --- | ---: |
| PubMed esearch (Chango 2000, Yates 2005, Stanisławska-Sachadyn 2009) | 3 |
| PubMed esummary and efetch for all six PMIDs | 2 |
| Discovery records (Consensus searches, Scite outage) | 2 |
| **Total** | **7** |

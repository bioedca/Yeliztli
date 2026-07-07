# Variant detail

--8<-- "health-disclaimer.md"

Click any variant in the [Variant Explorer](variant-explorer.md) to open a side panel with a
summary, then **Open full detail** for the full page, organised into tabs:

1. **Overview** — genomic location, HGVS notation, and transcript details (the MANE Select
   transcript is flagged), a ClinVar summary, and a **Key Scores** block: gnomAD global allele
   frequency with a rare / ultra-rare flag, plus CADD, REVEL, SIFT, and PolyPhen-2.
2. **Population** — gnomAD allele frequencies across subpopulations (Global, African,
   Latino/Admixed, Ashkenazi Jewish, East Asian, European, Finnish, South Asian), the gnomAD
   homozygote count, and a rarity note.
3. **Protein** — the variant's protein change (HGVS p.) and a link to the gene's full
   **[Gene Detail](gene-detail.md)** page, where the Nightingale protein-domain diagram is
   rendered. The diagram is not shown inline here, and this page does not mark the variant's
   position on it.
4. **Clinical** — the ClinVar record (clinical significance, review stars, accession, and
   reported conditions), any evidence-conflict callout, the full in-silico prediction panel
   (CADD, SIFT, PolyPhen-2, REVEL, MutPred2, VEST4, MetaSVM, MetaLR, GERP++, phyloP, MPC,
   PrimateAI, plus an ensemble-pathogenic flag), GTEx eQTL and SpliceAI context badges, and
   gene–disease associations (MONDO/HPO, plus OMIM if you've added a key).
5. **Literature** — *planned; not yet implemented.* A per-variant PubMed literature search
   (keyed by gene and phenotype) is slated for a future release; today this tab shows a
   placeholder only. (PubMed literature *is* used elsewhere — for clinical **findings** — but is
   not wired into this tab.)
6. **Genome** — an embedded IGV view of a ~10 kb window around the variant with the default
   annotation tracks, plus a link into the full **[Genome Browser](genome-browser.md)**.

You can generate a single-variant **evidence card** (PDF or PNG) from this page, and jump
straight to the variant in the **[Genome Browser](genome-browser.md)**.

## Understanding HGVS notation

The Overview and Protein tabs name the variant in **HGVS notation** — the international standard
for describing sequence variants.[^hgvs] It appears as two strings:

- **Coding (`c.`)** — the change on the gene's **coding-DNA** reference sequence; `>` marks a
  single-base **substitution**. So `c.665C>T` means "at coding-DNA position 665, the reference
  base **C** is replaced by **T**."
- **Protein (`p.`)** — the predicted **amino-acid change**, written with three-letter amino-acid
  codes. So `p.Ala222Val` means "at protein position 222, **Ala**nine is replaced by **Val**ine."

Both describe the *same* variant — one at the DNA level, one at the protein level. The numbers are
relative to the variant's transcript (the MANE Select transcript is flagged in the Overview tab),
so the same change can carry different `c.` / `p.` numbers on different transcripts. See the
[HGVS nomenclature reference](https://hgvs-nomenclature.org/) for the full rules.

[^hgvs]: [HGVS Recommendations for the Description of Sequence Variants: 2016 Update](https://doi.org/10.1002/humu.22981) (den Dunnen et al., 2016, *Human Mutation*; [PMID 26931183](https://pubmed.ncbi.nlm.nih.gov/26931183/)) defines the international standard nomenclature for sequence variants, including the `c.` (coding-DNA) and `p.` (protein) prefixes.

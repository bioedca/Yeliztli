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
3. **Protein** — the variant's protein change (HGVS p.) and a link to the gene's full **Gene
   detail** page, where the Nightingale protein-domain diagram is rendered. The diagram is not
   shown inline here, and this page does not mark the variant's position on it.
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

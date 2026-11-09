# Variant detail

--8<-- "health-disclaimer.md"

Click any variant in the [Variant Explorer](variant-explorer.md) to open a side panel with a
summary, then **Open full detail** for the full page, organised into tabs:

1. **Overview** — genomic location, HGVS notation, and transcript details (the MANE Select
   transcript is flagged), a ClinVar summary, and a **Key Scores** block: gnomAD global allele
   frequency with a rare / ultra-rare flag, plus CADD, REVEL, SIFT, and PolyPhen-2.
   At a site with more than one alternate allele, the transcript details describe the allele
   shown under **Ref / Alt**, and stay blank when that allele can't be determined rather than
   borrowing another allele's consequence.
2. **Population** — gnomAD allele frequencies across subpopulations (Global, African,
   Latino/Admixed, Ashkenazi Jewish, East Asian, European (non-Finnish), Finnish, South Asian),
   the gnomAD homozygote count, and a rarity note.[1]
3. **Protein** — the variant's protein change (HGVS p.) and a link to the gene's full
   **[Gene Detail](gene-detail.md)** page, where the Nightingale protein-domain diagram is
   rendered. The diagram is not shown inline here, and this page does not mark the variant's
   position on it.
4. **Clinical** — the ClinVar record (clinical significance, review stars, accession, and
   reported conditions), any evidence-conflict callout, the full in-silico prediction panel
   (CADD, SIFT, PolyPhen-2, REVEL, MutPred2, VEST4, MetaSVM, MetaLR, GERP++, phyloP, MPC,
   PrimateAI, plus an [ensemble-pathogenic flag](../modules/interpretation-reference.md#ensemble-pathogenic)),
   GTEx eQTL and SpliceAI context badges, and
   gene–disease associations (MONDO/HPO, plus OMIM if you've added a key). The GTEx eQTL
   badge names its top gene by GTEx's gene symbol, with the Ensembl gene ID beside it. A GTEx
   eQTL database built before gene names were stored shows the labelled Ensembl ID alone until
   it is [rebuilt](../install/reference-data.md).
5. **Literature** — the gene-level, cache-backed PubMed results returned by the same service as
   **[Gene Detail](gene-detail.md)**. Each card links to PubMed and shows the article title,
   authors, journal, year, and an expandable abstract when one is available. A link opens the
   full Gene Detail page for the variant's gene. These are gene-level results, not a
   variant-specific evidence search.
6. **Genome** — an embedded IGV view of a ~10 kb window around the variant with the default
   annotation tracks, plus a link into the full **[Genome Browser](genome-browser.md)**.

You can jump straight to the variant in the **[Genome Browser](genome-browser.md)**.
Single-variant evidence-card exports are not currently surfaced from the variant detail page;
the backend variant-card endpoints are keyed to reportable findings rather than arbitrary
Variant Explorer variants.

## Understanding the zygosity label

The side panel and full detail page show **Reference call** when the stored call
has been classified as reference for the listed Ref / Alt comparison. This is
distinct from **—**, which means no display label is available. The reference
label does not assume two chromosome copies; it also applies to stored reference
calls on X, Y, and MT.[^reference-call]

This describes the call at that site, not its clinical significance. Existing
labels for carried calls remain unchanged. **Reference call** is Yeliztli display
wording, with no LOINC answer code assigned; the LOINC allelic-state answer list
does not include a separate reference-call answer.[^allelic-state]

[^reference-call]: Danecek P, et al. [The variant call format and VCFtools](https://doi.org/10.1093/bioinformatics/btr330), *Bioinformatics* (2011), [PMID:21653522](https://pubmed.ncbi.nlm.nih.gov/21653522/) (accessed 2026-11-09); [VCF v4.5 specification](https://samtools.github.io/hts-specs/VCFv4.5.pdf), section 1.6.2, revision `e821e4f`, dated 2026-02-25 (accessed 2026-11-09). These define reference versus missing calls and genotype ploidy; the application wording describes its stored classification.

[^allelic-state]: [LOINC 53034-5 — Allelic state](https://loinc.org/53034-5) and its [LL381-5 answer list](https://loinc.org/LL381-5) (accessed 2026-11-09). The terminology review, source snapshots, and limitations are recorded in `data/science-evidence/2026-11-09-reference-label-2389/`.

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

## References

[1] Karczewski KJ, et al. [The mutational constraint spectrum quantified from variation in
141,456 humans](https://doi.org/10.1038/s41586-020-2308-7). *Nature*. 2020;581:434–443.
[PMID 32461654](https://pubmed.ncbi.nlm.nih.gov/32461654/); [PMCID
PMC7334197](https://pmc.ncbi.nlm.nih.gov/articles/PMC7334197/).

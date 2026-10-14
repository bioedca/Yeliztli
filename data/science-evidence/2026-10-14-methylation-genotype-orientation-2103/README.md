# Methylation genotype-orientation note: evidence for issue #2103

## Scope

This packet supports the explanatory note added to the MTHFR & Methylation Advanced View in `frontend/src/components/methylation/PathwayDetailPanel.tsx`. The note makes four claims:

- **(a)** A variant name written as a DNA change, such as C677T, follows the strand its gene is read from.
- **(b)** Arrays report some variants on the other strand.
- **(c)** When they do, the genotype shows the complementary letters (A pairs with T, C with G).
- **(d)** Names written as protein changes, such as Val158Met, use amino-acid codes and cannot be compared letter by letter with a genotype.

The note makes no claim about any individual genotype's strand. "Genotypes are shown as your array reported them" describes the software: the route returns the stored genotype unchanged.

Repository base: `de1ce9a7aac357748eb723a527ac9067d469dd46`

Access date: `2026-10-14`

## Evidence ladder

| Source | Result |
| --- | --- |
| Consensus | Three searches, with complete rankings in `raw/consensus-search-sanitized.json`. They surfaced the HGVS papers and the two array-strand papers. |
| Scite | All five DOIs indexed; no editorial notices (`raw/scite-doi-lookup-sanitized.json`). |
| PubMed | PMIDs resolved by title search. Corrections checked: `CommentIn` only, with no erratum, retraction or expression of concern. |
| HGVS Nomenclature | The specification itself (v21.1.5, CC0), kept whole. |
| NCBI dbSNP | RefSNP rs1801133 HGVS set (registry). |

## Claim mapping

**(a) DNA-change names follow the gene's transcriptional orientation.** This is a definitional claim, so the defining document plus the registry that implements it suffices.
- The HGVS Nomenclature specification v21.1.5 (`raw/hgvs-refseq-page.html`, accessed 2026-10-14) states:
  - "c. = coding DNA reference sequence (based on a protein coding transcript)";
  - "Nucleotide numbering on coding DNA and RNA level are based on the transcriptional orientation of the gene";
  - "the gene of interest may be on the minus (-) strand".
- It is published as PMID:26931183 / DOI:10.1002/humu.22981 (den Dunnen 2016) and PMID:39702242 / DOI:10.1186/s13073-024-01421-5 (Hart 2024).
- The registry is NCBI dbSNP rs1801133 (`raw/ncbi-refsnp-1801133-extract.json`): `NC_000001.10:g.11856378G>A` is listed with `NM_005957.5:c.665C>T`. The coding-DNA description of this MTHFR variant uses the complementary letters of the genomic one.
- The panel's "C677T" for rs1801133 carries `hgvs_protein` `p.Ala222Val` in `backend/data/panels/methylation_panel.json`, matching `NP_005948.3:p.Ala222Val` in the same dbSNP record.

**(b) Arrays report some variants on the other strand.** Two independent sources agree (`raw/pubmed-abstract-excerpts.json`):
- Wang J et al., *Bioinformatics* 2017, PMID:28402386 / DOI:10.1093/bioinformatics/btx186: "Against convention, Illumina uses their own definition of strand, which is inconsistent with the standard reference forward and reverse definition."
- Nicolazzi EL et al., *BMC Genomics* 2014, PMID:24517501 / DOI:10.1186/1471-2164-15-123: "SNPs can be coded using different formats and referencing different strands (e.g. A/B or A/C/T/G alleles, referencing forward/reverse, top/bottom or plus/minus strand)".

**(c) Complementary letters.** A pairs with T and C with G (Watson-Crick base pairing). This is definitional:
- Watson JD, Crick FH, *Nature* 1953, PMID:13054692 / DOI:10.1038/171737a0. The text is not kept because the article is closed-access.
- The registry demonstrates it for this very variant: genomic G>A reads as coding C>T (claim a).

**(d) Protein names use amino-acid codes.**
- The HGVS protein substitution recommendation (`raw/hgvs-protein-substitution-page.html`, CC0) describes "a sequence change where, compared to a reference sequence, one amino acid is replaced by one other amino acid", with examples such as `NP_003997.1:p.Trp24Cys`.
- The panel's "Val158Met" (rs4680, COMT) carries `hgvs_protein` `p.Val158Met`.
- dbSNP gives `NP_005948.3:p.Ala222Val` for C677T.
- "Cannot be compared letter by letter" follows because amino-acid codes are not nucleotide bases.

## Independence

- **(b):** a Vanderbilt group analysing Illumina human arrays, and an Italian group cataloguing six bovine chips from two platforms. No shared data or authors.
- **(a), (c), (d):** definitional (nomenclature and base pairing). Per the contract, the defining document plus the registry that implements it suffices. The two HGVS papers come from the same committee and count as one lineage, not two.

## Limits

- The note does not say which genes or which calls are on which strand. Earlier rounds of PR #2440 that labelled each row were withdrawn, because the local allele data cannot prove a label correct.
- "C677T" is the panel's legacy name for `c.665C>T`. The note uses it as an example of a DNA-change name, not as an HGVS description.

## Retention

- The HGVS pages are CC0 and kept whole.
- The PubMed efetch record is kept with abstracts and contact details removed. The esummary record is kept whole, and short verbatim abstract sentences are kept.
- dbSNP: an extract is kept, and the full payload is hashed.
- Scite and Consensus: metadata only.

## Inventory

This `README.md`, `manifest.json`, and the files `manifest.json` lists with SHA-256:
- `queries.json`
- `raw/` (10):
  - `hgvs-refseq-page.html`
  - `hgvs-protein-substitution-page.html`
  - `ncbi-refsnp-1801133-extract.json`
  - `pubmed-esummary.json`
  - `pubmed-efetch-sanitized.xml`
  - `pubmed-abstract-excerpts.json`
  - `pubmed-comments-corrections-extract.json`
  - `source-versions-and-licences.json`
  - `scite-doi-lookup-sanitized.json`
  - `consensus-search-sanitized.json`

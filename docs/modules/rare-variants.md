# Rare variants

--8<-- "health-disclaimer.md"

The Rare Variant Finder is a flexible tool for surfacing the **rare** and **ultra-rare**
variants you actually carry, with filters you control.

## How it runs

The finder runs automatically after annotation as part of the all-modules pipeline. That
automatic run is intentionally broad: it stores carried-only variants below the default
gnomAD allele-frequency threshold of 1%, using population-max AF when available and global
AF as a fallback, plus variants with no gnomAD AF. It also applies the resolved biological
sex gate, so incompatible sex-chromosome findings are dropped.

This can produce tens of thousands of low-evidence Rare Variant Finder rows for a typical
sample. Most are discovery-context inventory, not diagnoses and not known disease
associations. Use the interactive filters, categories, and evidence ratings to narrow the
set before reviewing or exporting.

## What it looks at

You can set the filters: a gene panel, a gnomAD allele-frequency threshold, whether to
include variants with no gnomAD AF, ClinVar significance, predicted consequence type, and
zygosity. CADD and REVEL scores are shown in the result details when available.

## What you'll see

Findings sorted by clinical relevance, in these categories:

- **ClinVar pathogenic** — known Pathogenic/Likely-Pathogenic variants.
- **ClinVar lower-penetrance/risk allele** — lower-penetrance/risk-allele findings are stored
  under a distinct findings category from high-penetrance P/LP variants. The category spans
  both ClinVar low-penetrance assertions (such as *Pathogenic, low penetrance*) and
  risk-allele assertions (such as *Established risk allele*). A low-penetrance assertion still
  carries a Pathogenic or Likely pathogenic primary classification; it is held separately
  because the ClinGen Low Penetrance/Risk Allele Working Group recommends that assertions
  carrying a penetrance modifier be curated and reported distinctly from standard
  high-penetrance results.[^clingen-risk-allele]
- **[Ensemble pathogenic](interpretation-reference.md#ensemble-pathogenic)** — a supporting
  computational flag based on a strict majority of the prediction axes with data; the reference
  explains the axes, thresholds, and displayed fraction.
- **Novel** — not catalogued in gnomAD, dbSNP, or ClinVar.
- **Rare** — other variants passing your frequency filter.

Each carries an evidence rating, rsID, gene, consequence, HGVS, population frequencies, ClinVar
details, CADD/REVEL scores, zygosity, and inheritance. You can export results to TSV or VCF.

## Good to know

- Only variants you **carry** are shown — arrays report a call at every probe regardless of
  biology, so unscoreable and reference calls are filtered out.
- Y-chromosome findings are dropped for XX samples.
- "Novel" requires absence from gnomAD **and** dbSNP/ClinVar — a gnomAD gap alone isn't enough.
- A frequency belongs to one specific allele, not to an rsID. Where an rsID covers several
  alternate alleles and no single frequency can be attributed to your call — because the call
  carries more than one of them, carries none, or the alleles cannot be matched — no gnomAD
  frequency is shown rather than another allele's, so the variant is treated as having no
  gnomAD AF and appears only when you include those.

[^clingen-risk-allele]: Schmidt RJ, et al. [Recommendations for risk allele evidence
    curation, classification, and reporting from the ClinGen Low Penetrance/Risk Allele
    Working Group](https://doi.org/10.1016/j.gim.2023.101036). *Genetics in Medicine*.
    2024;26(3):101036. PMID:38054408; DOI:10.1016/j.gim.2023.101036
    (accessed 2026-07-31).

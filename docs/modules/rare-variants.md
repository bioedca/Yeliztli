# Rare variants

--8<-- "health-disclaimer.md"

The Rare Variant Finder is a flexible tool for surfacing the **rare** and **ultra-rare**
variants you actually carry, with filters you control.

## What it looks at

You set the filters: a gene panel, a gnomAD allele-frequency threshold (global or
population-specific), ClinVar significance, predicted consequence type, and in-silico scores
(CADD, REVEL).

## What you'll see

Findings sorted by clinical relevance, in four categories:

- **ClinVar pathogenic** — known Pathogenic/Likely-Pathogenic variants.
- **Ensemble pathogenic** — computationally predicted pathogenic.
- **Novel** — not catalogued in gnomAD, dbSNP, or ClinVar.
- **Rare** — other variants passing your frequency filter.

Each carries an evidence rating, rsID, gene, consequence, HGVS, population frequencies, ClinVar
details, CADD/REVEL scores, zygosity, and inheritance. You can export results to TSV or VCF.

## Good to know

- Only variants you **carry** are shown — arrays report a call at every probe regardless of
  biology, so unscoreable and reference calls are filtered out.
- Y-chromosome findings are dropped for XX samples.
- "Novel" requires absence from gnomAD **and** dbSNP/ClinVar — a gnomAD gap alone isn't enough.

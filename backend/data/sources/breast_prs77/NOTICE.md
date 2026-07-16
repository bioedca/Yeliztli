# Breast-cancer PRS77 source notice

The bundled 77-variant breast-cancer score is derived from Supplementary
Table 4 of:

> Mavaddat N, et al. Prediction of breast cancer risk based on profiling with
> common genetic variants. *Journal of the National Cancer Institute*.
> 2015;107(5):djv036. DOI: [10.1093/jnci/djv036](https://doi.org/10.1093/jnci/djv036).
> PMID: [25855707](https://pubmed.ncbi.nlm.nih.gov/25855707/); PMCID:
> [PMC4754625](https://pmc.ncbi.nlm.nih.gov/articles/PMC4754625/).

The article and its supplementary material are distributed under the
[Creative Commons Attribution 3.0 Unported license](https://creativecommons.org/licenses/by/3.0/).
The tabular transcription in this directory preserves the published marker,
allele, and overall odds-ratio values. The runtime weight is the natural
logarithm of the published overall per-allele odds ratio; in the supplement,
the odds ratio is for the second listed allele.

PGS Catalog score [PGS000001](https://www.pgscatalog.org/score/PGS000001/)
was used as an independent exact cross-check of the 77 ordered rsIDs, alleles,
and weights. The PGS Catalog files are not the redistribution-license basis
for the bundled transcription. GRCh37 coordinates come from the PGS Catalog
harmonized snapshot dated 2022-09-23. The checked-in Ensembl snapshot records
the primary-chromosome GRCh38 allele sets consulted on 2026-07-16; alternate
scaffold mappings were excluded.

## Reproducibility

Regenerate or verify the panel entry with:

```text
python scripts/build_breast_prs77.py --write
python scripts/build_breast_prs77.py --check
```

Checked source artifacts and their SHA-256 digests:

- Checked-in supplement transcription: `f08dac2d21dbbf14d2fe09f88de8d0b699e70b1a2eb5a9e02d0723c672ed80f8`
- Checked-in GRCh37 harmonization snapshot: `7faab8eac749b1748c243105de6241bfc3c4bc5dc95bce54d1893d2e6cdd0221`
- Checked-in primary GRCh38 allele snapshot: `7e212e701dfbb7d189155326683396785e6cd3018a4d0a1645650d067fda50b5`
- Mavaddat supplementary DOCX: `83086cd11c67dc01bd606539d4d894f2c16f6a1e6182ee54aa916031ab4c8c3e`
- Mavaddat supplementary archive: `617952a5528414959dd07e1d5687ab606b1d62d7e88883df2d81995e6c486792`
- PGS000001 source gzip: `ccccbb2af5579cc284bfea95a9e5845bf39426f8c71f701ddc2f4197016c6277`
- PGS000001 source text: `a5e5adddb922531789b9f18f4b2eb7ba7f68dc1e99d643c7bc829d10aebc7173`
- PGS000001 GRCh37 harmonized gzip: `5a3069e1a1844df02f3d24e889f97dfad03ee0c40888fc5ecf5f44dc5c36ffc3`
- PGS000001 GRCh37 harmonized text: `1adafcf2ba083cde9616f416281fed7fbaf0e3a833b91c154abdfe96c96a5ea0`
- Ensembl raw 77-record response set: `36597c0ab5d02c7ce373f671f87cd6630ae9df865b99c2e056770dd2eb78cbac`

The generator also pins compact ordered projections of the source and
harmonized rows (`db7811c2…`, `d365caed…`, `6e014bab…`, and `9ee7f543…`),
so reordering or decimal-string drift fails verification.

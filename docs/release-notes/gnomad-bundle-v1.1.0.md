# gnomAD Bundle v1.1.0

> **Status:** PUBLISHED 2026-07-02. The `gnomad-bundle-v1.1.0` release asset
> (`gnomad_af.db.gz`) was verified by the bundle-release workflow and rebuilds
> the gnomAD r2.1.1 exomes bundle with observed allele count (`AN`) columns for
> ACMG BA1/BS1 data-quality guards.

- **Dataset source**: [gnomAD (Genome Aggregation Database)](https://gnomad.broadinstitute.org/)
- **Version**: gnomAD v2.1.1 exomes (GRCh37) - `release/2.1.1` sites VCF
- **Source object**: `gnomad.exomes.r2.1.1.sites.vcf.bgz`
  (`63145056967` bytes; ETag `f034173bf6e57fbb5e8ce680e95134f2`)
- **Individuals**: ~141,456 (gnomAD v2.1.1 cohort)
- **Rows**: 17,209,972 coordinate/allele rows loaded into `gnomad_af`
  (`chrom, pos, ref, alt` primary key). Validation confirmed all rows have
  non-null `an_global`, `an_afr`, and `an_eur`.
- **Scope**: allele frequencies, observed allele counts, and homozygous counts only
  (table `gnomad_af`: `rsid, chrom, pos, ref, alt, af_global, af_afr, af_amr,
  af_asj, af_eas, af_eur, af_fin, af_sas, an_global, an_afr, an_amr, an_asj,
  an_eas, an_eur, an_fin, an_sas, homozygous_count`). No SpliceAI / CADD /
  REVEL / SIFT / PolyPhen or any academic-license-restricted predictor columns -
  those live in dbNSFP, which is NOT redistributed.
- **Build date**: `2026-07-02`
- **Release asset SHA-256** (`gnomad_af.db.gz`):
  `6e5097687001744a9a8533b8b5cbf19f7ad723229ec1e74a04a4890cd55ef32c`
- **Release asset size**: `1301509755` bytes (approx. 1.21 GiB / approx. 1.30 GB)
- **Installed SQLite SHA-256** (`gnomad_af.db`):
  `1627132abfe2e01d0927755f46f8732ea56add645e42679497965aedc1baa24e`
- **Installed SQLite size**: `2853896192` bytes (approx. 2.66 GiB / approx. 2.85 GB)
- **min_app_version**: `0.2.0`
- **Built by**: `scripts/build_gnomad_bundle.py` on SLURM job `458`
  (downloads the r2.1.1 exomes sites VCF and loads AF/AN data via
  `backend.annotation.gnomad.load_gnomad_from_vcf`; table created, bulk insert,
  indexes built post-load, WAL checkpointed). Shipped as a gzip-compressed
  release asset to fit GitHub's asset-size limit; the app installs the
  decompressed SQLite file as `gnomad_af.db`.

## Attribution

gnomAD primary allele-frequency data is released under **CC0 1.0** (public domain
dedication), so redistributing this derived SQLite file is permitted. The gnomAD
project requests citation, and the gnomAD name is a Broad Institute trademark used
here solely for source attribution.

Cite:

> Karczewski, K.J., Francioli, L.C., Tiao, G. et al. "The mutational constraint
> spectrum quantified from variation in 141,456 humans." *Nature* 581, 434-443
> (2020). doi:10.1038/s41586-020-2308-7

See the repo-root `NOTICE` file for the full third-party data attribution list.

## Compatibility

- Minimum Yeliztli app version: **0.2.0**
- GRCh37 throughout (matching the rest of the app's reference data).

## Verification

Verify the downloaded asset against the manifest:

```bash
sha256sum gnomad_af.db.gz
# Compare against bundles/manifest.json -> bundles.gnomad.sha256

stat -c %s gnomad_af.db.gz
# Compare against bundles/manifest.json -> bundles.gnomad.size_bytes
```

`gnomad_af.db` has no embedded version/metadata table, so the compressed release
asset's SHA-256 + size_bytes match is the integrity gate (the verify-and-publish
workflow skips the internal-version check for `gnomad`).

## Rollback

If a regression is found, roll the `bundles/manifest.json -> bundles.gnomad` entry
back to v1.0.0 (the manifest can be rolled back independently of the GitHub
Release asset); do not delete the published release. Reverting the manifest entry
entirely (and restoring `pipeline_pins.gnomad`) returns gnomAD to the inert
deferred state without breaking installs.

# VEP Bundle v4.0.0

Canonical-transcript rebuild of the VEP consequence bundle for the union of the
23andMe v5 and AncestryDNA v2.0 site catalogs.

- **Catalog source**: union of 23andMe v5 + AncestryDNA v2.0 sites on GRCh37
- **Catalog SHA-256**: `544295b6813fb5a288e1824f4ab9e29824dd70ebc5027b9d2db8fdbbd3536317`
- **rsID denominator**: 1,941,528 (`union_rsids.tsv`)
- **Coordinate input rows**: 1,927,332
- **Coordinate input coverage**: 99.27% of rsIDs resolved to GRCh37 variation features before VEP
- **Ensembl VEP release**: 112
- **VEP mode**: offline GRCh37 cache + FASTA, default coordinate input, `--canonical`, `--hgvs`
- **Build date**: 2026-07-02
- **Schema version**: 1
- **Bundle SHA-256**: `c8e5f162e1872ecd0ff94408c66ce0874b79dea5f4f645c758d2c45c3b6cc4d3`
- **Bundle size**: 352,251,904 bytes (335.9 MB)
- **Variant rows stored**: 2,973,074 (`bundle_metadata.variant_count`)
- **Bundle coverage gate**: 99.37538864191114% (`vep_bundle_build_stats.json::coverage_percent`)
- **Canonical/MANE-equivalent rows**: 1,958,413 (`mane_select = 1`)
- **min_app_version**: `0.2.0`

## Notes

This release replaces the prior manifest-only v3.0.0 re-annotation trigger
with a real rebuilt bundle. The VEP run includes `--canonical`, which makes
GRCh37 VEP emit `CANONICAL=YES`; the bundle builder stores those rows as
`mane_select = 1` so preferred-transcript tiebreaks can use the canonical
transcript.

Validation checks confirmed nonzero canonical rows and the targeted canonical
transcript outcomes:

- `rs771467011` resolves to AGTRAP transcript `ENST00000314340` with
  `p.Leu98=` and `mane_select = 1`.
- `rs1801133` resolves to MTHFR transcript `ENST00000376592` with
  `c.665C>T`, `p.Ala222Val`, and `mane_select = 1`.

`bundle_metadata.bundle_version = "v4.0.0"` is recorded inside the SQLite and
matches the manifest `version` field.

## Inputs

- VEP cache: `https://ftp.ensembl.org/pub/release-112/variation/vep/homo_sapiens_vep_112_GRCh37.tar.gz`
- FASTA: `https://ftp.ensembl.org/pub/grch37/release-112/fasta/homo_sapiens/dna/Homo_sapiens.GRCh37.dna.primary_assembly.fa.gz`
- Variation feature dump: `https://ftp.ensembl.org/pub/release-112/mysql/homo_sapiens_variation_112_37/variation_feature.txt.gz`
- Build script commit: `c541f24ad9b700823e151f72c7c7444b7e7b6c7d`
- Ensembl VEP git commit: `31a3581b84495b617b2f3980da6c6313ca6d238f`

## Verification

Verify the downloaded asset against the manifest:

```bash
sha256sum vep_bundle.db
# c8e5f162e1872ecd0ff94408c66ce0874b79dea5f4f645c758d2c45c3b6cc4d3
```

The bundle-release workflow also verifies the release tag, manifest version,
asset SHA-256, byte size, and embedded `bundle_metadata.bundle_version`.

## Rollback

If a regression is found, see `docs/bundle-release-runbook.md` §9 (Rollback).
The manifest can be rolled back independently of the GitHub Release asset.
